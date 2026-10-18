"""My trading tab (jb-trading): trading journal, win/loss by session with daily totals, the daily
loss-limit alarm (with algo pause), the paper algo leaderboard, and the "what if" replay.

Your fills come in by themselves where the brokers allow it, and you can always add one by hand:
  IBKR    every 5 minutes, a read-only connection to IB Gateway (client id 67) asks for today's
          executions. IB Gateway only hands out the current day's, so the Mac keeps them as they come.
  Webull  every 10 minutes, the order history of the last 7 days (needs Webull API keys that are
          approved for trading; if not, the tab says so and the IBKR part still works).
Everything is stored in data/journal.json (fills, your tags and notes, hand-added fills).

A "trading day" starts at 8 pm ET the evening before, so one day = overnight, pre-market,
regular hours and after-hours, in the order you trade them.

Leaderboard: every trading day at 8:30 pm ET (or when you press Run now) each strategy file of
both algos is backtested on the board's own minute-by-minute record (the last 35 days, overnight
included), one at a time. Each day's result is kept in data/leaderboard.json, so the ranking
covers weeks as the days add up.

Replay: any night the board recorded (35 days), 4 pm ET to 10:30 am ET the next trading day.
"""
import asyncio
import csv
import glob
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
JOURNAL = os.path.join(DATA, 'journal.json')
SETTINGS = os.path.join(DATA, 'trading_settings.json')
STATE = os.path.join(DATA, 'trading_state.json')          # alarms already sent today
BOARD = os.path.join(DATA, 'leaderboard.json')
ALGOS = {'ibkr': os.path.join(ROOT, 'ibkr-algo'), 'webull': os.path.join(ROOT, 'webull-algo')}
SESSIONS = ('overnight', 'pre-market', 'regular', 'after-hours')

DEFAULTS = {'loss_on': True, 'loss_limit': 500.0, 'pause_algos': False,
            'algo_on': True, 'algo_limit': 1000.0,
            'ibkr_fills': True, 'webull_fills': True,
            'account': 25000.0, 'risk': 100.0}

_lock = threading.Lock()
_status = {'started': False, 'ibkr': {'at': 0, 'ok': None, 'note': ''}, 'webull': {'at': 0, 'ok': None, 'note': ''},
           'board': {'running': '', 'at': 0, 'note': ''}}


# ------------------------------------------------------------------ files
def _read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, separators=(',', ':'))
    os.replace(tmp, path)


def settings():
    s = dict(DEFAULTS)
    got = _read(SETTINGS, {})
    if isinstance(got, dict):
        for k, v in got.items():
            if k in DEFAULTS and type(v) in (type(DEFAULTS[k]), int, float, bool):
                s[k] = type(DEFAULTS[k])(v)
    return s


def _journal():
    j = _read(JOURNAL, {})
    if not isinstance(j, dict):
        j = {}
    j.setdefault('fills', {})        # id -> fill
    j.setdefault('notes', {})        # trip id -> {'tag', 'note'}
    return j


def _now():
    return int(time.time() * 1000)


# ------------------------------------------------------------------ sessions and trading days
def session_of(ms):
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    if 4 * 60 <= m < 9 * 60 + 30:
        return 'pre-market'
    if 9 * 60 + 30 <= m < 16 * 60:
        return 'regular'
    if 16 * 60 <= m < 20 * 60:
        return 'after-hours'
    return 'overnight'


def _market_day(d):
    """Mon-Fri and not an NYSE holiday (jb-night)."""
    try:
        sys.path.insert(0, os.path.join(ROOT, 'lib')) if os.path.join(ROOT, 'lib') not in sys.path else None
        from nyse_hours import trading_day as _td
        return _td(d)
    except Exception:
        return d.weekday() < 5


def trading_day(ms):
    """The day a moment counts toward: 8 pm ET starts the next day (Fri 8 pm / weekend -> Monday)."""
    d = (watch.et(ms) + timedelta(hours=4)).date()
    while not _market_day(d):
        d += timedelta(days=1)
    return d.isoformat()


# ------------------------------------------------------------------ fills -> round trips
def _fill(fid, broker, sym, side, qty, price, ms, mult=1.0, commission=0.0, source='auto'):
    return {'id': fid, 'broker': broker, 'sym': sym, 'side': side, 'qty': float(qty), 'price': float(price),
            't': int(ms), 'mult': float(mult or 1), 'commission': float(commission or 0), 'source': source}


def round_trips(fills):
    """First in, first out per broker + symbol. A sell that closes a buy (or a buy that closes a
    short) makes one finished trade per piece it closes. Returns (trips, open lots)."""
    lots = {}                      # (broker, sym) -> [ {qty (signed), price, t, id, comm} ]
    trips = []
    for f in sorted(fills, key=lambda x: (x['t'], x['id'])):
        key = (f['broker'], f['sym'])
        q = f['qty'] if f['side'] == 'buy' else -f['qty']
        book = lots.setdefault(key, [])
        comm_left = f.get('commission', 0.0)
        while q and book and (book[0]['qty'] > 0) != (q > 0):
            lot = book[0]
            n = min(abs(q), abs(lot['qty']))
            long_ = lot['qty'] > 0
            pnl = (f['price'] - lot['price']) * n * f['mult'] * (1 if long_ else -1)
            c_in = lot['comm'] * n / abs(lot['qty0'])
            c_out = comm_left * n / f['qty'] if f['qty'] else 0.0
            pnl -= c_in + c_out
            trips.append({'id': f['id'] + ':' + lot['id'], 'broker': f['broker'], 'sym': f['sym'],
                          'side': 'long' if long_ else 'short', 'qty': n,
                          'entry': lot['price'], 'exit': f['price'], 'entry_t': lot['t'], 'exit_t': f['t'],
                          'entry_session': session_of(lot['t']), 'exit_session': session_of(f['t']),
                          'day': trading_day(f['t']), 'pnl': round(pnl, 2),
                          'pnl_pct': round((f['price'] / lot['price'] - 1) * 100 * (1 if long_ else -1), 2) if lot['price'] else None,
                          'mins': round((f['t'] - lot['t']) / 60000), 'fees': round(c_in + c_out, 4)})
            lot['qty'] -= n if long_ else -n
            q += n if long_ else -n
            if abs(lot['qty']) < 1e-9:
                book.pop(0)
        if abs(q) > 1e-9:
            book.append({'qty': q, 'qty0': q, 'price': f['price'], 't': f['t'], 'id': f['id'],
                         'comm': comm_left * abs(q) / f['qty'] if f['qty'] else 0.0, 'mult': f['mult'],
                         'unmatched': q < 0})   # sold more than the journal saw bought: usually shares bought before it started
    open_ = []
    for (broker, sym), book in lots.items():
        for lot in book:
            open_.append({'broker': broker, 'sym': sym, 'qty': lot['qty'], 'price': lot['price'], 't': lot['t'],
                          'mult': lot['mult'], 'session': session_of(lot['t']), 'unmatched': lot.get('unmatched', False)})
    return trips, open_


_offboard = {}      # symbol -> [[t_ms, price]] for positions in symbols on no board (Yahoo, review 2026-10-11 #11)


def _last_price(sym):
    with watch._lock:
        h = watch._px.get(sym)
        if h:
            return h[-1][1]
    # review 2026-10-11 #11: a position in a symbol that is on no board still counts toward the daily loss
    # limit, priced from Yahoo (cached 20 s by the ticket), and its prices are kept for tomorrow's 8 pm start
    try:
        import ticket
        # plain stock symbols only: the ticket's quote drops '=' and the like, so a future such as CL=F would be
        # looked up as CLF, another company's stock, and its price times the contract size counted as your loss
        p = ticket.quote(sym).get('last') if ticket._clean_sym(sym) == sym else None
    except Exception:
        p = None
    if p:
        now = int(time.time() * 1000)
        h = _offboard.setdefault(sym, [])
        if not h or now // 60000 != h[-1][0] // 60000:
            h.append([now, p])
        while h and h[0][0] < now - watch.KEEP_S * 1000:
            h.pop(0)
    return p


def _history(sym):
    with watch._lock:
        h = list(watch._px.get(sym) or [])
    return h or list(_offboard.get(sym) or [])


def summary(trips, open_, notes):
    for t in trips:
        n = notes.get(t['id']) or {}
        t['tag'], t['note'] = n.get('tag', ''), n.get('note', '')

    def add(bucket, t):
        b = bucket.setdefault('n', 0)
        bucket['n'] = b + 1
        bucket['pnl'] = round(bucket.get('pnl', 0) + t['pnl'], 2)
        bucket['wins'] = bucket.get('wins', 0) + (t['pnl'] > 0)
        bucket['best'] = max(bucket.get('best', t['pnl']), t['pnl'])
        bucket['worst'] = min(bucket.get('worst', t['pnl']), t['pnl'])

    by_entry, by_exit, by_tag, by_sym, days = {}, {}, {}, {}, {}
    for t in trips:
        add(by_entry.setdefault(t['entry_session'], {}), t)
        add(by_exit.setdefault(t['exit_session'], {}), t)
        add(by_tag.setdefault(t['tag'] or 'untagged', {}), t)
        add(by_sym.setdefault(t['sym'], {}), t)
        d = days.setdefault(t['day'], {'sessions': {}})
        add(d, t)
        add(d['sessions'].setdefault(t['entry_session'], {}), t)
    for o in open_:
        p = _last_price(o['sym'])
        o['last'] = p
        # a sale of shares bought before the journal started is not a real short: no open P&L for it
        o['unrealized'] = round((p - o['price']) * o['qty'] * o['mult'], 2) if p is not None and not o.get('unmatched') else None
    today = trading_day(_now())
    unreal = sum(o['unrealized'] or 0 for o in open_)
    unreal_today = sum(o['unrealized'] or 0 for o in open_ if trading_day(o['t']) == today)
    # positions carried in from an earlier day count only TODAY's move (since 8 pm ET last evening, when
    # the trading day starts), so a held 3x ETF falling today still counts toward the daily loss limit
    d = date.fromisoformat(today) - timedelta(days=1)
    start = watch.at_et(d, 20, 0)
    for o in open_:
        if o['unrealized'] is None or trading_day(o['t']) == today:
            continue
        hist = _history(o['sym'])
        base = watch.price_at(hist, start) if hist else None
        if base:
            o['unrealized_today'] = round((o['last'] - base) * o['qty'] * o['mult'], 2)
            unreal_today += o['unrealized_today']
    return {'by_entry': by_entry, 'by_exit': by_exit, 'by_tag': by_tag, 'by_sym': by_sym,
            'days': [dict(v, day=k) for k, v in sorted(days.items(), reverse=True)],
            'today': today, 'today_realized': round((days.get(today) or {}).get('pnl', 0), 2),
            'unrealized': round(unreal, 2), 'unrealized_today': round(unreal_today, 2)}


# ------------------------------------------------------------------ IBKR fills
def _ibkr_ports():
    p = (os.environ.get('IBKR_GATEWAY_PORT') or '').strip()
    return [int(p)] if p.isdigit() else [4001, 4002, 7496, 7497]


async def _ibkr_fetch():
    from ib_async import IB
    ib = IB()
    last = None
    for port in _ibkr_ports():
        try:
            await ib.connectAsync('127.0.0.1', port, clientId=67, readonly=True, timeout=8)
            break
        except Exception as e:
            last = e
    if not ib.isConnected():
        raise RuntimeError(f'IB Gateway not reachable ({last!r})')
    try:
        fills = await asyncio.wait_for(ib.reqExecutionsAsync(), 20)
        await asyncio.sleep(3)          # jb-fees: IBKR sends each fill's commission just after the fill list
        out = []
        for f in fills:
            c, e = f.contract, f.execution
            sym = c.symbol if c.secType == 'STK' else (c.localSymbol or c.symbol)
            if c.secType == 'FUT':
                sym = c.symbol + '=F'
            mult = float(c.multiplier) if getattr(c, 'multiplier', '') not in ('', None) else 1.0
            comm = getattr(getattr(f, 'commissionReport', None), 'commission', 0.0) or 0.0
            if comm > 1e9:
                comm = 0.0                                   # IBKR's "not known yet"
            out.append(_fill('ibkr:' + e.execId, 'ibkr', sym, 'buy' if e.side == 'BOT' else 'sell', e.shares,
                             e.price, e.time.timestamp() * 1000, mult, comm))
        return out
    finally:
        ib.disconnect()


def fetch_ibkr():
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_ibkr_fetch())
    finally:
        loop.close()


# ------------------------------------------------------------------ Webull fills
def _webull_items(obj):
    """Every dict in Webull's answer that looks like an order (has a symbol), however nested."""
    if isinstance(obj, dict):
        if obj.get('symbol') or obj.get('ticker'):
            yield obj
        for v in obj.values():
            if isinstance(v, (dict, list)):
                yield from _webull_items(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _webull_items(v)


def _pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, '', 0, '0'):
            return v
    return None


def _wb_time(v):
    if v is None:
        return None
    try:
        v = float(v)
        return v if v > 1e12 else v * 1000
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp() * 1000
    except ValueError:
        return None


def _wb_fee(o):
    """jb-fees: commission + fees on one Webull order, from whatever fee fields Webull sends (0 if none)."""
    for k in ('total_fee', 'total_fees', 'fees_total', 'total_commission_fee'):
        try:
            if o.get(k) not in (None, ''):
                return abs(float(o[k]))
        except (TypeError, ValueError):
            pass
    tot = 0.0
    for k, v in o.items():
        if ('commission' in k.lower() or 'fee' in k.lower()) and not isinstance(v, (dict, list, bool)):
            try:
                tot += abs(float(v))
            except (TypeError, ValueError):
                pass
    return tot


def fetch_webull():
    sys.path.insert(0, os.path.join(ROOT, 'lib')) if os.path.join(ROOT, 'lib') not in sys.path else None
    import jb_webull
    key, secret = os.environ.get('WEBULL_APP_KEY'), os.environ.get('WEBULL_APP_SECRET')
    if not key or not secret:
        raise RuntimeError('no Webull keys in settings.env')
    _, _, tc = jb_webull.make_clients(key, secret, os.environ.get('WEBULL_REGION_ID') or 'us', trade=True)
    acct = os.environ.get('WEBULL_ACCOUNT_ID')
    if not acct:
        res = tc.account_v2.get_account_list()
        accts = res.json() if hasattr(res, 'json') else res
        if isinstance(accts, dict):
            accts = accts.get('data') or accts.get('result') or []
        if not accts:
            raise RuntimeError('Webull returned no accounts for these keys (are they approved for trading?)')
        acct = str(accts[0]['account_id'])
    today = watch.et(_now()).date()
    # Webull needs both dates; end = tomorrow, so fills after 9 pm stamped with the next day's date are included
    start, end = (today - timedelta(days=6)).isoformat(), (today + timedelta(days=1)).isoformat()
    res = tc.order_v3.get_order_history(account_id=acct, page_size=100, start_date=start, end_date=end)
    body = res.json() if hasattr(res, 'json') else res
    out, sample = [], None
    for o in _webull_items(body):
        status = str(_pick(o, 'status', 'order_status') or '').upper()
        qty = _pick(o, 'filled_quantity', 'filled_qty', 'total_filled_quantity')
        px = _pick(o, 'filled_price', 'avg_filled_price', 'filled_avg_price', 'average_price', 'avg_price')
        if sample is None:
            sample = {k: v for k, v in o.items() if 'account' not in k.lower()}
        try:                                   # any order with shares filled counts, even if the rest was cancelled later
            if float(qty or 0) <= 0 or float(px or 0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        side = str(_pick(o, 'side', 'order_side', 'action') or '').upper()
        t = _wb_time(_pick(o, 'filled_time_at', 'filled_time', 'update_time_at', 'update_time', 'place_time_at', 'place_time', 'create_time'))
        oid = _pick(o, 'client_order_id', 'order_id', 'id')
        if not side or t is None or not oid:
            continue
        sym = str(_pick(o, 'symbol', 'ticker')).upper()
        out.append(_fill('webull:' + str(oid), 'webull', sym, 'sell' if 'SELL' in side or 'SHORT' in side else 'buy',
                         qty, px, t, commission=_wb_fee(o)))
    if sample is not None:
        try:
            _write(os.path.join(DATA, 'journal_webull_sample.json'), sample)   # for checking field names
        except Exception:
            pass
    return out


def _merge(new, broker):
    if not new:
        return 0
    with _lock:
        j = _journal()
        added = 0
        for f in new:
            if f['id'] not in j['fills']:
                added += 1
            elif not f.get('commission') and j['fills'][f['id']].get('commission'):
                f['commission'] = j['fills'][f['id']]['commission']     # keep a commission seen earlier
            j['fills'][f['id']] = f
        _write(JOURNAL, j)
    return added


def refresh_fills(force=False):
    cfg = settings()
    for broker, every, fn in (('ibkr', 300, fetch_ibkr), ('webull', 600, fetch_webull)):
        st = _status[broker]
        if not cfg[broker + '_fills']:
            st.update(ok=None, note='turned off')
            continue
        if not force and time.time() - st['at'] < every:
            continue
        st['at'] = time.time()
        try:
            got = fn()
            n = _merge(got, broker)
            st.update(ok=True, note=f'{len(got)} fills seen' + (f', {n} new' if n else ''))
        except Exception as e:
            msg = str(e) or repr(e)
            st.update(ok=False, note=msg[:200])


# ------------------------------------------------------------------ loss limits
def _algo_today(name):
    st = _read(os.path.join(ALGOS[name], 'logs', 'live_status.json'), None)
    if not isinstance(st, dict):
        return None
    today = trading_day(_now())
    pnl = 0.0
    for r in st.get('round_trips') or []:
        try:
            ms = datetime.fromisoformat(str(r['exit_time']).replace('Z', '+00:00')).timestamp() * 1000
        except Exception:
            continue
        if trading_day(ms) == today:
            pnl += float(r.get('pnl') or 0)
    # jb-hedge: the Auto-Hedge's result today counts too (realized since 8 pm ET + what the open hedge shows)
    h = st.get('hedge') if isinstance(st.get('hedge'), dict) else {}
    hr, hu = float(h.get('realized_today') or 0), float(h.get('unrealized') or 0)
    un = float(st.get('unrealized_pnl') or 0) + hu
    return {'realized': round(pnl + hr, 2), 'unrealized': round(un, 2),
            'total': round(pnl + hr + un, 2), 'killed': bool(st.get('killed')),
            'strategy': st.get('strategy_name'), 'symbol': st.get('symbol'), 'hedge': round(hr + hu, 2)}


def _usd(v):
    return ('-' if v < 0 else '') + f'${abs(v):,.0f}'


def _pause(name, why):
    import algoctl
    if not os.path.isdir(ALGOS[name]):
        return False
    if os.path.exists(algoctl._kill_path(name)):
        return False
    algoctl.post(name, {'action': 'stop'})
    print(f'trading: paused the {name} algo: {why}', flush=True)
    return True


def check_limits():
    cfg = settings()
    state = _read(STATE, {})
    today = trading_day(_now())
    if state.get('day') != today:
        state = {'day': today, 'sent': []}
    changed = False
    if cfg['loss_on'] and cfg['loss_limit'] > 0:
        with _lock:
            j = _journal()
        trips, open_ = round_trips(list(j['fills'].values()))
        s = summary(trips, open_, j['notes'])
        total = s['today_realized'] + s['unrealized_today']    # today's trades + today's move of positions carried in
        if total <= -cfg['loss_limit'] and 'you' not in state['sent']:
            paused = []
            if cfg['pause_algos']:
                paused = [n for n in ALGOS if _pause(n, 'your daily loss limit')]
            watch.push('Daily loss limit hit',
                       f"Today you're down {_usd(-total)} (limit {_usd(cfg['loss_limit'])}): {_usd(s['today_realized'])} closed,"
                       f" {_usd(s['unrealized_today'])} on open positions today." + (f" Paused the {' and '.join(p.upper() for p in paused)} algo"
                                                             f"{'s' if len(paused) > 1 else ''}." if paused else ''),
                       'rotating_light')
            state['sent'].append('you'); changed = True
    if cfg['algo_on'] and cfg['algo_limit'] > 0:
        for name in ALGOS:
            a = _algo_today(name)
            if not a or a['killed'] or a['total'] > -cfg['algo_limit'] or ('algo ' + name) in state['sent']:
                continue
            _pause(name, 'its daily loss limit')
            watch.push(f'{name.upper()} algo paused',
                       f"The {name} paper algo is down ${-a['total']:,.0f} today (limit ${cfg['algo_limit']:,.0f}), so it was "
                       "stopped and its position closed. Press Resume on its tab to restart it.", 'pause_button')
            state['sent'].append('algo ' + name); changed = True
    if changed or not os.path.exists(STATE):
        _write(STATE, state)


# ------------------------------------------------------------------ leaderboard
def _strategy_files():
    out = []
    for name, d in ALGOS.items():
        for p in sorted(glob.glob(os.path.join(d, 'strategies', '*.y*ml'))):
            out.append((name, p))
    return out


def _read_trades_csv(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            try:
                ms = datetime.fromisoformat(r['time'].replace('Z', '+00:00'))
                if ms.tzinfo is None:
                    ms = ms.replace(tzinfo=timezone.utc)
                rows.append({'t': ms.timestamp() * 1000, 'side': r['side'], 'qty': float(r['shares']),
                             'price': float(r['price']), 'reason': r.get('reason', ''),
                             'commission': float(r.get('commission') or 0)})
            except Exception:
                continue
    return rows


def _strategy_meta(path):
    name, sym = os.path.basename(path), ''
    try:
        for line in open(path, encoding='utf-8'):
            s = line.strip()
            if s.startswith('name:'):
                name = s[5:].strip().strip('"\'')
            elif s.startswith('symbol:'):
                sym = s[7:].strip().strip('"\'')
    except OSError:
        pass
    return name, sym


def run_leaderboard():
    """Backtest every strategy file on the board's own record; keep each day's result."""
    if _status['board']['running']:
        return
    lb = _read(BOARD, {})
    if not isinstance(lb, dict):
        lb = {}
    notes = []
    for algo, path in _strategy_files():
        key = algo + '/' + os.path.basename(path)
        _status['board']['running'] = key
        name, sym = _strategy_meta(path)
        out_dir = tempfile.mkdtemp(prefix='jb-lb-')
        try:
            r = subprocess.run([sys.executable, 'run_backtest.py', '--strategy', path, '--recorded', '--no-plot',
                                '--out-dir', out_dir, '--status-path', ''], cwd=ALGOS[algo],
                               capture_output=True, text=True, timeout=15 * 60, stdin=subprocess.DEVNULL)
            csvs = glob.glob(os.path.join(out_dir, '*_trades_*.csv'))
            if r.returncode != 0 or not csvs:
                tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or ['no output']
                notes.append(f'{key}: {tail[0][:160]}')
                e = lb.setdefault(key, {'days': {}})
                e.update(algo=algo, file=os.path.basename(path), name=name, symbol=sym, error=tail[0][:160])
                continue
            trades = _read_trades_csv(csvs[0])
            fills = [_fill(f'{key}:{i}', algo, sym, 'buy' if t['side'].lower() in ('buy', 'cover', 'bot') else 'sell',
                           t['qty'], t['price'], t['t'], 1, t['commission']) for i, t in enumerate(trades)]
            trips, _ = round_trips(fills)
            days = {}
            for t in trips:
                d = days.setdefault(t['day'], {'pnl': 0.0, 'n': 0, 'wins': 0})
                d['pnl'] = round(d['pnl'] + t['pnl'], 2); d['n'] += 1; d['wins'] += t['pnl'] > 0
            e = lb.setdefault(key, {'days': {}})
            e.update(algo=algo, file=os.path.basename(path), name=name, symbol=sym, error='', ran=_now())
            today = trading_day(_now())
            for d, v in days.items():
                if d < today:                     # finished days only
                    e['days'][d] = v
            # a recorded day with no trades is a 0, so days with nothing to do count too
            for d in _recorded_days():
                if d < today:
                    e['days'].setdefault(d, {'pnl': 0.0, 'n': 0, 'wins': 0})
        except subprocess.TimeoutExpired:
            notes.append(f'{key}: took longer than 15 minutes, skipped')
        except Exception as ex:
            notes.append(f'{key}: {ex!r}'[:200])
        finally:
            for p in glob.glob(os.path.join(out_dir, '*')):
                try:
                    os.remove(p)
                except OSError:
                    pass
            try:
                os.rmdir(out_dir)
            except OSError:
                pass
    lb['_ran'] = _now()
    _write(BOARD, lb)
    _status['board'].update(running='', at=time.time(), note='; '.join(notes))


def _recorded_days():
    days = set()
    for f in glob.glob(os.path.join(DATA, 'ibkr_ticks', '*.csv')):
        d = os.path.basename(f)[:-4]
        try:
            dt = datetime.strptime(d, '%Y-%m-%d')
        except ValueError:
            continue
        if dt.weekday() < 5:
            days.add(d)
    return days


def leaderboard():
    lb = _read(BOARD, {})
    rows = []
    today = trading_day(_now())
    for key, e in lb.items():
        if key.startswith('_') or not isinstance(e, dict):
            continue
        ds = sorted(e.get('days', {}).items())

        def tot(n):
            part = ds[-n:] if n else ds
            pnl = sum(v['pnl'] for _, v in part)
            trades = sum(v['n'] for _, v in part)
            wins = sum(v['wins'] for _, v in part)
            return {'pnl': round(pnl, 2), 'trades': trades, 'win_rate': round(wins / trades * 100) if trades else None,
                    'days': len(part), 'green_days': sum(1 for _, v in part if v['pnl'] > 0)}
        rows.append({'key': key, 'algo': e.get('algo'), 'file': e.get('file'), 'name': e.get('name'),
                     'symbol': e.get('symbol'), 'error': e.get('error', ''), 'd5': tot(5), 'd20': tot(20), 'all': tot(0),
                     'curve': [[d, v['pnl']] for d, v in ds][-60:],
                     'running': os.path.basename(os.environ.get(('IBKR' if e.get('algo') == 'ibkr' else 'WEBULL') + '_ALGO_STRATEGY', '') or '') == e.get('file')})
    rows.sort(key=lambda r: -r['d20']['pnl'])
    live = {n: _algo_today(n) for n in ALGOS}
    return {'rows': rows, 'ran': lb.get('_ran'), 'live': live, 'status': dict(_status['board']), 'today': today}


# ------------------------------------------------------------------ replay
def replay_nights():
    """Evenings with a recorded night after them (newest first), from the IBKR board's record."""
    files = sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(DATA, 'ibkr_ticks', '*.csv')))
    out = []
    for d in files:
        try:
            dt = datetime.strptime(d, '%Y-%m-%d').date()
        except ValueError:
            continue
        if dt.weekday() < 5 and _window(d)[0] < _now():      # its 4 pm has passed
            out.append(d)
    return sorted(out, reverse=True)


def _window(evening):
    d = datetime.strptime(evening, '%Y-%m-%d').date()
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return watch.at_et(d, 16, 0), watch.at_et(nxt, 10, 30)


def replay(query):
    q = {k: (v[0] if isinstance(v, list) else v) for k, v in (query or {}).items()}
    nights = replay_nights()
    night = q.get('night') or (nights[0] if nights else '')
    if night not in nights:
        return json.dumps({'nights': nights, 'syms': [], 'error': 'No recorded nights yet.' if not nights else 'Pick a night from the list.'})
    t0, t1 = _window(night)
    want = (q.get('sym') or '').upper()
    days = {time.strftime('%Y-%m-%d', time.gmtime(t / 1000)) for t in range(t0, t1 + 86400000, 86400000)}
    prices, syms = {}, set()
    for broker in ('ibkr', 'webull'):
        for d in sorted(days):
            p = os.path.join(DATA, broker + '_ticks', d + '.csv')
            if not os.path.exists(p):
                continue
            with open(p, encoding='utf-8', errors='replace') as f:
                for line in f:
                    parts = line.rstrip('\n').split(',')
                    if len(parts) != 3:
                        continue
                    try:
                        t = int(parts[1])
                    except ValueError:
                        continue
                    if t0 <= t <= t1:
                        syms.add(parts[0])
                        if parts[0] == want and (broker == 'ibkr' or t not in prices):
                            try:
                                prices[t] = float(parts[2])
                            except ValueError:
                                pass
    pts = sorted(prices.items())
    return json.dumps({'nights': nights, 'night': night, 'syms': sorted(syms), 'sym': want, 't0': t0, 't1': t1,
                       'points': pts})


# ------------------------------------------------------------------ API
def fees_paid(fills):
    """jb-fees: commissions paid per broker: this trading day, the last 7 and 30 days, and everything recorded."""
    now = _now()
    today = trading_day(now)
    out = {k: {'ibkr': 0.0, 'webull': 0.0, 'hand': 0.0} for k in ('today', 'd7', 'd30', 'all')}
    for f in fills:
        c = float(f.get('commission') or 0)
        if not c:
            continue
        b = f.get('broker') if f.get('broker') in ('ibkr', 'webull') else 'hand'
        out['all'][b] += c
        if now - f['t'] <= 30 * 86400000:
            out['d30'][b] += c
        if now - f['t'] <= 7 * 86400000:
            out['d7'][b] += c
        if trading_day(f['t']) == today:
            out['today'][b] += c
    for v in out.values():
        for b in list(v):
            v[b] = round(v[b], 2)
        v['total'] = round(v['ibkr'] + v['webull'] + v['hand'], 2)
    return out


def snapshot():
    with _lock:
        j = _journal()
    fills = list(j['fills'].values())
    trips, open_ = round_trips(fills)
    s = summary(trips, open_, j['notes'])
    trips.sort(key=lambda t: -t['exit_t'])
    return json.dumps({'at': _now(), 'settings': settings(), 'trips': trips[:400], 'open': open_, 'summary': s,
                       'fills': sorted(fills, key=lambda f: -f['t'])[:200], 'fees': fees_paid(fills),
                       'sources': {'ibkr': dict(_status['ibkr']), 'webull': dict(_status['webull'])},
                       'algos': {n: _algo_today(n) for n in ALGOS}, 'sessions': SESSIONS})


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def post(data):
    act = data.get('action')
    if act == 'settings':
        cur = settings()
        for k, v in (data.get('settings') or {}).items():
            if k in DEFAULTS:
                if isinstance(DEFAULTS[k], bool):
                    cur[k] = bool(v)
                else:
                    n = _num(v)
                    if n is not None and n >= 0:
                        cur[k] = n
        _write(SETTINGS, cur)
        return 200, snapshot()
    if act == 'note':
        tid = str(data.get('id') or '')[:200]
        with _lock:
            j = _journal()
            j['notes'][tid] = {'tag': str(data.get('tag') or '')[:40], 'note': str(data.get('note') or '')[:500]}
            _write(JOURNAL, j)
        return 200, snapshot()
    if act == 'add':
        sym = str(data.get('sym') or '').upper().strip()[:16]
        side = data.get('side')
        qty, price = _num(data.get('qty')), _num(data.get('price'))
        try:
            dt = datetime.strptime(str(data.get('when')), '%Y-%m-%dT%H:%M').replace(tzinfo=watch.ET)
        except ValueError:
            return 400, json.dumps({'error': 'Pick the date and time (New York time).'})
        if not sym or side not in ('buy', 'sell') or not qty or qty <= 0 or not price or price <= 0:
            return 400, json.dumps({'error': 'Fill in the symbol, buy or sell, shares and price.'})
        broker = data.get('broker') if data.get('broker') in ('ibkr', 'webull', 'other') else 'other'
        fid = 'hand:' + str(_now()) + ':' + os.urandom(3).hex()
        _merge([_fill(fid, broker, sym, side, qty, price, dt.timestamp() * 1000, 1, _num(data.get('commission')) or 0, 'hand')], broker)
        return 200, snapshot()
    if act == 'delete':
        fid = str(data.get('id') or '')
        with _lock:
            j = _journal()
            if fid in j['fills'] and j['fills'][fid].get('source') == 'hand':
                del j['fills'][fid]
                _write(JOURNAL, j)
        return 200, snapshot()
    if act == 'refresh':
        refresh_fills(force=True)
        return 200, snapshot()
    if act == 'leaderboard':
        if not _status['board']['running']:
            threading.Thread(target=run_leaderboard, name='leaderboard', daemon=True).start()
            time.sleep(0.3)
        return 200, json.dumps(leaderboard())
    return 400, json.dumps({'error': 'unknown action'})


# ------------------------------------------------------------------ background
def _loop():
    while True:
        try:
            refresh_fills()
        except Exception as e:
            print(f'trading: fills: {e!r}', flush=True)
        try:
            check_limits()
        except Exception as e:
            print(f'trading: limits: {e!r}', flush=True)
        try:
            d = watch.et(_now())
            lb = _read(BOARD, {})
            ran = lb.get('_ran') or 0
            if d.weekday() < 5 and (d.hour > 20 or (d.hour == 20 and d.minute >= 30)) and \
                    trading_day(ran) != trading_day(_now()) and not _status['board']['running']:
                threading.Thread(                      # its own thread, so fills and the loss limit keep being checked
                    target=run_leaderboard, name='leaderboard', daemon=True).start()
        except Exception as e:
            print(f'trading: leaderboard: {e!r}', flush=True)
        time.sleep(60)


def start():
    if _status['started']:
        return
    _status['started'] = True
    threading.Thread(target=_loop, name='trading', daemon=True).start()
