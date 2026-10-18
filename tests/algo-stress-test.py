"""Stress test for one algo folder (ibkr-algo or webull-algo) with simulated brokers.
usage: python3 stress.py <algo dir> <ibkr|webull>
"""
import asyncio, json, logging, os, sys, tempfile, time, types
ALGO, BROKER = sys.argv[1], sys.argv[2]
sys.path.insert(0, ALGO)
sys.path.insert(0, os.path.join(os.path.dirname(ALGO), 'lib'))
logging.basicConfig(level=logging.CRITICAL)
os.environ['IBKR_LIVE_TRADING_CONFIRM'] = os.environ['WEBULL_LIVE_TRADING_CONFIRM'] = 'I_UNDERSTAND_THE_RISK'
os.environ['JB_NTFY_TOPIC'] = ''
import pandas as pd
from algo import core_trader as ct, guards
from algo.strategy import Strategy

ct.session_at = lambda *a, **k: 'regular'          # pretend it is 10:00 am ET
RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok), detail))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  -- {detail}' if detail else ''))


def strategy(tmp, **risk):
    cfg = {'name': 't', 'symbol': 'TQQQ', 'indicators': [], 'entry': {'side': 'buy', 'when': 'close > 0'},
           'exit': {'when': 'close < 0'}, 'position_size': {'pct_of_equity': 25},
           'risk': dict({'max_order_notional_usd': 25000}, **risk), 'confirm_live': True}
    return Strategy(cfg, source_path=os.path.join(tmp, 't.yaml'))


def core(mode='live', **risk):
    tmp = tempfile.mkdtemp()
    c = ct.TraderCore(strategy(tmp, **risk), mode, logs_dir=tmp, starting_cash=300_000,
                      status_path=os.path.join(tmp, 'live_status.json'), broker_name=BROKER,
                      options_path=os.path.join(tmp, 'opts.json'))
    c.connected = True
    c.last_price = 100.0
    c.tmp = tmp
    return c


def now():
    return pd.Timestamp.now(tz='UTC')


# ======================================================================= IBKR fake
class Ev(list):
    def __iadd__(self, f):
        self.append(f); return self

    def emit(self, *a):
        for f in list(self):
            f(*a)


class FakeTrade:
    def __init__(self, order):
        self.order = order
        self.orderStatus = types.SimpleNamespace(status='PendingSubmit', filled=0.0, avgFillPrice=0.0)
        self.statusEvent = Ev()
        self.log = []

    def isDone(self):
        return self.orderStatus.status in ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')

    def set(self, status, filled=None, avg=None, code=0, msg=''):
        self.orderStatus.status = status
        if filled is not None:
            self.orderStatus.filled = filled
        if avg is not None:
            self.orderStatus.avgFillPrice = avg
        self.log.append(types.SimpleNamespace(errorCode=code, message=msg, status=status))
        self.statusEvent.emit(self)


class FakeIB:
    def __init__(self):
        self.trades, self.pos, self.cancels, self.connected = [], 0.0, 0, True
        self.on_cancel = None
        self.client = types.SimpleNamespace(cancelOrder=lambda *a: None)

    def placeOrder(self, contract, order):
        t = FakeTrade(order); self.trades.append(t); return t

    def cancelOrder(self, order):
        self.cancels += 1
        if self.on_cancel:
            self.on_cancel(self.trades[-1])

    def positions(self, acct=''):
        if not self.connected:
            return []
        out = [types.SimpleNamespace(contract=types.SimpleNamespace(conId=1), position=self.pos)] if self.pos else []
        for cid, q in getattr(self, 'extra', {}).items():          # other contracts held (the Auto-Hedge tests)
            if q:
                out.append(types.SimpleNamespace(contract=types.SimpleNamespace(conId=cid), position=q))
        return out

    def isConnected(self):
        return self.connected


# ======================================================================= Webull fake
class HttpErr(Exception):
    def __init__(self, status, msg):
        super().__init__(msg); self.http_status = status; self.error_code = msg


class Resp:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


class FakeWB:
    """Scripted Webull trade client: place answers + a list of order-detail answers."""
    def __init__(self):
        self.place_err = None
        self.details = []          # each: dict row, None (not found), or Exception
        self.pos = 0.0
        self.cancels = 0
        self.placed = []
        outer = self

        class O:
            def place_order(self, account_id, new_orders):
                outer.placed.append(new_orders[0])
                if outer.place_err:
                    e = outer.place_err; outer.place_err = None; raise e
                return Resp({'ok': True})

            def get_order_detail(self, account_id, client_order_id):
                x = outer.details.pop(0) if len(outer.details) > 1 else (outer.details[0] if outer.details else None)
                if isinstance(x, Exception):
                    raise x
                return Resp({'orders': [x]} if x else {'orders': []})

            def cancel_order(self, account_id, client_order_id):
                outer.cancels += 1

            def get_order_open(self, account_id):
                return Resp({'orders': []})

        class A:
            def get_account_position(self, acct):
                rows = [{'symbol': 'TQQQ', 'quantity': str(outer.pos)}] if outer.pos else []
                rows += [{'symbol': k, 'quantity': str(v)} for k, v in getattr(outer, 'extra', {}).items() if v]   # Auto-Hedge tests
                return Resp({'holdings': rows})

            def get_account_list(self):
                return Resp([{'account_id': 'X1'}])

        self.order_v3, self.account_v2 = O(), A()


# ======================================================================= harness
def attach(c):
    """Wire core c to a fake broker. Returns the fake and a function that waits for the order outcome."""
    from algo.live_broker import LiveBroker
    if BROKER == 'ibkr':
        ib = FakeIB()
        b = LiveBroker(ib)
        contract = types.SimpleNamespace(conId=1, exchange='SMART')
        c.place_live = lambda spec, f, fa, u, un: b.place_order(c.strategy, spec, contract, f, fa, u, un, cancel_s=1)
        c.broker_position = lambda: b.position(contract) if ib.connected else None
        return ib, b
    wb = FakeWB()
    wb.account_id = 'X1'
    b = LiveBroker(wb, account_id='X1')
    import threading
    lock = threading.RLock()
    c.place_live = lambda spec, f, fa, u, un: b.place_order(c.strategy, spec, 'TQQQ', f, fa, u, un, lock=lock,
                                                            cancel_s=1, poll_s=0.05)
    c.broker_position = lambda: b.position('TQQQ')
    return wb, b


async def settle(c, secs=3.0):
    t0 = time.time()
    while c.pending and time.time() - t0 < secs:
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)


def wait_s(c, s):
    c.opts = dict(c.opts, cancel_after_s=s)


async def scenarios():
    # -------------------------------------------------- 1 normal entry + exit
    c = core(); fk, b = attach(c); wait_s(c, 1)
    c._enter(now(), 100.0, 'regular')
    if BROKER == 'ibkr':
        fk.trades[-1].set('Submitted'); fk.trades[-1].set('Filled', 250, 100.05)
    else:
        fk.details = [{'status': 'FILLED', 'filled_quantity': '250', 'filled_price': '100.05'}]
    await settle(c)
    pos = c.portfolio.position
    check('entry fills and is booked at the broker price', pos and pos.shares == 250 and abs(pos.entry_price - 100.05) < 1e-9,
          f'position {pos}')
    c._sent = []
    c._exit(now(), 101.0, 'exit-rule', 'regular')
    if BROKER == 'ibkr':
        fk.trades[-1].set('Filled', 250, 101.0)
    else:
        fk.details = [{'status': 'FILLED', 'filled_quantity': '250', 'filled_price': '101'}]
    await settle(c)
    check('exit fills and the ledger goes flat', c.portfolio.position is None, str(c.last_order))

    # -------------------------------------------------- 2 size at $300k: capped by max_order_notional_usd
    c = core(); fk, b = attach(c); wait_s(c, 1)
    c._enter(now(), 100.0, 'regular')
    sh = c.pending and c.pending['shares']
    check('$300k ledger, 25% sizing: order capped at $25,000', sh == 249, f'{sh} shares x ~$100 (25% of $300k would be 750)')
    await settle(c, 3)

    # -------------------------------------------------- 3 three rejections -> live trading stops
    c = core(); fk, b = attach(c); wait_s(c, 1)
    for k in range(3):
        c.cooldown_until = 0; c._sent = []
        c._enter(now(), 100.0, 'regular')
        if BROKER == 'ibkr':
            fk.trades[-1].set('Cancelled', code=201, msg='Order rejected - reason: insufficient buying power')
        else:
            fk.details = [{'status': 'FAILED', 'filled_quantity': '0'}]
        await settle(c)
    check('rejection: nothing booked', c.portfolio.position is None)
    check('rejection: 15 min cool-down after a reject', c.cooldown_until > time.time() + 800)
    check('3 rejections in a row stop live trading', bool(c.halted), (c.halted or '')[:80])

    # -------------------------------------------------- 4 partial fill then cancel
    c = core(); fk, b = attach(c); wait_s(c, 1)
    c._enter(now(), 100.0, 'regular')
    if BROKER == 'ibkr':
        fk.trades[-1].set('Submitted', 100, 100.0)
        fk.on_cancel = lambda t: t.set('Cancelled', 100, 100.0)
    else:
        fk.details = [{'status': 'PARTIAL_FILLED', 'filled_quantity': '100', 'filled_price': '100'}] * 30 + \
                     [{'status': 'CANCELLED', 'filled_quantity': '100', 'filled_price': '100'}]
    await settle(c, 5)
    pos = c.portfolio.position
    check('partial fill then cancel: books only the 100 filled', pos and pos.shares == 100, str(pos))

    # -------------------------------------------------- 5 no answer at all -> unconfirmed -> paused
    c = core(); fk, b = attach(c); wait_s(c, 1)
    c._enter(now(), 100.0, 'regular')
    if BROKER == 'webull':
        fk.details = [HttpErr(500, 'timeout')]
    await asyncio.sleep(3.5)
    check('broker silent: order marked unconfirmed and live trading PAUSED', bool(c.halted) and c.pending is not None,
          (c.halted or '')[:70])
    c._sent = []
    c._exit(now(), 100.0, 'exit-rule', 'regular')
    check('while paused, nothing else is sent', c.last_order['status'] in ('blocked',) or len(getattr(fk, 'trades', getattr(fk, 'placed', []))) == 1,
          str(c.last_order)[:120])

    # -------------------------------------------------- 6 fill arrives after we reported unfilled
    if BROKER == 'ibkr':
        c = core(); fk, b = attach(c); wait_s(c, 1)
        c._enter(now(), 100.0, 'regular')
        fk.on_cancel = lambda t: t.set('Cancelled')
        await settle(c, 3)
        fk.trades[-1].orderStatus.filled = 249; fk.trades[-1].set('Filled', 249, 100)
        check('late fill after cancel: pauses live trading', bool(c.halted), (c.halted or '')[:70])

    # -------------------------------------------------- 7 Webull: rate limit (429) on send / lookups
    if BROKER == 'webull':
        c = core(); fk, b = attach(c); wait_s(c, 1)
        fk.place_err = HttpErr(429, 'TOO_MANY_REQUESTS')
        c._enter(now(), 100.0, 'regular')
        await settle(c)
        check('429 on send: reported rejected, nothing booked, cool-down', c.portfolio.position is None and c.last_order['status'] == 'rejected',
              str(c.last_order['detail'])[:80])
        c = core(); fk, b = attach(c); wait_s(c, 1)
        fk.details = [HttpErr(429, 'TOO_MANY_REQUESTS')] * 5 + [{'status': 'FILLED', 'filled_quantity': '249', 'filled_price': '100'}]
        c._enter(now(), 100.0, 'regular')
        await settle(c)
        check('429 on order lookups: keeps looking and books the fill', c.portfolio.position and c.portfolio.position.shares == 249)
        c = core(); fk, b = attach(c); wait_s(c, 1)
        fk.place_err = HttpErr(502, 'bad gateway')
        fk.details = [None] * 10 + [None]
        c._enter(now(), 100.0, 'regular')
        await settle(c, 4)
        check('send failed (502) and Webull never got it: reported not sent', c.portfolio.position is None and c.last_order['status'] == 'rejected',
              str(c.last_order['detail'])[:80])
        c = core(); fk, b = attach(c); wait_s(c, 1)
        fk.place_err = HttpErr(502, 'bad gateway')
        fk.details = [None, {'status': 'FILLED', 'filled_quantity': '249', 'filled_price': '100'}]
        c._enter(now(), 100.0, 'regular')
        await settle(c, 4)
        check('send failed (502) but Webull did get it: fill found and booked (no double order)',
              c.portfolio.position and c.portfolio.position.shares == 249 and len(fk.placed) == 1)

    # -------------------------------------------------- 8 duplicate-order guard
    c = core(); fk, b = attach(c); wait_s(c, 1)
    c._enter(now(), 100.0, 'regular')
    await settle(c, 3)
    c.halted = None; c.pending = None
    c._enter(now(), 100.0, 'regular')
    check('same order again within 30 s is blocked', c.last_order['status'] == 'blocked', str(c.last_order['detail'])[:90])

    # -------------------------------------------------- 9 trailing stop + short cancel window -> duplicate guard
    c = core(); fk, b = attach(c)
    c.opts = dict(c.opts, cancel_after_s=10, trailing_stop_pct=1.0)
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True)
    if BROKER == 'ibkr':
        fk.pos = 200; fk.on_cancel = lambda t: t.set('Cancelled')
    else:
        fk.pos = 200; fk.details = [{'status': 'NEW', 'filled_quantity': '0'}] * 25 + [{'status': 'CANCELLED', 'filled_quantity': '0'}]
    wait_s(c, 1)
    c.on_tick(98.5, int(time.time() * 1000))       # trailing stop fires: sell 200
    await settle(c, 5)                             # not filled -> cancelled -> "unfilled"
    c.on_tick(98.4, int(time.time() * 1000))       # still below the stop: fires again
    check('trailing-stop exit that did not fill is retried (not paused)', not c.halted,
          (c.halted or 'retried: ' + str(c.last_order['status']))[:110])

    # -------------------------------------------------- 10 kill switch while paused
    c = core(); fk, b = attach(c)
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True)
    fk.pos = 200
    c.halted = 'test: 3 rejects'
    open(c.kill_path, 'w').write('{}')
    c.check_kill()
    check('Stop button while the algo is paused still closes the position', c.pending is not None,
          'last: ' + str(c.last_order and c.last_order['detail'])[:90])

    # -------------------------------------------------- 11 kill switch: SELL ALL already sold
    c = core(); fk, b = attach(c)
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True)
    fk.pos = 0
    open(c.kill_path, 'w').write('{"by":"sellall"}')
    c.check_kill()
    check('kill after SELL ALL sold: algo marks flat, never sells twice', c.portfolio.position is None and c.pending is None)

    # -------------------------------------------------- 12 disconnect / IB Gateway nightly restart
    c = core(); fk, b = attach(c)
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True); fk.pos = 200
    c._guardian_tick()
    c.connected = False
    if BROKER == 'ibkr':
        fk.connected = False
    c._guardian_tick()
    check('connection lost: safe mode on', bool(getattr(c, 'safe_mode', None)), getattr(c, 'safe_mode', ''))
    c._sent = []
    c._exit(now(), 100, 'exit-rule', 'regular')
    check('safe mode blocks orders while disconnected', c.last_order['status'] == 'blocked')
    if BROKER == 'ibkr':                           # (Webull reads positions over the web, not the stream)
        open(c.kill_path, 'w').write('{}')
        c._kill_try = 0
        c.check_kill()
        check('kill while disconnected: does NOT mark flat on an empty position list', c.portfolio.position is not None,
              str(c.last_order['detail'])[:80])
        os.remove(c.kill_path); c.check_kill()
    c.connected = True; fk.connected = True
    c._guardian_tick()
    c._g['conn_since'] -= 61; c._g['pos'] = 0
    c._guardian_tick()
    check('reconnected and positions match: safe mode clears by itself', not getattr(c, 'safe_mode', None))

    # -------------------------------------------------- 13 position differs from broker -> safe mode
    c = core(); fk, b = attach(c)
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True); fk.pos = 150
    for _ in range(2):
        c._guardian_tick(); c._g['pos'] = 0
    check('broker position differs twice: safe mode (no orders)', str(getattr(c, 'safe_mode', '')).startswith('position'))

    # -------------------------------------------------- 14 restart mid-position
    c = core(); fk, b = attach(c)
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True); c.save()
    c2 = ct.TraderCore(c.strategy, 'live', logs_dir=c.tmp, starting_cash=300_000, status_path=c.status_path,
                       broker_name=BROKER, options_path=c.options_path)
    check('restart: open position restored from disk', c2.portfolio.position and c2.portfolio.position.shares == 200)
    try:
        c2.check_matches_broker(0, BROKER); ok = False
    except SystemExit:
        ok = True
    check('restart: refuses to start if the broker does not hold it', ok)

    # -------------------------------------------------- 15 frozen price: nothing trades on old prices
    c = core('paper')
    c.on_tick(100.0, int(time.time() * 1000) - 600000)
    c.on_timer(int(time.time() * 1000))
    n = len(c.bars)
    c.on_timer(int(time.time() * 1000) + 120000)
    check('frozen feed: no new bars form, so no new signals', len(c.bars) == n)

    # -------------------------------------------------- 16 guards
    st = c.strategy
    for args, want in [((st, 'buy', 'TQQQ', 0, 100), 'zero shares'), ((st, 'buy', 'TQQQ', 20000, 1), 'quantity'),
                       ((st, 'buy', 'TQQQ', 300, 100), 'notional'), ((st, 'hold', 'TQQQ', 1, 1), 'side')]:
        try:
            guards.check_order(*args); check('guard blocks ' + want, False)
        except guards.GuardRejected:
            check('guard blocks ' + want, True)
    try:
        guards.check_order(st, 'sell', 'TQQQ', 300, 100, closing=True); check('guard always lets a closing order out', True)
    except guards.GuardRejected as e:
        check('guard always lets a closing order out', False, str(e))
    os.environ.pop('IBKR_LIVE_TRADING_CONFIRM'); os.environ.pop('WEBULL_LIVE_TRADING_CONFIRM')
    try:
        guards.check_live_trading_enabled(st); check('no live order without the second (Mac) switch', False)
    except guards.GuardRejected:
        check('no live order without the second (Mac) switch', True)


async def stop_reentry():
    """SMA strategy: after the stop-loss, does it buy straight back in?"""
    import numpy as np
    os.environ['IBKR_LIVE_TRADING_CONFIRM'] = os.environ['WEBULL_LIVE_TRADING_CONFIRM'] = 'I_UNDERSTAND_THE_RISK'
    tmp = tempfile.mkdtemp()
    cfg = {'name': 's', 'symbol': 'TQQQ', 'indicators': [{'kind': 'sma', 'period': 20}, {'kind': 'sma', 'period': 50}],
           'entry': {'side': 'buy', 'when': 'sma_20 > sma_50'}, 'exit': {'when': 'sma_20 < sma_50'},
           'position_size': {'pct_of_equity': 25}, 'risk': {'stop_loss_pct': 8, 'max_order_notional_usd': 25000}}
    c = ct.TraderCore(Strategy(cfg, os.path.join(tmp, 's.yaml')), 'paper', logs_dir=tmp, starting_cash=300_000,
                      status_path=os.path.join(tmp, 'st.json'), options_path=os.path.join(tmp, 'o.json'))
    c.opts = dict(c.opts, order_type='market')
    t0 = (int(time.time()) // 60 - 400) * 60 * 1000
    prices = [100.0] * 60 + list(np.linspace(100, 120, 60)) + [91.0] + [115.0] * 5   # entry ~100.3, one-bar flush to 91 (stop), trend intact
    for k, p in enumerate(prices):
        c.on_tick(p, t0 + k * 60000)
    reasons = [t.reason for t in c.portfolio.trades]
    after = reasons[reasons.index('stop-loss') + 1:] if 'stop-loss' in reasons else []
    check('SMA algo: after an 8% stop-loss it does NOT buy straight back in', 'entry' not in after[:1],
          f"trades: {', '.join(reasons[-6:])}")


asyncio.run(scenarios())
asyncio.run(stop_reentry())
bad = [r for r in RESULTS if not r[1]]
print(f'\n{BROKER}: {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')


async def ib_link():
    if BROKER != 'ibkr':
        return
    from algo.paper_trader import IBKRTrader
    tmp = tempfile.mkdtemp()
    a = types.SimpleNamespace(mode='paper', status_path=os.path.join(tmp, 's.json'), starting_cash=300000, commission_per_share=0,
                              slippage_pct=0, max_order_quantity=10000, max_order_notional_usd=None, options_path=os.path.join(tmp, 'o.json'),
                              no_tbt=False)
    t = IBKRTrader(strategy(tmp), a)
    calls = []
    t.ib.isConnected = lambda: True
    t.ib.reqMktData = lambda *x: calls.append('req')
    t.ib.reqTickByTickData = lambda *x: calls.append('tbt')
    t.ib.cancelMktData = lambda *x: calls.append('cancel')
    t.ib.cancelTickByTickData = lambda *x: calls.append('cancel')
    t.contract = types.SimpleNamespace(conId=1, exchange='SMART')
    t.core.connected = True
    t.core._guardian_tick()
    t.on_error(-1, 1100, 'Connectivity between IB and Trader Workstation has been lost.')
    t.core._guardian_tick()
    check('IBKR 1100 (Gateway lost its link to IBKR): safe mode on', not t.core.connected and bool(getattr(t.core, 'safe_mode', None)))
    t.on_error(-1, 1101, 'Connectivity restored - data lost.')
    await asyncio.sleep(1.3)
    check('IBKR 1101 (link back, data lost): prices asked for again', t.core.connected and calls.count('req') == 1 and 'tbt' in calls, str(calls))


asyncio.run(ib_link())
bad = [r for r in RESULTS if not r[1]]
print(f'\n{BROKER} (with link test): {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')


async def round2():
    os.environ['IBKR_LIVE_TRADING_CONFIRM'] = os.environ['WEBULL_LIVE_TRADING_CONFIRM'] = 'I_UNDERSTAND_THE_RISK'
    # buying power
    c = core(); fk, b = attach(c); wait_s(c, 1)
    c.buying_power, c.buying_power_at = 10000.0, time.time()
    c._enter(now(), 100.0, 'regular')
    check('buying power: entry skipped when the order costs more than the account can pay',
          c.pending is None and c.last_order['status'] == 'skipped', c.last_order['detail'][:90])
    c.buying_power = 50000.0
    c._enter(now(), 100.0, 'regular')
    check('buying power: entry sent when there is enough', c.pending is not None)
    await settle(c, 3)
    c = core(); fk, b = attach(c)
    c.buying_power, c.buying_power_at = 10.0, time.time() - 600
    c._enter(now(), 100.0, 'regular')
    check('buying power: an old reading (10 min) is not used', c.pending is not None)
    await settle(c, 3)
    # backtest re-arm
    import numpy as np
    from algo.backtest import run_backtest
    tmp = tempfile.mkdtemp()
    cfg = {'name': 's', 'symbol': 'TQQQ', 'indicators': [{'kind': 'sma', 'period': 20}, {'kind': 'sma', 'period': 50}],
           'entry': {'side': 'buy', 'when': 'sma_20 > sma_50'}, 'exit': {'when': 'sma_20 < sma_50'},
           'position_size': {'pct_of_equity': 25}, 'risk': {'stop_loss_pct': 8}, 'trading': {'order_type': 'market'}}
    pr = [100.0] * 60 + list(np.linspace(100, 120, 60)) + [91.0] + [115.0] * 5
    idx = pd.date_range('2026-10-08 14:00', periods=len(pr), freq='1min', tz='UTC')
    bars = pd.DataFrame({'open': pr, 'high': pr, 'low': pr, 'close': pr, 'volume': 1000.0}, index=idx)
    r = run_backtest(Strategy(cfg, os.path.join(tmp, 's.yaml')), bars)
    rs = [t.reason for t in r['portfolio'].trades]
    check('backtest matches: no buy-back right after the stop-loss', 'stop-loss' in rs and rs[rs.index('stop-loss') + 1:rs.index('stop-loss') + 2] in ([], ['end-of-data']), ', '.join(rs))


async def stall():
    if BROKER != 'ibkr':
        return
    from algo import paper_trader as pt
    pt.TraderCore.session_now = lambda self: ('regular', True)
    tmp = tempfile.mkdtemp()
    a = types.SimpleNamespace(mode='paper', status_path=os.path.join(tmp, 's.json'), starting_cash=300000, commission_per_share=0,
                              slippage_pct=0, max_order_quantity=10000, max_order_notional_usd=None, options_path=os.path.join(tmp, 'o.json'),
                              no_tbt=False)
    t = pt.IBKRTrader(strategy(tmp), a)
    calls = []
    t.ib.reqMktData = lambda *x: calls.append('req'); t.ib.reqTickByTickData = lambda *x: calls.append('tbt')
    t.ib.cancelMktData = lambda *x: None; t.ib.cancelTickByTickData = lambda *x: None
    t.contract = types.SimpleNamespace(conId=1); t.core.connected = True
    t._stall_check(); r1 = list(calls)
    t.alive_at -= 301; t._stall_check()
    await asyncio.sleep(1.3)
    check('IBKR prices silent 5 min in regular hours: asks again + notes it', r1 == [] and 'req' in calls and 'no prices' in (t.core.last_error or ''),
          t.core.last_error or '')


asyncio.run(round2())
asyncio.run(stall())
bad = [r for r in RESULTS if not r[1]]
print(f'\n{BROKER} (all rounds): {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')


class FakeStops:
    def __init__(self):
        self.placed, self.cancels, self.state, self.filled, self.avg, self.refuse = [], 0, 'working', 0, None, False
        self.listed = None             # is the stop among the broker's open orders? True / False / None (can't tell)

    def api(self):
        return {'place': self.place, 'cancel': self.cancel, 'status': self.status, 'listed': lambda b: self.listed}

    def place(self, side, shares, stop):
        if self.refuse:
            raise RuntimeError('stop orders not allowed')
        self.placed.append((side, shares, stop)); self.state, self.filled = 'working', 0
        return {'id': len(self.placed)}

    def cancel(self, b):
        self.cancels += 1

    def status(self, b):
        return self.state, self.filled, self.avg


async def backup_stop():
    os.environ['IBKR_LIVE_TRADING_CONFIRM'] = os.environ['WEBULL_LIVE_TRADING_CONFIRM'] = 'I_UNDERSTAND_THE_RISK'

    def setup(**risk):
        c = core(**risk); fk, b = attach(c); wait_s(c, 1)
        fs = FakeStops(); c.stop_api = fs.api()
        c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True); fk.pos = 200
        return c, fk, fs

    def tick(c, n=1):
        for _ in range(n):
            c._bstop_checked = 0; c._bstop_tick()

    c, fk, fs = setup(stop_loss_pct=8)
    tick(c)
    check('backup stop: placed at the broker after a buy, 1% wider than the algo stop', fs.placed == [('sell', 200, 91.0)] and c.bstop,
          str(fs.placed))
    tick(c)
    check('backup stop: placed only once', len(fs.placed) == 1)
    c2 = ct.TraderCore(c.strategy, 'live', logs_dir=c.tmp, starting_cash=300000, status_path=c.status_path, broker_name=BROKER,
                       options_path=c.options_path)
    check('backup stop: remembered across a restart', c2.bstop and c2.bstop['shares'] == 200)
    # algo exit: cancel first, then sell
    c._sent = []
    c._exit(now(), 100.0, 'exit-rule', 'regular')
    check('exit with a backup stop: cancels the stop first, sends nothing yet', fs.cancels == 1 and c.pending is None,
          str(c.last_order['detail'])[:80])
    c._exit(now(), 100.0, 'exit-rule', 'regular'); tick(c)
    check('exit: no second cancel or sell while waiting', fs.cancels == 1 and c.pending is None)
    fs.state = 'cancelled'; tick(c)
    check('exit: once the stop is cancelled, the sell goes out', c.pending is not None and c.pending['side'] == 'sell' and c.bstop is None)
    await settle(c, 3)

    # stop filled at the broker
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); fs.state, fs.filled, fs.avg = 'filled', 200, 90.9; fk.pos = 0; tick(c)
    check('backup stop fills at the broker: ledger flat at its price, algo sends nothing', c.portfolio.position is None and c.pending is None
          and c.portfolio.trades[-1].reason == 'broker-stop' and c.portfolio.trades[-1].price == 90.9)
    tick(c)
    check('after the broker stop: no new stop placed', len(fs.placed) == 1)

    # race: exit waiting while the stop fills
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); c._sent = []
    c._exit(now(), 91.5, 'stop-loss', 'regular')
    fs.state, fs.filled, fs.avg = 'filled', 200, 91.0; fk.pos = 0; tick(c, 3)
    check('race: the stop fills while the algo is cancelling it: no second sell', c.portfolio.position is None and c.pending is None
          and (fk.trades if BROKER == 'ibkr' else fk.placed) == [])

    # partial fill of the stop while cancelling
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); c._sent = []
    c._exit(now(), 95, 'exit-rule', 'regular')
    fs.state, fs.filled, fs.avg = 'cancelled', 50, 91.0; fk.pos = 150; tick(c, 2)
    check('stop part-filled then cancelled: books 50, sells only the other 150',
          c.pending is not None and c.pending['shares'] == 150, str(c.pending))
    await settle(c, 3)

    # SELL ALL cancels the stop (not the algo)
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); fs.state = 'cancelled'; tick(c)
    check('stop cancelled outside the algo: noted, not re-placed for 5 min', c.bstop is None and len(fs.placed) == 1)
    c._bstop_failed_at -= 301; tick(c)
    check('...and placed again after 5 min while still holding', len(fs.placed) == 2)

    # refused
    c, fk, fs = setup(stop_loss_pct=8); fs.refuse = True
    tick(c)
    check('broker refuses the stop: noted, algo keeps trading, retry later', c.bstop is None and not c.halted
          and c.last_order['status'] == 'stop not placed')

    # unknown answers during an exit
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); c._sent = []
    c._exit(now(), 95, 'exit-rule', 'regular'); fs.state = 'unknown'; tick(c)
    check('no answer about the stop: exit keeps waiting at first', c.pending is None)
    pushed = []
    c._push = lambda *a, **k: pushed.append(a)
    c._bstop_unknown_since -= 61; tick(c, 2)
    check('no answer for 60 s, broker still holds all: exit keeps waiting (no double sell), one push',
          c.pending is None and len(pushed) == 1, str(pushed))
    tick(c, 2)
    check('...and no second push', len(pushed) == 1)
    fk.pos = 0; tick(c, 2)
    check('...broker shows the shares gone: booked as sold by the backup stop, nothing sent',
          c.portfolio.position is None and c.pending is None, str(c.portfolio.position))

    # review 2026-10-11 #1: while SELL ALL closes the position, the algo's own exits send nothing
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); c._sent = []
    with open(c.kill_path, 'w') as f:
        json.dump({'at': 'x', 'by': 'sellall'}, f)
    c.killed = 'x'
    fs.state = 'cancelled'; tick(c)
    c._exit(now(), 95, 'exit-rule', 'regular'); tick(c, 2)
    check('SELL ALL running: algo stop-loss/exit sends nothing (no double sell)',
          c.pending is None and c.last_order['status'] == 'blocked', str(c.last_order))
    os.remove(c.kill_path); c.killed = None

    # review 2026-10-11 #3: after a rejection (cooldown) or a halt, closing orders still go out
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); c._sent = []
    c.cooldown_until = time.time() + 900
    c.halted = '3 live orders in a row were rejected'
    fs.state = 'cancelled'; tick(c)
    c._exit(now(), 95, 'stop-loss', 'regular'); tick(c, 2)
    check('cooldown + halted: the stop-loss sell still goes out', c.pending is not None and c.pending['side'] == 'sell',
          str(c.last_order))
    await settle(c, 3)
    await settle(c, 3)

    # cancel not confirmed: never sells while the stop may still work
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); c._sent = []
    c._exit(now(), 95, 'exit-rule', 'regular'); tick(c, 3)
    c.bstop['cancel_at'] -= 31; tick(c)
    check('stop still working after cancel: asks again, still no sell', fs.cancels == 2 and c.pending is None)

    # kill switch with a stop
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c); open(c.kill_path, 'w').write('{}'); c.check_kill()
    check('Stop button: cancels the backup stop first', fs.cancels == 1 and c.pending is None)
    fs.state = 'cancelled'; tick(c)
    check('Stop button: then sells, and no new backup stop', c.pending is not None and len(fs.placed) == 1)
    await settle(c, 3)

    # restart: filled while away
    c, fk, fs = setup(stop_loss_pct=8)
    tick(c)
    ok = c.bstop_filled_while_away(0)
    check('restart after the stop filled while the Mac was off: books it, starts cleanly', ok and c.portfolio.position is None)
    try:
        c.check_matches_broker(0, BROKER); ok = True
    except SystemExit:
        ok = False
    check('...and the start-up position check then passes', ok)

    # no stop-loss in the strategy: default 10%; broker_stop_pct 0 = off
    c, fk, fs = setup(); tick(c)
    check('no stop-loss set: backup stop at 10%', fs.placed and fs.placed[0][2] == 90.0, str(fs.placed))
    c, fk, fs = setup(stop_loss_pct=8, broker_stop_pct=0); tick(c)
    check('broker_stop_pct: 0 turns it off', fs.placed == [])

    # paper never places one
    c = core('paper'); fs = FakeStops(); c.stop_api = fs.api()
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True); c._bstop_tick()
    check('paper mode: no backup stop', fs.placed == [])


async def bstop_broker():
    """The real broker functions for the backup stop, against the fake broker clients."""
    tmp = tempfile.mkdtemp()
    if BROKER == 'ibkr':
        from algo import paper_trader as pt
        a = types.SimpleNamespace(mode='live', status_path=os.path.join(tmp, 's.json'), starting_cash=300000, commission_per_share=0,
                                  slippage_pct=0, max_order_quantity=10000, max_order_notional_usd=None, options_path=os.path.join(tmp, 'o.json'),
                                  no_tbt=False)
        t = pt.IBKRTrader(strategy(tmp, stop_loss_pct=8), a)
        ib = FakeIB(); t.ib = ib; t.broker.ib = ib
        t.contract = types.SimpleNamespace(conId=1, exchange='SMART')

        ib.trades_list = []

        def place(contract, order):
            tr = FakeTrade(order); tr.contract = contract; order.orderId = len(ib.trades_list) + 1; ib.trades_list.append(tr); return tr
        ib.placeOrder = place
        ib.trades = lambda: ib.trades_list
        b = t.core.stop_api['place']('sell', 200, 91.0)
        o = ib.trades_list[-1].order
        check('IBKR backup stop: STP sell 200 @ 91, GTC, outside regular hours on, tagged',
              o.orderType == 'STP' and o.auxPrice == 91.0 and o.tif == 'GTC' and o.outsideRth and o.orderRef == 'jb-backup-stop'
              and o.totalQuantity == 200, f'{o.orderType} {o.auxPrice} {o.tif} {o.outsideRth}')
        check('IBKR backup stop: status working', t.core.stop_api['status'](b)[0] == 'working')
        ib.trades_list[-1].set('Filled', 200, 90.95)
        check('IBKR backup stop: status filled with the fill price', t.core.stop_api['status'](b) == ('filled', 200, 90.95))
    else:
        sys.modules.setdefault('webull', types.ModuleType('webull'))
        from algo import paper_trader as pt
        from algo.live_broker import LiveBroker
        tr = pt.WebullTrader.__new__(pt.WebullTrader)
        wb = FakeWB(); tr.broker = LiveBroker(wb, account_id='X1')
        tr.core = types.SimpleNamespace(strategy=strategy(tmp))
        b = tr._bs_place('sell', 200, 91.0)
        o = wb.placed[-1]
        check('Webull backup stop: STOP_LOSS sell 200 @ 91, GTC, all sessions first',
              o['order_type'] == 'STOP_LOSS' and o['stop_price'] == '91' and o['time_in_force'] == 'GTC' and o['quantity'] == '200'
              and o['support_trading_session'] == 'ALL', str(o))
        wb.details = []
        wb.place_err = HttpErr(400, 'INVALID_PARAMETER support_trading_session')   # refuses the first (all-sessions) stop
        tr._bs_session = 'ALL'
        tr._bs_place('sell', 200, 91.0)
        check('Webull refuses an all-sessions stop: placed again for regular hours, remembered',
              wb.placed[-2]['support_trading_session'] == 'ALL' and wb.placed[-1]['support_trading_session'] == 'CORE'
              and tr._bs_session == 'CORE', str(wb.placed[-2:]))
        wb.details = [{'status': 'SUBMITTED', 'filled_quantity': '0'}]
        check('Webull backup stop: status working', tr._bs_status(b)[0] == 'working')
        wb.details = [{'status': 'FILLED', 'filled_quantity': '200', 'filled_price': '90.9'}]
        check('Webull backup stop: status filled', tr._bs_status(b) == ('filled', 200.0, 90.9))
        wb.details = [{'status': 'CANCELLED', 'filled_quantity': '0'}]
        check('Webull backup stop: status cancelled', tr._bs_status(b)[0] == 'cancelled')


asyncio.run(backup_stop())
asyncio.run(bstop_broker())
bad = [r for r in RESULTS if not r[1]]
print(f'\n{BROKER} (with backup stop): {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')


# ======================================================================= round 3 (second review, 2026-10-11)
# Each block below is one asyncio.run(...); the summary and exit code come last.
def session_now_real(self):                # the real one (stall() above replaced it for IBKR)
    s = ct.session_at()
    return s, s is not None and s in self.allowed_sessions()


def live_core(**risk):
    os.environ['IBKR_LIVE_TRADING_CONFIRM'] = os.environ['WEBULL_LIVE_TRADING_CONFIRM'] = 'I_UNDERSTAND_THE_RISK'
    ct.TraderCore.session_now = session_now_real
    c = core(**risk); fk, b = attach(c); wait_s(c, 1)
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True); fk.pos = 200
    return c, fk


def reject_last(fk):
    if BROKER == 'ibkr':
        fk.trades[-1].set('Cancelled', code=201, msg='Order rejected - reason: some broker reason')
    else:
        fk.details = [{'status': 'FAILED', 'filled_quantity': '0'}]


async def r3_fills_and_sessions():
    # exit partly filled, then cancelled: the rest stays open and the next exit sells only the rest
    c, fk = live_core()
    c._exit(now(), 99.0, 'exit-rule', 'regular')
    if BROKER == 'ibkr':
        fk.trades[-1].set('Submitted', 120, 99.0)
        fk.on_cancel = lambda t: t.set('Cancelled', 120, 99.0)
    else:
        fk.details = [{'status': 'PARTIAL_FILLED', 'filled_quantity': '120', 'filled_price': '99'}] * 30 + \
                     [{'status': 'CANCELLED', 'filled_quantity': '120', 'filled_price': '99'}]
    await settle(c, 5)
    fk.pos = 80
    pos = c.portfolio.position
    check('exit part-filled then cancelled: books the 120 sold, keeps 80 open', pos is not None and pos.shares == 80, str(pos))
    c._sent = []
    c._exit(now(), 99.0, 'exit-rule', 'regular')
    check('...and the next exit sells only the 80 left', c.pending is not None and c.pending['shares'] == 80, str(c.pending))
    await settle(c, 3)

    # stop-loss sell rejected: tried again after 1 minute (not 15)
    c, fk = live_core()
    c._exit(now(), 95, 'stop-loss', 'regular')
    reject_last(fk)
    await settle(c)
    n0 = len(fk.trades if BROKER == 'ibkr' else fk.placed)
    c._exit(now(), 95, 'stop-loss', 'regular')
    n1 = len(fk.trades if BROKER == 'ibkr' else fk.placed)
    c._close_retry_at -= 61
    c._sent = [(t - 61, sd, n) for t, sd, n in c._sent]      # the minute really passes
    c._exit(now(), 95, 'stop-loss', 'regular')
    check('stop-loss sell rejected: not resent at once, resent after 1 minute', n1 == n0 and c.pending is not None,
          f'orders {n0} -> {n1}, pending {c.pending}')
    await settle(c, 3)

    # overnight / holiday session times
    real = ct.session_at
    try:
        ct.session_at = lambda *a, **k: 'overnight'
        c, fk = live_core()
        c._exit(now(), 95, 'stop-loss', 'overnight')
        check('overnight not ticked for live: the algo sends nothing overnight (says so)', c.pending is None and
              'not turned on' in c.last_order['detail'], c.last_order['detail'])
        c.opts = dict(c.opts, live_sessions=['overnight'], order_type='market')
        spec = c.order_spec('sell', 200, 95, 'overnight', closing=True)
        check('overnight ticked: a market exit is sent as a limit order overnight', spec['type'] == 'limit' and spec['limit'] < 95, str(spec))
        ct.session_at = lambda *a, **k: None                 # holiday / weekend / 3:50-4 am
        c = core('paper')
        c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True)
        c.opts = dict(c.opts, trailing_stop_pct=1.0, paper_sessions=['premarket', 'afterhours', 'overnight'])
        c.on_tick(90.0, int(time.time() * 1000))
        check('market closed (holiday): no paper order at all, not even the trailing stop', c.paper_order is None and
              c.portfolio.position is not None)
    finally:
        ct.session_at = real


asyncio.run(r3_fills_and_sessions())


async def r3_bstop_gone():
    # backup stop: no answer about it, and the broker's own open-order list shows it is gone
    c, fk = live_core(stop_loss_pct=8)
    fs = FakeStops(); c.stop_api = fs.api()
    c._bstop_checked = 0; c._bstop_tick()
    c._sent = []
    c._exit(now(), 91.5, 'stop-loss', 'regular')
    fs.state = 'unknown'; c._bstop_checked = 0; c._bstop_tick()
    c._push = lambda *a, **k: None
    c._bstop_unknown_since -= 61
    fs.listed = True
    for _ in range(3):
        c._bstop_checked = 0; c._bstop_tick()
    check('no answer about the stop, but it is still among the broker\'s open orders: keeps waiting', c.pending is None)
    fs.listed = None
    for _ in range(3):
        c._bstop_checked = 0; c._bstop_tick()
    check('no answer about the stop and the open-order list can\'t be read either: keeps waiting', c.pending is None)
    fs.listed = False
    c._bstop_checked = 0; c._bstop_tick()
    first = c.pending
    c._bstop_gone_at -= 11
    c._bstop_checked = 0; c._bstop_tick()
    check('no answer about the stop and the broker lists no such open order (seen twice, 10 s apart): the stop-loss sells',
          first is None and c.pending is not None and c.pending['side'] == 'sell' and c.bstop is None, f'first {first}, then {c.pending}')
    await settle(c, 3)

    # the real broker functions
    tmp = tempfile.mkdtemp()
    if BROKER == 'ibkr':
        from algo import paper_trader as pt
        a = types.SimpleNamespace(mode='live', status_path=os.path.join(tmp, 's.json'), starting_cash=300000, commission_per_share=0,
                                  slippage_pct=0, max_order_quantity=10000, max_order_notional_usd=None, options_path=os.path.join(tmp, 'o.json'),
                                  no_tbt=False)
        t = pt.IBKRTrader(strategy(tmp, stop_loss_pct=8), a)
        ib = FakeIB(); t.ib = ib; t.broker.ib = ib
        t.contract = types.SimpleNamespace(conId=1, exchange='SMART')
        ib.trades_list = []

        def place(contract, order):
            tr = FakeTrade(order); tr.contract = contract; order.orderId = len(ib.trades_list) + 1; ib.trades_list.append(tr); return tr
        ib.placeOrder = place
        ib.trades = lambda: ib.trades_list
        ib.openTrades = lambda: [x for x in ib.trades_list if not x.isDone()]
        t.core.connected = True
        b = t.core.stop_api['place']('sell', 200, 91.0)
        check('IBKR: the backup stop is listed among the open orders', t.core.stop_api['listed'](b) is True)
        ib.trades_list.clear()                                  # e.g. cancelled while IB Gateway was reconnecting
        check('IBKR: a backup stop IBKR no longer lists counts as gone', t.core.stop_api['listed'](b) is False)
        ib.connected = False
        check('IBKR: while disconnected nothing is concluded', t.core.stop_api['listed'](b) is None)
    else:
        sys.modules.setdefault('webull', types.ModuleType('webull'))
        from algo import paper_trader as pt
        from algo.live_broker import LiveBroker
        tr = pt.WebullTrader.__new__(pt.WebullTrader)
        wb = FakeWB(); tr.broker = LiveBroker(wb, account_id='X1')
        tr.core = types.SimpleNamespace(strategy=strategy(tmp))
        wb.open_list = [{'symbol': 'TQQQ', 'client_order_id': 'abc', 'status': 'SUBMITTED'}]
        wb.order_v3.get_order_open = lambda account_id: Resp({'orders': wb.open_list})
        check('Webull: the backup stop is listed among the open orders', tr._bs_listed({'id': 'abc'}) is True)
        wb.open_list = []
        check('Webull: a backup stop Webull no longer lists counts as gone', tr._bs_listed({'id': 'abc'}) is False)

        def boom(account_id):
            raise HttpErr(500, 'down')
        wb.order_v3.get_order_open = boom
        check('Webull: open orders unreadable: nothing is concluded', tr._bs_listed({'id': 'abc'}) is None)


asyncio.run(r3_bstop_gone())


async def r3_webull_open_orders_page():
    if BROKER != 'webull':
        return
    from algo.live_broker import LiveBroker
    wb = FakeWB(); b = LiveBroker(wb, account_id='X1')
    asked = {}

    def page(account_id, page_size=None):
        asked['size'] = page_size
        return Resp({'orders': [{'symbol': 'SOXL', 'client_order_id': f'o{k}'} for k in range(page_size or 10)]})
    wb.order_v3.get_order_open = page
    try:
        b.open_orders_for('TQQQ'); ok = False
    except RuntimeError:
        ok = True
    check('Webull open orders: asks for 100 per page, and a full page is "could not read them all", never "none open"',
          ok and asked['size'] == 100, str(asked))


asyncio.run(r3_webull_open_orders_page())


async def r3_webull_stop_send_error():
    if BROKER != 'webull':
        return
    sys.modules.setdefault('webull', types.ModuleType('webull'))
    from algo import paper_trader as pt
    from algo.live_broker import LiveBroker
    tmp = tempfile.mkdtemp()
    tr = pt.WebullTrader.__new__(pt.WebullTrader)
    wb = FakeWB(); tr.broker = LiveBroker(wb, account_id='X1')
    tr.core = types.SimpleNamespace(strategy=strategy(tmp))
    tr._bs_session = 'ALL'
    # the place call times out (the stop DID reach Webull: the fake keeps it) and the lookup fails too
    wb.place_err = HttpErr(504, 'GATEWAY_TIMEOUT')
    wb.details = [HttpErr(500, 'lookup failed')]
    try:
        tr._bs_place('sell', 200, 91.0); raised = False
    except Exception:
        raised = True
    check('Webull backup stop: send timed out and lookup failed: no second (regular-hours) stop placed',
          raised and len(wb.placed) == 1 and tr._bs_session == 'ALL', str([o['support_trading_session'] for o in wb.placed]))
    first = wb.placed[0]['client_order_id']
    wb.details = [{'status': 'SUBMITTED', 'filled_quantity': '0', 'client_order_id': first}]
    h = tr._bs_place('sell', 200, 91.0)
    check('...5 minutes later: the stop that did reach Webull is found and kept, not placed again',
          len(wb.placed) == 1 and h.get('id') == first and h.get('session') == 'ALL', f'{len(wb.placed)} placed, handle {h}')
    # Webull clearly refuses the all-sessions stop (400): the regular-hours one is used (review fix #5 kept)
    wb = FakeWB(); tr.broker = LiveBroker(wb, account_id='X1'); tr._bs_session = 'ALL'; tr._bs_unsure = None
    wb.place_err = HttpErr(400, 'INVALID_PARAMETER support_trading_session')
    wb.details = [HttpErr(404, 'ORDER_NOT_FOUND')]
    tr._bs_place('sell', 200, 91.0)
    check('Webull refuses the all-sessions stop (400): a regular-hours stop is placed instead',
          [o['support_trading_session'] for o in wb.placed] == ['ALL', 'CORE'] and tr._bs_session == 'CORE')


asyncio.run(r3_webull_stop_send_error())


async def r3_sellall_race():
    # SELL ALL has just written the KILL file; the algo's 2-second timer has not read it yet
    c, fk = live_core()
    with open(c.kill_path, 'w') as f:
        json.dump({'at': 'x', 'by': 'sellall'}, f)
    c._exit(now(), 95, 'stop-loss', 'regular')
    check('SELL ALL pressed a moment ago (timer not run yet): the algo still sends no sell of its own',
          c.pending is None and c.last_order['status'] == 'blocked', str(c.last_order)[:120])


asyncio.run(r3_sellall_race())


async def r3_skipped_not_sent():
    # buy rejected: during the 15-minute wait, the buy signal on every bar is not counted as an order sent
    c = core(); fk, b = attach(c); wait_s(c, 1)
    c._enter(now(), 100.0, 'regular')
    reject_last(fk)
    await settle(c)
    for k in range(40):                    # 40 one-minute bars with the buy signal still true, while it waits
        c._sent = [(t - 61, sd, n) for t, sd, n in c.__dict__.get('_sent', [])]
        c._enter(now(), 100.0, 'regular')
    check('after a rejected buy: the buys skipped while waiting do not trip the 30-orders-a-day pause',
          not c.halted and len(c._sent) == 1, (c.halted or f'{len(c._sent)} counted as sent')[:100])


asyncio.run(r3_skipped_not_sent())


async def r3_ibkr_resubscribe_lines():
    """Asking IBKR for prices again (after code 1101 or a price stall) must not leave the old requests running: each one is
    a market-data line, and IBKR allows about 100. Uses the fake IB Gateway (tests/fake-brokers/fake_tws.py) over a socket."""
    if BROKER != 'ibkr':
        return
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(ALGO)), 'tests', 'fake-brokers'))
    import fake_tws
    from algo import paper_trader as pt
    tmp = tempfile.mkdtemp()
    srv = fake_tws.Server(0, tmp)
    server = await asyncio.start_server(srv.handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    a = types.SimpleNamespace(mode='paper', status_path=os.path.join(tmp, 's.json'), starting_cash=300000, commission_per_share=0,
                              slippage_pct=0, max_order_quantity=10000, max_order_notional_usd=None, options_path=os.path.join(tmp, 'o.json'),
                              no_tbt=False, port=port, host='127.0.0.1', client_id=47, md_type=1)
    t = pt.IBKRTrader(strategy(tmp), a)
    t.ib.errorEvent += t.on_error
    try:
        await t.connect()
        await t.subscribe()
        await asyncio.sleep(0.3)
        n0 = srv.mkt_subs(47)
        for _ in range(3):
            await t._resubscribe()
        await asyncio.sleep(0.3)
        n1 = srv.mkt_subs(47)
        check('IBKR: asking for prices again 3 times keeps 2 market-data lines (regular + overnight venue), none left behind',
              n0 == 2 and n1 == 2 and t.overnight is not None, f'lines before {n0}, after 3 re-asks {n1}')
    finally:
        t.ib.disconnect()
        server.close()


asyncio.run(r3_ibkr_resubscribe_lines())


# ======================================================================= Auto-Hedge (jb-hedge)
from algo import autohedge  # noqa: E402
_REAL_CME_PAUSED = autohedge.cme_paused
autohedge.cme_paused = lambda now=None: False          # CME's 5-6 pm pause is tested on its own below


def hcore(mode='live', settings=None, lock=True, **risk):
    """A core whose logs folder is <tmp>/logs (as on the Mac: <algo folder>/logs), with Auto-Hedge settings in <tmp>."""
    tmp = tempfile.mkdtemp()
    logs = os.path.join(tmp, 'logs')
    os.makedirs(logs)
    if settings is not None:
        cfg = dict({'on': True, 'symbol': 'SQQQ', 'for_symbol': 'TQQQ', 'dollar_ratio': 1.0, 'side': 'buy', 'ratio_pct': 100}, **settings)
        if mode == 'live' and lock:
            with open(os.path.join(logs, 'LIVE_RUNNING'), 'w') as f:
                f.write('4242\n')
            cfg.setdefault('live_on', True)
            cfg.setdefault('live_lock', {'pid': '4242', 'mtime': os.stat(os.path.join(logs, 'LIVE_RUNNING')).st_mtime})
        with open(os.path.join(tmp, 'hedge_settings.json'), 'w') as f:
            json.dump(cfg, f)
    c = ct.TraderCore(strategy(tmp, **risk), mode, logs_dir=logs, starting_cash=300_000,
                      status_path=os.path.join(logs, 'live_status.json'), broker_name=BROKER,
                      options_path=os.path.join(tmp, 'opts.json'))
    c.connected = True
    c.last_price = 100.0
    c.tmp = tmp
    c.hedge.reload(force=True)
    return c


HC = types.SimpleNamespace(conId=2, exchange='SMART', secType='STK', symbol='SQQQ')
FC = types.SimpleNamespace(conId=3, exchange='CME', secType='FUT', symbol='MNQ')


def hattach(c, fut=False):
    """attach() plus the hedge's order and position functions on the same fake broker."""
    fk, b = attach(c)
    if BROKER == 'ibkr':
        fk.extra = {}
        con = FC if fut else HC
        c.hedge.place = lambda spec, f, fa, u, un: b.place_order(autohedge._As(c.strategy, spec['symbol']), spec, con, f, fa, u, un, cancel_s=1)
        c.hedge.position = lambda: b.position(con) if fk.connected else None
    else:
        import threading
        fk.extra = {}
        lk = threading.RLock()
        c.hedge.place = lambda spec, f, fa, u, un: b.place_order(autohedge._As(c.strategy, spec['symbol']), spec, spec['symbol'],
                                                                 f, fa, u, un, lock=lk, cancel_s=1, poll_s=0.05)
        c.hedge.position = lambda: b.position(c.hedge.st.get('symbol') or c.hedge.symbol())
    return fk, b


def n_orders(fk):
    return len(fk.trades) if BROKER == 'ibkr' else len(fk.placed)


def last_order(fk):
    """(side, qty, symbol-or-orderRef) of the last order the fake broker got."""
    if BROKER == 'ibkr':
        o = fk.trades[-1].order
        return o.action.lower(), int(o.totalQuantity), getattr(o, 'orderRef', '')
    o = fk.placed[-1]
    return o['side'].lower(), int(float(o['quantity'])), o['symbol']


def fill_last(fk, n, price):
    if BROKER == 'ibkr':
        fk.trades[-1].set('Filled', n, price)
    else:
        fk.details = [{'status': 'FILLED', 'filled_quantity': str(n), 'filled_price': str(price)}]


def reject_any(fk):
    if BROKER == 'ibkr':
        fk.trades[-1].set('Cancelled', 0, 0, code=201, msg='Order rejected - insufficient buying power')
    else:
        fk.details = [{'status': 'FAILED', 'filled_quantity': '0', 'filled_price': '0'}]


async def hsettle(c, secs=3.0):
    t0 = time.time()
    while (c.pending or c.hedge.pending) and time.time() - t0 < secs:
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.05)


async def live_entry(c, fk, price=100.0):
    c._sent = []
    c._enter(now(), price, 'regular')
    shares = c.pending['shares']
    fill_last(fk, shares, price)
    await hsettle(c)
    fk.pos = shares
    return c.portfolio.position


async def hedge_scenarios():
    # ---------------------------------------------------------------- paper
    c = hcore('paper'); fk, b = hattach(c)
    c.opts = dict(c.opts, order_type='market')
    c.on_hedge_tick(20.0)
    c._enter(now(), 100.0, 'regular')
    c.on_timer(int(time.time() * 1000))
    check('Auto-Hedge default OFF: no settings file, no hedge after a buy', c.portfolio.position and c.hedge.units() == 0 and n_orders(fk) == 0,
          str(c.hedge.status()['why']))

    c = hcore('paper', {}); fk, b = hattach(c)
    c.opts = dict(c.opts, order_type='market')
    c.on_hedge_tick(20.0)
    c._enter(now(), 100.0, 'regular')
    sh = c.portfolio.position.shares
    c.on_timer(int(time.time() * 1000))
    check('paper: Auto-Hedge on -> a simulated hedge right after the buy ($1 of SQQQ per $1), no broker order',
          c.hedge.units() == round(sh * 100.0 / 20.0) and n_orders(fk) == 0 and 'paper' in (c.hedge.note or ''),
          f'{sh} TQQQ -> {c.hedge.units()} SQQQ; {c.hedge.note}')
    st = json.load(open(c.status_path))
    check('paper: the algo tab sees the hedge (live_status.json "hedge")', st.get('hedge', {}).get('units') == c.hedge.units(), str(st.get('hedge'))[:160])
    c.on_hedge_tick(21.0)
    c._sent = []
    c._exit(now(), 101.0, 'exit-rule', 'regular')
    c.on_timer(int(time.time() * 1000))
    check('paper: the position closes -> the hedge closes at once and its P&L is booked',
          c.portfolio.position is None and c.hedge.units() == 0 and c.hedge.st['day_pnl'] > 0, f"P&L {c.hedge.st['day_pnl']}")

    c = hcore('paper', {}); fk, b = hattach(c)
    c.opts = dict(c.opts, order_type='market')
    c.on_hedge_tick(20.0)
    c._enter(now(), 100.0, 'regular'); c.on_timer(int(time.time() * 1000))
    held = c.hedge.units()
    with open(c.kill_path, 'w') as f:
        json.dump({'at': 'x'}, f)
    c.on_timer(int(time.time() * 1000))
    check('paper: Stop button -> the position AND the hedge close at once, nothing new opens', held > 0 and c.portfolio.position is None
          and c.hedge.units() == 0)
    c._enter(now(), 100.0, 'regular'); c.on_timer(int(time.time() * 1000))
    check('paper: while stopped no new hedge (and no new position)', c.hedge.units() == 0 and c.portfolio.position is None)

    c = hcore('paper', {}); fk, b = hattach(c)
    c.opts = dict(c.opts, order_type='market')
    c.on_hedge_tick(20.0)
    c._enter(now(), 100.0, 'regular'); c.on_timer(int(time.time() * 1000))
    with open(c.kill_path, 'w') as f:
        json.dump({'at': 'x', 'by': 'sellall'}, f)
    c.on_timer(int(time.time() * 1000))
    check('paper: SELL ALL -> the hedge is closed too (simulated), no order sent', c.hedge.units() == 0 and n_orders(fk) == 0,
          str(c.hedge.note))

    c = hcore('paper', {}); fk, b = hattach(c)
    c.opts = dict(c.opts, order_type='market')
    c.on_hedge_tick(20.0)
    c._enter(now(), 100.0, 'regular'); c.on_timer(int(time.time() * 1000))
    cfg = json.load(open(os.path.join(c.tmp, 'hedge_settings.json'))); cfg['on'] = False
    json.dump(cfg, open(os.path.join(c.tmp, 'hedge_settings.json'), 'w')); c.hedge.reload(force=True)
    c.on_timer(int(time.time() * 1000))
    check('Auto-Hedge turned off while hedged: the hedge is closed (never left behind)', c.hedge.units() == 0 and c.portfolio.position is not None)

    c = hcore('paper', {}); fk, b = hattach(c)
    c.opts = dict(c.opts, order_type='market')
    c.on_hedge_tick(20.0)
    c._enter(now(), 100.0, 'regular'); c.on_timer(int(time.time() * 1000))
    h0, s0 = c.hedge.units(), c.portfolio.position.shares
    c.portfolio.reduce_position(now(), s0 // 2, 100.0, 'exit-rule', exact=True)
    c._sent = []
    for _ in range(3):
        c.on_timer(int(time.time() * 1000))
    check('part exit: the hedge shrinks with the position, once (not again on every tick)',
          abs(c.hedge.units() - round(h0 * c.portfolio.position.shares / s0)) <= 1, f'{h0} -> {c.hedge.units()} for {s0} -> {c.portfolio.position.shares} shares')

    # ---------------------------------------------------------------- live
    c = hcore('live', {'live_on': False}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    await live_entry(c, fk)
    n0 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    check('live: Auto-Hedge on but the live switch off -> no hedge order (says why)', n_orders(fk) == n0 and c.hedge.units() == 0
          and 'live hedging is off' in (c.hedge.status()['why'] or ''), str(c.hedge.status()['why']))

    c = hcore('live', {}, lock=False); fk, b = hattach(c); wait_s(c, 1)
    cfg = json.load(open(os.path.join(c.tmp, 'hedge_settings.json')))
    cfg.update(live_on=True, live_lock={'pid': '999', 'mtime': 1.0}); json.dump(cfg, open(os.path.join(c.tmp, 'hedge_settings.json'), 'w'))
    c.hedge.reload(force=True)
    c.on_hedge_tick(20.0)
    await live_entry(c, fk)
    n0 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    check('live: the live switch was confirmed for another ./jb live run (no LIVE_RUNNING match) -> no hedge order',
          n_orders(fk) == n0 and 'earlier ./jb live run' in (c.hedge.status()['why'] or ''), str(c.hedge.status()['why']))

    c = hcore('live', {}, max_order_notional_usd=100000); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(100.0)                                # same price as TQQQ: the hedge order has the same share count
    pos = await live_entry(c, fk)
    n0 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    sent = last_order(fk) if n_orders(fk) > n0 else None
    check('live: switch confirmed for this ./jb live run -> a real hedge order after the buy fills',
          sent is not None and sent[0] == 'buy' and sent[1] == pos.shares and sent[2] in ('jb-hedge', 'SQQQ'), str(sent))
    check('live: the hedge order is not blocked as a "duplicate" of the buy with the same share count', c.hedge.pending is not None
          and c.hedge.pending['units'] == pos.shares, str(c.hedge.note))
    c4 = hcore('live', {'dollar_ratio': 1.2}); fk4, b4 = hattach(c4); wait_s(c4, 1)
    c4.on_hedge_tick(100.0)
    p4 = await live_entry(c4, fk4)
    c4.on_timer(int(time.time() * 1000))
    check('live: a hedge bigger than risk.max_order_notional_usd is cut down to fit it', c4.hedge.pending is not None and
          c4.hedge.pending['units'] * 100.05 <= 25000, f"{c4.hedge.pending and c4.hedge.pending['units']} SQQQ for {p4.shares} TQQQ")
    reject_any(fk4); await hsettle(c4)
    fill_last(fk, pos.shares, 100.0)
    await hsettle(c)
    fk.extra = {2: pos.shares} if BROKER == 'ibkr' else {'SQQQ': pos.shares}
    check('live: the hedge fill is booked', c.hedge.units() == pos.shares and c.hedge.pending is None, str(c.hedge.status())[:200])
    c._sent = []
    c._exit(now(), 101.0, 'exit-rule', 'regular')
    fill_last(fk, pos.shares, 101.0)
    await hsettle(c)
    fk.pos = 0
    n1 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    sent = last_order(fk) if n_orders(fk) > n1 else None
    check('live: the position is sold -> the hedge is sold right after (never left open)', c.portfolio.position is None and sent is not None
          and sent[0] == 'sell' and sent[1] == pos.shares, str(sent))
    fill_last(fk, pos.shares, 99.0)
    await hsettle(c)
    check('live: hedge closed and booked', c.hedge.units() == 0 and c.hedge.st['day_pnl'] < 0, f"P&L {c.hedge.st['day_pnl']}")

    # the broker holds fewer hedge shares than the record (sold some in the app): only those are sold
    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    pos = await live_entry(c, fk)
    c.on_timer(int(time.time() * 1000)); u = c.hedge.pending['units']
    fill_last(fk, u, 20.0); await hsettle(c)
    fk.extra = {2: u - 300} if BROKER == 'ibkr' else {'SQQQ': u - 300}
    c.portfolio.close_position(now(), 100.0, 'exit-rule', exact=True); fk.pos = 0
    c._sent = []; c.hedge.checked_at = time.time()
    n1 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    sent = last_order(fk) if n_orders(fk) > n1 else None
    check('live: closing never sells more of the hedge than the broker holds', sent is not None and sent[1] == u - 300, f'record {u}, broker {u - 300}, sent {sent}')

    # SELL ALL running: no hedge order at all; once the broker shows it gone it is booked as closed
    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    pos = await live_entry(c, fk)
    c.on_timer(int(time.time() * 1000)); u = c.hedge.pending['units']
    fill_last(fk, u, 20.0); await hsettle(c)
    fk.extra = {2: u} if BROKER == 'ibkr' else {'SQQQ': u}
    with open(c.kill_path, 'w') as f:
        json.dump({'at': 'x', 'by': 'sellall'}, f)
    n1 = n_orders(fk)
    c.hedge.checked_at = 0
    for _ in range(2):
        c.on_timer(int(time.time() * 1000))
    check('live: while SELL ALL runs, no hedge order is sent (SELL ALL closes it)', n_orders(fk) == n1 and c.hedge.units() == u)
    fk.extra = {}; fk.pos = 0
    c.hedge.checked_at = 0
    c.on_timer(int(time.time() * 1000))
    check('live: SELL ALL closed the hedge at the broker -> booked as closed, still nothing sent', c.hedge.units() == 0 and n_orders(fk) == n1,
          str(c.hedge.note))

    # Stop button: the position's exit first, then the hedge; the broker unreadable -> nothing is sent
    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    pos = await live_entry(c, fk)
    c.on_timer(int(time.time() * 1000)); u = c.hedge.pending['units']
    fill_last(fk, u, 20.0); await hsettle(c)
    fk.extra = {2: u} if BROKER == 'ibkr' else {'SQQQ': u}
    with open(c.kill_path, 'w') as f:
        json.dump({'at': 'x'}, f)
    c._sent = []
    c.on_timer(int(time.time() * 1000))
    check('live: Stop -> the position\'s sell goes out first, the hedge waits for it', c.pending is not None and c.pending['side'] == 'sell'
          and c.hedge.pending is None)
    fill_last(fk, pos.shares, 100.0); await hsettle(c); fk.pos = 0
    c.on_timer(int(time.time() * 1000))
    check('live: ...then the hedge is sold', c.hedge.pending is not None and c.hedge.pending['side'] == 'sell', str(c.hedge.note))
    fill_last(fk, u, 20.0); await hsettle(c)
    c2 = hcore('live', {}); fk2, b2 = hattach(c2); wait_s(c2, 1)
    c2.on_hedge_tick(20.0)
    await live_entry(c2, fk2)
    c2.on_timer(int(time.time() * 1000)); u2 = c2.hedge.pending['units']
    fill_last(fk2, u2, 20.0); await hsettle(c2)
    c2.portfolio.close_position(now(), 100.0, 'exit-rule', exact=True)
    fk2.connected = False
    if BROKER == 'webull':
        c2.hedge.position = lambda: None
    n1 = n_orders(fk2)
    c2.on_timer(int(time.time() * 1000))
    check('live: closing the hedge but the broker position can\'t be read -> nothing sent, tries again later',
          n_orders(fk2) == n1 and 'could not read' in (c2.hedge.note or ''), str(c2.hedge.note))

    # rejected hedge: 15-min wait for a new hedge, counts toward 3 in a row; halted still lets the hedge close
    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    pos = await live_entry(c, fk)
    c.on_timer(int(time.time() * 1000))
    reject_any(fk); await hsettle(c)
    check('live: a rejected hedge order -> no new hedge for 15 min, the rejection counts', c.hedge.units() == 0 and c.rejects == 1
          and c.hedge.cooldown_until > time.time() + 800, str(c.hedge.note))
    n1 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    check('live: ...and the next tick sends nothing (waiting)', n_orders(fk) == n1 and 'waiting 15 min' in (c.hedge.note or ''), str(c.hedge.note))
    c.rejects = 2
    c.hedge.cooldown_until = 0
    c.cooldown_until = 0
    c._sent = []                                          # (the same order again within 30 s would trip the duplicate guard)
    c.on_timer(int(time.time() * 1000))
    reject_any(fk); await hsettle(c)
    check('live: 3 rejections in a row (the last one a hedge) stop live trading', bool(c.halted) and 'hedge' in c.halted, (c.halted or '')[:100])

    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    pos = await live_entry(c, fk)
    c.on_timer(int(time.time() * 1000)); u = c.hedge.pending['units']
    fill_last(fk, u, 20.0); await hsettle(c)
    fk.extra = {2: u} if BROKER == 'ibkr' else {'SQQQ': u}
    c.halted = 'paused after 3 rejected orders'
    c.portfolio.close_position(now(), 100.0, 'exit-rule', exact=True); fk.pos = 0
    c._sent = []
    c.on_timer(int(time.time() * 1000))
    check('live: paused (halted) -> the hedge of a closed position is still sold', c.hedge.pending is not None and c.hedge.pending['side'] == 'sell',
          str(c.hedge.note))

    # speed limit: hedge orders count; buying power; an order in doubt
    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    await live_entry(c, fk)
    c._sent = [(time.time() - 5, 'buy', 1), (time.time() - 4, 'sell', 1), (time.time() - 3, 'buy', 2)]
    n1 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    check('live: the order speed limit counts hedge orders (3 a minute: no hedge, trading paused)', n_orders(fk) == n1 and c.hedge.units() == 0
          and 'speed limit' in (c.halted or ''), (c.halted or '')[:90])

    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    await live_entry(c, fk)
    c.buying_power, c.buying_power_at = 1000.0, time.time()
    n1 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    check('live: buying power check: a hedge the account can\'t pay for is not sent', n_orders(fk) == n1 and 'buying power' in (c.hedge.note or ''),
          str(c.hedge.note))

    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    await live_entry(c, fk)
    c.on_timer(int(time.time() * 1000))
    if BROKER == 'webull':
        fk.details = [{'status': 'SUBMITTED', 'filled_quantity': '0', 'filled_price': '0'}]
    t0 = time.time()
    while not c.halted and time.time() - t0 < 6:
        await asyncio.sleep(0.05)
    n1 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    check('live: a hedge order with no final answer -> live trading PAUSED, nothing more sent', bool(c.halted) and c.hedge.pending is not None
          and n_orders(fk) == n1, (c.halted or '')[:100])

    # futures: IBKR only; whole contracts on the 0.25 step; checked at full size against max_order_notional_usd
    c = hcore('live', {'symbol': 'MNQ', 'side': 'sell', 'dollar_ratio': 3.0}, max_order_notional_usd=200000); fk, b = hattach(c, fut=True); wait_s(c, 1)
    c.on_hedge_tick(20000.37)
    if BROKER == 'webull':
        check('Webull algo: a futures hedge (MNQ) is refused (IBKR only)', not c.hedge.active()[0] and 'IBKR only' in c.hedge.active()[1], c.hedge.active()[1])
    else:
        pos = await live_entry(c, fk)
        n1 = n_orders(fk)
        c.on_timer(int(time.time() * 1000))
        o = fk.trades[-1].order if n_orders(fk) > n1 else None
        want = min(round(3.0 * pos.shares * 100.0 / (20000.37 * 2)), int(200000 // (20000.37 * 2)))   # whole contracts, within the cap
        check('IBKR futures hedge: sells whole MNQ contracts, limit on the 0.25 step, tagged jb-hedge',
              o is not None and o.action == 'SELL' and int(o.totalQuantity) == want and abs(o.lmtPrice * 4 - round(o.lmtPrice * 4)) < 1e-9
              and o.orderRef == 'jb-hedge', f'{o and (o.action, o.totalQuantity, o.lmtPrice, o.orderRef)} want {want}')
        c3 = hcore('live', {'symbol': 'MNQ', 'side': 'sell', 'dollar_ratio': 3.0}); fk3, b3 = hattach(c3, fut=True); wait_s(c3, 1)
        c3.on_hedge_tick(20000.0)
        await live_entry(c3, fk3)
        n1 = n_orders(fk3)
        c3.on_timer(int(time.time() * 1000))
        check('IBKR futures hedge: one MNQ ($40,000) over risk.max_order_notional_usd ($25,000) -> not sent, says why',
              n_orders(fk3) == n1 and 'max_order_notional' in (c3.hedge.note or ''), str(c3.hedge.note))
    from datetime import datetime as _dtm
    from zoneinfo import ZoneInfo
    ny = ZoneInfo('America/New_York')
    t530 = _dtm(2026, 10, 13, 17, 30, tzinfo=ny).timestamp()
    t930 = _dtm(2026, 10, 13, 21, 30, tzinfo=ny).timestamp()
    check('CME pause: 5:30 pm ET yes, 9:30 pm ET no', _REAL_CME_PAUSED(t530) is True and _REAL_CME_PAUSED(t930) is False)
    if BROKER == 'ibkr':
        c5 = hcore('live', {'symbol': 'MNQ', 'side': 'sell', 'dollar_ratio': 3.0}, max_order_notional_usd=200000); fk5, b5 = hattach(c5, fut=True); wait_s(c5, 1)
        c5.on_hedge_tick(20000.0)
        await live_entry(c5, fk5)
        autohedge.cme_paused = lambda now=None: True
        try:
            n1 = n_orders(fk5)
            c5.on_timer(int(time.time() * 1000))
            check('IBKR futures hedge: no order during CME\'s 5-6 pm ET pause (waits, says why)', n_orders(fk5) == n1 and 'CME' in (c5.hedge.note or ''),
                  str(c5.hedge.note))
        finally:
            autohedge.cme_paused = lambda now=None: False

    # start-up: the record says a hedge is open, the broker has none -> booked as closed
    c = hcore('live', {}); fk, b = hattach(c)
    c.hedge.st.update(symbol='SQQQ', units=500, avg=20.0, mult=1.0)
    c.hedge.on_price(20.5)
    c.hedge.startup(0)
    check('live start-up: the hedge record says 500, the broker none -> booked as closed outside the algo', c.hedge.units() == 0,
          str(c.hedge.note))


asyncio.run(hedge_scenarios())


# ======================================================================= leftover hedge at start-up (hedge-2026-10-12 part 1)
async def hedge_leftovers():
    # every live hedge order is written down (jbhedge... id) before it is sent; Webull sends that id
    c = hcore('live', {}, max_order_notional_usd=100000); fk, b = hattach(c); wait_s(c, 1)
    c.on_hedge_tick(20.0)
    await live_entry(c, fk)
    c.on_timer(int(time.time() * 1000))
    rec = (c.hedge.st.get('orders') or [{}])[-1]
    saved = (json.load(open(c.hedge.state_path)).get('orders') or [{}])[-1]
    sent_id = fk.placed[-1].get('client_order_id') if BROKER == 'webull' else rec.get('id')
    check('leftover hedge: each live hedge order is written down before it is sent, id jbhedge... (Webull sends that id)',
          c.hedge.pending is not None and str(rec.get('id', '')).startswith('jbhedge') and len(rec['id']) == 32
          and not saved.get('done') and saved.get('id') == rec['id'] and sent_id == rec['id'], f"{rec.get('id')} sent {sent_id}")
    u = c.hedge.pending['units'] if c.hedge.pending else 0
    fill_last(fk, u, 20.0); await hsettle(c)
    check('leftover hedge: ...marked done with what was booked once it fills', rec.get('done') and rec.get('booked') == u, str(rec)[:120])

    # a hedge order of an earlier run filled while the algo was off: adopted as the algo's hedge, no second one
    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    c.portfolio.open_position(now(), 'long', 200, 100.0, 'entry', exact=True); fk.pos = 200
    pos = c.portfolio.position
    rec = {'id': 'jbhedge' + 'a' * 25, 'symbol': 'SQQQ', 'side': 'buy', 'units': 1000, 'px': 20.0, 'booked': 0, 'done': False,
           'mult': 1.0, 'for_entry': pos.entry_time, 'main_shares': 200, 'dollar_ratio': 1.0}
    c.hedge.st['orders'] = [rec]
    fk.extra = {2: 1000} if BROKER == 'ibkr' else {'SQQQ': 1000}
    c.hedge.startup(lookups={rec['id']: ('FILLED', 1000.0, 20.05)})
    n0 = n_orders(fk)
    c.on_hedge_tick(20.0); c._sent = []
    c.on_timer(int(time.time() * 1000))
    check('leftover hedge: an earlier run\'s hedge order that filled while the algo was off is adopted (no second hedge)',
          c.hedge.units() == 1000 and abs(c.hedge.st['avg'] - 20.05) < 1e-9 and rec['done'] and n_orders(fk) == n0
          and c.hedge.st.get('for_entry') == pos.entry_time and 'adopted' in (c.hedge.note or ''), str(c.hedge.note))

    # the account holds the hedge fund outside the record: reported, no second hedge until they are gone
    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    fk.extra = {2: 300} if BROKER == 'ibkr' else {'SQQQ': 300}
    c.hedge.startup(c.hedge.position())
    c.on_hedge_tick(20.0)
    await live_entry(c, fk)
    n0 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    st = c.hedge.status()
    check('leftover hedge: 300 SQQQ in the account beyond the record -> reported, no second hedge opened, never sold',
          n_orders(fk) == n0 and c.hedge.units() == 0 and 'already holds 300' in (st['why'] or '')
          and (st.get('outside') or {}).get('units') == 300, str(st['why'])[:120])
    fk.extra = {2: 0} if BROKER == 'ibkr' else {'SQQQ': 0}
    c.hedge.outside_checked = 0; c._sent = []
    c.on_timer(int(time.time() * 1000))
    check('leftover hedge: ...once they are sold in the broker app, Auto-Hedge hedges again',
          c.hedge.st.get('outside') is None and n_orders(fk) == n0 + 1 and last_order(fk)[0] == 'buy', str(c.hedge.note))
    await hsettle(c, 2)

    # paper: the saved paper hedge is kept after a restart, never a second one
    c = hcore('paper', {}); fk, b = hattach(c)
    c.opts = dict(c.opts, order_type='market'); c.on_hedge_tick(20.0)
    c._enter(now(), 100.0, 'regular'); c.on_timer(int(time.time() * 1000))
    u, trips = c.hedge.units(), len(c.hedge.st['trips'])
    c.save()
    c2 = ct.TraderCore(strategy(c.tmp), 'paper', logs_dir=os.path.join(c.tmp, 'logs'), starting_cash=300_000,
                       status_path=os.path.join(c.tmp, 'logs', 'live_status.json'), broker_name=BROKER,
                       options_path=os.path.join(c.tmp, 'opts.json'))
    c2.connected = True; c2.last_price = 100.0; c2.opts = dict(c2.opts, order_type='market'); c2.on_hedge_tick(20.0)
    c2.on_timer(int(time.time() * 1000))
    check('leftover hedge (paper): after a restart the saved paper hedge is kept, no second hedge',
          u > 0 and c2.portfolio.position is not None and c2.hedge.units() == u and len(c2.hedge.st['trips']) == trips
          and 'kept the paper hedge' in (c2.hedge.note or ''), str(c2.hedge.note))


async def wb_hedge_startup():
    """The Webull live trader's own start-up (connect_api) against the fake Webull trade client."""
    if BROKER != 'webull':
        return
    sys.modules.setdefault('webull', types.ModuleType('webull'))
    from algo import paper_trader as pt

    def trader(open_list=None, details=None, extra=None, settings=None, orders=None, unreadable=False):
        tmp = tempfile.mkdtemp(); logs = os.path.join(tmp, 'logs'); os.makedirs(logs)
        if settings is not None:
            json.dump(dict({'on': True, 'symbol': 'SQQQ', 'for_symbol': 'TQQQ', 'dollar_ratio': 1.0, 'side': 'buy'}, **settings),
                      open(os.path.join(tmp, 'hedge_settings.json'), 'w'))
        if orders:
            json.dump({'orders': orders}, open(os.path.join(logs, 'hedge_state_live.json'), 'w'))
        fk = FakeWB(); fk.extra = extra or {}
        if unreadable:
            def boom(account_id, page_size=None):
                raise HttpErr(500, 'down')
            fk.order_v3.get_order_open = boom
        else:
            fk.order_v3.get_order_open = lambda account_id, page_size=None: Resp({'orders': open_list or []})
        fk.details = details or []
        pt.jb_webull.make_clients = lambda *a, **k: (None, None, fk)
        args = types.SimpleNamespace(mode='live', strategy=os.path.join(tmp, 'strategies', 't.yaml'), status_path=os.path.join(logs, 'live_status.json'),
                                     starting_cash=300000, commission_per_share=0, slippage_pct=0, max_order_quantity=10000,
                                     max_order_notional_usd=None, options_path=os.path.join(tmp, 'o.json'), app_key='k', app_secret='s',
                                     region='us', environment='prod')
        return pt.WebullTrader(strategy(tmp), args), fk

    def start(t):
        try:
            t.connect_api()
            return 'started'
        except SystemExit as e:
            return str(e)

    tag = 'jbhedge' + 'b' * 25
    t, fk = trader([{'symbol': 'SQQQ', 'client_order_id': tag, 'side': 'BUY', 'quantity': '300'}])
    r = start(t)
    check('Webull start-up: an Auto-Hedge order (jbhedge... id) still open -> ./jb live refuses to start, says why',
          'NOT STARTED' in r and 'Auto-Hedge order is still open' in r and tag in r, r.strip()[:140])
    t, fk = trader([{'symbol': 'SQQQ', 'client_order_id': 'f' * 32, 'side': 'BUY', 'quantity': '300'}], settings={})
    r = start(t)
    check('Webull start-up: an open order for the Auto-Hedge fund (an older run, no tag) -> refuses to start',
          'NOT STARTED' in r and 'SQQQ' in r, r.strip()[:140])
    t, fk = trader([{'symbol': 'SQQQ', 'client_order_id': 'f' * 32, 'side': 'BUY', 'quantity': '300'}])
    check('Webull start-up: an SQQQ order of yours while Auto-Hedge is off and nothing is recorded -> starts', start(t) == 'started')
    rec = {'id': 'jbhedge' + 'c' * 25, 'symbol': 'SQQQ', 'side': 'buy', 'units': 300, 'px': 20.0, 'booked': 0, 'done': False, 'mult': 1.0}
    t, fk = trader(unreadable=True, orders=[rec], details=[{'status': 'SUBMITTED', 'filled_quantity': '0'}])
    r = start(t)
    check('Webull start-up: open-order list unreadable but the recorded hedge order is still working -> refuses',
          'NOT STARTED' in r and rec['id'] in r, r.strip()[:140])
    t, fk = trader(orders=[dict(rec, id='jbhedge' + 'd' * 25)], extra={'SQQQ': 300},
                   details=[{'status': 'FILLED', 'filled_quantity': '300', 'filled_price': '20.1'}])
    r = start(t)
    check('Webull start-up: the recorded hedge order filled while the algo was off -> starts and adopts the 300 SQQQ',
          r == 'started' and t.core.hedge.units() == 300 and abs(t.core.hedge.st['avg'] - 20.1) < 1e-9, f'{r[:80]} {t.core.hedge.note}')
    t, fk = trader(extra={'SQQQ': 300}, settings={})
    r = start(t)
    check('Webull start-up: 300 SQQQ in the account, no record, Auto-Hedge on -> starts, reports them, opens no second hedge',
          r == 'started' and t.core.hedge.units() == 0 and (t.core.hedge.st.get('outside') or {}).get('units') == 300,
          str(t.core.hedge.note)[:120])


asyncio.run(hedge_leftovers())
asyncio.run(wb_hedge_startup())


# ======================================================================= Webull at the IBKR level (hedge-2026-10-12 part 2)
def sstrategy(tmp, **risk):
    """A short-selling strategy (entry side: sell)."""
    cfg = {'name': 's', 'symbol': 'TQQQ', 'indicators': [], 'entry': {'side': 'sell', 'when': 'close > 0'},
           'exit': {'when': 'close < 0'}, 'position_size': {'pct_of_equity': 25},
           'risk': dict({'max_order_notional_usd': 25000}, **risk), 'confirm_live': True}
    return Strategy(cfg, source_path=os.path.join(tmp, 's.yaml'))


async def wb_parity():
    if BROKER != 'webull':
        return
    sys.modules.setdefault('webull', types.ModuleType('webull'))
    from algo import paper_trader as pt
    from algo import live_broker as lb

    def wtrader(mode='paper', strat=None):
        tmp = tempfile.mkdtemp(); logs = os.path.join(tmp, 'logs'); os.makedirs(logs)
        args = types.SimpleNamespace(mode=mode, strategy=os.path.join(tmp, 'strategies', 't.yaml'), status_path=os.path.join(logs, 'live_status.json'),
                                     starting_cash=300000, commission_per_share=0, slippage_pct=0, max_order_quantity=10000,
                                     max_order_notional_usd=None, options_path=os.path.join(tmp, 'o.json'), app_key='k', app_secret='s',
                                     region='us', environment='prod')
        return pt.WebullTrader((strat or strategy)(tmp), args)

    # (a) prices stopped: the IBKR wait times per session, one push per outage, reconnect each time
    t = wtrader(); pushes, disc = [], []
    t.core._push = lambda *a: pushes.append(a)
    t.client = types.SimpleNamespace(disconnect=lambda: disc.append(1), _thread_terminate=False)
    t.session_ok = True
    t.core.session_now = lambda: ('regular', True)
    t.alive_at = time.time() - 200; t._stall_check()
    check('Webull prices stopped: nothing before 5 minutes in regular hours', not disc and not pushes)
    t.alive_at = time.time() - 301; t._stall_check(); await asyncio.sleep(0.2)
    check('Webull prices silent 5 min in regular hours: reconnects, pushes the phone (Webull algo: prices stopped), notes it',
          disc == [1] and len(pushes) == 1 and pushes[0][0] == 'Webull algo: prices stopped' and 'no prices' in (t.core.last_error or ''),
          str(pushes)[:120])
    t.alive_at = time.time() - 301; t._stall_check(); await asyncio.sleep(0.2)
    check('...still silent 5 min later: reconnects again, but no second push (once per outage)', disc == [1, 1] and len(pushes) == 1)
    ms = int(time.time() * 1000)
    t._on_message(t.client, 'TQQQ-TICK', types.SimpleNamespace(price='100.5', time=str(ms), volume='10', basic=types.SimpleNamespace(symbol='TQQQ')))
    t.alive_at = time.time() - 301; t._stall_check(); await asyncio.sleep(0.2)
    check('...prices came back, then stopped again: a new outage gets its own push', len(pushes) == 2 and t.stall_pushed)
    t.core.session_now = lambda: ('premarket', True)
    n = len(disc); t.alive_at = time.time() - 600; t._stall_check()
    check('Webull prices stopped: pre-market waits 15 minutes (IBKR\'s wait time)', len(disc) == n)
    t.alive_at = time.time() - 901; t._stall_check()
    check('...and reconnects after 15 minutes', len(disc) == n + 1)
    t.overnight = False; t.core.session_now = lambda: ('overnight', True)
    n = len(disc); t.alive_at = time.time() - 4000; t._stall_check()
    check('Webull prices stopped: overnight is not checked when the plan has no overnight prices', len(disc) == n)
    t.overnight = True; t.alive_at = time.time() - 1801; t._stall_check()
    check('...with overnight prices it waits 30 minutes overnight, then reconnects', len(disc) == n + 1)
    t2 = wtrader(); pushes2, disc2 = [], []
    t2.core._push = lambda *a: pushes2.append(a)
    t2.client = types.SimpleNamespace(disconnect=lambda: disc2.append(1), _thread_terminate=False); t2.session_ok = True
    t2.core.session_now = lambda: ('afterhours', False)
    t2.alive_at = time.time() - 901; t2._stall_check(); await asyncio.sleep(0.2)
    check('Webull prices stopped in a session the algo does not trade (and nothing held): reconnects quietly, no push',
          disc2 == [1] and not pushes2)

    # (b) connection lost: paho's on_disconnect (Webull's streaming client is an MQTT client) = IBKR's 1100
    t = wtrader('live'); t.client = types.SimpleNamespace()
    t.core.connected = True; t.core._guardian_tick()
    t._on_disconnect(t.client, None, 0)
    check('Webull disconnect we asked for (code 0): no safe mode', t.core.connected)
    t._on_disconnect(t.client, None, 7); t.core._guardian_tick()
    check('Webull connection lost (code 7): safe mode on, orders held', not t.core.connected and bool(getattr(t.core, 'safe_mode', None))
          and 'connection lost' in (t.core.last_error or ''), str(getattr(t.core, 'safe_mode', '')))
    t.core.connected = True; t.core.last_error = None; t.core._guardian_tick()
    t._on_message(t.client, 'notice', {'type': '1002', 'content': 'quotes permission taken by another device'})
    check('Webull notice 1002 (another session took over the market data): safe mode', not t.core.connected
          and 'another session' in (t.core.last_error or ''), t.core.last_error or '')
    ms = int(time.time() * 1000)
    t._on_message(t.client, 'TQQQ-TICK', types.SimpleNamespace(price='100.5', time=str(ms), volume='10', basic=types.SimpleNamespace(symbol='TQQQ')))
    check('...prices come back: connected again (safe mode ends after a minute and a position check, as before)',
          t.core.connected and not t.core.last_error)

    # (c) short sales: SHORT for an entry while flat, SELL to close a long, BUY to cover
    sp = {'side': 'sell', 'shares': 10, 'type': 'limit', 'limit': 99.0, 'session': 'regular'}
    check('Webull short sale: an opening sell goes out as SHORT; a closing sell as SELL; a cover as BUY',
          lb.build_order(sp, 'TQQQ', 'x')['side'] == 'SHORT' and lb.build_order(dict(sp, closing=True), 'TQQQ', 'x')['side'] == 'SELL'
          and lb.build_order(dict(sp, side='buy', closing=True), 'TQQQ', 'x')['side'] == 'BUY')
    wb = FakeWB(); b = lb.LiveBroker(wb, account_id='X1')
    try:
        b.place_order(strategy(tempfile.mkdtemp()), dict(sp, ref_price=100.0), 'TQQQ', *(lambda *a: None,) * 4, cancel_s=1, poll_s=0.05)
        ok = False
    except guards.GuardRejected:
        ok = True
    check('Webull: a short sale from a strategy that buys is refused (never sent)', ok and not wb.placed)
    tmp = tempfile.mkdtemp()
    c = ct.TraderCore(sstrategy(tmp), 'live', logs_dir=tmp, starting_cash=300_000, status_path=os.path.join(tmp, 'live_status.json'),
                      broker_name='webull', options_path=os.path.join(tmp, 'opts.json'))
    c.connected, c.last_price = True, 100.0; c.no_short_sessions = set(pt.NO_SHORT_SESSIONS)
    fk, b = attach(c); wait_s(c, 1)
    c._enter(now(), 100.0, 'overnight')
    check('Webull short strategy overnight: no short sale sent (Webull takes none then), not counted as an order',
          c.pending is None and not fk.placed and c.last_order['status'] == 'skipped' and 'short' in c.last_order['detail']
          and not getattr(c, '_sent', []), str(c.last_order))
    c.buying_power, c.buying_power_at = 1000.0, time.time()
    c._enter(now(), 100.0, 'regular')
    check('Webull short sale: the buying power check applies, as for buys', c.pending is None and c.last_order['status'] == 'skipped'
          and 'buying power' in c.last_order['detail'])
    c.buying_power = None
    c._enter(now(), 100.0, 'regular')
    check('Webull short strategy in regular hours: a live SHORT order (capped by max_order_notional_usd)',
          c.pending is not None and fk.placed and fk.placed[-1]['side'] == 'SHORT' and int(fk.placed[-1]['quantity']) * 100 <= 25000,
          str(fk.placed[-1] if fk.placed else None)[:120])
    fk.details = [{'status': 'FILLED', 'filled_quantity': fk.placed[-1]['quantity'], 'filled_price': '100'}]
    await settle(c)
    fk.pos = -int(fk.placed[-1]['quantity'])
    check('...the fill is booked as a short position', c.portfolio.position and c.portfolio.position.side == 'short')
    c._sent = []
    c._exit(now(), 99.0, 'exit-rule', 'regular')
    check('...and the exit is a BUY (cover), never another short sale', fk.placed[-1]['side'] == 'BUY', str(fk.placed[-1])[:100])
    fk.details = [{'status': 'FILLED', 'filled_quantity': fk.placed[-1]['quantity'], 'filled_price': '99'}]
    await settle(c)

    def short_start(kind, preview_err=None, price=100.0):
        t = wtrader('live', sstrategy)
        fk = FakeWB()
        fk.account_v2.get_account_list = lambda: Resp([{'account_id': 'X1', 'account_type': kind}])
        fk.order_v3.get_order_open = lambda account_id, page_size=None: Resp({'orders': []})
        asked = []

        def preview(account_id, new_orders):
            asked.append(new_orders[0])
            if preview_err:
                raise preview_err
            return Resp({'ok': True})
        fk.order_v3.preview_order = preview
        pt.jb_webull.make_clients = lambda *a, **k: (None, None, fk)
        real = pt.jb_webull.fetch_bars
        pt.jb_webull.fetch_bars = lambda *a, **k: [{'close': price}]
        try:
            t.connect_api()
            return 'started', asked, fk
        except SystemExit as e:
            return str(e), asked, fk
        finally:
            pt.jb_webull.fetch_bars = real
    r, asked, fk = short_start('CASH')
    check('Webull short strategy, cash account: live refuses to start, says a margin account is needed',
          'NOT STARTED' in r and 'not a margin account' in r and not asked, r.strip()[:120])
    r, asked, fk = short_start('MARGIN', HttpErr(400, 'SHORT_SELL_NOT_ALLOWED'))
    check('Webull short strategy, margin account, Webull\'s preview says no: refuses to start with Webull\'s reason',
          'NOT STARTED' in r and 'SHORT_SELL_NOT_ALLOWED' in r and asked and asked[0]['side'] == 'SHORT' and not fk.placed, r.strip()[:120])
    r, asked, fk = short_start('MARGIN')
    check('Webull short strategy, margin account, preview accepted: live starts (a preview is never an order)',
          r == 'started' and asked and asked[0]['quantity'] == '1' and not fk.placed, r[:100])
    os.environ['WEBULL_ACCOUNT_ID'] = 'Z9'
    try:
        r, asked, fk = short_start('MARGIN')
    finally:
        os.environ.pop('WEBULL_ACCOUNT_ID', None)
    check('Webull: WEBULL_ACCOUNT_ID that is not one of the key\'s accounts -> live refuses to start (as at IBKR)',
          'NOT STARTED' in r and 'WEBULL_ACCOUNT_ID=Z9' in r, r.strip()[:120])

    # backtests skip short entries overnight too
    from algo.backtest import run_backtest
    from algo import options as opt
    idx = pd.date_range('2026-10-08 00:30', periods=90, freq='1min', tz='UTC')      # 8:30 pm ET Wednesday: overnight
    bars = pd.DataFrame({'open': 100.0, 'high': 100.5, 'low': 99.5, 'close': 100.0, 'volume': 1000.0}, index=idx)
    o = opt.validate({'paper_sessions': ['overnight'], 'order_type': 'limit'})
    r1 = run_backtest(sstrategy(tempfile.mkdtemp()), bars, options=o)
    r2 = run_backtest(sstrategy(tempfile.mkdtemp()), bars, options=o, no_short_sessions=('overnight',))
    n1 = sum(1 for x in r1['portfolio'].trades if x.reason == 'entry')
    n2 = sum(1 for x in r2['portfolio'].trades if x.reason == 'entry')
    check('Webull backtest: a short strategy makes no short entry overnight (as paper and live)', n1 > 0 and n2 == 0, f'{n1} -> {n2}')


asyncio.run(wb_parity())


# ======================================================================= Webull overnight ready (hedge-2026-10-12 part 4)
async def hedge_overnight():
    """A hedge order the broker refuses overnight (e.g. a fund not in Webull's night session): no more hedge orders until
    the overnight session is over, instead of piling up rejections (3 in a row would stop live trading)."""
    c = hcore('live', {}); fk, b = hattach(c); wait_s(c, 1)
    if BROKER == 'ibkr':
        c.hedge.place = lambda spec, f, fa, u, un: b.place_order(autohedge._As(c.strategy, spec['symbol']), spec, types.SimpleNamespace(
            conId=2, exchange='OVERNIGHT' if spec['session'] == 'overnight' else 'SMART', secType='STK', symbol='SQQQ'), f, fa, u, un, cancel_s=1)
    c.on_hedge_tick(20.0)
    await live_entry(c, fk)
    c.session_now = lambda: ('overnight', True)
    c._sent = []
    n0 = n_orders(fk)
    c.on_timer(int(time.time() * 1000))
    sent = n_orders(fk) == n0 + 1
    if BROKER == 'webull':
        sent = sent and fk.placed[-1]['support_trading_session'] == 'NIGHT'
    check('Auto-Hedge overnight: the hedge order goes to the broker\'s overnight session when overnight is allowed', sent and c.hedge.pending)
    reject_any(fk); await hsettle(c)
    c.hedge.cooldown_until = c.cooldown_until = 0; c._sent = []
    n1 = n_orders(fk)
    for _ in range(3):
        c.on_timer(int(time.time() * 1000))
    check('Auto-Hedge overnight refused: no more hedge orders this overnight session (one rejection, not three), says why',
          n_orders(fk) == n1 and c.rejects == 1 and not c.halted and '4 am' in (c.hedge.note or ''), str(c.hedge.note))
    c.session_now = lambda: ('premarket', True)
    c.on_timer(int(time.time() * 1000))
    check('...from 4 am the hedge is tried again', n_orders(fk) == n1 + 1, str(c.hedge.note))
    await hsettle(c, 2)


asyncio.run(hedge_overnight())


bad = [r for r in RESULTS if not r[1]]
print(f'\n{BROKER} (all, with round 3, Auto-Hedge and the hedge-2026-10-12 parts): {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')
sys.exit(1 if bad else 0)
