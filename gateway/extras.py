"""Extras (jb-extras): the market "weather report", the phone page, and the board health page.

  * Weather: each sector fund (XLK tech, XLE energy, ...) gets a word from how far it moved:
      sunny / partly sunny / cloudy / rainy / stormy. "Today" is the move since the last 4 pm
      close (pre-market included, from the Analysis tab's sector numbers); "this week" is the
      move since the last close of the week before (Yahoo daily bars, refreshed every 6 hours).
      Saved every 5 minutes in data/weather.json, where the recap videos read it.
  * /api/phone   everything the phone page (phone.html) shows, in one small answer.
  * /api/health/page and /api/health/restart: the Health tab (health.html). Which services are
      running (asks macOS's launchd, the same as ./jb status), when each broker last sent a price,
      the last lines of each log, and a Restart button per service. Restarting the board itself
      uses "launchctl kickstart", so macOS brings it straight back (the page reconnects by itself).
  * week_facts(): what the weekly recap needs from the board (your trades, the algo leaderboard,
      the calendar), written to data/recaps/week_facts.json just before the video is made.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import date, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
WEATHER = os.path.join(DATA, 'weather.json')
LOGS = os.path.join(ROOT, 'logs')

_lock = threading.Lock()
_st = {'started': False, 'weather': None, 'restarts': {}, 'svc_cache': (0, None)}


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
        json.dump(obj, f)
    os.replace(tmp, path)


def _now():
    return int(time.time() * 1000)


# ------------------------------------------------------------------ weather
SPOKEN = {'XLK': 'tech', 'XLC': 'communication stocks', 'XLY': 'consumer spending', 'XLP': 'everyday goods',
          'XLF': 'finance', 'XLV': 'health care', 'XLE': 'energy', 'XLI': 'industrials',
          'XLB': 'materials', 'XLU': 'utilities', 'XLRE': 'real estate'}
# (lowest % move for the word, word, icon); a week's moves are bigger, so its steps are 2.5x wider
LEVELS = [(1.0, 'sunny', '☀️'), (0.3, 'partly sunny', '🌤️'), (-0.3, 'cloudy', '☁️'),
          (-1.0, 'rainy', '🌧️'), (None, 'stormy', '⛈️')]


def word(chg, scale=1.0):
    for low, w, icon in LEVELS:
        if low is None or chg >= low * scale:
            return w, icon


def forecast(moves, scale=1.0):
    """moves {etf: % change} -> rows (best first) and the spoken sentence."""
    import analysis
    rows = []
    for sym, chg in moves.items():
        if chg is None:
            continue
        w, icon = word(chg, scale)
        rows.append({'sym': sym, 'name': analysis.SECTOR_ETF.get(sym, sym), 'say': SPOKEN.get(sym, sym),
                     'chg': round(chg, 2), 'word': w, 'icon': icon})
    rows.sort(key=lambda r: -r['chg'])
    return rows, sentence(rows)


def sentence(rows):
    if not rows:
        return ''
    groups = {}
    for r in rows:
        groups.setdefault(r['word'], []).append(r['say'])
    order = [w for _, w, _ in LEVELS]

    def join(xs):
        return xs[0] if len(xs) == 1 else ', '.join(xs[:-1]) + ' and ' + xs[-1]
    bits = [f'{w} in {join(groups[w])}' for w in order if w != 'cloudy' and w in groups]
    if not bits:
        return 'Market weather: cloudy everywhere, with nothing much moving.'
    out = 'Market weather: ' + '; '.join(bits) + '.'
    if 'cloudy' in groups:
        out += ' Cloudy everywhere else.' if len(groups['cloudy']) > 2 else f" Cloudy in {join(groups['cloudy'])}."
    return out


def _day_moves():
    """% since the last 4 pm close per sector fund: the board's own prices if it has the fund,
    else the Analysis tab's Yahoo numbers (pre-market and after-hours included)."""
    import analysis
    out = {}
    with analysis._lock:
        etf = dict(analysis._c['etf'])
    for sym in analysis.SECTOR_ETF:
        s = analysis.sessions(sym) or (etf.get(sym) or {}).get('s') or {}
        out[sym] = s.get('total')
    return out


def week_change(rows):
    """(% change, from date, to date): last daily close vs the last close before that week's Monday."""
    if not rows:
        return None, None, None
    last = rows[-1]
    d = date.fromisoformat(last[0])
    monday = (d - timedelta(days=d.weekday())).isoformat()
    base = next((r for r in reversed(rows) if r[0] < monday), None)
    if not base or not base[4]:
        return None, None, None
    return (last[4] - base[4]) / base[4] * 100, base[0], last[0]


def _week_moves():
    import analysis
    out, span = {}, None
    for sym in analysis.SECTOR_ETF:
        chg, a, b = week_change(analysis._cached_daily(sym))
        out[sym] = chg
        if chg is not None:
            span = (a, b)
    return out, span


def refresh_weather():
    day_rows, day_line = forecast(_day_moves())
    wk, span = _week_moves()
    week_rows, week_line = forecast(wk, 2.5)
    w = {'at': _now(), 'close_ms': watch.last_close_ms(_now()),
         'day': {'rows': day_rows, 'line': day_line},
         'week': {'rows': week_rows, 'line': week_line.replace('Market weather:', 'Market weather for the week:'),
                  'from': span[0] if span else None, 'to': span[1] if span else None}}
    with _lock:
        _st['weather'] = w
    _write(WEATHER, w)
    return w


def _loop():
    import analysis
    last_daily = 0
    time.sleep(30)                                  # let the other tabs start first
    while True:
        try:
            if time.time() - last_daily > 6 * 3600:  # daily bars for the week's weather
                last_daily = time.time()
                for sym in analysis.SECTOR_ETF:
                    try:
                        analysis.daily(sym)
                    except Exception as e:
                        print(f'extras: daily {sym}: {e!r}', flush=True)
                        if '429' in str(e):
                            last_daily = time.time() - 5 * 3600    # Yahoo said slow down: try in an hour
                            break
                    time.sleep(3)
            refresh_weather()
        except Exception as e:                       # never let the thread die
            print(f'extras: weather: {e!r}', flush=True)
        time.sleep(300)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
        _st['weather'] = _read(WEATHER, None)
    threading.Thread(target=_loop, name='extras', daemon=True).start()


def weather():
    with _lock:
        return _st['weather']


# ------------------------------------------------------------------ health
SERVICES = [
    ('gateway', 'The board', 'This website: every tab, the alerts, the recap videos.', 'JB_GATEWAY_PORT', None),
    ('ibkr', 'IBKR prices', 'Live prices from IB Gateway (overnight included) for the IBKR board.', 8765, 'RUN_IBKR_BRIDGE'),
    ('webull', 'Webull prices', 'Live prices from Webull for the Webull board.', 8766, 'RUN_WEBULL_BRIDGE'),
    ('scanner', 'Scanner and Picks', 'The ETF and stock scanner, Picks and earnings dates.', 8767, 'RUN_SCANNER'),
    ('webull-algo', 'Webull paper algo', 'The Webull practice trader (paper money).', None, 'RUN_WEBULL_ALGO'),
    ('ibkr-algo', 'IBKR paper algo', 'The IBKR practice trader (paper money).', None, 'RUN_IBKR_ALGO'),
    ('keepawake', 'Keep awake', 'Stops the Mac from going to sleep.', None, 'KEEP_AWAKE'),
    ('ibgateway', 'IB Gateway auto-login', 'Starts IB Gateway and logs in for you (IBC). Tap Approve in IBKR Mobile when asked.', None, 'RUN_IB_AUTOLOGIN'),
]
NAMES = [s[0] for s in SERVICES]


def _settings_switches():
    """RUN_... / KEEP_AWAKE from settings.env as it is now (only those keys are read)."""
    keys = {s[4] for s in SERVICES if s[4]}
    out = {k: os.environ.get(k, '') for k in keys}
    try:
        with open(os.path.join(ROOT, 'settings.env'), encoding='utf-8', errors='replace') as f:
            for line in f:
                k, sep, v = line.strip().partition('=')
                k = k.strip()
                if sep and k in keys:
                    out[k] = v.split(' #')[0].strip().strip('\'"')
    except OSError:
        pass
    return {k: v.lower() in ('on', 'yes', 'true', '1') for k, v in out.items()}


def _launchd():
    """{label: (pid or None, last exit code)} from 'launchctl list'; None if launchctl isn't here."""
    try:
        out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    jobs = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) == 3 and parts[2].startswith('com.josephsboard.'):
            pid = int(parts[0]) if parts[0].strip().isdigit() else None
            jobs[parts[2][len('com.josephsboard.'):]] = (pid, parts[1].strip())
    return jobs


def _port_up(port):
    try:
        with socket.create_connection(('127.0.0.1', int(port)), timeout=0.6):
            return True
    except Exception:
        return False


def _tail(path, n=4, skip_banner=True):
    """The last n lines of a log (and when the service last started)."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - 16000))
            lines = f.read().decode('utf-8', 'replace').splitlines()
    except OSError:
        return [], None
    started = None
    for ln in reversed(lines):
        if ln.startswith('===== ') and 'starting' in ln:
            started = ln.strip('= ').replace(' starting', ',').split(',')[0]
            break
    keep = [ln[:220] for ln in lines if ln.strip() and not (skip_banner and ln.startswith('====='))]
    return keep[-n:], started


def _last_tick(broker):
    """Newest minute the broker's prices were recorded (data/<broker>_ticks/), ms or None."""
    folder = os.path.join(DATA, f'{broker}_ticks')
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith('.csv'))
    except OSError:
        return None
    for n in reversed(names[-2:]):
        p = os.path.join(folder, n)
        try:
            size = os.path.getsize(p)
            with open(p, 'rb') as f:
                f.seek(max(0, size - 4000))
                lines = f.read().decode('utf-8', 'replace').splitlines()
        except OSError:
            continue
        best = None
        for ln in lines:
            parts = ln.split(',')
            if len(parts) == 3 and parts[1].isdigit():
                best = max(best or 0, int(parts[1]))
        if best:
            return best
    return None


def _live_price(broker):
    """Newest price the board itself got from this broker (gateway/watch.py), ms or None."""
    with watch._lock:
        ts = [h[-1][0] for s, h in watch._px.items() if h and watch._src.get(s) == broker]
    return max(ts) if ts else None


def services():
    sw = _settings_switches()
    jobs = _launchd()
    algo_dirs = {'webull-algo': os.path.join(ROOT, 'webull-algo'), 'ibkr-algo': os.path.join(ROOT, 'ibkr-algo')}
    out = []
    for name, title, about, port, key in SERVICES:
        if port == 'JB_GATEWAY_PORT':
            port = int(os.environ.get('JB_GATEWAY_PORT', '8090') or 8090)
        on = True if key is None else sw.get(key, False)
        row = {'name': name, 'title': title, 'about': about, 'on': on, 'port': port}
        if name == 'gateway':
            row['state'] = 'running'
            row['pid'] = os.getpid()
            row['port_up'] = True                    # it is answering: this page came from it
        elif jobs is None:
            row['state'] = 'unknown'
        elif name in jobs:
            pid, code = jobs[name]
            row['pid'] = pid
            row['state'] = 'running' if pid else 'restarting'
            row['exit'] = code
        else:
            row['state'] = 'stopped' if on else 'off'
        if port and name != 'gateway' and row['state'] in ('running', 'unknown'):
            row['port_up'] = _port_up(port)
            if row['state'] == 'unknown':
                row['state'] = 'running' if row['port_up'] else ('stopped' if on else 'off')
        if name in algo_dirs:
            st = _read(os.path.join(algo_dirs[name], 'logs', 'live_status.json'), None)
            try:
                age = int(time.time() - os.path.getmtime(os.path.join(algo_dirs[name], 'logs', 'live_status.json')))
            except OSError:
                age = None
            row['algo'] = {'age_s': age, 'killed': bool((st or {}).get('killed')),
                           'strategy': (st or {}).get('strategy_name') or '', 'symbol': (st or {}).get('symbol') or ''}
            if row['state'] == 'unknown':
                row['state'] = 'running' if age is not None and age < 180 else ('stopped' if on else 'off')
        lines, started = _tail(os.path.join(LOGS, f'{name}.log'))
        row['log'], row['started'] = lines, started
        last = _st['restarts'].get(name)
        row['restarted_ms'] = int(last * 1000) if last else None
        out.append(row)
    return out


def health_page():
    with _lock:
        ts, cached = _st['svc_cache']
    if cached is None or time.time() - ts > 5:
        cached = services()
        with _lock:
            _st['svc_cache'] = (time.time(), cached)
    prices = {}
    for b in ('ibkr', 'webull'):
        prices[b] = {'live_ms': _live_price(b), 'recorded_ms': _last_tick(b)}
    try:
        du = shutil.disk_usage(ROOT)
        disk = {'free_gb': round(du.free / 1e9, 1), 'total_gb': round(du.total / 1e9, 1)}
    except OSError:
        disk = None
    other = {}
    for n in ('recap', 'health'):
        other[n] = _tail(os.path.join(LOGS, f'{n}.log'), 3)[0]
    return json.dumps({'server_ms': _now(), 'services': cached, 'prices': prices, 'disk': disk,
                       'other_logs': other, 'connected': dict(watch._state.get('connected') or {}),
                       'launchd': _launchd() is not None})


def restart(name):
    """Restart one service the same way ./jb restart <name> does. Returns (status, json)."""
    if name not in NAMES:
        return 400, json.dumps({'ok': False, 'error': 'unknown service'})
    last = _st['restarts'].get(name, 0)
    if time.time() - last < 30:
        return 429, json.dumps({'ok': False, 'error': 'It was just restarted. Give it 30 seconds.'})
    _st['restarts'][name] = time.time()
    with _lock:
        _st['svc_cache'] = (0, None)
    os.makedirs(LOGS, exist_ok=True)
    log = open(os.path.join(LOGS, 'health.log'), 'a')
    log.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} restart {name} (Health tab)\n")
    log.flush()
    if name == 'gateway':
        # launchd itself stops and starts the board, so it comes back even though this process ends
        cmd = ['/bin/bash', '-c', f'sleep 1; launchctl kickstart -k gui/{os.getuid()}/com.josephsboard.gateway']
    else:
        cmd = ['/bin/bash', os.path.join(ROOT, 'jb'), 'restart', name]
    try:
        subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception as e:
        log.close()
        return 500, json.dumps({'ok': False, 'error': f'could not start the restart: {e}'})
    log.close()
    return 200, json.dumps({'ok': True, 'name': name})


# ------------------------------------------------------------------ phone page
def _session(now_ms):
    d = watch.et(now_ms)
    m = d.hour * 60 + d.minute
    wk = d.weekday()
    try:
        from nyse_hours import trading_day
        open_day = trading_day(d.date())
    except Exception:
        open_day = wk < 5
    if wk == 5 or (wk == 4 and m >= 20 * 60) or (wk == 6 and m < 20 * 60):
        return 'Weekend', 'Closed until Sunday 8 pm (overnight)'
    if 20 * 60 <= m or m < 3 * 60 + 50:
        return 'Overnight', 'Blue Ocean, 8 pm - 3:50 am'
    if not open_day:
        return 'Holiday', 'Market closed today'
    if m < 4 * 60:
        return 'Quiet', 'Pre-market starts at 4 am'
    if m < 9 * 60 + 30:
        return 'Pre-market', '4 am - 9:30 am'
    if m < 16 * 60:
        return 'Regular', '9:30 am - 4 pm'
    return 'After-hours', '4 pm - 8 pm'


def phone():
    now = _now()
    sess, sess_note = _session(now)
    out = {'server_ms': now, 'session': sess, 'session_note': sess_note}
    try:
        import world
        w = json.loads(world.snapshot())
        want = ('ES=F', 'NQ=F', 'YM=F', 'RTY=F', '^VIX', 'CL=F', 'BTC-USD')
        out['mood'] = w.get('mood')
        out['futures'] = [{'sym': r['sym'], 'name': r['name'], 'chg': r.get('since_close'), 'last': r.get('last')}
                          for s in want for r in w['rows'] if r['sym'] == s and r.get('last') is not None]
        out['events'] = [e for e in w.get('calendar') or [] if e['t'] >= now - 3600000 and
                         e.get('impact') == 'High'][:3]
    except Exception as e:
        out['world_error'] = str(e)[:120]
    out['weather'] = (weather() or {}).get('day')
    # board movers vs the 4 pm close (and overnight)
    try:
        held = set()
        try:
            import portfolio
            held = {p['sym'] for p in portfolio.positions()}
        except Exception:
            pass
        with watch._lock:
            syms = list(watch._px)
        rows = []
        for s in syms:
            st = watch.stats(s, now)
            if st and st.get('close') is not None:
                rows.append({'sym': s, 'last': st['last'], 'chg': round(st['close'], 2),
                             'on': round(st['on'], 2) if st.get('on') is not None else None, 'held': s in held})
        rows.sort(key=lambda r: -abs(r['chg']))
        out['movers'] = rows[:8]
        out['held'] = [r for r in rows if r['held']][:8]
    except Exception as e:
        out['movers_error'] = str(e)[:120]
    try:
        import trading
        t = json.loads(trading.snapshot())
        s = t['summary']
        out['trading'] = {'today': s['today_realized'], 'unrealized': s['unrealized'],
                          'limit': t['settings'].get('loss_limit') if t['settings'].get('loss_on') else None,
                          'algos': {n: {'killed': bool((a or {}).get('killed')), 'pnl': (a or {}).get('total')}
                                    for n, a in (t.get('algos') or {}).items()}}
    except Exception as e:
        out['trading_error'] = str(e)[:120]
    # the latest phone alerts (Signals tab + Alerts panel)
    sig = _read(os.path.join(DATA, 'signals_log.json'), [])[:10]
    al = _read(os.path.join(DATA, 'alerts_log.json'), [])[:10]
    out['alerts'] = sorted(({'at': a.get('at'), 'sym': a.get('sym', ''), 'text': a.get('text', '')} for a in sig + al
                            if isinstance(a, dict)), key=lambda a: -(a['at'] or 0))[:6]
    # anything not running
    try:
        with _lock:
            ts, cached = _st['svc_cache']
        if cached is None or time.time() - ts > 60:
            cached = services()
            with _lock:
                _st['svc_cache'] = (time.time(), cached)
        out['down'] = [s['title'] for s in cached if s['on'] and s['state'] != 'running']
    except Exception:
        out['down'] = []
    try:
        import recap
        r = json.loads(recap.status()).get('recaps') or []
        out['recap'] = {'title': r[0]['title'], 'file': r[0]['file']} if r else None
    except Exception:
        out['recap'] = None
    return json.dumps(out)


# ------------------------------------------------------------------ weekly recap facts
def week_facts():
    """What the weekly recap video needs from the running board, saved for bin/make_recap.py."""
    facts = {'at': _now()}
    try:
        import trading
        t = json.loads(trading.snapshot())
        facts['days'] = t['summary']['days']
        facts['trips'] = [{'sym': x['sym'], 'pnl': x['pnl'], 'day': x['day'], 'entry_session': x['entry_session'],
                           'tag': x.get('tag', '')} for x in t['trips']]
        lb = trading.leaderboard()
        facts['leaderboard'] = [{'name': r['name'], 'algo': r['algo'], 'symbol': r['symbol'], 'd5': r['d5'],
                                 'running': r['running']} for r in lb['rows'] if not r['error']]
    except Exception as e:
        facts['trading_error'] = str(e)[:200]
    try:
        import world
        facts['calendar'] = json.loads(world.snapshot()).get('calendar') or []
    except Exception as e:
        facts['calendar_error'] = str(e)[:200]
    try:
        import analysis
        facts['symbols'] = sorted(analysis.board_symbols())
    except Exception:
        facts['symbols'] = []
    try:
        refresh_weather()
    except Exception as e:
        print(f'extras: weather: {e!r}', flush=True)
    _write(os.path.join(DATA, 'recaps', 'week_facts.json'), facts)
    return facts
