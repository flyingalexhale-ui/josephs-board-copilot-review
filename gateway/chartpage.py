"""The Chart page (jb-navchart): type any ticker and see its line chart.

Prices come from IBKR first (your own IB Gateway, including the overnight venue for stocks and
ETFs), through the gateway's shared IB link (iblink.py). If IBKR can't give them (IB Gateway not
logged in, a symbol your IBKR data plan doesn't cover such as some NYSE stocks, indexes like ^N225,
crypto), the page falls back to free Yahoo prices and says so. The board's own live price is added
at the end when it is newer.

  GET /api/plus/chartpage/bars?sym=TQQQ&range=5D      range: 1D 5D 1M 6M 1Y 5Y ALL
  GET /api/plus/chartpage/flow?sym=TQQQ               order flow for the 1D view (orderflow.py)
Nothing is saved; answers are kept 55 seconds so a phone left open doesn't use up IBKR's limits.
"""
import asyncio
import re
import threading
import time
import urllib.parse

import watch

RANGES = {   # IBKR duration, bar size, regular hours only, overnight venue too, Yahoo range, Yahoo interval
    '1D': ('2 D', '2 mins', False, True, '2d', '2m'),
    '5D': ('5 D', '5 mins', False, True, '5d', '5m'),
    '1M': ('1 M', '30 mins', False, True, '1mo', '30m'),
    '6M': ('6 M', '1 day', True, False, '6mo', '1d'),
    '1Y': ('1 Y', '1 day', True, False, '1y', '1d'),
    '5Y': ('5 Y', '1 week', True, False, '5y', '1wk'),
    'ALL': ('30 Y', '1 month', True, False, 'max', '1mo'),      # everything IBKR / Yahoo has, one point a month
}
FUT = {'ES': 'CME', 'NQ': 'CME', 'RTY': 'CME', 'YM': 'CBOT', 'MES': 'CME', 'MNQ': 'CME', 'GC': 'COMEX', 'CL': 'NYMEX'}
_cache = {}
_lock = threading.Lock()
_ib_lock = threading.Lock()      # one IBKR history request at a time (IBKR's pacing rules)


def _clean(s):
    s = str(s or '').strip().upper()
    return s if re.fullmatch(r'[A-Z0-9.\-^=]{1,15}', s) else ''


def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return v or ''


def _ms_of(d):
    if hasattr(d, 'hour'):
        return int(d.timestamp() * 1000)
    return watch.at_et(d, 16, 0)          # a daily bar: that day's 4 pm close


def _ibkr(sym, rng):
    import iblink
    dur, size, rth, night, _, _ = RANGES[rng]
    span = {'2 mins': 120000, '5 mins': 300000, '30 mins': 1800000}.get(size, 0)

    async def go(ib):
        from ib_async import ContFuture, Stock
        if sym.endswith('=F'):
            root = sym[:-2]
            if root not in FUT:
                raise RuntimeError('not an IBKR future the board knows')
            q = await asyncio.wait_for(ib.qualifyContractsAsync(ContFuture(root, FUT[root])), 10)
            if not q or q[0] is None:
                raise RuntimeError(f'IBKR did not recognise {sym}')
            c, night_ok = q[0], False
        else:
            c, night_ok = await iblink.stock(ib, sym), night
        bars = await ib.reqHistoricalDataAsync(c, '', dur, size, 'TRADES', rth, 2, timeout=25) or []
        pts = [{'t': min(_ms_of(b.date) + span, int(time.time() * 1000)), 'p': b.close} for b in bars if b.close and b.close > 0]
        extra = 0
        if night_ok and pts:
            try:
                on = Stock(c.symbol, 'OVERNIGHT', 'USD', primaryExchange=c.primaryExchange)
                q2 = await asyncio.wait_for(ib.qualifyContractsAsync(on), 10)
                if q2 and q2[0] is not None and on.conId:
                    nb = await ib.reqHistoricalDataAsync(on, '', dur, size, 'TRADES', False, 2, timeout=25) or []
                    have = {p['t'] for p in pts}
                    for b in nb:
                        t = min(_ms_of(b.date) + span, int(time.time() * 1000))
                        if b.close and b.close > 0 and t not in have:
                            pts.append({'t': t, 'p': b.close})
                            extra += 1
            except Exception:
                pass
        pts.sort(key=lambda p: p['t'])
        return pts, extra

    with _ib_lock:
        pts, extra = iblink.run(go, timeout=60)
    if not pts:
        raise RuntimeError('IBKR sent no prices (your IBKR data plan may not cover it)')
    return {'points': pts, 'src': 'IBKR' + (' + overnight' if extra else '')}


def _yahoo(sym, rng):
    import world
    _, _, _, _, yr, yi = RANGES[rng]
    q = urllib.parse.quote(sym if ('=' in sym or '^' in sym or '-USD' in sym) else sym.replace('.', '-'))
    body = world._get(f'https://{world.HOSTS[0]}/v8/finance/chart/{q}?range={yr}&interval={yi}&includePrePost=true', timeout=15)
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    meta = res.get('meta') or {}
    closes = (((res.get('indicators') or {}).get('quote') or [{}])[0].get('close') or [])
    pts = []
    for t, c in zip(res.get('timestamp') or [], closes):
        if not c:
            continue
        t = int(t) * 1000
        if yi in ('1d', '1wk', '1mo'):
            t = watch.at_et(watch.et(t).date(), 16, 0)
        pts.append({'t': t, 'p': round(c, 6)})
    if not pts:
        raise RuntimeError(f'Yahoo has no prices for {sym}')
    return {'points': pts, 'src': 'Yahoo' + (' (futures and indexes can lag 10-15 min)' if ('=' in sym or '^' in sym) else ''),
            'name': meta.get('shortName') or meta.get('longName') or ''}


def bars(sym, rng):
    sym, rng = _clean(sym), (rng or '5D').upper()
    if not sym:
        return {'sym': '', 'points': [], 'error': 'Type a ticker, like TQQQ, SPY or NQ=F.'}
    if rng not in RANGES:
        rng = '5D'
    key = (sym, rng)
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < 55:
        return hit[1]
    out, why = None, ''
    if '^' not in sym and '-USD' not in sym and not (sym.endswith('=F') and sym[:-2] not in FUT) and not sym.endswith('=X'):
        try:
            out = _ibkr(sym, rng)
        except Exception as e:
            why = str(e)[:140]
    if out is None:
        try:
            out = _yahoo(sym, rng)
            if why:
                out['note'] = 'IBKR: ' + why + '. Showing Yahoo instead.'
        except Exception as e:
            ye = str(e) if 'no prices' in str(e) else 'did not answer (try again in a minute)'
            return {'sym': sym, 'range': rng, 'points': [], 'error': f'No prices found for {sym}. ' + (f'IBKR: {why}. ' if why else '') + f'Yahoo: {ye[:120]}.'}
    with watch._lock:                      # the board's own newest price, if it streams this symbol
        live = list(watch._px.get(sym) or [])[-1:]
    if live and out['points'] and live[0][0] > out['points'][-1]['t'] and time.time() * 1000 - live[0][0] < 15 * 60000:
        out['points'].append({'t': live[0][0], 'p': live[0][1]})
    out.update(sym=sym, range=rng, at=int(time.time() * 1000))
    with _lock:
        _cache[key] = (time.time(), out)
        for k in [k for k, v in _cache.items() if time.time() - v[0] > 600]:
            del _cache[k]
    return out


def get(sub, query):
    if sub == 'bars':
        return 200, bars(_q(query, 'sym'), _q(query, 'range'))
    if sub == 'flow':                      # order flow (jb-orderflow): gateway/orderflow.py
        import orderflow
        return 200, orderflow.snapshot(_clean(_q(query, 'sym')))
    if sub in ('', 'status'):
        return 200, {'ranges': list(RANGES)}
    return 404, {'error': 'unknown'}
