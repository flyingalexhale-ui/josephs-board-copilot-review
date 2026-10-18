#!/usr/bin/env python3
"""The front door for everything on the spare Mac: ONE address, ONE password.

    http://<spare-mac>:8090/            the dashboard (tabs for every board / scanner / algo)
    http://<spare-mac>:8090/ibkr.html   the IBKR board on its own   (also /webull.html, ...)

What it does, all on one port, using only Python's standard library:
  * serves the pages in web/
  * forwards the pages' live WebSocket connections to the services behind it, which only
    listen on this Mac (127.0.0.1):  /ws/ibkr -> 8765   /ws/webull -> 8766   /ws/scanner -> 8767
  * /api/algo/<webull|ibkr>  - the trading algos' latest status (read from their logs/ folders)
  * /api/algo/<webull|ibkr>/live  - Go Live / Stop Live (gateway/livectl.py); /api/algo/live: both, for the LIVE badges
  * /api/algo/<webull|ibkr>/options (POST) - saves the algo tab's Trading options panel to
                               <algo folder>/trading_options.json (checked first; see algo/options.py)
  * /api/health              - which services are up, for the dashboard's status dots
  * a password (JB_PASSWORD in settings.env): enter it once per device, it's remembered for a year

Because pages talk to the SAME address they were loaded from, nothing has to be typed into
them, and the same pages work over plain http (Tailscale) or https (Tailscale Serve/Funnel).
If you ever expose this beyond your own Tailscale devices (e.g. Tailscale Funnel), a password
is mandatory.
"""
import argparse
import asyncio
import errno
import gzip
import hashlib
import hmac
import html
import json
import mimetypes
import os
import posixpath
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BACKENDS = {'ibkr': 8765, 'webull': 8766, 'scanner': 8767}
ALGOS = {'webull': os.path.join(ROOT, 'webull-algo', 'logs'),
         'ibkr': os.path.join(ROOT, 'ibkr-algo', 'logs')}
PUBLIC = {'/login', '/favicon.ico', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png'}
PUBLIC_PREFIXES = ('/manifest', '/fonts/')
COOKIE = 'jb_auth'
MAX_HEAD = 32 * 1024
KEEPALIVE_S = 30                        # idle time before a browser connection is closed
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
         '.css': 'text/css; charset=utf-8', '.json': 'application/json',
         '.webmanifest': 'application/manifest+json', '.png': 'image/png', '.svg': 'image/svg+xml',
         '.ico': 'image/x-icon', '.txt': 'text/plain; charset=utf-8', '.woff2': 'font/woff2'}
COMPRESSIBLE = ('.html', '.js', '.css', '.json', '.svg', '.txt', '.webmanifest')
LONG_CACHE = ('.woff2', '.png')         # fonts/icons: cache for a week (pages/scripts: always revalidate)


class Gateway:
    def __init__(self, args):
        self.args = args
        self.web = os.path.realpath(args.web_dir)
        self.password = args.password or ''
        self.cookie_value = hmac.new(self.password.encode(), b'jb-auth-v1', hashlib.sha256).hexdigest() \
            if self.password else ''
        self.fails = {}                 # ip -> [timestamps] of failed logins
        self.started = time.time()
        self.files = {}                 # path -> (etag, body, gzipped body or None)
        self.health_cache = (0.0, None)

    # ------------------------------------------------------------ http helpers
    @staticmethod
    async def read_head(reader, timeout=15):
        data = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=timeout)
        if len(data) > MAX_HEAD:
            raise ValueError('header too large')
        lines = data.decode('latin-1').split('\r\n')
        method, target, version = lines[0].split(' ', 2)
        headers = {}
        for ln in lines[1:]:
            if ':' in ln:
                k, v = ln.split(':', 1)
                headers[k.strip().lower()] = v.strip()
        return method.upper(), target, headers, data

    @staticmethod
    async def respond(writer, status, body=b'', ctype='text/plain; charset=utf-8', extra=None,
                      head_only=False, keep=False):
        reasons = {200: 'OK', 204: 'No Content', 302: 'Found', 303: 'See Other', 304: 'Not Modified',
                   400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found',
                   405: 'Method Not Allowed', 206: 'Partial Content', 409: 'Conflict', 416: 'Range Not Satisfiable', 429: 'Too Many Requests', 502: 'Bad Gateway'}
        if isinstance(body, str):
            body = body.encode()
        hdrs = {'Content-Type': ctype, 'Content-Length': str(len(body)),
                'Connection': 'keep-alive' if keep else 'close',
                'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                'Content-Security-Policy': "frame-ancestors 'self'", 'Cache-Control': 'no-cache'}
        hdrs.update(extra or {})
        head = f'HTTP/1.1 {status} {reasons.get(status, "OK")}\r\n' + \
               ''.join(f'{k}: {v}\r\n' for k, v in hdrs.items()) + '\r\n'
        writer.write(head.encode('latin-1') + (b'' if head_only or status == 304 else body))
        try:
            await writer.drain()
        except Exception:
            pass
        return keep

    def authed(self, headers):
        if not self.password:
            return True
        for part in headers.get('cookie', '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == COOKIE and hmac.compare_digest(v, self.cookie_value):
                return True
        return False

    @staticmethod
    def client_ip(writer, headers):
        peer = (writer.get_extra_info('peername') or ('?',))[0]
        if peer in ('127.0.0.1', '::1') and headers.get('x-forwarded-for'):
            return headers['x-forwarded-for'].split(',')[-1].strip()     # jb-secure: the address Tailscale added, not one the visitor typed
        return peer

    # ------------------------------------------------------------ connection handler
    async def handle(self, reader, writer):
        """One browser connection. Plain page/API requests are answered in a loop on the same
        connection (HTTP keep-alive: faster over Tailscale, no new handshake per file); a
        WebSocket request hands the connection over to the proxy for good."""
        try:
            first = True
            while True:
                try:
                    method, target, headers, raw_head = await self.read_head(
                        reader, timeout=15 if first else KEEPALIVE_S)
                except Exception:
                    return
                first = False
                keep = self.wants_keepalive(raw_head, headers)
                if not await self.handle_one(method, target, headers, raw_head, reader, writer, keep):
                    return
        except Exception as e:
            print(f'gateway error: {e!r}', flush=True)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    def wants_keepalive(raw_head, headers):
        version = raw_head.split(b'\r\n', 1)[0].rsplit(b' ', 1)[-1]
        conn = headers.get('connection', '').lower()
        if version == b'HTTP/1.1':
            return 'close' not in conn
        return 'keep-alive' in conn

    def same_origin(self, headers):
        """Browsers send Origin on WebSocket and POST requests. Refuse ones started by some
        other website (defence in depth on top of the SameSite login cookie)."""
        origin = headers.get('origin')
        if not origin:
            return True
        if origin == 'null':          # review 2026-10-11 #13: a hidden frame on another site sends 'null': refuse it
            return False
        host = urllib.parse.urlsplit(origin).netloc.lower()
        return host == headers.get('host', '').lower() or host == headers.get('x-forwarded-host', '').lower()

    async def handle_one(self, method, target, headers, raw_head, reader, writer, keep):
        """Answer one request. Returns True if the connection can be reused."""
        url = urllib.parse.urlsplit(target)
        raw_path = urllib.parse.unquote(url.path)
        path = posixpath.normpath('/' + raw_path.lstrip('/'))      # '/fonts/../x' -> '/x' before any checks
        if raw_path.endswith('/') and path != '/':
            path += '/'
        query = urllib.parse.parse_qs(url.query)
        is_ws = headers.get('upgrade', '').lower() == 'websocket'

        # (the login form itself arrives with Origin "null": the pages' Referrer-Policy is no-referrer, and browsers
        # then hide the origin of a form POST. Allowed for /login only -- all anyone can do there is try a password)
        login_form = method == 'POST' and path == '/login' and headers.get('origin') == 'null'
        if (is_ws or method == 'POST') and not self.same_origin(headers) and not login_form:
            return await self.respond(writer, 403, 'cross-site request refused')
        if (is_ws or method == 'POST') and not self.password and not _home_host(headers.get('host', '')):
            # without a password, only accept changes addressed to this Mac by an IP address or a
            # local/Tailscale name: a web page elsewhere can't then reach it through its own domain
            return await self.respond(writer, 403, 'unknown host name -- set JB_PASSWORD to use custom names')
        if method == 'POST' and not self.password and path.startswith(('/api/kill/', '/api/algo/', '/api/plus', '/api/trading')):
            # review 2026-10-11 #13: with no password anyone on the Wi-Fi could press SELL ALL or send orders
            return await self.respond(writer, 403, 'Set JB_PASSWORD in settings.env (then ./jb restart gateway) to trade, '
                                                   'press SELL ALL or change the algos from the board.')
        if path == '/api/tv':      # Roku TV view (gateway/tvview.py, jb-roku): read only, its own TV key, home Wi-Fi or ts.net
            import tvview
            code, body = await asyncio.get_running_loop().run_in_executor(None, tvview.answer, method, query, headers)
            return await self.respond(writer, code, body, 'application/json', head_only=(method == 'HEAD'), keep=keep)
        if path == '/api/tvchannel':   # Alex's channel on the Roku (gateway/channel.py, jb-channel): read only, the TV key
            import channel
            code, body, ctype, extra = await asyncio.get_running_loop().run_in_executor(None, channel.tv, method, query, headers)
            return await self.respond(writer, code, body, ctype, extra=extra, head_only=(method == 'HEAD'), keep=keep)
        if path == '/login':
            return await self.login(method, reader, writer, headers, query, keep)

        if path == '/api/widget':     # iPhone widget / Siri (gateway/tools.py): read-only, needs its own key
            import tools
            code, body = await asyncio.get_running_loop().run_in_executor(None, tools.widget, query)
            return await self.respond(writer, code, json.dumps(body), 'application/json', keep=keep)
        if not self.authed(headers) and path not in PUBLIC and not path.startswith(PUBLIC_PREFIXES):
            if is_ws or path.startswith('/api/'):
                return await self.respond(writer, 401, 'login required', keep=keep and not is_ws)
            nxt = urllib.parse.quote(target, safe='')
            return await self.respond(writer, 302, extra={'Location': f'/login?next={nxt}'}, keep=keep)

        if path.startswith('/ws/') and is_ws:
            await self.proxy_ws(path[4:].strip('/'), raw_head, target, reader, writer)
            return False
        if path in ('/api/holdings', '/api/portfolio/backtest', '/api/volume'):   # Portfolio tab (gateway/portfolio.py)
            import portfolio
            if path == '/api/holdings' and method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 4096 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = portfolio.post(data)
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            if method not in ('GET', 'HEAD'):
                return await self.respond(writer, 405, 'method not allowed')
            if path == '/api/holdings':
                body = portfolio.holdings_json()
            elif path == '/api/volume':
                body = portfolio.volume(url.query)
            else:
                body = await asyncio.get_running_loop().run_in_executor(None, portfolio.backtest, url.query)
            return await self.respond(writer, 200, body, 'application/json', keep=keep)
        if path in ('/api/signals', '/api/why'):     # the Signals tab + WHY? button (gateway/signals.py)
            import signals
            if path == '/api/signals' and method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 4096 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = await asyncio.get_running_loop().run_in_executor(None, signals.post, data)
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            if path == '/api/why':
                body = await asyncio.get_running_loop().run_in_executor(None, signals.why, query)
                return await self.respond(writer, 200, body, 'application/json', keep=keep)
            return await self.respond(writer, 200, signals.snapshot(), 'application/json', keep=keep)
        if path == '/api/ranges':     # 1W / 52W range bars on the boards (gateway/ranges.py)
            import ranges
            body = await asyncio.get_running_loop().run_in_executor(None, ranges.snapshot, query)
            return await self.respond(writer, 200, body, 'application/json', keep=keep)
        if path in ('/api/analysis', '/api/analysis/options'):   # Analysis tab (gateway/analysis.py)
            import analysis
            if path == '/api/analysis/options':
                body = await asyncio.get_running_loop().run_in_executor(None, analysis.option_one, query)
            else:
                body = await asyncio.get_running_loop().run_in_executor(None, analysis.snapshot)
            return await self.respond(writer, 200, body, 'application/json', keep=keep)
        if path in ('/api/trading', '/api/trading/leaderboard', '/api/trading/replay'):   # My trading tab (gateway/trading.py)
            import trading
            run = asyncio.get_running_loop().run_in_executor
            if path == '/api/trading' and method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 8192 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = await run(None, trading.post, data)
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            if path == '/api/trading/leaderboard':
                body = await run(None, lambda: json.dumps(trading.leaderboard()))
            elif path == '/api/trading/replay':
                body = await run(None, trading.replay, query)
            else:
                body = await run(None, trading.snapshot)
            return await self.respond(writer, 200, body, 'application/json', keep=keep)
        if path in ('/api/phone', '/api/health/page', '/api/health/restart'):   # Phone page + Health tab (gateway/extras.py)
            import extras
            run = asyncio.get_running_loop().run_in_executor
            if path == '/api/health/restart':
                if method != 'POST':
                    return await self.respond(writer, 405, 'method not allowed')
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 1024 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = await run(None, extras.restart, str(data.get('name', '')))
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            if method not in ('GET', 'HEAD'):
                return await self.respond(writer, 405, 'method not allowed')
            body = await run(None, extras.phone if path == '/api/phone' else extras.health_page)
            return await self.respond(writer, 200, body, 'application/json', keep=keep)
        if path.startswith('/api/kill/'):           # SELL ALL buttons (gateway/killswitch.py)
            import killswitch
            run = asyncio.get_running_loop().run_in_executor
            who = path[len('/api/kill/'):].strip('/')
            if method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 1024 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = await run(None, killswitch.post, who, data)
            elif method in ('GET', 'HEAD'):
                code, body = await run(None, killswitch.preview, who)
            else:
                return await self.respond(writer, 405, 'method not allowed')
            return await self.respond(writer, code, body, 'application/json', keep=keep)
        if path in ('/api/recap', '/api/recap/video'):    # Recap tab (gateway/recap.py)
            import recap
            if path == '/api/recap/video' and method in ('GET', 'HEAD'):
                code, body, extra = await asyncio.get_running_loop().run_in_executor(None, recap.video, query, headers.get('range', ''))
                return await self.respond(writer, code, body, 'video/mp4' if code in (200, 206) else 'text/plain; charset=utf-8',
                                          extra=extra, head_only=(method == 'HEAD'), keep=keep)
            if path == '/api/recap' and method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 4096 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = recap.post(data)
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            if path == '/api/recap' and method in ('GET', 'HEAD'):
                return await self.respond(writer, 200, recap.status(), 'application/json', keep=keep)
            return await self.respond(writer, 405, 'method not allowed')
        if path == '/api/channel/voices':   # Alex's channel voices page (web/channel-voices.html, jb-anchor)
            import channel
            data = None
            if method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 4096 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
            code, body, ctype, extra = await asyncio.get_running_loop().run_in_executor(None, channel.voices, method, query, data)
            return await self.respond(writer, code, body, ctype, extra=extra, head_only=(method == 'HEAD'), keep=keep)
        if path in ('/api/channel', '/api/channel/audio'):   # Alex's channel page (web/channel.html, jb-channel): read only
            import channel
            code, body, ctype, extra = await asyncio.get_running_loop().run_in_executor(None, channel.web, method, path, query, headers)
            return await self.respond(writer, code, body, ctype, extra=extra, head_only=(method == 'HEAD'), keep=keep)
        if path.startswith('/api/plus/') or path == '/api/plus':   # Night desk add-ons (gateway/plus.py)
            import plus
            data = None
            if method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 65536 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
            elif method not in ('GET', 'HEAD'):
                return await self.respond(writer, 405, 'method not allowed')
            code, body, ctype = await asyncio.get_running_loop().run_in_executor(None, plus.handle, method, path[len('/api/plus'):], query, data)
            return await self.respond(writer, code, body, ctype, keep=keep)
        if path.startswith('/api/'):
            return await self.api(method, path, headers, reader, writer, keep)
        if method not in ('GET', 'HEAD'):
            return await self.respond(writer, 405, 'method not allowed')
        return await self.static(path, headers, writer, head_only=(method == 'HEAD'), keep=keep)

    # ------------------------------------------------------------ login
    async def login(self, method, reader, writer, headers, query, keep=False):
        nxt = (query.get('next') or ['/'])[0]
        if not _safe_next(nxt):
            nxt = '/'
        if not self.password:
            return await self.respond(writer, 302, extra={'Location': nxt}, keep=keep)
        error = ''
        if method == 'POST':
            ip = self.client_ip(writer, headers)
            now = time.time()
            recent = [t for t in self.fails.get(ip, []) if now - t < 300]
            if len(recent) >= 5:
                return await self.respond(writer, 429, 'Too many wrong passwords -- wait 5 minutes.')
            n = int(headers.get('content-length', '0') or 0)
            if n > 4096:
                return await self.respond(writer, 400, 'bad request')
            body = (await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8', 'replace') if n else ''
            form = urllib.parse.parse_qs(body)
            pw = (form.get('password') or [''])[0]
            nxt = (form.get('next') or [nxt])[0]
            if not _safe_next(nxt):
                nxt = '/'
            if hmac.compare_digest(_plain_quotes(pw).encode(), _plain_quotes(self.password).encode()):
                self.fails.pop(ip, None)
                cookie = f'{COOKIE}={self.cookie_value}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax'
                return await self.respond(writer, 303, extra={'Location': nxt, 'Set-Cookie': cookie})
            recent.append(now)
            self.fails[ip] = recent
            error = 'Wrong password.'
        page = LOGIN_HTML.replace('{{next}}', html.escape(nxt, quote=True)).replace('{{error}}', error)
        return await self.respond(writer, 200, page, 'text/html; charset=utf-8', keep=keep)

    # ------------------------------------------------------------ websocket proxy
    async def proxy_ws(self, name, raw_head, target, reader, writer):
        port = BACKENDS.get(name)
        if not port:
            return await self.respond(writer, 404, 'no such service')
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), timeout=3)
        except Exception:
            return await self.respond(writer, 502, f'{name} service is not running')
        # forward the browser's upgrade request unchanged except for the path
        head = raw_head.decode('latin-1')
        first, rest = head.split('\r\n', 1)
        m, _, v = first.split(' ', 2)
        up_w.write(f'{m} / {v}\r\n{rest}'.encode('latin-1'))

        async def pump(src, dst):
            try:
                while True:
                    chunk = await src.read(65536)
                    if not chunk:
                        break
                    dst.write(chunk)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        await asyncio.gather(pump(reader, up_w), pump(up_r, writer))

    # ------------------------------------------------------------ api
    async def api(self, method, path, headers, reader, writer, keep=False):
        if path.startswith('/api/algo/') and path.rstrip('/').endswith('/live'):      # Go Live / Stop Live (gateway/livectl.py)
            import livectl
            who = path[len('/api/algo/'):].rstrip('/')[:-len('live')].strip('/')
            run = asyncio.get_running_loop().run_in_executor
            if not who:
                code, body = livectl.summary()
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            if who not in livectl._dirs():
                return await self.respond(writer, 404, 'no such algo', keep=keep)
            if method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 2048 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = await run(None, livectl.post, who, data)
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            if method not in ('GET', 'HEAD'):
                return await self.respond(writer, 405, 'method not allowed')
            code, body = await run(None, livectl.get, who)
            return await self.respond(writer, code, body, 'application/json', keep=keep)
        if path.startswith('/api/algo/') and path.rstrip('/').endswith('/control'):   # Run backtest, Stop / Resume (gateway/algoctl.py)
            import algoctl
            who = path[len('/api/algo/'):].rstrip('/')[:-len('/control')].strip('/')
            if who not in algoctl.DIRS:
                return await self.respond(writer, 404, 'no such algo', keep=keep)
            if method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 2048 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = await asyncio.get_running_loop().run_in_executor(None, algoctl.post, who, data)
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            return await self.respond(writer, 200, algoctl.info(who), 'application/json', keep=keep)
        if path == '/api/watch':                     # alerts + morning card (gateway/watch.py)
            import watch
            if method == 'POST':
                try:
                    n = int(headers.get('content-length', '0') or 0)
                    data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8')) if 0 < n <= 4096 else None
                except Exception:
                    data = None
                if not isinstance(data, dict):
                    return await self.respond(writer, 400, 'bad request')
                code, body = await asyncio.get_running_loop().run_in_executor(None, watch.post, data)
                return await self.respond(writer, code, body, 'application/json', keep=keep)
            return await self.respond(writer, 200, watch.snapshot(), 'application/json', keep=keep)
        if path.startswith('/api/algo/') and path.rstrip('/').endswith('/options'):
            return await self.save_options(method, path, headers, reader, writer, keep)
        if method not in ('GET', 'HEAD'):
            return await self.respond(writer, 405, 'method not allowed')
        if path == '/api/world':                     # the World tab (gateway/world.py)
            import world
            return await self.respond(writer, 200, world.snapshot(), 'application/json', keep=keep)
        if path == '/api/news':                      # the News tab (gateway/news.py)
            import news
            body, extra = news.response(headers)
            return await self.respond(writer, 200, body, 'application/json', extra=extra, keep=keep)
        if path == '/api/health':
            ts, cached = self.health_cache
            if cached is not None and time.time() - ts < 4:   # several open dashboards share one check
                return await self.respond(writer, 200, cached, 'application/json', keep=keep)
            out = {'gateway': {'up': True, 'uptime_s': int(time.time() - self.started)}}
            ups = await asyncio.gather(*(_port_open(port) for port in BACKENDS.values()))
            for name, up in zip(BACKENDS, ups):
                out[name] = {'up': up}
            for name, d in ALGOS.items():
                age = _age(os.path.join(d, 'live_status.json'))
                out['algo_' + name] = {'up': age is not None and age < 180, 'status_age_s': age,
                                       'backtest_age_s': _age(os.path.join(d, 'backtest_status.json'))}
            enabled = [x.strip() for x in os.environ.get('JB_SERVICES', '').split(',') if x.strip()]
            if enabled:
                for k in out:
                    if k != 'gateway':
                        out[k]['enabled'] = k in enabled or (k.startswith('algo_') and out[k].get('backtest_age_s') is not None)
            body = json.dumps(out)
            self.health_cache = (time.time(), body)
            return await self.respond(writer, 200, body, 'application/json', keep=keep)
        if path.startswith('/api/algo/'):
            name = path[len('/api/algo/'):].strip('/')
            d = ALGOS.get(name)
            if not d:
                return await self.respond(writer, 404, 'no such algo', keep=keep)
            live = _read_json(os.path.join(d, 'live_status.json'))
            bt = _read_json(os.path.join(d, 'backtest_status.json'))
            out = {'live': live, 'backtest': bt, 'server_ts': time.time(),
                   'live_age_s': _age(os.path.join(d, 'live_status.json'))}
            if name == 'webull':          # is Webull's overnight data on right now? (hedge-2026-10-12; the ? Legend shows it)
                try:
                    import jb_webull
                    out['overnight_data'] = jb_webull.overnight_state()
                except Exception:
                    out['overnight_data'] = None
            mod = _options_module()
            if mod is not None:
                saved, problem = mod.load_file(mod.path_for(os.path.dirname(d)))
                out.update(options_saved=saved, options_problem=problem, options_defaults=mod.DEFAULTS)
            return await self.respond(writer, 200, json.dumps(out), 'application/json', keep=keep)
        return await self.respond(writer, 404, 'not found', keep=keep)

    async def save_options(self, method, path, headers, reader, writer, keep=False):
        """POST {"options": {...}} saves the Trading options panel; {"reset": true} deletes the
        saved file so the strategy file's trading: section (or the defaults) apply again."""
        name = path[len('/api/algo/'):].rstrip('/')[:-len('/options')].strip('/')
        d = ALGOS.get(name)
        if not d:
            return await self.respond(writer, 404, 'no such algo')
        if method != 'POST':
            return await self.respond(writer, 405, 'method not allowed')
        if not headers.get('content-type', '').lower().startswith('application/json'):
            return await self.respond(writer, 400, 'expected JSON')
        try:
            n = int(headers.get('content-length', '0') or 0)
        except ValueError:
            n = -1
        if not 0 < n <= 8192:
            return await self.respond(writer, 400, 'bad request')
        try:
            data = json.loads((await asyncio.wait_for(reader.readexactly(n), timeout=10)).decode('utf-8'))
        except Exception:
            return await self.respond(writer, 400, 'expected JSON')
        mod = _options_module()
        if mod is None or not isinstance(data, dict):
            return await self.respond(writer, 400 if mod else 502, 'bad request' if mod else 'options module missing')
        target = mod.path_for(os.path.dirname(d))
        try:
            if data.get('reset') is True:
                try:
                    os.remove(target)
                except FileNotFoundError:
                    pass
                saved = None
            elif not isinstance(data.get('options'), dict) or not data['options']:
                # an empty or missing "options" would save the defaults over your settings (review 2026-10-11, second check)
                return await self.respond(writer, 400, json.dumps({'error': 'no options sent: nothing was changed'}),
                                          'application/json', keep=keep)
            else:
                saved = mod.save_file(target, data.get('options'))
        except mod.OptionsError as e:
            return await self.respond(writer, 400, json.dumps({'error': str(e)}), 'application/json', keep=keep)
        except OSError as e:
            return await self.respond(writer, 502, json.dumps({'error': f'could not save: {e}'}), 'application/json')
        print(f'trading options for {name} ' + ('reset to the strategy file' if saved is None else 'saved'), flush=True)
        return await self.respond(writer, 200, json.dumps({'ok': True, 'options_saved': saved}),
                                  'application/json', keep=keep)

    # ------------------------------------------------------------ static files
    async def static(self, path, headers, writer, head_only=False, keep=False):
        if path.endswith('/'):
            path += 'index.html'
        if path == '/favicon.ico':
            path = '/icon-192.png'
        full = os.path.realpath(os.path.join(self.web, path.lstrip('/')))
        if not (full == self.web or full.startswith(self.web + os.sep)) or not os.path.isfile(full) \
                or os.path.basename(full).startswith('.'):
            return await self.respond(writer, 404, 'not found', keep=keep)
        etag, body, zipped = self.load(full)
        ext = os.path.splitext(full)[1].lower()
        extra = {'ETag': etag, 'Vary': 'Accept-Encoding'}
        if ext in LONG_CACHE:
            extra['Cache-Control'] = 'public, max-age=604800'
        if headers.get('if-none-match') == etag:
            return await self.respond(writer, 304, extra=extra, keep=keep)
        ctype = TYPES.get(ext) or mimetypes.guess_type(full)[0] or 'application/octet-stream'
        if zipped is not None and 'gzip' in headers.get('accept-encoding', ''):
            body = zipped
            extra['Content-Encoding'] = 'gzip'
        return await self.respond(writer, 200, body, ctype, extra=extra, head_only=head_only, keep=keep)

    def load(self, full):
        """File contents (and a gzipped copy for text) cached in memory until the file changes."""
        st = os.stat(full)
        etag = '"%x-%x"' % (int(st.st_mtime_ns // 1000), st.st_size)
        hit = self.files.get(full)
        if hit and hit[0] == etag:
            return hit
        with open(full, 'rb') as f:
            body = f.read()
        zipped = None
        if full.lower().endswith(COMPRESSIBLE) and len(body) > 1024:
            zipped = gzip.compress(body, 6, mtime=0)
        self.files[full] = (etag, body, zipped)
        return self.files[full]

    async def run(self):
        server = await asyncio.start_server(self.handle, self.args.bind, self.args.port,
                                            limit=MAX_HEAD + 1024)
        print(f'gateway serving {self.web} on http://{self.args.bind}:{self.args.port}/ '
              f'(password {"ON" if self.password else "OFF"})', flush=True)
        if not self.password:
            print('WARNING: no JB_PASSWORD set -- anyone who can reach this Mac can open the boards. '
                  'Fine on your own Tailscale network; never expose it further without one.', flush=True)
        import watch; watch.start()          # alerts + morning cards (gateway/watch.py)
        import recap; recap.start()          # the morning recap video (gateway/recap.py)
        import world; world.start()          # World tab: futures, Asia/Europe, mood, calendar (gateway/world.py)
        import signals; signals.start()      # Signals tab: smarter alerts, SEC, buzz, summary (gateway/signals.py)
        import analysis; analysis.start()    # Analysis tab: gaps, sessions, heatmap, strength, options (gateway/analysis.py)
        import trading; trading.start()      # My trading tab: journal, loss limit, leaderboard, replay (gateway/trading.py)
        import extras; extras.start()        # market weather, phone page, Health tab (gateway/extras.py)
        import plus; plus.start()            # Night desk add-ons (gateway/plus.py)
        async with server:
            await server.serve_forever()


def _home_host(host):
    """An IP address, localhost, a single-word name (the Mac's own name, Tailscale MagicDNS) or a
    .ts.net / .local / .lan / .internal / .home.arpa name."""
    import ipaddress
    h = host.strip().lower()
    if h.startswith('['):
        h = h[1:h.find(']')] if ']' in h else h[1:]
    elif h.count(':') == 1:
        h = h.split(':', 1)[0]
    if not h:
        return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    h = h.rstrip('.')
    return h == 'localhost' or '.' not in h or h.endswith(('.ts.net', '.local', '.lan', '.internal', '.home.arpa'))


def _safe_next(nxt):
    """Only a path on this site: '/x' -- not '//evil.com' or '/\\evil.com' (browsers read both
    as another site), and nothing with control characters."""
    return (nxt.startswith('/') and not nxt.startswith('//') and '\\' not in nxt
            and not any(ord(ch) < 32 for ch in nxt))


def _plain_quotes(s):
    """Phones and TextEdit like to turn ' and " into curly quotes; treat them the same."""
    return (s.replace('\u2018', "'").replace('\u2019', "'")
             .replace('\u201c', '"').replace('\u201d', '"'))


async def _port_open(port):
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection('127.0.0.1', port), timeout=0.6)
        w.close()
        return True
    except Exception:
        return False


def _age(p):
    try:
        return int(time.time() - os.path.getmtime(p))
    except OSError:
        return None


_OPTIONS_MOD = []


def _options_module():
    """algo/options.py (the same file in both algos), loaded once; None if neither is there."""
    if _OPTIONS_MOD:
        return _OPTIONS_MOD[0]
    import importlib.util
    for algo in ('webull-algo', 'ibkr-algo'):
        p = os.path.join(ROOT, algo, 'algo', 'options.py')
        if os.path.isfile(p):
            try:
                spec = importlib.util.spec_from_file_location('jb_trading_options', p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _OPTIONS_MOD.append(mod)
                return mod
            except Exception as e:
                print(f'could not load {p}: {e!r}', flush=True)
    return None


def _read_json(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="theme-color" content="#04101f">
<title>Alex Hale's Board</title><link rel="icon" href="/icon-192.png"><link rel="stylesheet" href="/fonts/plex.css"><style>
:root{--bg:#04101f;--panel:#071a30;--line:#164060;--green:#2f7a44;--text:#f2f2f2;--dim:#6f8aa3;--red:#ff3b30}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:var(--bg);color:var(--text);font-family:'IBM Plex Mono',ui-monospace,'SF Mono',Menlo,monospace;padding:16px}
form{width:100%;max-width:340px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:22px}
h1{font-size:15px;letter-spacing:.08em;margin:0 0 16px}label{font-size:11px;color:var(--dim);display:block;margin-bottom:6px}
input{width:100%;font:inherit;font-size:16px;padding:11px;border-radius:6px;border:1px solid var(--line);background:#000;color:var(--text)}
button{margin-top:14px;width:100%;font:inherit;font-weight:700;padding:11px;border:0;border-radius:6px;background:var(--green);color:#fff}
.err{color:var(--red);font-size:12px;min-height:16px;margin-top:10px}</style></head><body>
<form method="post" action="/login"><h1>ALEX HALE'S BOARD</h1><label for="pw">Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" autofocus>
<input type="hidden" name="next" value="{{next}}"><button type="submit">Open</button><div class="err">{{error}}</div></form>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Joseph's Board gateway")
    ap.add_argument('--bind', default=os.environ.get('JB_GATEWAY_BIND') or '0.0.0.0')
    ap.add_argument('--port', type=int, default=int(os.environ.get('JB_GATEWAY_PORT') or 8090))
    ap.add_argument('--web-dir', default=os.path.join(ROOT, 'web'))
    ap.add_argument('--password', default=os.environ.get('JB_PASSWORD', ''))
    args = ap.parse_args()
    try:
        asyncio.run(Gateway(args).run())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        print(f'port {args.port} on this Mac is already in use -- is another copy of this service already running? '
              f'(./jb status shows what is running; ./jb restart fixes most cases)', flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
