"""Your holdings, a "what if I had held this basket" backtest, and recorded volume -- all
served by the gateway for the Portfolio tab, the boards' tiles and the price chart.

  data/holdings.json    every buy/sell you type in on the Portfolio tab (date, symbol, shares, price)
  /api/holdings         GET: trades + positions (shares, average cost, price now, gain/loss)
                        POST {action: add|remove, ...}
  /api/portfolio/backtest?range=3mo&basket=AAPL:10,NVDA:5
                        how that basket (default: your current positions) would have done
                        over the range, day by day, using free daily prices from Yahoo Finance
  /api/volume?b=ibkr&s=AAPL   volume per minute the bridge recorded (last 7 days)

Average cost: buys raise it, sells lower the share count at the same average cost.
Nothing here places orders.
"""
import json
import os
import ssl
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
HOLDINGS = os.path.join(DATA, 'holdings.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
RANGES = {'1mo': 31, '3mo': 92, '6mo': 183, '1y': 366, '2y': 731}
YAHOO_FUT = {'ES=F': 'ES=F', 'NQ=F': 'NQ=F', 'YM=F': 'YM=F'}
_lock = threading.Lock()
_daily = {}          # yahoo symbol -> (fetched_at, [(date, close)])
_vol = {}            # bridge -> (mtime, data)


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
    os.replace(path + '.tmp', path)


def _last_price(sym):
    try:
        import watch
        with watch._lock:
            h = watch._px.get(sym)
            return h[-1][1] if h else None
    except Exception:
        return None


# ------------------------------------------------------------------ holdings
def trades():
    return sorted(_read(HOLDINGS, []), key=lambda t: (t['date'], t.get('created', 0)))


def positions():
    pos = {}
    for t in trades():
        p = pos.setdefault(t['sym'], {'sym': t['sym'], 'shares': 0.0, 'cost': 0.0, 'realized': 0.0})
        q, px = float(t['qty']), float(t['price'])
        if q > 0:
            p['cost'] += q * px
            p['shares'] += q
        elif p['shares'] > 0:
            sell = min(-q, p['shares'])
            avg = p['cost'] / p['shares']
            p['realized'] += sell * (px - avg)
            p['cost'] -= sell * avg
            p['shares'] -= sell
    out = []
    for p in pos.values():
        sh = round(p['shares'], 6)
        avg = p['cost'] / p['shares'] if p['shares'] > 1e-9 else None
        last = _last_price(p['sym'])
        val = sh * last if last is not None and sh else None
        gain = (last - avg) * sh if last is not None and avg else None
        out.append({'sym': p['sym'], 'shares': sh, 'avg': round(avg, 4) if avg else None, 'last': last,
                    'value': round(val, 2) if val is not None else None, 'gain': round(gain, 2) if gain is not None else None,
                    'gain_pct': round((last - avg) / avg * 100, 2) if last is not None and avg else None,
                    'realized': round(p['realized'], 2)})
    out.sort(key=lambda p: (p['shares'] <= 0, p['sym']))
    return out


def holdings_json():
    return json.dumps({'trades': trades(), 'positions': positions(), 'server_ms': int(time.time() * 1000)})


def post(data):
    act = data.get('action')
    try:
        with _lock:
            lst = _read(HOLDINGS, [])
            if act == 'add':
                sym = str(data.get('sym') or '').strip().upper()
                qty, price = float(data.get('qty')), float(data.get('price'))
                day = str(data.get('date') or '')[:10]
                datetime.strptime(day, '%Y-%m-%d')
                if not sym or len(sym) > 12 or not qty or not price > 0:
                    raise ValueError('fill in the symbol, shares and price')
                if data.get('side') == 'sell':
                    qty = -abs(qty)
                else:
                    qty = abs(qty)
                lst.append({'id': uuid.uuid4().hex[:10], 'date': day, 'sym': sym, 'qty': qty, 'price': price,
                            'note': str(data.get('note') or '')[:80], 'created': int(time.time() * 1000)})
            elif act == 'remove':
                lst = [t for t in lst if t.get('id') != data.get('id')]
            else:
                return 400, json.dumps({'error': 'unknown action'})
            _write(HOLDINGS, lst)
    except (ValueError, TypeError) as e:
        msg = str(e)
        return 400, json.dumps({'error': msg if 'fill in' in msg else 'check the date (YYYY-MM-DD), shares and price'})
    return 200, holdings_json()


# jb-desk: broker positions fill the Portfolio (brokerpos.py); typed-in trades fill the rest
_ledger_positions = positions


def positions():
    try:
        import brokerpos
        return brokerpos.merge(_ledger_positions(), _last_price)
    except Exception:
        return _ledger_positions()


# ------------------------------------------------------------------ daily prices (Yahoo, free)
_CTX = None


def _ctx():
    global _CTX
    if _CTX is None:
        try:
            import certifi
            _CTX = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _CTX = ssl.create_default_context()
    return _CTX


def daily(sym):
    """[(YYYY-MM-DD, close)] for about 2 years, cached 6 hours."""
    ys = YAHOO_FUT.get(sym, sym.replace('.', '-'))
    with _lock:
        hit = _daily.get(ys)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(ys) +
           '?range=2y&interval=1d&includePrePost=false')
    import world                       # asks Yahoo like a browser does (gateway/world.py)
    body = world._get(url)
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    ts = res.get('timestamp') or []
    closes = (((res.get('indicators') or {}).get('quote') or [{}])[0].get('close')) or []
    adj = (((res.get('indicators') or {}).get('adjclose') or [{}])[0].get('adjclose')) or closes
    rows = []
    for t, c in zip(ts, adj):
        if c:
            rows.append((datetime.utcfromtimestamp(t - 5 * 3600).strftime('%Y-%m-%d'), float(c)))
    with _lock:
        _daily[ys] = (time.time(), rows)
    return rows


def backtest(query):
    q = urllib.parse.parse_qs(query or '')
    rng = (q.get('range') or ['3mo'])[0]
    days = RANGES.get(rng, 92)
    basket = {}
    if q.get('basket') and q['basket'][0].strip():
        for part in q['basket'][0].split(','):
            if ':' in part:
                s, n = part.split(':', 1)
                try:
                    if float(n) > 0:
                        basket[s.strip().upper()] = float(n)
                except ValueError:
                    pass
    else:
        basket = {p['sym']: p['shares'] for p in positions() if p['shares'] > 0}
    if not basket:
        return json.dumps({'error': 'No holdings yet. Add your buys above, or type a basket like AAPL:10, NVDA:5.'})
    cutoff = datetime.fromtimestamp(time.time() - days * 86400).strftime('%Y-%m-%d')
    series, missing = {}, []
    for s in list(basket)[:25] + ['SPY']:
        try:
            series[s] = {d: c for d, c in daily(s) if d >= cutoff}
        except Exception:
            if s != 'SPY':
                missing.append(s)
    have = [s for s in basket if series.get(s)]
    if not have:
        return json.dumps({'error': "Couldn't get daily prices for these symbols from Yahoo Finance right now."})
    dates = sorted(set().union(*(set(series[s]) for s in have)))
    last = {}
    values = []
    for d in dates:
        for s in have:
            if d in series[s]:
                last[s] = series[s][d]
        if len(last) == len(have):
            values.append((d, sum(basket[s] * last[s] for s in have)))
    if len(values) < 2:
        return json.dumps({'error': 'Not enough shared price history for these symbols yet.'})
    v0 = values[0][1]
    peak, mdd, rets = v0, 0.0, []
    for i, (d, v) in enumerate(values):
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak * 100)
        if i:
            rets.append(((v / values[i - 1][1] - 1) * 100, d))
    spy = series.get('SPY') or {}
    spy_pts = [(d, spy[d]) for d, _ in values if d in spy]
    out = {'range': rng, 'basket': basket, 'missing': missing,
           'start': values[0][0], 'end': values[-1][0], 'start_value': round(v0, 2), 'end_value': round(values[-1][1], 2),
           'return_pct': round((values[-1][1] / v0 - 1) * 100, 2), 'max_drawdown_pct': round(mdd, 2),
           'best_day': max(rets) if rets else None, 'worst_day': min(rets) if rets else None,
           'up_days_pct': round(sum(1 for r, _ in rets if r > 0) / len(rets) * 100, 1) if rets else None,
           'spy_return_pct': round((spy_pts[-1][1] / spy_pts[0][1] - 1) * 100, 2) if len(spy_pts) > 1 else None,
           'points': [[d, round(v, 2)] for d, v in values],
           'spy': [[d, round(v0 * p / spy_pts[0][1], 2)] for d, p in spy_pts] if spy_pts else []}
    if out['best_day']:
        out['best_day'] = [round(out['best_day'][0], 2), out['best_day'][1]]
        out['worst_day'] = [round(out['worst_day'][0], 2), out['worst_day'][1]]
    return json.dumps(out)


# ------------------------------------------------------------------ recorded volume
def volume(query):
    q = urllib.parse.parse_qs(query or '')
    b = (q.get('b') or ['ibkr'])[0]
    s = (q.get('s') or [''])[0].upper()
    if b not in ('ibkr', 'webull'):
        return json.dumps({'rows': []})
    path = os.path.join(DATA, b + '_volume.json')
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return json.dumps({'rows': []})
    with _lock:
        hit = _vol.get(b)
    if not hit or hit[0] != mt:
        hit = (mt, _read(path, {}))
        with _lock:
            _vol[b] = hit
    return json.dumps({'rows': hit[1].get(s, [])})
