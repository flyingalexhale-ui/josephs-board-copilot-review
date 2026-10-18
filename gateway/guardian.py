"""Reliability helpers from the final-ideas list (jb-guardian), running inside the gateway.

  System check rows: Mac restarts by itself after a power cut (idea 23), memory and clock (27),
     macOS installing updates by itself (44), crash loops (41), backups (26), algo heartbeats (25)
  algo heartbeat push: an algo that stops checking in for 3 minutes                              (idea 25)
  nightly backup at 2:30 am New York, last 7 kept, optional iCloud Drive copy                     (idea 26)
  early loss-limit warnings at 50% and 75% of your daily loss limit                               (idea 39)
  confirmed SELL ALL: re-checks after 60 s and re-sells (closer to the market) what is left       (idea 40)
  crash-loop guard: a service restarted 5+ times in 10 minutes is held back (bin/run-service.sh)  (idea 41)
  internet outage log (shown on Health), push when back after 2+ minutes                          (idea 42)
  log trimming: every log kept under 20 MB and old rolled-over logs deleted after 7 days          (idea 43)
  Sunday 5 pm New York self-test push                                                              (idea 45)
  Saturday 7 am quiet-time refresh: services restarted one at a time, then a self-test push        (idea 57)
  (urgent pushes, idea 55, are in watch.push; the algos' own checks, ideas 24 and 36-38, are in
   algo/core_trader.py)

  GET  /api/plus/guardian              status, outages, backups, settings
  POST /api/plus/guardian/settings     {heartbeat_push, early_loss, confirm_sellall, backup, icloud, sunday_test, saturday_refresh}
  POST /api/plus/guardian/backup       make a backup now
Files: data/guardian.json, data/guardian_settings.json, data/outages.json, backups/ (in ~/josephs-board).
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import tarfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
STATE = os.path.join(DATA, 'guardian.json')
SETTINGS = os.path.join(DATA, 'guardian_settings.json')
OUTAGES = os.path.join(DATA, 'outages.json')
BACKUPS = os.path.join(ROOT, 'backups')
LOGS = os.path.join(ROOT, 'logs')
ALGOS = {'webull': os.path.join(ROOT, 'webull-algo'), 'ibkr': os.path.join(ROOT, 'ibkr-algo')}
ICLOUD = os.path.expanduser('~/Library/Mobile Documents/com~apple~CloudDocs/AlexHaleBoard-backups')
REFRESH_ORDER = ('scanner', 'webull', 'ibkr', 'webull-algo', 'ibkr-algo', 'keepawake')
DEFAULTS = {'heartbeat_push': True, 'early_loss': True, 'confirm_sellall': True, 'backup': True, 'icloud': False,
            'sunday_test': True, 'saturday_refresh': True}
_st = {'started': False, 'net': {'up': True, 'since': 0, 'fails': 0}, 'sell': {}, 'busy': ''}
_lock = threading.Lock()


def _now():
    return int(time.time() * 1000)


def settings():
    v = watch._read(SETTINGS, {})
    return dict(DEFAULTS, **(v if isinstance(v, dict) else {}))


def _state():
    v = watch._read(STATE, {})
    return v if isinstance(v, dict) else {}


def _put(**kw):
    with _lock:
        s = _state()
        s.update(kw)
        watch._write(STATE, s)


def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ''


def _item(name, state, detail='', fix=''):
    return {'name': name, 'state': state, 'detail': detail, 'fix': fix}


def _service_on(name):
    key = {'webull-algo': 'RUN_WEBULL_ALGO', 'ibkr-algo': 'RUN_IBKR_ALGO', 'webull': 'RUN_WEBULL_BRIDGE', 'ibkr': 'RUN_IBKR_BRIDGE',
           'scanner': 'RUN_SCANNER'}.get(name)
    return not key or os.environ.get(key, 'on').strip().lower() not in ('off', 'no', 'false', '0')


# ------------------------------------------------------------------ System check rows (23, 25, 26, 27, 41, 44)
def check_autorestart():
    out = _run(['pmset', '-g'])
    if not out:
        return _item('Turns back on after a power cut', 'info', 'could not read the Mac\'s power settings')
    on = any(l.split()[:2] == ['autorestart', '1'] for l in out.splitlines())
    return _item('Turns back on after a power cut', 'ok' if on else 'warn', 'on: after a power cut the Mac starts again by itself, and the board with it' if on else
                 'off: after a power cut the Mac stays off until someone presses the power button',
                 '' if on else 'In Terminal on the Mac: sudo pmset -a autorestart 1  (type your Mac password when asked; nothing shows while you type).')


def check_memory():
    vm = _run(['vm_stat'])
    try:
        size = int(_run(['sysctl', '-n', 'hw.memsize']).strip())
        page = 4096
        pages = {}
        for l in vm.splitlines():
            if 'page size of' in l:
                page = int(l.split('page size of')[1].split()[0])
            elif ':' in l:
                k, v = l.split(':', 1)
                try:
                    pages[k.strip()] = int(v.strip().rstrip('.'))
                except ValueError:
                    pass
        free = (pages.get('Pages free', 0) + pages.get('Pages inactive', 0) + pages.get('Pages speculative', 0) + pages.get('Pages purgeable', 0)) * page
        swap = _run(['sysctl', '-n', 'vm.swapusage'])
        used_swap = 0.0
        if 'used =' in swap:
            v = swap.split('used =')[1].split()[0]
            used_swap = float(v[:-1]) / (1024 if v.endswith('M') else 1) if v[-1] in 'MG' else 0
        pct = free / size * 100
        st = 'bad' if pct < 4 and used_swap > 3 else 'warn' if pct < 8 or used_swap > 4 else 'ok'
        return _item('Mac memory', st, f'{free / 1e9:.1f} GB of {size / 1e9:.0f} GB free or reusable' + (f', {used_swap:.1f} GB swap in use' if used_swap else ''),
                     '' if st == 'ok' else 'Quit apps you are not using on the Mac (browser tabs especially), or restart the Mac at a quiet time.')
    except Exception:
        return _item('Mac memory', 'info', 'could not read')


def clock_drift():
    """Seconds the Mac's clock is off, from sntp (macOS) or an HTTP Date header (1 s steps)."""
    out = _run(['sntp', '-t', '5', 'time.apple.com'], timeout=12)
    for tok in out.replace('+/-', ' ').split():
        try:
            v = float(tok)
            if abs(v) < 86400 and ('.' in tok):
                return v
        except ValueError:
            continue
    try:
        t0 = time.time()
        with urllib.request.urlopen(urllib.request.Request('https://www.apple.com/', method='HEAD'), timeout=8, context=watch._ctx()) as r:
            d = r.headers.get('Date')
        t1 = time.time()
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(d).timestamp() - (t0 + t1) / 2
    except Exception:
        return None


def check_clock():
    s = _state()
    d, at = s.get('drift'), s.get('drift_at', 0)
    if d is None:
        return _item('Mac clock', 'info', 'not checked yet')
    st = 'bad' if abs(d) > 30 else 'warn' if abs(d) > 2 else 'ok'
    return _item('Mac clock', st, f'{abs(d):.1f} s {"ahead" if d < 0 else "behind"} the real time' if abs(d) >= 0.5 else 'right on time',
                 '' if st == 'ok' else 'System Settings > General > Date & Time: switch on "Set time and date automatically". Session times depend on the clock.')


def check_updates():
    out = _run(['defaults', 'read', '/Library/Preferences/com.apple.SoftwareUpdate', 'AutomaticallyInstallMacOSUpdates']).strip()
    out2 = _run(['defaults', 'read', '/Library/Preferences/com.apple.commerce', 'AutoUpdateRestartRequired']).strip()
    if out == '1' or out2 == '1':
        return _item('macOS updates', 'warn', 'set to install updates by itself, which can restart the Mac overnight',
                     'System Settings (or System Preferences) > Software Update > Advanced (or Automatic Updates): untick "Install macOS updates". '
                     'Keep "Install security responses" if you like. Then update by hand on a weekend.')
    return _item('macOS updates', 'ok', 'will not restart the Mac by itself for an update' if out in ('0', '') else out)


def check_crashloops():
    out = []
    for f in glob.glob(os.path.join(LOGS, '.crashloop-*')):
        name = os.path.basename(f)[11:]
        if time.time() - os.path.getmtime(f) > 3600:
            continue
        try:
            last = open(f).read().strip()[-200:]
        except OSError:
            last = ''
        out.append(_item(f'{name}: restarting over and over', 'bad', f'held back for an hour after 5+ restarts in 10 minutes. Last line: {last}',
                         f'In Terminal: cd ~/josephs-board && ./jb logs {name} (last lines show the error), then ./jb restart {name}.'))
    return out


def heartbeat_age(name):
    p = os.path.join(ALGOS[name], 'logs', 'heartbeat.json')
    try:
        return time.time() - float(json.load(open(p)).get('ts') or 0)
    except Exception:
        return None


def check_heartbeats():
    out = []
    for name in ALGOS:
        if not os.path.isdir(ALGOS[name]) or not _service_on(name + '-algo'):
            continue
        a = heartbeat_age(name)
        if a is None:
            out.append(_item(f'{name.upper()} algo heartbeat', 'info', 'no heartbeat yet (starts after this install restarts the algo)'))
        else:
            out.append(_item(f'{name.upper()} algo heartbeat', 'ok' if a < 180 else 'bad', f'last check-in {int(a)} s ago',
                             '' if a < 180 else f'Health tab: Restart {name}-algo, or ./jb logs {name}-algo for the error.'))
    return out


def check_backup():
    lst = sorted(glob.glob(os.path.join(BACKUPS, 'board-*.tar.gz')))
    if not settings()['backup']:
        return _item('Nightly backup', 'info', 'switched off')
    if not lst:
        return _item('Nightly backup', 'info', 'none yet (first one tonight at 2:30 am)')
    age = (time.time() - os.path.getmtime(lst[-1])) / 3600
    return _item('Nightly backup', 'ok' if age < 30 else 'warn', f'{len(lst)} kept, newest {age:.0f} h ago ({os.path.getsize(lst[-1]) / 1e6:.1f} MB)',
                 '' if age < 30 else 'Check the gateway log; the backup runs at 2:30 am New York time.')


# ------------------------------------------------------------------ battery (jb-power)
_batt = {'on': False, 'lvl': 100, 'seen': 0}


def battery():
    """{'battery': True when running on battery, 'pct': charge %} from pmset, or None."""
    import re
    out = _run(['pmset', '-g', 'batt'])
    if not out:
        return None
    m = re.search(r'(\d+)%', out)
    return {'battery': 'Battery Power' in out, 'pct': int(m.group(1)) if m else None}


def check_battery():
    b = battery()
    if not b:
        return _item('Charger plugged in', 'info', 'could not read the battery')
    pct = f"{b['pct']}%" if b['pct'] is not None else ''
    if b['battery']:
        return _item('Charger plugged in', 'warn', f'no: running on battery ({pct} left); the Mac sleeps when it runs out and the board goes dark',
                     'Plug the charger into the Mac. The battery icon at the top of the screen should then say Power Adapter.')
    return _item('Charger plugged in', 'ok', f'yes ({pct} charged)' if pct else 'yes')


def _step_battery():
    b = battery()
    if not b:
        return
    p = b['pct']
    if b['battery']:
        _batt['seen'] += 1
        if _batt['seen'] < 2:                       # two readings in a row (1 minute), so a wiggled plug does not push
            return
        lvl = 10 if p is not None and p <= 10 else 20 if p is not None and p <= 20 else 100
        if not _batt['on'] or lvl < _batt['lvl']:
            first = not _batt['on']
            _batt.update(on=True, lvl=lvl)
            left = f' ({p}%)' if p is not None else ''
            watch.push_later(f'Mac is on battery{left}, plug it in' if first else f'Mac battery down to {p}%, plug it in',
                             'The Mac is not on its charger. When the battery runs out it sleeps: the board, alerts and algos stop until it is charged.',
                             'warning' if lvl == 100 else 'rotating_light')
    else:
        _batt['seen'] = 0
        if _batt['on']:
            _batt.update(on=False, lvl=100)
            watch.push_later('Mac is charging again', 'The charger is back in. Nothing else to do.', 'white_check_mark')


def checks():
    out = [check_battery(), check_autorestart(), check_memory(), check_clock(), check_updates(), check_backup()] + check_heartbeats() + check_crashloops()
    o = outages()[:1]
    if o and o[0].get('end') and _now() - o[0]['end'] < 24 * 3600000:
        out.append(_item('Internet', 'ok', f"last drop {watch.et(o[0]['start']):%a %I:%M %p}, {o[0]['mins']} min (list on Health)"))
    return out


# ------------------------------------------------------------------ heartbeat push (25)
def _step_heartbeats(cfg, s):
    hb = s.setdefault('hb', {})
    for name in ALGOS:
        if not os.path.isdir(ALGOS[name]):
            continue
        _live_exited(name)
        lock = os.path.join(ALGOS[name], 'logs', 'LIVE_RUNNING')
        if os.path.exists(lock):
            _step_live(cfg, hb, name, lock)        # review 2026-10-11 #4: the live trader is always watched
            continue
        hb.pop('live_' + name, None)
        if not _service_on(name + '-algo'):
            continue
        a = heartbeat_age(name)
        if a is None:
            continue
        if a > 180 and not hb.get(name):
            hb[name] = _now()
            if cfg['heartbeat_push'] and not os.path.exists(lock):
                watch.push_later(f'{name.upper()} algo stopped checking in', f'The {name} algo has not checked in for {int(a / 60)} minutes. Health tab: Restart {name}-algo.', 'rotating_light')
        elif a < 90 and hb.get(name):
            down = (_now() - hb.pop(name)) // 60000
            if cfg['heartbeat_push']:
                watch.push_later(f'{name.upper()} algo is back', f'The {name} algo is checking in again (it was quiet about {down + 3} minutes).', 'white_check_mark')


def _live_pid_alive(lock):
    try:
        pid = int(open(lock).read().strip())
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def _live_exited(name):
    """./jb live (and the Go Live job, jb-golive) writes logs/LIVE_EXITED when the live trader stops with an error on its
    own (LIVE_RUNNING is then removed, so _step_live never sees it): one loud push, always on."""
    p = os.path.join(ALGOS[name], 'logs', 'LIVE_EXITED')
    if not os.path.exists(p):
        return
    try:
        d = json.load(open(p)) or {}
    except Exception:
        d = {}
    rc, how = d.get('rc'), d.get('how')
    try:
        os.remove(p)
    except OSError:
        pass
    if how == 'service' and rc == 3:               # the Go Live job refused to start (e.g. after a Mac restart)
        watch.push_later(f'{name.upper()} LIVE trading did not start', f'The {name} live trader (Go Live) refused to start: '
                         'its start-up check found something to fix (an order still open, or the position differs from its '
                         'record). The Go Live card on the algo tab says what. No live orders are being sent.', 'rotating_light')
        return
    where = ('The Go Live card on the algo tab shows why' if how == 'service' else 'Its Terminal window on the Mac says why')
    again = ('press Go Live on the algo tab again' if how == 'service' else 'start ./jb live again')
    watch.push_later(f'{name.upper()} LIVE trader stopped (error)', f'The {name} live trader stopped with an error'
                     + (f' (exit code {rc})' if rc is not None else '') + '. Its stop-loss is no longer watched and no more live '
                     f'orders are sent. {where}. Check your position in the broker app, then {again}.', 'rotating_light')


def _step_live(cfg, hb, name, lock):
    """Live trading (./jb live in a Terminal window, or the Go Live job) is never restarted by itself, so it gets its own
    loud push when it is gone (crash, Mac restart, window closed) or it stops checking in (frozen). Always on,
    whatever RUN_<BROKER>_ALGO and the heartbeat switch say: this is the one process holding real money."""
    key = 'live_' + name
    a = heartbeat_age(name)
    job = os.path.exists(lock + '.by')             # started by Go Live (jb-golive)
    if not _live_pid_alive(lock):
        why = ('its background job is no longer running (crashed, or the Mac restarted)' if job else
               'its Terminal window is no longer running (closed, crashed, or the Mac restarted)')
    elif a is not None and a > 180:
        why = f'it has not checked in for {int(a / 60)} minutes (frozen or lost its connection)'
    else:
        if hb.pop(key, None):
            watch.push_later(f'{name.upper()} LIVE trader is back', f'The {name} live trader is checking in again.', 'white_check_mark')
        return
    if not hb.get(key):
        hb[key] = _now()
        watch.push_later(f'{name.upper()} LIVE trader is NOT running', f'The {name} live trader is not working: {why}. '
                         'Its stop-loss is not being watched. Check your position in the broker app, then '
                         + ('press Go Live on the algo tab again.' if job else 'start ./jb live again on the Mac.'), 'rotating_light')


# ------------------------------------------------------------------ backup (26)
def backup():
    os.makedirs(BACKUPS, exist_ok=True)
    name = os.path.join(BACKUPS, f'board-{watch.et(_now()):%Y-%m-%d}.tar.gz')
    tmp = name + '.tmp'
    with tarfile.open(tmp, 'w:gz') as tar:
        def add(p, arc):
            if os.path.isfile(p) and os.path.getsize(p) < 30e6:
                tar.add(p, arc)
        add(os.path.join(ROOT, 'settings.env'), 'settings.env')
        for root, dirs, files in os.walk(DATA):
            dirs[:] = [d for d in dirs if d not in ('webull-token', 'cache', '__pycache__')]
            for f in files:
                p = os.path.join(root, f)
                add(p, os.path.relpath(p, ROOT))
        for name_, d in ALGOS.items():
            for sub in ('logs', 'strategies'):
                for p in glob.glob(os.path.join(d, sub, '*.json')) + glob.glob(os.path.join(d, sub, '*.yaml')) + glob.glob(os.path.join(d, sub, '*.csv')):
                    add(p, os.path.relpath(p, ROOT))
    os.replace(tmp, name)
    lst = sorted(glob.glob(os.path.join(BACKUPS, 'board-*.tar.gz')))
    for old in lst[:-7]:
        os.remove(old)
    copied = ''
    if settings()['icloud'] and os.path.isdir(os.path.dirname(ICLOUD)):
        try:
            os.makedirs(ICLOUD, exist_ok=True)
            shutil.copy2(name, ICLOUD)
            for old in sorted(glob.glob(os.path.join(ICLOUD, 'board-*.tar.gz')))[:-7]:
                os.remove(old)
            copied = ' and iCloud Drive'
        except OSError as e:
            copied = f' (iCloud copy failed: {e})'
    _put(backup_at=_now(), backup_file=os.path.basename(name), backup_note=copied)
    return name, copied


# ------------------------------------------------------------------ early loss warnings (39)
def _step_loss(cfg, s):
    if not cfg['early_loss']:
        return
    import trading
    tc = trading.settings()
    if not tc.get('loss_on') or not tc.get('loss_limit'):
        return
    with trading._lock:
        j = trading._journal()
    trips, open_ = trading.round_trips(list(j['fills'].values()))
    sm = trading.summary(trips, open_, j['notes'])
    total = sm['today_realized'] + sm['unrealized_today']
    day = trading.trading_day(_now())
    sent = s.setdefault('loss', {})
    if sent.get('day') != day:
        sent.clear()
        sent['day'] = day
    for pct in (50, 75):
        if total <= -tc['loss_limit'] * pct / 100 and not sent.get(str(pct)) and total > -tc['loss_limit']:
            sent[str(pct)] = True
            watch.push_later(f'Down ${-total:,.0f} today ({pct}% of your limit)',
                             f"You're down ${-total:,.0f} today: {pct}% of your ${tc['loss_limit']:,.0f} daily loss limit. "
                             + ('At 100% the algos pause.' if tc.get('pause_algos') else 'At 100% you get the limit push.'), 'warning')


# ------------------------------------------------------------------ confirmed SELL ALL (40)
def _positions(broker):
    import killswitch as ks
    if broker == 'ibkr':
        import iblink

        async def go(ib):
            import asyncio
            acct = iblink.account()
            ps = await asyncio.wait_for(ib.reqPositionsAsync(), 15)
            return [(p.contract.symbol, float(p.position)) for p in ps if p.contract.secType == 'STK' and float(p.position) != 0 and (not acct or p.account == acct)]
        return iblink.run(go, timeout=25)
    tc = ks._wb_client()
    acct, _ = ks._wb_account(tc)
    return [(p['sym'], float(p['qty'])) for p in ks._wb_positions(tc, acct) if p['qty']]


def _step_sellall(cfg):
    if not cfg['confirm_sellall']:
        return
    import killswitch as ks
    for broker in ('ibkr', 'webull'):
        r = ks._runs.get(broker)
        if not r or not r.get('ended'):
            continue
        c = _st['sell'].setdefault(broker, {})
        if c.get('run') != r['started'] and c.get('origin') is None:
            c.update(run=r['started'], origin=r['started'], rounds=0, done=False, syms=r.get('syms'))
        if c.get('syms') is None and r.get('syms') is not None:
            c['syms'] = list(r['syms'])     # the press failed before it read the positions: the first run that did
        if c.get('done') or time.time() * 1000 - r['ended'] < 60000:
            continue
        if c.get('run') != r['started']:                 # one of our own re-runs just finished
            c['run'] = r['started']
        try:
            left = _positions(broker)
        except Exception as e:
            print(f'guardian: SELL ALL check {broker}: {e!r}', flush=True)
            continue
        if not left:
            if c['rounds']:
                watch.push_later(f'SELL ALL {broker.upper()}: all sold', f'Confirmed: no stock positions left on {broker.upper()}.', 'white_check_mark')
            c['done'] = True
            continue
        text = ', '.join(f'{q:g} {s}' for s, q in left)
        # (syms None: the press stopped before it could read what you held, e.g. IB Gateway did not answer: press again)
        new = [] if c.get('syms') is None else sorted({s for s, _ in left} - set(c['syms']))
        if new:
            # review 2026-10-11 #8: a position SELL ALL didn't start with (bought since) is never sold automatically
            watch.push_later(f'SELL ALL {broker.upper()}: not retried', f'You now hold {", ".join(new)}, which was not part of '
                             f'the SELL ALL, so the board did not press it again. Still holding {text}. Sell in the '
                             f'{broker.upper()} app or press SELL ALL again if you want everything out.', 'rotating_light')
            c['done'] = True
            continue
        if c['rounds'] >= 3 or ks.session_now() is None:
            watch.push_later(f'SELL ALL {broker.upper()}: still holding', f'Still holding {text} after {c["rounds"]} extra tries. Sell it in the {broker.upper()} app.', 'rotating_light')
            c['done'] = True
            continue
        c['rounds'] += 1
        watch.push_later(f'SELL ALL {broker.upper()}: still holding {text}'[:120], f'Trying again (try {c["rounds"] + 1}), priced from the market now.', 'rotating_light')
        code, _ = ks.post(broker, {'confirm': ks.CONFIRM})
        if code != 200:
            c['done'] = True


def _reset_sellall():
    """A new SELL ALL press (not one of ours) starts a fresh confirmation."""
    import killswitch as ks
    for broker, c in list(_st['sell'].items()):
        r = ks._runs.get(broker)
        if r and not r.get('ended') and c.get('done'):
            _st['sell'][broker] = {}


# ------------------------------------------------------------------ internet outages (42)
def _online():
    for host in (('1.1.1.1', 443), ('8.8.8.8', 443), ('www.apple.com', 443)):
        try:
            socket.create_connection(host, timeout=4).close()
            return True
        except OSError:
            continue
    return False


def outages():
    v = watch._read(OUTAGES, [])
    return v if isinstance(v, list) else []


def _step_net():
    n = _st['net']
    up = _online()
    if up:
        if not n['up'] and n['since']:
            mins = round((_now() - n['since']) / 60000, 1)
            lst = outages()
            lst.insert(0, {'start': n['since'], 'end': _now(), 'mins': mins})
            watch._write(OUTAGES, lst[:200])
            if mins >= 2:
                watch.push_later('Internet was down', f'The Mac\'s internet was down {mins:g} min ({watch.et(n["since"]):%I:%M %p} - now). Check your positions and open orders.', 'warning')
        n.update(up=True, fails=0, since=0)
        return
    n['fails'] += 1
    if n['fails'] >= 2 and n['up']:                 # two failures 30 s apart: it is down
        n.update(up=False, since=_now() - 30000)


# ------------------------------------------------------------------ log trimming (43)
def trim_logs():
    trimmed = []
    for p in glob.glob(os.path.join(LOGS, '*.log')) + glob.glob(os.path.join(ROOT, '*', 'logs', '*.log')):
        try:
            if os.path.getsize(p) > 20e6:
                with open(p, 'rb') as f:
                    f.seek(-5_000_000, 2)
                    tail = f.read()
                with open(p, 'wb') as f:
                    f.write(b'===== trimmed by the board (kept the last 5 MB) =====\n' + tail[tail.find(b'\n') + 1:])
                trimmed.append(os.path.basename(p))
        except OSError:
            pass
    for p in glob.glob(os.path.join(LOGS, '*.old')) + glob.glob(os.path.join(ROOT, '*', 'logs', '*.old')):
        try:
            if time.time() - os.path.getmtime(p) > 7 * 86400:
                os.remove(p)
                trimmed.append(os.path.basename(p) + ' (deleted)')
        except OSError:
            pass
    return trimmed


# ------------------------------------------------------------------ self-test (45) and refresh (57)
def self_test(why):
    import syscheck
    code, res = syscheck.run_live(why)          # it pushes its own one-line summary
    return res


def saturday_refresh():
    _st['busy'] = 'refresh'
    done = []
    try:
        for svc in REFRESH_ORDER:
            if svc != 'keepawake' and not _service_on(svc):
                continue
            r = subprocess.run([os.path.join(ROOT, 'jb'), 'restart', svc], cwd=ROOT, capture_output=True, text=True, timeout=120)
            done.append(svc)
            time.sleep(45)
        time.sleep(60)
        self_test('saturday refresh')
        _put(refresh_at=_now(), refresh_done=done)
    finally:
        _st['busy'] = ''


# ------------------------------------------------------------------ loop
def _loop():
    time.sleep(60)
    n = 0
    while True:
        cfg, now = settings(), _now()
        d = watch.et(now)
        s = _state()
        changed = False
        try:
            _step_net()
        except Exception as e:
            print(f'guardian: net: {e!r}', flush=True)
        try:
            _step_battery()                         # on battery push (jb-power)
        except Exception as e:
            print(f'guardian: battery: {e!r}', flush=True)
        for fn in (lambda: _step_heartbeats(cfg, s), lambda: _step_loss(cfg, s), lambda: _reset_sellall(), lambda: _step_sellall(cfg)):
            try:
                fn()
            except Exception as e:
                print(f'guardian: {e!r}', flush=True)
        key = d.strftime('%Y-%m-%d')
        try:
            if cfg['backup'] and d.hour == 2 and d.minute >= 30 and s.get('backup_day') != key:
                s['backup_day'] = key
                changed = True
                threading.Thread(target=backup, name='backup', daemon=True).start()
            if d.hour == 3 and s.get('trim_day') != key:
                s['trim_day'] = key
                changed = True
                trim_logs()
            if n % 120 == 0:                            # every hour: the clock
                s['drift'], s['drift_at'] = clock_drift(), now
                changed = True
            if cfg['sunday_test'] and d.weekday() == 6 and d.hour == 17 and s.get('sunday_day') != key:
                s['sunday_day'] = key
                changed = True
                threading.Thread(target=self_test, args=('sunday self-test',), daemon=True).start()
            if cfg['saturday_refresh'] and d.weekday() == 5 and d.hour == 7 and s.get('refresh_day') != key and not _st['busy']:
                s['refresh_day'] = key
                changed = True
                threading.Thread(target=saturday_refresh, name='refresh', daemon=True).start()
        except Exception as e:
            print(f'guardian: schedule: {e!r}', flush=True)
        with _lock:                                     # keep keys other threads wrote meanwhile (backup)
            cur = _state()
            cur.update({k: v for k, v in s.items() if k in ('hb', 'loss', 'backup_day', 'trim_day', 'drift', 'drift_at', 'sunday_day', 'refresh_day')})
            watch._write(STATE, cur)
        n += 1
        time.sleep(30)


def start():
    if _st['started']:
        return
    _st['started'] = True
    threading.Thread(target=_loop, name='guardian', daemon=True).start()


def snapshot():
    s = _state()
    return {'outages': outages()[:30], 'net_up': _st['net']['up'], 'net_down_since': _st['net']['since'] or None,
            'backups': [os.path.basename(p) for p in sorted(glob.glob(os.path.join(BACKUPS, 'board-*.tar.gz')))][::-1],
            'state': {k: s.get(k) for k in ('backup_at', 'backup_file', 'backup_note', 'drift', 'refresh_at', 'refresh_done')},
            'checks': checks(), 'settings': settings(), 'busy': _st['busy']}


def get(sub, query):
    if sub in ('', 'status'):
        return 200, snapshot()
    if sub == 'outages':
        return 200, {'outages': outages()[:50], 'net_up': _st['net']['up']}
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'settings':
        cur = settings()
        for k in DEFAULTS:
            if k in data:
                cur[k] = bool(data[k])
        watch._write(SETTINGS, cur)
        return 200, {'settings': cur}
    if sub == 'backup':
        try:
            name, copied = backup()
            return 200, {'text': f'Saved {os.path.basename(name)} in ~/josephs-board/backups{copied}.'}
        except Exception as e:
            return 500, {'error': f'Backup failed: {str(e)[:200]}'}
    return 404, {'error': 'unknown'}
