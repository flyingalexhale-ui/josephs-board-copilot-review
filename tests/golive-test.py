"""Go Live / Stop Live (jb-golive), end to end without a Mac: the real ./jb live-service, bin/run-service.sh and
gateway/livectl.py, in a temporary copy of the board, with a fake launchctl (it starts and stops the jobs the way
launchd does: one supervisor per job, SIGTERM on bootout) and a fake .venv/bin/python whose "trader" only records what it
was asked to do. Nothing here talks to a broker or to ntfy.

Checks: the page's refusals (password, the typed words, confirm_live, the order cap, a live run already going);
Go Live starts the live job with the paper algo's strategy and the second switch for that job only, stops the paper
algo, sets logs/LIVE_RUNNING; a second Go Live is refused; Stop Live stops it cleanly (the trader gets SIGTERM, no
LIVE_EXITED push, the paper algo starts again, the live position shown from the live record); a crash and a start-up
refusal (leftover order or hedge) end it with the right record and push; after a Mac restart it does NOT start again
unless the option is on; ./jb live in Terminal blocks Go Live and Stop Live says to use Ctrl+C there; the guardian's
pushes name Go Live; the paper algo waits while live runs.

usage (from the josephs-board folder):   python3 tests/golive-test.py        (exit code 1 if a check fails)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix='jb-golive-test-')
REC = os.path.join(T, 'rec')
FAKEBIN = os.path.join(T, 'fakebin')
RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  -- {detail}' if detail else ''), flush=True)


# ---------------------------------------------------------------- a temporary board
for sub in ('bin', 'lib', 'gateway'):
    shutil.copytree(os.path.join(ROOT, sub), os.path.join(T, sub), ignore=shutil.ignore_patterns('__pycache__'))
for algo in ('webull-algo', 'ibkr-algo'):
    shutil.copytree(os.path.join(ROOT, algo), os.path.join(T, algo), ignore=shutil.ignore_patterns('__pycache__', 'logs', '*.csv', '*.png'))
    os.makedirs(os.path.join(T, algo, 'logs'), exist_ok=True)
shutil.copy(os.path.join(ROOT, 'jb'), os.path.join(T, 'jb'))
for d in (REC, FAKEBIN, os.path.join(T, 'data'), os.path.join(T, 'logs'), os.path.join(T, 'home', 'Library', 'LaunchAgents'), os.path.join(T, '.venv', 'bin')):
    os.makedirs(d, exist_ok=True)
with open(os.path.join(T, 'settings.env'), 'w') as f:
    f.write('RUN_WEBULL_ALGO=on\nRUN_IBKR_ALGO=off\nWEBULL_ALGO_STRATEGY=strategies/golive_t.yaml\nJB_NTFY_TOPIC=\n')
STRAT = """name: "Go Live test"
symbol: "TQQQ"
indicators: []
entry: {side: buy, when: "close > 0"}
exit: {when: "close < 0"}
position_size: {pct_of_equity: 10}
risk: {max_order_notional_usd: 2000}
confirm_live: %s
"""
STRAT_FILE = os.path.join(T, 'webull-algo', 'strategies', 'golive_t.yaml')
with open(STRAT_FILE, 'w') as f:
    f.write(STRAT % 'true')

# the fake python: "-m algo.paper_trader" records its arguments and runs until SIGTERM; rec/behaviour = refuse | crash
PY = os.path.join(T, '.venv', 'bin', 'python')
with open(PY, 'w') as f:
    f.write(r'''#!/bin/bash
case "$1" in
  -c) exit 0 ;;
  -) exit 1 ;;
  -m)
    shift; mode=paper; strat=""
    while [ $# -gt 0 ]; do case "$1" in --mode) mode="$2"; shift;; --strategy) strat="$2"; shift;; esac; shift; done
    rec="$JB_TEST_REC/trader-$mode.txt"
    echo "start strategy=$strat confirm=${WEBULL_LIVE_TRADING_CONFIRM}|${IBKR_LIVE_TRADING_CONFIRM} pid=$$ dir=$(basename "$PWD")" >> "$rec"
    if [ "$mode" = live ]; then
      case "$(cat "$JB_TEST_REC/behaviour" 2>/dev/null)" in
        refuse) echo "LIVE TRADING NOT STARTED: an Auto-Hedge order is still open at Webull: BUY 300 SQQQ."; exit 3 ;;
        crash) sleep 1; echo "Traceback: something broke"; exit 1 ;;
      esac
    fi
    trap 'echo "stopped cleanly" >> "$rec"; exit 0' TERM INT
    while true; do sleep 0.2; done ;;
esac
exit 0
''')
os.chmod(PY, 0o755)

# the fake launchctl: list / bootstrap / bootout, one supervisor process per job (it reaps the job, as launchd does)
RUNNER = os.path.join(FAKEBIN, 'fake_launchd_job.py')
with open(RUNNER, 'w') as f:
    f.write(r'''import fcntl, json, os, subprocess, sys
state, label, cwd, prog = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
def update(fn):
    with open(state + '.lock', 'a') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            d = json.load(open(state))
        except Exception:
            d = {}
        fn(d)
        json.dump(d, open(state + '.tmp', 'w')); os.replace(state + '.tmp', state)
p = subprocess.Popen(prog, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
update(lambda d: d.__setitem__(label, {'pid': p.pid, 'exited': False}))
rc = p.wait()
def done(d):
    if d.get(label, {}).get('pid') == p.pid:
        d[label].update(exited=True, rc=rc)
update(done)
''')
LCTL = os.path.join(FAKEBIN, 'launchctl')
with open(LCTL, 'w') as f:
    f.write(r'''#!/usr/bin/env python3
import fcntl, json, os, re, signal, subprocess, sys, time
REC = os.environ['JB_TEST_REC']; STATE = os.path.join(REC, 'launchd.json')
def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}
def drop(label):
    with open(STATE + '.lock', 'a') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = load(); d.pop(label, None)
        json.dump(d, open(STATE + '.tmp', 'w')); os.replace(STATE + '.tmp', STATE)
a = sys.argv[1:]
open(os.path.join(REC, 'launchctl-calls.txt'), 'a').write(' '.join(a) + '\n')
if a and a[0] == 'list':
    print('PID\tStatus\tLabel')
    for label, j in load().items():
        print(f"{'-' if j.get('exited') else j.get('pid')}\t{j.get('rc', 0)}\t{label}")
elif a and a[0] == 'bootstrap':
    x = open(a[2]).read()
    label = re.search(r'<key>Label</key><string>([^<]+)', x).group(1)
    if label in load():
        print('Bootstrap failed: 37: Operation already in progress'); sys.exit(37)
    prog = re.findall(r'<string>([^<]+)</string>', re.search(r'<array>(.*?)</array>', x, re.S).group(1))
    cwd = re.search(r'<key>WorkingDirectory</key><string>([^<]+)', x).group(1)
    subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fake_launchd_job.py'), STATE, label, cwd] + prog,
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        if label in load():
            break
        time.sleep(0.05)
elif a and a[0] == 'bootout':
    label = a[1].split('/')[-1]
    j = load().get(label)
    if not j:
        sys.exit(3)
    if not j.get('exited'):
        try:
            os.kill(j['pid'], signal.SIGTERM)
        except OSError:
            pass
        for _ in range(500):
            if (load().get(label) or {}).get('exited'):
                break
            time.sleep(0.1)
    drop(label)
''')
os.chmod(LCTL, 0o755)

os.environ.update(JB_TEST_REC=REC, HOME=os.path.join(T, 'home'), PATH=FAKEBIN + os.pathsep + os.environ.get('PATH', ''),
                  JB_DATA_DIR=os.path.join(T, 'data'), JB_NTFY_TOPIC='', WEBULL_ALGO_STRATEGY='strategies/golive_t.yaml')
os.environ.pop('JB_PASSWORD', None)
LOGS = os.path.join(T, 'webull-algo', 'logs')
LA = os.path.join(T, 'home', 'Library', 'LaunchAgents', 'com.josephsboard.webull-live.plist')


def jb(*args, timeout=120):
    r = subprocess.run(['/bin/bash', os.path.join(T, 'jb')] + list(args), cwd=T, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


def launchd():
    try:
        return json.load(open(os.path.join(REC, 'launchd.json')))
    except Exception:
        return {}


def wait_for(fn, secs=20):
    t0 = time.time()
    while time.time() - t0 < secs:
        if fn():
            return True
        time.sleep(0.2)
    return False


def alive_pid():
    try:
        pid = int(open(os.path.join(LOGS, 'LIVE_RUNNING')).read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def last():
    try:
        return json.load(open(os.path.join(LOGS, 'LIVE_LAST.json')))
    except Exception:
        return {}


def rec(mode):
    try:
        return open(os.path.join(REC, f'trader-{mode}.txt')).read()
    except OSError:
        return ''


def behaviour(b):
    with open(os.path.join(REC, 'behaviour'), 'w') as f:
        f.write(b)


def fresh():
    for p in ('.starts-webull-algo', '.starts-webull-live'):
        try:
            os.remove(os.path.join(T, 'logs', p))
        except OSError:
            pass


# ---------------------------------------------------------------- the page's refusals (gateway/livectl.py)
sys.path.insert(0, os.path.join(T, 'gateway'))
sys.path.insert(0, os.path.join(T, 'lib'))
import algoctl   # noqa: E402
import livectl   # noqa: E402
import guardian  # noqa: E402
import watch     # noqa: E402
PUSHES = []
watch.push_later = lambda title, text, tags='': PUSHES.append((title, text))
algoctl.DIRS.update({'webull': os.path.join(T, 'webull-algo'), 'ibkr': os.path.join(T, 'ibkr-algo')})
guardian.ALGOS.update(algoctl.DIRS)


def post(data):
    code, body = livectl.post('webull', data)
    return code, json.loads(body)


try:
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    check('Go Live: refused without a board password (JB_PASSWORD)', code == 403 and 'JB_PASSWORD' in b.get('error', ''), b.get('error'))
    os.environ['JB_PASSWORD'] = 'testpw'
    code, b = post({'action': 'start', 'confirm': 'i understand the risk'})
    check('Go Live: refused unless I_UNDERSTAND_THE_RISK is typed exactly', code == 400 and 'I_UNDERSTAND_THE_RISK' in b.get('error', ''), b.get('error'))
    with open(STRAT_FILE, 'w') as f:
        f.write(STRAT % 'false')
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    check('Go Live: refused while the paper algo\'s strategy file lacks confirm_live: true (says so)', code == 409 and 'confirm_live: true' in b.get('error', ''),
          b.get('error', '')[:100])
    with open(STRAT_FILE, 'w') as f:
        f.write((STRAT % 'true').replace('risk: {max_order_notional_usd: 2000}', 'risk: {}'))
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    check('Go Live: refused without risk: max_order_notional_usd', code == 409 and 'max_order_notional_usd' in b.get('error', ''))
    with open(STRAT_FILE, 'w') as f:
        f.write(STRAT % 'true')
    code, s = livectl.get('webull')
    s = json.loads(s)
    import importlib.util
    spec = importlib.util.spec_from_file_location('golive_bin', os.path.join(ROOT, 'bin', 'golive.py'))
    gb = importlib.util.module_from_spec(spec); spec.loader.exec_module(gb)
    same = gb.checklist('webull', T)
    check('Go Live card: shows the go-live checklist (the same checks as bin/golive.py / ./jb live)',
          s['checklist'].get('items') and [i['name'] for i in s['checklist']['items']] == [i['name'] for i in same['items']]
          and s['facts'].get('confirm_live') is True and s['strategy'] == 'strategies/golive_t.yaml' and s['password_set'],
          f"{s['checklist'].get('passed')} of {s['checklist'].get('total')}")

    # ---------------------------------------------------------------- Go Live (the real ./jb live-service + run-service.sh)
    fresh()
    rc, out = jb('restart', 'webull-algo')
    paper_up = wait_for(lambda: 'com.josephsboard.webull-algo' in launchd() and 'start' in rec('paper'))
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    up = wait_for(lambda: alive_pid() and 'start' in rec('live'))
    req = open(os.path.join(LOGS, 'LIVE_SERVICE')).read() if os.path.exists(os.path.join(LOGS, 'LIVE_SERVICE')) else ''
    check('Go Live: starts the live job in the background with the paper algo\'s strategy (launchd, like the other services)',
          code == 200 and up and 'strategy=strategies/golive_t.yaml' in rec('live') and 'com.josephsboard.webull-live' in launchd(),
          (b.get('error') or b.get('message') or '')[:100])
    check('Go Live: the second switch (WEBULL_LIVE_TRADING_CONFIRM) is set for the live job only',
          'confirm=I_UNDERSTAND_THE_RISK|' in rec('live') and 'confirm=|' in rec('paper') and 'confirm=I_UNDERSTAND_THE_RISK' in req)
    check('Go Live: logs/LIVE_RUNNING holds the live job (the guardian watches it), marked as Go Live',
          alive_pid() is not None and os.path.exists(os.path.join(LOGS, 'LIVE_RUNNING.by')))
    check('Go Live: the paper algo is stopped while live runs', paper_up and 'com.josephsboard.webull-algo' not in launchd())
    check('Go Live: the live job is NOT in ~/Library/LaunchAgents (so a Mac restart does not start it again)',
          not os.path.exists(LA) and os.path.exists(os.path.join(T, 'logs', 'launchd', 'com.josephsboard.webull-live.plist')))
    plist = open(os.path.join(T, 'logs', 'launchd', 'com.josephsboard.webull-live.plist')).read()
    check('Go Live: launchd does not restart the live job by itself (KeepAlive off)', '<key>KeepAlive</key><false/>' in plist)
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    check('Go Live a second time while it runs: refused', code == 409 and 'already running' in b.get('error', ''), b.get('error'))
    s = json.loads(livectl.get('webull')[1])
    sm = json.loads(livectl.summary()[1])
    check('LIVE shown: the card says running (Go Live) and /api/algo/live says webull is live (tab badge, phone banner)',
          s['running'] and s['by'] == 'service' and sm['webull']['running'] and not sm['ibkr']['running'])
    rc, out = jb('status')
    check('./jb status lists the live job', 'webull-live' in out and 'Go Live' in out, out.strip().splitlines()[-8:-7])
    # the paper algo started while live runs (e.g. ./jb restart): it waits
    fresh()
    jb('restart', 'webull-algo')
    time.sleep(1.5)
    log = open(os.path.join(T, 'logs', 'webull-algo.log')).read()
    check('while live runs, a paper algo start waits (does not trade beside it)', 'live trading is running (./jb live or Go Live)' in log)
    subprocess.run([LCTL, 'bootout', 'gui/0/com.josephsboard.webull-algo'], capture_output=True)

    # put a live position in the live record, then Stop Live
    json.dump({'symbol': 'TQQQ', 'strategy_name': 'Go Live test', 'mode': 'live', 'starting_cash': 100000.0,
               'portfolio': {'position': {'side': 'long', 'shares': 20, 'entry_price': 50.5, 'entry_time': '2026-10-12T14:00:00+00:00'}}},
              open(os.path.join(LOGS, 'state_golive_t_live.json'), 'w'))
    fresh()
    code, b = post({'action': 'stop'})
    check('Stop Live: the live job ends cleanly (the trader gets SIGTERM, like Ctrl+C: orders working are cancelled, none sent)',
          code == 200 and alive_pid() is None and 'stopped cleanly' in rec('live') and last().get('how') == 'stopped', str(b.get('error') or last()))
    check('Stop Live: no "stopped on its own" push, the Go Live request is cleared', not os.path.exists(os.path.join(LOGS, 'LIVE_EXITED'))
          and not os.path.exists(os.path.join(LOGS, 'LIVE_SERVICE')) and not os.path.exists(os.path.join(LOGS, 'LIVE_RUNNING')))
    check('Stop Live: the paper algo starts again', wait_for(lambda: 'com.josephsboard.webull-algo' in launchd()))
    s = json.loads(livectl.get('webull')[1])
    check('Stop Live: the position stays and is shown (from the live record)', not s['running'] and (s.get('record_position') or {}).get('shares') == 20
          and s['last']['how'] == 'stopped', str(s.get('record_position')))

    # ---------------------------------------------------------------- stops on its own / refuses at start-up
    behaviour('crash'); PUSHES.clear(); fresh()
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    ended = wait_for(lambda: last().get('how') == 'exited' and alive_pid() is None, 25)
    check('live job stops on its own (error): LIVE_RUNNING cleared, LIVE_EXITED written for the guardian\'s push',
          code == 200 and ended and os.path.exists(os.path.join(LOGS, 'LIVE_EXITED')) and not os.path.exists(os.path.join(LOGS, 'LIVE_SERVICE')), str(last()))
    guardian._live_exited('webull')
    check('...the guardian pushes the phone once, naming Go Live', len(PUSHES) == 1 and 'LIVE trader stopped' in PUSHES[0][0]
          and 'Go Live' in PUSHES[0][1], str(PUSHES)[:160])
    check('...and the paper algo starts again', wait_for(lambda: 'com.josephsboard.webull-algo' in launchd(), 25))
    behaviour('refuse'); PUSHES.clear(); fresh()
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    ended = wait_for(lambda: last().get('how') == 'refused', 25)
    s = json.loads(livectl.get('webull')[1])
    check('leftover order / hedge at the broker: the live job refuses to start and the card shows why (no push: you are on the page)',
          ended and not os.path.exists(os.path.join(LOGS, 'LIVE_EXITED')) and any('NOT STARTED' in l for l in s['log']) and not s['running'],
          str(s['log'][-2:])[:160])
    wait_for(lambda: 'com.josephsboard.webull-algo' in launchd(), 25)
    behaviour('run')

    # ---------------------------------------------------------------- after a Mac restart
    fresh()
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    wait_for(lambda: alive_pid())
    subprocess.run([LCTL, 'bootout', 'gui/0/com.josephsboard.webull-live'], capture_output=True)    # macOS shutting down
    wait_for(lambda: alive_pid() is None)
    check('Mac restart, option off (default): live is stopped cleanly and nothing makes it start again at login',
          last().get('how') == 'shutdown' and not os.path.exists(LA) and 'com.josephsboard.webull-live' not in launchd())
    code, b = post({'action': 'stop'})                                 # tidy up the leftover request
    code, b = post({'action': 'autostart', 'on': True})
    check('the option "Start Live Trading Again After A Mac Restart" is saved (default off)', code == 200 and b['autostart'] is True)
    fresh()
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    wait_for(lambda: alive_pid())
    check('option on: the live job is also in ~/Library/LaunchAgents (launchd starts it at login)', os.path.exists(LA))
    subprocess.run([LCTL, 'bootout', 'gui/0/com.josephsboard.webull-live'], capture_output=True)    # macOS shutting down
    wait_for(lambda: alive_pid() is None)
    n0 = rec('live').count('start ')
    subprocess.run([LCTL, 'bootstrap', 'gui/0', LA], capture_output=True)                          # login: launchd loads it
    back = wait_for(lambda: alive_pid() and rec('live').count('start ') == n0 + 1)
    check('option on: after a Mac restart live trading starts again by itself (the request was kept)', back and last().get('how') == 'shutdown')
    fresh()
    code, b = post({'action': 'stop'})
    check('Stop Live with the option on: the LaunchAgents copy is removed too (no start at the next login)', code == 200 and not os.path.exists(LA)
          and alive_pid() is None)
    post({'action': 'autostart', 'on': False})

    # ---------------------------------------------------------------- ./jb live in a Terminal window
    open(os.path.join(LOGS, 'LIVE_RUNNING'), 'w').write(str(os.getpid()))
    code, b = post({'action': 'start', 'confirm': 'I_UNDERSTAND_THE_RISK'})
    code2, b2 = post({'action': 'stop'})
    check('./jb live running in Terminal: Go Live refused, Stop Live says to use Ctrl+C there',
          code == 409 and code2 == 409 and 'Ctrl+C' in b2.get('error', ''), b2.get('error'))
    s = json.loads(livectl.get('webull')[1])
    check('...and the card shows it as live (./jb live in a Terminal window)', s['running'] and s['by'] == 'terminal')
    os.remove(os.path.join(LOGS, 'LIVE_RUNNING'))

    # ---------------------------------------------------------------- the guardian's watch of a Go Live job
    PUSHES.clear()
    open(os.path.join(LOGS, 'LIVE_RUNNING'), 'w').write('999999')
    open(os.path.join(LOGS, 'LIVE_RUNNING.by'), 'w').write('service')
    guardian._step_live(guardian.settings(), {}, 'webull', os.path.join(LOGS, 'LIVE_RUNNING'))
    check('guardian: a Go Live job that is gone (crash, power cut) -> one loud push naming its background job and Go Live',
          len(PUSHES) == 1 and 'NOT running' in PUSHES[0][0] and 'background job' in PUSHES[0][1] and 'Go Live' in PUSHES[0][1], str(PUSHES)[:160])
    for p in ('LIVE_RUNNING', 'LIVE_RUNNING.by'):
        os.remove(os.path.join(LOGS, p))

    # ---------------------------------------------------------------- the REAL Webull trader (fake Webull SDK): SIGTERM and code 3
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.join(ROOT, 'tests', 'fake-brokers', 'pkgs'), os.path.join(T, 'lib')]),
               JB_FAKE_BROKERS=os.path.join(ROOT, 'tests', 'fake-brokers'), JB_FAKE_LOGDIR=REC, WEBULL_APP_KEY='FAKEKEY',
               WEBULL_APP_SECRET='FAKESECRET', WEBULL_OPENAPI_TOKEN_DIR=os.path.join(T, 'data', 'webull-token'), PYTHONUNBUFFERED='1')
    os.makedirs(env['WEBULL_OPENAPI_TOKEN_DIR'], exist_ok=True)
    with open(os.path.join(env['WEBULL_OPENAPI_TOKEN_DIR'], 'token.txt'), 'w') as f:
        f.write('faketoken\n%d\nNORMAL\n' % ((time.time() + 30 * 86400) * 1000))
    env.pop('WEBULL_LIVE_TRADING_CONFIRM', None)
    tlog = os.path.join(REC, 'real-trader.log')
    p = subprocess.Popen([sys.executable, '-m', 'algo.paper_trader', '--strategy', 'strategies/golive_t.yaml', '--mode', 'paper'],
                         cwd=os.path.join(T, 'webull-algo'), env=env, stdout=open(tlog, 'w'), stderr=subprocess.STDOUT)
    running = wait_for(lambda: 'trader running' in open(tlog).read(), 40)
    p.send_signal(15)
    try:
        rc = p.wait(20)
    except subprocess.TimeoutExpired:
        p.kill(); rc = 'hung'
    check('the real Webull trader stops cleanly on SIGTERM (what Stop Live sends): exit code 0, state saved',
          running and rc == 0 and os.path.exists(os.path.join(LOGS, 'state_golive_t_paper.json')), f'rc {rc}; ' + open(tlog).read()[-160:])
    r = subprocess.run([sys.executable, '-m', 'algo.paper_trader', '--strategy', 'strategies/golive_t.yaml', '--mode', 'live'],
                       cwd=os.path.join(T, 'webull-algo'), env=env, capture_output=True, text=True, timeout=60)
    check('the real trader refusing to go live (here: the second switch not set) exits with code 3 and says why',
          r.returncode == 3 and 'LIVE TRADING NOT STARTED' in (r.stdout + r.stderr), f'rc {r.returncode}')
finally:
    for label, j in launchd().items():                 # stop anything still running
        try:
            os.kill(j['pid'], 15)
        except Exception:
            pass
    time.sleep(0.5)
    shutil.rmtree(T, ignore_errors=True)

bad = [r for r in RESULTS if not r[1]]
print(f'\nGo Live tests: {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')
sys.exit(1 if bad else 0)
