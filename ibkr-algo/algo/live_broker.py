"""The ONLY file in ibkr-algo that sends a real order to IBKR. Read it before going live.

Uses ib_async's own order API (checked against the ib_async 2.x source) and IBKR's rules for
each session:
  * regular hours (9:30-16:00 ET): a limit order, or a market order if chosen
  * pre-market / after-hours: a LIMIT order with outsideRth=True ("allow outside regular
    trading hours") -- IBKR only takes limit orders then
  * overnight (20:00-3:50 ET): a LIMIT order sent to IBKR's OVERNIGHT venue (contract
    exchange OVERNIGHT, primaryExchange = the stock's listing exchange); only limit orders
  * time in force DAY; the algo cancels a limit order itself if it isn't filled in time
  * ib.placeOrder returns a Trade at once; its status then moves PendingSubmit -> Submitted ->
    Filled (or Cancelled if IBKR rejects it, e.g. error 201 "insufficient buying power")
  * the ledger books only what IBKR reports FILLED, at IBKR's average fill price and filled
    quantity -- including a partial fill that ends in a cancel
  * not filled in time (the limit wasn't reached), or ended by IBKR itself without an error
    (e.g. its session closed): reported UNFILLED -- not an error. An order IBKR refuses (an
    error such as 201 "insufficient buying power") is reported REJECTED.
  * if even the cancel isn't confirmed within 30 s (e.g. the connection dropped), or IBKR
    reports a fill AFTER the order was reported as not filled, live trading PAUSES until a
    person has checked the account

Auto-Hedge orders (jb-hedge) go through the same place_order: tagged orderRef 'jb-hedge'; a futures hedge (MNQ / MES)
is checked against max_order_notional_usd at its full size (price x $ per point) and needs no OVERNIGHT route.

IB Gateway must allow orders: Configure > Settings > API > Settings > untick "Read-Only API".
(The board bridge and scanner work either way -- they never place orders.)
"""
import asyncio
import logging

from ib_async import LimitOrder, MarketOrder

from . import guards

log = logging.getLogger('live_broker')
# IBKR messages that are notices, not a refusal of the order: ib_async's own warning list,
# 2100-2199 (informational), 202 "order cancelled" and 10147-10149 (cancel of a finished order)
# (110 "price does not conform to the minimum tick" is NOT a notice: it can end the order)
_NOTICES = frozenset({105, 165, 202, 321, 329, 399, 404, 434, 492, 10147, 10148, 10149, 10167})


def _benign(code):
    return code in _NOTICES or 2100 <= code < 2200


class LiveBroker:
    def __init__(self, ib, account=None):
        self.ib = ib
        self.account = account          # IBKR_ACCOUNT_ID, only needed if the login has several
        self.working = []               # trades placed by this run that haven't finished

    def cancel_working(self):
        """On shutdown: cancel any order still working, so it can't fill unseen after the algo
        stops. Returns how many it tried to cancel."""
        live = [t for t in self.working if not t.isDone()]
        for t in live:
            try:
                self.ib.cancelOrder(t.order)
            except Exception as e:
                log.error('could not cancel order %s on shutdown: %r', t.order.orderId, e)
        return len(live)

    def position(self, contract):
        """Shares of this contract the account really holds (signed), from IB Gateway."""
        total = 0.0
        for p in self.ib.positions(self.account or ''):
            if getattr(p.contract, 'conId', None) == contract.conId:
                total += float(p.position)
        return total

    @staticmethod
    def build_order(spec):
        """ib_async order for an order spec from core_trader.order_spec()."""
        action, qty, sess = spec['side'].upper(), int(spec['shares']), spec['session']
        if spec['type'] == 'market':
            if sess != 'regular':
                raise guards.GuardRejected('market orders are only sent 9:30-16:00 ET')
            order = MarketOrder(action, qty)
        else:
            if not spec.get('limit') or spec['limit'] <= 0:
                raise guards.GuardRejected('a limit order needs a limit price')
            order = LimitOrder(action, qty, float(spec['limit']))
        order.tif = 'DAY'
        if sess in ('premarket', 'afterhours'):
            order.outsideRth = True
        return order

    def place_order(self, strategy, spec, contract, on_filled, on_failed, on_unconfirmed, on_unfilled,
                    max_order_quantity=10_000, max_order_notional_usd=None, cancel_s=30):
        side, shares, sess = spec['side'], int(spec['shares']), spec['session']
        guard_price = max(spec['ref_price'], spec.get('limit') or 0.0) * float(spec.get('mult') or 1.0)   # futures: per contract
        guards.check_live_trading_enabled(strategy)
        guards.check_order(strategy, side, strategy.symbol, shares, guard_price,
                           max_order_quantity=max_order_quantity, max_order_notional_usd=max_order_notional_usd,
                           closing=spec.get('closing', False))
        if getattr(contract, 'secType', 'STK') != 'FUT' and (sess == 'overnight') != (getattr(contract, 'exchange', '') == 'OVERNIGHT'):
            raise guards.GuardRejected(f'internal: {sess} order must go to the '
                                       f'{"OVERNIGHT" if sess == "overnight" else "SMART"} route')
        order = self.build_order(spec)
        if spec.get('hedge'):
            order.orderRef = 'jb-hedge'               # Auto-Hedge order (jb-hedge): found again at start-up
        if self.account:
            order.account = self.account
        wait_s = int(spec.get('wait_s') or 60)
        how = 'market' if spec['type'] == 'market' else f"limit {spec['limit']:g}"
        log.warning('PLACING LIVE ORDER: %s %d %s (%s, %s%s, via %s)', side.upper(), shares, strategy.symbol, how,
                    sess, ', outside RTH' if order.outsideRth else '', contract.exchange)
        trade = self.ib.placeOrder(contract, order)
        self.working = [t for t in self.working if not t.isDone()] + [trade]
        st = {'done': False, 'we_cancelled': False, 'before_cancel': None, 'reported': None, 'booked': 0}

        def finish_filled(tr, note):
            os_ = tr.orderStatus
            filled = int(round(os_.filled or 0))
            avg = os_.avgFillPrice or spec.get('limit') or spec['ref_price']
            st['reported'], st['booked'] = 'filled', filled
            log.warning('LIVE ORDER %s: %s %s @ %s', note.upper(), filled, strategy.symbol, avg)
            on_filled(avg, filled, note)

        def late_fill(tr):
            """IBKR filled (more of) an order after we had already reported its outcome."""
            filled = int(round(tr.orderStatus.filled or 0))
            if filled <= st['booked'] or st['reported'] == 'unconfirmed':
                return
            st['reported'] = 'unconfirmed'
            log.error('IBKR reports %s of the %s %s filled AFTER the order was reported as %s', filled, side,
                      strategy.symbol, 'not filled' if not st['booked'] else f"{st['booked']} filled")
            on_unconfirmed(f'IBKR filled {filled} of the {side} of {shares} {strategy.symbol} after it was reported '
                           f'as {"not filled" if not st["booked"] else str(st["booked"]) + " filled"}')

        def ended(tr, status):
            st['done'] = True
            if (tr.orderStatus.filled or 0) > 0:          # part filled, then cancelled/rejected
                if not st['we_cancelled']:                # (ended by an error: make sure the rest is dropped)
                    try:
                        self.ib.cancelOrder(tr.order)
                    except Exception:
                        pass
                finish_filled(tr, 'partly filled by IBKR')
                return
            codes = [e.errorCode for e in tr.log if getattr(e, 'errorCode', 0)]
            why = next((e.message for e in reversed(tr.log) if e.message), status)
            rejected = any(not _benign(c) for c in codes) or \
                (st['we_cancelled'] and st['before_cancel'] == 'Inactive')
            if rejected:
                st['reported'] = 'failed'
                log.error('LIVE ORDER NOT ACCEPTED: %s', why)
                try:        # ib_async marks an order cancelled on an error; make sure IBKR drops it too
                    self.ib.cancelOrder(tr.order)
                except Exception:
                    pass
                on_failed(f'IBKR: {why}')
                return
            st['reported'] = 'unfilled'
            if st['we_cancelled']:
                log.warning('LIVE ORDER NOT FILLED within %ss -- cancelled (%s)', wait_s, why)
                on_unfilled(f'not filled within {wait_s}s ({how}) -- cancelled')
            else:
                log.warning('LIVE ORDER ENDED BY IBKR before it filled (%s)', why)
                on_unfilled(f'IBKR ended the order before it filled ({how}; e.g. its session closed)')

        def on_status(tr):
            status = tr.orderStatus.status
            if st['done']:
                late_fill(tr)
                return
            if status == 'Filled':
                st['done'] = True
                finish_filled(tr, 'filled by IBKR')
            elif status in ('Cancelled', 'ApiCancelled'):
                last_code = next((e.errorCode for e in reversed(tr.log) if getattr(e, 'errorCode', 0)), 0)
                if st['we_cancelled'] and last_code == 161:
                    # 161 = "not in a cancellable state": our cancel crossed a fill. ib_async marks the
                    # order cancelled locally; wait for IBKR's real final status (or the watchdog)
                    return
                ended(tr, status)
            # 'Inactive' (held or refused by IBKR) is left to the watchdog below

        trade.statusEvent += on_status

        async def watchdog():
            await asyncio.sleep(wait_s)
            if st['done']:
                return
            st['before_cancel'] = trade.orderStatus.status
            if st['before_cancel'] == 'Inactive' and not (trade.orderStatus.filled or 0):
                # IBKR refused or is holding the order: report it as not accepted (ib_async won't
                # cancel an Inactive order, so ask IBKR directly to drop it)
                st['done'], st['reported'] = True, 'failed'
                why = next((e.message for e in reversed(trade.log) if e.message), 'order inactive')
                try:
                    self.ib.client.cancelOrder(trade.order.orderId, '')
                except Exception:
                    pass
                log.error('LIVE ORDER NOT ACCEPTED (inactive): %s', why)
                on_failed(f'IBKR: {why}')
                return
            st['we_cancelled'] = True
            log.warning('no fill after %ss (status %s) -- cancelling the order', wait_s, trade.orderStatus.status)
            try:
                self.ib.cancelOrder(trade.order)
            except Exception as e:
                log.error('cancel failed: %r', e)
            await asyncio.sleep(cancel_s)
            if not st['done']:
                st['done'] = True
                st['reported'] = 'unconfirmed'
                on_unconfirmed(f'IBKR gave no final answer for the {side} of {shares} {strategy.symbol} '
                               f'(last status {trade.orderStatus.status}, filled {trade.orderStatus.filled:g})')
        asyncio.ensure_future(watchdog())
        return trade
