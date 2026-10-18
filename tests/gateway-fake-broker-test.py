"""Fake-broker tests for the gateway's money paths: SELL ALL (and its automatic follow-up), the order
ticket, the board's safety stops and Flip, the daily loss limit, and session times (overnight,
holidays, 1 pm early closes). Nothing here talks to IBKR, Webull, Yahoo or ntfy: every broker call
goes to the fakes below, and pushes are only recorded.

usage (from the josephs-board folder):   python3 tests/gateway-fake-broker-test.py
Prints PASS / FAIL per check and exits with 1 if any check failed.
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import types
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = tempfile.mkdtemp(prefix='jb-gw-test-')
os.environ['JB_DATA_DIR'] = DATA
os.environ['JB_NTFY_TOPIC'] = ''
os.environ['WEBULL_APP_KEY'] = os.environ['WEBULL_APP_SECRET'] = ''
os.environ.pop('JB_TICKET_MAX', None)
sys.path.insert(0, os.path.join(ROOT, 'gateway'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))

import watch          # noqa: E402
import killswitch as ks   # noqa: E402
import ticket         # noqa: E402
import orders         # noqa: E402
import guardian       # noqa: E402
import trading        # noqa: E402
import algoctl        # noqa: E402

RESULTS = []
PUSHES = []
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  -- {detail}' if detail else ''), flush=True)


# ---------------------------------------------------------------- isolation: temp algo folders, no pushes, no waiting
ALGO_DIRS = {n: os.path.join(DATA, n + '-algo') for n in ('ibkr', 'webull')}
for d in ALGO_DIRS.values():
    os.makedirs(os.path.join(d, 'logs'), exist_ok=True)
algoctl.DIRS.update(ALGO_DIRS)
guardian.ALGOS.update(ALGO_DIRS)
trading.ALGOS.update(ALGO_DIRS)
watch.push_later = lambda title, text, tags='': PUSHES.append((title, text))
watch.push = lambda title, text, tags='': PUSHES.append((title, text)) or ''
_fast = types.SimpleNamespace(time=time.time, sleep=lambda s: None, monotonic=time.monotonic)
ks.time = _fast
orders.time = _fast
ticket.time = _fast
ks.WB_GAP_S = 0
ks.WAIT_S = 4                                  # SELL ALL: 4 s per try instead of 20
orders._wait_fill.__defaults__ = (0.3,)        # safety stops: 0.3 s per step instead of 20
ticket._yahoo_last = lambda sym: None          # no internet: tests set it when they need a "Yahoo" price


def set_price(sym, p):
    now = int(time.time() * 1000)
    with watch._lock:
        watch._px[sym] = [[now - 60000, p], [now, p]]


def clear_files():
    for f in ('ticket_log.json', 'orders.json', 'orders_log.json', 'kill_log.json', 'journal.json',
              'trading_state.json', 'trading_settings.json'):
        try:
            os.remove(os.path.join(DATA, f))
        except FileNotFoundError:
            pass
    for d in ALGO_DIRS.values():
        for f in ('KILL', 'LIVE_RUNNING', 'LIVE_EXITED', 'heartbeat.json', 'live_status.json'):
            try:
                os.remove(os.path.join(d, 'logs', f))
            except FileNotFoundError:
                pass
    with watch._lock:
        watch._px.clear()
    ticket._last_refresh[0] = 0
    PUSHES.clear()


# ================================================================ fake IB Gateway (ib_async-shaped)
class Ev(list):
    def __iadd__(self, f):
        self.append(f)
        return self


class FTrade:
    def __init__(self, gw, contract, order):
        self.gw, self.contract, self.order = gw, contract, order
        self.orderStatus = types.SimpleNamespace(status='Submitted', filled=0.0, avgFillPrice=0.0)
        self.log = []

    def isDone(self):
        return self.orderStatus.status in ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')


class FakeGW:
    """The broker side: positions, open orders, executions. `fill` decides what an order does when placed:
    a function(trade) -> (filled shares now, final status or None to stay working)."""
    def __init__(self, positions=None):
        self.pos = dict(positions or {})            # symbol -> signed shares
        self.trades = []
        self.execs = []
        self.fill = lambda tr: (0, None)            # by default: nothing fills, order stays working
        self.cancel_ok = True
        self.pos_lag = 0                           # position reads that still show the old count after a fill
        self._stale = None
        self.connect_error = None
        self.next_id = 1
        self.max_open_sells = {}                   # symbol -> most shares ever offered for sale at once

    def contract(self, sym):
        return types.SimpleNamespace(symbol=sym, localSymbol=sym, secType='STK', conId=abs(hash(sym)) % 10 ** 6,
                                     exchange='SMART', primaryExchange='NASDAQ')

    def apply(self, tr, n, avg):
        if n <= 0:
            return
        sym = tr.contract.symbol
        before = self.pos.get(sym, 0.0)
        sign = -1 if tr.order.action == 'SELL' else 1
        self.pos[sym] = before + sign * n
        if self.pos_lag:
            self._stale = (sym, before, self.pos_lag)
        tr.orderStatus.filled += n
        tr.orderStatus.avgFillPrice = avg
        self.execs.append(types.SimpleNamespace(
            contract=tr.contract, execution=types.SimpleNamespace(
                orderRef=getattr(tr.order, 'orderRef', ''), shares=n, price=avg, side='SLD' if sign < 0 else 'BOT',
                execId=f'e{len(self.execs)}', time=datetime.now().astimezone())))

    def read_pos(self, sym):
        if self._stale and self._stale[0] == sym and self._stale[2] > 0:
            s, before, k = self._stale
            self._stale = (s, before, k - 1)
            return before
        return self.pos.get(sym, 0.0)

    def open_sells(self, sym):
        return sum(t.order.totalQuantity - t.orderStatus.filled for t in self.trades
                   if not t.isDone() and t.contract.symbol == sym and t.order.action == 'SELL')


class FakeIB:
    """One API connection to FakeGW (what ib_async.IB looks like to the gateway's code)."""
    def __init__(self, gw):
        self.gw = gw
        self.errorEvent = Ev()

    def managedAccounts(self):
        return ['U123']

    def disconnect(self):
        pass

    def isConnected(self):
        return True

    async def reqPositionsAsync(self):
        return []                              # (the gateway reads the list itself with positions())

    def positions(self, account=''):
        out = []
        for s in list(self.gw.pos):
            q = self.gw.read_pos(s)            # one read per symbol (a lagging read is used up by one call)
            if q:
                out.append(types.SimpleNamespace(contract=self.gw.contract(s), position=q, account='U123'))
        return out

    async def reqAllOpenOrdersAsync(self):
        return [t for t in self.gw.trades if not t.isDone()]

    async def reqExecutionsAsync(self):
        return list(self.gw.execs)

    def reqGlobalCancel(self):
        for t in self.gw.trades:
            if not t.isDone():
                self.cancelOrder(t.order)

    async def qualifyContractsAsync(self, *cs):
        out = []
        for c in cs:
            sym = getattr(c, 'symbol', '') or next((t.contract.symbol for t in self.gw.trades
                                                    if t.contract.conId == getattr(c, 'conId', None)), '')
            if not sym:
                sym = next((s for s in self.gw.pos if self.gw.contract(s).conId == getattr(c, 'conId', None)), '')
            out.append(self.gw.contract(sym) if sym else None)
        return out

    def placeOrder(self, contract, order):
        order.orderId = self.gw.next_id
        self.gw.next_id += 1
        tr = FTrade(self.gw, contract, order)
        self.gw.trades.append(tr)
        n, final = self.gw.fill(tr)
        price = getattr(order, 'lmtPrice', 0) or 0
        if n:
            self.gw.apply(tr, n, price or 1.0)
        if final:
            tr.orderStatus.status = final
        sym = contract.symbol
        self.gw.max_open_sells[sym] = max(self.gw.max_open_sells.get(sym, 0), self.gw.open_sells(sym))
        return tr

    def cancelOrder(self, order):
        tr = next((t for t in self.gw.trades if t.order is order), None)
        if tr and not tr.isDone() and self.gw.cancel_ok:
            tr.orderStatus.status = 'Cancelled'

    def reqMarketDataType(self, n):
        pass

    async def reqTickersAsync(self, *cs):
        raise RuntimeError('no market data in the fake')

    def portfolio(self, account=''):
        return []

    def openTrades(self):
        return [t for t in self.gw.trades if not t.isDone()]


def use_ib(gw):
    async def connect(*a, **k):
        if gw.connect_error:
            raise RuntimeError(gw.connect_error)
        return FakeIB(gw), 4001
    ks._ib_connect = connect
    ticket._ib_connect = connect

    def positions(broker):
        assert broker == 'ibkr'
        return [(s, q) for s, q in gw.pos.items() if q]
    guardian._positions = positions


# ================================================================ fake Webull trade API
class WbErr(Exception):
    def __init__(self, status, code):
        super().__init__(f'HTTP Status: {status}, Code: {code}')
        self.http_status, self.error_code = status, code


class Resp:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


class FakeWebull:
    def __init__(self, positions=None):
        self.pos = dict(positions or {})
        self.orders = {}                 # coid -> dict(symbol, side, qty, filled, status)
        self.place_errors = []           # exceptions raised by the next place calls (order NOT created)
        self.detail_errors = []          # exceptions raised by the next lookups
        self.fill = lambda o: (0, 'SUBMITTED')
        outer = self

        class Acct:
            def get_account_list(self):
                return Resp([{'account_id': 'W1', 'account_type': 'MARGIN'}])

            def get_account_position(self, account):
                return Resp({'holdings': [{'symbol': s, 'quantity': str(q), 'last_price': '50'} for s, q in outer.pos.items() if q]})

        class Ord:
            def place_order(self, account_id, new_orders):
                o = new_orders[0]
                if outer.place_errors:
                    raise outer.place_errors.pop(0)
                rec = {'symbol': o['symbol'], 'side': o['side'], 'qty': float(o['quantity']), 'filled': 0.0,
                       'status': 'SUBMITTED', 'coid': o['client_order_id'], 'raw': o}
                outer.orders[o['client_order_id']] = rec
                n, st = outer.fill(rec)
                outer._fill(rec, n)
                rec['status'] = st or rec['status']
                return Resp({'ok': True})

            def get_order_detail(self, account_id, client_order_id):
                if outer.detail_errors:
                    raise outer.detail_errors.pop(0)
                r = outer.orders.get(client_order_id)
                if r is None:
                    raise WbErr(404, 'ORDER_NOT_FOUND')
                return Resp({'orders': [{'symbol': r['symbol'], 'status': r['status'], 'filled_quantity': str(r['filled']),
                                         'filled_price': '50', 'client_order_id': client_order_id}]})

            def cancel_order(self, account_id, client_order_id):
                r = outer.orders.get(client_order_id)
                if r and r['status'] not in ('FILLED', 'CANCELLED', 'FAILED'):
                    r['status'] = 'CANCELLED'

            def get_order_open(self, account_id, page_size=100):
                return Resp({'orders': [{'symbol': r['symbol'], 'side': r['side'], 'quantity': str(r['qty']),
                                         'status': r['status'], 'client_order_id': c}
                                        for c, r in outer.orders.items() if r['status'] in ('SUBMITTED', 'PARTIAL_FILLED')]})

        self.account_v2, self.order_v3 = Acct(), Ord()

    def _fill(self, rec, n):
        if n <= 0:
            return
        sign = -1 if rec['side'] == 'SELL' else 1
        self.pos[rec['symbol']] = self.pos.get(rec['symbol'], 0) + sign * n
        rec['filled'] += n


def use_webull(wb):
    ks._wb_client = lambda: wb


def run_sellall(broker):
    code, _ = ks.post(broker, {'confirm': 'SELL ALL'})
    t0 = time.time()
    while time.time() - t0 < 30:
        r = ks._runs.get(broker)
        if r and r.get('ended'):
            return r
        time.sleep(0.05)
    raise RuntimeError('SELL ALL did not finish')


# ================================================================ tests
@test
def t_sessions():
    et = watch.ET
    import nyse_hours as nh
    sys.path.insert(0, os.path.join(ROOT, 'ibkr-algo'))
    from algo.runstate import session_at
    cases = [
        (datetime(2026, 10, 12, 21, 0, tzinfo=et), 'overnight'),      # Monday 9 pm
        (datetime(2026, 10, 13, 3, 49, tzinfo=et), 'overnight'),
        (datetime(2026, 10, 13, 3, 55, tzinfo=et), None),             # 3:50-4:00 gap
        (datetime(2026, 10, 16, 20, 30, tzinfo=et), None),            # Friday night
        (datetime(2026, 10, 18, 20, 30, tzinfo=et), 'overnight'),     # Sunday night
        (datetime(2026, 11, 26, 10, 0, tzinfo=et), None),             # Thanksgiving
        (datetime(2026, 11, 25, 21, 0, tzinfo=et), None),             # the night before Thanksgiving
        (datetime(2026, 11, 27, 12, 59, tzinfo=et), 'regular'),       # early close day
        (datetime(2026, 11, 27, 13, 30, tzinfo=et), 'afterhours'),
        (datetime(2026, 11, 27, 17, 30, tzinfo=et), None),
        (datetime(2026, 12, 24, 13, 5, tzinfo=et), 'afterhours'),     # Christmas Eve (Thursday)
        (datetime(2026, 12, 24, 20, 30, tzinfo=et), None),            # Christmas is a holiday
        (datetime(2027, 6, 18, 10, 0, tzinfo=et), None),              # Juneteenth 2027 observed Friday
        (datetime(2027, 12, 31, 10, 0, tzinfo=et), 'regular'),        # New Year's Day 2028 is a Saturday: not made up
        (datetime(2026, 7, 3, 10, 0, tzinfo=et), None),               # July 4th 2026 observed Friday
    ]
    bad = [(d.isoformat(), want, ks.session_now(d), session_at(d)) for d, want in cases
           if ks.session_now(d) != want or session_at(d) != want]
    check('sessions: overnight, 3:50 gap, weekends, holidays and 1 pm early closes (board and algos agree)', not bad, str(bad))
    lc = watch.last_close_ms(int(datetime(2026, 11, 30, 7, 0, tzinfo=et).timestamp() * 1000))
    check('last close: Monday 7 am after Thanksgiving week -> Friday 1 pm (early close)',
          watch.et(lc) == datetime(2026, 11, 27, 13, 0, tzinfo=et), str(watch.et(lc)))
    lc = watch.last_close_ms(int(datetime(2026, 11, 27, 9, 0, tzinfo=et).timestamp() * 1000))
    check('last close: the morning after Thanksgiving -> Wednesday 4 pm', watch.et(lc) == datetime(2026, 11, 25, 16, 0, tzinfo=et), str(watch.et(lc)))
    check('Webull stall watch hours: none on a holiday, until 5 pm on an early close',
          not nh.stocks_trade(datetime(2026, 11, 26, 10, 0, tzinfo=et)) and nh.stocks_trade(datetime(2026, 11, 27, 16, 59, tzinfo=et))
          and not nh.stocks_trade(datetime(2026, 11, 27, 17, 0, tzinfo=et)))
    d = datetime(2026, 10, 13, 19, 59, tzinfo=et)                  # a Tuesday
    check('trading day: 7:59 pm counts toward today, 8:00 pm toward the next trading day',
          trading.trading_day(d.timestamp() * 1000) == '2026-10-13' and
          trading.trading_day((d + timedelta(minutes=1)).timestamp() * 1000) == '2026-10-14')
    fri = datetime(2026, 11, 27, 21, 0, tzinfo=et)                 # day after Thanksgiving, 9 pm
    check('trading day: Friday 9 pm counts toward Monday', trading.trading_day(fri.timestamp() * 1000) == '2026-11-30')
    wed = datetime(2026, 11, 25, 20, 30, tzinfo=et)                # night before Thanksgiving
    check('trading day: the evening before Thanksgiving counts toward Friday', trading.trading_day(wed.timestamp() * 1000) == '2026-11-27')


@test
def t_sellall_ibkr():
    clear_files()
    ks.session_now = lambda d=None: 'overnight'
    set_price('TQQQ', 50.0)
    gw = FakeGW({'TQQQ': 100})
    use_ib(gw)
    tries = []

    def fill(tr):                          # try 1: 40 fill then the rest is cancelled; try 2: the rest fills
        tries.append(tr.order.totalQuantity)
        return (40, None) if len(tries) == 1 else (tr.order.totalQuantity, 'Filled')
    gw.fill = fill
    r = run_sellall('ibkr')
    check('SELL ALL IBKR overnight, part fill then cancel: sells exactly what is left, never more',
          gw.pos['TQQQ'] == 0 and tries == [100, 60], f'orders {tries}, position now {gw.pos["TQQQ"]}')
    check('SELL ALL IBKR: uses the OVERNIGHT venue with limit orders overnight',
          all(t.contract.exchange == 'OVERNIGHT' and t.order.orderType == 'LMT' for t in gw.trades), r['result'])
    k = json.load(open(os.path.join(ALGO_DIRS['ibkr'], 'logs', 'KILL')))
    check('SELL ALL: algo stays paused afterwards, without the SELL ALL mark', 'by' not in k, str(k))
    clear_files()
    ks.session_now = lambda d=None: 'regular'
    gw = FakeGW({'TQQQ': 100, 'SOXL': -20})
    use_ib(gw)
    gw.fill = lambda tr: (tr.order.totalQuantity, 'Filled')
    r = run_sellall('ibkr')
    check('SELL ALL IBKR regular hours: market orders; sells the long, buys back the short',
          gw.pos == {'TQQQ': 0, 'SOXL': 0} and all(t.order.orderType == 'MKT' for t in gw.trades), r['result'])
    clear_files()
    ks.session_now = lambda d=None: None
    gw = FakeGW({'TQQQ': 100})
    use_ib(gw)
    r = run_sellall('ibkr')
    check('SELL ALL with markets closed (holiday / weekend): sends no order, says so', gw.trades == [] and 'markets closed' in r['result'],
          r['result'])


@test
def t_sellall_webull():
    clear_files()
    ks.session_now = lambda d=None: 'afterhours'
    wb = FakeWebull({'SOXL': 100})
    use_webull(wb)
    wb.place_errors = [WbErr(504, 'GATEWAY_TIMEOUT')]
    wb.detail_errors = [ConnectionError('network down')]
    wb.fill = lambda o: (o['qty'], 'FILLED')
    r = run_sellall('webull')
    check('SELL ALL Webull: send timed out and the lookup failed too: stops (never risks a second sell)',
          wb.orders == {} and wb.pos['SOXL'] == 100 and 'NOT closed' in r['result'], r['result'])

    clear_files()
    wb = FakeWebull({'SOXL': 100})
    use_webull(wb)
    st = {'n': 0}

    def part(o):
        st['n'] += 1
        return (30, 'SUBMITTED') if st['n'] == 1 else (o['qty'], 'FILLED')
    wb.fill = part
    run_sellall('webull')
    qtys = [o['qty'] for o in wb.orders.values()]
    check('SELL ALL Webull: part filled then cancelled: next try sells only the rest', qtys == [100, 70] and wb.pos['SOXL'] == 0,
          f'orders {qtys}, left {wb.pos["SOXL"]}')
    check('SELL ALL Webull after-hours: extended-hours limit orders', all(o['raw']['support_trading_session'] == 'ALL'
          and o['raw']['order_type'] == 'LIMIT' for o in wb.orders.values()))
    clear_files()
    ks.session_now = lambda d=None: 'overnight'
    wb = FakeWebull({'SOXL': 100})
    use_webull(wb)
    wb.fill = lambda o: (o['qty'], 'FILLED')
    run_sellall('webull')
    check('SELL ALL Webull overnight: NIGHT session limit order', [o['raw']['support_trading_session'] for o in wb.orders.values()] == ['NIGHT']
          and wb.pos['SOXL'] == 0)


@test
def t_sellall_followup_new_symbol():
    # something bought AFTER the press is never sold by the follow-up (review fix #8)
    clear_files()
    ks.session_now = lambda d=None: 'overnight'
    gw = FakeGW({'TQQQ': 100})
    use_ib(gw)
    guardian._st['sell'] = {}
    r = run_sellall('ibkr')
    gw.pos['SOXL'] = 50                   # bought after the press
    r['ended'] -= 61000
    pressed = []
    real_post = ks.post
    ks.post = lambda broker, data: pressed.append(broker) or (200, '{}')
    try:
        guardian._step_sellall(guardian.settings())
    finally:
        ks.post = real_post
    check('SELL ALL follow-up: a symbol bought after the press is not sold automatically',
          pressed == [] and any('not retried' in p[0] for p in PUSHES), str([p[0] for p in PUSHES][-2:]))


@test
def t_ticket_open():
    check('ticket: an unknown status counts as open (no second sell stacked on it)', 'Partial_Filled' in ticket.OPEN and 'Weird' in ticket.OPEN
          and 'PendingCancel' in ticket.OPEN and 'Cancelled' not in ticket.OPEN and 'Filled' not in ticket.OPEN)


@test
def t_ticket_max():
    clear_files()
    ks.session_now = lambda d=None: 'regular'
    set_price('TQQQ', 50.0)
    gw = FakeGW({'TQQQ': 200})          # $10,000 of TQQQ, JB_TICKET_MAX is $5,000
    use_ib(gw)
    gw.fill = lambda tr: (tr.order.totalQuantity, 'Filled')
    sold, avg, msg = orders.sell_now('ibkr', 'TQQQ', 200, 'stop')
    check('safety stop on $10,000 (over JB_TICKET_MAX): sells it all', sold == 200 and gw.pos['TQQQ'] == 0, msg)
    code, res = ticket.check({'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'buy', 'qty': 200, 'limit': 50.0})
    check('ticket: a BUY over JB_TICKET_MAX is still refused', code == 400 and 'JB_TICKET_MAX' in res.get('error', ''), str(res)[:100])
    code, res = ticket.check({'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'sell', 'qty': 10, 'limit': 50.0})
    check('ticket: selling more than you hold is refused (never opens a short)', code == 400 and 'short' in res.get('error', ''), str(res)[:100])


def _journal(fills):
    watch._write(trading.JOURNAL, {'fills': {f['id']: f for f in fills}, 'notes': {}})


@test
def t_loss_limit():
    clear_files()
    now = int(time.time() * 1000)
    trading._offboard.clear()
    ticket._yq.clear()
    _journal([trading._fill('webull:s1', 'webull', 'SOXL', 'buy', 100, 30.0, now - 3600 * 1000)])
    ticket._yahoo_last = lambda sym: {'SOXL': 24.0}.get(sym)
    with trading._lock:
        j = trading._journal()
    trips, open_ = trading.round_trips(list(j['fills'].values()))
    s = trading.summary(trips, open_, j['notes'])
    check('loss limit: a stock on no board is priced from Yahoo and counts (review fix #11)', s['unrealized'] == -600.0, str(s['unrealized']))
    watch._write(trading.SETTINGS, {'loss_on': True, 'loss_limit': 500, 'pause_algos': True})
    PUSHES.clear()
    trading.check_limits()
    check('loss limit: down $600 vs a $500 limit -> push and both algos paused',
          any(p[0] == 'Daily loss limit hit' for p in PUSHES) and all(os.path.exists(os.path.join(d, 'logs', 'KILL')) for d in ALGO_DIRS.values()),
          str(PUSHES[-1:])[:120])
    PUSHES.clear()
    trading.check_limits()
    check('loss limit: pushed once per trading day, not every minute', not PUSHES)
    # a position carried in from an earlier day counts only today's move (since 8 pm ET last evening)
    clear_files()
    today = date.fromisoformat(trading.trading_day(now))
    start = watch.at_et(today - timedelta(days=1), 20, 0)
    _journal([trading._fill('ibkr:x', 'ibkr', 'TQQQ', 'buy', 100, 40.0, start - 5 * 86400 * 1000)])
    with watch._lock:
        watch._px['TQQQ'] = [[start - 3600 * 1000, 52.0], [start + 60000, 51.0], [now, 47.0]]
    with trading._lock:
        j = trading._journal()
    trips, open_ = trading.round_trips(list(j['fills'].values()))
    s = trading.summary(trips, open_, j['notes'])
    check('loss limit: a position held since last week counts only today\'s move (52 at 8 pm -> 47 now = -$500)',
          s['unrealized_today'] == -500.0 and s['unrealized'] == 700.0, f"today {s['unrealized_today']}, open {s['unrealized']}")


@test
def t_live_trader_window():
    """Review fix #4: the guardian pushes when the live trader's window is gone."""
    clear_files()
    hb = {}
    lock = os.path.join(ALGO_DIRS['ibkr'], 'logs', 'LIVE_RUNNING')
    open(lock, 'w').write('999999')                      # a PID that is not running
    os.environ['RUN_IBKR_ALGO'] = 'off'
    guardian._step_heartbeats(guardian.settings(), {'hb': hb})
    check('live trader: window gone (LIVE_RUNNING left, PID dead) -> loud push, even with RUN_IBKR_ALGO=off',
          any('NOT running' in p[0] for p in PUSHES), str(PUSHES[-1:])[:120])
    os.remove(lock)
    os.environ.pop('RUN_IBKR_ALGO')


@test
def t_live_trader_crash():
    """./jb live: the trader crashed but its Terminal window stays open, so jb removed LIVE_RUNNING itself."""
    clear_files()
    os.environ['RUN_IBKR_ALGO'] = 'off'
    ex = os.path.join(ALGO_DIRS['ibkr'], 'logs', 'LIVE_EXITED')
    open(ex, 'w').write(json.dumps({'rc': 1, 'at': int(time.time())}))
    guardian._step_heartbeats(guardian.settings(), {'hb': {}})
    check('live trader: crashed with an error while its window stayed open -> loud push',
          any('LIVE trader' in p[0] and 'stopped' in p[0] for p in PUSHES), str(PUSHES[-1:])[:160])
    PUSHES.clear()
    guardian._step_heartbeats(guardian.settings(), {'hb': {}})
    check('live trader: that push is sent once', not PUSHES, str(PUSHES)[:120])
    os.environ.pop('RUN_IBKR_ALGO')
    # the jb script writes LIVE_EXITED only for a crash, not for Ctrl+C (exit 0) -- checked by running its cmd_live
    import subprocess
    jbdir = tempfile.mkdtemp(prefix='jb-live-')
    for sub in ('lib', 'bin', 'ibkr-algo/algo', 'ibkr-algo/logs', '.venv/bin'):
        os.makedirs(os.path.join(jbdir, sub), exist_ok=True)
    import shutil
    shutil.copy(os.path.join(ROOT, 'jb'), jbdir)
    shutil.copy(os.path.join(ROOT, 'lib', 'settings.sh'), os.path.join(jbdir, 'lib'))
    for rc in (1, 0):
        fake_py = os.path.join(jbdir, '.venv', 'bin', 'python')
        with open(fake_py, 'w') as f:      # a stand-in for Python: the "trader" exits with this code
            f.write(f'#!/bin/bash\ncase "$*" in *paper_trader*) exit {rc} ;; *) exit 0 ;; esac\n')
        os.chmod(fake_py, 0o755)
        flag = os.path.join(jbdir, 'ibkr-algo', 'logs', 'LIVE_EXITED')
        if os.path.exists(flag):
            os.remove(flag)
        env = dict(os.environ, HOME=jbdir, PATH='/usr/bin:/bin')
        p = subprocess.run(['bash', os.path.join(jbdir, 'jb'), 'live', 'ibkr', 'strategies/x.yaml'], input='I_UNDERSTAND_THE_RISK\n',
                           capture_output=True, text=True, env=env, timeout=60)
        check(f'./jb live: trader exit code {rc} -> crash note for the board {"written" if rc else "not written"}',
              os.path.exists(flag) == bool(rc) and not os.path.exists(os.path.join(jbdir, 'ibkr-algo', 'logs', 'LIVE_RUNNING')),
              (p.stdout + p.stderr)[-200:])


@test
def t_guard_restart():
    """Board safety stop: the gateway restarts while the stop's sell order is still working (thin overnight
    market). After the restart the stop watches again; it must not send a second sell for the same shares."""
    clear_files()
    ks.session_now = lambda d=None: 'overnight'
    set_price('TQQQ', 49.0)
    gw = FakeGW({'TQQQ': 100})
    use_ib(gw)
    gw.fill = lambda tr: (0, None)        # overnight: the limit sell rests unfilled
    g = {'id': 'g1', 'broker': 'ibkr', 'sym': 'TQQQ', 'qty': 100, 'stop': 50.0, 'target': None, 'trail': None, 'be': None,
         'be_done': False, 'entry': 55.0, 'high': 55.0, 'after': '', 'status': 'firing', 'created': int(time.time() * 1000),
         'hits': 2, 'note': ''}
    orders._change(lambda v: v['guards'].append(g))
    ok, t1 = ticket.place({'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'sell', 'qty': 100,
                           'limit': ks.limit_price('SELL', 49.0, 0.5), 'tif': 'day'}, 'guard')    # the sell sent before the restart
    check('setup: first stop sell is working at the broker', ok and gw.open_sells('TQQQ') == 100, str(t1)[:120])
    orders._st['firing'].clear()
    orders._unstick_guards()                                # gateway start-up (review fix #10)
    st = [x['status'] for x in orders._load()['guards']]
    check('restart: a stop left "firing" watches again', st == ['on'], str(st))
    fired = []
    real_thread = orders.threading.Thread
    orders.threading.Thread = lambda target, args, **k: types.SimpleNamespace(start=lambda: fired.append(args))
    try:
        for k in range(3):
            set_price('TQQQ', 49.0 - 0.01 * k)
            time.sleep(0.002)
            orders._step_guards(int(time.time() * 1000))
    finally:
        orders.threading.Thread = real_thread
    for a in fired:
        orders._fire(*a)
    most = gw.max_open_sells.get('TQQQ', 0)
    check('restart: the stop never has more shares for sale at the broker than you hold',
          fired and most <= 100, f'fired {len(fired)}; at most {most:g} shares offered for sale at once, 100 held')
    # the earlier sell can't be cancelled (no answer): nothing new is sent
    clear_files()
    set_price('TQQQ', 49.0)
    gw = FakeGW({'TQQQ': 100})
    use_ib(gw)
    ok, t1 = ticket.place({'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'sell', 'qty': 100,
                           'limit': ks.limit_price('SELL', 49.0, 0.5), 'tif': 'day'}, 'guard')
    gw.cancel_ok = False
    sold, avg, msg = orders.sell_now('ibkr', 'TQQQ', 100, 'stop')
    check('stop fires while an earlier sell for it is open and can\'t be cancelled: no second sell, says why',
          len(gw.trades) == 1 and sold == 0 and 'could not be cancelled' in msg, msg)
    # Webull: a ticket left at "Sending" by a restart, which Webull never received, does not hold back the stop
    clear_files()
    ks.session_now = lambda d=None: 'overnight'
    set_price('SOXL', 30.0)
    wb = FakeWebull({'SOXL': 100})
    use_webull(wb)
    wb.fill = lambda o: (o['qty'], 'FILLED')
    now = int(time.time() * 1000)
    watch._write(ticket.LOG, [{'id': 'lost1', 'broker': 'webull', 'sym': 'SOXL', 'side': 'sell', 'qty': 100, 'limit': 29.8,
                               'at': now - 300000, 'status': 'Sending', 'filled': 0, 'source': 'guard'}])
    sold, avg, msg = orders.sell_now('webull', 'SOXL', 100, 'stop')
    check('Webull: a sell left at "Sending" that never reached Webull does not block the stop (it sells)',
          sold == 100 and wb.pos['SOXL'] == 0, msg)
    t = ticket.ticket_by_id('lost1')
    check('...and that ticket now reads ended', t and t['status'] not in ticket.OPEN, str(t and t['status']))


@test
def t_sellall_followup_failed_press():
    clear_files()
    ks.session_now = lambda d=None: 'overnight'
    gw = FakeGW({'TQQQ': 100})
    use_ib(gw)
    gw.connect_error = 'IB Gateway not reachable (timeout)'
    guardian._st['sell'] = {}
    r = run_sellall('ibkr')
    gw.connect_error = None
    r['ended'] -= 61000
    pressed = []
    real_post = ks.post
    ks.post = lambda broker, data: pressed.append(broker) or (200, '{}')
    try:
        guardian._step_sellall(guardian.settings())
    finally:
        ks.post = real_post
    check('SELL ALL follow-up: a press that failed before it could read positions is pressed again',
          pressed == ['ibkr'], f'pressed {pressed}; pushes: {[p[0] for p in PUSHES][-2:]}')
    # a press that found nothing to sell: anything held later was bought after it, and is left alone
    clear_files()
    gw = FakeGW({})
    use_ib(gw)
    guardian._st['sell'] = {}
    r = run_sellall('ibkr')
    gw.pos['TQQQ'] = 10
    r['ended'] -= 61000
    pressed = []
    ks.post = lambda broker, data: pressed.append(broker) or (200, '{}')
    try:
        guardian._step_sellall(guardian.settings())
    finally:
        ks.post = real_post
    check('SELL ALL follow-up: after a press that found nothing to sell, a later buy is not sold', pressed == [])


@test
def t_loss_limit_futures():
    clear_files()
    now = int(time.time() * 1000)
    trading._offboard.clear()
    ticket._yq.clear()
    # IBKR crude-oil future bought yesterday, not on any board (multiplier 1000)
    _journal([trading._fill('ibkr:cl', 'ibkr', 'CL=F', 'buy', 1, 70.0, now - 26 * 3600 * 1000, 1000)])
    asked = []
    ticket._yahoo_last = lambda sym: asked.append(sym) or {'CLF': 10.0}.get(sym)   # Yahoo's CLF is Cleveland-Cliffs stock
    with trading._lock:
        j = trading._journal()
    trips, open_ = trading.round_trips(list(j['fills'].values()))
    s = trading.summary(trips, open_, j['notes'])
    check('loss limit: a future on no board is never priced from an unrelated Yahoo stock (CL=F is not CLF)',
          open_[0]['unrealized'] is None and s['unrealized'] == 0 and 'CLF' not in asked,
          f"CL=F priced at {open_[0]['last']}, open P&L {s['unrealized']}, asked Yahoo for {asked}")
    watch._write(trading.SETTINGS, {'loss_on': True, 'loss_limit': 500, 'pause_algos': True})
    PUSHES.clear()
    trading.check_limits()
    check('loss limit: no false "limit hit" push or algo pause from that wrong price',
          not any('limit' in p[0].lower() for p in PUSHES) and not os.path.exists(os.path.join(ALGO_DIRS['ibkr'], 'logs', 'KILL')),
          str(PUSHES[-1:])[:150])


@test
def t_flip_cap():
    clear_files()
    ks.session_now = lambda d=None: 'regular'
    set_price('TQQQ', 50.0)
    set_price('SQQQ', 20.0)
    gw = FakeGW({'TQQQ': 200})          # $10,000 of TQQQ, JB_TICKET_MAX is $5,000
    use_ib(gw)
    gw.fill = lambda tr: (tr.order.totalQuantity, 'Filled')
    orders.twin = lambda s: {'TQQQ': 'SQQQ', 'SQQQ': 'TQQQ'}.get(s)
    code, res = orders.flip_check({'broker': 'ibkr', 'sym': 'TQQQ'})
    if code == 200:
        orders._flip_run(orders._st['flips'].pop(res['code']))
    check('Flip over JB_TICKET_MAX: refused before anything is sold (never left half-flipped)',
          code == 400 and gw.pos.get('TQQQ') == 200 and 'JB_TICKET_MAX' in res.get('error', ''),
          f"{code} {res.get('error') or res.get('text')} | holds {gw.pos}")
    os.environ['JB_TICKET_MAX'] = '20000'
    try:
        code, res = orders.flip_check({'broker': 'ibkr', 'sym': 'TQQQ'})
        if code == 200:
            orders._flip_run(orders._st['flips'].pop(res['code']))
    finally:
        os.environ.pop('JB_TICKET_MAX')
    check('Flip under JB_TICKET_MAX: sells TQQQ and buys SQQQ', gw.pos.get('TQQQ') == 0 and gw.pos.get('SQQQ', 0) > 0, str(gw.pos))


@test
def t_ticket_stuck_status():
    clear_files()
    gw = FakeGW({'TQQQ': 0})
    use_ib(gw)
    now = int(time.time() * 1000)
    stuck = [{'id': 'aaa', 'broker': 'ibkr', 'sym': 'TQQQ', 'side': 'buy', 'qty': 100, 'limit': 50.0, 'at': now - 60000,
              'status': 'PendingCancel', 'filled': 0, 'source': 'ticket'}]
    stuck.append(dict(stuck[0], id='bbb', status='ApiPending'))
    stuck.append(dict(stuck[0], id='ccc', status='Sending'))          # the board restarted while sending it
    watch._write(ticket.LOG, stuck)
    ticket.refresh()
    left = [t['status'] for t in ticket.open_tickets()]
    check('ticket: IBKR orders left at PendingCancel / ApiPending / Sending that IBKR no longer lists are not "open" for 7 days',
          left == [], f'still open: {left}')
    # ...but one IBKR still lists stays open
    clear_files()
    gw = FakeGW({'TQQQ': 0})
    use_ib(gw)
    ib = FakeIB(gw)
    from ib_async import LimitOrder
    o = LimitOrder('BUY', 100, 50.0)
    o.orderRef = 'jb-ticket-ddd'
    ib.placeOrder(gw.contract('TQQQ'), o).orderStatus.status = 'PendingCancel'
    watch._write(ticket.LOG, [dict(stuck[0], id='ddd')])
    ticket.refresh()
    check('ticket: a PendingCancel order IBKR still lists stays open', [t['status'] for t in ticket.open_tickets()] == ['PendingCancel'])


@test
def t_sellall_webull_refused():
    clear_files()
    ks.session_now = lambda d=None: 'afterhours'
    set_price('SOXL', 30.0)
    wb = FakeWebull({'SOXL': 100})
    use_webull(wb)
    wb.place_errors = [WbErr(400, 'ORDER_PRICE_OUT_OF_RANGE')]       # try 1 refused outright (never created)
    wb.fill = lambda o: (o['qty'], 'FILLED')
    r = run_sellall('webull')
    sent = [o['raw']['limit_price'] for o in wb.orders.values()]
    check('SELL ALL Webull: a try Webull refuses outright (400 + "no such order") is followed by the next try',
          wb.pos['SOXL'] == 0 and len(sent) == 1, f"{r['result']} | orders created: {sent}")


@test
def t_sellall_ibkr_position_lag():
    # the broker's position list can lag a fill by a moment: the next try must still never sell more than is left
    clear_files()
    ks.session_now = lambda d=None: 'overnight'
    set_price('TQQQ', 50.0)
    gw = FakeGW({'TQQQ': 100})
    use_ib(gw)
    tries = []

    def fill2(tr):
        tries.append(tr.order.totalQuantity)
        return (40, None) if len(tries) == 1 else (0, None)
    gw.fill = fill2
    gw.pos_lag = 1
    run_sellall('ibkr')
    check('SELL ALL IBKR: a position read that lags a part fill does not make the next try oversell',
          tries[:1] == [100] and all(q <= 60 for q in tries[1:]), f'orders {tries}')


@test
def t_webull_bridge_overnight_stall():
    """The Webull bridge feeds the boards (and the board's own safety stops). Its stream can go quiet without an
    error; overnight that was never noticed."""
    sys.path.insert(0, os.path.join(ROOT, 'bridges'))
    import webull_bridge as wbb
    et = watch.ET
    check('Webull overnight session: weeknights 8 pm - 4 am before a trading day only',
          wbb.overnight_now(datetime(2026, 10, 12, 22, 0, tzinfo=et).timestamp()) and
          wbb.overnight_now(datetime(2026, 10, 13, 3, 59, tzinfo=et).timestamp()) and
          not wbb.overnight_now(datetime(2026, 10, 16, 22, 0, tzinfo=et).timestamp()) and          # Friday night
          not wbb.overnight_now(datetime(2026, 11, 25, 22, 0, tzinfo=et).timestamp()))             # night before Thanksgiving

    def run(overnight_plan, quiet_s):
        b = wbb.WebullBridge.__new__(wbb.WebullBridge)
        b.mqtt_up, b.subscribed, b.stream_client = True, {'TQQQ': 'US_STOCK'}, object()
        b.server = types.SimpleNamespace(last_tick_ms=(time.time() - quiet_s) * 1000)
        b.connected_at = time.time() - 3600
        b.overnight = overnight_plan
        restarted = []
        b._restart_stream = lambda c: restarted.append(c)
        calls = []

        async def one_pass(s):
            calls.append(s)
            if len(calls) > 1:
                raise asyncio.CancelledError
        real = (wbb.asyncio.sleep, wbb.webull_streams_now, wbb.overnight_now)
        wbb.asyncio.sleep, wbb.webull_streams_now, wbb.overnight_now = one_pass, (lambda: False), (lambda: True)
        try:
            asyncio.run(b.stall_watch())
        except asyncio.CancelledError:
            pass
        finally:
            wbb.asyncio.sleep, wbb.webull_streams_now, wbb.overnight_now = real
        return bool(restarted)
    check('Webull bridge: no prices at all for 16 min overnight (plan with overnight prices) -> reconnects', run(True, 960))
    check('Webull bridge: 10 quiet minutes overnight is not yet a stall', not run(True, 600))
    check('Webull bridge: plan without overnight prices -> nothing to wait for overnight', not run(False, 5000))


@test
def t_login_and_origin():
    """Review fix #13 refuses Origin "null"; the login form must still work (browsers send "null" there)."""
    import socket
    import subprocess
    import urllib.error
    import urllib.request
    s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, JB_PASSWORD='testpw', JB_DATA_DIR=tempfile.mkdtemp(prefix='jb-gw-login-'), PYTHONPATH=os.path.join(ROOT, 'lib'),
               IBKR_GATEWAY_PORT='1', WEBULL_APP_KEY='', JB_NTFY_TOPIC='')
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, 'gateway', 'gateway.py'), '--port', str(port), '--bind', '127.0.0.1'],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect)

    def post(path, body, origin, ctype='application/x-www-form-urlencoded', cookie=''):
        h = {'Content-Type': ctype}
        if origin:
            h['Origin'] = origin
        if cookie:
            h['Cookie'] = cookie
        try:
            r = op.open(urllib.request.Request(base + path, data=body.encode(), headers=h, method='POST'), timeout=10)
            return r.status, r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.headers
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base + '/login', timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        code, h = post('/login', 'password=testpw&next=/', 'null')       # what Chrome / Safari send from the login page
        cookie = (h.get('Set-Cookie') or '').split(';')[0]
        check('login form: a browser\'s form POST (Origin "null") with the right password logs in',
              code == 303 and cookie.startswith('jb_auth='), f'{code}')
        code, _ = post('/login', 'password=wrong&next=/', 'null')
        check('login form: a wrong password is still a wrong password', code == 200)
        code, _ = post('/api/kill/ibkr', '{"confirm": "SELL ALL"}', 'null', 'application/json', cookie)
        check('fix #13 kept: SELL ALL from a frame with Origin "null" is refused even with the login cookie', code == 403, str(code))
        code, _ = post('/api/kill/ibkr', '{"confirm": "SELL ALL"}', 'https://evil.example', 'application/json', cookie)
        check('another website\'s request is refused', code == 403, str(code))
    finally:
        p.terminate()
        p.wait(10)


@test
def t_guard_with_ibkr_safety_stop():
    """The protection check offers a real stop at IBKR ("Place at IBKR", good until cancelled, 4 am - 8 pm) next to
    the board's own safety stop, which covers overnight. If the board's stop sells overnight, the IBKR stop must
    not be left to sell the same shares again at 4 am."""
    import iblink
    from ib_async import Order
    clear_files()
    ks.session_now = lambda d=None: 'overnight'
    set_price('TQQQ', 49.0)
    gw = FakeGW({'TQQQ': 100})
    use_ib(gw)
    gw.fill = lambda tr: (0, None) if tr.order.orderType == 'STP LMT' else (tr.order.totalQuantity, 'Filled')
    link = FakeIB(gw)
    real = (iblink.connected, iblink.run)
    iblink.connected = lambda: True
    iblink.run = lambda fn, timeout=20: ks._in_loop(fn(link))
    try:
        link.placeOrder(gw.contract('TQQQ'), Order(action='SELL', orderType='STP LMT', totalQuantity=100, auxPrice=50.0, lmtPrice=49.5,
                                                   tif='GTC', outsideRth=True, orderRef='jb-safety-stop'))
        sold, avg, msg = orders.sell_now('ibkr', 'TQQQ', 100, 'stop')
    finally:
        iblink.connected, iblink.run = real
    check('board stop sells overnight while a stop it placed at IBKR is open: that IBKR stop is cancelled first',
          sold == 100 and gw.pos['TQQQ'] == 0 and gw.open_sells('TQQQ') == 0 and gw.max_open_sells['TQQQ'] <= 100,
          f"{msg} | sell orders left open: {gw.open_sells('TQQQ'):g} shares, held {gw.pos['TQQQ']:g}")


# ================================================================ Auto-Hedge (jb-hedge)
@test
def t_sellall_closes_hedges_ibkr():
    """SELL ALL also closes the Auto-Hedge: the inverse fund and an MNQ future, with the future's limit on its 0.25 step."""
    clear_files()
    ks.session_now = lambda d=None: 'afterhours'
    set_price('TQQQ', 50.0)
    set_price('SQQQ', 20.0)

    class GW(FakeGW):
        def contract(self, sym):
            c = FakeGW.contract(self, sym)
            if sym == 'MNQ':
                c.secType, c.localSymbol, c.exchange, c.multiplier = 'FUT', 'MNQZ6', 'CME', '2'
            return c
    gw = GW({'TQQQ': 100, 'SQQQ': 250, 'MNQ': -2})
    use_ib(gw)
    gw.fill = lambda tr: (tr.order.totalQuantity, 'Filled')
    old = FakeIB.portfolio
    FakeIB.portfolio = lambda self, account='': [types.SimpleNamespace(contract=self.gw.contract('MNQ'), marketPrice=24750.37)]
    try:
        r = run_sellall('ibkr')
    finally:
        FakeIB.portfolio = old
    fut = [t for t in gw.trades if t.contract.symbol == 'MNQ']
    check('SELL ALL IBKR closes the position, the SQQQ hedge and the MNQ futures hedge', gw.pos == {'TQQQ': 0, 'SQQQ': 0, 'MNQ': 0},
          f"{gw.pos} -- {r['result']}")
    check('SELL ALL: the MNQ limit sits on the 0.25 price step (IBKR refuses any other price), buys back the short',
          fut and fut[0].order.action == 'BUY' and abs(fut[0].order.lmtPrice * 4 - round(fut[0].order.lmtPrice * 4)) < 1e-9,
          str([(t.order.action, t.order.lmtPrice) for t in fut]))
    check('futures price step: sells round down, buys round up (stays marketable)', ks._fut_tick('MNQ', 'SELL', 24502.37) == 24502.25
          and ks._fut_tick('MES', 'BUY', 6700.01) == 6700.25 and ks._fut_tick('ZZ', 'BUY', 1.234) == 1.234)


@test
def t_sellall_closes_hedges_webull():
    clear_files()
    ks.session_now = lambda d=None: 'premarket'
    set_price('TQQQ', 50.0)
    set_price('SQQQ', 20.0)
    wb = FakeWebull({'TQQQ': 100, 'SQQQ': 300})
    wb.fill = lambda o: (o['qty'], 'FILLED')
    use_webull(wb)
    r = run_sellall('webull')
    check('SELL ALL Webull closes the position and the SQQQ hedge', wb.pos == {'TQQQ': 0, 'SQQQ': 0}, f"{wb.pos} -- {r['result']}")


@test
def t_autohedge_settings():
    import hedge
    clear_files()
    d = ALGO_DIRS['ibkr']
    for f in ('hedge_settings.json',):
        try:
            os.remove(os.path.join(d, f))
        except FileNotFoundError:
            pass
    code, g = hedge.algo_get({'name': ['ibkr']})
    check('Auto-Hedge: OFF by default (no settings saved)', code == 200 and not g['settings'].get('on') and g['defaults']['on'] is False, str(g['settings']))
    code, r = hedge.algo_post({'name': 'ibkr', 'on': True})
    check('Auto-Hedge: refused before the algo has started (the board does not know its symbol)', code == 409, r.get('error'))
    for n in ALGO_DIRS:
        watch._write(os.path.join(ALGO_DIRS[n], 'logs', 'live_status.json'), {'symbol': 'TQQQ', 'mode': 'paper'})
    code, r = hedge.algo_post({'name': 'ibkr', 'on': True, 'ratio_pct': 100, 'instrument': 'auto'})
    cfg = json.load(open(os.path.join(d, 'hedge_settings.json')))
    check('Auto-Hedge on (one click): auto picks the inverse twin SQQQ, $1 per $1 at 100%, live hedging stays off',
          code == 200 and cfg['on'] and cfg['symbol'] == 'SQQQ' and cfg['for_symbol'] == 'TQQQ' and abs(cfg['dollar_ratio'] - 1.0) < 0.01
          and cfg['side'] == 'buy' and not cfg.get('live_on'), json.dumps(cfg)[:200])
    code, r = hedge.algo_post({'name': 'ibkr', 'on': True, 'ratio_pct': 50, 'instrument': 'MNQ'})
    cfg = json.load(open(os.path.join(d, 'hedge_settings.json')))
    check('IBKR algo: MNQ hedge at 50% -> sell, $ per point 2, half the size', code == 200 and cfg['symbol'] == 'MNQ' and cfg['side'] == 'sell'
          and cfg['mult'] == 2.0 and abs(cfg['dollar_ratio'] - 1.5) < 0.01, json.dumps(cfg)[:200])
    code, r = hedge.algo_post({'name': 'webull', 'on': True, 'instrument': 'MNQ'})
    check('Webull algo: a futures hedge is refused (IBKR only)', code == 400 and 'IBKR Only' in r['error'], r.get('error'))
    code, r = hedge.algo_post({'name': 'ibkr', 'on': True, 'ratio_pct': 300})
    check('hedge ratio over 150% refused', code == 400, r.get('error'))
    code, r = hedge.algo_live({'name': 'ibkr', 'on': True, 'confirm': 'yes'})
    check('live hedging: refused without typing HEDGE LIVE', code == 400 and 'HEDGE LIVE' in r['error'], r.get('error'))
    code, r = hedge.algo_live({'name': 'ibkr', 'on': True, 'confirm': 'HEDGE LIVE'})
    check('live hedging: refused while ./jb live is not running', code == 409 and './jb live' in r['error'], r.get('error'))
    lr = os.path.join(d, 'logs', 'LIVE_RUNNING')
    with open(lr, 'w') as f:
        f.write(str(os.getpid()))
    code, r = hedge.algo_live({'name': 'ibkr', 'on': True, 'confirm': 'HEDGE LIVE'})
    cfg = json.load(open(os.path.join(d, 'hedge_settings.json')))
    check('live hedging: on with the words while ./jb live runs, tied to that run (LIVE_RUNNING pid and time)',
          code == 200 and cfg['live_on'] and cfg['live_lock']['pid'] == str(os.getpid()) and r['live_ok'], json.dumps(cfg.get('live_lock')))
    code, r = hedge.algo_post({'name': 'ibkr', 'on': True, 'instrument': 'SQQQ'})
    cfg = json.load(open(os.path.join(d, 'hedge_settings.json')))
    check('choosing another hedge turns live hedging off again (it was confirmed for MNQ)', code == 200 and not cfg['live_on'], str(cfg.get('live_on')))
    hedge.algo_live({'name': 'ibkr', 'on': True, 'confirm': 'HEDGE LIVE'})
    code, r = hedge.algo_post({'name': 'ibkr', 'on': False})
    cfg = json.load(open(os.path.join(d, 'hedge_settings.json')))
    check('Auto-Hedge off turns live hedging off too', code == 200 and not cfg['on'] and not cfg['live_on'])
    code, r = hedge.algo_live({'name': 'ibkr', 'on': True, 'confirm': 'HEDGE LIVE'})
    check('live hedging: refused while Auto-Hedge itself is off (paper first)', code == 409, r.get('error'))
    os.remove(lr)
    import plus
    code, body, _ = plus.handle('GET', '/hedge/algo', {'name': ['webull']}, None)
    check('GET /api/plus/hedge/algo?name=webull answers through the Plus doorway', code == 200 and json.loads(body)['name'] == 'webull', str(code))


@test
def t_loss_limit_counts_hedge():
    """The algo's daily loss limit counts the Auto-Hedge's result too (realized today + open)."""
    clear_files()
    now = int(time.time() * 1000)
    exit_t = datetime.fromtimestamp(now / 1000).astimezone().isoformat()
    st = {'symbol': 'TQQQ', 'strategy_name': 't', 'unrealized_pnl': -300.0,
          'round_trips': [{'exit_time': exit_t, 'pnl': -400.0}],
          'hedge': {'realized_today': -200.0, 'unrealized': -150.0}}
    watch._write(os.path.join(ALGO_DIRS['ibkr'], 'logs', 'live_status.json'), st)
    a = trading._algo_today('ibkr')
    check('algo today = position + hedge: -400 closed, -300 open, hedge -200 closed and -150 open = -1,050',
          a['total'] == -1050.0 and a['hedge'] == -350.0, str(a))
    watch._write(trading.SETTINGS, {'loss_on': False, 'algo_on': True, 'algo_limit': 1000})
    trading.check_limits()
    check('algo loss limit $1,000: the hedge\'s loss tips it over -> algo paused (its KILL file), so the hedge closes too',
          os.path.exists(os.path.join(ALGO_DIRS['ibkr'], 'logs', 'KILL')), str(PUSHES[-1:])[:120])


if __name__ == '__main__':
    only = sys.argv[1:]
    for t in TESTS:
        if only and t.__name__ not in only:
            continue
        try:
            t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(t.__name__ + ' ran without crashing', False, repr(e))
    bad = [r for r in RESULTS if not r[1]]
    print(f'\ngateway fake-broker tests: {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')
    sys.exit(1 if bad else 0)
