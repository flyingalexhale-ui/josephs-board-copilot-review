"""Profit-finding and Bloomberg-style extras from the final-ideas list (jb-edge), running inside the gateway.

  fair value of 3x / 2x ETFs right now, from the futures (NQ, ES, YM, RTY) or from the prices of
     the stocks inside the fund, vs where the ETF trades                                        (idea 7)
  what's inside my ETF: top holdings, how each is moving, who reports earnings soon             (idea 8)
  end-of-day rebalance estimate of the leveraged funds, from 3 pm                               (idea 9)
  holding cost (decay) of 3x funds over 5 and 20 days vs 3x the index                           (idea 10)
  overnight vs day drift over 20 / 60 / 250 days and by weekday                                 (idea 11)
  options expiry / VIX expiry / index rebalance calendar (fixed rules)                          (idea 12)
  VIX curve warning: short-term VIX above 3-month VIX                                           (idea 13)
  futures positioning from the CFTC weekly report                                               (idea 19)
  Fed meeting odds for the next 4 meetings, with the change over the week (Kalshi)              (idea 47)
  one-tap fund card: what it tracks, fees, size, top holdings (Yahoo)                           (idea 48)
  price history table: daily and per-session open / high / low / close                         (idea 49)
  futures trend light for the 3x fund tiles                                                     (idea 64)
  options "magnet" levels (gamma) for QQQ and SPY, from Yahoo's delayed options                 (idea 65)
  Nasdaq-100 breadth: how many are up, how many above their 50-day average                      (idea 66)
  net liquidity: Fed balance sheet - Treasury cash - reverse repo (FRED), next to the Nasdaq     (idea 67)
  Treasury 10 / 20 / 30-year auction results with a push when one goes badly                    (idea 68)
  yen shock push: dollar/yen moves more than 1% since the close                                  (idea 69)

  GET  /api/plus/edge                 everything for the Night desk section
  GET  /api/plus/edge/tiles           fair value + trend light per symbol (board tiles)
  GET  /api/plus/edge/holdings?sym=   top holdings and their moves
  GET  /api/plus/edge/fund?sym=       fund card
  GET  /api/plus/edge/history?sym=    daily + session price table
  GET  /api/plus/edge/gamma           QQQ / SPY levels for the chart
  POST /api/plus/edge/settings        {vix_push, yen_push, auction_push}
Everything is free. Yahoo is asked slowly (a few seconds between calls) and most things only
once a day, so the World tab's own Yahoo prices are not slowed down.
Files: data/edge.json (cache), data/edge_settings.json, data/ndx_closes.json.
"""
import json
import math
import os
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'edge.json')
SETTINGS = os.path.join(DATA, 'edge_settings.json')
NDX = os.path.join(DATA, 'ndx_closes.json')
GAP_S = 3.0                                   # between Yahoo calls
FUTURES = {'QQQ': 'NQ=F', 'SPY': 'ES=F', 'DIA': 'YM=F', 'IWM': 'RTY=F'}
FUT_NAME = {'NQ=F': 'Nasdaq futures', 'ES=F': 'S&P futures', 'YM=F': 'Dow futures', 'RTY=F': 'Russell futures'}
DEFAULTS = {'vix_push': True, 'yen_push': True, 'auction_push': True}
_lock = threading.Lock()
_c = {}                                       # the cache (see _load)
_st = {'started': False, 'errors': {}, 'busy': ''}


def _now():
    return int(time.time() * 1000)


def _load():
    global _c
    v = watch._read(CACHE, {})
    _c = v if isinstance(v, dict) else {}
    for k in ('holdings', 'aum', 'daily', 'fund', 'gamma', 'odds_hist', 'pushed', 'quotes'):
        _c.setdefault(k, {})


def _save():
    with _lock:
        watch._write(CACHE, _c)


def settings():
    v = watch._read(SETTINGS, {})
    return dict(DEFAULTS, **(v if isinstance(v, dict) else {}))


def _err(k, e):
    _st['errors'][k] = f'{str(e)[:140]} ({watch.et(_now()):%a %I:%M %p} ET)'
    print(f'edge: {k}: {e!r}', flush=True)


def _ok(k):
    _st['errors'].pop(k, None)


def _yahoo(url):
    import world
    return world._get(url, timeout=20)


def _raw(v):
    return v.get('raw') if isinstance(v, dict) else v


def _ysym(s):
    return urllib.parse.quote(s.replace('.', '-') if '=' not in s and '^' not in s else s)


def pairs():
    try:
        import mdata
        return [p for p in mdata.pairs() if len(p) >= 5]
    except Exception:
        return []


def board_last(sym, max_age_ms=10 * 60000):
    with watch._lock:
        h = watch._px.get(sym)
        if h and _now() - h[-1][0] < max_age_ms:
            return float(h[-1][1])
    return None


def since_close(sym):
    """% move since the last 4 pm close: the board's own record, else mdata / World (Yahoo)."""
    now = _now()
    with watch._lock:
        hist = list(watch._px.get(sym) or [])
    if hist and now - hist[-1][0] < 10 * 60000:
        ref = watch.price_at(hist, watch.last_close_ms(now))
        if ref:
            return watch.pct(hist[-1][1], ref), 'board'
    try:
        import world
        q = world._quotes.get(sym)
        if q and q.get('since_close') is not None and now - (q.get('last_ms') or 0) < 3 * 3600000:
            return q['since_close'], 'yahoo'
    except Exception:
        pass
    try:
        import mdata
        p = mdata.price(sym, now)
        if p and p.get('since_close') is not None:
            return p['since_close'], p.get('src', '')
    except Exception:
        pass
    q = _c['quotes'].get(sym)
    if q and now - q['at'] < 20 * 60000:
        return q.get('since_close'), 'yahoo'
    return None, ''


def _fut_move(fut):
    """Futures move since 4 pm: the board's futures tile if it has one, else the World tab."""
    for k in (fut, fut.replace('=F', '')):
        m, src = since_close(k)
        if m is not None:
            return m
    return None


# ------------------------------------------------------------------ holdings (ideas 8, 48, 66)
def fetch_holdings(etf):
    body = _yahoo('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + _ysym(etf) + '?modules=topHoldings')
    res = ((body.get('quoteSummary') or {}).get('result') or [None])[0] or {}
    out = []
    for h in ((res.get('topHoldings') or {}).get('holdings') or []):
        w = _raw(h.get('holdingPercent'))
        if h.get('symbol') and w:
            out.append({'sym': str(h['symbol']).replace('-', '.'), 'name': h.get('holdingName') or '', 'w': round(float(w) * 100, 2)})
    return out


def holdings(etf):
    h = _c['holdings'].get(etf)
    return h['rows'] if h else []


def basket_move(base):
    """Weighted move of the fund's top holdings from the board's own prices (needs half the weight)."""
    rows = holdings(base)
    tot = got = 0.0
    for r in rows:
        tot += r['w']
        if board_last(r['sym']) is None:
            continue
        m, _ = since_close(r['sym'])
        if m is not None:
            got += r['w']
            r['_m'] = m
    if not rows or got < 0.5 * tot or got < 20:
        return None, 0
    return sum(r['w'] * r['_m'] for r in rows if '_m' in r) / got, got


def holdings_view(sym):
    sym = sym.upper()
    base = next((p[1] for p in pairs() if sym in (p[3], p[4])), sym)
    rows = holdings(base)
    if not rows and base not in _c['holdings']:
        try:
            rows = fetch_holdings(base)
            _c['holdings'][base] = {'at': _now(), 'rows': rows}
        except Exception as e:
            return {'sym': sym, 'base': base, 'rows': [], 'error': str(e)[:150]}
    dates = {}
    try:
        import earn
        dates = earn._earn_dates()
    except Exception:
        pass
    today = watch.et(_now()).date()
    out = []
    for r in rows:
        m, src = since_close(r['sym'])
        if m is None:
            m = _quick_move(r['sym'])
        e = dates.get(r['sym']) or {}
        soon = ''
        if e.get('date'):
            try:
                dd = (date.fromisoformat(e['date']) - today).days
                if 0 <= dd <= 7:
                    soon = ('today' if dd == 0 else 'tomorrow' if dd == 1 else e['date']) + (' ' + e['when'] if e.get('when') else '')
            except Exception:
                pass
        out.append(dict(r, move=None if m is None else round(m, 2), contrib=None if m is None else round(m * r['w'] / 100, 3), earnings=soon))
    lev = next((p[2] * (1 if sym == p[3] else -1) for p in pairs() if sym in (p[3], p[4])), 1)
    return {'sym': sym, 'base': base, 'lev': lev, 'rows': out,
            'note': f'{sym} follows {base} x{lev}: a move in these stocks moves {sym} about {abs(lev)}x as much.' if base != sym else ''}


def _quick_move(sym):
    """A Yahoo price for a holding the boards don't stream, kept 10 minutes."""
    q = _c['quotes'].get(sym)
    if q and _now() - q['at'] < 10 * 60000:
        return q.get('since_close')
    try:
        import world
        x = world._quote(sym, _now())
        _c['quotes'][sym] = {'at': _now(), 'since_close': x.get('since_close'), 'last': x.get('last')}
        return x.get('since_close')
    except Exception:
        return None


def fund_card(sym):
    sym = sym.upper()
    hit = _c['fund'].get(sym)
    if hit and _now() - hit['at'] < 24 * 3600000:
        return hit['card']
    body = _yahoo('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + _ysym(sym) +
                  '?modules=fundProfile,summaryDetail,defaultKeyStatistics,price,topHoldings,quoteType,assetProfile')
    res = ((body.get('quoteSummary') or {}).get('result') or [None])[0] or {}
    fp, sd, ks, pr, qt = (res.get(k) or {} for k in ('fundProfile', 'summaryDetail', 'defaultKeyStatistics', 'price', 'quoteType'))
    fees = (fp.get('feesExpensesInvestment') or {})
    er = _raw(fees.get('annualReportExpenseRatio')) or _raw(sd.get('expenseRatio')) or _raw(ks.get('annualReportExpenseRatio'))
    name = pr.get('longName') or pr.get('shortName') or qt.get('longName') or sym
    lev = next((p[2] * (1 if sym == p[3] else -1) for p in pairs() if sym in (p[3], p[4])), None)
    if lev is None:
        low = name.lower()
        lev = next((n * s for n, w in ((3, '3x'), (2, '2x'), (3, 'ultrapro'), (2, 'ultra')) if w in low
                    for s in [(-1 if any(k in low for k in ('short', 'bear', 'inverse')) else 1)]), 1)
    base = next((p[1] for p in pairs() if sym in (p[3], p[4])), None)
    card = {'sym': sym, 'name': name, 'type': qt.get('quoteType') or '', 'family': fp.get('family') or '', 'category': fp.get('categoryName') or '',
            'leverage': lev, 'tracks': base or '', 'expense': round(er * 100, 2) if er and er < 1 else er,
            'aum': _raw(sd.get('totalAssets')) or _raw(ks.get('totalAssets')), 'avg_vol': _raw(sd.get('averageVolume')) or _raw(pr.get('averageDailyVolume3Month')),
            'yield': _raw(sd.get('yield')), 'inception': _raw(ks.get('fundInceptionDate')), 'exchange': pr.get('exchangeName') or '',
            'summary': ((res.get('assetProfile') or {}).get('longBusinessSummary') or fp.get('legalType') or '')[:700],
            'holdings': [{'sym': h.get('symbol'), 'name': h.get('holdingName'), 'w': round((_raw(h.get('holdingPercent')) or 0) * 100, 2)}
                         for h in ((res.get('topHoldings') or {}).get('holdings') or [])][:10]}
    if base and not card['holdings']:
        card['holdings'] = holdings(base)[:10]
        card['holdings_of'] = base
    _c['fund'][sym] = {'at': _now(), 'card': card}
    return card


# ------------------------------------------------------------------ fair value + trend (ideas 7, 64)
def fair_rows():
    out = []
    for g, base, lev, bull, bear in pairs():
        fut = FUTURES.get(base)
        how, bm = '', None
        fm = _fut_move(fut) if fut else None
        bsc, bsrc = since_close(base)
        sess = _session()
        base_live = board_last(base, 3 * 60000) is not None
        if fm is not None and (sess in ('overnight', None) or not base_live):
            bm, how = fm, FUT_NAME[fut]
        elif bsc is not None and base_live:
            bm, how = bsc, base + ' now'
        else:
            km, w = basket_move(base)
            if km is not None:
                bm, how = km, f'{base} top stocks ({w:.0f}% of the fund)'
            elif fm is not None:
                bm, how = fm, FUT_NAME[fut]
            elif bsc is not None:
                bm, how = bsc, base + ' (last price)'
        for sym, sign in ((bull, 1), (bear, -1)):
            if not sym:
                continue
            exp = None if bm is None else round(bm * lev * sign, 2)
            act, src = since_close(sym)
            row = {'sym': sym, 'group': g, 'lev': lev * sign, 'base': base, 'expected': exp, 'actual': None if act is None else round(act, 2),
                   'how': how, 'trend': trend(fut or base, sign)}
            if exp is not None and act is not None:
                row['gap'] = round(act - exp, 2)
            out.append(row)
    return out


def _session():
    try:
        import killswitch as ks
        return ks.session_now()
    except Exception:
        return None


def trend(sym, sign=1):
    """'up' / 'down' / 'flat' from the last ~90 minutes of the futures (or the base ETF): the price
    vs its own average, and the slope of the second half. Inverse funds get the opposite light."""
    pts = []
    with watch._lock:
        for k in (sym, sym.replace('=F', '')):
            h = watch._px.get(k)
            if h and _now() - h[-1][0] < 10 * 60000:
                pts = [p for t, p in h if t >= _now() - 90 * 60000]
                break
    if len(pts) < 10:
        try:
            import world
            q = world._quotes.get(sym)
            if q and q.get('spark') and _now() - (q.get('last_ms') or 0) < 3 * 3600000:
                pts = q['spark'][-8:]                 # 15-minute steps: about 2 hours
        except Exception:
            pass
    if len(pts) < 4:
        return None
    avg = sum(pts) / len(pts)
    half = pts[len(pts) // 2:]
    slope = (half[-1] / half[0] - 1) * 100
    last = pts[-1]
    if last > avg * 1.0005 and slope > 0.05:
        t = 'up'
    elif last < avg * 0.9995 and slope < -0.05:
        t = 'down'
    else:
        t = 'flat'
    if sign < 0 and t != 'flat':
        t = 'down' if t == 'up' else 'up'
    return {'light': t, 'from': FUT_NAME.get(sym, sym), 'slope': round(slope, 2)}


# ------------------------------------------------------------------ rebalance (idea 9)
def rebalance():
    groups = {}
    for g, base, lev, bull, bear in pairs():
        m, _ = since_close(base)
        if m is None and base in FUTURES:
            m = _fut_move(FUTURES[base])
        for sym, L in ((bull, lev), (bear, -lev)):
            a = (_c['aum'].get(sym) or {}).get('v') if sym else None
            if not sym or not a or m is None:
                continue
            flow = a * L * (L - 1) * m / 100
            x = groups.setdefault(base, {'base': base, 'group': g, 'move': round(m, 2), 'flow': 0.0, 'funds': []})
            x['flow'] += flow
            x['funds'].append({'sym': sym, 'aum': a, 'flow': round(flow)})
    rows = sorted(groups.values(), key=lambda x: -abs(x['flow']))
    for r in rows:
        r['flow'] = round(r['flow'])
    d = watch.et(_now())
    return {'rows': rows, 'live': d.weekday() < 5 and 15 <= d.hour < 16,
            'note': 'Leveraged funds must buy (index up) or sell (index down) near 4 pm to reset their leverage: about fund size x L x (L-1) x the day\'s move. '
                    'Bear funds also buy on up days. Research found this explains part of late-day moves on big days; most of its profits were in 2008, so treat it as context, not a signal.'}


# ------------------------------------------------------------------ daily bars (ideas 10, 11, 49)
def fetch_daily(sym, rng='1y'):
    body = _yahoo('https://query1.finance.yahoo.com/v8/finance/chart/' + _ysym(sym) + f'?range={rng}&interval=1d')
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    ts = res.get('timestamp') or []
    q = ((res.get('indicators') or {}).get('quote') or [{}])[0]
    rows = []
    for i, t in enumerate(ts):
        o, h, l, c, v = (q.get(k, [None] * len(ts))[i] for k in ('open', 'high', 'low', 'close', 'volume'))
        if c and o:
            rows.append([watch.et(int(t) * 1000).date().isoformat(), round(o, 4), round(h or c, 4), round(l or c, 4), round(c, 4), int(v or 0)])
    return rows


def daily(sym):
    d = _c['daily'].get(sym)
    return d['rows'] if d else []


def decay_rows():
    out = []
    for g, base, lev, bull, bear in pairs():
        b = {r[0]: r[4] for r in daily(base)}
        for sym, L in ((bull, lev), (bear, -lev)):
            rows = [r for r in daily(sym) if r[0] in b] if sym else []
            if len(rows) < 21:
                continue
            row = {'sym': sym, 'lev': L, 'base': base}
            for n in (5, 20):
                a, z = rows[-1 - n], rows[-1]
                act = (z[4] / a[4] - 1) * 100
                idx = (b[z[0]] / b[a[0]] - 1) * 100
                row[f'd{n}'] = {'actual': round(act, 2), 'naive': round(L * idx, 2), 'cost': round(act - L * idx, 2)}
            out.append(row)
    return out


def drift(sym):
    rows = daily(sym)
    if len(rows) < 30:
        return None
    on, dy, wd, cum = [], [], {}, []
    a = b = 1.0
    for prev, r in zip(rows, rows[1:]):
        o = r[1] / prev[4] - 1
        d = r[4] / r[1] - 1
        on.append(o)
        dy.append(d)
        a *= 1 + o
        b *= 1 + d
        cum.append([r[0], round((a - 1) * 100, 2), round((b - 1) * 100, 2)])
        wd.setdefault(date.fromisoformat(r[0]).weekday(), []).append(o)
    out = {'sym': sym, 'cum': cum[-250:]}
    for n in (20, 60, 250):
        po = pd = 1.0
        for x in on[-n:]:
            po *= 1 + x
        for x in dy[-n:]:
            pd *= 1 + x
        out[f'n{n}'] = {'overnight': round((po - 1) * 100, 2), 'day': round((pd - 1) * 100, 2)}
    out['weekday'] = {['Mon', 'Tue', 'Wed', 'Thu', 'Fri'][k]: round(sum(v) / len(v) * 100, 3) for k, v in sorted(wd.items()) if k < 5 and v}
    return out


def history_table(sym):
    sym = sym.upper()
    rows = daily(sym)
    if not rows:
        try:
            rows = fetch_daily(sym)
            _c['daily'][sym] = {'at': _now(), 'rows': rows}
        except Exception as e:
            return {'sym': sym, 'daily': [], 'error': str(e)[:150]}
    # sessions from the board's own record (about the last 40 hours)
    with watch._lock:
        hist = list(watch._px.get(sym) or [])
    sess = []
    if hist:
        import killswitch as ks
        cur = None
        for t, p in hist:
            s = ks.session_now(watch.et(t))
            day = watch.et(t + (4 * 3600000 if s == 'overnight' else 0)).date().isoformat()
            key = (day, s)
            if cur is None or cur['key'] != key:
                cur = {'key': key, 'day': day, 'session': ks.SESSION_NAME.get(s, 'closed'), 'o': p, 'h': p, 'l': p, 'c': p, 'from': t}
                sess.append(cur)
            cur['h'], cur['l'], cur['c'], cur['to'] = max(cur['h'], p), min(cur['l'], p), p, t
        for s_ in sess:
            s_.pop('key', None)
    return {'sym': sym, 'daily': [{'d': r[0], 'o': r[1], 'h': r[2], 'l': r[3], 'c': r[4], 'v': r[5]} for r in rows[-260:]][::-1],
            'sessions': sess[::-1]}


# ------------------------------------------------------------------ calendar (idea 12)
def _third_friday(y, m):
    d = date(y, m, 1)
    d += timedelta(days=(4 - d.weekday()) % 7)
    return d + timedelta(days=14)


def _trading(d):
    try:
        import sys
        sys.path.insert(0, os.path.join(ROOT, 'lib')) if os.path.join(ROOT, 'lib') not in sys.path else None
        import nyse_hours as nh
        return nh.trading_day(d)
    except Exception:
        return d.weekday() < 5


def opex_calendar(today=None, months=4):
    today = today or watch.et(_now()).date()
    out = []
    for i in range(months + 1):
        y, m = today.year + (today.month - 1 + i) // 12, (today.month - 1 + i) % 12 + 1
        f = _third_friday(y, m)
        while not _trading(f):
            f -= timedelta(days=1)
        quarter = m in (3, 6, 9, 12)
        out.append({'d': f.isoformat(), 'title': 'Triple witching (quarterly options + futures expiry)' if quarter else 'Monthly options expiry',
                    'why': 'Huge options and futures expire at once; big volume at the close and odd moves into it.' if quarter else
                           'Monthly options expire; prices can stick near big strikes, then move freely the next day.'})
        if quarter:
            out.append({'d': f.isoformat(), 'title': 'S&P 500 / Nasdaq-100 quarterly rebalance',
                        'why': 'Index funds trade at the close to match the new weights. December also has the yearly Nasdaq-100 reshuffle.'})
        ny, nm = (y + (m == 12), m % 12 + 1)
        v = _third_friday(ny, nm) - timedelta(days=30)        # VIX expiry: the Wednesday 30 days before next month's SPX expiry
        while not _trading(v):
            v -= timedelta(days=1)
        out.append({'d': v.isoformat(), 'title': 'VIX options and futures expiry (morning)',
                    'why': 'VIX contracts settle at the open; the fear index can jump or drop around it.'})
        if m in (6, 12):
            last_fri = date(y, m, 30 if m == 6 else 31)
            while last_fri.weekday() != 4:
                last_fri -= timedelta(days=1)
            out.append({'d': last_fri.isoformat(), 'title': 'Russell index reconstitution (approx. date)',
                        'why': 'Small-cap funds (IWM, TNA, TZA) trade huge volume at the close. Check FTSE Russell for the exact date.'})
    out = [x for x in out if x['d'] >= today.isoformat()]
    out.sort(key=lambda x: x['d'])
    return out[:12]


# ------------------------------------------------------------------ VIX curve (13), yen (69)
def vix_curve():
    q = _c['quotes'].get('^VIX3M') or {}
    try:
        import world
        v = (world._quotes.get('^VIX') or {}).get('last')
    except Exception:
        v = None
    v3 = q.get('last')
    if not v or not v3:
        return None
    r = v / v3
    return {'vix': round(v, 2), 'vix3m': round(v3, 2), 'ratio': round(r, 3), 'state': 'inverted' if r >= 1 else 'tight' if r >= 0.95 else 'normal',
            'text': ('Short-term fear is ABOVE 3-month fear: a stress signal. 3x funds get hit hardest in panics.' if r >= 1 else
                     'Short-term fear is close to 3-month fear: getting nervous.' if r >= 0.95 else 'Normal: short-term fear below 3-month fear.')}


def _check_pushes(cfg):
    now = _now()
    d = watch.et(now)
    key = d.strftime('%Y-%m-%d')
    p = _c['pushed']
    vc = vix_curve()
    if cfg['vix_push'] and vc and vc['state'] == 'inverted' and p.get('vix') != key:
        p['vix'] = key
        watch.push_later('VIX curve inverted', f"VIX {vc['vix']} is above 3-month VIX {vc['vix3m']}: a stress signal. 3x funds suffer most in panics.", 'warning')
    try:
        import world
        y = world._quotes.get('JPY=X') or {}
    except Exception:
        y = {}
    m = y.get('since_close')
    if cfg['yen_push'] and m is not None and abs(m) >= 1.0 and now - (y.get('last_ms') or 0) < 3600000:
        k2 = watch.last_close_ms(now)
        if p.get('yen') != k2:
            p['yen'] = k2
            watch.push_later('Yen shock', f"Dollar/yen {'up' if m > 0 else 'down'} {abs(m):.1f}% since the close ({y.get('last'):.2f}). "
                             + ('A stronger yen (dollar/yen falling) can force selling in US tech overnight, like August 2024.' if m < 0 else
                                'A fast yen move can shake markets worldwide.'), 'rotating_light' if m <= -1.5 else 'warning')


# ------------------------------------------------------------------ Fed odds (idea 47)
KALSHI = 'https://api.elections.kalshi.com/trade-api/v2/events?series_ticker=KXFEDDECISION&status=open&with_nested_markets=true&limit=8'


def _bucket(label):
    low = str(label).lower()
    if 'hike' in low or 'raise' in low or 'increase' in low:
        return 'hike'
    if 'cut' in low or 'lower' in low or 'decrease' in low:
        return 'cut'
    return 'hold'


def fetch_odds():
    import alerts2
    body = json.loads(alerts2._http(KALSHI, headers={'Accept': 'application/json'}))
    evs = sorted(body.get('events') or [], key=lambda e: e.get('strike_date') or e.get('event_ticker') or '')[:4]
    out = []
    for ev in evs:
        rows = []
        for m in ev.get('markets') or []:
            p = alerts2._price(m)
            if p is None:
                continue
            rows.append({'label': m.get('yes_sub_title') or m.get('subtitle') or m.get('title') or m.get('ticker'), 'p': round(p * 100)})
        b = {'cut': 0, 'hold': 0, 'hike': 0}
        for r in rows:
            b[_bucket(r['label'])] += r['p']
        tot = sum(b.values()) or 1
        out.append({'title': ev.get('title') or '', 'when': ev.get('strike_date') or '', 'ticker': ev.get('event_ticker') or '',
                    'rows': sorted(rows, key=lambda r: -r['p'])[:6], 'bucket': {k: round(v * 100 / tot) for k, v in b.items()}})
    day = watch.et(_now()).date().isoformat()
    h = _c['odds_hist']
    h[day] = {o['ticker']: o['bucket'] for o in out}
    for k in sorted(h)[:-15]:
        del h[k]
    week = (watch.et(_now()).date() - timedelta(days=7)).isoformat()
    old_day = max([k for k in h if k <= week] or [min(h)])
    for o in out:
        was = h.get(old_day, {}).get(o['ticker'])
        if was and old_day != day:
            o['week'] = {k: o['bucket'][k] - was.get(k, 0) for k in o['bucket']}
            o['week_from'] = old_day
    _c['odds'] = {'at': _now(), 'meetings': out}


# ------------------------------------------------------------------ gamma (idea 65)
def _ncdf_pdf(x):
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def _gamma(S, K, T, iv):
    if S <= 0 or K <= 0 or T <= 0 or not iv or iv <= 0.01:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    return _ncdf_pdf(d1) / (S * iv * math.sqrt(T))


def fetch_gamma(sym):
    base = 'https://query2.finance.yahoo.com/v7/finance/options/' + _ysym(sym)
    body = _yahoo(base)
    res = ((body.get('optionChain') or {}).get('result') or [None])[0] or {}
    S = float(((res.get('quote') or {}).get('regularMarketPrice')) or 0)
    exps = (res.get('expirationDates') or [])[:3]
    chains = [((res.get('options') or [{}])[0])]
    for e in exps[1:]:
        time.sleep(GAP_S)
        b2 = _yahoo(base + '?date=' + str(e))
        r2 = ((b2.get('optionChain') or {}).get('result') or [None])[0] or {}
        chains.append(((r2.get('options') or [{}])[0]))
    opts = []
    now_s = time.time()
    for ch in chains:
        for kind in ('calls', 'puts'):
            for o in ch.get(kind) or []:
                K, oi, iv = o.get('strike'), o.get('openInterest') or 0, o.get('impliedVolatility')
                exp = o.get('expiration') or ch.get('expirationDate')
                if not K or not oi or not exp:
                    continue
                T = max((float(exp) + 16 * 3600 - now_s) / (365 * 86400), 1 / 365 / 4)   # expires at 4 pm
                opts.append((float(K), int(oi), float(iv or 0), T, 1 if kind == 'calls' else -1))
    if not S or not opts:
        raise ValueError('no options data')

    def gex_at(s):
        return sum(sign * _gamma(s, K, T, iv) * oi * 100 * s * s * 0.01 for K, oi, iv, T, sign in opts)
    by_k = {}
    for K, oi, iv, T, sign in opts:
        if abs(K / S - 1) > 0.08:
            continue
        by_k[K] = by_k.get(K, 0) + sign * _gamma(S, K, T, iv) * oi * 100 * S * S * 0.01
    grid = [S * (1 + i / 400) for i in range(-24, 25)]           # +-6% in 0.25% steps
    vals = [gex_at(s) for s in grid]
    flip = None
    for i in range(1, len(grid)):
        if (vals[i - 1] < 0) != (vals[i] < 0):
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (0 - vals[i - 1]) / (vals[i] - vals[i - 1])
            if flip is None or abs(x - S) < abs(flip - S):
                flip = x
    pos = sorted([(v, k) for k, v in by_k.items() if v > 0], reverse=True)[:3]
    neg = sorted([(v, k) for k, v in by_k.items() if v < 0])[:2]
    levels = [{'p': k, 'kind': 'magnet', 'size': round(v / 1e9, 2)} for v, k in pos] + [{'p': k, 'kind': 'accel', 'size': round(v / 1e9, 2)} for v, k in neg]
    if flip:
        levels.append({'p': round(flip, 2), 'kind': 'flip', 'size': None})
    return {'sym': sym, 'spot': S, 'at': _now(), 'total': round(gex_at(S) / 1e9, 2), 'levels': levels,
            'expiries': [datetime.utcfromtimestamp(e).date().isoformat() for e in exps]}


# ------------------------------------------------------------------ breadth (idea 66)
def fetch_ndx_list():
    import world
    url = 'https://api.nasdaq.com/api/quote/list-type/nasdaq100'
    hdr = {'Accept': 'application/json', 'User-Agent': world.UA, 'Origin': 'https://www.nasdaq.com', 'Referer': 'https://www.nasdaq.com/'}
    if world._browser is not None:
        r = world._browser.Session(impersonate='chrome').get(url, timeout=20, headers={'Accept': 'application/json'})
        body = r.json()
    else:
        import urllib.request
        with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=20, context=world._ctx()) as r:
            body = json.loads(r.read().decode())
    d = body.get('data') or {}
    rows = (d.get('data') or {}).get('rows') or d.get('rows') or []
    out = []
    for r in rows:
        sym = r.get('symbol')
        try:
            last = float(str(r.get('lastSalePrice') or '').replace('$', '').replace(',', ''))
            pc = float(str(r.get('percentageChange') or '').replace('%', '').replace('+', '').replace(',', '') or 'nan')
        except ValueError:
            continue
        if sym:
            out.append({'sym': sym.replace('/', '.'), 'last': last, 'pct': pc})
    if len(out) < 50:
        raise ValueError(f'Nasdaq list had {len(out)} rows')
    return out


def breadth():
    b = _c.get('breadth')
    if not b:
        return None
    rows = b['rows']
    closes = watch._read(NDX, {})
    above = n50 = 0
    for r in rows:
        h = [c for _, c in (closes.get(r['sym']) or [])][-50:]
        if len(h) >= 45:
            n50 += 1
            live = board_last(r['sym']) or r['last']
            if live > sum(h) / len(h):
                above += 1
    # overnight: stocks on your boards since the close
    up = sum(1 for r in rows if (r['pct'] or 0) > 0)
    dn = sum(1 for r in rows if (r['pct'] or 0) < 0)
    return {'at': b['at'], 'up': up, 'down': dn, 'n': len(rows), 'above50': above, 'n50': n50,
            'note': 'Up / down = the last regular session (Nasdaq\'s list). If QQQ / TQQQ rise while most of its stocks fall, the move is narrow and fragile.'}


def _ndx_closes(rows):
    """Keep a 60-day close history per Nasdaq-100 stock: seeded once from Yahoo, then each evening's close."""
    closes = watch._read(NDX, {})
    closes = closes if isinstance(closes, dict) else {}
    d = watch.et(_now())
    today = d.date().isoformat()
    if d.hour >= 17 and _trading(d.date()):
        for r in rows:
            h = closes.setdefault(r['sym'], [])
            if not h or h[-1][0] != today:
                h.append([today, r['last']])
            closes[r['sym']] = h[-70:]
    missing = [r['sym'] for r in rows if len(closes.get(r['sym']) or []) < 45][:8]   # a few per round
    for sym in missing:
        try:
            rows_ = fetch_daily(sym, '3mo')
            closes[sym] = [[x[0], x[4]] for x in rows_][-70:]
        except Exception:
            pass
        time.sleep(GAP_S)
    watch._write(NDX, closes)


# ------------------------------------------------------------------ net liquidity (idea 67)
def _to_trillions(v, kind):
    if kind == 'walcl':
        return v / 1e6                   # millions
    if kind == 'tga':
        return v / 1e6 if v > 50000 else v / 1e3
    return v / 1e6 if v > 100000 else v / 1e3


def fetch_netliq():
    import macro
    w = macro.fetch_series('WALCL')
    time.sleep(1)
    tga = macro.fetch_series('WTREGEN')
    time.sleep(1)
    rrp = macro.fetch_series('RRPONTSYD')

    def at(rows, d):
        best = None
        for dd, v in rows:
            if dd > d:
                break
            best = v
        return best
    out = []
    for d, v in w[-110:]:
        t, r = at(tga, d), at(rrp, d)
        if t is None or r is None:
            continue
        out.append([d, round(_to_trillions(v, 'walcl') - _to_trillions(t, 'tga') - _to_trillions(r, 'rrp'), 3)])
    q = {r[0]: r[4] for r in daily('QQQ')}
    for row in out:
        row.append(next((q[k] for k in sorted(q, reverse=True) if k <= row[0]), None) if q else None)
    _c['netliq'] = {'at': _now(), 'rows': out}


# ------------------------------------------------------------------ Treasury auctions (idea 68)
def _term(r):
    t = str(r.get('originalSecurityTerm') or r.get('securityTerm') or '')
    for y in ('10-Year', '20-Year', '30-Year'):
        if y in t:
            return y
    return ''


def fetch_auctions():
    import world
    start = (watch.et(_now()).date() - timedelta(days=800)).isoformat()
    rows = []
    for typ in ('Note', 'Bond'):
        url = f'https://www.treasurydirect.gov/TA_WS/securities/search?format=json&type={typ}&startDate={start}'
        body = world._get(url, timeout=25)
        rows += body if isinstance(body, list) else []
        time.sleep(1)
    out = []
    for r in rows:
        term = _term(r)
        btc = r.get('bidToCoverRatio')
        if not term or not btc:
            continue
        try:
            tot = float(r.get('totalAccepted') or 0)
            dealer = float(r.get('primaryDealerAccepted') or 0)
            out.append({'d': str(r.get('auctionDate') or '')[:10], 'term': term, 'yield': float(r.get('highYield') or 0) or None,
                        'btc': float(btc), 'dealer': round(dealer / tot * 100, 1) if tot else None})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x['d'])
    res = []
    for term in ('10-Year', '20-Year', '30-Year'):
        lst = [x for x in out if x['term'] == term]
        if len(lst) < 4:
            continue
        last, prev = lst[-1], lst[-7:-1]
        avg_btc = sum(x['btc'] for x in prev) / len(prev)
        ds = [x['dealer'] for x in prev if x['dealer'] is not None]
        avg_d = sum(ds) / len(ds) if ds else None
        weak = last['btc'] < avg_btc * 0.95 or (avg_d is not None and last['dealer'] is not None and last['dealer'] > avg_d + 5)
        strong = last['btc'] > avg_btc * 1.05 and (avg_d is None or last['dealer'] is None or last['dealer'] < avg_d - 3)
        res.append(dict(last, avg_btc=round(avg_btc, 2), avg_dealer=None if avg_d is None else round(avg_d, 1),
                        verdict='weak' if weak else 'strong' if strong else 'normal'))
    _c['auctions'] = {'at': _now(), 'rows': res}
    cfg = settings()
    for r in res:
        k = 'auction ' + r['term'] + r['d']
        if r['verdict'] == 'weak' and cfg['auction_push'] and not _c['pushed'].get(k) and r['d'] >= (watch.et(_now()).date() - timedelta(days=1)).isoformat():
            _c['pushed'][k] = 1
            watch.push_later(f"Weak {r['term']} auction", f"{r['term']} Treasury auction looked weak: bid-to-cover {r['btc']:.2f} vs {r['avg_btc']:.2f} usual"
                             + (f", dealers took {r['dealer']:.0f}% vs {r['avg_dealer']:.0f}% usual" if r['dealer'] is not None and r['avg_dealer'] is not None else '')
                             + f", yield {r['yield']}%. Weak auctions have caused sharp Nasdaq drops.", 'warning')


# ------------------------------------------------------------------ CFTC positioning (idea 19)
CFTC = 'https://publicreporting.cftc.gov/resource/gpe5-46if.json'
CFTC_MARKETS = [('Nasdaq-100 futures', 'NASDAQ'), ('S&P 500 futures', 'S&P 500'), ('VIX futures', 'VIX'), ('US dollar index', 'USD INDEX')]


def _fnum(d, *need, avoid=('change', 'pct', 'traders', 'spread')):
    for k, v in d.items():
        kl = k.lower()
        if all(n in kl for n in need) and not any(a in kl for a in avoid):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def fetch_cftc():
    import world
    out = []
    for name, key in CFTC_MARKETS:
        where = urllib.parse.quote(f"upper(market_and_exchange_names) like '%{key}%'")
        url = f'{CFTC}?$where={where}&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=400'
        try:
            rows = world._get(url, timeout=30)
        except Exception as e:
            _err('cftc ' + name, e)
            continue
        by_mkt = {}
        for r in rows if isinstance(rows, list) else []:
            by_mkt.setdefault(r.get('market_and_exchange_names') or '', []).append(r)
        if not by_mkt:
            continue
        mkt = max(by_mkt, key=lambda m: sum(_fnum(r, 'open_interest') or 0 for r in by_mkt[m][:4]))   # the biggest contract
        hist = []
        for r in by_mkt[mkt]:
            lv = (_fnum(r, 'lev_money', 'long') or 0) - (_fnum(r, 'lev_money', 'short') or 0)
            am = (_fnum(r, 'asset_mgr', 'long') or 0) - (_fnum(r, 'asset_mgr', 'short') or 0)
            hist.append([str(r.get('report_date_as_yyyy_mm_dd') or '')[:10], lv, am])
        hist.sort()
        hist = hist[-156:]
        if not hist:
            continue
        vals = sorted(h[1] for h in hist)
        rank = sum(1 for v in vals if v <= hist[-1][1]) / len(vals) * 100
        out.append({'name': name, 'market': mkt.title(), 'date': hist[-1][0], 'lev': hist[-1][1], 'am': hist[-1][2], 'rank': round(rank),
                    'extreme': 'very crowded long' if rank >= 90 else 'very crowded short' if rank <= 10 else '',
                    'hist': [[h[0], h[1]] for h in hist[-52:]]})
        time.sleep(1)
    if out:
        _c['cftc'] = {'at': _now(), 'rows': out}


# ------------------------------------------------------------------ the loop
def _due(key, every_s):
    t = (_c.get('due') or {}).get(key, 0)
    return time.time() - t >= every_s


def _done(key):
    _c.setdefault('due', {})[key] = time.time()


def _job(key, fn):
    _st['busy'] = key
    try:
        fn()
        _ok(key)
    except Exception as e:
        _err(key, e)
    finally:
        _st['busy'] = ''
        _done(key)
        _save()


def _daily_fetch():
    ps = pairs()
    bases = sorted({p[1] for p in ps})
    for b in bases:
        try:
            _c['holdings'][b] = {'at': _now(), 'rows': fetch_holdings(b)}
        except Exception as e:
            _err('holdings ' + b, e)
        time.sleep(GAP_S)
    syms = sorted({s for p in ps for s in (p[1], p[3], p[4]) if s} | {'SPY', 'QQQ'})
    for s in syms:
        try:
            _c['daily'][s] = {'at': _now(), 'rows': fetch_daily(s)}
        except Exception as e:
            _err('daily ' + s, e)
        time.sleep(GAP_S)
    for s in sorted({x for p in ps for x in (p[3], p[4]) if x}):
        try:
            body = _yahoo('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' + _ysym(s) + '?modules=summaryDetail,defaultKeyStatistics')
            res = ((body.get('quoteSummary') or {}).get('result') or [None])[0] or {}
            a = _raw((res.get('summaryDetail') or {}).get('totalAssets')) or _raw((res.get('defaultKeyStatistics') or {}).get('totalAssets'))
            if a:
                _c['aum'][s] = {'at': _now(), 'v': float(a)}
        except Exception as e:
            _err('size ' + s, e)
        time.sleep(GAP_S)


def _loop():
    time.sleep(40)                                 # let the boards and the World tab start first
    while True:
        d = watch.et(_now())
        wk = d.weekday() < 5
        try:
            if _due('daily', 20 * 3600) and (d.hour >= 17 or d.hour < 7 or not _c['daily']):
                _job('daily', _daily_fetch)
            if _due('vix3m', 15 * 60):
                def vx():
                    import world
                    x = world._quote('^VIX3M', _now())
                    _c['quotes']['^VIX3M'] = {'at': _now(), 'last': x.get('last')}
                _job('vix3m', vx)
            if _due('odds', 30 * 60):
                _job('odds', fetch_odds)
            if _due('gamma', 30 * 60 if wk and 9 <= d.hour < 17 else 6 * 3600):
                def gm():
                    for s in ('QQQ', 'SPY'):
                        _c['gamma'][s] = fetch_gamma(s)
                        time.sleep(GAP_S)
                _job('gamma', gm)
            if _due('breadth', 10 * 60 if wk and 9 <= d.hour < 18 else 3 * 3600):
                def br():
                    rows = fetch_ndx_list()
                    _c['breadth'] = {'at': _now(), 'rows': rows}
                    _ndx_closes(rows)
                _job('breadth', br)
            if _due('netliq', 24 * 3600):
                _job('netliq', fetch_netliq)
            if _due('auctions', 15 * 60 if wk and 13 <= d.hour < 15 else 6 * 3600):
                _job('auctions', fetch_auctions)
            if _due('cftc', 7 * 86400) or (d.weekday() == 4 and d.hour >= 16 and _due('cftc', 3 * 86400)):
                _job('cftc', fetch_cftc)
            _check_pushes(settings())
        except Exception as e:
            print(f'edge: loop: {e!r}', flush=True)
        time.sleep(30)


def start():
    if _st['started']:
        return
    _st['started'] = True
    _load()
    threading.Thread(target=_loop, name='edge', daemon=True).start()


# ------------------------------------------------------------------ doorway
def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return ''.join(ch for ch in str(v or '').upper() if ch.isalnum() or ch in '.=^-')[:12]


def snapshot():
    if not _c:
        _load()
    now = _now()
    return {'fair': fair_rows(), 'rebalance': rebalance(), 'decay': decay_rows(),
            'drift': [x for x in (drift(s) for s in ('SPY', 'QQQ', 'TQQQ', 'SOXL', 'SPXL')) if x],
            'calendar': opex_calendar(), 'vix': vix_curve(), 'odds': _c.get('odds'), 'gamma': _c['gamma'],
            'breadth': breadth(), 'netliq': _c.get('netliq'), 'auctions': _c.get('auctions'), 'cftc': _c.get('cftc'),
            'settings': settings(), 'errors': _st['errors'], 'busy': _st['busy'], 'at': now}


def tiles():
    out = {}
    for r in fair_rows():
        out[r['sym']] = {k: r.get(k) for k in ('expected', 'actual', 'gap', 'how', 'trend', 'lev', 'base')}
    return out


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    if sub == 'tiles':
        return 200, tiles()
    if sub == 'holdings':
        return 200, holdings_view(_q(query, 'sym'))
    if sub == 'fund':
        try:
            return 200, fund_card(_q(query, 'sym'))
        except Exception as e:
            return 200, {'sym': _q(query, 'sym'), 'error': f'Yahoo did not answer: {str(e)[:120]}'}
    if sub == 'history':
        return 200, history_table(_q(query, 'sym'))
    if sub == 'gamma':
        return 200, _c.get('gamma') or {}
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'settings':
        cur = settings()
        for k in DEFAULTS:
            if k in data:
                cur[k] = bool(data[k])
        watch._write(SETTINGS, cur)
        return 200, {'settings': cur}
    return 404, {'error': 'unknown'}
