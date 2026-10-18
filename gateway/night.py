"""Overnight and session features (jb-night), running inside the gateway. All free.

  * Spreads: the IBKR bridge now also sends each stock's bid and ask (best prices to sell and
    to buy) with how many shares are on each side. /api/plus/night/quotes hands them to the
    boards, which show a SPREAD chip on each IBKR tile. Most useful overnight, when spreads widen.
  * Overnight high / low break (phone push): after the 9:30 open, a stock crossing above its
    overnight high or below its overnight low (8 pm - 4 am ET). Once per stock per side per day.
  * Pre-market high / low break (phone push): after the 9:30 open, crossing the 4:00-9:30 am
    high or low. Once per stock per side per day.
  * Overnight venue watch (phone push): overnight, if every stock that was trading on IBKR's
    overnight venue stops at the same moment while futures keep ticking, the venue itself has
    probably stopped (as Blue Ocean did on Aug 5, 2024). One push when it looks down, one when
    prices come back. The boards also show a red notice meanwhile.
Uses the board's own price record (gateway/watch.py). Nothing here places trades.
Files: data/night_settings.json (the switches on the Night desk tab), data/night_log.json.
"""
import asyncio
import json
import os
import sys
import threading
import time
from datetime import timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
SETTINGS = os.path.join(DATA, 'night_settings.json')
LOG = os.path.join(DATA, 'night_log.json')
sys.path.insert(0, os.path.join(ROOT, 'lib'))
try:
    from nyse_hours import trading_day, nyse_early_closes
except Exception:
    def trading_day(d):
        return d.weekday() < 5

    def nyse_early_closes(y):
        return frozenset()


def _close_min(day):
    """Regular-hours close in minutes after midnight: 1 pm on early-close days, else 4 pm."""
    return 780 if day in nyse_early_closes(day.year) else 960

DEFAULTS = {'on_break': True, 'pm_break': True, 'venue': True}
QUOTE_KEEP_MS = 26 * 3600 * 1000        # minute-by-minute bid / ask kept in memory (fill quality uses it)
VENUE_QUIET_MS = 15 * 60000             # every overnight stock silent this long = venue looks down
VENUE_ACTIVE_MS = 30 * 60000            # ...counting only stocks that traded in the 30 minutes before
VENUE_MIN_STOCKS = 5

_lock = threading.Lock()
_q = {}                 # sym -> {'bid','ask','bs','as','ts'}
_qh = {}                # sym -> [[minute_ms, bid, ask], ...]
_prints = []            # big single trades from the IBKR bridge: {'sym','price','size','ts'} (last 2 days)
_st = {'started': False, 'connected': False, 'venue': {'down': False, 'since': None, 'checked': None, 'stocks': 0},
       'last': {}, 'sent': {}}


# ------------------------------------------------------------------ small helpers
def _now():
    return int(time.time() * 1000)


def settings():
    s = watch._read(SETTINGS, {})
    return {k: bool(s.get(k, d)) for k, d in DEFAULTS.items()}


def _log(kind, sym, text):
    log = watch._read(LOG, [])
    log.insert(0, {'at': _now(), 'kind': kind, 'sym': sym, 'text': text})
    watch._write(LOG, log[:200])


def _send(kind, sym, title, text, tags='bell'):
    if sym and (kind.endswith(' high') or kind.endswith(' low')):        # jb-why: the board's own reason
        try:
            import why
            w = why.line(sym)
            if w:
                text = text + ' Why: ' + w + '.'
        except Exception:
            pass
    _log(kind, sym, text)
    print(f'night: {kind} {sym}: {text}', flush=True)
    watch.push_later(title, text, tags)


def _symbols():
    try:
        import news
        return [s for s, e in news.tracked().items() if e['kind'] in ('stock', 'etf')]
    except Exception:
        with watch._lock:
            return [s for s in watch._px if '=' not in s]


def session_at(ms):
    """'overnight' | 'pre-market' | 'regular' | 'after-hours' | 'closed' (New York time, holidays known)."""
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    today = trading_day(d.date())
    if m < 240:
        return 'overnight' if today else 'closed'
    nxt = d.date() + timedelta(days=1)
    if not today:
        return 'overnight' if m >= 1200 and trading_day(nxt) else 'closed'
    if m < 570:
        return 'pre-market'
    close = _close_min(d.date())
    if m < close:
        return 'regular'
    if m < close + 240:
        return 'after-hours'
    if m < 1200:
        return 'closed'                             # early-close day: after-hours ended at 5 pm
    return 'overnight' if trading_day(nxt) else 'closed'


def _hist(sym):
    with watch._lock:
        return list(watch._px.get(sym) or [])


def _range(hist, a, b):
    px = [p for t, p in hist if a <= t < b]
    return (max(px), min(px)) if px else (None, None)


# ------------------------------------------------------------------ quotes from the IBKR bridge
def _quote(m):
    sym = m.get('symbol')
    try:
        bid, ask = float(m.get('bid')), float(m.get('ask'))
    except (TypeError, ValueError):
        return
    if not sym or not (0 < bid <= ask):
        return
    ts = int(m.get('ts') or _now())
    with _lock:
        _q[sym] = {'bid': bid, 'ask': ask, 'bs': m.get('bs') or 0, 'as': m.get('as') or 0, 'ts': ts}
        h = _qh.setdefault(sym, [])
        mn = ts - ts % 60000
        if h and h[-1][0] == mn:
            h[-1] = [mn, bid, ask]
        else:
            h.append([mn, bid, ask])
            cut = ts - QUOTE_KEEP_MS
            while h and h[0][0] < cut:
                h.pop(0)


def _print(m):
    try:
        p = {'sym': str(m['symbol']), 'price': float(m['price']), 'size': int(m['size']), 'ts': int(m['ts'])}
    except (KeyError, TypeError, ValueError):
        return
    with _lock:
        _prints.append(p)
        cut = p['ts'] - 2 * 86400000
        while _prints and (_prints[0]['ts'] < cut or len(_prints) > 2000):
            _prints.pop(0)


def prints(sym=None):
    """Big single trades (blocks) seen on the IBKR feed, newest first."""
    with _lock:
        lst = [p for p in _prints if sym is None or p['sym'] == sym]
    return lst[::-1]


def quote_at(sym, ms, within_ms=3 * 60000):
    """(bid, ask) at about that moment, from the minute record, or None."""
    with _lock:
        h = list(_qh.get(sym) or [])
    best = None
    for t, b, a in h:
        if t > ms + 60000:
            break
        if abs(t - ms) <= within_ms + 60000:
            best = (b, a)
    return best


async def _listen():
    import websockets
    port, tok = 8765, os.environ.get('IBKR_BRIDGE_TOKEN', '')
    delay = 2
    while True:
        try:
            async with websockets.connect(f'ws://127.0.0.1:{port}', max_size=2 ** 22) as ws:
                if tok:
                    await ws.send(json.dumps({'type': 'auth', 'token': tok}))
                _st['connected'] = True
                delay = 2
                async for raw in ws:
                    if '"quote"' not in raw[:40] and '"print"' not in raw[:40]:
                        continue
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    if m.get('type') == 'quote':
                        _quote(m)
                    elif m.get('type') == 'print':
                        _print(m)
        except Exception:
            pass
        _st['connected'] = False
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


# ------------------------------------------------------------------ alerts
def check_breaks(cfg, now_ms):
    """Overnight and pre-market high / low crossed during regular hours (prices must keep coming)."""
    if session_at(now_ms) != 'regular' or not (cfg['on_break'] or cfg['pm_break']):
        _st['last'].clear()
        return
    d = watch.et(now_ms).date()
    on_s, on_e = watch.overnight_window(now_ms)
    if watch.et(on_e).date() != d:                     # no overnight session led into today
        on_s = on_e = None
    pm_s, pm_e = watch.at_et(d, 4, 0), watch.at_et(d, 9, 30)
    sent = _st['sent']
    for sym in _symbols():
        hist = _hist(sym)
        if len(hist) < 2 or now_ms - hist[-1][0] > 5 * 60000:
            continue
        last = hist[-1][1]
        prev = _st['last'].get(sym)
        _st['last'][sym] = last
        if prev is None:
            continue
        checks = []
        if cfg['on_break'] and on_s:
            hi, lo = _range(hist, on_s, on_e)
            checks.append(('overnight', hi, lo))
        if cfg['pm_break']:
            hi, lo = _range(hist, pm_s, pm_e)
            checks.append(('pre-market', hi, lo))
        for name, hi, lo in checks:
            if hi is not None and prev <= hi < last and f'{name} hi {sym} {d}' not in sent:
                sent[f'{name} hi {sym} {d}'] = now_ms
                _send(f'{name} high', sym, f'{sym} above {name} high',
                      f"{sym} just broke above its {name} high of {hi:g} after the open, now {last:g}.", 'arrow_up')
            if lo is not None and prev >= lo > last and f'{name} lo {sym} {d}' not in sent:
                sent[f'{name} lo {sym} {d}'] = now_ms
                _send(f'{name} low', sym, f'{sym} below {name} low',
                      f"{sym} just broke below its {name} low of {lo:g} after the open, now {last:g}.", 'arrow_down')
    for k in [k for k, t in sent.items() if now_ms - t > 3 * 86400000]:
        del sent[k]


def venue_state(now_ms):
    """Overnight: did every stock that was trading on the overnight venue go quiet at once?"""
    v = _st['venue']
    if session_at(now_ms) != 'overnight':
        return dict(v, down=False, stocks=0, why='')
    with watch._lock:
        rows = [(s, h[-1][0], [t for t, _ in h[-40:]]) for s, h in watch._px.items()
                if h and watch._src.get(s) == 'ibkr' and '=' not in s]
        fut = [h[-1][0] for s, h in watch._px.items() if h and s.endswith('=F') and watch._src.get(s) == 'ibkr']
    on_s, _ = watch.overnight_window(now_ms)
    # stocks that traded overnight in the half hour before the quiet window began
    active = [(s, last) for s, last, ts in rows
              if any(max(on_s, now_ms - VENUE_QUIET_MS - VENUE_ACTIVE_MS) <= t for t in ts)]
    futures_alive = any(now_ms - t < 3 * 60000 for t in fut)
    quiet = [s for s, last in active if now_ms - last >= VENUE_QUIET_MS]
    down = len(active) >= VENUE_MIN_STOCKS and len(quiet) == len(active) and futures_alive
    return {'down': down, 'stocks': len(active), 'quiet': len(quiet), 'futures_alive': futures_alive,
            'active': [x for x, _ in active], 'last': {x: last for x, last, _ in rows}}


def check_venue(cfg, now_ms):
    v = _st['venue']
    s = venue_state(now_ms)
    v['checked'] = now_ms
    v['stocks'] = s.get('stocks', 0)
    if v['down'] and session_at(now_ms) == 'overnight':
        # back only when one of the stocks that went quiet really ticks again (the active window slides,
        # so "not down" alone would come back true after a while even if nothing traded)
        s['down'] = not any((s['last'].get(x) or 0) > v['since'] for x in v.get('set') or [])
    if s['down'] and not v['down']:
        v['down'], v['since'], v['set'] = True, now_ms, s.get('active') or []
        if cfg['venue']:
            _send('venue down', '', 'Overnight prices stopped',
                  f"Heads up: overnight stock prices froze. All {s['stocks']} stocks trading overnight on IBKR stopped updating {VENUE_QUIET_MS // 60000}+ minutes ago, "
                  "while futures keep moving. The overnight venue may be down: don't trust frozen overnight prices, "
                  "and orders may not fill or could be cancelled.", 'warning')
    elif v['down'] and not s['down'] and session_at(now_ms) == 'overnight':
        mins = round((now_ms - (v['since'] or now_ms)) / 60000)
        v['down'], v['since'], v['set'] = False, None, []
        if cfg['venue']:
            _send('venue back', '', 'Overnight prices are back', f'Overnight stock prices are updating again (they stopped for about {mins} min).', 'white_check_mark')
    elif v['down'] and session_at(now_ms) != 'overnight':
        v['down'], v['since'], v['set'] = False, None, []


# ------------------------------------------------------------------ thread + API
async def _main():
    asyncio.ensure_future(_listen())
    while True:
        await asyncio.sleep(5)
        try:
            cfg = settings()
            now = _now()
            check_breaks(cfg, now)
            check_venue(cfg, now)
        except Exception as e:           # never let the thread die
            print(f'night error: {e!r}', flush=True)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=lambda: asyncio.run(_main()), name='night', daemon=True).start()


def quotes():
    now = _now()
    with _lock:
        return {s: q for s, q in _q.items() if now - q['ts'] < 15 * 60000}


def fills_by_hour():
    """Your fills (My trading journal) counted by New York clock hour, outside regular hours."""
    try:
        import trading
        with trading._lock:
            fills = list(trading._journal()['fills'].values())
    except Exception:
        return {}
    out = {}
    for f in fills:
        d = watch.et(f['t'])
        m = d.hour * 60 + d.minute
        if 570 <= m < 960 and d.weekday() < 5:
            continue
        out[d.hour] = out.get(d.hour, 0) + 1
    return out


def snapshot():
    now = _now()
    v = _st['venue']
    return {'settings': settings(), 'session': session_at(now), 'server_ms': now,
            'venue': {'down': v['down'], 'since': v['since'], 'stocks': v['stocks'], 'checked': v['checked']},
            'quotes_connected': _st['connected'], 'fills_by_hour': fills_by_hour(), 'log': watch._read(LOG, [])[:30]}


def get(sub, query):
    if sub == 'quotes':
        return 200, {'quotes': quotes(), 'venue_down': _st['venue']['down'], 'server_ms': _now()}
    if sub in ('', 'status'):
        return 200, snapshot()
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'settings':
        s = settings()
        for k in DEFAULTS:
            if k in (data.get('settings') or {}):
                s[k] = bool(data['settings'][k])
        watch._write(SETTINGS, s)
        return 200, snapshot()
    return 404, {'error': 'unknown'}
