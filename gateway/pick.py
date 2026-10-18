"""Night desk "ETF pick of the hour" (jb-mdata), running inside the gateway. Free: fixed rules, no AI.

Every hour on the hour (Sunday 8 pm to Friday 8 pm New York time) the board looks at the 3x leveraged
ETF pairs on the Night desk twins list (TQQQ / SQQQ, SOXL / SOXS, ...) and picks the one leg that
the rules like best right now:
  * Move since the 4 pm close of the index it follows (bigger = stronger)      up to 40 points
  * The last hour going the same way (the move is still building)             up to 30 points
  * The market mood on the World tab agreeing (risk-on for bull, risk-off for bear funds)   up to 20
  * Heavy volume in that fund (relative volume from the scanner data)          up to 10
Below 35 points nothing is clear, and the pick is "no clear pick, stay flat".
An hour later each pick is graded: how much the fund moved since it was picked. The track record
shows how the rules have done. Research help, not advice: the rules know nothing about news.
  GET  /api/plus/pick                 {current, history, record, settings, next_at}
  POST /api/plus/pick/settings        {push: true|false}
Files: data/pick_history.json, data/pick_settings.json.
"""
import math
import os
import threading
import time
from datetime import timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
HIST = os.path.join(DATA, 'pick_history.json')
SETTINGS = os.path.join(DATA, 'pick_settings.json')
MIN_SCORE = 35
EQUITY = ('Nasdaq-100', 'S&P 500', 'Dow', 'Russell 2000', 'Semiconductors', 'Technology', 'Financials', 'Biotech')

_lock = threading.Lock()
_st = {'started': False, 'mem': {}, 'last_hour': None, 'errors': {}}


def _now():
    return int(time.time() * 1000)


def settings():
    s = watch._read(SETTINGS, {})
    return {'push': bool(s.get('push', False))} if isinstance(s, dict) else {'push': False}


def history():
    h = watch._read(HIST, [])
    return h if isinstance(h, list) else []


def open_now(ms):
    """Sunday 8 pm .. Friday 8 pm New York time (the hours 3x ETFs can trade somewhere)."""
    d = watch.et(ms)
    wd, h = d.weekday(), d.hour
    if wd == 5 or (wd == 4 and h >= 20) or (wd == 6 and h < 20):
        return False
    return True


def _remember(sym, p, ms):
    if not p or not p.get('last'):
        return
    h = _st['mem'].setdefault(sym, [])
    if not h or ms - h[-1][0] >= 60000:
        h.append([ms, p['last']])
    while h and ms - h[0][0] > 3 * 3600000:
        h.pop(0)


def hour_change(sym, ms):
    h = _st['mem'].get(sym) or []
    if not h or ms - h[-1][0] > 15 * 60000:
        return None
    then = None
    for t, v in h:
        if t <= ms - 55 * 60000:
            then = v
    return watch.pct(h[-1][1], then) if then else None


def candidates(ms):
    import mdata
    try:
        import world
        m = world.mood()
    except Exception:
        m = None
    mood = m['score'] if m else None
    with mdata._lock:
        rv = dict(mdata._st.get('rv') or {})
    out, seen = [], set()
    for g, base, lev, bull, bear in mdata.pairs():
        if lev != 3 or (bull, bear) in seen:
            continue
        seen.add((bull, bear))
        b = mdata.price(base, ms)
        if not b or b.get('since_close') is None:
            continue
        mv = b['since_close']
        hr = hour_change(base, ms)
        for side, sym, sign in (('bull', bull, 1), ('bear', bear, -1)):
            if not sym:
                continue
            p = mdata.price(sym, ms)
            why, score = [], 0.0
            d = sign * mv
            s1 = 40 * max(0.0, math.tanh(d / 1.0))
            score += s1
            why.append(f"{base} {mv:+.2f}% since the 4 pm close" + (f' (fits the {side} fund)' if d > 0 else f' (against the {side} fund)'))
            if hr is not None:
                s2 = 30 * max(0.0, math.tanh(sign * hr / 0.4))
                score += s2
                why.append(f"last hour {hr:+.2f}%" + (' (still building)' if sign * hr > 0.05 else ' (fading)' if sign * hr < -0.05 else ' (flat)'))
            if mood is not None and g in EQUITY:
                s3 = 20 * max(0.0, math.tanh(sign * mood / 40))
                score += s3
                why.append(f"market mood {mood:+d}" + (' agrees' if sign * mood > 10 else ' disagrees' if sign * mood < -10 else ' is mixed'))
            r = (rv.get(sym) or {}).get('rv')
            if r:
                score += 10 * min(1.0, max(0.0, (r - 1) / 2))
                why.append(f"volume {r:.1f}x usual")
            out.append({'sym': sym, 'side': side, 'group': g, 'base': base, 'score': int(round(score)), 'why': why,
                        'price': p['last'] if p else None, 'src': p.get('src') if p else None})
    out.sort(key=lambda c: -c['score'])
    return out


def make_pick(ms):
    c = candidates(ms)
    best = c[0] if c and c[0]['score'] >= MIN_SCORE else None
    return {'at': ms, 'pick': best, 'runners_up': c[1:4] if best else c[:3], 'checked': len(c)}


def grade(ms):
    """Fill in how each pick did 60 minutes later."""
    import mdata
    changed = False
    h = history()
    for r in h:
        p = r.get('pick')
        if not p or 'result' in r or ms - r['at'] < 60 * 60000:
            continue
        if ms - r['at'] > 3 * 3600000 or not p.get('price'):
            r['result'] = None
            changed = True
            continue
        now = mdata.price(p['sym'], ms)
        if now and now.get('at', 0) >= r['at'] + 50 * 60000:
            r['result'] = round(watch.pct(now['last'], p['price']), 2)
            changed = True
    if changed:
        watch._write(HIST, h[-500:])


def record(h):
    done = [r['result'] for r in h if r.get('pick') and r.get('result') is not None]
    if not done:
        return {'graded': 0}
    wins = sum(1 for x in done if x > 0)
    last20 = done[-20:]
    return {'graded': len(done), 'wins': wins, 'win_pct': round(100 * wins / len(done)), 'avg': round(sum(done) / len(done), 2),
            'last20_win_pct': round(100 * sum(1 for x in last20 if x > 0) / len(last20)), 'best': max(done), 'worst': min(done)}


def _tick():
    import mdata
    ms = _now()
    for g, base, lev, bull, bear in mdata.pairs():
        if lev == 3:
            for s in (base, bull, bear):
                if s:
                    _remember(s, mdata.price(s, ms), ms)
    grade(ms)
    d = watch.et(ms)
    key = d.strftime('%Y-%m-%d %H')
    if d.minute >= 5 or _st['last_hour'] == key or not open_now(ms):
        return
    if any(watch.et(r['at']).strftime('%Y-%m-%d %H') == key for r in history()[-2:]):
        _st['last_hour'] = key                    # already picked this hour (gateway restarted)
        return
    _st['last_hour'] = key
    r = make_pick(ms)
    h = history()
    h.append(r)
    watch._write(HIST, h[-500:])
    p = r['pick']
    if p and settings()['push']:
        watch.push_later(f"Pick of the hour: {p['sym']}", f"{p['sym']} ({p['group']} {'bull' if p['side'] == 'bull' else 'bear'} 3x), score {p['score']}/100. " + '; '.join(p['why']) + '. Rules only, not advice.', 'dart')


def _loop():
    while True:
        try:
            _tick()
            _st['errors'].pop('pick', None)
        except Exception as e:
            _st['errors']['pick'] = str(e)[:160]
            print(f'pick error: {e!r}', flush=True)
        time.sleep(20)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_loop, name='pick', daemon=True).start()


def next_hour(ms):
    d = watch.et(ms).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return int(d.timestamp() * 1000)


def get(sub, query):
    h = history()
    picks = [r for r in h if 'at' in r]
    cur = picks[-1] if picks else None
    ms = _now()
    if cur and ms - cur['at'] > 65 * 60000:
        cur = None
    return 200, {'current': cur, 'history': list(reversed(picks[-24:])), 'record': record(picks), 'settings': settings(),
                 'next_at': next_hour(ms), 'open': open_now(ms), 'preview': None if cur else make_pick(ms), 'errors': dict(_st['errors'])}


def post(sub, data):
    if sub == 'settings':
        s = settings()
        if 'push' in data:
            s['push'] = bool(data['push'])
        watch._write(SETTINGS, s)
        return 200, {'settings': s}
    return 404, {'error': 'unknown'}
