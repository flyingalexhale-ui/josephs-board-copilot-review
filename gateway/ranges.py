"""1-week and 52-week range bars on the IBKR / Webull boards (jb-ranges).

GET /api/ranges?syms=AAPL,TQQQ,ES=F  ->  {"at": ms, "r": {"AAPL": {"hi": .., "hiD": "2026-07-14",
    "lo": .., "loD": "2025-11-03", "days": [["2026-10-01", high, low], ... last 8 days]}}}

The year of daily highs and lows comes from Yahoo through the Analysis tab's own cache
(analysis.daily: one request per symbol every 6 hours, shared with the Analysis tab). Symbols the
Analysis tab doesn't follow (futures, ones just added) are fetched here in the background, one
every 3 seconds, so a page asking never waits on Yahoo. The page adds its own price record on top
(after-hours and overnight included), so a new high or low shows right away.
"""
import json
import threading
import time

import analysis

_lock = threading.Lock()
_want = []                  # symbols waiting for their first Yahoo year
_tried = {}                 # sym -> time of the last fetch attempt
_status = {'thread': False}


def _ok(sym):
    return 0 < len(sym) <= 12 and all(ch.isalnum() or ch in '.-=^' for ch in sym)


def _r(p):
    return round(p, 2) if p >= 1 else round(p, 6)      # Yahoo sends 236.2922973632812


def _summary(rows):
    hi = lo = None
    for r in rows:
        day, h, l = r[0], r[2], r[3]
        if hi is None or h > hi[0]:
            hi = (h, day)
        if lo is None or l < lo[0]:
            lo = (l, day)
    if hi is None:
        return None
    return {'hi': _r(hi[0]), 'hiD': hi[1], 'lo': _r(lo[0]), 'loD': lo[1],
            'days': [[r[0], _r(r[2]), _r(r[3])] for r in rows[-8:]]}


def _worker():
    while True:
        with _lock:
            sym = _want.pop(0) if _want else None
        if sym is None:
            time.sleep(2)
            continue
        try:
            analysis.daily(sym)
        except Exception as e:
            print(f'ranges: {sym}: {str(e)[:120]}', flush=True)
            if '429' in str(e):          # Yahoo asked to slow down: try the rest later
                with _lock:
                    _want.clear()
                time.sleep(300)
        time.sleep(3)


def snapshot(query):
    syms = query.get('syms') if isinstance(query, dict) else query
    if isinstance(syms, list):
        syms = syms[0] if syms else ''
    out = {}
    now = time.time()
    for sym in (syms or '').upper().split(',')[:80]:
        sym = sym.strip()
        if not _ok(sym):
            continue
        rows = analysis._cached_daily(sym)
        if rows:
            s = _summary(rows)
            if s:
                out[sym] = s
        if not rows or now - analysis._c['daily'].get(sym, {}).get('at', 0) > 7 * 3600:
            with _lock:
                if now - _tried.get(sym, 0) > 3600 and sym not in _want:
                    _tried[sym] = now
                    _want.append(sym)
    with _lock:
        if _want and not _status['thread']:
            _status['thread'] = True
            threading.Thread(target=_worker, name='ranges', daemon=True).start()
    return json.dumps({'at': int(now * 1000), 'r': out})
