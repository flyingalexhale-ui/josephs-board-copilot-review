"""SELL ALL buttons (jb-kill): one red button for IBKR and one for Webull.

  GET  /api/kill/<ibkr|webull>          what the button would do right now: the account (and whether
                                        it is a paper or a LIVE account), each position, open orders,
                                        the session, and the last run (read-only, sends nothing)
  POST /api/kill/<ibkr|webull>          {"confirm": "SELL ALL"} starts it

What one press does, in this order:
  1. pauses that broker's algo (the same KILL file as its Stop button; Resume on its tab undoes it)
  2. cancels every open order on that broker account, including ones placed in the broker's app
  3. closes every stock position (sells longs, buys back shorts); IBKR futures too
       regular hours (9:30-4)       a market order
       pre-market / after-hours     a limit order 1% through the last price (IBKR: outside RTH;
       overnight (8 pm - 3:50 am)   Webull: extended / NIGHT session; IBKR: the OVERNIGHT venue)
     Brokers refuse market orders outside regular hours, so a "marketable" limit is used instead:
     it fills at the best price available, the 1% is only the worst it will accept. If it is not
     filled in 20 seconds it is cancelled and sent again 2% through, then 3% (three tries).
     Before every try the position is read again, so nothing is sold twice.
  4. reports each line on the page, keeps it in data/kill_log.json and sends a phone push.
When no session is open (weekends, holidays, 3:50-4 am) steps 1 and 2 still run, and the page says
nothing could be sold until pre-market opens.
Options and anything else that isn't a stock (or an IBKR future) is listed but left alone.
"""
import asyncio
import json
import math
import os
import sys
import threading
import time
import uuid
from datetime import datetime, time as dtime, timedelta, timezone

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
LOG = os.path.join(DATA, 'kill_log.json')
CONFIRM = 'SELL ALL'
STEPS = (1.0, 2.0, 3.0)          # % through the last price on each try (outside regular hours)
WAIT_S = 20                      # how long each try may take before it is cancelled
IB_CLIENT_ID = 68                # its own IB Gateway connection (journal uses 67, bridge/algos others)
IB_PREVIEW_ID = 70               # the preview on another device's open window uses its own, so it can't block a run

_lock = threading.Lock()
_runs = {}                       # broker -> {'started', 'done', 'lines': [...], 'result'}


# ------------------------------------------------------------------ helpers
def _et_now():
    return watch.et(time.time() * 1000)


def session_now(d=None):
    """'regular', 'premarket', 'afterhours', 'overnight' or None (closed), US/Eastern.
    Same rules as the algos (algo/runstate.session_at): holidays, early closes, overnight
    20:00-3:50 only the night before a trading day."""
    sys.path.insert(0, os.path.join(ROOT, 'lib')) if os.path.join(ROOT, 'lib') not in sys.path else None
    import nyse_hours as nh
    d = d or _et_now()
    day, t = d.date(), d.time()
    if t >= dtime(20, 0):
        return 'overnight' if nh.trading_day(day + timedelta(days=1)) else None
    if t < dtime(3, 50):
        return 'overnight' if nh.trading_day(day) else None
    if not nh.trading_day(day) or t < dtime(4, 0):
        return None
    if t < dtime(9, 30):
        return 'premarket'
    early = day in nh.nyse_early_closes(day.year)
    if t < (dtime(13, 0) if early else dtime(16, 0)):
        return 'regular'
    if t < (dtime(17, 0) if early else dtime(20, 0)):
        return 'afterhours'
    return None


SESSION_NAME = {'regular': 'regular hours', 'premarket': 'pre-market', 'afterhours': 'after-hours',
                'overnight': 'overnight', None: 'markets closed'}


def _board_price(sym):
    try:
        with watch._lock:
            h = watch._px.get(sym)
            if h and time.time() * 1000 - h[-1][0] < 15 * 60 * 1000:
                return float(h[-1][1])
    except Exception:
        pass
    return None


def limit_price(side, last, pct):
    """A limit `pct`% through `last`: below it to sell, above it to buy. Whole cents from $1 up
    (rounded away from the market's side so it stays marketable), 4 decimals below $1."""
    raw = last * (1 - pct / 100) if side == 'SELL' else last * (1 + pct / 100)
    q = 100 if last >= 1 else 10000
    v = math.floor(raw * q) / q if side == 'SELL' else math.ceil(raw * q) / q
    return round(max(v, 1.0 / q), 4)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _say(run, text):
    line = {'t': int(time.time() * 1000), 'text': text}
    with _lock:
        run['lines'].append(line)
    print(f'kill: {text}', flush=True)


def _keep_log(broker, run):
    try:
        with open(LOG, encoding='utf-8') as f:
            old = json.load(f)
    except Exception:
        old = []
    old.append({'broker': broker, 'started': run['started'], 'ended': run.get('ended'),
                'result': run.get('result'), 'lines': run['lines']})
    os.makedirs(DATA, exist_ok=True)
    tmp = LOG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(old[-50:], f, indent=1)
    os.replace(tmp, LOG)


def _result(done, total, failed, skipped):
    return (f'closed {done} of {total} position{"s" if total != 1 else ""}' +
            (f'; NOT closed: {", ".join(failed)}' if failed else '') +
            (f'; {skipped} left alone (not stocks)' if skipped else ''))


def _kill_write(broker, by):
    """Write the algo's KILL file in ONE step (temp file, then rename), so the algo never reads a half-written
    file or a pause without SELL ALL's mark. by='sellall': a live algo leaves the selling to SELL ALL;
    by=None: an ordinary pause (the algo closes its own position, as after the Stop button)."""
    import algoctl
    path = algoctl._kill_path(broker)
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not d.get('at'):
        d['at'] = datetime.now(timezone.utc).isoformat()
    if by:
        d['by'] = by
    else:
        d.pop('by', None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f)
    os.replace(tmp, path)


def _pause_algo(broker, run):
    try:
        import algoctl
        was = os.path.exists(algoctl._kill_path(broker))
        _kill_write(broker, 'sellall')
        _say(run, f'{broker.upper()} algo was already paused.' if was else
             f'Paused the {broker.upper()} algo (press Resume on its tab to restart it).')
    except Exception as e:
        _say(run, f'Could not pause the {broker.upper()} algo: {e}')


def _unmark_algo(broker):
    """SELL ALL is done: keep the algo paused, but drop the SELL ALL mark so a live algo closes anything
    SELL ALL could not (it checks the broker's real position first)."""
    try:
        import algoctl
        if os.path.exists(algoctl._kill_path(broker)):
            _kill_write(broker, None)
    except Exception as e:
        print(f'kill: could not clear the SELL ALL mark: {e!r}', flush=True)


# ------------------------------------------------------------------ IBKR
def _ibkr_ports():
    p = (os.environ.get('IBKR_GATEWAY_PORT') or '').strip()
    return [int(p)] if p.isdigit() else [4001, 4002, 7496, 7497]


def _ib_kind(account, port):
    if account.upper().startswith('DU') or port in (4002, 7497):
        return 'paper'
    return 'LIVE'


async def _ib_connect(client_id=IB_CLIENT_ID):
    from ib_async import IB
    ib = IB()
    last = None
    for port in _ibkr_ports():
        try:
            await ib.connectAsync('127.0.0.1', port, clientId=client_id, timeout=8)
            return ib, port
        except Exception as e:
            last = e
    raise RuntimeError(f'IB Gateway not reachable ({last!r}). Is it running and logged in?')


def _ib_account(ib):
    want = (os.environ.get('IBKR_ACCOUNT_ID') or '').strip()
    accts = ib.managedAccounts()
    if want:
        return want
    if len(accts) == 1:
        return accts[0]
    if not accts:
        raise RuntimeError('IB Gateway reports no account')
    raise RuntimeError(f'this IBKR login has several accounts ({", ".join(accts)}): put the one to use '
                       f'in settings.env as IBKR_ACCOUNT_ID=...')


def _ib_name(c):
    return c.symbol if c.secType == 'STK' else (c.localSymbol or c.symbol)


async def _ib_positions(ib, account):
    await asyncio.wait_for(ib.reqPositionsAsync(), 15)
    out = []
    for p in ib.positions(account):
        if p.position:
            out.append(p)
    return out


async def _ib_open_orders(ib, account):
    trades = await asyncio.wait_for(ib.reqAllOpenOrdersAsync(), 15)
    return [t for t in trades if not t.isDone() and (not t.order.account or t.order.account == account)]


async def _ib_preview():
    ib, port = await _ib_connect(IB_PREVIEW_ID)
    try:
        account = _ib_account(ib)
        pos = await _ib_positions(ib, account)
        orders = await _ib_open_orders(ib, account)
        return {'account': account, 'kind': _ib_kind(account, port),
                'positions': [{'sym': _ib_name(p.contract), 'type': p.contract.secType, 'qty': float(p.position),
                               'closes': p.contract.secType in ('STK', 'FUT')} for p in pos],
                'orders': [{'sym': _ib_name(t.contract), 'side': t.order.action, 'qty': float(t.order.totalQuantity),
                            'status': t.orderStatus.status} for t in orders]}
    finally:
        ib.disconnect()


async def _ib_price(ib, account, contract):
    if contract.secType == 'STK':
        p = _board_price(contract.symbol)
        if p:
            return p
    try:
        ib.reqMarketDataType(4)          # live where the plan has it, delayed otherwise
        [t] = await asyncio.wait_for(ib.reqTickersAsync(contract), 8)
        for v in (t.last, t.marketPrice(), t.close):
            if v and v == v and v > 0:
                return float(v)
    except Exception:
        pass
    for it in ib.portfolio(account):
        if it.contract.conId == contract.conId and it.marketPrice and it.marketPrice > 0:
            return float(it.marketPrice)
    return None


async def _ib_run(run):
    from ib_async import Contract, LimitOrder, MarketOrder
    ib, port = await _ib_connect()
    errors = []
    ib.errorEvent += lambda *a: errors.append(a)
    try:
        account = _ib_account(ib)
        _say(run, f'Connected to IBKR account {account} ({_ib_kind(account, port)}).')
        orders = await _ib_open_orders(ib, account)
        if orders:
            ib.reqGlobalCancel()
            for _ in range(20):
                await asyncio.sleep(0.5)
                if not await _ib_open_orders(ib, account):
                    break
            left = await _ib_open_orders(ib, account)
            if any(a[1] == 321 for a in errors if len(a) > 1):
                raise RuntimeError('IB Gateway is set to "Read-Only API", so it refuses orders and cancels. '
                                   'In IB Gateway: Configure > Settings > API > Settings, untick Read-Only API.')
            _say(run, f'Cancelled {len(orders) - len(left)} of {len(orders)} open orders.' +
                 (f' Still open: {", ".join(_ib_name(t.contract) for t in left)} -- cancel them in the IBKR app.' if left else ''))
        else:
            _say(run, 'No open orders to cancel.')
        sess = session_now()
        pos = await _ib_positions(ib, account)
        if not pos:
            _say(run, 'No positions to close.')
            run['syms'] = []                 # read, and nothing held: anything held later was bought after the press
            return 'nothing to sell'
        for p in pos:
            if p.contract.secType not in ('STK', 'FUT'):
                _say(run, f'{_ib_name(p.contract)} ({p.contract.secType}) left alone: close it in the IBKR app.')
        todo = [p for p in pos if p.contract.secType in ('STK', 'FUT')]
        run['syms'] = sorted({p.contract.symbol for p in todo})      # the guardian's follow-up re-sells only these
        if sess is None and any(p.contract.secType == 'STK' for p in todo):
            _say(run, 'Markets are closed right now, so no stock could be sold. Press again once pre-market '
                      'opens (4 am ET on a trading day).')
            todo = [p for p in todo if p.contract.secType != 'STK']
        waiting = [_ib_name(p.contract) + ' (markets closed)' for p in pos
                   if sess is None and p.contract.secType == 'STK']
        done, failed = 0, list(waiting)
        for p in todo:
            try:
                ok = await _ib_close_one(ib, account, p, sess, run, errors, Contract, LimitOrder, MarketOrder)
            except Exception as e:     # one position failing must not stop the others
                _say(run, f'{_ib_name(p.contract)}: problem ({str(e)[:120] or type(e).__name__}) -- check it in the IBKR app.')
                ok = False
            if ok:
                done += 1
            else:
                failed.append(_ib_name(p.contract))
        skipped = sum(1 for p in pos if p.contract.secType not in ('STK', 'FUT'))
        return (_result(done, len(todo) + len(waiting), failed, skipped) if todo else
                'nothing could be sold (markets closed)' if waiting else
                'nothing here can be closed by SELL ALL (close it in the IBKR app)')
    finally:
        ib.disconnect()


async def _ib_close_one(ib, account, p, sess, run, errors, Contract, LimitOrder, MarketOrder):
    name = _ib_name(p.contract)
    [c] = await asyncio.wait_for(ib.qualifyContractsAsync(Contract(conId=p.contract.conId)), 10)
    if c is None:
        _say(run, f'{name}: IBKR did not recognise the contract -- sell it in the IBKR app.')
        return False
    is_stk = c.secType == 'STK'
    if is_stk:
        c.exchange = 'OVERNIGHT' if sess == 'overnight' else 'SMART'
    tries = (None,) if (is_stk and sess == 'regular') else STEPS
    start, done_sh = None, 0.0       # what was held before the first try, and what the tries have filled since
    for n, pct in enumerate(tries, 1):
        qty = next((float(x.position) for x in await _ib_positions(ib, account)
                    if x.contract.conId == p.contract.conId), 0.0)
        if not qty:
            _say(run, f'{name}: closed.')
            return True
        start = qty if start is None else start
        side = 'SELL' if qty > 0 else 'BUY'
        # never more than is left by our own count: IBKR's position list can lag a part fill for a moment
        n_sh = min(abs(qty), abs(start) - done_sh)
        if n_sh <= 0:
            _say(run, f'{name}: closed (IBKR\'s position list is still catching up).')
            return True
        if pct is None:
            order = MarketOrder(side, n_sh)
            how = 'market order'
        else:
            last = await _ib_price(ib, account, c)
            if not last:
                _say(run, f'{name}: no price available, so no safe limit price -- sell it in the IBKR app.')
                return False
            lim = limit_price(side, last, pct)
            if not is_stk:                      # jb-hedge: a futures limit must sit on the contract's price step
                lim = _fut_tick(c.symbol, side, lim)
            order = LimitOrder(side, n_sh, lim)
            how = f'limit {lim:g} ({pct:g}% through {last:g})'
            if is_stk and sess in ('premarket', 'afterhours'):
                order.outsideRth = True
        order.tif = 'DAY'
        order.account = account
        _say(run, f'{name}: {side.lower()} {n_sh:g}, {how}' + (f', try {n} of {len(tries)}' if len(tries) > 1 else ''))
        errors.clear()
        tr = ib.placeOrder(c, order)
        for _ in range(WAIT_S * 2):
            await asyncio.sleep(0.5)
            if tr.isDone():
                break
        if any(len(a) > 1 and a[1] == 321 for a in errors):
            _say(run, 'IB Gateway is set to "Read-Only API" and refused the order. In IB Gateway: Configure > '
                      'Settings > API > Settings, untick Read-Only API.')
            return False
        if tr.orderStatus.status == 'Filled':
            _say(run, f'{name}: filled {tr.orderStatus.filled:g} @ {tr.orderStatus.avgFillPrice:g}.')
            return True
        if not tr.isDone():
            ib.cancelOrder(order)
            for _ in range(20):
                await asyncio.sleep(0.5)
                if tr.isDone():
                    break
            if not tr.isDone():        # never send the next try while this one might still fill (could oversell)
                _say(run, f'{name}: IBKR has not confirmed the cancel, so no new order was sent -- check it in the IBKR app.')
                return False
        done_sh += float(tr.orderStatus.filled or 0)
        why = next((e.message for e in reversed(tr.log) if e.message), tr.orderStatus.status)
        _say(run, f'{name}: not filled ({tr.orderStatus.filled:g} filled, {why}).')
    qty = next((float(x.position) for x in await _ib_positions(ib, account)
                if x.contract.conId == p.contract.conId), 0.0)
    if not qty:
        _say(run, f'{name}: closed.')
        return True
    _say(run, f'{name}: still {qty:g} left after {len(tries)} tries -- sell it in the IBKR app.')
    return False


# price step of the futures SELL ALL may close (jb-hedge: an Auto-Hedge MNQ / MES is closed by SELL ALL too; IBKR refuses
# a limit that is not on the step, e.g. 24750.37 for MNQ)
FUT_TICKS = {'MNQ': 0.25, 'NQ': 0.25, 'MES': 0.25, 'ES': 0.25, 'M2K': 0.1, 'RTY': 0.1, 'MYM': 1.0, 'YM': 1.0,
             'CL': 0.01, 'MCL': 0.01, 'GC': 0.1, 'MGC': 0.1}


def _fut_tick(sym, side, lim):
    tick = FUT_TICKS.get(str(sym).upper())
    if not tick:
        return lim
    q = math.floor if side == 'SELL' else math.ceil       # away from the market's side, so it stays marketable
    return round(q(round(lim / tick, 6)) * tick, 4)


def _in_loop(coro):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ------------------------------------------------------------------ Webull
WB_SESSION = {'regular': 'CORE', 'premarket': 'ALL', 'afterhours': 'ALL', 'overnight': 'NIGHT'}


def _wb_json(res):
    return res.json() if hasattr(res, 'json') else res


def _wb_rows(data):
    if isinstance(data, dict):
        for k in ('orders', 'positions', 'holdings', 'items', 'data', 'result'):
            if isinstance(data.get(k), list):
                return data[k]
        return [data] if data.get('symbol') or data.get('ticker') else []
    return data if isinstance(data, list) else []


WB_GAP_S = 1.2                      # Webull refuses calls that come too close together ("too many requests")
WB_RETRY_S = (2, 3, 5, 8, 10)       # on "too many requests": wait this long and ask again
_wb_lock = threading.Lock()
_wb_last = [0.0]


def _wb_busy(e):
    s = ' '.join(str(x) for x in (e, getattr(e, 'error_code', ''), getattr(e, 'http_status', ''))).upper()
    return '429' in s or 'TOO_MANY_REQUESTS' in s or 'TOO MANY REQUESTS' in s


def _wb_call(fn, *a, **k):
    """Every Webull call goes through here: a short gap between calls, and on Webull's
    "too many requests" it waits and asks again (an order refused that way was never placed,
    so asking again cannot sell twice)."""
    for wait in WB_RETRY_S + (None,):
        with _wb_lock:
            gap = _wb_last[0] + WB_GAP_S - time.time()
            if gap > 0:
                time.sleep(gap)
            try:
                return fn(*a, **k)
            except Exception as e:
                if wait is None or not _wb_busy(e):
                    raise
            finally:
                _wb_last[0] = time.time()
        time.sleep(wait)


class _WbPaced:
    """Wraps the Webull trade client so tc.account_v2.x(...) / tc.order_v3.x(...) use _wb_call."""
    def __init__(self, obj, top=True):
        self._o, self._top = obj, top

    def __getattr__(self, name):
        a = getattr(self._o, name)
        if self._top:
            return _WbPaced(a, False)
        return (lambda *x, **k: _wb_call(a, *x, **k)) if callable(a) else a


def _wb_client():
    sys.path.insert(0, os.path.join(ROOT, 'lib')) if os.path.join(ROOT, 'lib') not in sys.path else None
    import jb_webull
    key, secret = os.environ.get('WEBULL_APP_KEY'), os.environ.get('WEBULL_APP_SECRET')
    if not key or not secret:
        raise RuntimeError('no Webull keys in settings.env')
    _, _, tc = jb_webull.make_clients(key, secret, os.environ.get('WEBULL_REGION_ID') or 'us', trade=True)
    return _WbPaced(tc)


def _wb_account(tc):
    want = (os.environ.get('WEBULL_ACCOUNT_ID') or '').strip()
    accts = _wb_json(tc.account_v2.get_account_list())
    if isinstance(accts, dict):
        accts = accts.get('data') or accts.get('result') or []
    if want:
        row = next((a for a in accts if str(a.get('account_id')) == want), {})
        return want, row
    if not accts:
        raise RuntimeError('Webull returned no accounts for these keys (are they approved for trading?)')
    if len(accts) > 1:
        raise RuntimeError('these Webull keys have several accounts: put the one to use in settings.env as '
                           'WEBULL_ACCOUNT_ID=...')
    return str(accts[0]['account_id']), accts[0]


def _wb_kind(row):
    text = ' '.join(str(v) for k, v in (row or {}).items() if 'type' in k or 'class' in k).upper()
    return 'paper' if 'PAPER' in text or 'SIMUL' in text else 'LIVE'


def _wb_positions(tc, account):
    rows = _wb_rows(_wb_json(tc.account_v2.get_account_position(account)))
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = str(r.get('symbol') or r.get('ticker') or '').upper()
        qty = _num(r.get('quantity', r.get('qty', r.get('position'))))
        side = str(r.get('side') or r.get('position_side') or '').upper()
        if side in ('SHORT', 'SELL'):
            qty = -abs(qty)
        kind = str(r.get('instrument_type') or r.get('asset_type') or 'EQUITY').upper()
        price = next((_num(r.get(k)) for k in ('last_price', 'market_price', 'price', 'close_price')
                      if _num(r.get(k)) > 0), 0.0)
        if sym and qty:
            out.append({'sym': sym, 'qty': qty, 'type': kind, 'price': price or None,
                        'closes': kind in ('EQUITY', 'STOCK', 'STK', 'ETF')})
    return out


def _wb_open_orders(tc, account):
    out = []
    try:
        data = tc.order_v3.get_order_open(account_id=account, page_size=100)
    except TypeError:
        data = tc.order_v3.get_order_open(account_id=account)
    for r in _wb_rows(_wb_json(data)):
        if not isinstance(r, dict):
            continue
        for o in (r.get('orders') if isinstance(r.get('orders'), list) else [r]):
            if isinstance(o, dict):
                out.append({'sym': str(o.get('symbol') or '').upper(), 'side': str(o.get('side') or ''),
                            'qty': _num(o.get('quantity') or o.get('total_quantity')),
                            'status': str(o.get('status') or o.get('order_status') or ''),
                            'coid': o.get('client_order_id') or r.get('client_order_id')})
    return out


def _wb_preview():
    tc = _wb_client()
    account, row = _wb_account(tc)
    return {'account': account, 'kind': _wb_kind(row), 'positions': _wb_positions(tc, account),
            'orders': [{k: v for k, v in o.items() if k != 'coid'} for o in _wb_open_orders(tc, account)]}


def _wb_state(tc, account, coid):
    data = _wb_json(tc.order_v3.get_order_detail(account_id=account, client_order_id=coid))
    rows = [r for r in _wb_rows(data) if isinstance(r, dict)]
    if not rows:
        return '', 0.0, None
    r = rows[0]
    st = str(r.get('status') or r.get('order_status') or '').upper().replace(' ', '_')
    return (st, _num(r.get('filled_quantity') or r.get('total_filled_qty') or r.get('filled_qty')),
            _num(r.get('filled_price') or r.get('avg_filled_price') or r.get('average_price')) or None)


def _wb_decimal(v):
    from decimal import Decimal
    return format(Decimal(repr(float(v))).normalize(), 'f')


def _wb_run(run):
    tc = _wb_client()
    account, row = _wb_account(tc)
    _say(run, f'Connected to Webull account {account} ({_wb_kind(row)}).')
    orders = _wb_open_orders(tc, account)
    if orders:
        failed, tried, left = [], set(), orders
        for _ in range(5):                  # Webull lists open orders a page at a time: repeat until none are left
            new = [o for o in left if o['coid'] and o['coid'] not in tried]
            if not new:
                break
            for o in new:
                tried.add(o['coid'])
                try:
                    tc.order_v3.cancel_order(account_id=account, client_order_id=o['coid'])
                except Exception as e:
                    failed.append(f"{o['sym']} ({e})"[:80])
            time.sleep(3)
            left = _wb_open_orders(tc, account)
        total = len({o['coid'] for o in orders} | tried)
        _say(run, f'Cancelled {max(0, total - len(left))} of {total} open orders.' +
             (f' Still open: {", ".join(o["sym"] for o in left)} -- cancel them in the Webull app.' if left else ''))
    else:
        _say(run, 'No open orders to cancel.')
    sess = session_now()
    pos = _wb_positions(tc, account)
    if not pos:
        _say(run, 'No positions to close.')
        run['syms'] = []                     # read, and nothing held: anything held later was bought after the press
        return 'nothing to sell'
    for p in pos:
        if not p['closes']:
            _say(run, f"{p['sym']} ({p['type'].lower()}) left alone: close it in the Webull app.")
    todo = [p for p in pos if p['closes']]
    run['syms'] = sorted({p['sym'] for p in todo})                    # the guardian's follow-up re-sells only these
    if sess is None:
        _say(run, 'Markets are closed right now, so nothing could be sold. Press again once pre-market '
                  'opens (4 am ET on a trading day).')
        return 'nothing could be sold (markets closed)'
    done, failed = 0, []
    for p in todo:
        try:
            ok = _wb_close_one(tc, account, p, sess, run)
        except Exception as e:         # one position failing must not stop the others
            _say(run, f"{p['sym']}: problem ({str(e)[:120] or type(e).__name__}) -- check it in the Webull app.")
            ok = False
        if ok:
            done += 1
        else:
            failed.append(p['sym'])
    return _result(done, len(todo), failed, len(pos) - len(todo))


def _wb_qty(tc, account, sym):
    return next((p['qty'] for p in _wb_positions(tc, account) if p['sym'] == sym), 0.0)


def _wb_settle(tc, account, sym, before):
    """After a fill, wait (up to ~10 s) for Webull's position list to catch up, so the next try doesn't
    re-read the old share count and send a second sell for shares already sold."""
    for _ in range(5):
        time.sleep(2)
        try:
            if _wb_qty(tc, account, sym) != before:
                return True
        except Exception:
            pass
    return False


def _wb_close_one(tc, account, p, sess, run):
    sym = p['sym']
    tries = (None,) if sess == 'regular' else STEPS
    for n, pct in enumerate(tries, 1):
        qty = _wb_qty(tc, account, sym)
        whole = math.floor(abs(qty))
        if whole < 1:
            _say(run, f'{sym}: closed.' if not qty else
                 f'{sym}: only {abs(qty):g} (a fraction of a share) left -- sell it in the Webull app.')
            return not qty
        side = 'SELL' if qty > 0 else 'BUY'
        coid = uuid.uuid4().hex[:32]
        order = {'combo_type': 'NORMAL', 'client_order_id': coid, 'instrument_type': 'EQUITY', 'market': 'US',
                 'symbol': sym, 'side': side, 'quantity': str(whole), 'time_in_force': 'DAY',
                 'entrust_type': 'QTY', 'support_trading_session': WB_SESSION[sess]}
        if pct is None:
            order['order_type'] = 'MARKET'
            how = 'market order'
        else:
            last = _board_price(sym) or p.get('price')
            fresh = next((x for x in _wb_positions(tc, account) if x['sym'] == sym), None)
            last = _board_price(sym) or (fresh or {}).get('price') or last
            if not last:
                _say(run, f'{sym}: no price available, so no safe limit price -- sell it in the Webull app.')
                return False
            lim = limit_price(side, last, pct)
            order['order_type'] = 'LIMIT'
            order['limit_price'] = _wb_decimal(lim)
            how = f'limit {lim:g} ({pct:g}% through {last:g})'
        _say(run, f'{sym}: {side.lower()} {whole}, {how}' + (f', try {n} of {len(tries)}' if len(tries) > 1 else ''))
        try:
            tc.order_v3.place_order(account_id=account, new_orders=[order])
        except Exception as e:
            try:
                status = int(getattr(e, 'http_status', None))
            except (TypeError, ValueError):
                status = None
            if status is not None and 400 <= status < 500:
                # Webull said no (an HTTP 4xx answer; "too many requests" was already waited out by _wb_call):
                # this try was never placed, so the next one can go out
                _say(run, f'{sym}: Webull refused the order: {str(e)[:200]}')
                continue
            # a timeout can mean the order DID reach Webull: look it up before trying again (could oversell)
            time.sleep(3)
            try:
                seen = _wb_state(tc, account, coid)[0]
            except Exception as e2:
                # review 2026-10-11 #8: we can't tell whether the order reached Webull, so a next try could sell twice
                _say(run, f'{sym}: Webull answered with an error ({str(e)[:120]}) and the order could not be looked up '
                          f'({str(e2)[:80]}), so nothing more was sent -- check it in the Webull app.')
                return False
            if not seen:
                _say(run, f'{sym}: Webull refused the order: {str(e)[:200]}')
                continue
            _say(run, f'{sym}: Webull answered with an error, but the order is there ({seen.lower()}); watching it.')
        st, filled, avg = '', 0.0, None
        for _ in range(WAIT_S // 2):
            time.sleep(2)
            try:
                st, filled, avg = _wb_state(tc, account, coid)
            except Exception:
                continue
            if st == 'FILLED' or st in ('CANCELLED', 'CANCELED', 'FAILED', 'REJECTED', 'EXPIRED'):
                break
        if st == 'FILLED':
            _say(run, f'{sym}: filled {filled:g}' + (f' @ {avg:g}.' if avg else '.'))
            if whole == abs(qty):
                return True
            _wb_settle(tc, account, sym, qty)
            continue
        try:
            tc.order_v3.cancel_order(account_id=account, client_order_id=coid)
        except Exception:
            pass
        end = ''
        for _ in range(5):             # never send the next try while this one might still fill (could oversell)
            time.sleep(2)
            try:
                end, f2, _ = _wb_state(tc, account, coid)
                filled = f2 or filled
            except Exception:
                continue
            if end in ('FILLED', 'CANCELLED', 'CANCELED', 'FAILED', 'REJECTED', 'EXPIRED'):
                break
        if end not in ('FILLED', 'CANCELLED', 'CANCELED', 'FAILED', 'REJECTED', 'EXPIRED'):
            _say(run, f'{sym}: Webull has not confirmed the cancel, so no new order was sent -- check it in the Webull app.')
            return False
        if end == 'FILLED':
            _say(run, f'{sym}: filled while cancelling.')
            _wb_settle(tc, account, sym, qty)
            continue
        if filled and not _wb_settle(tc, account, sym, qty):
            # review 2026-10-11 #8: part filled, then cancelled: the next try must use the new share count
            _say(run, f'{sym}: part filled ({filled:g}) but Webull has not updated the position yet, so no new order '
                      'was sent -- check it in the Webull app.')
            return False
        _say(run, f'{sym}: not filled ({(st or "no answer").lower()}).')
    qty = _wb_qty(tc, account, sym)
    if not qty:
        _say(run, f'{sym}: closed.')
        return True
    _say(run, f'{sym}: still {qty:g} left after {len(tries)} tries -- sell it in the Webull app.')
    return False


# ------------------------------------------------------------------ the page's calls
def _run_view(broker):
    with _lock:
        r = _runs.get(broker)
        return None if not r else {'started': r['started'], 'ended': r.get('ended'), 'running': not r.get('ended'),
                                    'result': r.get('result'), 'lines': list(r['lines'])}


def preview(broker):
    if broker not in ('ibkr', 'webull'):
        return 404, json.dumps({'error': 'unknown broker'})
    sess = session_now()
    out = {'broker': broker, 'session': SESSION_NAME[sess],
           'how': ('market orders' if sess == 'regular' else
                   'limit orders 1% through the last price (then 2%, then 3%)' if sess else
                   'nothing can be sold until pre-market (orders still get cancelled)'),
           'run': _run_view(broker)}
    if out['run'] and out['run']['running']:
        return 200, json.dumps(out)      # while it works the page only needs the log: don't ask the broker every 2 s
    try:
        out.update(_in_loop(_ib_preview()) if broker == 'ibkr' else _wb_preview())
    except Exception as e:
        out['error'] = str(e)[:300]
    return 200, json.dumps(out)


def post(broker, data):
    if broker not in ('ibkr', 'webull'):
        return 404, json.dumps({'error': 'unknown broker'})
    if str(data.get('confirm', '')).strip().upper() != CONFIRM:
        return 400, json.dumps({'error': f'type {CONFIRM} to confirm'})
    with _lock:
        r = _runs.get(broker)
        if r and not r.get('ended'):
            return 409, json.dumps({'error': 'already running'})
        run = _runs[broker] = {'started': int(time.time() * 1000), 'lines': []}
    threading.Thread(target=_work, args=(broker, run), name='kill-' + broker, daemon=True).start()
    return 200, json.dumps({'ok': True, 'run': _run_view(broker)})


def _work(broker, run):
    name = broker.upper()
    _say(run, f'SELL ALL {name} pressed.')
    _pause_algo(broker, run)
    try:
        result = _in_loop(_ib_run(run)) if broker == 'ibkr' else _wb_run(run)
    except Exception as e:
        result = f'stopped: {str(e)[:250]}'
        _say(run, f'Stopped: {e}')
    _unmark_algo(broker)
    with _lock:
        run['result'], run['ended'] = result, int(time.time() * 1000)
    _say(run, f'Finished: {result}.')
    try:
        _keep_log(broker, run)
    except Exception as e:
        print(f'kill: could not save the log: {e!r}', flush=True)
    watch.push_later(f'SELL ALL {name}: {result}'[:120],
                     '\n'.join(l['text'] for l in run['lines'][-12:]), 'rotating_light')
