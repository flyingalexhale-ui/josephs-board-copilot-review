"""Webull tick feed for the scanner (official webull-openapi-python-sdk; read-only).

Fixed against the real SDK 3.x source:
  * the symbol is read from the trade's .basic.symbol (the first version looked for .symbol,
    which Webull ticks don't have, so every Webull tick was silently dropped)
  * the SDK calls back on its own network thread; on_tick is thread-safe (Scanner hands the
    tick to the asyncio loop), and nothing here ever raises back into the SDK
  * the streaming host is 'data-api.webull.com' -- the SDK adds port 1883 itself; the first
    version's 'data-api.webull.com:1883' can't be looked up and never connected
  * shares the board bridge's Webull login token and start-up lock (lib/jb_webull.py), so it
    never triggers an extra approval request on your phone
  * Webull plans without the overnight session refuse a subscription that asks for it; the
    feed then subscribes again without overnight (same fix as the Webull board bridge), and asks again
    every 3 hours and at 7:45 pm New York, so a plan bought later works without a restart
"""
import asyncio
import logging
import os
import sys
import time

log = logging.getLogger('webull_feed')
STALL_S = 180      # no Webull price for any symbol this long (4am-8pm trading days): reconnect
STALL_OVERNIGHT_S = 900   # ...overnight (8pm-4am before a trading day, plans with overnight prices): thinner trading

_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'lib')
if os.path.isdir(_LIB) and _LIB not in sys.path:
    sys.path.insert(0, os.path.abspath(_LIB))
import jb_webull  # noqa: E402
from ..picks import bar_date_from_ms  # noqa: E402


def _overnight_now(now=None):
    """8 pm - 4 am New York time the night before a trading day: Webull's overnight session."""
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        import nyse_hours
    except ImportError:
        return False
    d = datetime.fromtimestamp(now or time.time(), ZoneInfo('America/New_York'))
    if d.hour >= 20:
        return nyse_hours.trading_day(d.date() + timedelta(days=1))
    return d.hour < 4 and nyse_hours.trading_day(d.date())


def _overnight_refused(e):
    """Webull said no only because this plan lacks the overnight session
    (403 MARKET_DATA_NOT_SUBSCRIBED ... please subscribe to US_STOCK OVERNIGHT)."""
    s = ' '.join(str(x) for x in (jb_webull.describe_error(e), getattr(e, 'error_code', ''),
                                   getattr(e, 'error_msg', ''))).upper()
    return ('MARKET_DATA_NOT_SUBSCRIBED' in s or 'INSUFFICIENT PERMISSION' in s) and 'OVERNIGHT' in s


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


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class WebullFeed:
    def __init__(self, symbols, on_tick, app_key, app_secret, region='us', environment='prod',
                 session_id='josephs_board_scanner'):
        self.symbols = list(symbols)
        self.on_tick = on_tick
        self.app_key, self.app_secret, self.region, self.environment = app_key, app_secret, region, environment
        self.http_host, self.mqtt_host = jb_webull.hosts(environment)
        self.session_id = session_id
        self.overnight = True        # ask for overnight prices too (off if the plan lacks them; checked again every few hours)
        self.overnight_checked = time.time()
        self.overnight_noted = None
        self.data_client = None      # Webull's REST client, for the picks' daily history
        self.client = None           # the current streaming client
        self.alive_at = 0.0          # last price, or when the stream connected

    def _on_message(self, client, topic, q):
        try:
            basic = getattr(q, 'basic', None)
            sym = getattr(basic, 'symbol', None)
            price = _num(getattr(q, 'price', None))
            if not sym or price is None:
                return
            self.alive_at = time.time()
            ts = _num(getattr(q, 'time', None) or getattr(basic, 'timestamp', None))
            ts = int(ts * 1000 if ts and ts < 1e11 else ts) if ts else int(time.time() * 1000)
            self.on_tick(str(sym).upper(), price, ts, _num(getattr(q, 'volume', None)) or 0.0, 'webull')
        except Exception as e:
            log.error('webull tick not understood (%r): %r', q, e)

    def _run_blocking(self):
        # jb-onestream: Webull streams to one connection per App Key, so the scanner gets its prices through the
        # board's Webull bridge (lib/jb_webull_relay.py), not a Webull connection of its own
        import jb_webull_relay
        DataStreamingClient = jb_webull_relay.streaming_client_class()
        from webull.data.common.category import Category
        from webull.data.common.subscribe_type import SubscribeType
        client = DataStreamingClient(self.app_key, self.app_secret, self.region, self.session_id,
                                     http_host=self.http_host, mqtt_host=self.mqtt_host)

        def on_connect(c, api_client, session_id):
            self.session_ok = True
            self.alive_at = time.time()
            # subscribing is a quick HTTP call; do it on a helper thread so the SDK callback
            # returns immediately and can never raise back into the SDK
            import threading

            def sub():
                for _ in range(2):
                    try:
                        c.subscribe(self.symbols, Category.US_STOCK.name, [SubscribeType.TICK.name],
                                    overnight_required=self.overnight)
                        log.info('Webull feed subscribed %d symbols%s', len(self.symbols),
                                 '' if self.overnight else ' (no overnight session on this Webull plan)')
                        if self.overnight and self.overnight_noted is not True:
                            self.overnight_noted = True
                            jb_webull.note_overnight('scanner', True, 'Webull streams the overnight session')
                        return
                    except Exception as e:
                        if self.overnight and _overnight_refused(e):
                            self.overnight = False      # try once more without overnight (checked again every few hours)
                            self.overnight_checked = time.time()
                            self.overnight_noted = False
                            jb_webull.note_overnight('scanner', False, 'the Webull plan has no overnight session')
                            continue
                        log.error('Webull subscribe failed: %s', jb_webull.describe_error(e))
                        return
            threading.Thread(target=sub, daemon=True).start()

        client.on_connect_success = on_connect
        client.on_quotes_message = self._on_message
        self.client = client
        client.connect_and_loop_forever(logger_enable=True, customer_logger=jb_webull.sdk_logger())

    async def daily_bars(self, sym):
        """About a year of daily bars for the research picks, oldest first -- or None until
        the Webull client is ready."""
        if self.data_client is None:
            return None
        rows = await asyncio.to_thread(jb_webull.fetch_bars, self.data_client, sym, 'D', 260, sessions=None)
        for r in rows or []:
            r['date'] = bar_date_from_ms(r['t'])
        return rows or None

    def _overnight_probe(self):
        """Webull overnight ready (hedge-2026-10-12): one subscription of one stock that asks for the overnight session.
        Taken: overnight prices are on, and the stream reconnects so every symbol gets them. Refused: nothing changes."""
        from webull.data.common.category import Category
        from webull.data.common.subscribe_type import SubscribeType
        c = self.client
        self.overnight_checked = time.time()
        try:
            c.subscribe(self.symbols[:1], Category.US_STOCK.name, [SubscribeType.TICK.name], overnight_required=True)
        except Exception as e:
            if _overnight_refused(e):
                log.info('overnight re-check: still no overnight session on this Webull plan')
                jb_webull.note_overnight('scanner', False, 'the Webull plan has no overnight session (checked again)')
            else:
                log.warning('overnight re-check failed (%s); trying again later', jb_webull.describe_error(e))
            return
        log.warning('overnight re-check: Webull now streams the overnight session -- reconnecting with overnight prices')
        self.overnight = True
        self.overnight_noted = True
        jb_webull.note_overnight('scanner', True, 'the overnight session is now on the Webull plan')
        try:
            c._thread_terminate = True
            c.disconnect()
        except Exception as e:
            log.warning('disconnect failed: %r', e)

    async def _stall_watch(self):
        """The stream can die without any error (connected, but no prices). If no symbol has had a
        price for STALL_S while stocks trade, end the session so run() connects a fresh one. Also checks again for
        the overnight session every few hours while this plan has none (jb_webull.overnight_recheck_due)."""
        while True:
            await asyncio.sleep(30)
            c = self.client
            if (c is not None and getattr(self, 'session_ok', False) and not self.overnight and self.symbols
                    and jb_webull.overnight_recheck_due(self.overnight_checked)):
                await asyncio.to_thread(self._overnight_probe)
                continue
            if c is None or not getattr(self, 'session_ok', False):
                continue
            # overnight too when this plan streams overnight prices (review 2026-10-11, second check; same as the bridge)
            if webull_streams_now():
                limit = STALL_S
            elif self.overnight and _overnight_now():
                limit = STALL_OVERNIGHT_S
            else:
                continue
            quiet = time.time() - self.alive_at
            if quiet >= limit:
                log.warning('no Webull prices for %ds while the market is open -- reconnecting', int(quiet))
                self.alive_at = time.time()
                try:
                    c._thread_terminate = True
                    c.disconnect()
                except Exception as e:
                    log.warning('disconnect failed: %r', e)

    async def run(self):
        asyncio.ensure_future(self._stall_watch())
        delay = 60
        # make sure the (shared) Webull login token is valid before streaming
        while True:
            try:
                _, self.data_client, _ = await asyncio.to_thread(jb_webull.make_clients, self.app_key,
                                                                 self.app_secret, self.region, self.environment)
                break
            except jb_webull.NeedsApproval:
                log.error('%s', jb_webull.AUTH_HELP)
                await asyncio.to_thread(jb_webull.wait_for_approval)
                log.info('Webull approval found -- connecting')
            except Exception as e:
                log.error('%s (%s) -- retrying in %d min', jb_webull.AUTH_HELP if jb_webull.is_auth_problem(e)
                          else 'Webull client failed to start', jb_webull.describe_error(e), max(1, delay // 60))
                await asyncio.sleep(delay)
                delay = min(delay * 2, 3600)
        delay = 1
        while True:
            self.session_ok = False
            if not jb_webull.stream_allowed():        # connecting now would ping the phone
                log.error('%s', jb_webull.AUTH_HELP)
                await asyncio.to_thread(jb_webull.wait_for_approval)
            try:
                log.info('Webull feed connecting for %d symbols', len(self.symbols))
                await asyncio.to_thread(self._run_blocking)
            except Exception as e:
                log.error('Webull stream error: %s', jb_webull.describe_error(e))
            if self.session_ok:          # it had connected fine: reconnect straight away
                delay = 1
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
