"""Your price levels on the charts (jb-ticket), running inside the gateway.

Set a level by tapping the chart (Levels button on the chart) or typing a price; it is saved on the
Mac, so it stays on every chart of that symbol, on the board and the phone, until you remove it.
Each level can also send a phone push when the price crosses it (once, then that alert switches
itself off; switch it back on to arm it again).
  GET  /api/plus/levels            {sym: [level, ...]}
  POST /api/plus/levels/add        {sym, price, label?, alert?}
  POST /api/plus/levels/remove     {id}
  POST /api/plus/levels/alert      {id, alert: true|false}
File: data/chart_levels.json.
"""
import math
import os
import threading
import time
import uuid

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
FILE = os.path.join(DATA, 'chart_levels.json')
_lock = threading.Lock()
_st = {'started': False, 'side': {}}       # id -> which side of the level the price was on


def _now():
    return int(time.time() * 1000)


def all_levels():
    v = watch._read(FILE, [])
    return v if isinstance(v, list) else []


def _save(lst):
    watch._write(FILE, lst[-500:])


def by_sym():
    out = {}
    for l in all_levels():
        out.setdefault(l['sym'], []).append(l)
    return out


def add(data):
    sym = ''.join(ch for ch in str(data.get('sym') or '').upper() if ch.isalnum() or ch in '.=-')[:12]
    try:
        price = float(data.get('price'))
    except (TypeError, ValueError):
        return 400, {'error': 'Type a price.'}
    if not sym or not math.isfinite(price) or price <= 0:
        return 400, {'error': 'Need a symbol and a price above 0.'}
    lv = {'id': uuid.uuid4().hex[:12], 'sym': sym, 'price': round(price, 4), 'label': str(data.get('label') or '')[:30],
          'alert': bool(data.get('alert')), 'at': _now()}
    with _lock:
        lst = all_levels()
        lst.append(lv)
        _save(lst)
    return 200, {'levels': by_sym(), 'added': lv}


def remove(data):
    with _lock:
        _save([l for l in all_levels() if l['id'] != str(data.get('id'))])
    return 200, {'levels': by_sym()}


def set_alert(data):
    with _lock:
        lst = all_levels()
        for l in lst:
            if l['id'] == str(data.get('id')):
                l['alert'] = bool(data.get('alert'))
                _st['side'].pop(l['id'], None)
        _save(lst)
    return 200, {'levels': by_sym()}


def check(now_ms):
    """Push once when the price crosses a level that has its alert on."""
    lst = all_levels()
    fired = set()
    for l in lst:
        if not l.get('alert'):
            continue
        with watch._lock:
            h = watch._px.get(l['sym'])
            last = h[-1][1] if h and now_ms - h[-1][0] < 10 * 60000 else None
        if last is None:
            continue
        side = 'above' if last >= l['price'] else 'below'
        prev = _st['side'].get(l['id'])
        _st['side'][l['id']] = side
        if prev and prev != side:
            name = f" ({l['label']})" if l['label'] else ''
            watch.push_later(f"{l['sym']} crossed {l['price']:g}", f"{l['sym']} is now {side} your level {l['price']:g}{name}: {last:g}.", 'triangular_flag_on_post')
            fired.add(l['id'])
    if fired:
        with _lock:                               # re-read, so changes made meanwhile on other levels are kept
            lst = all_levels()
            for l in lst:
                if l['id'] in fired:
                    l['alert'], l['fired'] = False, now_ms
            _save(lst)


def _loop():
    while True:
        try:
            check(_now())
        except Exception as e:
            print(f'levels error: {e!r}', flush=True)
        time.sleep(5)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_loop, name='levels', daemon=True).start()


def get(sub, query):
    return 200, {'levels': by_sym()}


def post(sub, data):
    if sub == 'add':
        return add(data)
    if sub == 'remove':
        return remove(data)
    if sub == 'alert':
        return set_alert(data)
    return 404, {'error': 'unknown'}
