#!/usr/bin/env python3
"""IBKR bridge for Joseph's Board: IB Gateway / TWS -> your board pages.

Runs always-on on the spare Mac (started by ./install.sh as a launchd job). It connects to
IB Gateway on this same Mac, streams every symbol on the board's watchlist 24/7, records every
price it sees to data/ibkr_ticks.json (that record is what lets the board show overnight moves
IBKR's own history bars don't cover), and serves the board pages through the gateway at
http://<spare-mac>:8090/.

What it streams, per symbol:
  * Stocks/ETFs: two routes to the same stock -- SMART (4am-8pm ET) and IBKR's OVERNIGHT venue
    (8pm-3:50am ET, Sun-Fri, ~10,000 eligible US stocks/ETFs). Symbols IBKR won't quote
    overnight are detected and simply use SMART only. Each stock uses 2 of your ~100 IBKR
    market-data lines.
  * Futures (written like ES=F): the current front-month contract, re-checked every 4 hours so
    the board rolls to the next contract by itself after each expiry.
  * Tick-by-tick trades for as many symbols as your IBKR tick-by-tick allowance covers (usually
    3-5); the rest use IBKR's regular streaming feed.
  * Symbols you have no IBKR live-data subscription for (e.g. CL=F / GC=F without NYMEX/COMEX):
    when IBKR refuses live data, the bridge asks for IBKR's free delayed data instead (about
    10-20 minutes old) and marks those prices as delayed, so the board labels the tile DELAYED.
    Works the same for any symbol added later. Symbols you do subscribe to stay live. After
    buying a new subscription, ./jb restart makes the bridge try live data again.

Run by hand (normally ./install.sh does this for you):
    python bridges/ibkr_bridge.py            # finds IB Gateway on 4001 / 4002 / 7496 / 7497
IB Gateway: Configure > Settings > API > Settings: "Enable ActiveX and Socket Clients" on,
"Read-Only API" can stay on (this bridge never places orders), trusted IP 127.0.0.1.
"""
import argparse
import asyncio
import errno
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BridgeServer, TickStore, Watchlist, is_futures, now_ms  # noqa: E402

from ib_async import IB, ContFuture, Future, Stock  # noqa: E402
import logging  # noqa: E402


class _QuietLookups(logging.Filter):
    """ib_async logs every failed contract lookup as an error; for the OVERNIGHT-eligibility
    probe that's expected (many symbols aren't eligible), so keep those out of the log."""
    def filter(self, record):
        m = record.getMessage()
        return not ('OVERNIGHT' in m and ('Unknown contract' in m or 'Error 200' in m))


logging.getLogger('ib_async.wrapper').addFilter(_QuietLookups())
logging.getLogger('ib_async.ib').addFilter(_QuietLookups())

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

EXCHANGE = {'ES': 'CME', 'NQ': 'CME', 'RTY': 'CME', 'MES': 'CME', 'MNQ': 'CME', 'M2K': 'CME',
            'YM': 'CBOT', 'MYM': 'CBOT', 'ZB': 'CBOT', 'ZN': 'CBOT', 'ZC': 'CBOT', 'ZS': 'CBOT',
            'CL': 'NYMEX', 'NG': 'NYMEX', 'RB': 'NYMEX', 'HO': 'NYMEX', 'MCL': 'NYMEX',
            'GC': 'COMEX', 'SI': 'COMEX', 'HG': 'COMEX', 'MGC': 'COMEX',
            '6E': 'CME', '6J': 'CME', '6B': 'CME', 'BTC': 'CME', 'MBT': 'CME'}
DEFAULT_WATCHLIST = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA',
                     'ES=F', 'NQ=F', 'YM=F', 'RTY=F', 'CL=F', 'GC=F']
ROLL_CHECK_S = 4 * 3600

# IBKR message codes worth explaining in plain English on the board's status line.
FRIENDLY = {
    200: 'IBKR has no such symbol (check the ticker)',
    101: 'IBKR market-data line limit reached -- remove some symbols',
    354: 'not subscribed to IBKR market data for this symbol',
    10089: 'this symbol needs an extra IBKR market-data subscription',
    10090: 'part of this symbol\'s data needs an extra IBKR subscription',
    10168: 'not subscribed to IBKR market data for this symbol',
    10197: 'no market data: you are logged into IBKR somewhere else (mobile app / TWS / web) '
           'with live data -- log out there, or set that login to not take market data',
    1100: 'IB Gateway lost its connection to IBKR -- waiting for it to come back',
    1101: 'IB Gateway reconnected to IBKR (data re-subscribed)',
    1102: 'IB Gateway reconnected to IBKR',
    2110: 'IB Gateway lost its connection to IBKR servers -- waiting',
    162: 'IBKR refused a history request (pacing limit or no data) -- live prices unaffected',
    366: 'harmless: IBKR was slow sending chart history, so the board stopped waiting and will ask '
         'again; IBKR is only saying that old request is gone. Live prices are fine',
}
IGNORE = {2103, 2104, 2105, 2106, 2107, 2108, 2119, 2157, 2158, 10167, 300}

# "no live-data subscription for this symbol": answered by switching that symbol to delayed data
NOT_SUBSCRIBED = {354, 10089}
# ...and the codes meaning even delayed data was refused
NO_DELAYED = {354, 10089, 10168, 10186}
DELAYED_MD_TYPE = 4          # IBKR's delayed-frozen data: free, ~10-20 min old, last price kept when closed
DELAYED_SOURCE = 'ibkr-delayed'
DELAYED_MSG = 'DELAYED ~15 min: no IBKR live-data subscription for this symbol'


class Stream:
    def __init__(self, sym):
        self.sym = sym
        self.md = None          # qualified market-data contract
        self.hist = None        # contract for history requests
        self.overnight = None   # qualified OVERNIGHT-route contract (stocks only)
        self.mkt_reqids = []    # reqIds of reqMktData calls, so each can be cancelled exactly
        self.main_reqid = None  # reqId of the main (SMART / futures) reqMktData call
        self.delayed = False    # streaming IBKR's delayed data instead of live
        self.tbt_reqid = None
        self.seen_tbt = 0
        self.sub_at = 0.0       # when the latest reqMktData for this symbol was sent


class IBKRBridge:
    def __init__(self, args):
        self.args = args
        self.ib = IB()
        data_dir = args.data_dir
        self.store = TickStore(os.path.join(data_dir, 'ibkr_ticks.json'),
                               legacy_paths=['~/.josephs_board_ticks.json'])
        self.watchlist = Watchlist(os.path.join(data_dir, 'ibkr_watchlist.json'), DEFAULT_WATCHLIST)
        self.server = BridgeServer('ibkr', self.store, self.watchlist, token=args.token,
                                   on_add=self.on_add, on_remove=self.on_remove,
                                   history=self.history)
        self.streams = {}         # sym -> Stream
        self.by_con = {}          # conId -> sym
        self.no_overnight = set()
        self.tbt_denied = False
        self.hist_lock = asyncio.Lock()
        self.connected = False
        self._reconnect_task = None
        self._starting = set()
        self.attempted = set()    # symbols looked up since the last (re)connect
        self.retry_at = {}        # symbol -> when its lookup was last retried
        self.delayed = set()      # symbols IBKR refused live data for -> delayed data (until restart)
        self.no_data = set()      # symbols IBKR refused even delayed data for

    # ------------------------------------------------------------------ connection
    async def connect_forever(self):
        ports = [self.args.port] if self.args.port else [4001, 4002, 7496, 7497]
        t0, warned, warned_id = time.time(), False, False
        while True:
            in_use, err = None, None
            for port in ports:
                try:
                    await self.ib.connectAsync(self.args.host, port, clientId=self.args.client_id,
                                               timeout=8)
                    self.ib.reqMarketDataType(self.args.md_type)
                    self.connected = True
                    print(f'connected to IB Gateway/TWS on port {port} (client id {self.args.client_id})',
                          flush=True)
                    self.server.status(None, 'idle', 'connected to IB Gateway')
                    return
                except Exception as e:
                    if 'already in use' in str(e):
                        in_use = port
                    elif err is None or not isinstance(e, ConnectionRefusedError):
                        err = e
            if in_use:
                # right after a restart IB Gateway can take a few seconds to let go of our old
                # connection; only worth mentioning if it lasts
                if time.time() - t0 > 30 and not warned_id:
                    print(f'client id {self.args.client_id} is still in use on port {in_use}: another '
                          f'program is connected to IB Gateway with the same client id', flush=True)
                    warned_id = True
                await asyncio.sleep(2)
                continue
            if not warned:
                print(f'cannot reach IB Gateway/TWS on port(s) {", ".join(map(str, ports))} ({err!r}); '
                      f'retrying every 5s. Is it running and logged in, with the API enabled?', flush=True)
                warned = True
            self.server.status(None, 'stale', 'waiting for IB Gateway (is it running and logged in?)')
            await asyncio.sleep(5)

    def on_disconnect(self):
        if self.connected:
            print('lost the connection to IB Gateway; reconnecting', flush=True)
            self.server.status(None, 'stale', 'IB Gateway disconnected -- reconnecting')
        self.connected = False
        self._ensure_reconnect()

    def _ensure_reconnect(self):
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self):
        """Keep trying until connected AND the watchlist is streaming again -- a second drop
        in the middle of this (IB Gateway restarting) just goes round the loop again."""
        while True:
            await asyncio.sleep(1)
            try:
                await self.connect_forever()
                self.streams.clear()
                self.by_con.clear()
                self.attempted.clear()
                self.tbt_denied = False
                await self.start_all()
                if self.ib.isConnected():
                    self.server.status(None, 'idle', 'IB Gateway reconnected')
                    return
            except Exception as e:
                print(f'reconnect attempt failed ({e!r}); trying again', flush=True)
            self.connected = False

    async def supervisor(self):
        """Every 10 s: notice a dead connection even if no disconnect event came; every
        10 min per symbol: retry symbols whose lookup failed (e.g. during IBKR's nightly
        reset); once a day: re-check which stocks trade on the overnight venue."""
        cleared = time.time()
        while True:
            await asyncio.sleep(10)
            try:
                if self.connected and not self.ib.isConnected():
                    self.on_disconnect()
                    continue
                if not self.connected:
                    self._ensure_reconnect()
                    continue
                now = time.time()
                if now - cleared > 86400:
                    cleared = now
                    self.no_overnight.clear()
                for sym in list(self.watchlist.symbols):
                    if sym in self.streams or sym in self._starting:
                        continue
                    if now - self.retry_at.get(sym, 0) >= 600:
                        self.retry_at[sym] = now
                        asyncio.ensure_future(self.start_stream(sym))
            except Exception as e:
                print(f'supervisor: {e!r}', flush=True)

    # ------------------------------------------------------------------ contracts
    async def resolve(self, sym):
        """-> (md_contract, hist_contract) qualified, or (None, None) if IBKR doesn't know it.
        ib_async 2.x returns None for an unknown contract instead of raising, so every result
        is checked explicitly."""
        if is_futures(sym):
            root = sym[:-2]
            details = await self.ib.reqContractDetailsAsync(ContFuture(root, EXCHANGE.get(root, 'CME')))
            if not details:
                return None, None
            front = details[0].contract          # a CONTFUT's conId IS the front month's conId
            md = Future(conId=front.conId, exchange=front.exchange)
            q = await self.ib.qualifyContractsAsync(md)
            if not q or q[0] is None:
                return None, None
            return md, front
        c = Stock(sym.replace('.', ' '), 'SMART', 'USD')
        q = await self.ib.qualifyContractsAsync(c)
        if not q or q[0] is None or not c.conId:
            return None, None
        return c, c

    async def resolve_overnight(self, sym, md):
        if sym in self.no_overnight:
            return None
        on = Stock(md.symbol, 'OVERNIGHT', 'USD', primaryExchange=md.primaryExchange)
        q = await self.ib.qualifyContractsAsync(on)
        if not q or q[0] is None or not on.conId:
            self.no_overnight.add(sym)
            return None
        return on

    def _req_mkt(self, st, contract):
        st.sub_at = time.time()
        if st.delayed:
            # the data type applies to the requests sent after it, so only this one is delayed
            self.ib.reqMarketDataType(DELAYED_MD_TYPE)
        try:
            ticker = self.ib.reqMktData(contract, '233', False, False)   # 233 = RT volume
        finally:
            if st.delayed:
                self.ib.reqMarketDataType(self.args.md_type)
        rid = self.ib.wrapper.ticker2ReqId['mktData'].get(ticker)
        if rid:
            st.mkt_reqids.append(rid)
        return rid

    # ------------------------------------------------------------------ streams
    async def start_stream(self, sym):
        if sym in self.streams or sym in self._starting or not self.connected:
            return
        self._starting.add(sym)
        try:
            await self._start_stream(sym)
        finally:
            self._starting.discard(sym)
            self.attempted.add(sym)

    async def _start_stream(self, sym):
        st = Stream(sym)
        try:
            md, hist = await self.resolve(sym)
            if md is None:
                self.server.status(sym, 'error', 'IBKR does not recognise this symbol')
                return
            st.md, st.hist = md, hist
            st.delayed = sym in self.delayed or self.args.md_type in (3, 4)
            self.streams[sym] = st
            self.by_con[md.conId] = sym
            st.main_reqid = self._req_mkt(st, md)
            if st.delayed:
                # delayed data: no tick-by-tick or overnight venue (neither is offered delayed)
                if sym in self.delayed:
                    self.server.status(sym, 'delayed', DELAYED_MSG)
                return
            if not self.args.no_tbt and not self.tbt_denied:
                t = self.ib.reqTickByTickData(md, 'Last', 0, False)
                st.tbt_reqid = self.ib.wrapper.ticker2ReqId['Last'].get(t)
            if not is_futures(sym):
                on = await self.resolve_overnight(sym, md)
                if on is not None:
                    st.overnight = on
                    self._req_mkt(st, on)
        except Exception as e:
            self.server.status(sym, 'error', f'ibkr: {e}')

    def stop_stream(self, sym):
        st = self.streams.pop(sym, None)
        if not st:
            return
        for rid in st.mkt_reqids:
            try:
                self.ib.client.cancelMktData(rid)
            except Exception:
                pass
        if st.tbt_reqid:
            try:
                self.ib.client.cancelTickByTickData(st.tbt_reqid)
            except Exception:
                pass
        try:   # let ib_async forget the ticker bookkeeping too
            t = self.ib.ticker(st.md)
            if t is not None:
                self.ib.wrapper.ticker2ReqId['mktData'].pop(t, None)
                self.ib.wrapper.ticker2ReqId['Last'].pop(t, None)
        except Exception:
            pass
        if st.md is not None and self.by_con.get(st.md.conId) == sym:
            del self.by_con[st.md.conId]

    async def start_all(self):
        """Subscribe the whole watchlist, several lookups at a time (IBKR allows ~50 msgs/s)."""
        sem = asyncio.Semaphore(6)

        async def one(sym):
            async with sem:
                await self.start_stream(sym)
        await asyncio.gather(*(one(s) for s in list(self.watchlist.symbols)))

    def on_add(self, sym):
        asyncio.ensure_future(self.start_stream(sym))

    def on_remove(self, sym):
        self.stop_stream(sym)

    async def roll_checker(self):
        """Always-on means living through contract expiries: re-resolve each future's front
        month periodically and switch the stream when IBKR's front month changes."""
        while True:
            await asyncio.sleep(self.args.roll_check_s)
            if not self.connected:
                continue
            for sym in [s for s in list(self.streams) if is_futures(s)]:
                st = self.streams.get(sym)
                try:
                    root = sym[:-2]
                    details = await self.ib.reqContractDetailsAsync(ContFuture(root, EXCHANGE.get(root, 'CME')))
                    if details and st and details[0].contract.conId != st.md.conId:
                        new_local = details[0].contract.localSymbol
                        print(f'{sym}: front month rolled to {new_local}', flush=True)
                        self.stop_stream(sym)
                        await self.start_stream(sym)
                        self.server.begin_roll(sym, new_local)
                        self.server.status(sym, 'idle', f'rolled to {new_local}')
                except Exception as e:
                    print(f'roll check failed for {sym}: {e!r}', flush=True)

    # ------------------------------------------------------------------ ticks
    def on_tickers(self, batch):
        """Every trade price, stamped with its own time:
          * tick-by-tick trades carry IBKR's exact trade time;
          * streamed RT-volume and last-price ticks are stamped when they arrive (a stock's
            regular and overnight feeds share one ticker, so the ticker's shared time fields
            can't be trusted for either);
          * the last-price snapshot IBKR sends right after subscribing is the LAST trade, which
            may be hours old: it is stamped with IBKR's last-trade time, or skipped."""
        now = time.time()
        for t in batch:
            sym = self.by_con.get(getattr(t.contract, 'conId', None))
            if not sym:
                continue
            st = self.streams.get(sym)
            # delayed prices are labelled as such all the way to the board (and never live)
            delayed = (st is not None and st.delayed) or getattr(t, 'marketDataType', 1) in (3, 4)
            src = DELAYED_SOURCE if delayed else 'ibkr'
            for x in list(getattr(t, 'tickByTicks', None) or []):
                tm = _ms(getattr(x, 'time', None))
                if st and tm is not None:
                    if tm < st.seen_tbt:
                        continue
                    st.seen_tbt = tm
                self.server._tick(sym, getattr(x, 'price', None), tm, src)
                if not delayed:
                    self._print(sym, getattr(x, 'price', None), getattr(x, 'size', 0) or 0)   # big trades (jb-night)
                    self._flow(sym, t, getattr(x, 'price', None), getattr(x, 'size', 0) or 0)   # buy / sell pressure (jb-ideas)
            for x in list(getattr(t, 'ticks', None) or []):
                tt = getattr(x, 'tickType', None)
                price = getattr(x, 'price', None)
                if price is None or not (price == price) or price <= 0:
                    continue
                if tt in (48, 77):                           # RT volume: a new trade (its size is recorded too)
                    self.server._tick(sym, price, _ms(x.time), src, getattr(x, 'size', 0) or 0)
                    if not delayed:
                        self._print(sym, price, getattr(x, 'size', 0) or 0)   # big trades (jb-night)
                        if not (st and st.seen_tbt):                # counted once: tick-by-tick when it runs
                            self._flow(sym, t, price, getattr(x, 'size', 0) or 0)
                elif tt == 68:                               # delayed last price
                    # stamped with IBKR's delayed trade time, so the record and the tile's age
                    # show how old the price really is
                    lt = _ms(getattr(t, 'delayedLastTimestamp', None))
                    if not lt or lt > now * 1000 + 5000:
                        lt = _ms(x.time)
                    self.server._tick(sym, price, lt, DELAYED_SOURCE)
                elif tt == 4:                                # last price
                    if st and now - st.sub_at < 3:
                        lt = _ms(getattr(t, 'lastTimestamp', None))
                        if lt:
                            self.server._tick(sym, price, lt, src)
                        continue
                    self.server._tick(sym, price, _ms(x.time), src)
            if not delayed:
                self._quote(sym, t)                          # bid / ask for the SPREAD chip (jb-night)

    def _print(self, sym, price, size):
        """Send one big trade (a block: $1M+ or 20,000+ shares, JB_BLOCK_DOLLARS / JB_BLOCK_SHARES)
        to the boards. The same trade can arrive twice (tick-by-tick and RT volume): sent once."""
        try:
            if not price or not size or price != price or size != size:
                return
            lim = getattr(self, '_blim', None)
            if lim is None:
                lim = self._blim = (float(os.environ.get('JB_BLOCK_DOLLARS') or 1e6), float(os.environ.get('JB_BLOCK_SHARES') or 20000))
            if price * size < lim[0] and size < lim[1]:
                return
            now, key = time.time(), (sym, round(price, 4), int(size))
            seen = getattr(self, '_pseen', None)
            if seen is None:
                seen = self._pseen = {}
            if now - seen.get(key, 0) < 3:
                return
            seen[key] = now
            if len(seen) > 500:
                for k in [k for k, v in seen.items() if now - v > 10]:
                    del seen[k]
            self.server._broadcast({'type': 'print', 'symbol': sym, 'price': price, 'size': int(size), 'ts': int(now * 1000)})
        except Exception:
            pass

    def _flow(self, sym, t, price, size):
        """Buy vs sell pressure (jb-ideas, idea 20): shares traded at the ask (buyers) vs at the bid
        (sellers), sent to the board every 10 s per symbol. Never touches the prices."""
        try:
            if not price or not size or price != price or size != size:
                return
            bid, ask = getattr(t, 'bid', None), getattr(t, 'ask', None)
            if bid is None or ask is None or not (bid == bid and ask == ask) or not (0 < bid <= ask):
                return
            fl = getattr(self, '_fl', None)
            if fl is None:
                fl = self._fl = {}
            x = fl.setdefault(sym, {'buy': 0.0, 'sell': 0.0, 'mid': 0.0, 'at': time.time()})
            k = 'buy' if price >= ask else 'sell' if price <= bid else 'mid'
            x[k] += float(size)
            now = time.time()
            if now - x['at'] >= 10:
                self.server._broadcast({'type': 'flow', 'symbol': sym, 'buy': int(x['buy']), 'sell': int(x['sell']),
                                        'mid': int(x['mid']), 'ts': int(now * 1000)})
                x.update(buy=0.0, sell=0.0, mid=0.0, at=now)
        except Exception:
            pass

    def _quote(self, sym, t):
        """Send the best bid and ask (and shares on each side) to the boards, at most every 5 s per
        symbol, and again every minute even if unchanged. Never lets a problem here touch the prices."""
        try:
            bid, ask = getattr(t, 'bid', None), getattr(t, 'ask', None)
            if bid is None or ask is None or not (bid == bid and ask == ask) or not (0 < bid <= ask):
                return
            bs, az = getattr(t, 'bidSize', 0) or 0, getattr(t, 'askSize', 0) or 0
            bs, az = (int(bs) if bs == bs else 0), (int(az) if az == az else 0)
            last = getattr(self, '_qlast', None)
            if last is None:
                last = self._qlast = {}
            now, key = time.time(), (bid, ask, bs, az)
            prev = last.get(sym)
            if prev and ((prev[1] == key and now - prev[0] < 60) or now - prev[0] < 5):
                return
            last[sym] = (now, key)
            self.server._broadcast({'type': 'quote', 'symbol': sym, 'bid': bid, 'ask': ask, 'bs': bs, 'as': az, 'ts': int(now * 1000)})
        except Exception:
            pass

    def _stream_for_req(self, req_id):
        for sym, st in self.streams.items():
            if req_id == st.main_reqid or req_id in st.mkt_reqids or req_id == st.tbt_reqid:
                return sym, st
        return None, None

    def on_error(self, req_id, code, text, contract=None):
        if code in IGNORE:
            return
        sym, st = self._stream_for_req(req_id)
        if sym is None and contract is not None:
            sym = self.by_con.get(getattr(contract, 'conId', None))
            st = self.streams.get(sym) if sym else None
        if st is not None and code in NOT_SUBSCRIBED | NO_DELAYED:
            if req_id == st.main_reqid and not st.delayed and code in NOT_SUBSCRIBED:
                # no live-data subscription: switch this symbol to IBKR's free delayed data
                print(f'[IBKR {code}] {sym}: no live-data subscription -- using IBKR delayed data', flush=True)
                self.delayed.add(sym)
                asyncio.ensure_future(self._restart_stream(sym))
                return
            if req_id == st.main_reqid and st.delayed:
                print(f'[IBKR {code}] {sym}: IBKR refused delayed data too: {text}', flush=True)
                self.no_data.add(sym)
                self.server.status(sym, 'error', 'no IBKR data: not subscribed, and IBKR offers no delayed '
                                                 f'data for it ({code})')
                return
            if req_id == st.tbt_reqid:            # tick-by-tick not allowed: the regular feed carries on
                try:
                    self.ib.client.cancelTickByTickData(req_id)
                except Exception:
                    pass
                st.tbt_reqid = None
                return
            if req_id in st.mkt_reqids:           # the overnight venue isn't subscribed: SMART carries on
                try:
                    self.ib.client.cancelMktData(req_id)
                except Exception:
                    pass
                st.mkt_reqids.remove(req_id)
                self.no_overnight.add(sym)
                return
        if sym in self.delayed and code in NOT_SUBSCRIBED | NO_DELAYED | {162}:
            # e.g. a history request for a delayed symbol: keep the DELAYED label on the tile
            print(f'[IBKR {code}] {sym} {text}', flush=True)
            return
        if code == 10190:
            if not self.tbt_denied:
                print('note: IBKR tick-by-tick allowance used up; remaining symbols use the regular '
                      'streaming feed (still real-time)', flush=True)
            self.tbt_denied = True
            return
        if code == 200 and sym is None:
            return   # a contract lookup that failed -- resolve()/resolve_overnight() report it per symbol
        msg = FRIENDLY.get(code, text)
        print(f'[IBKR {code}] {sym or ""} {text}', flush=True)
        kind = 'idle' if code in (1101, 1102) else 'stale'
        self.server.status(sym, kind, f'{msg} ({code})')
        if code == 1101:
            asyncio.ensure_future(self._resubscribe())

    async def _restart_stream(self, sym):
        self.stop_stream(sym)
        await asyncio.sleep(0.5)
        if sym in self.watchlist:
            await self.start_stream(sym)

    async def _resubscribe(self):
        for sym in list(self.streams):
            self.stop_stream(sym)
        await self.start_all()

    # ------------------------------------------------------------------ history
    async def _bars(self, contract, duration, size, rth, span_ms):
        bars = await self.ib.reqHistoricalDataAsync(contract, '', duration, size, 'TRADES', rth, 2,
                                                    timeout=30)
        now = now_ms()
        out = []
        for b in bars or []:
            d = b.date
            if hasattr(d, 'hour'):                    # intraday: aware datetime (bar start)
                t = int(d.timestamp() * 1000) + span_ms
            else:                                     # daily: a date -> that day's 4pm ET close
                t = _date_close_ms(d)
            out.append({'t': min(t, now), 'p': b.close})
        return out

    async def history(self, sym):
        """IBKR's own bars: 5 days of 5-min + 1 day of 1-min (incl. pre/after-hours) and
        2 months of daily closes. They fill whatever our own record doesn't cover."""
        deadline = time.time() + 30         # right after a (re)start: wait for the symbol's lookup
        while time.time() < deadline and sym in self.watchlist and sym not in self.streams:
            if self.connected and sym in self.attempted and sym not in self._starting:
                break                       # looked up, IBKR doesn't know it
            await asyncio.sleep(0.25)
        st = self.streams.get(sym)
        if st is None or not self.connected:
            return [], []
        boot, daily = [], []
        async with self.hist_lock:          # one request at a time, spaced, to respect IBKR pacing
            boot += await self._bars(st.hist, '5 D', '5 mins', False, 300000)
            await asyncio.sleep(0.2)
            boot += await self._bars(st.hist, '1 D', '1 min', False, 60000)
            await asyncio.sleep(0.2)
            daily = await self._bars(st.hist, '2 M', '1 day', True, 0)
            await asyncio.sleep(0.2)
            if st.overnight is not None:    # overnight 8 pm - 4 am bars too, so downtime overnight is filled (jb-ideas)
                try:
                    boot += await asyncio.wait_for(self._bars(st.overnight, '2 D', '5 mins', False, 300000), 12)
                except Exception:
                    pass
                await asyncio.sleep(0.2)
        boot.sort(key=lambda b: b['t'])
        return boot, daily

    # ------------------------------------------------------------------ main
    async def run(self):
        self.ib.pendingTickersEvent += self.on_tickers
        self.ib.errorEvent += self.on_error
        self.ib.disconnectedEvent += self.on_disconnect
        await self.server.start(self.args.bind, self.args.ws_port)
        print(f'ibkr bridge listening on {self.args.bind}:{self.args.ws_port} '
              f'({len(self.watchlist.symbols)} symbols on the watchlist)', flush=True)
        asyncio.ensure_future(self.roll_checker())
        await self.connect_forever()
        await self.start_all()
        asyncio.ensure_future(self.supervisor())
        await asyncio.Future()


def _ms(dt):
    try:
        return int(dt.timestamp() * 1000) if dt else None
    except Exception:
        return None


def _date_close_ms(d):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return int(datetime(d.year, d.month, d.day, 16, 0, tzinfo=ZoneInfo('America/New_York')).timestamp() * 1000)


def main():
    ap = argparse.ArgumentParser(description='IBKR bridge for Joseph\'s Board')
    ap.add_argument('--host', default='127.0.0.1', help='where IB Gateway/TWS runs (this Mac)')
    ap.add_argument('--port', type=int, default=int(os.environ.get('IBKR_GATEWAY_PORT') or 0) or None,
                    help='IB Gateway/TWS API port (default: try 4001, 4002, 7496, 7497)')
    ap.add_argument('--client-id', type=int, default=17)
    ap.add_argument('--bind', default=os.environ.get('JB_BIND', '127.0.0.1'),
                    help='interface for the browser side (127.0.0.1: reachable only through the gateway)')
    ap.add_argument('--ws-port', type=int, default=8765)
    ap.add_argument('--md-type', type=int, default=1, help='1 = live, 3 = delayed')
    ap.add_argument('--no-tbt', action='store_true', help='skip tick-by-tick, use reqMktData only')
    ap.add_argument('--token', default=os.environ.get('IBKR_BRIDGE_TOKEN', ''),
                    help='only needed if pages connect to this bridge directly instead of via the gateway')
    ap.add_argument('--data-dir', default=os.environ.get('JB_DATA_DIR', os.path.join(ROOT, 'data')))
    ap.add_argument('--roll-check-s', type=float, default=ROLL_CHECK_S)
    args = ap.parse_args()
    bridge = IBKRBridge(args)
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
        bridge.store.save(final=True)


if __name__ == '__main__':
    main()
