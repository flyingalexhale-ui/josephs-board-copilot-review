"""Futures mood and Picks mood meters (jb-meters), running inside the gateway.

Two meters in the same style as "Mood since the 4 pm close": one for the stock index futures
(S&P, Nasdaq, Dow, Russell), one for the scanner's Picks. Each can be read over five rolling windows,
each starting at the most recent time that moment passed (New York time, NYSE holidays skipped):
    open        9:30 am  regular session open
    close       4:00 pm  regular close (1:00 pm on early-close days)
    after       4:00 pm  after-hours start (the same moment as the close, so the same number)
    overnight   8:00 pm  overnight session start (evenings before a trading day)
    premarket   4:00 am  pre-market start
Score -100 .. +100 like the mood meter: each symbol's move is squashed (tanh) so one big mover can't
dominate, then averaged (S&P and Nasdaq count double in the futures meter).
Prices: Yahoo 5-minute bars including pre/after hours (refreshed every 5 minutes, through the World
tab's Yahoo connection). For Picks, the board's own live price record (IBKR, incl. overnight) is
used first when it covers the time, because Yahoo has no overnight stock prices.
  GET /api/plus/meters      {windows, futures, picks, updated, note}
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'lib'))
try:
    from nyse_hours import trading_day, nyse_early_closes
except Exception:
    def trading_day(d):
        return d.weekday() < 5

    def nyse_early_closes(y):
        return frozenset()

REFRESH_S = 300
FUTURES = [('ES=F', 'S&P', 2.0, 0.8), ('NQ=F', 'Nasdaq', 2.0, 1.0), ('YM=F', 'Dow', 1.0, 0.8), ('RTY=F', 'Russell', 1.0, 1.0)]
PICK_FULL = 1.5            # a pick up 1.5% counts about as much as it can (tanh)
MAX_PICKS = 12
WINDOWS = [('open', 'Open', 'since the 9:30 am open'), ('close', 'Close', 'since the 4 pm close'),
           ('after', 'After-hrs', 'since after-hours began'), ('overnight', 'Overnight', 'since the overnight session began'),
           ('premarket', 'Pre-mkt', 'since pre-market began')]

_lock = threading.Lock()
_bars = {}                 # symbol -> [(t_ms, open, close)] 5-minute bars, newest last
_picks = {'list': [], 'mode': '', 'at': 0, 'error': ''}
_state = {'at': 0, 'error': '', 'started': False}


# ------------------------------------------------------------------ window start times
def _day_ok(day):
    try:
        return trading_day(day)
    except Exception:
        return day.weekday() < 5


def anchors(now_ms):
    """Window key -> start time (ms): the latest such moment that has passed."""
    d = watch.et(now_ms)
    out = {}
    for back in range(0, 12):
        day = d.date() - timedelta(days=back)
        nxt = day + timedelta(days=1)
        cands = []
        if _day_ok(day):
            early = day in nyse_early_closes(day.year)
            cands += [('open', 9, 30), ('premarket', 4, 0)]
            cands += [('close', 13 if early else 16, 0), ('after', 13 if early else 16, 0)]
        if _day_ok(nxt):
            cands.append(('overnight', 20, 0))
        for key, hh, mm in cands:
            t = watch.at_et(day, hh, mm)
            if key not in out and t <= now_ms:
                out[key] = t
        if len(out) == len(WINDOWS):
            break
    return out


# ------------------------------------------------------------------ prices
def _yahoo_bars(sym):
    import world
    host = world.HOSTS[0]
    body = world._get(f'https://{host}/v8/finance/chart/' + urllib.parse.quote(sym) +
                      '?range=5d&interval=5m&includePrePost=true')
    res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
    q = ((res.get('indicators') or {}).get('quote') or [{}])[0]
    ts, op, cl = res.get('timestamp') or [], q.get('open') or [], q.get('close') or []
    bars = [(int(t) * 1000, float(o or c), float(c)) for t, o, c in zip(ts, op, cl) if c]
    meta = res.get('meta') or {}
    if bars and meta.get('regularMarketPrice') and (meta.get('regularMarketTime') or 0) * 1000 > bars[-1][0]:
        bars.append((int(meta['regularMarketTime']) * 1000, float(meta['regularMarketPrice']), float(meta['regularMarketPrice'])))
    return bars


def _bar_price(bars, ms):
    """Price at a moment: the opening price of the first bar starting then (within 30 min),
    else the last price before it."""
    before = None
    for t, o, c in bars:
        if t >= ms:
            if t - ms <= 30 * 60000:
                return o
            break
        before = c
    return before


def _live(sym):
    with watch._lock:
        return list(watch._px.get(sym) or [])


def _price_pair(sym, start_ms, now_ms, use_live):
    """(price at start, price now) or (None, None)."""
    bars = _bars.get(sym) or []
    hist = _live(sym) if use_live else []
    now_p = None
    if hist and now_ms - hist[-1][0] < 15 * 60000:
        now_p = hist[-1][1]
    elif bars:
        now_p = bars[-1][2]
    start_p = None
    if hist and hist[0][0] <= start_ms:
        start_p = watch.price_at(hist, start_ms)
    if start_p is None and bars:
        start_p = _bar_price(bars, start_ms)
    return start_p, now_p


def _pct(a, b):
    return round((b / a - 1) * 100, 2) if a and b else None


def _label(score, words):
    return words[0] if score >= 25 else words[2] if score <= -25 else words[1]


# ------------------------------------------------------------------ picks list (from the scanner)
def _read_picks():
    from websockets.sync.client import connect
    with connect('ws://127.0.0.1:8767', open_timeout=5, close_timeout=2, max_size=2 ** 23) as ws:
        tok = os.environ.get('MOVER_SCANNER_TOKEN', '')
        if tok:
            ws.send(json.dumps({'type': 'auth', 'token': tok}))
        for _ in range(20):
            m = json.loads(ws.recv(timeout=10))
            if isinstance(m, dict) and m.get('type') == 'snapshot':
                p = m.get('picks') or {}
                lst = [x for x in (p.get('picks') or []) if isinstance(x, dict) and x.get('symbol')]
                return lst[:MAX_PICKS], m.get('mode') or ''
    return [], ''


# ------------------------------------------------------------------ refresh loop
def refresh():
    import world
    if time.time() < (world._status.get('wait_until') or 0):
        return                                  # Yahoo asked the board to slow down
    try:
        lst, mode = _read_picks()
        with _lock:
            _picks.update(list=[{'sym': x['symbol'], 'lean': x.get('lean') or ''} for x in lst], mode=mode, at=time.time(), error='')
    except Exception as e:
        _picks['error'] = f'Scanner not reachable ({type(e).__name__})'
    syms = [s for s, _, _, _ in FUTURES] + [p['sym'] for p in _picks['list']]
    errs = []
    for s in syms:
        try:
            b = _yahoo_bars(s)
            if b:
                with _lock:
                    _bars[s] = b
        except Exception as e:
            errs.append(s)
            if '429' in str(e) or 'Too Many' in str(e):
                break
        time.sleep(1.5)                         # spaced out, Yahoo limits how often we may ask
    with _lock:
        for s in list(_bars):
            if s not in syms:
                del _bars[s]
    _state.update(at=time.time(), error=('No Yahoo prices for ' + ', '.join(errs)) if errs else '')


def _loop():
    time.sleep(20)
    while True:
        try:
            refresh()
        except Exception as e:
            _state['error'] = str(e)[:200]
            print(f'meters: {e!r}', flush=True)
        time.sleep(REFRESH_S)


def start():
    if _state['started']:
        return
    _state['started'] = True
    threading.Thread(target=_loop, name='meters', daemon=True).start()


# ------------------------------------------------------------------ the two meters
def snapshot(now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    starts = anchors(now_ms)
    fut, pk = {}, {}
    with _lock:
        picks = list(_picks['list'])
    for key, _, _ in WINDOWS:
        a = starts.get(key)
        if a is None:
            continue
        # futures
        parts, tot, wsum = [], 0.0, 0.0
        for sym, name, w, full in FUTURES:
            p0, p1 = _price_pair(sym, a, now_ms, False)
            v = _pct(p0, p1)
            if v is None:
                continue
            tot += w * math.tanh(v / full)
            wsum += w
            parts.append({'name': name, 'pct': v})
        if wsum:
            sc = int(round(100 * tot / wsum))
            fut[key] = {'score': sc, 'label': _label(sc, ('Risk-on', 'Mixed', 'Risk-off')), 'parts': parts}
        # picks
        rows, tot = [], 0.0
        for p in picks:
            p0, p1 = _price_pair(p['sym'], a, now_ms, True)
            v = _pct(p0, p1)
            if v is None:
                continue
            tot += math.tanh(v / PICK_FULL)
            rows.append({'sym': p['sym'], 'pct': v, 'lean': p['lean']})
        if rows:
            sc = int(round(100 * tot / len(rows)))
            rows.sort(key=lambda r: -r['pct'])
            pk[key] = {'score': sc, 'label': _label(sc, ('Bullish', 'Mixed', 'Bearish')), 'parts': rows,
                       'up': sum(1 for r in rows if r['pct'] > 0), 'down': sum(1 for r in rows if r['pct'] < 0),
                       'avg': round(sum(r['pct'] for r in rows) / len(rows), 2)}
    note = ''
    if not _state['at']:
        note = 'Getting prices (about a minute after a restart)…'
    elif _picks['error'] and not picks:
        note = _picks['error']
    return {'server_ms': now_ms, 'updated': int(_state['at'] * 1000),
            'windows': [{'key': k, 'name': n, 'what': w, 'from_ms': starts.get(k)} for k, n, w in WINDOWS],
            'futures': fut, 'picks': pk, 'picks_mode': _picks['mode'], 'n_picks': len(picks), 'note': note,
            'error': _state['error']}


# ------------------------------------------------------------------ doorway
def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    return 404, {'error': 'unknown'}
