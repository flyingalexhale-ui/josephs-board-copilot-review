"""IBKR paper/live trader: streams the strategy's symbol from IB Gateway, builds 1-minute bars,
runs the strategy (algo/core_trader.py), and in live mode sends real orders through
live_broker.py. Paper mode never sends an order.

Run from the ibkr-algo folder (./jb does this for you):
    python -m algo.paper_trader --strategy strategies/rsi_reversal.yaml --mode paper

Client id 47 by default (the board bridge uses 17, the scanner 37, backtest fetches 57), so
all of them can be connected to IB Gateway at the same time.
"""
import argparse
import asyncio
import calendar
import logging
import os
import signal
import sys
import threading
import time

import pandas as pd
from ib_async import IB, ContFuture, Future, Stock, StopOrder

from . import autohedge
from . import guards
from . import options as opt
from .core_trader import TraderCore, default_options_path, default_status_path
from .strategy import Strategy

log = logging.getLogger('paper_trader')
STALL_S = {'regular': 300, 'premarket': 900, 'afterhours': 900, 'overnight': 1800}   # quiet this long: prices stopped (jb-ready)
BSTOP_REF = 'jb-backup-stop'      # orderRef of the algo's backup stop at IBKR (jb-ready)
IGNORE = {2103, 2104, 2105, 2106, 2107, 2108, 2119, 2157, 2158, 10167, 10190, 300}


def _ms(dt):
    try:
        return int(dt.timestamp() * 1000) if dt else None
    except Exception:
        return None


class IBKRTrader:
    def __init__(self, strategy, args):
        if strategy.symbol.endswith('=F'):
            raise SystemExit('ibkr-algo trades stocks/ETFs only (the strategy symbol is a future).')
        self.args = args
        self.core = TraderCore(strategy, args.mode, logs_dir=os.path.dirname(args.status_path),
                               starting_cash=args.starting_cash, commission_per_share=args.commission_per_share,
                               slippage_pct=args.slippage_pct, max_order_quantity=args.max_order_quantity,
                               max_order_notional_usd=args.max_order_notional_usd,
                               status_path=args.status_path, broker_name='ibkr',
                               options_path=args.options_path)
        self.ib = IB()
        self.contract = None
        self.overnight = None             # the same stock on IBKR's OVERNIGHT venue (8pm-3:50am ET)
        self.broker = None
        self.seen = 0
        self.sub_at = 0.0
        self._reconnect_task = None
        self._warned_delayed = False
        self.alive_at = time.time()       # last price for the symbol (stalled-price check)
        if args.mode == 'live':
            from .live_broker import LiveBroker
            self.broker = LiveBroker(self.ib)
            self.core.place_live = self._place_live
            self.core.stop_api = {'place': self._bs_place, 'cancel': self._bs_cancel, 'status': self._bs_status,
                                  'listed': self._bs_listed,
                                  'hours': 'works 4 am - 8 pm; IBKR takes no stop orders overnight (8 pm - 4 am)'}
            # kill switch checks it first; while IB Gateway is disconnected (nightly restart) its position list is
            # empty, so answer 'unknown' (None) instead of 0 -- 0 would make the algo forget shares it still holds
            self.core.broker_position = lambda: (self.broker.position(self.contract)
                                                 if self.ib.isConnected() and self.core.connected else None)
            # Auto-Hedge (jb-hedge): its orders and the broker's count of it (None while disconnected, never 0)
            self.core.hedge.place = self._place_hedge
            self.core.hedge.position = lambda: (self.broker.position(self.hedge_contract)
                                                if self.hedge_contract is not None and self.ib.isConnected() and self.core.connected else None)
        self.hedge_sym = None             # Auto-Hedge instrument streamed (jb-hedge)
        self.hedge_contract = None
        self.hedge_overnight = None
        self.hedge_rids = []

    def _place_live(self, spec, filled, failed, unconfirmed, unfilled):
        a = self.args
        contract = self.contract
        if spec['session'] == 'overnight':
            if self.overnight is None:
                raise guards.GuardRejected(f'{self.core.strategy.symbol} does not trade on IBKR\'s overnight venue')
            contract = self.overnight
        self.broker.place_order(self.core.strategy, spec, contract, filled, failed, unconfirmed, unfilled,
                                max_order_quantity=a.max_order_quantity,
                                max_order_notional_usd=a.max_order_notional_usd)

    # ---------------- Auto-Hedge (jb-hedge) ----------------
    def _place_hedge(self, spec, filled, failed, unconfirmed, unfilled):
        a = self.args
        contract = self.hedge_contract
        if contract is None or (self.hedge_sym or '') != spec.get('symbol'):
            raise guards.GuardRejected(f"IBKR has not looked up the hedge {spec.get('symbol')} yet")
        if spec['session'] == 'overnight' and getattr(contract, 'secType', 'STK') == 'STK':
            if self.hedge_overnight is None:
                raise guards.GuardRejected(f"{spec.get('symbol')} does not trade on IBKR's overnight venue")
            contract = self.hedge_overnight
        self.broker.place_order(autohedge._As(self.core.strategy, spec['symbol']), spec, contract, filled, failed, unconfirmed,
                                unfilled, max_order_quantity=a.max_order_quantity, max_order_notional_usd=a.max_order_notional_usd)

    async def _hedge_subscribe(self, force=False):
        """Stream the Auto-Hedge instrument's prices (a fund: SMART + the overnight venue; MNQ / MES: the front month)."""
        want = self.core.hedge.feed_symbol()
        if want == self.hedge_sym and not force:
            return
        for rid in self.hedge_rids:
            try:
                self.ib.client.cancelMktData(rid)
            except Exception:
                pass
        self.hedge_rids, self.hedge_contract, self.hedge_overnight, self.hedge_sym = [], None, None, None
        if not want:
            return
        try:
            if want in autohedge.FUTURES:
                q = await self.ib.qualifyContractsAsync(ContFuture(want, 'CME', currency='USD'))
                if not q or q[0] is None or not q[0].conId:
                    raise RuntimeError(f'IBKR could not look up the {want} future')
                c = Future(conId=q[0].conId, exchange='CME')
                await self.ib.qualifyContractsAsync(c)
            else:
                c = Stock(want, 'SMART', 'USD')
                q = await self.ib.qualifyContractsAsync(c)
                if not q or q[0] is None or not c.conId:
                    raise RuntimeError(f'IBKR does not recognise the hedge {want}')
                on = Stock(c.symbol, 'OVERNIGHT', 'USD', primaryExchange=c.primaryExchange)
                q2 = await self.ib.qualifyContractsAsync(on)
                if q2 and q2[0] is not None and on.conId:
                    self.hedge_overnight = on
            self.hedge_contract, self.hedge_sym = c, want
            for con in (c, self.hedge_overnight):
                if con is not None:
                    t = self.ib.reqMktData(con, '233', False, False)
                    rid = self.ib.wrapper.ticker2ReqId['mktData'].get(t)
                    if rid:
                        self.hedge_rids.append(rid)
            log.warning('Auto-Hedge: streaming %s', want)
        except Exception as e:
            self.core.hedge.note = f'could not stream the hedge {want}: {e}'
            log.warning('Auto-Hedge: could not stream %s (%r)', want, e)

    def _hedge_ticks(self, t, now):
        for x in list(getattr(t, 'ticks', None) or []):
            if getattr(x, 'tickType', None) in (48, 77, 4) and now - self.sub_at >= 3:
                p = getattr(x, 'price', None)
                if p is not None and p == p and p > 0:
                    self.core.on_hedge_tick(p, _ms(getattr(x, 'time', None)) or int(now * 1000))

    # ---------------- backup stop at IBKR (jb-ready) ----------------
    def _bs_trades(self):
        cid = getattr(self.contract, 'conId', None)
        return [t for t in self.ib.trades() if getattr(t.order, 'orderRef', '') == BSTOP_REF
                and getattr(t.contract, 'conId', None) == cid]

    def _bs_place(self, side, shares, stop):
        o = StopOrder(side.upper(), int(shares), float(stop))
        o.tif, o.outsideRth, o.orderRef = 'GTC', True, BSTOP_REF    # stays until cancelled; pre/after-hours too
        if self.broker.account:
            o.account = self.broker.account
        t = self.ib.placeOrder(self.contract, o)
        return {'id': t.order.orderId}

    def _bs_find(self, b):
        ts = [t for t in self._bs_trades() if t.order.orderId == b.get('id')] or \
             [t for t in self._bs_trades() if not t.isDone()]
        return ts[-1] if ts else None

    def _bs_status(self, b):
        t = self._bs_find(b)
        if t is None:
            return 'unknown', 0, None
        os_ = t.orderStatus
        if os_.status == 'Filled':
            return 'filled', os_.filled or 0, os_.avgFillPrice or None
        if os_.status in ('Cancelled', 'ApiCancelled', 'Inactive'):
            return 'cancelled', os_.filled or 0, os_.avgFillPrice or None
        return 'working', os_.filled or 0, os_.avgFillPrice or None

    def _bs_listed(self, b):
        """Is a backup stop among IBKR's open orders? (ib_async reloads them on every reconnect.) None while not
        connected and subscribed, when the list may be incomplete."""
        if not (self.ib.isConnected() and self.core.connected):
            return None
        cid = getattr(self.contract, 'conId', None)
        return any(getattr(t.order, 'orderRef', '') == BSTOP_REF and getattr(t.contract, 'conId', None) == cid
                   for t in self.ib.openTrades())

    def _bs_cancel(self, b):
        t = self._bs_find(b)
        if t is None:
            raise RuntimeError('IBKR does not list the backup stop right now')
        self.ib.cancelOrder(t.order)

    # ---------------- connection ----------------
    async def connect(self):
        ports = [self.args.port] if self.args.port else [4001, 4002, 7496, 7497]
        warned, t0 = False, time.time()
        while True:
            in_use, err = None, None
            for port in ports:
                try:
                    await self.ib.connectAsync(self.args.host, port, clientId=self.args.client_id, timeout=8)
                    self.ib.reqMarketDataType(self.args.md_type)
                    log.info('connected to IB Gateway on port %s (client id %s)', port, self.args.client_id)
                    return
                except Exception as e:
                    if 'already in use' in str(e):
                        in_use = port
                    elif err is None or not isinstance(e, ConnectionRefusedError):
                        err = e
            if in_use:     # just restarted: IB Gateway still releasing our previous connection
                if time.time() - t0 > 30 and not warned:
                    log.warning('client id %s is still in use on port %s: another program is connected '
                                'with the same client id', self.args.client_id, in_use)
                    warned = True
                await asyncio.sleep(2)
                continue
            if not warned:
                log.warning('cannot reach IB Gateway/TWS on port(s) %s (%r); retrying every 5s. Is it running '
                            'and logged in?', ', '.join(map(str, ports)), err)
                warned = True
            self.core.connected, self.core.last_error = False, 'waiting for IB Gateway'
            self.core.write_status()
            await asyncio.sleep(5)

    async def subscribe(self):
        c = Stock(self.core.strategy.symbol.replace('.', ' '), 'SMART', 'USD')
        q = await self.ib.qualifyContractsAsync(c)
        if not q or q[0] is None or not c.conId:
            if self.contract is not None:      # a reconnect (e.g. IBKR's nightly reset): lookups fail for a while, keep trying
                raise RuntimeError(f'IBKR could not look up {self.core.strategy.symbol} right now')
            raise SystemExit(f'IBKR does not recognise the strategy symbol {self.core.strategy.symbol!r}')
        self.contract = c
        self.sub_at = time.time()
        self.mkt_rids = []
        self._req_mkt(c)
        if not self.args.no_tbt:
            self.ib.reqTickByTickData(c, 'Last', 0, False)
        # the overnight venue: its trades (8pm-3:50am ET) make the overnight bars, and overnight
        # orders go there. SMART only covers 4am-8pm.
        self.overnight = None
        try:
            on = Stock(c.symbol, 'OVERNIGHT', 'USD', primaryExchange=c.primaryExchange)
            q = await self.ib.qualifyContractsAsync(on)
            if q and q[0] is not None and on.conId:
                self.overnight = on
                self._req_mkt(on)
            else:
                log.info('%s does not trade on IBKR\'s overnight venue', self.core.strategy.symbol)
        except Exception as e:
            log.warning('overnight venue lookup failed (%r) -- no overnight data or orders', e)
        self.core.connected, self.core.last_error = True, None
        self.core.write_status()
        await self._hedge_subscribe(force=True)        # Auto-Hedge instrument, if any (jb-hedge)

    def _req_mkt(self, contract):
        """reqMktData, remembering its request id: the regular and overnight-venue contracts have the same conId, so
        ib_async keeps ONE ticker for both and knows only the latest request -- cancelling by contract would cancel
        the overnight feed twice and leave the regular one running (an IBKR market-data line used up each time)."""
        t = self.ib.reqMktData(contract, '233', False, False)
        rid = self.ib.wrapper.ticker2ReqId['mktData'].get(t)
        if rid:
            self.mkt_rids.append(rid)

    def on_disconnect(self):
        if self.core.connected:
            log.warning('lost the connection to IB Gateway; reconnecting')
            self.core.connected, self.core.last_error = False, 'IB Gateway disconnected'
            self.core.write_status()
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self):
        """Until connected AND subscribed again (a second drop mid-way just loops)."""
        while True:
            await asyncio.sleep(1)
            try:
                await self.connect()
                await self.subscribe()
                if self.ib.isConnected():
                    return
            except SystemExit:
                raise
            except Exception as e:
                log.warning('reconnect attempt failed (%r); trying again in 10 s', e)
                await asyncio.sleep(9)

    # ---------------- data ----------------
    def on_tickers(self, batch):
        """Tick-by-tick trades carry IBKR's exact time; streamed trades are stamped on arrival;
        the last-trade snapshot sent right after subscribing (possibly hours old) is skipped."""
        now = time.time()
        for t in batch:
            hc = self.hedge_contract
            if hc is not None and t.contract.conId == hc.conId and (self.contract is None or hc.conId != self.contract.conId):
                if getattr(t, 'marketDataType', 1) not in (3, 4):      # never delayed prices
                    self._hedge_ticks(t, now)
                continue
            if not self.contract or t.contract.conId != self.contract.conId:
                continue
            if getattr(t, 'marketDataType', 1) in (3, 4):
                # IBKR's delayed (10-20 min old) prices: never traded on, in paper or live mode
                if not self._warned_delayed:
                    self._warned_delayed = True
                    log.warning('IBKR is sending DELAYED prices for %s (no live-data subscription) -- the algo '
                                'ignores them and does not trade', self.core.strategy.symbol)
                self.core.last_error = 'IBKR sends only delayed prices for this symbol: not trading'
                continue
            for x in list(getattr(t, 'tickByTicks', None) or []):
                tm = _ms(getattr(x, 'time', None)) or int(now * 1000)
                if tm >= self.seen:
                    self.seen = tm
                    self.alive_at = now
                    self.core.on_tick(getattr(x, 'price', None), tm, getattr(x, 'size', 0) or 0)
            if getattr(t, 'tickByTicks', None):
                continue                       # tick-by-tick already gave every trade
            for x in list(getattr(t, 'ticks', None) or []):
                if getattr(x, 'tickType', None) in (48, 77, 4) and now - self.sub_at >= 3:   # 68 = delayed: never
                    p = getattr(x, 'price', None)
                    if p is not None and p == p and p > 0:
                        self.alive_at = now
                        size = getattr(x, 'size', 0) if getattr(x, 'tickType', None) in (48, 77) else 0
                        self.core.on_tick(p, _ms(x.time) or int(now * 1000), size or 0)

    def on_error(self, req_id, code, text, contract=None):
        if code in IGNORE:
            return
        overnight = getattr(contract, 'exchange', '') == 'OVERNIGHT'
        if code == 200 and overnight:
            return                      # not on the overnight venue: subscribe() says so once
        if overnight and code in (354, 10089, 10168, 10197):
            log.warning('[IBKR %s] no overnight market data (%s) -- the regular 4am-8pm feed still works, but '
                        'no overnight bars form, so nothing is traded overnight', code, text)
            return
        log.warning('[IBKR %s] %s', code, text)
        if code in (354, 10089, 10168, 10197, 1100, 2110):
            self.core.last_error = f'IBKR {code}: {text}'
        if code == 1100 and self.core.connected:
            # IB Gateway is still running but lost its link to IBKR: no prices, no orders. Treat it as a lost
            # connection, so safe mode holds orders until it is back and the position matches (jb-ready)
            self.core.connected = False
            self.core.write_status()
        elif code in (1101, 1102) and self.contract is not None and self.ib.isConnected():
            if code == 1101:          # link back but IBKR dropped the price feed: ask for it again
                asyncio.ensure_future(self._resubscribe())
            self.core.connected, self.core.last_error = True, None
            self.core.write_status()

    def _stall_check(self):
        """Prices can stop without any error. While the algo may trade (or holds a position) in the session
        now, no price for STALL_S: push the phone once and ask IBKR for prices again (jb-ready)."""
        if not self.core.connected or self.contract is None:
            self.alive_at = time.time()
            return
        sess, ok = self.core.session_now()
        if sess is None or not (ok or self.core.portfolio.position is not None):
            self.alive_at = time.time()
            return
        quiet = time.time() - self.alive_at
        if quiet < STALL_S.get(sess, 900):
            return
        self.alive_at = time.time()
        mins = int(quiet // 60)
        log.warning('no IBKR prices for %s for %d min -- asking for them again', self.core.strategy.symbol, mins)
        self.core.last_error = f'no prices for {mins} min: asked IBKR again'
        held = self.core.portfolio.position
        threading.Thread(target=self.core._push, daemon=True, args=('IBKR algo: prices stopped',
                        f'No {self.core.strategy.symbol} prices for {mins} min, so the algo asked IBKR again.'
                        + (' It holds a position: its stop-loss only works while prices arrive.' if held else ''),
                        'rotating_light' if held else 'warning')).start()
        asyncio.ensure_future(self._resubscribe())

    async def _resubscribe(self):
        c, on = self.contract, self.overnight
        for rid in getattr(self, 'mkt_rids', []):     # each request by its own id (see _req_mkt)
            try:
                self.ib.client.cancelMktData(rid)
            except Exception:
                pass
        self.mkt_rids = []
        try:
            t = self.ib.ticker(c)
            if t is not None:
                self.ib.wrapper.ticker2ReqId['mktData'].pop(t, None)
        except Exception:
            pass
        try:
            self.ib.cancelTickByTickData(c, 'Last')
        except Exception:
            pass
        await asyncio.sleep(1)
        try:
            self.sub_at = time.time()
            self._req_mkt(c)
            if not self.args.no_tbt:
                self.ib.reqTickByTickData(c, 'Last', 0, False)
            if on is not None:            # review 2026-10-11 #6: the overnight venue too (its prices make the
                self._req_mkt(on)         # 8 pm - 4 am bars, and IBKR's nightly reset falls in that session)
            log.warning('IBKR link restored (1101): asked for %s prices again', self.core.strategy.symbol)
        except Exception as e:
            log.warning('could not ask for prices again after 1101 (%r)', e)

    async def warm_up(self):
        try:
            bars = await self.ib.reqHistoricalDataAsync(self.contract, '', '2 D', '1 min', 'TRADES', False, 2, timeout=60)
            rows = [{'t': calendar.timegm(b.date.timetuple()) * 1000, 'open': b.open, 'high': b.high,
                     'low': b.low, 'close': b.close, 'volume': b.volume} for b in bars or []]
            if rows:
                df = pd.DataFrame(rows)
                df.index = pd.to_datetime(df.pop('t'), unit='ms', utc=True)
                self.core.seed_bars(df)
        except Exception as e:
            log.warning('warm-up history failed (starting with none): %r', e)

    async def timer(self):
        n = 0
        while True:
            await asyncio.sleep(2)
            n += 1
            try:
                self.core.on_timer(int(time.time() * 1000))
                if self.core.connected and not self.ib.isConnected():
                    self.on_disconnect()                  # dropped without an event
                self._stall_check()
                if n % 15 == 0:
                    self.core.write_status()
                if self.broker and n % 30 == 1 and self.core.connected:
                    asyncio.ensure_future(self._refresh_bp())
                if n % 5 == 0 and self.core.connected and self.core.hedge.feed_symbol() != self.hedge_sym:
                    asyncio.ensure_future(self._hedge_subscribe())     # Auto-Hedge turned on / changed (jb-hedge)
            except Exception as e:
                log.exception('timer failed: %r', e)

    async def _refresh_bp(self):
        """Live: the account's buying power from IBKR, for the entry check in core_trader (jb-ready)."""
        try:
            acct = self.broker.account or ''
            vals = await asyncio.wait_for(self.ib.accountSummaryAsync(acct), 15)
            for v in vals or []:
                if v.tag == 'BuyingPower' and (not acct or v.account == acct) and (v.currency or 'USD') in ('USD', 'BASE'):
                    self.core.buying_power, self.core.buying_power_at = float(v.value), time.time()
                    return
        except Exception as e:
            log.warning('could not read the IBKR buying power (%r)', e)

    def check_account(self):
        """Live mode: make sure we know which IBKR account the orders go to."""
        accts = list(self.ib.managedAccounts() or self.ib.client.getAccounts() or [])
        want = (os.environ.get('IBKR_ACCOUNT_ID') or '').strip()
        if want:
            if accts and want not in accts:
                raise SystemExit(f'\nLIVE TRADING NOT STARTED: IBKR_ACCOUNT_ID={want} is not one of this '
                                 f'login\'s accounts ({", ".join(accts)}).\n')
            self.broker.account = want
        elif len(accts) > 1:
            raise SystemExit(f'\nLIVE TRADING NOT STARTED: this IBKR login has several accounts '
                             f'({", ".join(accts)}). Put the one to trade in settings.env as '
                             f'IBKR_ACCOUNT_ID=... and run again.\n')
        log.warning('live orders go to IBKR account %s', want or (accts[0] if accts else '(the login\'s default)'))

    async def run(self):
        self.ib.pendingTickersEvent += self.on_tickers
        self.ib.errorEvent += self.on_error
        self.ib.disconnectedEvent += self.on_disconnect
        self.core.write_status()
        await self.connect()
        if self.broker:
            self.check_account()
        await self.subscribe()
        if self.broker:                                   # live: ledger must match the account
            await asyncio.sleep(1)                        # (positions arrive right after connecting)
            listed = True
            try:
                trades = await asyncio.wait_for(self.ib.reqAllOpenOrdersAsync(), 20)
            except Exception as e:
                trades, listed = [], False
                log.warning('could not list open IBKR orders (%r) -- check TWS / the IBKR app for any open '
                            '%s order', e, self.core.strategy.symbol)
            mine = [t for t in trades or [] if getattr(t.contract, 'conId', None) == self.contract.conId and not t.isDone()]
            stops = [t for t in mine if getattr(t.order, 'orderRef', '') == BSTOP_REF]    # the algo's own backup stop
            if stops:
                if self.core.portfolio.position is None:
                    for t in stops:                       # left over from a position that is gone: remove it
                        self.ib.cancelOrder(t.order)
                    log.warning('cancelled a leftover backup stop at IBKR (the algo holds nothing)')
                    self.core.bstop = None
                else:
                    t = stops[-1]
                    self.core.bstop = dict(self.core.bstop or {}, id=t.order.orderId, symbol=self.core.strategy.symbol,
                                           side=t.order.action.lower(), shares=int(t.order.totalQuantity),
                                           stop=float(t.order.auxPrice))
                    log.warning('backup stop still at IBKR: %s %s stop %s', t.order.action, t.order.totalQuantity, t.order.auxPrice)
                self.core._bstop_save()
            elif self.core.bstop is not None and listed:
                if not self.core.bstop_filled_while_away(self.broker.position(self.contract)):
                    self.core.bstop = None                # gone (cancelled in the app or by SELL ALL): placed again
                    self.core._bstop_save()
            if any(t not in stops for t in mine):
                raise SystemExit(f'\nLIVE TRADING NOT STARTED: an order for {self.core.strategy.symbol} is still open '
                                 f'at IBKR. Cancel it in TWS or the IBKR app (or let it finish), then start again.\n')
            if any(getattr(t.order, 'orderRef', '') == 'jb-hedge' and not t.isDone() for t in trades or []):
                raise SystemExit('\nLIVE TRADING NOT STARTED: an Auto-Hedge order (orderRef jb-hedge) is still open at IBKR. '
                                 'Cancel it in TWS or the IBKR app (or let it finish), then start again.\n')
            if self.core.hedge.units() or self.core.hedge.feed_symbol():   # Auto-Hedge record vs the broker (jb-hedge):
                await self._hedge_subscribe(force=True)                   # less there: booked closed; more: reported
                await asyncio.sleep(0.5)
                self.core.hedge.startup(self.core.hedge.position())
            self.core.check_matches_broker(self.broker.position(self.contract), 'IBKR')
            log.warning('IBKR position in %s matches the algo\'s record (%d shares)',
                        self.core.strategy.symbol, self.core.ledger_shares())
        await self.warm_up()
        self.core.write_status()
        asyncio.ensure_future(self.timer())
        log.info('trader running (%s mode) for %s', self.core.mode, self.core.strategy.symbol)
        try:
            await asyncio.Future()
        finally:                                          # Ctrl+C: don't leave an order working
            if self.broker is not None and self.ib.isConnected():
                n = self.broker.cancel_working()
                if n:
                    log.warning('STOPPED WITH AN ORDER STILL WORKING: asked IBKR to cancel it. Check TWS or the IBKR '
                                'app: if part of it filled, the next start will say the position differs -- then use '
                                './jb live-reset after checking.')
                    await asyncio.sleep(1)                # let the cancel go out before disconnecting


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('ib_async').setLevel(logging.WARNING)   # our own lines say what matters
    ap = argparse.ArgumentParser(description='IBKR paper/live trader')
    ap.add_argument('--strategy', required=True)
    ap.add_argument('--mode', choices=['paper', 'live'], default='paper')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=int(os.environ.get('IBKR_GATEWAY_PORT') or 0) or None)
    ap.add_argument('--client-id', type=int, default=None, help='default 47 (paper) / 48 (live)')
    ap.add_argument('--md-type', type=int, default=1)
    ap.add_argument('--no-tbt', action='store_true')
    ap.add_argument('--starting-cash', type=float, default=100_000.0)
    ap.add_argument('--commission-per-share', type=float, default=0.0)
    ap.add_argument('--slippage-pct', type=float, default=0.0)
    ap.add_argument('--max-order-quantity', type=int, default=10_000)
    ap.add_argument('--max-order-notional-usd', type=float, default=None)
    ap.add_argument('--status-path', default=None, help='default: <project>/logs/live_status.json')
    ap.add_argument('--options-path', default=None, help='default: <project>/trading_options.json (the dashboard\'s)')
    args = ap.parse_args()
    if args.md_type != 1:         # the algo never asks for (or trades on) IBKR's delayed prices
        log.warning('ignoring --md-type %s: ibkr-algo uses IBKR live prices only', args.md_type)
        args.md_type = 1
    if args.client_id is None:
        args.client_id = 48 if args.mode == 'live' else 47
    args.status_path = args.status_path or default_status_path(args.strategy)
    args.options_path = args.options_path or default_options_path(args.strategy)
    strategy = Strategy.from_yaml(args.strategy)
    if args.mode == 'live':
        try:                      # both switches must be on BEFORE anything starts
            guards.check_live_trading_enabled(strategy)
        except guards.GuardRejected as e:
            raise SystemExit(f'\nLIVE TRADING NOT STARTED: {e}\n')
        cap = args.max_order_notional_usd or strategy.max_order_notional_usd
        opts, source, _ = opt.effective(strategy.trading_raw, args.options_path)
        log.warning('=' * 70)
        log.warning('LIVE MODE -- REAL ORDERS CAN BE PLACED ON IBKR. Ctrl+C within 5 s to abort.')
        log.warning('strategy %r on %s; max order value %s', strategy.name, strategy.symbol,
                    f'${cap:,.0f}' if cap else 'not capped')
        log.warning('trading (%s): %s', source, opt.describe(opts, 'live'))
        log.warning('(the trading options can be changed on the dashboard while this runs)')
        log.warning('=' * 70)
        time.sleep(5)
    trader = IBKRTrader(strategy, args)
    signal.signal(signal.SIGHUP, signal.default_int_handler)   # Terminal window closed: stop cleanly, like Ctrl+C
    # Stop Live on the algo tab (launchd's SIGTERM to the Go Live service) stops it the same clean way: orders still
    # working are cancelled, nothing new is sent, the position stays (jb-golive). SIGINT set too: a background
    # process may have been started with it ignored.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        pass
    finally:
        trader.core.save()


REFUSED_RC = 3          # live trading refused to start (LIVE TRADING NOT STARTED: ...): ./jb live and the Go Live service tell
                        # a start-up refusal (nothing ran) apart from a stop while running (jb-golive)


def run_main():
    try:
        main()
    except SystemExit as e:
        if isinstance(e.code, str) and 'LIVE TRADING NOT STARTED' in e.code:
            print(e.code, flush=True)
            sys.exit(REFUSED_RC)
        raise


if __name__ == '__main__':
    run_main()
