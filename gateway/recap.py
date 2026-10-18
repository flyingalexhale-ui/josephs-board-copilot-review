"""The Recap tab (jb-recap), running inside the gateway:
  * every weekday at JB_RECAP_TIME (settings.env, default 8:05 = 8:05 am New York time) starts
    bin/make_recap.py, which makes the "since the close" video and sends it to the phone
    (it skips market holidays itself). Only one a day, even if the gateway restarts; if the
    Mac was asleep or the gateway down at that time, it catches up when it's back, until 9:30 am.
  * every Friday at JB_WEEKLY_TIME (default 8:15 = 8:15 pm New York time) the weekly recap
    (jb-extras): the week's best and worst, the market weather, how the picks and your trades did,
    and next week's earnings and events. If the Mac was asleep, it catches up until Sunday 6 pm.
  * /api/recap          GET: the list of recaps; POST {action: 'make'} or {action: 'make-weekly'}
  * /api/recap/video?f=recap-....mp4   the video, with byte ranges (iPhones need them to play)
The videos live in data/recaps/ (the last 14 are kept). Progress goes to logs/recap.log."""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
OUT = os.path.join(DATA, 'recaps')
LOGFILE = os.path.join(ROOT, 'logs', 'recap.log')
MAKER = os.path.join(ROOT, 'bin', 'make_recap.py')
NAME = re.compile(r'^recap-(week-)?[0-9-]+\.mp4$')
LAST_AUTO = os.path.join(OUT, '.last_auto')     # the date of the last morning recap (survives restarts)
LAST_WEEKLY = os.path.join(OUT, '.last_weekly') # the Friday of the last weekly recap
_state = {'started': False, 'proc': None}
_lock = threading.Lock()


def send_time():
    """(hour, minute) from JB_RECAP_TIME, e.g. '8:05' or '7:30'; 'off' turns it off."""
    v = os.environ.get('JB_RECAP_TIME', '8:05').strip().lower()
    if v in ('off', 'no', 'none', ''):
        return None
    m = re.match(r'^(\d{1,2})[:.](\d{2})\s*(am|pm)?$', v)
    if not m:
        return 8, 5
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3) == 'pm' and h < 12:
        h += 12
    return (h, mi) if h < 24 and mi < 60 else (8, 5)


def weekly_time():
    """(hour, minute) on Friday evening from JB_WEEKLY_TIME (an evening time: '8:15' = 8:15 pm); 'off' turns it off."""
    v = os.environ.get('JB_WEEKLY_TIME', '8:15').strip().lower()
    if v in ('off', 'no', 'none', ''):
        return None
    m = re.match(r'^(\d{1,2})[:.](\d{2})\s*(am|pm)?$', v)
    if not m:
        return 20, 15
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3) != 'am' and h < 12:
        h += 12
    return (h, mi) if h < 24 and mi < 60 else (20, 15)


def running():
    with _lock:
        p = _state['proc']
        return p is not None and p.poll() is None


def make(auto=False, weekly=False):
    """Start bin/make_recap.py in the background. Returns '' or why not."""
    if running():
        return 'A recap is already being made.'
    if weekly:
        try:
            import extras
            extras.week_facts()             # your trades, the leaderboard, the calendar
        except Exception as e:
            print(f'recap: weekly facts: {e!r}', flush=True)
    os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
    try:
        os.remove(os.path.join(OUT, 'last_error.json'))
    except OSError:
        pass
    log = open(LOGFILE, 'a')
    cmd = [sys.executable, MAKER] + (['--auto'] if auto else []) + (['--weekly'] if weekly else [])
    p = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    log.close()
    with _lock:
        _state['proc'] = p
    return ''


def _last_auto(path=LAST_AUTO):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _mark_auto(key, path=LAST_AUTO):
    try:
        os.makedirs(OUT, exist_ok=True)
        with open(path, 'w') as f:
            f.write(key)
    except OSError:
        pass


def _weekly_due(now):
    """Friday from JB_WEEKLY_TIME until Sunday 6 pm, once per week."""
    t = weekly_time()
    if not t:
        return None
    from datetime import timedelta
    fri = now.date() - timedelta(days=(now.weekday() - 4) % 7)
    start = now.replace(year=fri.year, month=fri.month, day=fri.day, hour=t[0], minute=t[1], second=0, microsecond=0)
    end = (start + timedelta(days=2)).replace(hour=18, minute=0)
    if start <= now < end and _last_auto(LAST_WEEKLY) != fri.isoformat():
        return fri.isoformat()
    return None


def _loop():
    from zoneinfo import ZoneInfo
    et = ZoneInfo('America/New_York')
    while True:
        time.sleep(20)
        try:
            now = datetime.now(et)
            wk = _weekly_due(now)
            if wk and not running():
                _mark_auto(wk, LAST_WEEKLY)
                print('recap: starting the weekly recap', flush=True)
                make(auto=True, weekly=True)
                continue
            t = send_time()
            if not t or now.weekday() >= 5:
                continue
            start = now.replace(hour=t[0], minute=t[1], second=0, microsecond=0)
            # catch up until the 9:30 open if the Mac was asleep at the send time (else 30 minutes)
            late = max(30 * 60, (now.replace(hour=9, minute=30, second=0, microsecond=0) - start).total_seconds())
            key = now.date().isoformat()
            if start <= now and (now - start).total_seconds() < late and _last_auto() != key and not running():
                _mark_auto(key)
                print(f'recap: starting the {t[0]}:{t[1]:02d} recap', flush=True)
                make(auto=True)
        except Exception as e:                  # never let the thread die
            print(f'recap error: {e!r}', flush=True)


def start():
    with _lock:
        if _state['started']:
            return
        _state['started'] = True
    threading.Thread(target=_loop, name='recap', daemon=True).start()


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def status():
    items = []
    try:
        names = sorted((n for n in os.listdir(OUT) if NAME.match(n)), reverse=True)
    except OSError:
        names = []
    for n in names:
        info = _read(os.path.join(OUT, n[:-4] + '.json'), {}) or {}
        try:
            size = os.path.getsize(os.path.join(OUT, n))
        except OSError:
            continue
        items.append({'file': n, 'size': size, 'at': info.get('at'), 'title': info.get('title') or n,
                      'seconds': info.get('seconds'), 'script': info.get('script', ''),
                      'weekly': n.startswith('recap-week-')})
    items.sort(key=lambda r: -(r['at'] or 0))
    t = send_time()
    log_tail = ''
    try:
        with open(LOGFILE, 'rb') as f:
            f.seek(max(0, os.path.getsize(LOGFILE) - 600))
            log_tail = f.read().decode('utf-8', 'replace').strip().splitlines()[-1:]
            log_tail = log_tail[0] if log_tail else ''
    except OSError:
        pass
    return json.dumps({'recaps': items, 'making': running(), 'error': _read(os.path.join(OUT, 'last_error.json'), None),
                       'time': f'{t[0]}:{t[1]:02d}' if t else 'off', 'last_log': log_tail,
                       'weekly_time': (lambda w: f'{w[0] - 12 if w[0] > 12 else w[0]}:{w[1]:02d} {"pm" if w[0] >= 12 else "am"}' if w else 'off')(weekly_time()),
                       'server_ms': int(time.time() * 1000)})


def post(data):
    if data.get('action') in ('make', 'make-weekly'):
        why = make(auto=False, weekly=data.get('action') == 'make-weekly')
        return (409 if why else 200), json.dumps({'ok': not why, 'error': why})
    return 400, json.dumps({'ok': False, 'error': 'unknown action'})


def video(query, range_header):
    """(status, body, extra_headers) for one recap video, honouring a 'Range: bytes=a-b' header."""
    name = (query.get('f') or [''])[0]
    path = os.path.join(OUT, name)
    if not NAME.match(name) or not os.path.isfile(path):
        return 404, b'not found', {}
    size = os.path.getsize(path)
    extra = {'Accept-Ranges': 'bytes', 'Cache-Control': 'private, max-age=3600'}
    m = re.match(r'^bytes=(\d*)-(\d*)$', (range_header or '').strip())
    if not m or (m.group(1) == '' and m.group(2) == ''):
        with open(path, 'rb') as f:
            return 200, f.read(), extra
    if m.group(1) == '':
        a, b = max(0, size - int(m.group(2))), size - 1
    else:
        a = int(m.group(1))
        b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if a >= size or a > b:
        extra['Content-Range'] = f'bytes */{size}'
        return 416, b'', extra
    with open(path, 'rb') as f:
        f.seek(a)
        body = f.read(b - a + 1)
    extra['Content-Range'] = f'bytes {a}-{b}/{size}'
    return 206, body, extra
