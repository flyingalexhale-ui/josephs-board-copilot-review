"""The ONLY file in webull-algo that sends a real order to Webull. Read it before going live.

Checked against the real SDK source (webull-inc/webull-openapi-python-sdk 3.x) and Webull's
own reference order builder (webull-openapi-skills, stock_order.py):
  * every TradeClient call returns an HTTP response object -- the data is response.json()
  * order fields match Webull's own builder: combo_type NORMAL, instrument_type EQUITY,
    market US, time_in_force DAY, entrust_type QTY, quantity and limit_price as plain
    decimal text, and the session in support_trading_session:
        regular hours            CORE   limit order, or a market order if chosen
        pre-market, after-hours  ALL    limit order only (Webull's "extended hours")
        overnight (8pm-4am ET)   NIGHT  limit order only
  * account: WEBULL_ACCOUNT_ID from settings.env if set; otherwise the single account on the
    API key. With several accounts and no WEBULL_ACCOUNT_ID it refuses to guess.
  * short sales (jb-parity): Webull's order side SHORT (webull.trade.common.order_side) for an entry of a strategy
    with entry side: sell while flat; the cover is a BUY and closing a long is a SELL. Only on a margin account (the
    trader checks it, and asks Webull to preview a 1-share short sale, at start-up), never overnight.

What happens after an order is sent (in a background thread, so prices keep flowing):
  * a clear rejection (Webull answers 4xx, e.g. not enough buying power) -> reported, nothing booked
  * otherwise the order is looked up by its client order id every 2 s: FILLED -> booked at
    Webull's fill price and quantity; CANCELLED/FAILED -> nothing booked (or the filled part)
  * not filled in time (the limit wasn't reached; 60 s by default) -> cancelled and reported
    UNFILLED, which is not an error; a limit order Webull cancels or expires itself (e.g. at
    the end of its session) is reported the same way
  * the cancel not confirmed 30 s later -> UNCONFIRMED, and the trader pauses live trading
    until a person has checked the account
  * if sending itself failed in a way that leaves it unclear whether Webull got the order
    (timeout, server error), the same lookup decides instead of guessing: only Webull
    explicitly answering "no such order" for 10 s (and still after a precautionary cancel)
    counts as not sent; lookups that fail count for nothing, so an order that can't be traced
    ends as UNCONFIRMED (trading pauses) rather than being assumed away
"""
import logging
import os
import threading
import time
import uuid

from . import guards

log = logging.getLogger('live_broker')
FINAL_BAD = ('CANCELLED', 'CANCELED', 'FAILED', 'REJECTED', 'EXPIRED')
NOT_FILLED = ('CANCELLED', 'CANCELED', 'EXPIRED')          # no fill, but not a rejection either
WEBULL_SESSION = {'regular': 'CORE', 'premarket': 'ALL', 'afterhours': 'ALL', 'overnight': 'NIGHT'}


def _rows(data):
    if isinstance(data, dict):
        for k in ('orders', 'positions', 'holdings', 'items', 'data', 'result'):
            if isinstance(data.get(k), list):
                return data[k]
        return [data]
    return data if isinstance(data, list) else []


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _describe(e):
    text = str(e).strip()
    return f'{type(e).__name__}: {text}' if text else repr(e)


def _not_found(e):
    """True if the error is Webull saying it has no such order (not a network failure)."""
    status = getattr(e, 'http_status', None)
    text = f"{getattr(e, 'error_code', '')} {e}".upper()
    return 'ORDER_NOT_FOUND' in text or 'ORDER NOT FOUND' in text or (str(status) == '404' and 'ORDER' in text)


def _decimal(v):
    """Webull wants plain decimal text: 180.25, never 1.8025e2."""
    from decimal import Decimal
    return format(Decimal(repr(float(v))).normalize(), 'f')


def order_side(spec):
    """BUY, SELL, or SHORT for a sell that opens a position (a short sale; never a sell that closes one)."""
    if spec['side'] == 'sell' and not spec.get('closing') and not spec.get('hedge'):
        return 'SHORT'
    return spec['side'].upper()


def build_order(spec, symbol, coid):
    """Webull order fields for an order spec from core_trader.order_spec()."""
    sess = spec['session']
    order = {
        'combo_type': 'NORMAL', 'client_order_id': coid, 'instrument_type': 'EQUITY', 'market': 'US',
        'symbol': symbol, 'side': order_side(spec), 'quantity': str(int(spec['shares'])),
        'time_in_force': 'DAY', 'entrust_type': 'QTY', 'support_trading_session': WEBULL_SESSION[sess],
    }
    if spec['type'] == 'market':
        if sess != 'regular':
            raise guards.GuardRejected('market orders are only sent 9:30-16:00 ET')
        order['order_type'] = 'MARKET'
    else:
        if not spec.get('limit') or spec['limit'] <= 0:
            raise guards.GuardRejected('a limit order needs a limit price')
        order['order_type'] = 'LIMIT'
        order['limit_price'] = _decimal(spec['limit'])
    return order


class LiveBroker:
    def __init__(self, trade_client, account_id=None):
        self.trade = trade_client
        self._account_id = account_id or os.environ.get('WEBULL_ACCOUNT_ID') or None
        self.working = {}               # client order id -> account, while an order is being followed

    def cancel_working(self):
        """On shutdown: cancel any order still working, so it can't fill unseen after the algo
        stops. Returns the client order ids it tried to cancel."""
        tried = []
        for coid, account in list(self.working.items()):
            try:
                self.trade.order_v3.cancel_order(account_id=account, client_order_id=coid)
            except Exception as e:
                log.error('could not cancel order %s on shutdown: %s', coid, _describe(e))
            tried.append(coid)
        return tried

    def account_id(self):
        if self._account_id:
            return self._account_id
        res = self.trade.account_v2.get_account_list()
        accounts = res.json() if hasattr(res, 'json') else res
        if isinstance(accounts, dict):
            accounts = accounts.get('data') or accounts.get('result') or []
        if not accounts:
            raise RuntimeError('Webull returned no accounts for this API key -- is it approved for trading?')
        if len(accounts) > 1:
            listing = ', '.join(f"{a.get('account_id')} ({a.get('account_class') or a.get('account_type')})" for a in accounts)
            raise RuntimeError(f'This API key has several Webull accounts: {listing}. Put the one to trade in '
                               f'settings.env as WEBULL_ACCOUNT_ID=... and restart.')
        self._account_id = str(accounts[0]['account_id'])
        log.info('using Webull account %s', self._account_id)
        return self._account_id

    def verify_account(self):
        """With WEBULL_ACCOUNT_ID set: it must be one of this API key's accounts (as IBKR_ACCOUNT_ID at IBKR; jb-parity)."""
        want = (os.environ.get('WEBULL_ACCOUNT_ID') or '').strip()
        if not want:
            return
        res = self.trade.account_v2.get_account_list()
        rows = res.json() if hasattr(res, 'json') else res
        if isinstance(rows, dict):
            rows = rows.get('data') or rows.get('result') or []
        ids = [str(r.get('account_id')) for r in rows or [] if isinstance(r, dict) and r.get('account_id')]
        if ids and want not in ids:
            raise RuntimeError(f'WEBULL_ACCOUNT_ID={want} is not one of this API key\'s Webull accounts ({", ".join(ids)})')

    def account_row(self):
        """This account's row in Webull's account list ({} when Webull does not list it)."""
        res = self.trade.account_v2.get_account_list()
        rows = res.json() if hasattr(res, 'json') else res
        if isinstance(rows, dict):
            rows = rows.get('data') or rows.get('result') or []
        want = str(self.account_id())
        return next((r for r in rows or [] if isinstance(r, dict) and str(r.get('account_id')) == want), {})

    def account_kind(self):
        """The account type words Webull gives (e.g. 'MARGIN', 'CASH'), upper case; '' if none."""
        row = self.account_row()
        return ' '.join(str(v) for k, v in row.items() if 'type' in str(k).lower() or 'class' in str(k).lower()).upper()

    def preview_short(self, symbol, price=None):
        """Asks Webull to preview a 1-share short sale (limit, regular hours, DAY) -- never placed.
        (True, '') accepted; (False, why) Webull said no; (None, why) the preview could not be made."""
        fn = getattr(self.trade.order_v3, 'preview_order', None)
        if fn is None:
            return None, 'this Webull SDK has no order preview'
        if not price:
            return None, 'no price to preview with'
        spec = {'side': 'sell', 'shares': 1, 'type': 'limit', 'limit': round(float(price) * 1.05, 2), 'session': 'regular'}
        order = build_order(spec, symbol, uuid.uuid4().hex[:32])
        try:
            fn(account_id=self.account_id(), new_orders=[order])
            return True, ''
        except Exception as e:
            status = getattr(e, 'http_status', None)
            try:
                status = int(status)
            except (TypeError, ValueError):
                status = None
            if status is not None and 400 <= status < 500 and status != 429:
                return False, _describe(e)
            return None, _describe(e)

    def position(self, symbol):
        """Shares of `symbol` the account really holds (signed). Raises if Webull's answer
        can't be read -- live trading must not start on a guess."""
        res = self.trade.account_v2.get_account_position(self.account_id())
        data = res.json() if hasattr(res, 'json') else res
        rows = _rows(data)
        if not isinstance(rows, list):
            raise RuntimeError(f'unexpected positions answer from Webull: {str(data)[:200]}')
        total = 0.0
        for r in rows:
            if isinstance(r, dict) and str(r.get('symbol') or r.get('ticker') or '').upper() == symbol.upper():
                qty = r.get('quantity', r.get('qty', r.get('position')))
                if qty is None:
                    raise RuntimeError(f'Webull position for {symbol} has no quantity: {str(r)[:200]}')
                q = _num(qty)
                side = str(r.get('side') or r.get('position_side') or '').upper()
                total += -abs(q) if side in ('SHORT', 'SELL') else q
        return total

    def open_orders(self):
        """Every order still open at Webull on this account (start-up checks, backup stop). Webull lists them a page at
        a time (10 unless asked for more): a full page means there may be more, so it raises rather than answer from
        part of the list."""
        try:
            page = 100
            res = self.trade.order_v3.get_order_open(account_id=self.account_id(), page_size=page)
        except TypeError:
            page = 10
            res = self.trade.order_v3.get_order_open(account_id=self.account_id())
        data = res.json() if hasattr(res, 'json') else res
        rows = _rows(data)
        if len(rows) >= page:
            raise RuntimeError(f'Webull lists {len(rows)}+ open orders on this account; could not read them all')
        found = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            legs = r.get('orders') if isinstance(r.get('orders'), list) else [r]
            for o in legs:
                if isinstance(o, dict):
                    if not o.get('client_order_id') and r.get('client_order_id'):
                        o = dict(o, client_order_id=r.get('client_order_id'))
                    found.append(o)
        return found

    def open_orders_for(self, symbol):
        """Orders for `symbol` still open at Webull (raises when the list can't be read in full)."""
        return [o for o in self.open_orders() if str(o.get('symbol') or '').upper() == symbol.upper()]

    def order_state(self, coid):
        """(status, filled_qty, avg_price) of an order by its client order id, or None if Webull doesn't know it."""
        return self._order_state(coid)

    def _order_state(self, coid):
        """(status, filled_qty, avg_price) of our order, or None if Webull doesn't know it."""
        res = self.trade.order_v3.get_order_detail(account_id=self.account_id(), client_order_id=coid)
        data = res.json() if hasattr(res, 'json') else res
        rows = [r for r in _rows(data) if isinstance(r, dict)]
        if not rows:
            return None
        row = rows[0]
        status = str(row.get('status') or (data.get('status') if isinstance(data, dict) else '') or '')
        status = status.upper().replace(' ', '_')
        filled = _num(row.get('filled_quantity') or row.get('total_filled_qty') or row.get('filled_qty'))
        avg = _num(row.get('filled_price') or row.get('avg_filled_price') or row.get('average_price')) or None
        return status, filled, avg

    def place_order(self, strategy, spec, symbol, on_filled, on_failed, on_unconfirmed, on_unfilled, lock=None,
                    max_order_quantity=10_000, max_order_notional_usd=None, cancel_s=30, poll_s=2):
        side, shares = spec['side'], int(spec['shares'])
        price_for_guard_check = max(spec['ref_price'], spec.get('limit') or 0.0)
        guards.check_live_trading_enabled(strategy)
        guards.check_order(strategy, side, symbol, shares, price_for_guard_check,
                           max_order_quantity=max_order_quantity, max_order_notional_usd=max_order_notional_usd,
                           closing=spec.get('closing', False))
        coid = str(spec.get('client_order_id') or '') or uuid.uuid4().hex[:32]   # Auto-Hedge: its recorded jbhedge... id
        order = build_order(spec, symbol, coid)
        if order['side'] == 'SHORT' and getattr(strategy, 'entry_side', 'buy') != 'sell':
            raise guards.GuardRejected('internal: a short sale from a strategy that buys')
        answer_s = int(spec.get('wait_s') or 60)
        how = 'market' if spec['type'] == 'market' else f"limit {spec['limit']:g}"
        log.warning('PLACING LIVE ORDER: %s', order)
        account = self.account_id()
        sent_ok = True
        try:
            res = self.trade.order_v3.place_order(account_id=account, new_orders=[order])
            data = res.json() if hasattr(res, 'json') else res
            log.warning('LIVE ORDER ACCEPTED: %s', data)
        except Exception as e:
            status = getattr(e, 'http_status', None)
            try:
                status = int(status)
            except (TypeError, ValueError):
                status = None
            if status is not None and 400 <= status < 500:
                raise                                   # Webull said no: core reports "rejected"
            sent_ok = False                             # unclear: did Webull get it? look it up
            log.error('sending the order failed (%s) -- checking whether Webull received it', _describe(e))

        def call(fn, *a):
            if lock is None:
                fn(*a)
            else:
                with lock:
                    fn(*a)

        self.working[coid] = account

        def follow():
            try:
                _follow()
            finally:
                self.working.pop(coid, None)

        def _follow():
            start, cancel_at, not_found = time.time(), None, 0
            while True:
                time.sleep(poll_s)
                try:
                    st = self._order_state(coid)
                    unknown = st is None                # Webull answered: no such order
                except Exception as e:
                    st = None
                    unknown = _not_found(e)
                    if not unknown:                     # no answer at all: proves nothing
                        log.warning('order lookup failed: %s', _describe(e))
                if st is None:
                    not_found = not_found + 1 if unknown else 0
                    if not sent_ok and not_found >= 5:
                        # Webull has said "no such order" for 10 s: cancel anyway (harmless if it
                        # really never arrived), look once more, and only then call it not sent
                        try:
                            self.trade.order_v3.cancel_order(account_id=account, client_order_id=coid)
                        except Exception:
                            pass
                        time.sleep(poll_s * 2)
                        try:
                            again = self._order_state(coid)
                        except Exception as e:
                            again = None if _not_found(e) else 'error'
                        if again is None:
                            call(on_failed, 'the order did not reach Webull (Webull does not know it)')
                            return
                        not_found = 0                   # it exists after all: keep following it
                else:
                    status, filled, avg = st
                    if status == 'FILLED':
                        log.warning('LIVE ORDER FILLED BY WEBULL: %s %s %s @ %s', side.upper(),
                                    int(round(filled or shares)), symbol, avg)
                        call(on_filled, avg or price_for_guard_check, int(round(filled or shares)), 'filled by Webull')
                        return
                    if status in FINAL_BAD:
                        if filled > 0:
                            log.warning('LIVE ORDER %s at Webull after a partial fill of %s', status, filled)
                            call(on_filled, avg or price_for_guard_check, int(round(filled)), 'partly filled by Webull')
                        elif status in NOT_FILLED:
                            log.warning('LIVE ORDER NOT FILLED (%s, %s)', status.lower(), how)
                            call(on_unfilled, f'not filled within {answer_s}s ({how}) -- cancelled'
                                 if cancel_at is not None else f'Webull: order {status.lower()} before it filled ({how})')
                        else:
                            log.error('LIVE ORDER %s at Webull', status)
                            call(on_failed, f'Webull: order {status.lower()}')
                        return
                now = time.time()
                if cancel_at is None and now - start > answer_s:
                    cancel_at = now
                    log.warning('no fill after %ss -- cancelling the order', answer_s)
                    try:
                        self.trade.order_v3.cancel_order(account_id=account, client_order_id=coid)
                    except Exception as e:
                        log.error('cancel failed: %s', _describe(e))
                if cancel_at is not None and now - cancel_at > cancel_s:
                    call(on_unconfirmed, f'Webull gave no final answer for the {side} of {shares} {symbol} '
                                         f'(client order id {coid})')
                    return

        threading.Thread(target=follow, name='webull-order', daemon=True).start()
        return {'client_order_id': coid}
