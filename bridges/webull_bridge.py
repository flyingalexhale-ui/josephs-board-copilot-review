#!/usr/bin/env python3
"""Webull bridge for Joseph's Board: Webull OpenAPI -> your board pages.

Runs always-on on the spare Mac (./install.sh sets it up as a launchd job). It streams every
symbol on the Webull board's watchlist 24/7 through Webull's official OpenAPI SDK
(webull-openapi-python-sdk), records every price to data/webull_ticks.json, and serves the
board pages through the gateway at http://<spare-mac>:8090/.

Written against the REAL SDK source (webull-inc/webull-openapi-python-sdk 3.0.x):
  * streamed trades arrive as TickResult objects on the SDK's own network thread:
    .basic.symbol, .basic.trading_session, .time (epoch ms as text), .price, .volume
  * callbacks must never raise -- the SDK re-raises and reconnects -- so every callback here
    just hands its data to the asyncio loop and returns immediately
  * get_history_bar() now calls the batch endpoint and returns [{symbol, result:[bars]}];
    futures history has its own endpoint (futures_market_data.get_futures_history_bars)
  * Webull names futures by CONTRACT (ESZ6, not ES): each ES=F on the board is mapped to the
    contract trading most, re-checked every 4 hours; when it changes (a roll) the stream
    switches and the history is adjusted by the gap between the two contracts
  * Webull limits subscribe/unsubscribe to 10 calls per minute per App Key (shared with the
    scanner and the Webull algo if they run too) and 100 symbols x data-types per App Key;
    subscription changes here are batched and paced to stay under that.

Needs WEBULL_APP_KEY / WEBULL_APP_SECRET (developer.webull.com -> API Management). Market data
through the API is its own subscription on Webull's side, separate from the Webull app's.
"""
import argparse
import asyncio
import errno
import collections
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, 'lib'))
from common import BridgeServer, TickStore, Watchlist, is_futures, now_ms  # noqa: E402
import jb_webull  # noqa: E402

DEFAULT_WATCHLIST = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA',
                     'ES=F', 'NQ=F', 'YM=F', 'RTY=F', 'CL=F', 'GC=F']
SUB_TYPES = ['TICK']              # trade-by-trade prices; 1 "topic" per symbol
MAX_TOPICS = 100
CALLS_PER_MIN = 6                 # leave headroom under Webull's 10/min for the scanner/algo
ROLL_CHECK_S = 4 * 3600
STALL_S = 180                     # no Webull price at all for this long (4am-8pm trading days): reconnect
STALL_OVERNIGHT_S = 900           # ...overnight (8pm-4am before a trading day, plans with overnight prices): thinner trading
OVERNIGHT_SESSIONS = ('OVERNIGHT', 'OVN', 'NIGHT', 'OVERNIGHT_SESSION')
NOT_SUBSCRIBED_MSG = {
    'US_FUTURES': 'no Webull futures data subscription -- see this symbol on the IBKR board',
    'US_STOCK': 'no Webull stock data subscription for this symbol',
}


OVERNIGHT_NOTICE_KEY = '__overnight__'      # a board-wide notice, replayed to every page that opens
OVERNIGHT_NOTICE = ('No overnight prices here: your Webull plan does not include the overnight session '
                    '(8 pm - 4 am ET), so these stocks stop updating overnight. The IBKR board shows '
                    'overnight prices. Checked again every 3 hours and at 7:45 pm ET: once the plan has it, '
                    'overnight prices start by themselves.')


def is_not_subscribed(e):
    """Webull refused a subscription because the account has no market data for it
    (e.g. 403 MARKET_DATA_NOT_SUBSCRIBED ... please subscribe to US_FUTURES)."""
    s = ' '.join(str(x) for x in (jb_webull.describe_error(e), getattr(e, 'error_code', ''),
                                   getattr(e, 'error_msg', ''))).upper()
    return 'MARKET_DATA_NOT_SUBSCRIBED' in s or 'INSUFFICIENT PERMISSION' in s


def is_overnight_refused(e):
    """...because only the overnight session isn't subscribed ("please subscribe to US_STOCK OVERNIGHT")."""
    return is_not_subscribed(e) and 'OVERNIGHT' in jb_webull.describe_error(e).upper()


def webull_streams_now(now=None):
    """Weekdays 4:00 am - 8:00 pm New York time: when Webull stocks trade without the overnight
    session (not on market holidays), so a stream with no prices at all for minutes has gone quiet."""
    from datetime import datetime, timedelta, timezone
    try:
        from zoneinfo import ZoneInfo
        d = datetime.fromtimestamp(now or time.time(), ZoneInfo('America/New_York'))
    except Exception:                  # no time-zone data: close enough (US summer time)
        d = datetime.fromtimestamp(now or time.time(), timezone(timedelta(hours=-4)))
    try:
        import nyse_hours               # market holidays and 1 pm early closes (lib/nyse_hours.py)
        return nyse_hours.stocks_trade(d)
    except ImportError:
        return d.weekday() < 5 and 4 <= d.hour < 20


def overnight_now(now=None):
    """8 pm - 4 am New York time the night before a trading day: Webull's overnight session."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    import nyse_hours
    d = datetime.fromtimestamp(now or time.time(), ZoneInfo('America/New_York'))
    if d.hour >= 20:
        return nyse_hours.trading_day(d.date() + timedelta(days=1))
    return d.hour < 4 and nyse_hours.trading_day(d.date())


def root_of(sym):
    return sym[:-2] if is_futures(sym) else sym


def to_points(rows, span_s):
    """OHLCV rows (bar start times) -> page points stamped at each bar's CLOSE."""
    now = now_ms()
    add = span_s * 1000 if span_s < 86400 else int(6.5 * 3600 * 1000)     # daily: 4pm ET close
    return [{'t': min(r['t'] + add, now), 'p': r['close']} for r in rows]


# ---------------------------------------------------------------- bridge
class WebullBridge:
    def __init__(self, args):
        self.args = args
        self.data_client = None          # created in init_worker (may wait for Webull approval)
        d = args.data_dir
        self.store = TickStore(os.path.join(d, 'webull_ticks.json'),
                               legacy_paths=['~/.josephs_board_webull_ticks.json'])
        self.watchlist = Watchlist(os.path.join(d, 'webull_watchlist.json'), DEFAULT_WATCHLIST,
                                   legacy_path='~/.josephs_board_webull_watchlist.json')
        self.server = BridgeServer('webull', self.store, self.watchlist, token=args.token,
                                   on_add=self.on_change, on_remove=self.on_change,
                                   history=self.history)
        self.server.on_follow = self.on_follow          # the scanner and the Webull algo stream through here
        self.server.on_unfollow = lambda: self.schedule_sync(1.5)
        self.stream_client = None
        self.mqtt_up = False
        self.subscribed = {}              # wire symbol (AAPL, ESZ6) -> category, on the CURRENT session
        self.contracts = {}               # 'ES=F' -> 'ESZ6' (Webull's name for the current contract)
        self.by_wire = {}                 # 'ESZ6' -> 'ES=F'
        self._resync = False
        self.calls = collections.deque()  # timestamps of subscribe/unsubscribe calls
        self._sync_handle = None
        self._syncing = False
        self.stop = threading.Event()
        self.hist_lock = asyncio.Lock()
        self.session_ok = False           # did the current streaming session connect?
        self.denied = set()               # categories Webull has no data subscription for (until restart)
        self.no_history = set()           # single stocks Webull refused history for (until restart)
        self.kick_delay = 0                # wait before reconnecting after a dropped session (grows 5s .. 5 min)
        self.overnight = True             # ask for overnight prices too (off if not subscribed)
        self.overnight_checked = time.time()   # when the plan was last checked for overnight prices (start-up = a check)
        self.overnight_noted = None       # what was last written to data/webull_overnight.json
        self.connected_at = 0.0           # when the current streaming session connected

    # ---------------- subscription management (event loop thread only) ----------------
    def on_change(self, sym=None):
        if sym and is_futures(sym) and sym in self.watchlist and sym not in self.contracts:
            asyncio.ensure_future(self.resolve_futures(only=sym))
        if sym and sym not in self.watchlist and sym in self.contracts:
            self.by_wire.pop(self.contracts.pop(sym), None)
        self.schedule_sync(1.5)

    async def on_follow(self, syms, overnight):
        """jb-onestream: another part of the board wants Webull prices for syms through this bridge (Webull streams to
        one connection per App Key only). None = yes; else the refusal text, in Webull's words where it is the plan's."""
        if 'US_STOCK' in self.denied:
            return 'Insufficient permission (MARKET_DATA_NOT_SUBSCRIBED): no Webull stock market data'
        if overnight and not self.overnight:
            return ('Insufficient permission, please subscribe to US_STOCK OVERNIGHT (MARKET_DATA_NOT_SUBSCRIBED; '
                    'the Webull plan has no overnight session)')
        self.schedule_sync(0.5)
        return None

    def schedule_sync(self, delay):
        if self._sync_handle:
            self._sync_handle.cancel()
        self._sync_handle = asyncio.get_running_loop().call_later(
            delay, lambda: asyncio.ensure_future(self.sync()))

    async def _rate_slot(self):
        while True:
            now = time.time()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) < CALLS_PER_MIN:
                self.calls.append(now)
                return
            await asyncio.sleep(61 - (now - self.calls[0]))

    def category(self, sym):
        return 'US_FUTURES' if is_futures(sym) else 'US_STOCK'

    def wire(self, sym):
        """What Webull calls a board symbol: stocks as-is, futures by current contract."""
        return self.contracts.get(sym) if is_futures(sym) else sym

    async def sync(self):
        """Bring the Webull subscription in line with the watchlist: one subscribe call per
        category for everything missing, one unsubscribe call for everything removed. A
        change that arrives while a sync is running is picked up by running again."""
        if self._syncing:
            self._resync = True
            return
        if not self.mqtt_up or self.stream_client is None:
            return
        self._syncing = True
        try:
            while True:
                self._resync = False
                await self._sync_once()
                if not self._resync:
                    break
        finally:
            self._syncing = False

    async def _sync_once(self):
        from webull.data.common.category import Category
        loop = asyncio.get_running_loop()
        client = self.stream_client
        desired = {}
        for sym in self.watchlist.symbols:
            cat = self.category(sym)
            if cat in self.denied:
                # not subscribed on Webull: label the tile, leave the other symbols streaming
                self.server._status(sym, 'error', NOT_SUBSCRIBED_MSG[cat])
                continue
            w = self.wire(sym)
            if w:
                desired[w] = Category.US_FUTURES.name if is_futures(sym) else Category.US_STOCK.name
        if Category.US_STOCK.name not in self.denied:
            for sym in self.server.followed():            # the scanner's and the Webull algo's (jb-onestream)
                if not is_futures(sym):
                    desired.setdefault(sym, Category.US_STOCK.name)
        if len(desired) > MAX_TOPICS:
            self.server._status(None, 'stale', f'{len(desired)} symbols is over Webull\'s '
                                f'{MAX_TOPICS}-symbol streaming cap -- some will not stream')
        for cat in (Category.US_STOCK.name, Category.US_FUTURES.name):
            add = sorted(w for w, c in desired.items() if c == cat and w not in self.subscribed)
            gone = sorted(w for w, c in self.subscribed.items() if c == cat and w not in desired)
            if add:
                await self._rate_slot()
                if client is not self.stream_client or not self.mqtt_up:
                    return
                try:
                    on = self.overnight
                    await loop.run_in_executor(None, lambda: client.subscribe(
                        add, cat, SUB_TYPES, overnight_required=on))
                    for w in add:
                        self.subscribed[w] = cat
                    self.kick_delay = 0
                    if cat == Category.US_STOCK.name and on and self.overnight_noted is not True:
                        self.overnight_noted = True       # Webull took a subscription with the overnight session
                        jb_webull.note_overnight('board', True, 'Webull streams the overnight session')
                except Exception as e:
                    print(f'subscribe {add} failed: {jb_webull.describe_error(e)}', flush=True)
                    if 'INVALID_SESSION' in jb_webull.describe_error(e).upper():
                        # Webull dropped the streaming session but the connection looked alive:
                        # end it, so stream_worker starts a fresh one and subscribes again
                        self._restart_stream(client)
                        return
                    if is_overnight_refused(e) and self.overnight:
                        # no overnight-session data on this plan: stream regular + extended hours, and check again
                        # every 3 hours and at 7:45 pm New York (overnight_recheck) -- no restart needed after adding it
                        self.overnight = False
                        self.overnight_checked = time.time()
                        print('Webull has no overnight-session data subscription: streaming without '
                              'overnight prices (checked again every few hours)', flush=True)
                        self.server._status(OVERNIGHT_NOTICE_KEY, 'notice', OVERNIGHT_NOTICE)
                        self.overnight_noted = False
                        jb_webull.note_overnight('board', False, 'the Webull plan has no overnight session')
                        self._resync = True
                    elif is_not_subscribed(e) and cat in NOT_SUBSCRIBED_MSG:
                        # a missing data subscription won't fix itself: stop retrying (until
                        # ./jb restart) and mark just those tiles, not the whole board
                        self.denied.add(cat)
                        print(f'Webull has no {cat} market-data subscription: those symbols are '
                              f'skipped until the next restart', flush=True)
                        for sym in self.watchlist.symbols:
                            if self.category(sym) == cat:
                                self.server._status(sym, 'error', NOT_SUBSCRIBED_MSG[cat])
                    else:
                        self.server._status(None, 'stale', f'webull subscribe failed: {e}')
                        self.schedule_sync(60)
            if gone:
                await self._rate_slot()
                try:
                    await loop.run_in_executor(None, lambda: client.unsubscribe(
                        symbols=gone, category=cat, sub_types=SUB_TYPES))
                except Exception as e:
                    print(f'unsubscribe {gone} failed: {jb_webull.describe_error(e)}', flush=True)
                for w in gone:
                    self.subscribed.pop(w, None)
        if self.subscribed:
            self.server._status(None, 'idle', f'webull streaming {len(self.subscribed)} symbols')

    # ---------------- futures contracts ----------------
    async def resolve_futures(self, only=None):
        """Look up the current contract of each future on the watchlist (all of them, or
        just `only`). A change for one that was already streaming is a roll."""
        if self.data_client is None or 'US_FUTURES' in self.denied:
            return
        loop = asyncio.get_running_loop()
        changed = False
        for sym in [s for s in list(self.watchlist.symbols) if is_futures(s) and (only is None or s == only)]:
            try:
                code = await loop.run_in_executor(None, jb_webull.front_month, self.data_client, sym[:-2])
            except Exception as e:
                print(f'{sym}: Webull contract lookup failed ({jb_webull.describe_error(e)})', flush=True)
                continue
            if not code:
                self.server._status(sym, 'error', 'Webull has no futures contract for this symbol')
                continue
            old = self.contracts.get(sym)
            if code == old:
                continue
            self.contracts[sym] = code
            if old:
                self.by_wire.pop(old, None)
            self.by_wire[code] = sym
            changed = True
            if old:
                print(f'{sym}: Webull front contract rolled {old} -> {code}', flush=True)
                self.server.begin_roll(sym, code)
                self.server._status(sym, 'idle', f'rolled to {code}')
            else:
                print(f'{sym}: following Webull contract {code}', flush=True)
        if changed:
            self.schedule_sync(0.5)

    def _deny(self, cat):
        if cat in self.denied:
            return
        self.denied.add(cat)
        print(f'Webull has no {cat} market-data subscription: those symbols are skipped until the '
              f'next restart', flush=True)
        for sym in self.watchlist.symbols:
            if self.category(sym) == cat:
                self.server._status(sym, 'error', NOT_SUBSCRIBED_MSG[cat])
        self.schedule_sync(0.5)

    async def roll_checker(self):
        while True:
            await asyncio.sleep(self.args.roll_check_s)
            try:
                await self.resolve_futures()
            except Exception as e:
                print(f'roll check failed: {e!r}', flush=True)

    async def stall_watch(self):
        """The stream can die without any error: the connection looks fine but no prices come.
        Webull's own reconnect never notices, so: if NO symbol has had a Webull price for
        STALL_S while stocks are trading, drop the session and connect a fresh one."""
        while True:
            await asyncio.sleep(30)
            try:
                if not (self.mqtt_up and self.subscribed and self.stream_client is not None):
                    continue
                # overnight too when this plan streams overnight prices (review 2026-10-11 #6): the board's own
                # safety stops on Webull stocks need them then
                if webull_streams_now():
                    limit = STALL_S
                elif self.overnight and overnight_now():
                    limit = STALL_OVERNIGHT_S
                else:
                    continue
                quiet = time.time() - max(self.server.last_tick_ms / 1000.0, self.connected_at)
                if quiet >= limit:
                    print(f'no Webull prices for {int(quiet)}s while the market is open -- the stream has gone '
                          f'quiet, reconnecting', flush=True)
                    self._restart_stream(self.stream_client)
            except Exception as e:
                print(f'stall check failed: {e!r}', flush=True)

    async def overnight_recheck(self):
        """Webull overnight ready (hedge-2026-10-12): while this plan has no overnight session, ask again every 3 hours
        and at 7:45 pm New York (jb_webull.overnight_recheck_due)."""
        while True:
            await asyncio.sleep(60)
            try:
                await self.overnight_check_once()
            except Exception as e:
                print(f'overnight re-check failed: {e!r}', flush=True)

    async def overnight_check_once(self, force=False):
        """ONE subscription of one stock that asks for the overnight session. Taken: overnight prices are on -- the
        board's notice goes, and the stream reconnects so every symbol gets them. Refused again: nothing changes.
        Returns 'on', 'off', 'error' or None (nothing to check now)."""
        from webull.data.common.category import Category
        if self.overnight or not (self.mqtt_up and self.stream_client is not None):
            return None
        if not force and not jb_webull.overnight_recheck_due(self.overnight_checked):
            return None
        stocks = sorted(w for w, c in self.subscribed.items() if c == Category.US_STOCK.name)
        if not stocks:
            return None
        self.overnight_checked = time.time()
        await self._rate_slot()
        client = self.stream_client
        try:
            await asyncio.get_running_loop().run_in_executor(None, lambda: client.subscribe(
                stocks[:1], Category.US_STOCK.name, SUB_TYPES, overnight_required=True))
        except Exception as e:
            if is_overnight_refused(e):
                print('overnight re-check: still no overnight session on this Webull plan', flush=True)
                jb_webull.note_overnight('board', False, 'the Webull plan has no overnight session (checked again)')
                return 'off'
            print(f'overnight re-check failed ({jb_webull.describe_error(e)}); trying again later', flush=True)
            return 'error'
        print('overnight re-check: Webull now streams the overnight session -- reconnecting with overnight prices', flush=True)
        self.overnight = True
        self.overnight_noted = True
        self.server._status(OVERNIGHT_NOTICE_KEY, 'notice', '')          # the "no overnight prices" notice goes
        jb_webull.note_overnight('board', True, 'the overnight session is now on the Webull plan')
        if client is self.stream_client:
            self._restart_stream(client)
        return 'on'

    # ---------------- callbacks from the SDK thread: hand off, never raise ----------------
    def _on_connect(self, client, api_client, session_id):
        self.session_ok = True
        self.server.post(self._mqtt_connected, client)

    def _mqtt_connected(self, client):
        if client is not self.stream_client:
            return
        print('connected to Webull streaming', flush=True)
        self.mqtt_up = True
        self.connected_at = time.time()
        self.subscribed = {}              # a new session starts with no subscriptions
        self.server._status(None, 'idle', 'connected to Webull')
        self.schedule_sync(0.2)

    def _restart_stream(self, client):
        print('Webull stream session is gone -- reconnecting', flush=True)
        self.mqtt_up = False
        self.subscribed = {}
        self.kick_delay = min(max(self.kick_delay * 2, 5), 300)
        self.server._status(None, 'stale', 'Webull stream dropped -- reconnecting')
        try:
            client._thread_terminate = True     # stops the SDK's own retry loop on this client
            client.disconnect()                 # ...and makes its loop_forever return
        except Exception as e:
            print(f'disconnect failed: {e!r}', flush=True)

    def _mqtt_down(self, why):
        self.mqtt_up = False
        self.subscribed = {}
        self.server._status(None, 'stale', f'Webull stream disconnected ({why}) -- reconnecting')

    def _on_message(self, client, topic, quotes):
        try:
            basic = getattr(quotes, 'basic', None)
            if basic is None and isinstance(quotes, dict):
                basic = quotes.get('basic')
            sym = getattr(basic, 'symbol', None) if basic is not None else None
            if sym is None and isinstance(quotes, dict):
                sym = quotes.get('symbol')
            price = getattr(quotes, 'price', None)
            if price is None and isinstance(quotes, dict):
                price = quotes.get('price')
            ts = getattr(quotes, 'time', None) or (getattr(basic, 'timestamp', None) if basic is not None else None)
            if ts is None and isinstance(quotes, dict):
                ts = quotes.get('time')
            session = getattr(basic, 'trading_session', None) if basic is not None else None
            vol = getattr(quotes, 'volume', None)
            if vol is None and isinstance(quotes, dict):
                vol = quotes.get('volume')
            if sym is None or price is None:
                return
            self.server.post(self._route_tick, str(sym).upper(), float(price), _to_ms(ts), session, vol)
        except Exception as e:     # never let a parse problem kill the SDK's network loop
            print(f'[webull message not understood] topic={topic!r} {quotes!r}: {e!r}', flush=True)

    def _route_tick(self, sym, price, ts, session, vol=None):
        self.server.relay(sym, price, ts, session, vol)   # to the scanner / Webull algo when they follow it
        if sym in self.by_wire:                      # a futures contract -> its board symbol
            sym = self.by_wire[sym]
        elif sym not in self.watchlist:
            return                                   # e.g. the old contract right after a roll
        source = 'webull-overnight' if isinstance(session, str) and session.upper() in OVERNIGHT_SESSIONS else 'webull'
        try:
            size = float(vol or 0)
        except (TypeError, ValueError):
            size = 0
        self.server._tick(sym, price, ts, source, size)

    # ---------------- start-up + streaming thread ----------------
    def init_worker(self):
        """Create the Webull clients off the event loop. If Webull wants API access approved,
        wait for ./jb webull-login rather than sending approval requests to the phone."""
        delay = 60
        while not self.stop.is_set():
            try:
                _, self.data_client, _ = jb_webull.make_clients(
                    self.args.app_key, self.args.app_secret, self.args.region, self.args.environment)
                print('webull api client ready', flush=True)
                self.server.post(lambda: asyncio.ensure_future(self.resolve_futures()))
                break
            except jb_webull.NeedsApproval:
                print(jb_webull.AUTH_HELP, flush=True)
                self.server.status(None, 'error', jb_webull.AUTH_HELP)
                if not jb_webull.wait_for_approval(self.stop):
                    return
                print('Webull approval found -- connecting', flush=True)
            except Exception as e:
                auth = jb_webull.is_auth_problem(e)
                msg = jb_webull.AUTH_HELP if auth else f'could not start the Webull client: {jb_webull.describe_error(e)}'
                print(f'{msg} (retrying in {max(1, delay // 60)} min)', flush=True)
                self.server.status(None, 'error', msg)
                if self.stop.wait(delay):
                    return
                delay = min(delay * 2, 3600)
        self.stream_worker()

    def stream_worker(self):
        from webull.data.data_streaming_client import DataStreamingClient
        delay = 1
        while not self.stop.is_set():
            why = 'stream ended'
            if not jb_webull.stream_allowed():        # connecting now would ping the phone
                print(jb_webull.AUTH_HELP, flush=True)
                self.server.status(None, 'error', jb_webull.AUTH_HELP)
                if not jb_webull.wait_for_approval(self.stop):
                    return
            try:
                client = DataStreamingClient(self.args.app_key, self.args.app_secret, self.args.region,
                                             self.args.session_id, http_host=self.args.http_host,
                                             mqtt_host=self.args.mqtt_host)
                client.on_connect_success = self._on_connect
                client.on_quotes_message = self._on_message
                self.stream_client = client
                self.session_ok = False
                client.connect_and_loop_forever(logger_enable=True,
                                                customer_logger=jb_webull.sdk_logger())   # blocks
            except Exception as e:
                why = jb_webull.describe_error(e)[:120]
                print(f'webull stream error: {why}', flush=True)
                if jb_webull.is_auth_problem(e):
                    self.server.status(None, 'error', jb_webull.AUTH_HELP)
                    delay = max(delay, 300)
            self.server.post(self._mqtt_down, why)
            if self.session_ok:            # it had connected fine: reconnect straight away
                delay = max(1, self.kick_delay)
            if self.stop.wait(delay):
                return
            delay = min(delay * 2, 900 if delay >= 300 else 60)

    # ---------------- history ----------------
    def _history_sync(self, sym):
        boot, daily = [], []
        if self.data_client is None or self.category(sym) in self.denied or sym in self.no_history:
            return boot, daily
        try:
            code = self.contracts.get(sym) if is_futures(sym) else None
            if is_futures(sym) and not code:
                return boot, daily                   # contract not looked up yet: try again later
            boot += to_points(jb_webull.fetch_bars(self.data_client, sym, 'M5', 1200, contract=code), 300)
            boot += to_points(jb_webull.fetch_bars(self.data_client, sym, 'M1', 1200, contract=code), 60)
            daily = to_points(jb_webull.fetch_bars(self.data_client, sym, 'D', 60, sessions=None, contract=code), 86400)
        except Exception as e:
            print(f'history fetch failed for {sym}: {jb_webull.describe_error(e)}', flush=True)
            cat = self.category(sym)
            if is_not_subscribed(e) and cat == 'US_STOCK':
                # one odd stock (e.g. OTC): skip just its history; its live prices and every
                # other stock carry on
                self.no_history.add(sym)
            elif is_not_subscribed(e) and cat in NOT_SUBSCRIBED_MSG:
                self.server.post(self._deny, cat)    # stop asking (and logging) for it until restart
        boot.sort(key=lambda b: b['t'])
        return boot, daily

    async def history(self, sym):
        """Webull's own bars: 1200 x 5-min, 1200 x 1-min (all sessions) and 60 daily closes.
        They fill whatever our own record doesn't cover."""
        async with self.hist_lock:          # one symbol at a time: gentle on Webull's rate limits
            return await asyncio.get_running_loop().run_in_executor(None, self._history_sync, sym)

    # ---------------- main ----------------
    async def run(self):
        await self.server.start(self.args.bind, self.args.ws_port)
        print(f'webull bridge listening on {self.args.bind}:{self.args.ws_port} '
              f'({len(self.watchlist.symbols)} symbols on the watchlist)', flush=True)
        threading.Thread(target=self.init_worker, name='webull-stream', daemon=True).start()
        asyncio.ensure_future(self.roll_checker())
        asyncio.ensure_future(self.stall_watch())
        asyncio.ensure_future(self.overnight_recheck())
        await asyncio.Future()


def _to_ms(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return int(n * 1000) if n < 1e11 else int(n)


def main():
    ap = argparse.ArgumentParser(description="Webull bridge for Joseph's Board")
    ap.add_argument('--app-key', default=os.environ.get('WEBULL_APP_KEY'))
    ap.add_argument('--app-secret', default=os.environ.get('WEBULL_APP_SECRET'))
    ap.add_argument('--region', default=(os.environ.get('WEBULL_REGION_ID') or 'us'))
    ap.add_argument('--environment', default=os.environ.get('WEBULL_ENVIRONMENT', 'prod'), choices=['prod', 'uat'])
    ap.add_argument('--session-id', default='josephs_board')
    ap.add_argument('--bind', default=os.environ.get('JB_BIND', '127.0.0.1'))
    ap.add_argument('--ws-port', type=int, default=8766)
    ap.add_argument('--token', default=os.environ.get('WEBULL_BRIDGE_TOKEN', ''),
                    help='only needed if pages connect to this bridge directly instead of via the gateway')
    ap.add_argument('--data-dir', default=os.environ.get('JB_DATA_DIR', os.path.join(ROOT, 'data')))
    ap.add_argument('--roll-check-s', type=float, default=ROLL_CHECK_S)
    args = ap.parse_args()
    if not args.app_key or not args.app_secret:
        raise SystemExit('Missing WEBULL_APP_KEY / WEBULL_APP_SECRET -- put them in settings.env '
                         '(from developer.webull.com -> API Management), then ./jb restart webull')
    args.http_host, args.mqtt_host = jb_webull.hosts(args.environment)
    bridge = WebullBridge(args)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        print(f'port {args.ws_port} on this Mac is already in use -- is another copy of this service already running? '
              f'(./jb status shows what is running; ./jb restart fixes most cases)', flush=True)
        raise SystemExit(1)
    finally:
        bridge.stop.set()
        bridge.store.save(final=True)


if __name__ == '__main__':
    main()
