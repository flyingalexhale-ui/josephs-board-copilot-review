"""Top 5 board picks on the Scanner tab (jb-desk).

Every minute the board reads the scanner's Picks (the same ones as the Picks view, strongest first)
and keeps the five strongest that lean up or down. Each time a symbol joins the Top 5 (or its lean
flips) that is a CALL: symbol, up or down, price, time. The board then grades itself on every call
with its own prices: the move in the leaned direction after 30 minutes, 1 hour, 4 hours and 1 day
(a call that leans down is right when the price falls). Flat results (under 0.02%) don't count.

  GET /api/plus/top5   {top: [...5 live cards...], stats, days, recent, mode, at, error}

Stored in data/top5.json (45 days, at most 4000 calls). Research, not advice: the Top 5 only
ranks what the scanner's simple chart rules found.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
PATH = os.path.join(DATA, 'top5.json')
HORIZONS = (('m30', 1800, '30 min'), ('h1', 3600, '1 hour'), ('h4', 14400, '4 hours'), ('d1', 86400, '1 day'))
KEEP_DAYS = 45
FLAT = 0.02
_lock = threading.Lock()
_state = {'top': [], 'mode': '', 'at': 0, 'error': '', 'started': False}
_db = {'calls': []}


def _load():
    try:
        d = json.load(open(PATH))
        if isinstance(d.get('calls'), list):
            _db['calls'] = d['calls']
    except (OSError, ValueError, AttributeError):
        pass


def _save():
    os.makedirs(DATA, exist_ok=True)
    tmp = PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(_db, f, separators=(',', ':'))
    os.replace(tmp, PATH)


def _read_scanner():
    """-> (picks list, {sym: price}, mode) from the scanner's snapshot (port 8767)."""
    from websockets.sync.client import connect
    with connect('ws://127.0.0.1:8767', open_timeout=5, close_timeout=2, max_size=2 ** 24) as ws:
        tok = os.environ.get('MOVER_SCANNER_TOKEN', '')
        if tok:
            ws.send(json.dumps({'type': 'auth', 'token': tok}))
        for _ in range(20):
            m = json.loads(ws.recv(timeout=10))
            if isinstance(m, dict) and m.get('type') == 'snapshot':
                if m.get('switching'):
                    continue
                p = m.get('picks') or {}
                lst = [x for x in (p.get('picks') or []) if isinstance(x, dict) and x.get('symbol')]
                px = {}
                for s, r in (m.get('latest') or {}).items():
                    if isinstance(r, dict) and r.get('last_price'):
                        px[s] = float(r['last_price'])
                return lst, px, m.get('mode') or ''
    return [], {}, ''


def _price(p, px):
    s = p['symbol']
    if px.get(s):
        return px[s]
    e = p.get('ext') or {}
    return e.get('price') or p.get('price')


def _lean_move(c, price):
    if not price or not c.get('price'):
        return None
    return round(c['dir'] * (price / c['price'] - 1) * 100, 3)


def refresh():
    picks, px, mode = _read_scanner()
    now = time.time()
    with _lock:
        top = []
        for p in picks:
            if p.get('lean') not in ('up', 'down'):
                continue
            pr = _price(p, px)
            if not pr:
                continue
            top.append((p, pr))
            if len(top) == 5:
                break
        open_calls = {c['sym']: c for c in _db['calls'] if c.get('out') is None}
        live = set()
        for rank, (p, pr) in enumerate(top, 1):
            s, d = p['symbol'], 1 if p['lean'] == 'up' else -1
            c = open_calls.get(s)
            if c and c['dir'] != d:
                c['out'] = now
                c = None
            if not c:
                c = {'id': f'{s}-{int(now)}', 'sym': s, 'dir': d, 'price': pr, 'at': now, 'out': None,
                     'rank': rank, 'best_rank': rank, 'agree': p.get('agree') or 1, 'mode': mode,
                     'summary': (p.get('summary') or '')[:200], 'res': {}, 'trail': []}
                _db['calls'].append(c)
                open_calls[s] = c
            c['rank'] = rank
            c['best_rank'] = min(c.get('best_rank') or rank, rank)
            c['agree'] = p.get('agree') or 1
            c['summary'] = (p.get('summary') or '')[:200]
            c['now_px'] = pr
            c['vs_close'] = p.get('change_pct') if not p.get('ext') else (p.get('ext') or {}).get('vs_close')
            c['signals'] = [x.get('title') for x in (p.get('signals') or [])][:4]
            live.add(s)
        for s, c in open_calls.items():                 # left the Top 5
            if s not in live and c.get('out') is None:
                c['out'] = now
        # grade every call from the last day and a bit, with the newest price the scanner has
        for c in _db['calls']:
            age = now - c['at']
            if age > 26 * 3600:
                c.pop('trail', None)
                continue
            pr = px.get(c['sym'])
            if not pr:
                continue
            mv = _lean_move(c, pr)
            c['move'] = mv
            for key, secs, _ in HORIZONS:
                if key not in c['res'] and age >= secs:
                    c['res'][key] = mv
            if c.get('out') is None:
                t = c.setdefault('trail', [])
                if not t or now - t[-1][0] >= 55:
                    t.append([int(now), pr])
                    del t[:-300]
            else:
                c.pop('trail', None)
        cut = now - KEEP_DAYS * 86400
        _db['calls'] = [c for c in _db['calls'] if c['at'] >= cut][-4000:]
        _state.update(top=[c['sym'] for c in sorted((open_calls[s] for s in live), key=lambda c: c['rank'])],
                      mode=mode, at=now, error='')
        _save()


def _loop():
    while True:
        try:
            refresh()
        except Exception as e:
            _state['error'] = f'Scanner not reachable ({type(e).__name__})'
            _state['at'] = _state['at'] or 0
        time.sleep(60)


def start():
    if _state['started']:
        return
    _state['started'] = True
    _load()
    threading.Thread(target=_loop, name='top5', daemon=True).start()


def _et_day(ts):
    # New York date (DST: second Sunday of March to first Sunday of November)
    u = datetime.fromtimestamp(ts, timezone.utc)
    y = u.year
    mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    mar += timedelta(days=(6 - mar.weekday()) % 7)
    nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    nov += timedelta(days=(6 - nov.weekday()) % 7)
    off = -4 if mar <= u < nov else -5
    return (u + timedelta(hours=off)).strftime('%Y-%m-%d')


def _grade(rate):
    if rate is None:
        return '–'
    return 'A' if rate >= 65 else 'B' if rate >= 58 else 'C' if rate >= 50 else 'D' if rate >= 42 else 'F'


def snapshot():
    with _lock:
        calls = list(_db['calls'])
        st = dict(_state)
    stats = {}
    for key, _, name in HORIZONS:
        vals = [c['res'][key] for c in calls if key in c.get('res', {}) and c['res'][key] is not None and abs(c['res'][key]) >= FLAT]
        n = len(vals)
        right = sum(1 for v in vals if v > 0)
        stats[key] = {'name': name, 'n': n, 'right': right, 'rate': round(right / n * 100) if n else None,
                      'avg': round(sum(vals) / n, 3) if n else None}
    # one point per New York day: average 1-hour result, and the running total of 1-hour results
    days, run = {}, 0.0
    for c in calls:
        v = (c.get('res') or {}).get('h1')
        if v is None:
            continue
        d = days.setdefault(_et_day(c['at']), [0.0, 0, 0])
        d[0] += v
        d[1] += 1
        d[2] += 1 if v > 0 else 0
    dl = []
    for k in sorted(days):
        s, n, r = days[k]
        run += s
        dl.append({'day': k, 'avg': round(s / n, 3), 'n': n, 'rate': round(r / n * 100), 'total': round(run, 3)})
    # each symbol's own record (1-hour results)
    rec = {}
    for c in calls:
        v = (c.get('res') or {}).get('h1')
        if v is None or abs(v) < FLAT:
            continue
        r = rec.setdefault(c['sym'], [0, 0])
        r[0] += 1
        r[1] += 1 if v > 0 else 0
    top = []
    open_by = {c['sym']: c for c in calls if c.get('out') is None}
    for s in st['top']:
        c = open_by.get(s)
        if not c:
            continue
        r = rec.get(s)
        top.append({'sym': s, 'dir': c['dir'], 'rank': c['rank'], 'price': c['price'], 'now': c.get('now_px'),
                    'since': c.get('move'), 'at': int(c['at'] * 1000), 'agree': c.get('agree'), 'summary': c.get('summary'),
                    'signals': c.get('signals') or [], 'vs_close': c.get('vs_close'),
                    'trail': [p[1] for p in (c.get('trail') or [])][-120:],
                    'record': {'n': r[0], 'right': r[1]} if r else None})
    perf = sorted(top, key=lambda t: -(t['since'] if t['since'] is not None else -1e9))
    for i, t in enumerate(perf, 1):
        t['perf_rank'] = i
    recent = [{'sym': c['sym'], 'dir': c['dir'], 'at': int(c['at'] * 1000), 'price': c['price'], 'rank': c.get('best_rank'),
               'res': c.get('res') or {}, 'move': c.get('move'), 'open': c.get('out') is None}
              for c in sorted(calls, key=lambda c: -c['at'])[:15]]
    h1 = stats['h1']
    return {'top': top, 'stats': stats, 'grade': _grade(h1['rate'] if h1['n'] >= 10 else None), 'days': dl[-60:],
            'recent': recent, 'n_calls': len(calls), 'mode': st['mode'], 'at': int(st['at'] * 1000), 'error': st['error']}


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    return 404, {'error': 'unknown'}
