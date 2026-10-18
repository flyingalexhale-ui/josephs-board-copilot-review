"""System check (jb-syscheck): one place, on the phone and the laptop, that says whether the whole
board is working, and a "Run check" button that tests every connection live and sends the answer to
your phone ("all live", or what is down and what to do).

  Board parts     every background service (same as the Health tab)
  Brokers         IBKR and Webull prices arriving when they should (knows the sessions and NYSE
                  holidays), IB Gateway's API port, the order/journal link to each broker, auto-login
  Phone alerts    the ntfy topic is set and ntfy answers (the live check sends the result through it)
  Data feeds      what each part of the board last got from its free sources (and any error)
  Internet feeds  (live check only) one small request to each outside site the board reads
  Mac             disk space, how long the Mac and the board have been up, remote access
  Settings        which important settings are filled in (never their values)

  GET  /api/plus/syscheck            the current picture (no outside requests)
  POST /api/plus/syscheck/run        test everything live now; result also pushed to the phone
  POST /api/plus/syscheck/push       send just a test push
  POST /api/plus/syscheck/settings   {watch: true|false}  push me when something breaks (off to start)
"""
import concurrent.futures
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from datetime import date, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
SETTINGS = os.path.join(DATA, 'syscheck_settings.json')
LAST = os.path.join(DATA, 'syscheck_last.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15'
STARTED = time.time()
ORDER = {'bad': 0, 'warn': 1, 'ok': 2, 'info': 3, 'off': 4}

_lock = threading.Lock()
_st = {'started': False, 'running': False, 'bad_since': {}, 'told': {}}


def _now():
    return int(time.time() * 1000)


def settings():
    s = watch._read(SETTINGS, {})
    return {'watch': bool(s.get('watch', False))} if isinstance(s, dict) else {'watch': False}


def item(name, state, detail='', fix=''):
    return {'name': name, 'state': state, 'detail': detail, 'fix': fix}


def _ago(ms, now):
    if not ms:
        return 'never'
    s = max(0, int((now - ms) / 1000))
    return f'{s} s ago' if s < 90 else f'{round(s / 60)} min ago' if s < 5400 else f'{round(s / 3600)} h ago' if s < 172800 else f'{round(s / 86400)} days ago'


def _port_up(port, timeout=0.8):
    try:
        with socket.create_connection(('127.0.0.1', int(port)), timeout=timeout):
            return True
    except Exception:
        return False


# ------------------------------------------------------------------ when should prices arrive?
def _trading_day(d):
    try:
        from nyse_hours import nyse_holidays
        return d.weekday() < 5 and d not in nyse_holidays(d.year)
    except Exception:
        return d.weekday() < 5


def expected(broker, ms):
    """True when this broker should be sending prices now (New York time)."""
    t = watch.et(ms)
    m = t.hour * 60 + t.minute
    d = t.date()
    if broker == 'ibkr':          # stocks overnight Sun 8 pm - Fri 8 pm, short pause 3:50-4:00 am; futures too
        if t.weekday() == 5 or (t.weekday() == 4 and m >= 20 * 60) or (t.weekday() == 6 and m < 20 * 60):
            return False
        if 3 * 60 + 50 <= m < 4 * 60:
            return False
        if m >= 20 * 60:          # the night before a trading day
            nxt = d + timedelta(days=1)
            return _trading_day(nxt)
        if m < 4 * 60:
            return _trading_day(d)
        return _trading_day(d)
    if _webull_overnight():       # the plan has the overnight session (hedge-2026-10-12): 8 pm - 4 am before trading days
        if m >= 20 * 60:
            return _trading_day(d + timedelta(days=1))
        if m < 4 * 60:
            return _trading_day(d)
    return _trading_day(d) and 4 * 60 <= m < 20 * 60       # Webull without the overnight session


# ------------------------------------------------------------------ the passive picture
def check_services():
    out = []
    try:
        import extras
        for s in extras.services():
            if not s['on']:
                continue
            ok = s['state'] == 'running' and (not s.get('port') or s.get('port_up', True))
            st = 'ok' if ok else 'warn' if s['state'] in ('running', 'unknown') else 'bad'
            det = 'running' if ok else {'restarting': 'keeps stopping and restarting', 'stopped': 'stopped',
                                         'running': 'starting up (port not open yet)'}.get(s['state'], s['state'])
            if s.get('algo') and s['algo'].get('killed'):
                det += ' · PAUSED (press Resume on its algo tab)'
                st = 'warn' if st == 'ok' else st
            out.append(item(s['title'], st, det, '' if ok else 'Health tab: press Restart on it (or ./jb restart ' + s['name'] + ').'))
    except Exception as e:
        out.append(item('Board parts', 'warn', f'could not read: {e}'))
    return out


def check_brokers(now):
    out = []
    try:
        import extras
        svcs = {s['name']: s for s in extras.services()}
    except Exception:
        svcs = {}
    conn = watch._state.get('connected') or {}
    for b, title in (('ibkr', 'IBKR'), ('webull', 'Webull')):
        if b in svcs and not svcs[b]['on']:
            out.append(item(f'{title} prices', 'off', 'off in settings.env'))
            continue
        try:
            import extras
            last = max(extras._live_price(b) or 0, extras._last_tick(b) or 0) or None
        except Exception:
            last = None
        exp = expected(b, now)
        fresh = last and now - last < 5 * 60000
        if not conn.get(b):
            out.append(item(f'{title} connection', 'bad' if exp else 'warn', f'the board is not connected to the {title} price service',
                            f'Health tab: Restart "{title} prices".'))
        elif fresh:
            out.append(item(f'{title} prices', 'ok', f'last price {_ago(last, now)}'))
        elif exp:
            out.append(item(f'{title} prices', 'bad', f'should be coming in now; last price {_ago(last, now)}',
                            'IBKR: is IB Gateway running and logged in? Then Restart "IBKR prices".' if b == 'ibkr'
                            else 'Restart "Webull prices"; if it keeps happening, ./jb webull-login.'))
        else:
            out.append(item(f'{title} prices', 'info', f'market closed for {title} now (last price {_ago(last, now)})'))
    # IB Gateway's API port
    if svcs.get('ibkr', {}).get('on', True):
        p = os.environ.get('IBKR_GATEWAY_PORT', '').strip()
        ports = [int(p)] if p.isdigit() else [4001, 4002, 7496, 7497]
        up = [x for x in ports if _port_up(x, 0.5)]
        out.append(item('IB Gateway', 'ok' if up else 'bad', f'API answering on port {up[0]}' if up else 'not answering on ' + '/'.join(map(str, ports)),
                        '' if up else 'Open IB Gateway on the Mac and log in (or let the auto-login do it).'))
    try:
        import ibauto
        _, a = ibauto.get('', {})
        if a.get('on'):
            st = {'logged in': 'ok', 'waiting for approval': 'bad', 'login failed': 'bad', 'starting': 'warn'}.get(a['state'], 'info')
            out.append(item('IB Gateway auto-login', st, a['state'], 'Open IBKR Mobile and tap Approve.' if a['state'] == 'waiting for approval'
                            else './jb ibkr-autologin to retype the login.' if a['state'] == 'login failed' else ''))
    except Exception:
        pass
    # reading orders / fills from each broker (journal)
    try:
        import trading
        for b, title in (('ibkr', 'IBKR'), ('webull', 'Webull')):
            s = dict(trading._status.get(b) or {})
            if s.get('ok') is None:
                continue
            out.append(item(f'{title} account link (fills)', 'ok' if s['ok'] else 'warn',
                            f"checked {_ago((s.get('at') or 0) * (1000 if (s.get('at') or 0) < 1e12 else 1), now)}" + (f" · {s.get('note')}" if s.get('note') else ''),
                            '' if s['ok'] else 'Usually fixes itself; if it stays, Restart the board on the Health tab.'))
    except Exception:
        pass
    return out


def check_phone():
    t = watch.topic()
    return [item('Phone alerts (ntfy)', 'ok' if t else 'bad', 'topic set' if t else 'no JB_NTFY_TOPIC in settings.env',
                 '' if t else 'Add JB_NTFY_TOPIC to settings.env, then ./jb restart.')]


def _feed(name, err, at, now, max_age_h, fix=''):
    if err:
        return item(name, 'warn', str(err)[:140], fix or 'Usually a busy or blocked site; it retries by itself.')
    if not at:
        return item(name, 'info', 'not checked yet')
    age_h = (now - at) / 3600000
    return item(name, 'ok' if age_h <= max_age_h else 'warn', f'last update {_ago(at, now)}',
                '' if age_h <= max_age_h else 'Older than expected; Restart the board on the Health tab if it stays.')


def check_feeds(now):
    out = []

    def add(fn):
        try:
            r = fn()
            if r:
                out.extend(r if isinstance(r, list) else [r])
        except Exception as e:
            pass

    def world():
        import world
        s = dict(world._status)
        at = s.get('quotes_at') or 0
        at = at * 1000 if at and at < 1e12 else at
        err = s.get('note') or '; '.join(f'{k}: {v}' for k, v in list((s.get('errors') or {}).items())[:2])
        return _feed('World prices (Yahoo)', err, at, now, 1)

    def news():
        import news
        with news._lock:
            f = dict(news._state['fetched'])
            e = dict(news._state['errors'])
        last = max(f.values()) * 1000 if f else 0
        return _feed('Stock news (Google News)', f'{len(e)} symbols failing' if f and len(e) > len(f) / 2 else '', last, now, 2)

    def signals():
        import signals
        return item('SEC filings (Signals)', 'warn' if signals._st.get('sec_note') else 'ok',
                    signals._st.get('sec_note') or 'no problem reported', '')

    def mdata():
        import mdata
        with mdata._lock:
            e = dict(mdata._st['errors'])
            px = [v.get('last_ms') or 0 for v in mdata._st['px'].values() if v]
        return _feed('Market data (Yahoo, FINRA)', '; '.join(f'{k}: {v}' for k, v in list(e.items())[:2]), max(px) if px else 0, now, 24)

    def alerts2():
        import alerts2
        s = alerts2._st
        return [_feed('Trading halts (Nasdaq)', s.get('halts_err'), s.get('halts_at'), now, 1),
                _feed('Fed / CPI / jobs odds (Kalshi)', s.get('odds_err'), s.get('odds_at'), now, 6)]

    def earn():
        import earn
        e = dict(earn._st.get('errors') or {})
        return item('Earnings data (Yahoo)', 'warn' if e else 'ok', '; '.join(f'{k}: {v}' for k, v in list(e.items())[:2]) or 'no problem reported')

    def macro():
        import macro
        with macro._lock:
            e = dict(macro._st['errors'])
            at = macro._st.get('news_at') or 0
        bad = {k: v for k, v in e.items() if not k.startswith('news ')}
        return _feed('MACRO (FRED, macro news)', '; '.join(f'{k}: {v}' for k, v in list(bad.items())[:2]), at, now, 2)

    def bigmoney():
        import bigmoney
        with bigmoney._lock:
            e = dict(bigmoney._st['errors'])
            h, s = bigmoney._st['house_at'], bigmoney._st['senate_at']
        be = '; '.join(f'{k}: {v}' for k, v in e.items() if k.startswith('own') or k == 'dark pools')[:140]
        return [item('Big money (Yahoo, FINRA)', 'warn' if be else 'ok', be or 'no problem reported', 'Usually a busy or blocked site; it retries by itself.' if be else ''),
                _feed('Politicians (House)', e.get('house'), h, now, 13), _feed('Politicians (Senate)', e.get('senate'), s, now, 13)]

    def webwatch():
        import webwatch
        with webwatch._lock:
            src = dict(webwatch._st['sources'])
        def one(k, v):                         # a site that worked in the last 6 h and hiccups once is a note, not a problem (jb-power)
            if v.get('ok'):
                return item('Web watch: ' + k, 'ok', f"checked {_ago(v.get('at'), now)}")
            if v.get('good') and now - v['good'] < 6 * 3600000:
                return item('Web watch: ' + k, 'ok', f"site busy just now ({v.get('err') or 'failed'}), last worked {_ago(v['good'], now)}; it retries by itself")
            return item('Web watch: ' + k, 'warn', v.get('err') or 'failed', 'The site has not answered for hours. Usually it fixes itself; nothing to do on the Mac.')
        return [one(k, v) for k, v in sorted(src.items())]

    def pick():
        import pick
        e = pick._st['errors'].get('pick')
        return item('ETF pick of the hour', 'warn' if e else 'ok', e or 'running')

    for fn in (world, news, signals, mdata, alerts2, earn, macro, bigmoney, webwatch, pick):
        add(fn)
    return out


def _tailscale():
    for t in ('tailscale', '/Applications/Tailscale.app/Contents/MacOS/Tailscale', '/usr/local/bin/tailscale', '/opt/homebrew/bin/tailscale'):
        try:
            r = subprocess.run([t, 'ip', '-4'], capture_output=True, text=True, timeout=5)
            ip = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ''
            if ip:
                return ip
        except Exception:
            continue
    return ''


def _boot_s():
    try:
        r = subprocess.run(['sysctl', '-n', 'kern.boottime'], capture_output=True, text=True, timeout=5).stdout
        sec = int(r.split('sec =')[1].split(',')[0])
        return time.time() - sec
    except Exception:
        return None


def _dur(s):
    s = int(s)
    return f'{s // 86400} days {s % 86400 // 3600} h' if s >= 86400 else f'{s // 3600} h {s % 3600 // 60} min' if s >= 3600 else f'{s // 60} min'


def check_mac():
    out = []
    try:
        du = shutil.disk_usage(ROOT)
        free = du.free / 1e9
        out.append(item('Mac disk space', 'bad' if free < 2 else 'warn' if free < 5 else 'ok', f'{free:.1f} GB free of {du.total / 1e9:.0f} GB',
                        '' if free >= 5 else 'Empty the Trash / delete old downloads on the Mac.'))
    except OSError:
        pass
    b = _boot_s()
    out.append(item('Mac up for', 'info', _dur(b) if b else 'unknown'))
    out.append(item('Board up for', 'info', _dur(time.time() - STARTED)))
    ip = _tailscale()
    out.append(item('Remote access (Tailscale)', 'ok' if ip else 'warn', f'Mac address {ip}' if ip else 'Tailscale address not found',
                    '' if ip else 'Open the Tailscale app on the Mac and make sure it is connected.'))
    try:                                   # power cut restart, memory, clock, updates, crash loops, backups (jb-ideas)
        import guardian
        out += guardian.checks()
    except Exception:
        pass
    return out


def check_settings():
    want = [('JB_PASSWORD', 'Board password', 'bad'), ('JB_NTFY_TOPIC', 'Phone alert topic', 'bad'), ('JB_SEC_EMAIL', 'SEC contact email', 'warn'),
            ('WEBULL_ACCOUNT_ID', 'Webull trading account', 'warn'), ('JB_HEARTBEAT_URL', 'Mac heartbeat (healthchecks.io)', 'info')]
    return [item(label, 'ok' if os.environ.get(k, '').strip() else lvl, 'set' if os.environ.get(k, '').strip() else 'not set',
                 '' if os.environ.get(k, '').strip() else f'Add {k}= to settings.env (Night desk guide).') for k, label, lvl in want]


def snapshot():
    now = _now()
    groups = [('Board parts', check_services()), ('Brokers', check_brokers(now)), ('Phone alerts', check_phone()),
              ('Data feeds', check_feeds(now)), ('Mac', check_mac()), ('Settings', check_settings())]
    return summarize(groups, now)


def summarize(groups, now):
    allitems = [i for _, g in groups for i in g]
    bad = [i for i in allitems if i['state'] == 'bad']
    warn = [i for i in allitems if i['state'] == 'warn']
    overall = 'bad' if bad else 'warn' if warn else 'ok'
    return {'at': now, 'overall': overall, 'counts': {'bad': len(bad), 'warn': len(warn), 'ok': sum(1 for i in allitems if i['state'] == 'ok'),
            'total': len(allitems)}, 'groups': [{'name': n, 'items': g} for n, g in groups], 'settings': settings(),
            'last_run': watch._read(LAST, None)}


# ------------------------------------------------------------------ the live check
def _probe(name, url, data=None, headers=None, method=None):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    h.update(headers or {})
    t0 = time.time()
    try:
        import world
        chrome = headers == CHROME and world._browser is not None
    except Exception:
        chrome = False
    if headers == CHROME and not chrome:
        return item(name, 'warn', 'the "ask like Chrome" helper (curl_cffi) is not installed on the Mac',
                    'In Terminal: cd ~/josephs-board && .venv/bin/pip install curl_cffi, then ./jb restart gateway')
    if chrome:                             # sites the board reads the Chrome way (they never answer plain Python)
        try:
            r = world._browser.Session(impersonate='chrome').get(url, timeout=15)
            if r.status_code < 400:
                return item(name, 'ok', f'answered in {int((time.time() - t0) * 1000)} ms')
            if r.status_code == 429:
                return item(name, 'ok', 'reachable (asked to slow down, HTTP 429, normal right after the board used it)')
            if r.status_code == 403:
                return item(name, 'warn', f'blocked or rate-limited (HTTP {r.status_code})', 'Usually temporary; the board backs off and retries.')
            return item(name, 'warn', f'HTTP {r.status_code}', 'Their site is having trouble; it retries by itself.')
        except Exception as e:
            return item(name, 'bad', f'no answer: {str(e)[:100]}', 'Check the Mac\'s internet connection.')
    try:
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=15, context=watch._ctx()) as r:
            r.read(200000)
            code = r.status
        return item(name, 'ok', f'answered in {int((time.time() - t0) * 1000)} ms')
    except urllib.error.HTTPError as e:
        if e.code == 429:                  # it answered: just "slow down" (the board itself asked it moments ago)
            return item(name, 'ok', f'reachable (asked to slow down, HTTP 429, normal right after the board used it)')
        if e.code == 403:
            return item(name, 'warn', f'blocked or rate-limited (HTTP {e.code})', 'Usually temporary; the board backs off and retries.')
        if e.code < 500:
            return item(name, 'ok', f'reachable (HTTP {e.code}) in {int((time.time() - t0) * 1000)} ms')
        return item(name, 'warn', f'site error HTTP {e.code}', 'Their site is having trouble; it retries by itself.')
    except Exception as e:
        return item(name, 'bad', f'no answer: {str(e)[:100]}', 'Check the Mac\'s internet connection.')


def _yahoo():
    t0 = time.time()
    try:
        import world
        world._get('https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=1d&interval=1d')
        return item('Yahoo Finance', 'ok', f'answered in {int((time.time() - t0) * 1000)} ms')
    except Exception as e:
        s = str(e)
        return item('Yahoo Finance', 'warn' if '429' in s else 'bad', s[:120], 'Yahoo limits requests; the board backs off and retries.')


CHROME = {'_chrome': '1'}


def _finra_short_url():
    """FINRA's short-volume file for the last weekday (the folder itself is not listable: HTTP 403)."""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return f'https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d:%Y%m%d}.txt'


def internet_probes():
    sec = {'User-Agent': 'JosephsBoard ' + os.environ.get('JB_SEC_EMAIL', '').strip(), 'Accept-Encoding': 'identity'}
    week = (date.today() - timedelta(days=10)).isoformat()
    jobs = [
        ('ntfy (phone alerts)', 'https://ntfy.sh/v1/health', None, None),
        ('Nasdaq halts', 'https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts', None, None),
        ('Kalshi odds', 'https://api.elections.kalshi.com/trade-api/v2/events?series_ticker=KXFED&status=open&limit=1', None, {'Accept': 'application/json'}),
        ('FINRA short volume', _finra_short_url(), None, None),
        ('FINRA dark pools', 'https://api.finra.org/data/group/otcMarket/name/weeklySummary', json.dumps({'limit': 1}).encode(),
         {'Content-Type': 'application/json', 'Accept': 'application/json'}),
        ('FRED (MACRO)', f'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF&cosd={week}', None, CHROME),
        ('SEC EDGAR', 'https://data.sec.gov/submissions/CIK0000320193.json', None, sec),
        ('SEC full-text search', 'https://efts.sec.gov/LATEST/search-index?q=%22Apple%22&forms=8-K', None, sec),
        ('Google News', 'https://news.google.com/rss/search?q=stock+market&hl=en-US&gl=US&ceid=US:en', None, None),
        ('Economic calendar', 'https://nfs.faireconomy.media/ff_calendar_thisweek.json', None, None),
        ('House filings', 'https://disclosures-clerk.house.gov/', None, None),
        ('Senate filings', 'https://efdsearch.senate.gov/search/home/', None, None),
        ('Press releases', 'https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies', None, CHROME),
        ('Reddit', 'https://www.reddit.com/r/stocks/new/.rss?limit=1', None, None),
        ('Wikipedia', 'https://en.wikipedia.org/w/api.php?action=query&meta=siteinfo&format=json', None, {'User-Agent': 'JosephsBoard/1.0 (personal dashboard)'}),
        ('Google Trends', 'https://trends.google.com/trending/rss?geo=US', None, None),
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_yahoo)] + [ex.submit(_probe, n, u, d, h) for n, u, d, h in jobs]
        return [f.result() for f in futs]


def local_probes(now):
    out = []
    for name, port, key in (('IBKR price service', 8765, 'RUN_IBKR_BRIDGE'), ('Webull price service', 8766, 'RUN_WEBULL_BRIDGE'),
                            ('Scanner service', 8767, 'RUN_SCANNER')):
        if os.environ.get(key, 'on').strip().lower() in ('off', 'no', 'false', '0'):
            continue
        up = _port_up(port)
        out.append(item(name, 'ok' if up else 'bad', f'answering on port {port}' if up else f'port {port} closed',
                        '' if up else 'Health tab: press Restart on it.'))
    return out


def run_live(source='button'):
    with _lock:
        if _st['running']:
            return 409, {'error': 'A check is already running; give it about 20 seconds.'}
        _st['running'] = True
    try:
        now = _now()
        groups = [('Board parts', check_services()), ('Brokers', check_brokers(now) + local_probes(now)), ('Phone alerts', check_phone()),
                  ('Internet feeds (tested now)', internet_probes()), ('Data feeds', check_feeds(now)), ('Mac', check_mac()), ('Settings', check_settings())]
        res = summarize(groups, now)
        msg = message(res)
        err = watch.push(msg['title'], msg['text'], msg['tags'])
        res['push'] = {'sent': not err, 'error': err, 'text': msg['text']}
        watch._write(LAST, {'at': now, 'overall': res['overall'], 'counts': res['counts'], 'push': res['push'], 'source': source})
        res['last_run'] = watch._read(LAST, None)
        return 200, res
    finally:
        with _lock:
            _st['running'] = False


def message(res):
    bad = [i for g in res['groups'] for i in g['items'] if i['state'] == 'bad']
    warn = [i for g in res['groups'] for i in g['items'] if i['state'] == 'warn']
    c = res['counts']
    if not bad and not warn:
        return {'title': 'Board check: ALL LIVE', 'tags': 'white_check_mark',
                'text': f"Everything is live: {c['ok']} checks passed, brokers, feeds and alerts all answering. "
                        f"({time.strftime('%I:%M %p').lstrip('0')} on the Mac)"}
    lines = [f"DOWN: {i['name']} - {i['detail']}" + (f" -> {i['fix']}" if i['fix'] else '') for i in bad[:6]]
    lines += [f"Check: {i['name']} - {i['detail']}" for i in warn[:6]]
    more = len(bad) + len(warn) - len(lines)
    return {'title': f"Board check: {len(bad)} down, {len(warn)} to check" if bad else f'Board check: live, {len(warn)} to check',
            'tags': 'rotating_light' if bad else 'warning',
            'text': '\n'.join(lines) + (f'\n...and {more} more on the System check page.' if more > 0 else '') + f"\n{c['ok']} other checks OK."}


# ------------------------------------------------------------------ optional: push me when something breaks
def _watch_loop():
    while True:
        time.sleep(300)
        try:
            if not settings()['watch']:
                _st['bad_since'].clear()
                continue
            now = _now()
            res = snapshot()
            bad = {i['name']: i for g in res['groups'] for i in g['items'] if i['state'] == 'bad'}
            for n in list(_st['bad_since']):
                if n not in bad:
                    _st['bad_since'].pop(n)
                    if _st['told'].pop(n, None):
                        watch.push_later(f'Fixed: {n}', f'{n} is working again.', 'white_check_mark')
            for n, i in bad.items():
                since = _st['bad_since'].setdefault(n, now)
                if now - since >= 10 * 60000 and not _st['told'].get(n):
                    _st['told'][n] = now
                    watch.push_later(f'Board problem: {n}', f"{i['detail']}." + (f" {i['fix']}" if i['fix'] else ''), 'rotating_light')
        except Exception as e:
            print(f'syscheck watch error: {e!r}', flush=True)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_watch_loop, name='syscheck', daemon=True).start()


def get(sub, query):
    return 200, snapshot()


def post(sub, data):
    if sub == 'run':
        return run_live()
    if sub == 'push':
        err = watch.push('Board test push', 'This is a test push from your board. If you can read this, phone alerts work.', 'bell')
        return (200, {'sent': True}) if not err else (502, {'error': err})
    if sub == 'settings':
        s = settings()
        if 'watch' in data:
            s['watch'] = bool(data['watch'])
        watch._write(SETTINGS, s)
        return 200, {'settings': s}
    return 404, {'error': 'unknown'}


def _webull_overnight():
    """Does the Webull plan stream the overnight session right now (lib/jb_webull.py, hedge-2026-10-12)?"""
    try:
        import jb_webull
        return jb_webull.overnight_state().get('on') is True
    except Exception:
        return False
