"""Calls every API route of a running gateway (GET, plus harmless POSTs and malformed POSTs) and reports
5xx answers, Python tracebacks in answers, and slow answers.
usage: python3 tests/fake-brokers/api-sweep.py http://127.0.0.1:8090 testpw"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE, PW = sys.argv[1].rstrip('/'), sys.argv[2]
ORIGIN = {'Origin': BASE}


def login():
    req = urllib.request.Request(BASE + '/login', data=urllib.parse.urlencode({'password': PW, 'next': '/'}).encode(),
                                 headers=ORIGIN, method='POST')

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        urllib.request.build_opener(NoRedirect).open(req, timeout=20)
    except urllib.error.HTTPError as e:
        return e.headers.get('Set-Cookie', '').split(';')[0]
    raise SystemExit('login did not redirect')


COOKIE = login()
BAD = []


def call(method, path, body=None, timeout=60):
    h = dict(ORIGIN, Cookie=COOKIE)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, txt = r.status, r.read(400000).decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        code, txt = e.code, e.read(4000).decode('utf-8', 'replace')
    except Exception as e:
        code, txt = 0, repr(e)
    dt = time.time() - t0
    flag = code >= 500 or code == 0 or 'Traceback' in txt
    if flag or dt > 15:
        BAD.append((method, path, code, round(dt, 1), txt[:200].replace('\n', ' ')))
    print(f'{code:3d} {dt:5.1f}s {method:4s} {path}  {"<-- " + txt[:150] if flag else ""}', flush=True)
    return code, txt


GETS = ['/api/health', '/api/health/page', '/api/phone', '/api/algo/ibkr', '/api/algo/webull', '/api/algo/ibkr/control',
        '/api/algo/webull/control', '/api/algo/ibkr/options', '/api/algo/webull/options', '/api/watch', '/api/world', '/api/news',
        '/api/holdings', '/api/portfolio/backtest?sym=TQQQ', '/api/volume?sym=TQQQ', '/api/signals', '/api/why?sym=TQQQ',
        '/api/ranges?syms=TQQQ,SOXL', '/api/analysis', '/api/analysis/options?sym=TQQQ', '/api/trading',
        '/api/trading/leaderboard', '/api/trading/replay', '/api/kill/ibkr', '/api/kill/webull', '/api/recap',
        '/api/channel', '/api/channel/voices', '/api/plus', '/api/tv', '/api/tvchannel', '/api/widget']
PLUS = {'night': ['', 'quotes', 'status', 'settings'], 'alerts2': ['', 'status', 'heartbeat'], 'mdata': ['', 'status', 'scan', 'twins', 'pairs'],
        'pick': [''], 'bigmoney': ['', 'status'], 'webwatch': [''], 'earn': ['', 'status', 'nightday', 'plan'],
        'ticket': ['', 'status', 'quote?sym=TQQQ', 'positions'], 'levels': [''], 'ibauto': [''], 'macro': ['', 'status'], 'claude': [''],
        'syscheck': [''], 'orders': ['', 'status', 'notes', 'twin?sym=TQQQ'], 'edge': ['', 'status', 'tiles', 'holdings', 'fund?sym=TQQQ',
        'history?sym=TQQQ', 'gamma?sym=QQQ'], 'feeds': ['', 'status', 'tiles', 'top', 'btc'], 'guardian': ['', 'status', 'outages'],
        'tools': ['', 'bars?sym=TQQQ', 'notes', 'alerts', 'scorecard', 'golive', 'changes', 'widget/setup', 'report'],
        'sentiment': ['', 'status'], 'meters': ['', 'status'], 'chartpage': ['', 'bars?sym=TQQQ', 'flow?sym=TQQQ', 'status'],
        'reportcard': ['', 'status', 'badges', 'trades', 'feature?name=insider', 'replay'], 'safety': ['', 'status', 'hotspot', 'dec6'],
        'tradetools': ['', 'fills', 'templates', 'history', 'plan'], 'why': ['', 'explain?sym=TQQQ', 'tiles', 'lines', 'brief', 'levels?sym=TQQQ'],
        'scanwhy': ['', 'tags'], 'insight': ['', 'notes', 'crowd', 'articles?sym=TQQQ'], 'rc_explore': ['', 'features', 'calls'],
        'rules': [''], 'stratlab': ['', 'knobs'], 'top5': ['', 'status'], 'algonotes': ['', 'ibkr', 'webull'], 'brokerpos': ['']}
for p in GETS:
    call('GET', p)
for m, subs in PLUS.items():
    for s in subs:
        call('GET', f'/api/plus/{m}' + (f'/{s}' if s else ''))
# malformed / empty POSTs must answer 4xx, never 500
POSTS_EMPTY = ['/api/trading', '/api/watch', '/api/signals', '/api/holdings', '/api/recap', '/api/kill/ibkr', '/api/algo/ibkr/control',
               '/api/algo/ibkr/options', '/api/health/restart', '/api/channel/voices']
for p in POSTS_EMPTY:
    call('POST', p, {})
for m, subs in PLUS.items():
    call('POST', f'/api/plus/{m}', {})
    for s in subs:
        if s and '?' not in s:
            call('POST', f'/api/plus/{m}/{s}', {})
for p, b in [('/api/plus/ticket/check', {'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'buy', 'qty': 'x', 'limit': 50}),
             ('/api/plus/ticket/check', {'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'sell', 'qty': 1e300, 'limit': 'nan'}),
             ('/api/plus/orders/guard', {'broker': 'ibkr', 'sym': 'TQQQ', 'stop': 'abc'}),
             ('/api/plus/orders/schedule', {'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'sell', 'qty': 'all', 'when': 'garbage'}),
             ('/api/trading', {'action': 'add', 'sym': 'X', 'side': 'buy', 'qty': 'a', 'price': None, 'when': '2026-13-45T25:61'}),
             ('/api/watch', {'action': 'add', 'sym': 'TQQQ', 'kind': 'above', 'value': 'abc'}),
             ('/api/algo/ibkr/options', {'options': {'cancel_after_s': 'x'}}),
             ('/api/plus/ticket/check', {'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'buy', 'qty': 10, 'limit': 50.0})]:
    call('POST', p, b)
print('\nPROBLEMS:' if BAD else '\nno 5xx, tracebacks or slow answers')
for b in BAD:
    print(' ', b)
