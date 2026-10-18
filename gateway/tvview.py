"""Roku TV view (jb-roku): the one thing the TV app is allowed to read.

    GET /api/tv?key=<TV key>              a small, ready-to-show summary of the board (read only)
    GET /api/tv?key=<TV key>&part=tiles   the IBKR board's tiles: price, colours, the 1M ... 1MO and O/N moves

It is READ ONLY by design: it answers GET with a summary and nothing else. It cannot place,
change or cancel orders, press SELL ALL, pause algos or change settings, and it never shows the
board password or anything from settings.env.

The TV key is NOT the board password. It is a separate random key made the first time it is needed
and kept in data/tv_key.txt. It only opens this summary. To lock out an old TV app, delete that
file, restart the gateway and make the TV app again (bash ~/josephs-board/roku/make-tv-app.sh).

The TV tries the Mac on the home Wi-Fi first, then the ts.net link (Tailscale Funnel), so it also
works on other Wi-Fi. Either way it needs the TV key and only gets this summary. Account money (profit / loss, positions) is left off, since anyone in
the room can see a TV.
"""
import bisect
import hmac
import json
import os
import secrets
import threading
import time

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
KEY_FILE = os.path.join(DATA, 'tv_key.txt')
CACHE_S = 10                       # the TV asks every 15 s; build the summary at most every 10 s

_lock = threading.Lock()
_cache = {'at': 0.0, 'body': ''}


def home_url():
    """This Mac's current home Wi-Fi address for the TV (it asks for it over ts.net if the old one stops working)."""
    import socket
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(('192.0.2.1', 9))            # picks the network the Mac uses; nothing is sent
        ip = sk.getsockname()[0]
        sk.close()
    except OSError:
        return ''
    if ip.startswith('127.') or ip.startswith('100.'):
        return ''                               # not a home Wi-Fi address (100.x is Tailscale)
    return f"http://{ip}:{os.environ.get('JB_GATEWAY_PORT') or 8090}/api/tv?key={key()}"


def key():
    """The TV key (made once, then read from data/tv_key.txt)."""
    try:
        k = open(KEY_FILE, encoding='utf-8').read().strip()
        if len(k) >= 24:
            return k
    except OSError:
        pass
    k = secrets.token_hex(16)
    os.makedirs(DATA, exist_ok=True)
    with open(KEY_FILE, 'w', encoding='utf-8') as f:
        f.write(k + '\n')
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass
    return k


# ------------------------------------------------------------------ formatting (the TV just shows these strings)
def _num(v, dec=2):
    if v is None:
        return '--'
    try:
        v = float(v)
    except (TypeError, ValueError):
        return '--'
    if abs(v) >= 10000:
        dec = 0
    return f'{v:,.{dec}f}'


def _pct(v):
    if v is None:
        return '--'
    try:
        v = float(v)
    except (TypeError, ValueError):
        return '--'
    return f'{v:+.2f}%'


def _dir(v, flat=0.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0
    return 1 if v > flat else -1 if v < -flat else 0


def _clock(ms):
    d = watch.et(ms)
    return d.strftime('%I:%M %p').lstrip('0') + ' ET'


def _when(ms, now_ms):
    d, n = watch.et(ms), watch.et(now_ms)
    t = d.strftime('%I:%M %p').lstrip('0')
    if d.date() == n.date():
        return 'Today ' + t
    if (d.date() - n.date()).days == 1:
        return 'Tomorrow ' + t
    return d.strftime('%a ') + t


def _sentence_case(text):
    """'Market weather: sunny in tech and banks; rainy in oil.' -> each part starts with a capital:
    'Market weather: Sunny in tech and banks; Rainy in oil.' (short words like S&P are left as they are)."""
    import re
    return re.sub(r'(^|: |; |\. )([a-z])', lambda m: m.group(1) + m.group(2).upper(), text)


def _meter(m, title):
    if not isinstance(m, dict) or m.get('score') is None:
        return {'title': title, 'score': '--', 'value': 0, 'label': 'Waiting for prices', 'dir': 0, 'ok': False}
    sc = int(round(float(m['score'])))
    return {'title': title, 'score': f'{sc:+d}', 'value': max(-100, min(100, sc)),
            'label': str(m.get('label') or ''), 'dir': _dir(sc, 24), 'ok': True}


# ------------------------------------------------------------------ the IBKR tiles (asked for every 2 s)
# the same change boxes as each tile on the IBKR board (1M ... 1D from the gateway's own 40-hour
# price record, 3D / 1W / 1MO from the IBKR bridge's 35-day record and IBKR's daily bars)
WINDOWS = (('1M', 60000), ('5M', 5 * 60000), ('15M', 15 * 60000), ('45M', 45 * 60000),
           ('4H', 4 * 3600000), ('8H', 8 * 3600000), ('1D', 24 * 3600000))
LONG = (('3D', 3 * 86400000), ('1W', 7 * 86400000), ('1MO', 30 * 86400000))
BOXES = [n for n, _ in WINDOWS + LONG] + ['O/N']
TICKS = os.path.join(DATA, 'ibkr_ticks')                 # data/ibkr_ticks/<UTC date>.csv: SYMBOL,minute_ms,price
MAX_TILES = 24


_days = {}                                                # a past day's price record, read once
_broker = {'at': 0.0, 'mt': None, 'd': {}, 'adj': {}}
_refs = {'at': 0.0, 'r': {}}


def _dayfile(name, today):
    if name in _days:
        return _days[name]
    d = {}
    try:
        with open(os.path.join(TICKS, name + '.csv'), encoding='utf-8', errors='replace') as f:
            for line in f:
                parts = line.split(',')
                if len(parts) != 3:
                    continue
                try:
                    d.setdefault(parts[0], []).append((int(parts[1]), float(parts[2])))
                except ValueError:
                    continue
    except OSError:
        pass
    for v in d.values():
        v.sort()
    if name != today:                                     # today's file is still growing
        _days[name] = d
    return d


def _broker_bars():
    """IBKR's own history bars the bridge keeps (data/ibkr_ticks_history.json), re-read when it changes."""
    if time.time() - _broker['at'] > 60:
        _broker['at'] = time.time()
        path = os.path.join(DATA, 'ibkr_ticks_history.json')
        try:
            mt = os.path.getmtime(path)
            if mt != _broker['mt']:
                raw = json.load(open(path, encoding='utf-8'))
                d = {}
                for sym, e in (raw.items() if isinstance(raw, dict) else []):
                    pts = []
                    for b in ((e.get('daily') or []) + (e.get('boot') or [])) if isinstance(e, dict) else []:
                        try:
                            if float(b['p']) > 0:
                                pts.append((int(b['t']), float(b['p'])))
                        except (KeyError, TypeError, ValueError):
                            continue
                    d[sym] = sorted(pts)
                _broker['d'], _broker['mt'] = d, mt
        except Exception:
            pass
        try:
            a = json.load(open(os.path.join(TICKS, 'adjustments.json'), encoding='utf-8'))
            _broker['adj'] = a if isinstance(a, dict) else {}
        except Exception:
            _broker['adj'] = {}
    return _broker['d'], _broker['adj']


def _long_refs(syms, now_ms):
    """Price at 3 days, 1 week and 1 month ago per symbol (the last price at or before then),
    worked out once a minute. Futures roll gaps are taken out, the same as on the IBKR board."""
    if time.time() - _refs['at'] < 60 and _refs['r']:
        return _refs['r']
    today = time.strftime('%Y-%m-%d', time.gmtime(now_ms / 1000))
    broker, adj = _broker_bars()
    used, out = set(), {}
    for name, ms in LONG:
        target = now_ms - ms
        files = [time.strftime('%Y-%m-%d', time.gmtime(target / 1000 - k * 86400)) for k in range(5)]
        used.update(files)
        for sym in syms:
            ref = None
            for f in files:
                pts = _dayfile(f, today).get(sym)
                if pts:
                    i = bisect.bisect_right(pts, (target, float('inf'))) - 1
                    if i >= 0:
                        t, p = pts[i]
                        p += sum(float(dl) for r, dl in (adj.get(sym) or []) if t < float(r))
                        ref = p
                        break
            if ref is None:
                pts = broker.get(sym) or []
                i = bisect.bisect_right(pts, (target, float('inf'))) - 1
                if i >= 0:
                    ref = pts[i][1]
            out.setdefault(sym, {})[name] = ref
    for f in list(_days):
        if f not in used:
            del _days[f]
    _refs['at'], _refs['r'] = time.time(), out
    return out


def _box(name, v):
    """One change box: the board's +0.25 style, plus a shorter one for narrow tiles."""
    if v is None:
        return {'name': name, 'val': '--', 'short': '--', 'dir': 0}
    if abs(v) < 0.005:
        return {'name': name, 'val': '0.00', 'short': '0.00', 'dir': 0}
    short = f'{v:.1f}' if abs(v) >= 10 else f'{v:.2f}'
    return {'name': name, 'val': f'{v:+.2f}', 'short': short, 'dir': _dir(v)}


def _watchlist():
    try:
        syms = json.load(open(os.path.join(DATA, 'ibkr_watchlist.json'), encoding='utf-8'))
        return [str(x) for x in syms if isinstance(x, str)]
    except Exception:
        return []


_years = {'at': 0.0, 'r': {}}


def _yearly(syms):
    """Yahoo's year of daily highs and lows per symbol, through the 1W / 52W range bars' own cache."""
    if time.time() - _years['at'] > 60:
        try:
            import ranges
            _years['r'] = json.loads(ranges.snapshot({'syms': [','.join(syms)]})).get('r') or {}
        except Exception:
            pass
        _years['at'] = time.time()
    return _years['r']


def _bar(name, lo, hi, now):
    if lo is None or hi is None or now is None or not hi > lo:
        return {'name': name, 'ok': False, 'lo': '', 'hi': '', 'pos': 0.5, 'edge': 0}
    pos = max(0.0, min(1.0, (now - lo) / (hi - lo)))
    return {'name': name, 'ok': True, 'lo': _num(lo), 'hi': _num(hi), 'pos': round(pos, 3),
            'edge': 1 if now >= hi else -1 if now <= lo else 0}


def _bars(sym, hist, now_ms, years):
    """O/N, 1W and 52W sliders, the same as under each tile on the IBKR board."""
    last = hist[-1][1]
    st = watch.stats(sym, now_ms) or {}
    out = [_bar('O/N', st.get('lo'), st.get('hi'), last)]
    y = years.get(sym)
    week = [p for t, p in hist if t >= now_ms - 7 * 86400000]
    lo, hi = min(week), max(week)
    if y:
        first = watch.et(now_ms - 7 * 86400000).strftime('%Y-%m-%d')
        for d in y.get('days') or []:
            if d[0] >= first:
                hi, lo = max(hi, d[1]), min(lo, d[2])
    out.append(_bar('1W', lo, hi, last))
    if y:
        out.append(_bar('52W', min(lo, y['lo']), max(hi, y['hi']), last))
    else:
        out.append(_bar('52W', None, None, None))
    return out


def tiles():
    """The IBKR board's symbols in its own order, stocks first, then futures (=F)."""
    now = int(time.time() * 1000)
    syms = _watchlist()
    syms = [x for x in syms if not x.endswith('=F')] + [x for x in syms if x.endswith('=F')]
    out = []
    years = _yearly(syms[:MAX_TILES])
    try:
        refs = _long_refs(syms[:MAX_TILES], now)
    except Exception:
        refs = {}
    for sym in syms[:MAX_TILES]:
        with watch._lock:
            hist = list(watch._px.get(sym) or [])
        fut = sym.endswith('=F')
        t = {'sym': sym[:-2] if fut else sym, 'fut': fut, 'last': '--', 'raw': 0.0, 'dir': 0, 'zone': 0, 'chg': []}
        if hist:
            last = hist[-1][1]
            ts = [x[0] for x in hist]

            def at(ms):
                i = bisect.bisect_right(ts, ms) - 1
                return hist[i][1] if i >= 0 else None
            t['raw'] = last
            t['last'] = _num(last)
            t['dir'] = _dir(watch.pct(last, at(now - 60000)))       # price colour: 1-minute change
            t['zone'] = _dir(watch.pct(last, at(now - 120000)))     # tile tint: 2-minute change
            for name, ms in WINDOWS:
                t['chg'].append(_box(name, watch.pct(last, at(now - ms))))
            for name, ms in LONG:
                t['chg'].append(_box(name, watch.pct(last, (refs.get(sym) or {}).get(name))))
            st = watch.stats(sym, now) or {}
            t['chg'].append(_box('O/N', st.get('on')))
            t['bars'] = _bars(sym, hist, now, years)
        else:
            t['chg'] = [_box(n, None) for n in BOXES]
            t['bars'] = [_bar(n, None, None, None) for n in ('O/N', '1W', '52W')]
        out.append(t)
    return {'ok': True, 'tiles': out, 'more': max(0, len(syms) - MAX_TILES)}


CHART_STEPS = 72                                          # 24 hours in 20-minute steps


def _fchart(now):
    """The IBKR board's futures over the last 24 hours, each as % change since then, for the TV's
    futures chart. Heights are sent ready to draw: 0 = bottom of the chart, 1 = top."""
    futs = [x for x in _watchlist() if x.endswith('=F')][:8]
    start = now - 24 * 3600000
    lines, lo, hi = [], 0.0, 0.0
    for c, sym in enumerate(futs):
        with watch._lock:
            hist = list(watch._px.get(sym) or [])
        if not hist:
            continue
        ts = [x[0] for x in hist]

        def at(ms):
            i = bisect.bisect_right(ts, ms) - 1
            return hist[i][1] if i >= 0 else None
        base = at(start) or hist[0][1]                     # a record under 24 h old starts at its first price
        if not base:
            continue
        pts = []
        for k in range(CHART_STEPS + 1):
            q = hist[-1][1] if k == CHART_STEPS else at(start + k * 24 * 3600000 // CHART_STEPS)
            pts.append(None if q is None else (q - base) / base * 100)
        vals = [v for v in pts if v is not None]
        lo, hi = min([lo] + vals), max([hi] + vals)
        lines.append({'sym': sym[:-2], 'c': c, 'pct': pts[-1], 'pts': pts})
    pad = max(0.05, (hi - lo) * 0.08)
    lo, hi = lo - pad, hi + pad

    def y(v):
        return -1 if v is None else round((v - lo) / (hi - lo), 3)
    for ln in lines:
        ln['pts'] = [y(v) for v in ln['pts']]
        ln['val'] = _pct(ln['pct'])
        ln['dir'] = _dir(ln['pct'])
    lines.sort(key=lambda ln: -(ln['pct'] or 0))
    return {'lines': lines, 'zero': y(0.0), 'top': f'{hi:+.1f}%', 'bottom': f'{lo:+.1f}%',
            'from': _clock(start), 'steps': CHART_STEPS}


# ------------------------------------------------------------------ the summary
def build():
    now = int(time.time() * 1000)
    out = {'ok': True, 'clock': _clock(now), 'updated': _clock(now), 'home': home_url()}
    try:
        import extras
        p = json.loads(extras.phone())
    except Exception as e:
        p = {'world_error': f'{e}'[:100]}
    out['session'] = p.get('session') or ''
    out['session_note'] = p.get('session_note') or ''
    regular = out['session'] == 'Regular'
    since = 'since the 9:30 open' if regular else 'since the 4 pm close'

    names = {'ES=F': 'S&P 500', 'NQ=F': 'Nasdaq', 'YM=F': 'Dow', 'RTY=F': 'Russell 2000',
             '^VIX': 'VIX', 'CL=F': 'Oil', 'BTC-USD': 'Bitcoin'}
    out['futures'] = [{'name': names.get(f.get('sym'), f.get('name') or f.get('sym') or ''),
                       'last': _num(f.get('last')), 'chg': _pct(f.get('chg')),
                       # VIX up is bad news, so its colour is flipped (same as the phone page)
                       'dir': -_dir(f.get('chg')) if f.get('sym') == '^VIX' else _dir(f.get('chg'))}
                      for f in (p.get('futures') or [])][:7]

    meters = [_meter(p.get('mood'), 'Market mood')]
    try:
        import meters as mt
        snap = mt.snapshot(now)
        k = 'open' if regular else 'close'
        fm, pm = (snap.get('futures') or {}).get(k), (snap.get('picks') or {}).get(k)
        meters.append(_meter(fm, 'Futures mood'))
        meters.append(_meter(pm, 'Picks mood'))
        picks = [{'sym': r['sym'], 'chg': _pct(r.get('pct')), 'dir': _dir(r.get('pct'))}
                 for r in ((pm or {}).get('parts') or [])][:8]
        if pm:
            meters[-1]['label'] += f"  ({pm.get('up', 0)} up, {pm.get('down', 0)} down)"
    except Exception:
        picks = []
    out['meters'] = meters
    out['meters_note'] = since
    out['picks'] = picks
    out['picks_note'] = since

    out['movers'] = [{'sym': r['sym'], 'last': _num(r.get('last')), 'chg': _pct(r.get('chg')), 'dir': _dir(r.get('chg')),
                      'on': _pct(r.get('on')) if r.get('on') is not None else '', 'ondir': _dir(r.get('on'))}
                     for r in (p.get('movers') or [])][:4]
    try:
        out['fchart'] = _fchart(now)
    except Exception:
        out['fchart'] = {'lines': []}
    out['events'] = [{'when': _when(e['t'], now), 'title': str(e.get('title') or '')[:60]}
                     for e in (p.get('events') or []) if e.get('t')][:2]
    out['alerts'] = [{'when': _clock(a['at']) if a.get('at') else '',
                      'text': ((a.get('sym') + ': ') if a.get('sym') else '') + str(a.get('text') or '')[:90]}
                     for a in (p.get('alerts') or [])][:2]
    w = p.get('weather')
    out['weather'] = (w.get('line') or '') if isinstance(w, dict) else (w if isinstance(w, str) else '')
    out['weather'] = _sentence_case(str(out['weather'] or ''))[:300]
    down = p.get('down') or []
    out['status'] = ('Not running: ' + ', '.join(down)) if down else 'All services running'
    out['status_ok'] = not down
    return json.dumps(out)


def answer(method, query, headers):
    """-> (status, body). Called by gateway.py for /api/tv."""
    if method not in ('GET', 'HEAD'):
        return 405, json.dumps({'ok': False, 'error': 'read only'})
    given = ((query or {}).get('key') or [''])[0]
    if not given or not hmac.compare_digest(given.encode(), key().encode()):
        return 403, json.dumps({'ok': False, 'error': 'wrong TV key: make the TV app again'})
    if ((query or {}).get('part') or [''])[0] == 'tiles':
        try:
            return 200, json.dumps(tiles())
        except Exception as e:
            return 200, json.dumps({'ok': False, 'error': f'tiles failed: {e}'[:160]})
    with _lock:
        if time.time() - _cache['at'] < CACHE_S and _cache['body']:
            return 200, _cache['body']
    try:
        body = build()
    except Exception as e:
        return 200, json.dumps({'ok': False, 'error': f'board summary failed: {e}'[:160]})
    with _lock:
        _cache['at'], _cache['body'] = time.time(), body
    return 200, body
