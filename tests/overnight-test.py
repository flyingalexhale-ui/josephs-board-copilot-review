"""Webull overnight ready (hedge-2026-10-12 part 4), with the fake Webull SDK (tests/fake-brokers/pkgs): nothing talks to
Webull. What happens the moment the Webull plan gets the overnight session, and until then:

  * lib/jb_webull.py: when the plan is checked again (every 3 hours, and at 7:45 pm New York before a trading day);
    the shared record of what was found (data/webull_overnight.json, shown on the algo tab and the ? Legend); Webull
    history keeps pre-market and after-hours when the plan refuses the overnight session
  * the Webull board bridge, the scanner feed and the Webull algo: refused -> overnight off; checked again later ->
    still refused: stays off; taken: overnight on again by itself (the board's notice goes, the stream reconnects, the
    algo trades overnight again when its trading options allow) -- no restart
  * the Webull backup stop overnight: Webull's night session if it takes it, a day stop if not (not asked again for a
    week), switched back at 3:50 am -- never two stops at once
  * the Webull copy of the overnight gap strategy backtests on Webull data (with and without overnight bars)
  * the gateway follows the plan: the health check expects Webull prices overnight only when the plan has them, and
    Hedge Ideas may go to the Webull algo overnight then

usage (from the josephs-board folder):   python3 tests/overnight-test.py        (exit code 1 if a check fails)
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import types
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = tempfile.mkdtemp(prefix='jb-overnight-test-')
os.environ.update(JB_DATA_DIR=DATA, JB_NTFY_TOPIC='', JB_FAKE_BROKERS=os.path.join(ROOT, 'tests', 'fake-brokers'),
                  JB_FAKE_LOGDIR=DATA)
for p in (os.path.join(ROOT, 'tests', 'fake-brokers', 'pkgs'), os.path.join(ROOT, 'lib'), os.path.join(ROOT, 'bridges'),
          os.path.join(ROOT, 'gateway'), os.path.join(ROOT, 'webull-algo'), os.path.join(ROOT, 'mover-scanner')):
    sys.path.insert(0, p)
import jb_webull  # noqa: E402
from webull.core.exception.exceptions import ServerException  # noqa: E402

RESULTS = []
NY = ZoneInfo('America/New_York')


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  -- {detail}' if detail else ''), flush=True)


def ny(*a):
    return datetime(*a, tzinfo=NY).timestamp()


REFUSED = ServerException('MARKET_DATA_NOT_SUBSCRIBED', 'please subscribe to US_STOCK OVERNIGHT', 403)


class FakeStream:
    """A streaming client: subscribe() takes or refuses the overnight session; disconnect() is recorded."""
    def __init__(self, refuse=False):
        self.refuse, self.subs, self.disc, self._thread_terminate = refuse, [], 0, False

    def subscribe(self, symbols, category, sub_types, depth=None, overnight_required=None):
        self.subs.append((list(symbols), overnight_required))
        if overnight_required and self.refuse:
            raise REFUSED

    def disconnect(self):
        self.disc += 1


# ---------------------------------------------------------------- lib/jb_webull.py
due = jb_webull.overnight_recheck_due
t_mon_2pm = ny(2026, 10, 12, 14, 0)
check('re-check: not again 1 hour after the last check', not due(t_mon_2pm - 3600, t_mon_2pm))
check('re-check: again after 3 hours', due(t_mon_2pm - 3 * 3600 - 1, t_mon_2pm))
check('re-check: again at 7:45 pm New York before a trading day (a plan bought today works tonight)',
      due(ny(2026, 10, 12, 19, 0), ny(2026, 10, 12, 19, 46)) and not due(ny(2026, 10, 12, 19, 0), ny(2026, 10, 12, 19, 40)))
check('re-check: no 7:45 pm check on a Friday (no overnight session before Saturday)',
      not due(ny(2026, 10, 16, 19, 0), ny(2026, 10, 16, 19, 46)))
jb_webull.note_overnight('board', False, 'no plan')
st = jb_webull.overnight_state()
check('shared record: "off" when the board found no overnight session', st['on'] is False and st['parts']['board']['on'] is False)
time.sleep(0.01)
jb_webull.note_overnight('algo', True, 'on')
check('shared record: the newest answer from Webull wins ("on" once a later check streams overnight prices)',
      jb_webull.overnight_state()['on'] is True and jb_webull.overnight_state()['parts']['board']['on'] is False)
os.remove(jb_webull.overnight_path())

asked = []


class FakeMD:
    def get_history_bar(self, symbol, category, timespan, count='200', trading_sessions=None, **k):
        asked.append(trading_sessions)
        if trading_sessions and 'OVN' in trading_sessions:
            raise REFUSED
        r = types.SimpleNamespace(json=lambda: [{'symbol': symbol, 'result': [{'time': str(int(ny(2026, 10, 12, 17, 0) * 1000)), 'open': 1,
                                                                                 'high': 1, 'low': 1, 'close': 1, 'volume': 1}]}])
        return r


rows = jb_webull.fetch_bars(types.SimpleNamespace(market_data=FakeMD()), 'AAPL', 'M1', 10)
check('Webull history, plan without overnight: keeps pre-market and after-hours (asks again without OVN only)',
      rows and asked[0] == ['PRE', 'RTH', 'ATH', 'OVN'] and asked[1] == ['PRE', 'RTH', 'ATH'], str(asked))


# ---------------------------------------------------------------- the Webull board bridge
async def bridge_tests():
    import webull_bridge as wbb
    args = types.SimpleNamespace(data_dir=DATA, token='', app_key='k', app_secret='s', region='us', environment='prod',
                                 session_id='t', roll_check_s=3600)
    br = wbb.WebullBridge(args)
    br.server.loop = asyncio.get_running_loop()
    br.calls.clear()
    c = FakeStream(refuse=True)
    br.stream_client, br.mqtt_up = c, True
    br.watchlist.symbols = ['AAPL', 'NVDA']
    await br.sync()                       # refused with overnight -> subscribes again without it
    st = br.server._last_status.get(wbb.OVERNIGHT_NOTICE_KEY) or {}
    check('board: plan without overnight -> streams without it, shows the notice, records "off"',
          not br.overnight and 'No overnight prices' in (st.get('msg') or '') and jb_webull.overnight_state()['parts']['board']['on'] is False
          and set(br.subscribed) == {'AAPL', 'NVDA'})
    check('board: it no longer says "until the next restart" -- it checks again', 'Checked again every 3 hours' in st.get('msg', ''))
    r = await br.overnight_check_once()
    check('board: not checked again within 3 hours (one subscription call every few hours at most)', r is None and len(c.subs) == 2)
    r = await br.overnight_check_once(force=True)
    check('board: checked again, still refused -> stays off', r == 'off' and not br.overnight and c.disc == 0)
    c.refuse = False
    br.overnight_checked = time.time() - 3 * 3600 - 5
    r = await br.overnight_check_once()
    st = br.server._last_status.get(wbb.OVERNIGHT_NOTICE_KEY) or {}
    check('board: the plan has overnight now -> on by itself: notice cleared, stream reconnects (every symbol with overnight), recorded "on"',
          r == 'on' and br.overnight and st.get('msg') == '' and c.disc == 1 and jb_webull.overnight_state()['parts']['board']['on'] is True)
    c2 = FakeStream()
    br.stream_client, br.mqtt_up, br.subscribed = c2, True, {}
    await br.sync()
    check('board: after the reconnect every stock is subscribed with the overnight session', c2.subs and all(o for _, o in c2.subs), str(c2.subs))

asyncio.run(bridge_tests())


# ---------------------------------------------------------------- the scanner feed
from scanner.feeds.webull_feed import WebullFeed  # noqa: E402
f = WebullFeed(['AAPL', 'NVDA'], lambda *a: None, 'k', 's')
f.overnight = False
f.client = c = FakeStream(refuse=True)
f._overnight_probe()
check('scanner: checked again, still refused -> stays off', not f.overnight and c.disc == 0)
c.refuse = False
f._overnight_probe()
check('scanner: the plan has overnight now -> on by itself, reconnects for overnight prices', f.overnight and c.disc == 1
      and jb_webull.overnight_state()['parts']['scanner']['on'] is True)


# ---------------------------------------------------------------- the Webull algo
from algo import paper_trader as pt  # noqa: E402
from algo import core_trader as ct  # noqa: E402
from algo.strategy import Strategy  # noqa: E402


def wtrader(mode='paper', **risk):
    tmp = tempfile.mkdtemp(); logs = os.path.join(tmp, 'logs'); os.makedirs(logs)
    cfg = {'name': 't', 'symbol': 'TQQQ', 'indicators': [], 'entry': {'side': 'buy', 'when': 'close > 0'}, 'exit': {'when': 'close < 0'},
           'position_size': {'pct_of_equity': 25}, 'risk': dict({'max_order_notional_usd': 25000}, **risk), 'confirm_live': True,
           'trading': {'paper_sessions': ['overnight'], 'live_sessions': ['overnight']}}
    args = types.SimpleNamespace(mode=mode, strategy=os.path.join(tmp, 'strategies', 't.yaml'), status_path=os.path.join(logs, 'live_status.json'),
                                 starting_cash=300000, commission_per_share=0, slippage_pct=0, max_order_quantity=10000,
                                 max_order_notional_usd=None, options_path=os.path.join(tmp, 'o.json'), app_key='k', app_secret='s',
                                 region='us', environment='prod')
    return pt.WebullTrader(Strategy(cfg, source_path=os.path.join(tmp, 't.yaml')), args)


t = wtrader()
check('algo: overnight is traded when its trading options say so (the plan has it)', 'overnight' in t.core.allowed_sessions())
t._drop_overnight()
t.core.write_status()
st = json.load(open(t.core.status_path))
check('algo: plan without overnight -> never trades overnight; the algo tab\'s note says Overnight Prices Off, checked again',
      'overnight' not in t.core.allowed_sessions() and any('Overnight Prices (Webull): Off' in n for n in st.get('broker_notes', [])))
t.client = c = FakeStream(refuse=True)
t._overnight_probe()
check('algo: checked again, still refused -> stays off', not t.overnight and 'overnight' not in t.core.allowed_sessions() and c.disc == 0)
c.refuse = False
t.core.last_error = pt.NO_OVERNIGHT_NOTE
t._overnight_probe()
st = json.load(open(t.core.status_path))
check('algo: the plan has overnight now -> trades overnight again by itself (trading options decide), note On, reconnects',
      t.overnight and 'overnight' in t.core.allowed_sessions() and c.disc == 1 and t.core.last_error is None
      and any('Overnight Prices (Webull): On' in n for n in st.get('broker_notes', [])), str(st.get('broker_notes')))
check('algo: the timer asks again on its own (every minute it looks whether a check is due)',
      'overnight_recheck_due(self.overnight_checked)' in open(pt.__file__).read())

# the backup stop overnight at Webull
from algo.live_broker import LiveBroker  # noqa: E402


class FakeTrade:
    def __init__(self):
        self.placed, self.refuse_night = [], False
        outer = self

        class O:
            def place_order(self, account_id, new_orders):
                o = new_orders[0]
                outer.placed.append(o)
                if outer.refuse_night and o.get('support_trading_session') == 'NIGHT':
                    raise ServerException('INVALID_ORDER', 'stop orders are not supported in the overnight session', 400)
                return types.SimpleNamespace(json=lambda: {'ok': True})

            def get_order_detail(self, account_id, client_order_id):
                return types.SimpleNamespace(json=lambda: {'orders': []})

        self.order_v3, self.account_v2 = O(), None


t = wtrader('live', stop_loss_pct=8)
t.broker = LiveBroker(FakeTrade(), account_id='X1')
t.core.stop_api = {'hours': ''}
t.core.session_now = lambda: ('overnight', True)
b = t._bs_place('sell', 200, 91.0)
o = t.broker.trade.placed[-1]
check('backup stop overnight: asked for in Webull\'s night session (a day order, as Webull wants overnight)',
      o['support_trading_session'] == 'NIGHT' and o['time_in_force'] == 'DAY' and o['order_type'] == 'STOP_LOSS' and b['session'] == 'NIGHT', str(o)[:120])
check('backup stop: a night stop fits overnight; at 3:50 am (closed) it does not', t._bs_fits(b) and
      (setattr(t.core, 'session_now', lambda: (None, False)) or not t._bs_fits(b)))
t.core.session_now = lambda: ('overnight', True)
t.broker.trade.refuse_night = True
n0 = len(t.broker.trade.placed)
b2 = t._bs_place('sell', 200, 91.0)
pl = t.broker.trade.placed[n0:]
check('backup stop overnight refused by Webull: a day stop (all sessions, GTC) instead, said on the tab',
      [x['support_trading_session'] for x in pl] == ['NIGHT', 'ALL'] and pl[-1]['time_in_force'] == 'GTC' and b2['session'] == 'ALL'
      and 'refused' in t.core.stop_api['hours'], str([x['support_trading_session'] for x in pl]))
n1 = len(t.broker.trade.placed)
b3 = t._bs_place('sell', 200, 91.0)
check('...and Webull is not asked for a night stop again for a week (remembered across restarts)',
      t.broker.trade.placed[n1]['support_trading_session'] == 'ALL' and t._bs_fits(b3)
      and json.load(open(t.caps_path)).get('night_stop_refused_at'))
os.remove(t.caps_path); t._night_refused_at = 0.0
check('a day stop does not fit overnight while Webull may take a night one (it is switched)', not t._bs_fits({'session': 'ALL'}))
t.core.session_now = lambda: ('regular', True)
check('a day stop fits in the day', t._bs_fits({'session': 'ALL'}))

# the core switches a stop that no longer fits: cancel first, then place the new one -- never two at once
c = ct.TraderCore(t.core.strategy, 'live', logs_dir=tempfile.mkdtemp(), starting_cash=300000, broker_name='webull')
c.connected, c.last_price = True, 100.0
c.portfolio.open_position(ct.pd.Timestamp.now(tz='UTC'), 'long', 200, 100.0, 'entry', exact=True)
log = []
state = {'fits': True, 'status': 'working', 'n': 0}


def place(side, shares, stop):
    state['n'] += 1
    log.append(('place', state['n']))
    state['status'] = 'working'
    return {'id': state['n'], 'session': 'NIGHT' if state['n'] > 1 else 'ALL'}


c.stop_api = {'place': place, 'cancel': lambda b: log.append(('cancel', b['id'])), 'status': lambda b: (state['status'], 0, None),
              'listed': lambda b: True, 'fits': lambda b: state['fits'], 'hours': ''}
c._bstop_tick()
state['fits'] = False                     # 8 pm: the day stop does not fit any more
c._bstop_checked = 0; c._bstop_tick()
c._bstop_checked = 0; c._bstop_tick()     # still cancelling: nothing new
check('core: a stop that no longer fits is cancelled first, and nothing new is placed until the broker confirms',
      log == [('place', 1), ('cancel', 1)], str(log))
state['status'], state['fits'] = 'cancelled', True
c._bstop_checked = 0; c._bstop_tick()
c._bstop_tick()
check('...then the stop for the session now is placed (one stop at a time)', log == [('place', 1), ('cancel', 1), ('place', 2)]
      and c.bstop and c.bstop['session'] == 'NIGHT', str(log))


# ---------------------------------------------------------------- the Webull overnight gap strategy on Webull data
import importlib  # noqa: E402
rb = importlib.import_module('run_backtest')
st = Strategy.from_yaml(os.path.join(ROOT, 'webull-algo', 'strategies', 'overnight_gap.yaml'))
check('overnight gap (Webull copy): a valid Webull strategy, paper only, overnight sessions', st.symbol == 'AAPL' and not st.confirm_live
      and set(st.trading_raw.get('paper_sessions')) == {'premarket', 'afterhours', 'overnight'})


def bars(with_overnight):
    """Webull 1-minute bars: Tuesday 3:30 pm -> Wednesday 10 am New York; up 1% after the close, 2% at the open."""
    out, t = [], ny(2026, 10, 6, 15, 30)
    end = ny(2026, 10, 7, 10, 0)
    while t < end:
        d = datetime.fromtimestamp(t, NY)
        night = d.hour >= 20 or d.hour < 4
        if with_overnight or not night:
            p = 100.0 if t < ny(2026, 10, 6, 16, 0) else (102.0 if t >= ny(2026, 10, 7, 9, 30) else 101.0)
            out.append({'time': str(int(t * 1000)), 'open': p, 'high': round(p * 1.003, 2), 'low': round(p * 0.997, 2), 'close': p,
                        'volume': 100})
        t += 60
    return out


class WebullData:
    def __init__(self, overnight):
        self.overnight, self.asked = overnight, []
        outer = self

        class MD:
            def get_history_bar(self, symbol, category, timespan, count='200', trading_sessions=None, **k):
                outer.asked.append(trading_sessions)
                if trading_sessions and 'OVN' in trading_sessions and not outer.overnight:
                    raise REFUSED
                rows = bars(outer.overnight)[-int(count):]
                return types.SimpleNamespace(json=lambda: [{'symbol': symbol, 'result': rows}])
        self.market_data = MD()


for with_ovn in (True, False):
    wd = WebullData(with_ovn)
    jb_webull.make_clients = lambda *a, **k: (None, wd, None)
    os.environ['WEBULL_APP_KEY'] = os.environ['WEBULL_APP_SECRET'] = 'fake'
    out = tempfile.mkdtemp()
    status = os.path.join(out, 'bt.json')
    sys.argv = ['run_backtest.py', '--strategy', os.path.join(ROOT, 'webull-algo', 'strategies', 'overnight_gap.yaml'), '--webull',
                '--sessions', 'premarket,afterhours,overnight', '--no-plot', '--out-dir', out, '--status-path', status]
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rb.main()
    bt = json.load(open(status))
    reasons = [x['reason'] for x in bt['trades']]
    check(f'overnight gap backtests on Webull data ({"with" if with_ovn else "without"} overnight bars): buys late after-hours, sells at the open',
          reasons == ['entry', 'exit-rule'] and bt['metrics']['total_return_pct'] > 0 and bt['source'] == 'webull'
          and (wd.asked[0] == ['PRE', 'RTH', 'ATH', 'OVN']) and (with_ovn or wd.asked[1] == ['PRE', 'RTH', 'ATH']),
          f"{reasons} {bt['metrics'].get('total_return_pct')}% asked {wd.asked}")


# ---------------------------------------------------------------- the gateway follows the plan
import syscheck  # noqa: E402
import hedge  # noqa: E402
night_ms = ny(2026, 10, 12, 22, 0) * 1000
os.remove(jb_webull.overnight_path())
jb_webull.note_overnight('board', False, 'no plan')
off = syscheck.expected('webull', night_ms)
jb_webull.note_overnight('board', True, 'plan')
on = syscheck.expected('webull', night_ms)
check('health check: Webull prices expected overnight only once the plan has the overnight session', off is False and on is True
      and syscheck.expected('webull', ny(2026, 10, 16, 22, 0) * 1000) is False)
check('Hedge Ideas: may go to the Webull algo overnight once the plan has it', hedge._webull_overnight() is True)

bad = [r for r in RESULTS if not r[1]]
print(f'\novernight tests: {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')
sys.exit(1 if bad else 0)
