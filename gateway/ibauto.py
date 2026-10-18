"""Automatic IB Gateway login, the board's side (jb-ibc), running inside the gateway.

The "ibgateway" service (bin/ib-autologin.sh, using IBC) starts IB Gateway and logs in. This file
watches that service's log and:
  * pushes to your phone when IBKR is waiting for you to tap Approve in IBKR Mobile (IB Key),
    at most once every 20 minutes while it waits;
  * pushes once when the login completes after such a wait;
  * shows the state on the Night desk.
  GET /api/plus/ibauto     {on, setup, state, since, last_2fa, last_login, bridge, lines}
"""
import json
import os
import re
import subprocess
import threading
import time

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG = os.path.join(ROOT, 'logs', 'ibgateway.log')
INI = os.path.join(ROOT, 'ibc', 'config.ini')
TWOFA = re.compile(r'second factor|2fa|two.factor', re.I)
DONE = re.compile(r'login has completed|login completed|logged in', re.I)
FAIL = re.compile(r'login failed|invalid (user|password)|unrecognized username|password.*(incorrect|invalid)', re.I)
SECRET = re.compile(r'(password|IbPassword|--pw)\s*[=:]\s*\S+', re.I)

# jb-ibweekly: planned weekly fresh login (IBKR wants a full login about once a week; doing it at a known time
# means the login token never runs out mid-week) and a fresh login whenever IB Gateway says the token expired
WEEKLY_DAY, WEEKLY_HM = 6, (17, 0)       # Sunday 5:00 pm New York time, 1 hour before futures open
EXPIRED = re.compile(r'tokens?\b.{0,80}\bexpired|expired.{0,60}(re-?enter|username and password)', re.I)
STATE = os.path.join(ROOT, 'data', 'ibauto.json')


def _saved():
    try:
        with open(STATE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save(**kw):
    d = _saved()
    d.update(kw)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(d, f)
    os.replace(STATE + '.tmp', STATE)


def _fresh_login(why):
    """Restart IB Gateway (the auto-login then types the password) and ask for the Approve tap."""
    try:
        os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)
        log = open(os.path.join(ROOT, 'logs', 'health.log'), 'a')
        log.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} restart ibgateway ({why})\n")
        log.flush()
        subprocess.Popen(['/bin/bash', os.path.join(ROOT, 'jb'), 'restart', 'ibgateway'], cwd=ROOT, stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
    except Exception as e:
        print(f'ibauto: could not restart IB Gateway: {e!r}', flush=True)


def _weekly(ms):
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    start = WEEKLY_HM[0] * 60 + WEEKLY_HM[1]
    if d.weekday() != WEEKLY_DAY or not (start <= m < start + 30):
        return
    key = d.date().isoformat()
    if _saved().get('weekly') == key:
        return
    _save(weekly=key)
    watch.push_later('Weekly IBKR Login', 'IB Gateway is doing its planned weekly fresh login now. '
                     'Open IBKR Mobile and tap Approve when it asks.', 'key')
    _fresh_login('weekly planned login, Sunday 5 pm ET')


_lock = threading.Lock()
_st = {'started': False, 'pos': 0, 'state': 'unknown', 'since': 0, 'last_2fa': 0, 'last_login': 0, 'pushed_2fa': 0, 'lines': []}


def _now():
    return int(time.time() * 1000)


def is_on():
    v = os.environ.get('RUN_IB_AUTOLOGIN', '')
    try:
        with open(os.path.join(ROOT, 'settings.env'), encoding='utf-8', errors='replace') as f:
            for line in f:
                k, sep, val = line.strip().partition('=')
                if sep and k.strip() == 'RUN_IB_AUTOLOGIN':
                    v = val.split(' #')[0].strip().strip('\'"')
    except OSError:
        pass
    return v.lower() in ('on', 'yes', 'true', '1')


def is_setup():
    try:
        with open(INI, encoding='utf-8', errors='replace') as f:
            return any(re.match(r'IbLoginId=\S', ln) for ln in f)
    except OSError:
        return False


def _set(state, ms):
    if _st['state'] != state:
        _st['state'], _st['since'] = state, ms


def feed(line, ms, replay=False):
    """One new log line -> state changes and pushes (no pushes while replaying old lines at start-up)."""
    clean = SECRET.sub(r'\1=***', line.strip())[:200]
    if not clean:
        return
    with _lock:
        _st['lines'] = (_st['lines'] + [clean])[-8:]
        if clean.startswith('starting IB Gateway') or clean.startswith('===== '):
            _set('starting', ms)
        elif FAIL.search(clean):
            _set('login failed', ms)
        elif EXPIRED.search(clean):                       # jb-ibweekly: expired
            _set('login expired', ms)
            if not replay and ms - (_st.get('expired_at') or 0) > 30 * 60000:
                _st['expired_at'] = ms
                watch.push_later('IBKR Login Expired', 'IB Gateway is logging in fresh now. Open IBKR Mobile and tap Approve when it asks.', 'key')
                _fresh_login('login token expired')
        elif TWOFA.search(clean) and not re.search(r'completed|accepted|approved|closed', clean, re.I):
            _st['last_2fa'] = ms
            _set('waiting for approval', ms)
            if not replay and (not _st['pushed_2fa'] or ms - _st['pushed_2fa'] > 20 * 60000):
                _st['pushed_2fa'] = ms
                watch.push_later('IB Gateway needs your OK', 'Open IBKR Mobile and tap Approve (IB Key) so IB Gateway can log in. '
                                 'It will keep asking every few minutes until you do.', 'key')
        elif DONE.search(clean):
            waited = _st['state'] == 'waiting for approval'
            _st['last_login'] = ms
            _set('logged in', ms)
            if waited and not replay:
                watch.push_later('IB Gateway logged in', 'Thanks: IB Gateway is logged in and the IBKR board is reconnecting.', 'white_check_mark')
            _st['pushed_2fa'] = 0


def read_new(ms):
    try:
        size = os.path.getsize(LOG)
    except OSError:
        return
    replay = not _st.get('read')                      # first look: the recent part, to show the state, without pushing
    _st['read'] = True
    if size < _st['pos']:
        _st['pos'] = 0                                # log was rolled over
    if replay and size > 20000:
        _st['pos'] = size - 20000
    with open(LOG, 'rb') as f:
        f.seek(_st['pos'])
        chunk = f.read(200000)
    _st['pos'] += len(chunk)
    for ln in chunk.decode('utf-8', 'replace').splitlines():
        feed(ln, ms, replay)


def _loop():
    while True:
        try:
            if is_on():
                read_new(_now())
                _weekly(_now())                           # jb-ibweekly: loop
        except Exception as e:
            print(f'ibauto error: {e!r}', flush=True)
        time.sleep(5)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_loop, name='ibauto', daemon=True).start()


def get(sub, query):
    with _lock:
        s = {k: _st[k] for k in ('state', 'since', 'last_2fa', 'last_login', 'lines')}
    s['on'], s['setup'] = is_on(), is_setup()
    s['bridge'] = bool((watch._state.get('connected') or {}).get('ibkr'))
    return 200, s


def post(sub, data):
    return 404, {'error': 'unknown'}
