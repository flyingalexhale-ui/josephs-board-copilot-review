# Reviewer G findings (security, gateway core, widgets, TV/Channel/Roku), saved by the thread from G's handback, 2026-10-17
Gateway really run on 127.0.0.1:9220 (JB_PASSWORD=testpw, data2 dir, bridges/algos off). Tests rev/G/tests, logs rev/G/logs.
Traced SAFE: static path traversal (realpath prefix + normpath); command injection (list-args subprocess everywhere incl channel/piper/recap/algoctl); XSS on display pages (consistent esc()); hmac.compare_digest for password/cookie/widget key/tv key; chartpage symbol regex; CSRF/Origin (cross-site + null Origin POST/WS refused; null only POST /login; no-Origin allowed by design with SameSite=Lax backstop); no-password-mode trade guards; SSRF only alerts2.save_heartbeat_url behind auth. /api/tv, /api/tvchannel 403 without key, no sleep path.
HIGH
- G-HIGH-1 login rate limit bypassed by rotating X-Forwarded-For behind Funnel (peer is loopback so client_ip trusts last XFF, gateway.py ~115-118; login ~368-395 5/IP/5min); no alert on failed logins. CONFIRMED g1_login_bruteforce.py 2000 guesses in 1.1 s (~1887/s), 0 refusals. Borderline CRITICAL: password is only defence for trading over public link.
- G-HIGH-2 unauthenticated /api/widget?key=wrong does time.sleep(1) (tools.py:144) inside shared default executor (gateway.py:201, run_in_executor(None)) -> flood stalls SELL ALL preview / kill switch / tickets / Go Live. CONFIRMED g2_widget_dos.py: authed /api/plus and /api/kill/ibkr 0.00 s -> 7-8 s with 64 threads.
- G-HIGH-3 same limiter: 5 wrong logins from the shared loopback bucket (no XFF) or the owner's spoofed address lock the owner out for 5 min, on demand. CONFIRMED.
MEDIUM
- G-MED-1 auth cookie no Secure flag (login Set-Cookie ~383; Max-Age 1 year, HttpOnly, SameSite=Lax) -> sent over plaintext LAN http. CONFIRMED inspection.
- G-MED-2 cookie = HMAC(password, b'jb-auth-v1'), identical every login, never rotates, no logout/revocation; leak (LAN sniff, log, backup) = permanent trade-capable credential until password changes. CONFIRMED inspection.
- G-MED-3 keep-alive desync: GET/HEAD handlers don't drain Content-Length body; next read_head parses it as a new request (read_head ~69-81; loop ~128-137; e.g. /api/phone 295-298). CONFIRMED g4_desync.py (one request -> two responses). Still passes auth; cross-client smuggling via Funnel pooling unverified.
- G-MED-4 self.fails dict keyed by XFF-controlled IP never evicts keys. CONFIRMED g1b_fails_memory.py 63 -> 111 MB per 200k addresses.
- G-MED-5 widget key is a long-lived bearer secret in the URL (logged by proxies, saved in iOS, printed on Night Desk setup page), never rotates, returns P&L, open positions, feed status, health without login (tools.py widget ~140-240, widget_key ~122-126). CONFIRMED.
LOW
- G-LOW-1 widget "feed Down" driven by health warn not price staleness (rate-limit or unset JB_SEC_EMAIL/WEBULL_ACCOUNT_ID pins amber, cf E18). TRACED.
- G-LOW-2 widget times use phone local TZ, no ET label. TRACED.
- G-LOW-3 widget chart plots by array index not time (gaps evenly spaced; fallback mixes cadences). TRACED.
- G-LOW-4 Lock Screen accessoryCircular never renders "Board Down" (error/no data = blank/zero ring looks like flat day); widget_setup lock copy promises health dot/P&L the script never draws. TRACED. (Note: Alex confirmed on 2026-10-14 the lock screen shows "IBKR Up . time . Webull Up" and red Board Down was intended on both widgets - verify against the jb-widgettime code.)
- G-LOW-5 widget "Up X today" uses whole open P&L (out['pnl']=out['pnl_today'], fed from extras.py:460, cf C10). TRACED.
- G-LOW-6 Roku can play a silent segment from a stale clip hash (ChannelView.brs never clears m.chClips; ChannelTask keeps 24 newest files). TRACED.
- G-LOW-7 channel.py _mem['v'] accumulates mv:/fut: keys unbounded. TRACED.
- G-LOW-8 tvview._bars() min/max over empty week raises ValueError -> /api/tv error for new/illiquid symbol or long outage. TRACED.
- G-LOW-9 Roku learnHome accepts a new home URL from any response (LAN spoof). TRACED.
- G-LOW-10 stale "Holidays are not known" badge title in web/board.js. TRACED.
Housekeeping: /home/claude/josephs-board/data/webull_overnight.json + .lock were created by some reviewer's run (not G); left untouched.
