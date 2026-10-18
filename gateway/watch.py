"""Price alerts and the morning summary cards, running inside the gateway.

One background thread listens to both price bridges (the same live prices the boards get) and:
  * checks your alerts every 2 seconds:  "NVDA above 140", "AAPL below 220",
    "TSLA moves 2% vs. the 4 pm close". A hit shows on the boards and is pushed to your phone
    through the free ntfy app (https://ntfy.sh), using the private topic in settings.env
    (JB_NTFY_TOPIC, made by add-watch.py).
  * at 7:00 am and 9:00 am New York time (weekdays) writes a morning card: the biggest moves
    since the close, the overnight session's moves, and which symbols broke their overnight
    high or low. Shown at the top of the boards and pushed to the phone.

Files (in data/):  alerts.json (your alerts), alerts_log.json (recent hits), morning.json.
Nothing here trades or changes a watchlist.
"""
import asyncio
import json
import os
import ssl
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
ALERTS = os.path.join(DATA, 'alerts.json')
LOG = os.path.join(DATA, 'alerts_log.json')
MORNING = os.path.join(DATA, 'morning.json')
BRIDGES = {'ibkr': (8765, 'IBKR_BRIDGE_TOKEN'), 'webull': (8766, 'WEBULL_BRIDGE_TOKEN')}
KEEP_S = 100 * 3600                    # price record kept per symbol: Friday's close is still there on Monday
                                       # morning and after a holiday (review 2026-10-11 #11, #12; was 40 h)
CARD_TIMES = ((7, 0, '7:00 am'), (9, 0, '9:00 am'))

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo('America/New_York')
except Exception:                      # no time-zone data: US rule, UTC-4 in summer / UTC-5 in winter
    class _NewYork(__import__('datetime').tzinfo):
        @staticmethod
        def _bounds(y):
            mar = datetime(y, 3, 8); nov = datetime(y, 11, 1)
            return mar + timedelta(days=(6 - mar.weekday()) % 7, hours=2), nov + timedelta(days=(6 - nov.weekday()) % 7, hours=2)
        def _dst(self, dt):
            if dt is None: return False
            a, b = self._bounds(dt.year); w = dt.replace(tzinfo=None)
            return a <= w < b - timedelta(hours=1)
        def utcoffset(self, dt): return timedelta(hours=-4 if self._dst(dt) else -5)
        def dst(self, dt): return timedelta(hours=1 if self._dst(dt) else 0)
        def tzname(self, dt): return 'EDT' if self._dst(dt) else 'EST'
        def fromutc(self, dt):
            a, b = self._bounds(dt.year); u = dt.replace(tzinfo=None)
            return dt + timedelta(hours=-4 if a + timedelta(hours=5) <= u < b + timedelta(hours=4) else -5)
    ET = _NewYork()

_lock = threading.Lock()
_px = {}           # symbol -> list of [t_ms, price] (1-minute steps, newest last)
_src = {}          # symbol -> 'ibkr' | 'webull' (IBKR wins when both have it)
_state = {'started': False, 'connected': {}}


# ------------------------------------------------------------------ New York sessions
def et(ms):
    return datetime.fromtimestamp(ms / 1000, ET)


def at_et(d, hh, mm):
    return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET).timestamp() * 1000)


def last_close_ms(now_ms):
    """The most recent regular-session close that has passed: 4:00 pm New York time, 1:00 pm on early-close
    days, skipping weekends and market holidays (review 2026-10-11 #12)."""
    try:
        from nyse_hours import trading_day, nyse_early_closes
    except ImportError:                 # no calendar: weekdays at 4 pm
        trading_day = lambda x: x.weekday() < 5
        nyse_early_closes = lambda y: frozenset()
    d = et(now_ms)
    day = d.date()
    for _ in range(15):
        if trading_day(day):
            hh = 13 if day in nyse_early_closes(day.year) else 16
            if day != d.date() or d.hour >= hh:
                return at_et(day, hh, 0)
        day -= timedelta(days=1)
    return at_et(day, 16, 0)


def overnight_window(now_ms):
    """(start, end) of the latest 8 pm -> 4 am session that has started (none Fri/Sat night)."""
    d = et(now_ms)
    day = d.date() if d.hour >= 20 else d.date() - timedelta(days=1)
    while day.weekday() in (4, 5):
        day -= timedelta(days=1)
    return at_et(day, 20, 0), at_et(day + timedelta(days=1), 4, 0)


def price_at(hist, ms):
    best = None
    for t, p in hist:
        if t > ms:
            break
        best = p
    return best


def pct(a, b):
    return None if a is None or not b else (a - b) / b * 100


def stats(sym, now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    with _lock:
        hist = list(_px.get(sym) or [])
    if not hist:
        return None
    last = hist[-1][1]
    s, e = overnight_window(now_ms)
    on = [p for t, p in hist if s <= t <= min(e, now_ms)]
    after4 = [p for t, p in hist if e <= t <= now_ms]
    lo, hi = (min(on), max(on)) if on else (None, None)
    return {'sym': sym, 'last': last, 'close': pct(last, price_at(hist, last_close_ms(now_ms))),
            'on': pct(price_at(hist, min(e, now_ms)), price_at(hist, s)), 'lo': lo, 'hi': hi,
            'broke_hi': bool(hi is not None and after4 and max(after4) > hi),
            'broke_lo': bool(lo is not None and after4 and min(after4) < lo)}


# ------------------------------------------------------------------ files
def _read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


# ------------------------------------------------------------------ phone push (ntfy)
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


def topic():
    return os.environ.get('JB_NTFY_TOPIC', '').strip()


def _priority(tags):
    """ntfy priority (idea 55): SELL ALL, stops firing, the loss limit = urgent; warnings high; the rest normal."""
    t = str(tags or '')
    return '5' if ('rotating_light' in t or 'sos' in t) else '4' if 'warning' in t else '3'


def push(title, text, tags='chart_with_upwards_trend'):
    """Send one notification to the phone. Never raises; returns '' or what went wrong."""
    t = topic()
    if not t:
        return 'no JB_NTFY_TOPIC in settings.env'
    try:
        req = urllib.request.Request('https://ntfy.sh/' + t, data=text.encode('utf-8'), method='POST',
                                     headers={'Title': title.encode('ascii', 'ignore').decode(), 'Tags': tags, 'Priority': _priority(tags)})
        urllib.request.urlopen(req, timeout=15, context=_ctx()).read()
        return ''
    except Exception as e:
        print(f'ntfy push failed: {e!r}', flush=True)
        return str(e)[:200]


def push_later(title, text, tags='chart_with_upwards_trend'):
    threading.Thread(target=push, args=(title, text, tags), daemon=True).start()


# ------------------------------------------------------------------ listening to the bridges
TAKEOVER_MS = 2 * 60000   # IBKR silent this long on a symbol -> another broker's live price is used


def _record(src, sym, p, t=None):
    if not isinstance(p, (int, float)) or p <= 0:
        return
    t = int(t or time.time() * 1000)
    with _lock:
        h = _px.setdefault(sym, [])
        if _src.get(sym) not in (None, src) and src != 'ibkr' and not (h and t - h[-1][0] > TAKEOVER_MS):
            return                      # IBKR already supplies this symbol (Webull takes over if IBKR goes quiet)
        _src[sym] = src
        if h and t < h[-1][0]:
            return
        if h and t // 60000 == h[-1][0] // 60000:
            h[-1] = [t, p]
        else:
            h.append([t, p])
        cut = t - KEEP_S * 1000
        while h and h[0][0] < cut:
            h.pop(0)


def _load_history(src, sym, intraday):
    pts = sorted((int(b['t']), float(b['p'])) for b in intraday if b.get('p'))
    with _lock:
        h = _px.get(sym) or []
        if _src.get(sym) not in (None, src) and src != 'ibkr' and not (h and time.time() * 1000 - h[-1][0] > TAKEOVER_MS):
            return
        _src[sym] = src
        live = [x for x in _px.get(sym, []) if pts and x[0] > pts[-1][0]]
        cut = time.time() * 1000 - KEEP_S * 1000
        merged, last_min = [], None
        for t, p in pts:
            if t < cut:
                continue
            if last_min == t // 60000:
                merged[-1] = [t, p]
            else:
                merged.append([t, p]); last_min = t // 60000
        _px[sym] = merged + live


async def _listen(name, port, token_env):
    import websockets
    delay = 2
    while True:
        try:
            async with websockets.connect(f'ws://127.0.0.1:{port}', max_size=2 ** 22) as ws:
                tok = os.environ.get(token_env, '')
                if tok:
                    await ws.send(json.dumps({'type': 'auth', 'token': tok}))
                _state['connected'][name] = True
                delay = 2
                async for raw in ws:
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    kind = m.get('type')
                    if kind == 'ticks':
                        for t in m.get('ticks', []):
                            _record(name, t.get('symbol'), t.get('price'))
                    elif kind == 'tick':
                        _record(name, m.get('symbol'), m.get('price'))
                    elif kind == 'watchlist':
                        for s in m.get('symbols', []):
                            await ws.send(json.dumps({'type': 'history', 'symbol': s}))
                    elif kind == 'history':
                        _load_history(name, m.get('symbol'), m.get('intraday') or [])
        except Exception:
            pass
        _state['connected'][name] = False
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


# ------------------------------------------------------------------ alerts
def alerts():
    return _read(ALERTS, [])


def add_alert(sym, kind, value):
    sym = str(sym or '').strip().upper()
    if not sym or len(sym) > 12 or kind not in ('above', 'below', 'move'):
        raise ValueError('pick a symbol and a condition')
    value = float(value)
    if not value > 0:
        raise ValueError('the number must be above 0')
    with _lock:
        lst = alerts()
        lst.append({'id': uuid.uuid4().hex[:10], 'sym': sym, 'kind': kind, 'value': value,
                    'created': int(time.time() * 1000), 'done': False, 'day': None})
        _write(ALERTS, lst)


def remove_alert(aid):
    with _lock:
        _write(ALERTS, [a for a in alerts() if a.get('id') != aid])


def describe(a):
    if a['kind'] == 'move':
        return f"{a['sym']} moves {a['value']:g}% vs. the 4 pm close"
    return f"{a['sym']} {a['kind']} {a['value']:g}"


def _check_alerts():
    now = int(time.time() * 1000)
    with _lock:
        lst = alerts()
        changed, hits = False, []
        for a in lst:
            if a.get('done'):
                continue
            h = _px.get(a['sym'])
            if not h:
                continue
            last = h[-1][1]
            if a['kind'] == 'above' and last >= a['value'] or a['kind'] == 'below' and last <= a['value']:
                a['done'] = True
                hits.append((a, f"{a['sym']} is {last:g} ({'above' if a['kind'] == 'above' else 'below'} {a['value']:g})"))
                changed = True
            elif a['kind'] == 'move':
                close_ms = last_close_ms(now)
                if a.get('day') == close_ms:
                    continue                # already told you since this close; it re-arms after the next one
                c = pct(last, price_at(h, close_ms))
                if c is not None and abs(c) >= a['value']:
                    a['day'] = close_ms
                    hits.append((a, f"{a['sym']} is {'up' if c > 0 else 'down'} {abs(c):.2f}% vs. the 4 pm close ({last:g})"))
                    changed = True
        if changed:
            _write(ALERTS, lst)
            log = _read(LOG, [])
            for a, text in hits:
                log.insert(0, {'at': now, 'id': a['id'], 'sym': a['sym'], 'text': text})
            _write(LOG, log[:50])
    for a, text in hits:
        print('alert: ' + text, flush=True)
        push_later('Alert: ' + a['sym'], text, 'bell')


# ------------------------------------------------------------------ morning cards
def _fmt(v):
    return '—' if v is None else f'{v:+.2f}%'


def build_card(label, now_ms=None):
    now_ms = now_ms or int(time.time() * 1000)
    with _lock:
        syms = sorted(_px)
    rows = [s for s in (stats(x, now_ms) for x in syms) if s and s['close'] is not None]
    fut = [r for r in rows if r['sym'].endswith('=F')]
    stk = [r for r in rows if not r['sym'].endswith('=F')]
    by = sorted(stk, key=lambda r: r['close'])
    card = {'at': now_ms, 'label': label, 'futures': fut,
            'up': [r for r in reversed(by) if r['close'] > 0][:5], 'down': [r for r in by if r['close'] < 0][:5],
            'broke_hi': [r['sym'] for r in rows if r['broke_hi']], 'broke_lo': [r['sym'] for r in rows if r['broke_lo']]}
    lines = [f"Morning card {label} ET"]
    if fut:
        lines.append('Futures: ' + ', '.join(f"{r['sym']} {_fmt(r['close'])}" for r in fut))
    if card['up']:
        lines.append('Up most vs. close: ' + ', '.join(f"{r['sym']} {_fmt(r['close'])}" for r in card['up']))
    if card['down']:
        lines.append('Down most vs. close: ' + ', '.join(f"{r['sym']} {_fmt(r['close'])}" for r in card['down']))
    if card['broke_hi']:
        lines.append('Broke above the overnight high since 4 am: ' + ', '.join(card['broke_hi']))
    if card['broke_lo']:
        lines.append('Broke below the overnight low since 4 am: ' + ', '.join(card['broke_lo']))
    card['text'] = '\n'.join(lines)
    return card


def _maybe_card(done):
    now = int(time.time() * 1000)
    d = et(now)
    if d.weekday() >= 5:
        return
    for hh, mm, label in CARD_TIMES:
        t = at_et(d.date(), hh, mm)
        key = f'{d.date()} {label}'
        if t <= now < t + 30 * 60000 and key not in done:
            done.add(key)
            card = build_card(label, now)
            _write(MORNING, card)
            print(f'morning card {label} written', flush=True)
            push_later(f'Morning card {label}', card['text'].split('\n', 1)[-1] or 'No prices yet.', 'sunrise')


# ------------------------------------------------------------------ thread + API
async def _main():
    for name, (port, env) in BRIDGES.items():
        asyncio.ensure_future(_listen(name, port, env))
    m = _read(MORNING, {})
    done = {f"{et(m['at']).date()} {m['label']}"} if m.get('at') else set()
    while True:
        await asyncio.sleep(2)
        try:
            _check_alerts()
            _maybe_card(done)
        except Exception as e:          # never let the thread die
            print(f'watch error: {e!r}', flush=True)


def start():
    with _lock:
        if _state['started']:
            return
        _state['started'] = True
    threading.Thread(target=lambda: asyncio.run(_main()), name='watch', daemon=True).start()


def snapshot():
    m = _read(MORNING, None)
    now = int(time.time() * 1000)
    out = {'alerts': [dict(a, text=describe(a), now=(stats(a['sym'], now) or {}).get('last')) for a in alerts()],
           'log': _read(LOG, [])[:20], 'morning': m, 'topic': topic(), 'server_ms': now,
           'connected': dict(_state['connected'])}
    return json.dumps(out)


def post(data):
    """The board's Alerts panel: {action: add|remove|test|card}. Returns (status, json)."""
    act = data.get('action')
    try:
        if act == 'add':
            add_alert(data.get('sym'), data.get('kind'), data.get('value'))
        elif act == 'remove':
            remove_alert(str(data.get('id')))
        elif act == 'test':
            err = push('Alex Hale\'s Board', 'Test notification: phone alerts are working.', 'white_check_mark')
            if err:
                return 400, json.dumps({'error': 'Could not send: ' + err})
        elif act == 'card':           # make a card right now (button on the board)
            card = build_card(et(time.time() * 1000).strftime('%-I:%M %p').lower())
            _write(MORNING, card)
        else:
            return 400, json.dumps({'error': 'unknown action'})
    except (ValueError, TypeError) as e:
        return 400, json.dumps({'error': str(e)})
    return 200, snapshot()
