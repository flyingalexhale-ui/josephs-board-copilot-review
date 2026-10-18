"""Phone order ticket (jb-ticket), running inside the gateway: buy or sell one stock or ETF on IBKR
or Webull from the Night desk or the phone page. LIMIT ORDERS ONLY, every session.

  POST /api/plus/ticket/check   {broker, sym, side, qty, limit}  -> what would be sent, with checks
                                (account, paper or LIVE, session and route, cost, warnings) and a
                                one-time code; nothing is sent
  POST /api/plus/ticket/send    {code}                           -> sends exactly what was checked
                                (the code works once, for 2 minutes)
  GET  /api/plus/ticket         recent tickets and their status
  GET  /api/plus/ticket/quote?sym=  bid / ask / last for the form
  POST /api/plus/ticket/cancel  {id}                             -> cancels a ticket's order if open

Routes, like the SELL ALL buttons (gateway/killswitch.py, whose connection code is reused):
  regular hours        IBKR SMART / Webull CORE
  pre-market, after    IBKR SMART + outside regular hours / Webull ALL
  overnight            IBKR OVERNIGHT venue / Webull NIGHT
Final-ideas additions (jb-orders): how long the order stays ("today", IBKR "overnight and into the
next day" or "until cancelled"), IBKR's own preview (estimated commission and what your cash /
buying power would be after), ladder orders (one order split into 2-5 limits a few cents apart), a
warning when the same order was just sent, and place() for the board's own automatic orders
(overnight safety stops, scheduled orders, the flip button: gateway/orders.py).
Safety: limit orders only; whole shares; a limit more than 5% away from the last price is refused
(typing mistakes); one order at most JB_TICKET_MAX dollars (settings.env, default 5000); selling
more than you hold is refused (the board never opens a short); nothing is sent without the
second tap. IBKR orders use their own connection (client id 69) so they can be cancelled later.
Files: data/ticket_log.json.
"""
import asyncio
import json
import math
import os
import threading
import time
import uuid

import watch
import killswitch as ks

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
LOG = os.path.join(DATA, 'ticket_log.json')
IB_CLIENT_ID = 69
MAX_AWAY_PCT = 5.0
_lock = threading.Lock()
_pending = {}                      # code -> {'at', 'order'}
_busy = threading.Lock()           # one broker conversation at a time


def _now():
    return int(time.time() * 1000)


def max_dollars():
    try:
        return float(os.environ.get('JB_TICKET_MAX', '5000') or 5000)
    except ValueError:
        return 5000.0


def _log_all():
    v = watch._read(LOG, [])
    return v if isinstance(v, list) else []


def _log_put(t):
    with _lock:
        lst = [x for x in _log_all() if x.get('id') != t['id']]
        lst.insert(0, t)
        watch._write(LOG, lst[:60])


def _clean_sym(s):
    return ''.join(ch for ch in str(s or '').upper() if ch.isalnum() or ch in '.')[:10]


# ------------------------------------------------------------------ prices for the form
def _yahoo_last(sym):
    """Last trade (pre/after-hours included) from Yahoo, when the boards have no price for it."""
    try:
        import world, urllib.parse
        body = world._get('https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(sym.replace('.', '-')) + '?range=1d&interval=1m&includePrePost=true', timeout=8)
        res = ((body.get('chart') or {}).get('result') or [None])[0] or {}
        closes = [c for c in ((((res.get('indicators') or {}).get('quote')) or [{}])[0].get('close') or []) if c]
        last = closes[-1] if closes else (res.get('meta') or {}).get('regularMarketPrice')
        return float(last) if last and math.isfinite(float(last)) and float(last) > 0 else None
    except Exception:
        return None


_yq = {}                           # sym -> (time, price): Yahoo prices for the form, kept 20 s


def quote(sym):
    sym = _clean_sym(sym)
    out = {'sym': sym, 'last': ks._board_price(sym)}
    try:
        import night
        q = night._q.get(sym)
        if q and _now() - q['ts'] < 5 * 60000:
            out.update(bid=q['bid'], ask=q['ask'])
    except Exception:
        pass
    if out['last'] is None and out.get('bid') is None and sym:     # any stock or ETF not on a board
        hit = _yq.get(sym)
        if not hit or time.time() - hit[0] > 20:
            hit = (time.time(), _yahoo_last(sym))
            _yq[sym] = hit
        if hit[1]:
            out.update(last=round(hit[1], 4), src='yahoo')
    return out


# ------------------------------------------------------------------ IBKR
async def _ib_connect():
    from ib_async import IB
    ib, last = IB(), None
    for port in ks._ibkr_ports():
        try:
            await ib.connectAsync('127.0.0.1', port, clientId=IB_CLIENT_ID, timeout=8)
            return ib, port
        except Exception as e:
            last = e
    raise RuntimeError(f'IB Gateway not reachable ({last!r}). Is it running and logged in?')


async def _ib_check(o, preview=False):
    ib, port = await _ib_connect()
    try:
        account = ks._ib_account(ib)
        pos = await ks._ib_positions(ib, account)
        held = sum(float(p.position) for p in pos if p.contract.secType == 'STK' and p.contract.symbol == o['sym'])
        out = {'account': account, 'kind': ks._ib_kind(account, port), 'held': held}
        if preview:
            out['preview'] = await _ib_whatif(ib, account, o)
        return out
    finally:
        ib.disconnect()


def _f(v):
    try:
        v = float(v)
        return v if math.isfinite(v) and abs(v) < 1e15 else None
    except (TypeError, ValueError):
        return None


async def _ib_whatif(ib, account, o):
    """IBKR's own preview of the order (nothing is sent): estimated commission and what the
    account would look like after. Never stops the check if IBKR can't answer."""
    try:
        from ib_async import Stock, LimitOrder
        q = await asyncio.wait_for(ib.qualifyContractsAsync(Stock(o['sym'], 'SMART', 'USD')), 10)
        if not q or q[0] is None:
            return None
        c = q[0]
        if o['sess'] == 'overnight':
            c.exchange = 'OVERNIGHT'
        legs = o.get('legs') or [[o['qty'], o['limit']]]
        qty = sum(int(x[0]) for x in legs)
        order = LimitOrder(o['side'].upper(), qty, legs[0][1])
        order.account = account
        order.tif = 'DAY'
        if o['sess'] in ('premarket', 'afterhours'):
            order.outsideRth = True
        st = await asyncio.wait_for(ib.whatIfOrderAsync(c, order), 12)
        if st is None:
            return None
        comm = _f(st.commission)
        if comm is None:
            lo, hi = _f(st.minCommission), _f(st.maxCommission)
            comm = (lo + hi) / 2 if lo is not None and hi is not None else lo
        return {'commission': round(comm, 2) if comm is not None else None,
                'equity_after': _f(st.equityWithLoanAfter), 'equity_before': _f(st.equityWithLoanBefore),
                'margin_change': _f(st.initMarginChange), 'warning': (st.warningText or '')[:200]}
    except Exception as e:
        return {'error': str(e)[:150]}


async def _ib_send(o):
    from ib_async import Stock, LimitOrder
    ib, port = await _ib_connect()
    errors = []
    ib.errorEvent += lambda *a: errors.append(a)
    try:
        account = ks._ib_account(ib)
        if account != o['account'] or ks._ib_kind(account, port) != o['kind']:
            raise RuntimeError(f"IB Gateway is now logged in to a different account ({account}) than when you checked ({o['account']}). Nothing was sent; check the order again.")
        c = Stock(o['sym'], 'SMART', 'USD')
        q = await asyncio.wait_for(ib.qualifyContractsAsync(c), 10)
        if not q or q[0] is None:
            raise RuntimeError(f"IBKR did not recognise {o['sym']}")
        c = q[0]
        if o['sess'] == 'overnight':
            c.exchange = 'OVERNIGHT'
        order = LimitOrder(o['side'].upper(), o['qty'], o['limit'])
        order.tif = {'gtc': 'GTC', 'ond': 'OND'}.get(o.get('tif'), 'DAY')
        order.account = account
        order.orderRef = 'jb-ticket-' + o['id']
        if o['sess'] in ('premarket', 'afterhours') or order.tif == 'GTC':
            order.outsideRth = True
        tr = ib.placeOrder(c, order)
        for _ in range(16):
            await asyncio.sleep(0.5)
            if tr.isDone() or tr.orderStatus.status in ('Submitted', 'Filled'):
                break
        if any(len(a) > 1 and a[1] == 321 for a in errors):
            raise RuntimeError('IB Gateway is set to "Read-Only API". In IB Gateway: Configure > Settings > API > Settings, untick Read-Only API.')
        why = next((e.message for e in reversed(tr.log) if e.message), '')
        return {'status': tr.orderStatus.status, 'filled': float(tr.orderStatus.filled or 0),
                'avg': float(tr.orderStatus.avgFillPrice or 0) or None, 'note': why[:200], 'order_id': tr.order.orderId}
    finally:
        ib.disconnect()


async def _ib_status(tickets):
    """Refresh the status of open IBKR tickets (open orders from this client id, plus today's fills)."""
    ib, _ = await _ib_connect()
    try:
        trades = await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), 15)
        fills = await asyncio.wait_for(ib.reqExecutionsAsync(), 15)
        by_ref = {t.order.orderRef: t for t in trades}
        out = {}
        for t in tickets:
            ref = 'jb-ticket-' + t['id']
            done = [f for f in fills if getattr(f.execution, 'orderRef', '') == ref]
            filled = sum(float(f.execution.shares) for f in done)
            avg = (sum(float(f.execution.shares) * float(f.execution.price) for f in done) / filled) if filled else None
            if ref in by_ref and not by_ref[ref].isDone():
                out[t['id']] = {'status': by_ref[ref].orderStatus.status, 'filled': filled, 'avg': avg}
            elif filled >= t['qty']:
                out[t['id']] = {'status': 'Filled', 'filled': filled, 'avg': avg}
            elif t.get('status') in OPEN:     # any status not final (PendingCancel, ApiPending, Sending...) that IBKR no longer lists
                out[t['id']] = {'status': 'Ended (see IBKR app)' if not filled else 'Part filled, rest ended', 'filled': filled, 'avg': avg}
        return out
    finally:
        ib.disconnect()


async def _ib_cancel(t):
    ib, _ = await _ib_connect()
    try:
        trades = await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), 15)
        tr = next((x for x in trades if x.order.orderRef == 'jb-ticket-' + t['id'] and not x.isDone()), None)
        if not tr:
            return 'not open any more'
        ib.cancelOrder(tr.order)
        for _ in range(16):
            await asyncio.sleep(0.5)
            if tr.isDone():
                break
        return 'cancelled' if tr.orderStatus.status in ('Cancelled', 'ApiCancelled') else tr.orderStatus.status
    finally:
        ib.disconnect()


# ------------------------------------------------------------------ Webull
def _wb_check(o):
    tc = ks._wb_client()
    account, row = ks._wb_account(tc)
    held = sum(p['qty'] for p in ks._wb_positions(tc, account) if p['sym'] == o['sym'])
    return {'account': account, 'kind': ks._wb_kind(row), 'held': held}


def _wb_send(o):
    tc = ks._wb_client()
    account, row = ks._wb_account(tc)
    if account != o['account'] or ks._wb_kind(row) != o['kind']:
        raise RuntimeError(f"Webull answered with a different account ({account}) than when you checked ({o['account']}). Nothing was sent; check the order again.")
    order = {'combo_type': 'NORMAL', 'client_order_id': o['id'], 'instrument_type': 'EQUITY', 'market': 'US',
             'symbol': o['sym'], 'side': o['side'].upper(), 'quantity': str(int(o['qty'])), 'time_in_force': 'GTC' if o.get('tif') == 'gtc' else 'DAY',
             'entrust_type': 'QTY', 'support_trading_session': ks.WB_SESSION[o['sess']],
             'order_type': 'LIMIT', 'limit_price': ks._wb_decimal(o['limit'])}
    tc.order_v3.place_order(account_id=account, new_orders=[order])
    time.sleep(3)
    try:
        st, filled, avg = ks._wb_state(tc, account, o['id'])
    except Exception:
        st, filled, avg = 'SENT', 0.0, None
    return {'status': (st or 'SENT').title(), 'filled': filled, 'avg': avg, 'note': ''}


def _wb_status(tickets):
    tc = ks._wb_client()
    account, _ = ks._wb_account(tc)
    out = {}
    for t in tickets:
        try:
            st, filled, avg = ks._wb_state(tc, account, t['id'])
            if st:
                out[t['id']] = {'status': st.title(), 'filled': filled, 'avg': avg}
        except Exception as e:
            # a ticket the board was still sending when it restarted, that Webull says it has never seen: it ended
            # (otherwise it would count as an open order for 7 days and hold back that stock's safety stop)
            gone = 'ORDER_NOT_FOUND' in f"{getattr(e, 'error_code', '')} {e}".upper() or str(getattr(e, 'http_status', '')) == '404'
            if gone and t.get('status') == 'Sending' and _now() - t.get('at', 0) > 120000:
                out[t['id']] = {'status': 'Ended (never reached Webull)', 'filled': 0, 'avg': None}
    return out


def _wb_cancel(t):
    tc = ks._wb_client()
    account, _ = ks._wb_account(tc)
    tc.order_v3.cancel_order(account_id=account, client_order_id=t['id'])
    time.sleep(2)
    st, _, _ = ks._wb_state(tc, account, t['id'])
    return (st or 'cancel sent').lower()


# ------------------------------------------------------------------ the two steps
def check(data):
    broker = str(data.get('broker') or '').lower()
    if broker not in ('ibkr', 'webull'):
        return 400, {'error': 'Pick IBKR or Webull.'}
    sym, side = _clean_sym(data.get('sym')), str(data.get('side') or '').lower()
    if not sym or side not in ('buy', 'sell'):
        return 400, {'error': 'Fill in the symbol and buy or sell.'}
    try:
        qty, limit = float(data.get('qty')), float(data.get('limit'))
    except (TypeError, ValueError):
        return 400, {'error': 'Fill in the shares and the limit price.'}
    if not (math.isfinite(qty) and math.isfinite(limit)):
        return 400, {'error': 'Fill in the shares and the limit price as numbers.'}
    if qty < 1 or qty != math.floor(qty):
        return 400, {'error': 'Whole shares only (1 or more).'}
    if limit <= 0:
        return 400, {'error': 'The limit price must be above 0.'}
    limit = round(limit, 2) if limit >= 1 else round(limit, 4)
    if limit <= 0:
        return 400, {'error': 'The limit price must be above 0.'}
    tif = str(data.get('tif') or 'day').lower()
    if tif not in TIFS:
        tif = 'day'
    try:
        n_legs = max(1, min(5, int(data.get('ladder') or 1)))
        step = abs(float(data.get('step') or 0.01))
    except (TypeError, ValueError):
        n_legs, step = 1, 0.01
    sess = ks.session_now()
    if sess is None:
        return 400, {'error': 'Markets are closed right now (weekend, holiday, or 3:50 - 4 am). Orders can be placed from 4 am on a trading day and overnight from 8 pm the night before one.'}
    warn = []
    q = quote(sym)
    ref = q.get('last') or ((q['bid'] + q['ask']) / 2 if q.get('bid') else None)
    band, src = MAX_AWAY_PCT, 'the last price'
    if ref and q.get('src') == 'yahoo':
        band, src = 15.0, "Yahoo's last price"
    if not ref:
        ref = _yahoo_last(sym)                    # not on a board: Yahoo's last trade, with a wider band (it can lag)
        band, src = 15.0, "Yahoo's last price"
        if ref:
            q['last'] = ref
    if ref:
        away = (limit / ref - 1) * 100
        if abs(away) > band:
            return 400, {'error': f'Your limit {limit:g} is {away:+.1f}% from {src} {ref:g}. Over {band:g}% away is refused, to catch typing mistakes.'}
        if (side == 'buy' and away > 1) or (side == 'sell' and away < -1):
            warn.append(f'Your limit is {abs(away):.1f}% {"above" if side == "buy" else "below"} the last price: it will likely fill at once, maybe at a worse price than needed.')
        if band > MAX_AWAY_PCT:
            warn.append(f'{sym} is not on your boards, so the limit was checked against Yahoo ({ref:g}), which can lag. Double-check it.')
    elif side == 'sell':
        return 400, {'error': f'No price found anywhere for {sym}, so a typing mistake in the limit could sell far too cheap. Put {sym} on your IBKR board first, then try again.'}
    else:
        warn.append(f'No recent price for {sym}, so the limit could not be checked against the market. Double-check it.')
    if broker == 'webull' and sess == 'overnight' and not _webull_overnight():
        warn.append('Your Webull plan has no overnight prices, so the board cannot show where this stock trades right now.')
    tif, why = tif_for(broker, sess, tif)
    if why:
        warn.append(why)
    legs = ladder_legs(int(qty), limit, side, n_legs, step)
    if len(legs) > 1:
        lo_, hi_ = min(x[1] for x in legs), max(x[1] for x in legs)
        if ref and (abs(lo_ / ref - 1) * 100 > band or abs(hi_ / ref - 1) * 100 > band):
            return 400, {'error': f'The ladder goes {lo_:g} to {hi_:g}, over {band:g}% from {src} {ref:g}. Use a smaller step.'}
    try:                                          # jb-tools2: cool-down, wash-sale and exposure notes (never block)
        import tradetools
        warn += tradetools.ticket_notes(sym, side, int(qty), limit)
    except Exception:
        pass
    dup = recent_same(broker, sym, side, int(qty), limit)
    if dup:
        warn.append(f'You sent this same order {dup} ago. Check it isn\'t a double tap before sending again.')
    cost = sum(x[0] * x[1] for x in legs)
    # review 2026-10-11 #2: the dollar cap guards against typing mistakes on BUYS. Sells are already limited
    # to what you hold (checked below), and a cap on them stopped safety stops / Flip / scheduled sells
    if side == 'buy' and (not math.isfinite(max_dollars()) or cost > max_dollars()):
        return 400, {'error': f'This order is ${cost:,.0f}, over your limit of ${max_dollars():,.0f} per order (JB_TICKET_MAX in settings.env).'}
    o = {'id': uuid.uuid4().hex[:24], 'broker': broker, 'sym': sym, 'side': side, 'qty': int(qty), 'limit': limit, 'sess': sess,
         'tif': tif, 'source': str(data.get('source') or 'ticket')[:20]}
    if len(legs) > 1:
        o['legs'] = legs
    if not _busy.acquire(timeout=30):
        return 409, {'error': 'Busy with another order, try again in a moment.'}
    try:
        acct = ks._in_loop(_ib_check(o, preview=bool(data.get('preview', True)))) if broker == 'ibkr' else _wb_check(o)
    except Exception as e:
        return 400, {'error': f'Could not reach {broker.upper()}: {str(e)[:200]}'}
    finally:
        _busy.release()
    if side == 'sell' and acct['held'] < qty:
        return 400, {'error': f"You hold {acct['held']:g} {sym} on {broker.upper()}; selling {qty:g} would go short, and the board never opens a short position."}
    route = {'ibkr': {'regular': 'SMART, regular hours', 'premarket': 'SMART, outside regular hours', 'afterhours': 'SMART, outside regular hours', 'overnight': 'IBKR OVERNIGHT venue'},
             'webull': {'regular': 'regular session', 'premarket': 'extended hours', 'afterhours': 'extended hours', 'overnight': 'Webull NIGHT session'}}[broker][sess]
    code = uuid.uuid4().hex
    with _lock:
        for k in [k for k, v in _pending.items() if time.time() - v['at'] > 120]:
            del _pending[k]
        _pending[code] = {'at': time.time(), 'order': dict(o, account=acct['account'], kind=acct['kind'])}
    good = TIF_TEXT[tif]
    how = (f"in {len(legs)} limits from {legs[0][1]:g} to {legs[-1][1]:g} ({', '.join(str(x[0]) for x in legs)} shares)" if len(legs) > 1
           else f"at {limit:g} limit")
    return 200, {'code': code, 'broker': broker, 'account': acct['account'], 'kind': acct['kind'], 'held': acct['held'],
                 'sym': sym, 'side': side, 'qty': int(qty), 'limit': limit, 'cost': round(cost, 2), 'session': ks.SESSION_NAME[sess],
                 'route': route, 'warnings': warn, 'quote': q, 'tif': tif, 'legs': legs, 'preview': acct.get('preview'),
                 'text': f"{side.upper()} {int(qty)} {sym} {how} on {broker.upper()} ({acct['kind']}), about ${cost:,.2f}, {good}, {ks.SESSION_NAME[sess]} ({route})."}


def send(data):
    code = str(data.get('code') or '')
    with _lock:
        p = _pending.pop(code, None)
    if not p or time.time() - p['at'] > 120:
        return 400, {'error': 'That check has run out (2 minutes) or was already used. Check the order again.'}
    o = p['order']
    if ks.session_now() != o['sess']:
        return 400, {'error': 'The session changed since you checked. Check the order again.'}
    if not _busy.acquire(timeout=45):
        with _lock:
            _pending[code] = p                    # nothing was sent: the same check can be sent again
        return 409, {'error': 'Busy talking to the broker, press Send again in a moment.'}
    legs = o.pop('legs', None) or [[o['qty'], o['limit']]]
    group = o['id'] if len(legs) > 1 else None
    done = []
    try:
        for i, (lq, lp) in enumerate(legs):
            leg = dict(o, qty=int(lq), limit=lp)
            if group:
                leg['id'], leg['group'] = f"{o['id'][:20]}-{i + 1}", group
            t = dict(leg, at=_now(), status='Sending', filled=0, avg=None)
            _log_put(t)
            try:
                r = ks._in_loop(_ib_send(leg)) if leg['broker'] == 'ibkr' else _wb_send(leg)
            except Exception as e:
                t.update(status='Refused', note=str(e)[:250])
                _log_put(t)
                done.append(t)
                break                                   # a refused leg stops the rest of a ladder
            t.update(r)
            _log_put(t)
            done.append(t)
            if group and leg['broker'] == 'webull':
                time.sleep(ks.WB_GAP_S)
    finally:
        _busy.release()
    t = done[0]
    if o.get('source', 'ticket') == 'ticket':
        what = (f"{o['side'].upper()} {sum(x['qty'] for x in done)} {o['sym']} in {len(done)} limits" if group
                else f"{o['side'].upper()} {o['qty']} {o['sym']} at {o['limit']:g} limit")
        watch.push_later(f"Order sent: {o['side'].upper()} {o['qty']} {o['sym']}",
                         f"{what} on {o['broker'].upper()} ({o['kind']}): {', '.join(x['status'] for x in done)}.", 'memo')
    return 200, {'ticket': t, 'tickets': done}


class _OpenStatuses:
    """`status in OPEN` is True for any status that is not clearly final (review 2026-10-11 #9): IBKR's
    PendingCancel / ApiPending and Webull statuses the board doesn't know count as still working, so no
    second sell is stacked on an order that may still fill."""
    FINAL = {'filled', 'cancelled', 'canceled', 'apicancelled', 'inactive', 'rejected', 'expired', 'failed',
             'refused', 'ended (see ibkr app)', 'part filled, rest ended', 'partial_cancelled', 'partially_cancelled',
             'ended (never reached webull)'}

    def __contains__(self, st):
        return bool(st) and str(st).strip().lower() not in self.FINAL


OPEN = _OpenStatuses()


_last_refresh = [0.0]


def refresh():
    lst = _log_all()
    open_ = [t for t in lst if t.get('status') in OPEN and _now() - t["at"] < 7 * 24 * 3600000]
    if not open_ or time.time() - _last_refresh[0] < 20:          # ask the brokers at most every 20 s
        return lst
    _last_refresh[0] = time.time()
    if not _busy.acquire(timeout=5):
        return lst
    try:
        for broker in ('ibkr', 'webull'):
            mine = [t for t in open_ if t['broker'] == broker]
            if not mine:
                continue
            try:
                upd = ks._in_loop(_ib_status(mine)) if broker == 'ibkr' else _wb_status(mine)
            except Exception:
                continue
            for t in mine:
                if t['id'] in upd:
                    t.update(upd[t['id']])
                    _log_put(t)
    finally:
        _busy.release()
    return _log_all()


def cancel(data):
    t = next((x for x in _log_all() if x.get('id') == str(data.get('id'))), None)
    if not t:
        return 404, {'error': 'No such ticket.'}
    if not _busy.acquire(timeout=30):
        return 409, {'error': 'Busy, try again in a moment.'}
    try:
        res = ks._in_loop(_ib_cancel(t)) if t['broker'] == 'ibkr' else _wb_cancel(t)
    except Exception as e:
        return 400, {'error': f'Could not cancel: {str(e)[:200]}'}
    finally:
        _busy.release()
    done = res.replace('_', '').replace(' ', '') in ('cancelled', 'canceled', 'apicancelled')
    t['status'] = 'Cancelled' if done else t['status']
    t['note'] = res
    _log_put(t)
    return 200, {'ticket': t, 'result': res}


def positions():
    """What you hold, per broker, from the My trading journal (and the Portfolio tab), for the
    "sell what I hold" button and the cost line on the chart."""
    out = []
    try:
        import trading
        with trading._lock:
            fills = list(trading._journal()['fills'].values())
        _, open_ = trading.round_trips(fills)
        out = [{'broker': o['broker'], 'sym': o['sym'], 'qty': o['qty'], 'price': round(o['price'], 4)} for o in open_
               if o.get('qty') and '=' not in o['sym'] and o['broker'] in ('ibkr', 'webull')]
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ final-ideas helpers (jb-orders)
TIFS = ('day', 'gtc', 'ond')
TIF_TEXT = {'day': 'good for this session', 'gtc': 'good until you cancel it', 'ond': 'good overnight and through the next day'}


def tif_for(broker, sess, tif):
    """The order life that this broker + session allows, and a note if the choice was changed."""
    if tif == 'ond' and not (broker == 'ibkr' and sess == 'overnight'):
        return 'day', '"Overnight and into tomorrow" works only for IBKR orders placed overnight; this one is good for this session.'
    if tif == 'gtc' and sess == 'overnight':
        return ('ond', 'Overnight orders can\'t be "until cancelled"; it is good overnight and through the next day instead.') if broker == 'ibkr' \
            else ('day', 'Webull overnight orders are good for tonight only.')
    return tif, ''


def ladder_legs(qty, limit, side, n, step):
    """Split qty into n limits `step` apart, the first at your limit and the rest further from the
    market (lower for a buy, higher for a sell). [[shares, price], ...]; at least 1 share each."""
    n = max(1, min(int(n), int(qty)))
    if n == 1:
        return [[int(qty), limit]]
    base, extra = divmod(int(qty), n)
    out = []
    for i in range(n):
        p = limit - i * step if side == 'buy' else limit + i * step
        p = round(p, 2) if p >= 1 else round(p, 4)
        if p <= 0:
            break
        out.append([base + (1 if i < extra else 0), p])
    return out


def recent_same(broker, sym, side, qty, limit, within_s=60):
    now = _now()
    for t in _log_all()[:10]:
        if (t.get('broker'), t.get('sym'), t.get('side'), t.get('qty'), t.get('limit')) == (broker, sym, side, qty, limit) and \
                now - t.get('at', 0) < within_s * 1000 and t.get('status') != 'Refused':
            return f'{round((now - t["at"]) / 1000)} s'
    return ''


def place(params, source):
    """The board's own orders (safety stops, scheduled orders, flip): check, then send at once.
    Returns (ok, ticket or error text). Same checks as your own orders (5% band, $ limit, no short)."""
    code, res = check(dict(params, source=source, preview=False))
    if code != 200:
        return False, res.get('error', 'refused')
    code, res = send({'code': res['code']})
    if code != 200:
        return False, res.get('error', 'not sent')
    t = res['ticket']
    if t.get('status') == 'Refused':
        return False, t.get('note') or 'refused by the broker'
    return True, t


def ticket_by_id(tid):
    return next((x for x in _log_all() if x.get('id') == tid), None)


def open_tickets():
    return [t for t in _log_all() if t.get('status') in OPEN and _now() - t['at'] < 7 * 24 * 3600000]


# ------------------------------------------------------------------ doorway
def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return v or ''


def get(sub, query):
    if sub in ('', 'status'):
        return 200, {'tickets': refresh()[:20], 'max': max_dollars(), 'session': ks.SESSION_NAME[ks.session_now()]}
    if sub == 'quote':
        return 200, quote(_q(query, 'sym'))
    if sub == 'positions':
        open_ = [t for t in _log_all() if t.get('status') in OPEN and _now() - t["at"] < 7 * 24 * 3600000]
        return 200, {'positions': positions(), 'open': [{k: t[k] for k in ('sym', 'side', 'qty', 'limit', 'broker')} for t in open_]}
    return 404, {'error': 'unknown'}


def post(sub, data):
    if sub == 'check':
        return check(data)
    if sub == 'send':
        return send(data)
    if sub == 'cancel':
        return cancel(data)
    return 404, {'error': 'unknown'}


def _webull_overnight():
    """Does the Webull plan stream the overnight session right now (lib/jb_webull.py, hedge-2026-10-12)?"""
    try:
        import jb_webull
        return jb_webull.overnight_state().get('on') is True
    except Exception:
        return False
