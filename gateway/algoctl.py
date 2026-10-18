"""The algo tab's buttons: Run backtest (no Terminal needed) and Stop / Resume (kill switch).

  GET  /api/algo/<webull|ibkr>/control   strategy files, which one the algo runs, kill switch
                                         state, and the last page-started backtest
  POST /api/algo/<webull|ibkr>/control   {"action": "backtest", "strategy": "overnight_gap.yaml"}
                                         (+ "recorded": true, "sessions": ["afterhours"] from the Hedge Ideas list)
                                         {"action": "stop"}     kill switch on: <algo>/logs/KILL
                                         {"action": "resume"}   kill switch off
                                         {"action": "use", "strategy": "x.yaml"}   the PAPER algo runs x.yaml from now on

Use This Strategy (jb-usestrat): changes WEBULL_ALGO_STRATEGY / IBKR_ALGO_STRATEGY in settings.env (that one line
only), then restarts that paper algo. Refused while live trading runs, while the paper algo holds a position (press
Stop first), or if the file is not a valid strategy. Live trading is never started or changed here: that is Go Live
(gateway/livectl.py) or ./jb live.

A backtest runs the same command as  ./jb backtest <broker> strategies/<file>  (the last 10
days of 1-minute bars from the broker), one at a time, and writes logs/backtest_status.json
which the tab already shows. Its output goes to logs/backtest_run.log.
The kill switch is a file, so it holds through restarts until Resume; the algo itself
(algo/core_trader.py) closes its position and opens nothing new while it is there.
"""
import datetime as _dt
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIRS = {'webull': os.path.join(ROOT, 'webull-algo'), 'ibkr': os.path.join(ROOT, 'ibkr-algo')}
ENV = {'webull': 'WEBULL_ALGO_STRATEGY', 'ibkr': 'IBKR_ALGO_STRATEGY'}
MAX_RUN_S = 15 * 60
_lock = threading.Lock()
_use_lock = threading.Lock()
_runs = {}          # name -> {'proc', 'strategy', 'started', 'ended', 'code'}


def _strategies(name):
    d = os.path.join(DIRS[name], 'strategies')
    try:
        return sorted(f for f in os.listdir(d) if f.endswith(('.yaml', '.yml')) and not f.startswith('.'))
    except OSError:
        return []


def _current(name):
    return os.path.basename(os.environ.get(ENV[name], '') or '') or None


def _kill_path(name):
    return os.path.join(DIRS[name], 'logs', 'KILL')


def _tail(path, n=6):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = [l.rstrip() for l in f.readlines() if l.strip()]
        return lines[-n:]
    except OSError:
        return []


def _run_state(name):
    r = _runs.get(name)
    if not r:
        return None
    p = r['proc']
    if r['code'] is None:
        code = p.poll()
        if code is None and time.time() - r['started'] > MAX_RUN_S:
            p.terminate()
            code = 'timeout'
        if code is not None:
            r['code'], r['ended'] = code, time.time()
    out = {'strategy': r['strategy'], 'started': int(r['started'] * 1000),
           'running': r['code'] is None, 'ok': r['code'] == 0}
    if r['code'] is not None:
        out['ended'] = int(r['ended'] * 1000)
        if r['code'] != 0:
            out['error'] = ('took longer than 15 minutes and was stopped' if r['code'] == 'timeout' else
                            '\n'.join(_tail(os.path.join(DIRS[name], 'logs', 'backtest_run.log'), 3)))
    return out


_SETTINGS = os.path.join(ROOT, 'settings.env')


def _live_running(name):
    lock = os.path.join(DIRS[name], 'logs', 'LIVE_RUNNING')
    try:
        pid = int(open(lock).read().strip() or 0)
    except (OSError, ValueError):
        return os.path.exists(lock)
    if pid <= 0:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _paper_position(name):
    """The paper algo's open position from its status file, or None."""
    try:
        st = json.load(open(os.path.join(DIRS[name], 'logs', 'live_status.json')))
    except Exception:
        return None
    if st.get('mode') != 'paper':
        return None
    pos = st.get('position')
    return pos if pos and pos.get('shares') else None


def _check_strategy(name, strat):
    """None if the algo's own code accepts the file, else the reason (short)."""
    py = sys.executable
    code = ('import sys\nfrom algo.strategy import Strategy\n'
            'try:\n    Strategy.from_yaml(sys.argv[1])\nexcept Exception as e:\n    print(str(e)[-300:]); sys.exit(1)\n')
    try:
        r = subprocess.run([py, '-c', code, 'strategies/' + strat], cwd=DIRS[name], capture_output=True,
                           text=True, timeout=60, stdin=subprocess.DEVNULL)
    except Exception as e:
        return f'could not check it ({e})'
    if r.returncode != 0:
        return (r.stdout or r.stderr or 'not a valid strategy file').strip().splitlines()[-1]
    return None


def _set_setting(key, value):
    """Changes only the KEY= line in settings.env (adds it if missing); everything else stays as it is."""
    try:
        with open(_SETTINGS, encoding='utf-8', errors='surrogateescape', newline='') as f:
            lines = f.read().splitlines(keepends=True)
    except FileNotFoundError:
        lines = []
    done = False
    for i, line in enumerate(lines):
        bare = line.lstrip().lstrip('\ufeff')
        if bare.startswith('export '):
            bare = bare[7:].lstrip()
        if bare.startswith(key) and bare[len(key):].lstrip().startswith('='):
            if not done:
                end = '\r\n' if line.endswith('\r\n') else '\n'
                lines[i] = f'{key}={value}{end}'
                done = True
            else:
                lines[i] = '# ' + line             # a second copy would win when the file is read: turn it off
    if not done:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append(f'{key}={value}\n')
    tmp = _SETTINGS + '.tmp'
    mode = os.stat(_SETTINGS).st_mode & 0o777 if os.path.exists(_SETTINGS) else 0o600
    with open(tmp, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
        f.writelines(lines)
    os.chmod(tmp, mode)
    os.replace(tmp, _SETTINGS)


def _use(name, strat):
    if strat not in _strategies(name):
        return 400, json.dumps({'error': 'pick a strategy file from the list'})
    if strat == _current(name):
        return 400, json.dumps({'error': 'the algo already runs ' + strat})
    if _live_running(name):
        return 409, json.dumps({'error': 'live trading is running (Go Live, or ./jb live in Terminal). It has its own strategy; '
                                         'stop it first (Stop Live, or Ctrl+C in its Terminal window)'})
    pos = _paper_position(name)
    if pos:
        return 409, json.dumps({'error': f"the paper algo holds {pos.get('shares')} shares. Press Stop algo first "
                                         '(it closes the paper position), then switch'})
    bad = _check_strategy(name, strat)
    if bad:
        return 400, json.dumps({'error': 'that file is not a valid strategy: ' + bad})
    old = _current(name)
    _set_setting(ENV[name], 'strategies/' + strat)
    os.environ[ENV[name]] = 'strategies/' + strat          # the board knows at once; the algo after its restart
    logs = os.path.join(ROOT, 'logs')
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, 'health.log'), 'a') as log:
        log.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {name} paper algo: {old} -> {strat} (Use This Strategy)\n")
        log.flush()
        subprocess.Popen(['/bin/bash', os.path.join(ROOT, 'jb'), 'restart', name + '-algo'], cwd=ROOT, stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    return 200, info(name)


def info(name):
    with _lock:
        run = _run_state(name)
    kp = _kill_path(name)
    killed = None
    if os.path.exists(kp):
        try:
            killed = json.load(open(kp)).get('at')
        except Exception:
            killed = True
    return json.dumps({'strategies': _strategies(name), 'current': _current(name), 'killed': killed, 'run': run})


def post(name, data):
    act = data.get('action')
    if act == 'stop':
        os.makedirs(os.path.dirname(_kill_path(name)), exist_ok=True)
        rec = {'at': _dt.datetime.now(_dt.timezone.utc).isoformat()}
        try:                       # review 2026-10-11 #1: keep SELL ALL's mark, so the algo keeps leaving that sale to SELL ALL
            old = json.load(open(_kill_path(name)))
            if old.get('by'):
                rec = dict(old)
        except Exception:
            pass
        with open(_kill_path(name), 'w') as f:
            json.dump(rec, f)
        return 200, info(name)
    if act == 'resume':
        try:
            os.remove(_kill_path(name))
        except FileNotFoundError:
            pass
        return 200, info(name)
    if act == 'backtest':
        strat = str(data.get('strategy') or '')
        if strat not in _strategies(name):
            return 400, json.dumps({'error': 'pick a strategy file from the list'})
        with _lock:
            if _run_state(name) and _runs[name]['code'] is None:
                return 409, json.dumps({'error': 'a backtest is already running'})
            logs = os.path.join(DIRS[name], 'logs')
            os.makedirs(logs, exist_ok=True)
            out = open(os.path.join(logs, 'backtest_run.log'), 'w')
            # jb-hedge (Hedge Ideas): {"recorded": true} backtests on the board's own minute record instead of the broker's
            # 10 days, and "sessions" (e.g. ["afterhours"]) trades those sessions for this one run
            cmd = [sys.executable, 'run_backtest.py', '--strategy', 'strategies/' + strat,
                   '--recorded' if data.get('recorded') is True else '--' + name, '--no-plot']
            sess = data.get('sessions')
            if isinstance(sess, list) and sess and all(x in ('premarket', 'afterhours', 'overnight') for x in sess):
                cmd += ['--sessions', ','.join(sess)]
            try:
                proc = subprocess.Popen(cmd, cwd=DIRS[name], stdout=out,
                                        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            finally:
                out.close()
            _runs[name] = {'proc': proc, 'strategy': strat, 'started': time.time(), 'ended': None, 'code': None}
        return 200, info(name)
    if act == 'use':
        with _use_lock:                 # one switch at a time (not _lock: info() takes that one)
            return _use(name, str(data.get('strategy') or ''))
    return 400, json.dumps({'error': 'unknown action'})
