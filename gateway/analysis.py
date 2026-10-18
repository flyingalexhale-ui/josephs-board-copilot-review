"""Analysis tab (jb-analysis): gap scorecard, session split, sector heatmap, relative strength vs
SPY/QQQ, correlation finder, and options max pain / put-call ratio.

Free sources only: the board's own price record (gateway/watch.py) for the after-hours / overnight /
pre-market split, Yahoo daily bars for the longer history, Yahoo's delayed options chains.
Every Yahoo request goes through world._get (asks like a browser does).

A background thread refreshes things slowly so Yahoo isn't bothered:
  daily bars (1 year) per symbol: every 6 hours, one symbol every 2 seconds
  sector ETFs (pre-market / after-hours): every 15 minutes
  options chains: every 30 minutes, one symbol every 3 seconds
  your own gap record: once a day at 4:05 pm ET (data/gap_log.json)
"""
import json
import math
import os
import sys
import threading
import time
import urllib.parse
from datetime import timedelta

import watch
import world

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CACHE = os.path.join(DATA, 'analysis_cache.json')      # daily bars, sectors, options (survives restarts)
GAPLOG = os.path.join(DATA, 'gap_log.json')            # your own record of gaps, one row per stock per day

SECTOR_ETF = {'XLK': 'Technology', 'XLC': 'Communication', 'XLY': 'Consumer (discretionary)',
              'XLP': 'Consumer (staples)', 'XLF': 'Financials', 'XLV': 'Health care', 'XLE': 'Energy',
              'XLI': 'Industrials', 'XLB': 'Materials', 'XLU': 'Utilities', 'XLRE': 'Real estate'}
# Yahoo's sector names -> the SPDR fund for that sector
YAHOO_SECTOR = {'Technology': 'XLK', 'Communication Services': 'XLC', 'Consumer Cyclical': 'XLY',
                'Consumer Defensive': 'XLP', 'Financial Services': 'XLF', 'Healthcare': 'XLV', 'Energy': 'XLE',
                'Industrials': 'XLI', 'Basic Materials': 'XLB', 'Utilities': 'XLU', 'Real Estate': 'XLRE'}
BENCH = ('SPY', 'QQQ')

_lock = threading.Lock()
_c = {'daily': {}, 'sectors': {}, 'options': {}, 'etf': {}, 'errors': {}}
_status = {'started': False, 'note': '', 'busy': ''}


# ------------------------------------------------------------------ small helpers
def _now():
    return int(time.time() * 1000)


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, separators=(',', ':'))
        os.replace(tmp, path)
    except Exception as e:
        print(f'analysis: could not save {os.path.basename(path)}: {e!r}', flush=True)


def _pct(a, b):
    return None if a is None or not b else round((a - b) / b * 100, 2)


def _ysym(sym):
    return sym.replace('.', '-')


def symbols():
    """Stocks and ETFs on the boards / scanner, plus your Portfolio holdings: {sym: 'stock'|'etf'}."""
    out = {}
    try:
        import news
        out = {s: e['kind'] for s, e in news.tracked().items() if e['kind'] in ('stock', 'etf')}
    except Exception:
        pass
    try:
        import portfolio
        for p in portfolio.positions() or []:
            if p['sym'] not in out and '=' not in p['sym']:
                out[p['sym']] = 'stock'
    except Exception:
        pass
    return out


def board_symbols():
    """The ones on the IBKR / Webull boards, the algos and your holdings (not scanner-only)."""
    out = set()
    try:
        import news
        out = {s for s, e in news.tracked().items()
               if e['kind'] in ('stock', 'etf') and any(not w.startswith('Scanner') for w in e['where'])}
    except Exception:
        pass
    return sorted(out | set(correlation_held()))


def _err(key, e):
    with _lock:
        _c['errors'][key] = str(e)[:160]
    if '429' in str(e):
        _status['note'] = 'Yahoo asked the Mac to slow down; the Analysis tab will catch up on its own.'


# ------------------------------------------------------------------ Yahoo daily bars (open/high/low/close)
def daily(sym, max_age=6 * 3600):
    """[[YYYY-MM-DD, open, high, low, close], ...] for about a year, oldest first."""
    with _lock:
        hit = _c['daily'].get(sym)
    if hit and time.time() - hit['at'] < max_age:
        return hit['rows']
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(_ysym(sym)) +
           '?range=1y&interval=1d&includePrePost=false')
    body = world._get(url)
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    ts = res.get('timestamp') or []
    q = ((res.get('indicators') or {}).get('quote') or [{}])[0]
    rows = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, l, c):
            continue
        day = watch.et(t * 1000).strftime('%Y-%m-%d')
        if rows and rows[-1][0] == day:
            rows[-1] = [day, o, h, l, c]
        else:
            rows.append([day, o, h, l, c])
    with _lock:
        _c['daily'][sym] = {'at': time.time(), 'rows': rows}
        _c['errors'].pop('daily ' + sym, None)
    return rows


def _cached_daily(sym):
    with _lock:
        hit = _c['daily'].get(sym)
    return hit['rows'] if hit else []


def sector(sym):
    with _lock:
        hit = _c['sectors'].get(sym)
    if hit and time.time() - hit['at'] < 30 * 86400:
        return hit['sector']
    if sym in SECTOR_ETF:
        sec = SECTOR_ETF[sym]
    else:
        body = world._get('https://query2.finance.yahoo.com/v10/finance/quoteSummary/' +
                          urllib.parse.quote(_ysym(sym)) + '?modules=assetProfile')
        prof = ((((body.get('quoteSummary') or {}).get('result') or [{}])[0]) or {}).get('assetProfile') or {}
        etf = YAHOO_SECTOR.get(prof.get('sector') or '')
        sec = SECTOR_ETF[etf] if etf else ''
    with _lock:
        _c['sectors'][sym] = {'sector': sec, 'at': time.time()}
    return sec


# ------------------------------------------------------------------ sessions from the board's own prices
def _windows(now_ms):
    """Times of the latest 4 pm close and the sessions after it (New York time):
    close, after-hours end (8 pm), overnight start/end (8 pm -> 4 am, Sunday 8 pm after a weekend),
    and the 9:30 open of the next trading day."""
    close = watch.last_close_ms(now_ms)
    d = watch.et(close).date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return {'close': close, 'ah_end': close + 4 * 3600 * 1000,
            'on_start': watch.at_et(d - timedelta(days=1), 20, 0), 'on_end': watch.at_et(d, 4, 0),
            'open': watch.at_et(d, 9, 30)}


def sessions(sym, now_ms=None):
    """% move in each session since the last 4 pm close: after-hours (4-8 pm), overnight
    (8 pm-4 am), pre-market (4-9:30 am) and regular (9:30 am-4 pm), plus the total.
    None where the Mac has no prices for it, or the session hasn't started yet."""
    now_ms = now_ms or _now()
    with watch._lock:
        hist = list(watch._px.get(sym) or [])
    if not hist:
        return None
    w = _windows(now_ms)
    at = lambda ms: watch.price_at(hist, min(ms, now_ms))
    c = at(w['close'])
    if c is None:
        return None
    out = {'ah': _pct(at(w['ah_end']), c)}
    out['on'] = _pct(at(w['on_end']), at(w['on_start'])) if now_ms > w['on_start'] else None
    out['pre'] = _pct(at(w['open']), at(w['on_end'])) if now_ms > w['on_end'] else None
    if now_ms > w['open']:
        # during the regular session the "last close" is still yesterday's, so this is today
        out['reg'] = _pct(at(now_ms), at(w['open']))
    else:
        out['reg'] = None
    out['total'] = _pct(hist[-1][1], c)
    out['last'] = hist[-1][1]
    return out


# ------------------------------------------------------------------ gap scorecard
def _size_bucket(g):
    a = abs(g)
    return '1-2%' if a < 2 else '2-4%' if a < 4 else '4%+'


def gap_stats(rows):
    """From daily bars: every day that opened 1%+ away from the day before's close.
    held   = the stock still closed on the gap side of the old close
    filled = during the day it came all the way back to the old close
    run    = it closed even further in the gap's direction than it opened"""
    out = []
    for i in range(1, len(rows)):
        pc = rows[i - 1][4]
        day, o, h, l, c = rows[i]
        if not pc:
            continue
        g = (o - pc) / pc * 100
        if abs(g) < 1:
            continue
        up = g > 0
        out.append({'day': day, 'gap': round(g, 2), 'held': (c > pc) if up else (c < pc),
                    'filled': (l <= pc) if up else (h >= pc), 'run': (c > o) if up else (c < o),
                    'after_open': round((c - o) / o * 100, 2)})
    return out


def _tally(gaps):
    n = len(gaps)
    if not n:
        return {'n': 0}
    return {'n': n, 'held': sum(g['held'] for g in gaps), 'filled': sum(g['filled'] for g in gaps),
            'run': sum(g['run'] for g in gaps),
            'avg_after_open': round(sum(g['after_open'] for g in gaps) / n, 2)}


def scorecard(sym, now_ms=None):
    now_ms = now_ms or _now()
    rows = _cached_daily(sym)
    gaps = gap_stats(rows)
    # before the 9:30 open: the gap so far (now vs the 4 pm close); once open: today's 9:30 gap
    s = sessions(sym, now_ms)
    cur = None
    if s and s.get('total') is not None:
        cur = s['total'] if s.get('reg') is None else round(((1 + s['total'] / 100) / (1 + s['reg'] / 100) - 1) * 100, 2)
    out = {'sym': sym, 'year': {'up': _tally([g for g in gaps if g['gap'] > 0]),
                                'down': _tally([g for g in gaps if g['gap'] < 0])},
           'now': cur, 'recent': gaps[-6:][::-1], 'have_daily': len(rows) > 100}
    if cur is not None and abs(cur) >= 1:
        like = [g for g in gaps if (g['gap'] > 0) == (cur > 0) and _size_bucket(g['gap']) == _size_bucket(cur)]
        out['like'] = dict(_tally(like), dir='up' if cur > 0 else 'down', size=_size_bucket(cur))
    mine = [r for r in _read(GAPLOG, {}).get('rows', []) if r['sym'] == sym and r.get('gap') is not None and abs(r['gap']) >= 1]
    if mine:
        out['mine'] = _tally(mine)
    return out


def _record_gaps(now_ms):
    """Once a day after 4 pm: write today's gap and how it ended from the Mac's own prices."""
    d = watch.et(now_ms)
    if d.weekday() >= 5 or d.hour < 16 or (d.hour == 16 and d.minute < 5):
        return
    try:
        sys.path.insert(0, os.path.join(ROOT, 'lib')) if os.path.join(ROOT, 'lib') not in sys.path else None
        import nyse_hours
        if d.date() in nyse_hours.nyse_holidays(d.year):
            return
        early = d.date() in nyse_hours.nyse_early_closes(d.year)
    except Exception:
        early = False
    log = _read(GAPLOG, {'rows': [], 'days': []})
    day = d.strftime('%Y-%m-%d')
    if day in log.get('days', []):
        return
    prev_close = watch.last_close_ms(watch.at_et(d.date(), 9, 0))
    t_open, t_close = watch.at_et(d.date(), 9, 30), watch.at_et(d.date(), 13 if early else 16, 0)
    for sym, kind in symbols().items():
        with watch._lock:
            hist = list(watch._px.get(sym) or [])
        pc, o, c = (watch.price_at(hist, t) for t in (prev_close, t_open, t_close))
        if not (pc and o and c) or hist[0][0] > prev_close:
            continue
        seg = [p for t, p in hist if t_open <= t <= t_close]
        if not seg:                        # no regular-hours prices that day (e.g. its price service was down)
            continue
        g = (o - pc) / pc * 100
        up = g > 0
        log['rows'].append({'sym': sym, 'day': day, 'gap': round(g, 2), 'held': (c > pc) if up else (c < pc),
                            'filled': (min(seg) <= pc) if up else (max(seg) >= pc),
                            'run': (c > o) if up else (c < o), 'after_open': round((c - o) / o * 100, 2)})
    log['days'] = (log.get('days', []) + [day])[-400:]
    log['rows'] = log['rows'][-20000:]
    _write(GAPLOG, log)


# ------------------------------------------------------------------ relative strength + correlation
def _closes(sym):
    return {r[0]: r[4] for r in _cached_daily(sym)}


def _returns(sym, days):
    rows = _cached_daily(sym)[-(days + 1):]
    return {rows[i][0]: rows[i][4] / rows[i - 1][4] - 1 for i in range(1, len(rows)) if rows[i - 1][4]}


def _corr(a, b):
    keys = [k for k in a if k in b]
    if len(keys) < 20:
        return None
    xa = [a[k] for k in keys]; xb = [b[k] for k in keys]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    va = sum((x - ma) ** 2 for x in xa); vb = sum((x - mb) ** 2 for x in xb)
    if not va or not vb:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / math.sqrt(va * vb)


def _beta(sym, bench='SPY', days=90):
    a, b = _returns(sym, days), _returns(bench, days)
    keys = [k for k in a if k in b]
    if len(keys) < 20:
        return None
    mb = sum(b[k] for k in keys) / len(keys)
    ma = sum(a[k] for k in keys) / len(keys)
    vb = sum((b[k] - mb) ** 2 for k in keys)
    return None if not vb else round(sum((a[k] - ma) * (b[k] - mb) for k in keys) / vb, 2)


def _move_n(sym, n):
    rows = _cached_daily(sym)
    if len(rows) <= n:
        return None
    return _pct(rows[-1][4], rows[-1 - n][4])


def _today(sym, now_ms):
    st = watch.stats(sym, now_ms)
    if st and st.get('close') is not None:
        return round(st['close'], 2)
    return None


def relative(now_ms=None):
    now_ms = now_ms or _now()
    bench = {b: {'now': _today(b, now_ms), 'd5': _move_n(b, 5), 'd21': _move_n(b, 21)} for b in BENCH}
    if bench['SPY']['now'] is None or bench['QQQ']['now'] is None:      # not on a board: use futures
        for b, f in (('SPY', 'ES=F'), ('QQQ', 'NQ=F')):
            if bench[b]['now'] is None:
                bench[b]['now'] = _today(f, now_ms)
                bench[b]['via'] = f
    out = []
    for sym, kind in sorted(symbols().items()):
        if sym in BENCH:
            continue
        beta = _beta(sym)
        row = {'sym': sym, 'kind': kind, 'beta': beta, 'now': _today(sym, now_ms),
               'd5': _move_n(sym, 5), 'd21': _move_n(sym, 21)}
        for b in BENCH:
            for k in ('now', 'd5', 'd21'):
                if row[k] is not None and bench[b][k] is not None:
                    row[f'vs_{b}_{k}'] = round(row[k] - bench[b][k], 2)
        if row['now'] is not None and bench['SPY']['now'] is not None and beta is not None:
            row['expected'] = round(beta * bench['SPY']['now'], 2)
            row['own'] = round(row['now'] - row['expected'], 2)
        out.append(row)
    return {'bench': bench, 'rows': out}


def correlation_held():
    try:
        import portfolio
        return sorted({p['sym'] for p in portfolio.positions() or [] if p.get('shares')})
    except Exception:
        return []


def correlation(days=60):
    syms = [s for s in sorted(symbols()) if len(_cached_daily(s)) > 21]
    rets = {s: _returns(s, days) for s in syms}
    m = {}
    pairs = []
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            c = _corr(rets[a], rets[b])
            if c is None:
                continue
            c = round(c, 2)
            m[a + '|' + b] = c
            pairs.append({'a': a, 'b': b, 'c': c})
    pairs.sort(key=lambda p: -p['c'])
    return {'syms': syms, 'm': m, 'together': [p for p in pairs if p['c'] >= 0.7][:25],
            'opposite': [p for p in pairs if p['c'] <= -0.3][-10:], 'held': correlation_held(), 'days': days}


# ------------------------------------------------------------------ sector heatmap
def _etf_sessions(sym):
    """Pre-market / after-hours / regular moves for a sector fund, from Yahoo 5-minute bars
    (Yahoo has no 8 pm - 4 am prices; the board's own record is used when it has the fund)."""
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(sym) +
           '?range=5d&interval=5m&includePrePost=true')
    body = world._get(url)
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    ts = res.get('timestamp') or []
    cl = (((res.get('indicators') or {}).get('quote') or [{}])[0].get('close')) or []
    hist = [[t * 1000, c] for t, c in zip(ts, cl) if c]
    if not hist:
        return None
    now_ms = _now()
    w = _windows(now_ms)
    at = lambda ms: watch.price_at(hist, min(ms, now_ms))
    c = at(w['close'])
    return {'ah': _pct(at(w['ah_end']), c), 'on': None,
            'pre': _pct(at(w['open']), at(w['on_end'])) if now_ms > w['on_end'] else None,
            'reg': _pct(at(now_ms), at(w['open'])) if now_ms > w['open'] else None,
            'total': _pct(hist[-1][1], c), 'last': hist[-1][1]}


def heatmap(now_ms=None):
    now_ms = now_ms or _now()
    tiles = []
    for sym, kind in sorted(symbols().items()):
        with _lock:
            sec = (_c['sectors'].get(sym) or {}).get('sector')
        if sym in SECTOR_ETF:
            sec = SECTOR_ETF[sym]
        elif kind == 'etf':
            sec = 'Funds'
        tiles.append({'sym': sym, 'sector': sec or 'Other', 's': sessions(sym, now_ms)})
    with _lock:
        etf = {k: v for k, v in _c['etf'].items()}
    funds = []
    for sym, name in SECTOR_ETF.items():
        own = sessions(sym, now_ms)
        s = own or (etf.get(sym) or {}).get('s')
        funds.append({'sym': sym, 'sector': name, 's': s, 'src': 'board' if own else 'yahoo'})
    return {'tiles': tiles, 'funds': funds}


# ------------------------------------------------------------------ options
def options(sym, max_age=30 * 60):
    with _lock:
        hit = _c['options'].get(sym)
    if hit and time.time() - hit['at'] < max_age:
        return hit
    base = 'https://query2.finance.yahoo.com/v7/finance/options/' + urllib.parse.quote(_ysym(sym))
    body = world._get(base)
    res = ((body.get('optionChain') or {}).get('result') or [None])[0]
    if not res:
        out = {'sym': sym, 'at': time.time(), 'none': True}
        with _lock:
            _c['options'][sym] = out
        return out
    exps = res.get('expirationDates') or []
    today = watch.et(_now()).strftime('%Y-%m-%d')
    chain = (res.get('options') or [{}])[0]
    exp = chain.get('expirationDate') or (exps[0] if exps else None)
    # skip an expiry that ends today (0-day options) and use the next one
    # Yahoo gives expiry dates as midnight UTC of the expiry day, so read them in UTC
    exp_day = lambda e: time.strftime('%Y-%m-%d', time.gmtime(e))
    if exp and exp_day(exp) <= today and len(exps) > 1:
        exp = exps[1]
        body = world._get(base + '?date=' + str(exp))
        res2 = ((body.get('optionChain') or {}).get('result') or [None])[0] or {}
        chain = (res2.get('options') or [{}])[0]
    calls, puts = chain.get('calls') or [], chain.get('puts') or []
    price = ((res.get('quote') or {}).get('regularMarketPrice'))
    out = dict(analyse_chain(calls, puts, price), sym=sym, at=time.time(),
               expiry=time.strftime('%a %b %d', time.gmtime(exp)) if exp else '', price=price)
    with _lock:
        _c['options'][sym] = out
        _c['errors'].pop('options ' + sym, None)
    return out


def analyse_chain(calls, puts, price=None):
    """Max pain = the price where option buyers, all together, would get the least money at
    expiry (so option sellers keep the most). Put/call = how many puts vs calls."""
    oi = lambda o: o.get('openInterest') or 0
    vol = lambda o: o.get('volume') or 0
    strikes = sorted({o['strike'] for o in calls + puts if 'strike' in o})
    pain = []
    for k in strikes:
        v = sum(oi(c) * max(0.0, k - c['strike']) for c in calls) + sum(oi(p) * max(0.0, p['strike'] - k) for p in puts)
        pain.append((v, k))
    mp = min(pain)[1] if pain and any(oi(o) for o in calls + puts) else None
    c_oi, p_oi = sum(oi(c) for c in calls), sum(oi(p) for p in puts)
    c_v, p_v = sum(vol(c) for c in calls), sum(vol(p) for p in puts)
    top_c = sorted(calls, key=oi, reverse=True)[:3]
    top_p = sorted(puts, key=oi, reverse=True)[:3]
    return {'max_pain': mp, 'vs_price': _pct(mp, price) if mp and price else None,
            'pc_oi': round(p_oi / c_oi, 2) if c_oi else None, 'pc_vol': round(p_v / c_v, 2) if c_v else None,
            'calls_oi': c_oi, 'puts_oi': p_oi, 'calls_vol': c_v, 'puts_vol': p_v,
            'call_walls': [{'strike': o['strike'], 'oi': oi(o)} for o in top_c if oi(o)],
            'put_walls': [{'strike': o['strike'], 'oi': oi(o)} for o in top_p if oi(o)]}


# ------------------------------------------------------------------ background refresh
def _save_cache():
    with _lock:
        snap = {'daily': _c['daily'], 'sectors': _c['sectors'], 'options': _c['options'], 'etf': _c['etf']}
    _write(CACHE, snap)


def _loop():
    last = {'etf': 0, 'opt': 0, 'save': 0}
    while True:
        try:
            _record_gaps(_now())
        except Exception as e:
            print(f'analysis: gap record: {e!r}', flush=True)
        syms = dict(symbols())
        for b in BENCH:
            syms.setdefault(b, 'etf')
        # daily bars + sectors, one symbol at a time
        for sym, kind in sorted(syms.items()):
            with _lock:
                hit = _c['daily'].get(sym)
            if hit and time.time() - hit['at'] < 6 * 3600:
                continue
            _status['busy'] = 'daily ' + sym
            try:
                daily(sym)
                if kind == 'stock':
                    sector(sym)
            except Exception as e:
                _err('daily ' + sym, e)
                if '429' in str(e):
                    break
            time.sleep(2)
        if time.time() - last['etf'] > 15 * 60:
            last['etf'] = time.time()
            for sym in SECTOR_ETF:
                try:
                    s = _etf_sessions(sym)
                    with _lock:
                        _c['etf'][sym] = {'at': time.time(), 's': s}
                except Exception as e:
                    _err('sector ' + sym, e)
                    if '429' in str(e):
                        break
                time.sleep(2)
        if time.time() - last['opt'] > 30 * 60:
            last['opt'] = time.time()
            for sym in board_symbols():          # scanner-only symbols: look them up on the page
                try:
                    options(sym)
                except Exception as e:
                    _err('options ' + sym, e)
                    if '429' in str(e):
                        break
                time.sleep(3)
        _status['busy'] = ''
        if time.time() - last['save'] > 10 * 60:
            last['save'] = time.time()
            _save_cache()
        time.sleep(60)


def start():
    if _status['started']:
        return
    _status['started'] = True
    old = _read(CACHE, {})
    with _lock:
        for k in ('daily', 'sectors', 'options', 'etf'):
            if isinstance(old.get(k), dict):
                _c[k] = old[k]
    threading.Thread(target=_loop, name='analysis', daemon=True).start()


def snapshot():
    now_ms = _now()
    syms = symbols()
    cards = [scorecard(s, now_ms) for s in sorted(syms)]
    with _lock:
        errors = dict(_c['errors'])
        opts = {s: v for s, v in _c['options'].items() if s in syms}
    split = [{'sym': s, 's': sessions(s, now_ms)} for s in sorted(syms)]
    where = {}
    try:
        import news
        where = {s: e['where'] for s, e in news.tracked().items() if s in syms}
    except Exception:
        pass
    for s in correlation_held():
        where.setdefault(s, []).append('Portfolio')
    return json.dumps({'at': now_ms, 'gaps': cards, 'split': split, 'heat': heatmap(now_ms),
                       'rs': relative(now_ms), 'corr': correlation(), 'options': opts,
                       'where': where, 'windows': _windows(now_ms), 'note': _status['note'], 'busy': _status['busy'],
                       'errors': errors})


def option_one(query):
    """GET /api/analysis/options?sym=XYZ: one symbol's options right now (also for symbols not on a board)."""
    sym = query.get('sym') if isinstance(query, dict) else query
    if isinstance(sym, list):
        sym = sym[0] if sym else ''
    sym = (sym or '').strip().upper()[:12]
    if not sym or not all(ch.isalnum() or ch in '.-' for ch in sym):
        return json.dumps({'error': 'Type a stock symbol, like AAPL.'})
    try:
        return json.dumps(options(sym))
    except Exception as e:
        return json.dumps({'sym': sym, 'error': 'Could not get options from Yahoo right now (' + str(e)[:80] + ').'})
