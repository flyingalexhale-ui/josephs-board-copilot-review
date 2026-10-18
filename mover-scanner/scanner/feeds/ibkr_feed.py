"""IBKR tick feed for the scanner (ib_async; read-only -- never places an order).

Same connection pattern as the IBKR board bridge (port fallback 4001/4002/7496/7497, automatic
reconnect + re-subscribe), with its own client id (37) so both can be connected at once.

Each trade comes from IBKR's RT-volume ticks (price and size of that one trade), stamped when
it arrives -- so a window's volume is exactly what traded in it. Plain last-price updates add
the price but no volume; the last-trade snapshot IBKR sends right after subscribing (possibly
hours old) is ignored.
"""
import asyncio
import time
import logging

log = logging.getLogger('ibkr_feed')
IGNORE = {2103, 2104, 2105, 2106, 2107, 2108, 2119, 2157, 2158, 10167, 10190, 300}


class _QuietLookups(logging.Filter):
    """Many symbols don't trade on IBKR's OVERNIGHT venue; the failed lookup is expected, not an error."""
    def filter(self, record):
        m = record.getMessage()
        return not ('OVERNIGHT' in m and ('Unknown contract' in m or 'Error 200' in m or 'No security definition' in m))


for _name in ('ib_async.wrapper', 'ib_async.ib', 'ibkr_feed'):
    logging.getLogger(_name).addFilter(_QuietLookups())


def _ms(dt):
    try:
        return int(dt.timestamp() * 1000) if dt else None
    except Exception:
        return None


class IBKRFeed:
    def __init__(self, symbols, on_tick, host='127.0.0.1', port=None, client_id=37, md_type=1):
        from ib_async import IB
        self.symbols = list(symbols)
        self.on_tick = on_tick
        self.host, self.port, self.client_id, self.md_type = host, port, client_id, md_type
        self.ib = IB()
        self.by_con = {}
        self.contracts = {}    # symbol -> its IBKR contract (for the picks' daily history)
        self.hist_skip = {}    # symbol -> (failures in a row, don't ask IBKR again before this time)
        self.sub_at = {}       # symbol -> when its market data was requested
        self.connected = False
        self._reconnect_task = None
        self.route = 'smart'   # 'overnight' from 8 pm to 3:50 am ET: prices come from IBKR's OVERNIGHT venue
        self.on_contracts = {} # symbol -> its OVERNIGHT-venue contract, or False if it doesn't trade there
        self.streaming = {}    # symbol -> the contract its market data is coming from now

    def _on_tickers(self, batch):
        now = time.time()
        for t in batch:
            sym = self.by_con.get(getattr(t.contract, 'conId', None))
            if not sym:
                continue
            for x in list(getattr(t, 'ticks', None) or []):
                tt = getattr(x, 'tickType', None)
                if tt in (48, 77):                                  # one trade: price + size
                    size = getattr(x, 'size', 0) or 0
                elif tt in (4, 68):                                 # last price, no volume
                    if now - self.sub_at.get(sym, 0) < 3:
                        continue                                    # the snapshot, maybe hours old
                    size = 0
                else:
                    continue
                price = getattr(x, 'price', None)
                if price is None or price != price or price <= 0:
                    continue
                try:
                    self.on_tick(sym, float(price), _ms(x.time), float(size), 'ibkr')
                except Exception as e:
                    log.error('on_tick failed for %s: %r', sym, e)

    def _on_error(self, req_id, code, text, contract=None):
        if code in IGNORE:
            return
        if code == 200 and getattr(contract, 'exchange', '') == 'OVERNIGHT':
            return                         # not on the overnight venue: expected
        sym = self.by_con.get(getattr(contract, 'conId', None)) if contract is not None else None
        log.warning('[IBKR %s] %s %s', code, sym or '', text)

    def _on_disconnect(self):
        if self.connected:
            log.warning('lost connection to IB Gateway; reconnecting')
        self.connected = False
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _connect(self):
        ports = [self.port] if self.port else [4001, 4002, 7496, 7497]
        warned, t0 = False, time.time()
        while True:
            in_use, err = None, None
            for port in ports:
                try:
                    await self.ib.connectAsync(self.host, port, clientId=self.client_id, timeout=8)
                    self.ib.reqMarketDataType(self.md_type)
                    self.connected = True
                    log.info('connected to IB Gateway on port %s (client id %s)', port, self.client_id)
                    return
                except Exception as e:
                    if 'already in use' in str(e):
                        in_use = port
                    elif err is None or not isinstance(e, ConnectionRefusedError):
                        err = e
            if in_use:     # just restarted: IB Gateway still releasing our previous connection
                if time.time() - t0 > 30 and not warned:
                    log.warning('client id %s is still in use on port %s: another program is connected '
                                'with the same client id', self.client_id, in_use)
                    warned = True
                await asyncio.sleep(2)
                continue
            if not warned:
                log.warning('cannot reach IB Gateway/TWS on port(s) %s (%r); retrying every 5s',
                            ', '.join(map(str, ports)), err)
                warned = True
            await asyncio.sleep(5)

    async def _subscribe_all(self):
        from ib_async import Stock
        self.by_con.clear()
        for sym in self.symbols:
            c = Stock(sym.replace('.', ' '), 'SMART', 'USD')
            q = await self.ib.qualifyContractsAsync(c)
            if not q or q[0] is None or not c.conId:
                log.error('IBKR does not recognise %s -- check watchlist.yaml', sym)
                continue
            self.by_con[c.conId] = sym
            self.contracts[sym] = c
            self.sub_at[sym] = time.time()
            self.ib.reqMktData(c, '233', False, False)     # 233 = RT volume (trade price/size/time)
            self.streaming[sym] = c
        self.route = 'smart'
        log.info('IBKR feed streaming %d/%d symbols', len(self.by_con), len(self.symbols))

    # ---- overnight: SMART only covers 4 am - 8 pm ET; 8 pm - 3:50 am trades are on IBKR's OVERNIGHT
    # venue. The feed switches each symbol over at 8 pm and back at 3:50, so it never uses more
    # IBKR market-data lines than before.
    @staticmethod
    def _want_route():
        from ..picks import et_now
        d = et_now()
        m = d.hour * 60 + d.minute
        night = (m >= 1200 and d.weekday() in (6, 0, 1, 2, 3)) or (m < 230 and d.weekday() < 5)
        return 'overnight' if night else 'smart'

    async def _overnight_contract(self, sym):
        on = self.on_contracts.get(sym)
        if on is not None:
            return on or None
        from ib_async import Stock
        c = self.contracts.get(sym)
        try:
            on = Stock(c.symbol, 'OVERNIGHT', 'USD', primaryExchange=c.primaryExchange)
            q = await self.ib.qualifyContractsAsync(on)
            ok = bool(q and q[0] is not None and on.conId)
        except Exception:
            ok = False
        self.on_contracts[sym] = on if ok else False
        return on if ok else None

    async def _set_route(self, route):
        moved = 0
        for sym in self.symbols:
            smart = self.contracts.get(sym)
            if smart is None:
                continue
            new = smart if route == 'smart' else await self._overnight_contract(sym)
            if new is None or new is self.streaming.get(sym):
                continue                       # not on the overnight venue: stays on SMART
            old = self.streaming.get(sym)
            if old is not None:
                try:
                    self.ib.cancelMktData(old)
                except Exception:
                    pass
            self.by_con[new.conId] = sym
            self.sub_at[sym] = time.time()
            self.ib.reqMktData(new, '233', False, False)
            self.streaming[sym] = new
            moved += 1
        self.route = route
        log.info('IBKR feed: %s prices now (%d symbols switched)', 'overnight-venue' if route == 'overnight' else 'SMART', moved)

    async def _route_loop(self):
        while True:
            await asyncio.sleep(30)
            want = self._want_route()
            if want != self.route and self.connected and self.ib.isConnected():
                try:
                    await self._set_route(want)
                except Exception as e:
                    log.warning('IBKR feed: switching to %s failed (%r); trying again', want, e)

    async def minute_bars(self, sym):
        """The last two days of 1-minute bars (4 am - 8 pm ET) as [t_ms, o, h, l, c, v], for the
        minute picks; None while IB Gateway isn't connected."""
        if not self.connected or not self.ib.isConnected():
            return None
        c = self.contracts.get(sym)
        if c is None:
            return None
        bars = await self.ib.reqHistoricalDataAsync(c, '', '2 D', '1 min', 'TRADES', False, 2, timeout=60)
        out = []
        for b in bars or []:
            try:
                out.append([int(_ms(b.date) if not isinstance(b.date, (int, float)) else b.date * 1000),
                            float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume or 0)])
            except (TypeError, ValueError):
                continue
        return out or None

    async def daily_bars(self, sym):
        """About a year of daily bars (regular hours) for the research picks, oldest first --
        or None while IB Gateway isn't connected or IBKR has no history for the symbol."""
        if not self.connected or not self.ib.isConnected():
            return None
        fails, until = self.hist_skip.get(sym, (0, 0))
        if time.time() < until:
            return None                    # IBKR had nothing lately: the picks use Webull meanwhile
        c = self.contracts.get(sym)
        if c is None:                      # e.g. SPY for the picks' comparison, when it isn't scanned
            from ib_async import Stock
            c = Stock(sym.replace('.', ' '), 'SMART', 'USD')
            q = await self.ib.qualifyContractsAsync(c)
            if not q or q[0] is None or not c.conId:
                self.hist_skip[sym] = (fails + 1, time.time() + 3600)
                return None
            self.contracts[sym] = c
        bars = await self.ib.reqHistoricalDataAsync(c, '', '1 Y', '1 day', 'TRADES', True, 1, timeout=30)
        out = []
        for b in bars or []:
            try:
                out.append({'date': str(b.date)[:10], 'open': float(b.open), 'high': float(b.high),
                            'low': float(b.low), 'close': float(b.close), 'volume': float(b.volume or 0)})
            except (TypeError, ValueError):
                continue
        if out:
            self.hist_skip.pop(sym, None)
        elif self.ib.isConnected():        # e.g. no data permission: ask again later, less often each time
            self.hist_skip[sym] = (fails + 1, time.time() + 60 * min(10 * 2 ** fails, 240))
        return out or None

    async def _reconnect_loop(self):
        """Until connected AND subscribed again -- a second drop mid-way just loops."""
        while True:
            await asyncio.sleep(1)
            try:
                await self._connect()
                await self._subscribe_all()
                if self.ib.isConnected():
                    return
            except Exception as e:
                log.warning('reconnect attempt failed (%r); trying again', e)
            self.connected = False

    async def run(self):
        self.ib.pendingTickersEvent += self._on_tickers
        self.ib.errorEvent += self._on_error
        self.ib.disconnectedEvent += self._on_disconnect
        await self._connect()
        await self._subscribe_all()
        asyncio.ensure_future(self._route_loop())
        while True:                                   # notice a dead connection even without an event
            await asyncio.sleep(10)
            if self.connected and not self.ib.isConnected():
                self._on_disconnect()
