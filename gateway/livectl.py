"""Go Live / Stop Live on the algo tabs (jb-golive): live trading for one broker as a background job, no Terminal.

  GET  /api/algo/<webull|ibkr>/live   what the Go Live card shows: running or not (Go Live, or ./jb live in a Terminal
                                      window), the strategy the paper algo runs and whether its file allows live trading,
                                      the go-live checklist (bin/golive.py, the same one ./jb live prints), the password,
                                      the start-after-a-Mac-restart option, how the last live run ended and the live
                                      position it left (the live record), and the live job's latest log lines
  POST /api/algo/<webull|ibkr>/live   {"action": "start", "confirm": "I_UNDERSTAND_THE_RISK"}   Go Live
                                      {"action": "stop"}                                        Stop Live
                                      {"action": "autostart", "on": true|false}                 the restart option

Go Live runs  ./jb live-service start <broker> strategies/<the paper algo's file> <0|1>  (launchd, like the other
services; bin/run-service.sh <broker>-live). Refused here, before anything starts: no JB_PASSWORD set; the words not
typed exactly; a live run already going (either way); the strategy file without confirm_live: true or without
risk: max_order_notional_usd, or not a valid strategy. The job then runs the trader's own start-up checks, exactly as
./jb live does (an order or an Auto-Hedge order still open at the broker, the position differing from the algo's
record, the account...): a refusal there ends it at once and this card shows the reason from its log.
While it runs: logs/LIVE_RUNNING (the guardian watches it and pushes the phone if it stops on its own), the paper
trader off. Stop Live: logs/LIVE_STOP, then launchd stops the job: orders still working are cancelled, nothing new is
sent, the position stays (shown here from the live record); the paper trader starts again.
After a Mac restart live trading does NOT start again by itself, unless the option is on (default off,
data/live_settings.json). ./jb live in Terminal keeps working as a backup.
"""
import json
import os
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
SETTINGS = os.path.join(DATA, 'live_settings.json')
CONFIRM = 'I_UNDERSTAND_THE_RISK'
ENV = {'webull': 'WEBULL_ALGO_STRATEGY', 'ibkr': 'IBKR_ALGO_STRATEGY'}
_lock = threading.Lock()
_yaml_cache = {}


def _dirs():
    import algoctl
    return algoctl.DIRS


def _logs(name):
    return os.path.join(_dirs()[name], 'logs')


def _read_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _read_kv(path):
    out = {}
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                k, _, v = line.strip().partition('=')
                if k:
                    out[k] = v
    except OSError:
        pass
    return out


def _pid_alive(path):
    try:
        pid = int(open(path).read().strip() or 0)
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
        return pid
    except PermissionError:
        return pid
    except OSError:
        return None


def password_set():
    return bool((os.environ.get('JB_PASSWORD') or '').strip())


def settings():
    v = _read_json(SETTINGS, {})
    return v if isinstance(v, dict) else {}


def _save_settings(v):
    os.makedirs(DATA, exist_ok=True)
    tmp = SETTINGS + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(v, f, indent=1)
    os.replace(tmp, SETTINGS)


def paper_strategy(name):
    """'strategies/<file>' the paper algo runs (settings.env), or None."""
    base = os.path.basename(os.environ.get(ENV[name], '') or '')
    return 'strategies/' + base if base else None


def strategy_facts(name, rel):
    """What the strategy file says about live trading (read with YAML, cached by file time)."""
    path = os.path.join(_dirs()[name], rel or '')
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        return {'file': rel, 'exists': False}
    hit = _yaml_cache.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    out = {'file': rel, 'exists': True}
    try:
        import yaml
        with open(path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        risk = cfg.get('risk') or {}
        entry = cfg.get('entry') or {}
        cap = risk.get('max_order_notional_usd')
        try:
            cap = float(cap) if cap is not None else None
        except (TypeError, ValueError):
            cap = None
        out.update(name=str(cfg.get('name') or ''), symbol=str(cfg.get('symbol') or ''),
                   confirm_live=cfg.get('confirm_live', False) is True, max_order_usd=cap if cap and cap > 0 else None,
                   side=str(entry.get('side') or 'buy').lower())
    except Exception as e:
        out['error'] = str(e)[:160]
    _yaml_cache[path] = (mt, out)
    return out


def checklist(name):
    import sys
    b = os.path.join(ROOT, 'bin')
    if b not in sys.path:
        sys.path.insert(0, b)
    import golive
    return golive.checklist(name, ROOT)


def _log_tail(name, n=12):
    """The live job's latest lines: from the last 'starting <broker>-live' on, the warnings and errors."""
    path = os.path.join(ROOT, 'logs', name + '-live.log')
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 60000))
            lines = f.read().splitlines()
    except OSError:
        return []
    start = max((i for i, l in enumerate(lines) if l.startswith('=====') and 'starting ' + name + '-live' in l), default=0)
    keep = [l for l in lines[start + 1:] if l.strip() and ('WARNING' in l or 'ERROR' in l or 'LIVE' in l
                                                           or 'NOT STARTED' in l or not l[:4].isdigit())]
    return [l[:300] for l in keep[-n:]]


def _live_record(name, rel):
    """The open position in the live record (logs/state_<strategy>_live.json), or None."""
    stem = os.path.splitext(os.path.basename(rel or ''))[0]
    d = _read_json(os.path.join(_logs(name), f'state_{stem}_live.json'), None) if stem else None
    if not isinstance(d, dict):
        return None
    pf = d.get('portfolio') if isinstance(d.get('portfolio'), dict) else {}
    pos = pf.get('position')
    return dict(pos, symbol=d.get('symbol')) if isinstance(pos, dict) and pos.get('shares') else None


def state(name):
    logs = _logs(name)
    lock = os.path.join(logs, 'LIVE_RUNNING')
    pid = _pid_alive(lock)
    by = 'service' if pid and os.path.exists(lock + '.by') else ('terminal' if pid else None)
    req = _read_kv(os.path.join(logs, 'LIVE_SERVICE'))
    paper = paper_strategy(name)
    strat = req.get('strategy') if by == 'service' and req.get('strategy') else paper
    st = _read_json(os.path.join(logs, 'live_status.json'), {}) or {}
    last = _read_json(os.path.join(logs, 'LIVE_LAST.json'), None)
    live = None
    if pid and st.get('mode') == 'live':
        live = {k: st.get(k) for k in ('strategy_name', 'symbol', 'connected', 'last_error', 'position', 'halted',
                                       'safe_mode', 'killed', 'last_price', 'unrealized_pnl', 'updated_at')}
    try:
        started = os.stat(lock).st_mtime if pid else None
    except OSError:
        started = None
    out = {'name': name, 'running': bool(pid), 'by': by, 'pid': pid, 'started_at': int(started * 1000) if started else None,
           'strategy': strat, 'paper_strategy': paper, 'facts': strategy_facts(name, strat) if strat else None,
           'password_set': password_set(), 'confirm_words': CONFIRM,
           'autostart': bool(settings().get(name + '_autostart')), 'starting': bool(req) and not pid and not last,
           'last': last, 'live': live, 'record_position': _live_record(name, strat) if not pid else None,
           'log': _log_tail(name)}
    try:
        out['checklist'] = checklist(name)
    except Exception as e:
        out['checklist'] = {'error': str(e)[:160]}
    return out


def _jb(*args, timeout=150):
    """./jb live-service ... (replaced in tests). Returns (exit code, output)."""
    os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)
    r = subprocess.run(['/bin/bash', os.path.join(ROOT, 'jb'), 'live-service'] + [str(a) for a in args], cwd=ROOT,
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    out = (r.stdout or '') + (r.stderr or '')
    with open(os.path.join(ROOT, 'logs', 'health.log'), 'a') as f:
        f.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} live-service {' '.join(str(a) for a in args)} (algo tab)\n{out}\n")
    return r.returncode, out.strip()


def _err(code, msg):
    return code, json.dumps({'error': msg})


def start(name, data):
    if not password_set():
        return _err(403, 'Go Live only works with a board password: set JB_PASSWORD in settings.env, then ./jb restart gateway')
    if str(data.get('confirm') or '').strip() != CONFIRM:
        return _err(400, f'type {CONFIRM} exactly to go live')
    if _pid_alive(os.path.join(_logs(name), 'LIVE_RUNNING')):
        return _err(409, 'live trading for this broker is already running')
    strat = paper_strategy(name)
    if not strat:
        return _err(409, 'the paper algo has no strategy set (WEBULL_ALGO_STRATEGY / IBKR_ALGO_STRATEGY in settings.env)')
    f = strategy_facts(name, strat)
    if not f.get('exists'):
        return _err(409, f'the strategy file {strat} is missing')
    if f.get('error'):
        return _err(400, f"the strategy file {strat} can't be read: {f['error']}")
    if not f.get('confirm_live'):
        return _err(409, f'the strategy file {strat} does not say confirm_live: true. Add that line to the file yourself '
                         'when you mean this strategy to trade real money (live trading stays off until you do)')
    if not f.get('max_order_usd'):
        return _err(409, f'the strategy file {strat} has no risk: max_order_notional_usd (the most one order may be worth, '
                         'e.g. 2000): live trading needs it')
    import algoctl
    bad = algoctl._check_strategy(name, os.path.basename(strat))
    if bad:
        return _err(400, 'that file is not a valid strategy: ' + bad)
    auto = 1 if settings().get(name + '_autostart') else 0
    rc, out = _jb('start', name, strat, auto)
    if rc != 0:
        return _err(409, (out.splitlines() or ['could not start live trading'])[-1][:300])
    return 200, json.dumps(dict(state(name), started=True, message=out.splitlines()[-1] if out else ''))


def stop(name):
    lock = os.path.join(_logs(name), 'LIVE_RUNNING')
    if _pid_alive(lock) and not os.path.exists(lock + '.by'):
        return _err(409, f'live trading for {name} runs in a Terminal window (./jb live): stop it there with Ctrl+C')
    rc, out = _jb('stop', name)
    if rc != 0:
        return _err(409, (out.splitlines() or ['could not stop live trading'])[-1][:300])
    return 200, json.dumps(dict(state(name), stopped=True, message=out.splitlines()[0] if out else ''))


def autostart(name, on):
    v = settings()
    v[name + '_autostart'] = bool(on)
    _save_settings(v)
    if os.path.exists(os.path.join(_logs(name), 'LIVE_SERVICE')):
        _jb('autostart', name, 'on' if on else 'off', timeout=30)
    return 200, json.dumps(state(name))


def get(name):
    return 200, json.dumps(state(name))


def post(name, data):
    act = str(data.get('action') or '')
    with _lock:                         # one live start / stop at a time
        if act == 'start':
            return start(name, data)
        if act == 'stop':
            return stop(name)
        if act == 'autostart':
            if not password_set():
                return _err(403, 'set JB_PASSWORD in settings.env first')
            return autostart(name, data.get('on') is True)
    return _err(400, 'unknown action')


def summary():
    """Both brokers in one small answer, for the phone page and the dashboard's tab bar."""
    out = {}
    for name in _dirs():
        lock = os.path.join(_logs(name), 'LIVE_RUNNING')
        pid = _pid_alive(lock)
        st = _read_json(os.path.join(_logs(name), 'live_status.json'), {}) or {}
        out[name] = {'running': bool(pid), 'by': ('service' if os.path.exists(lock + '.by') else 'terminal') if pid else None,
                     'symbol': st.get('symbol') if pid else None, 'position': st.get('position') if pid and st.get('mode') == 'live' else None,
                     'halted': st.get('halted') if pid else None}
    return 200, json.dumps(out)
