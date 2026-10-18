"""Order flow for the Chart page (jb-orderflow): who is buying and who is selling one symbol today.

Free data only. While the Chart page shows a symbol on 1D, the gateway's shared IB link (iblink.py)
watches every trade IBKR reports for it (the RT volume feed, plus the overnight venue for stocks)
and the best bid and ask. Each trade is sorted:
  at or above the ask -> a buyer paid up (BUY)      at or below the bid -> a seller hit the bid (SELL)
  in between          -> neither side (MID, not counted in the buyers vs sellers line)
From that it keeps, for the board day (it starts at 8 pm ET, like the journal): buyers vs sellers per
minute, shares traded at each price, the big trades, and the live bid / ask with sizes.

Trades before the page first asked for the symbol can't be sorted (IBKR doesn't send old bid / ask
for free), so that part of the day is ESTIMATED from 1-minute bars: a bar that closed up counts as
buying, down as selling. The page marks where the estimate ends. Big trades start when watching does.

Only symbols with LIVE IBKR prices work (not NYSE stocks your plan lacks, indexes or crypto).
At most 3 symbols are watched at once; one nobody looked at for 15 minutes is dropped.
Nothing is saved to disk.

  GET /api/plus/chartpage/flow?sym=TQQQ
"""
import asyncio
import threading
import time
from datetime import timedelta

import watch

MAX_SYMS = 3
IDLE_S = 15 * 60
BIG_X = 10                  # a big trade: 10x the average trade size today ...
BIG_MIN_USD = 100000        # ... and worth $100k or more (or $500k+ before 200 trades are counted)
BIG_EARLY_USD = 500000
NO_LIVE = (354, 10089, 10090, 10167, 10168, 10186, 10197)
_lock = threading.Lock()
_subs = {}                  # sym -> state dict
_hooked = set()             # IB connections whose error messages we listen to


def day_start(now_ms, fut=False):
    """The latest 8 pm ET that has passed (6 pm for futures, which reopen then), skipping Friday and
    Saturday nights (no session then)."""
    d = watch.et(now_ms)
    hh = 18 if fut else 20
    day = d.date() if d.hour >= hh else d.date() - timedelta(days=1)
    while day.weekday() in (4, 5):
        day -= timedelta(days=1)
    return watch.at_et(day, hh, 0)


def _new(sym, ds):
    return {'sym': sym, 'day': ds, 'ib': None, 'tickers': [], 'con': set(), 'state': 'starting', 'msg': '',
            'since': 0, 'est_until': 0, 'used': time.time(), 'mins': {}, 'vap': {}, 'prints': [],
            'n': 0, 'shares': 0.0, 'buy': 0.0, 'sell': 0.0, 'mid': 0.0, 'q': None, 'last_trade': 0, 'started_at': 0}


def _num(x):
    try:
        x = float(x)
        return x if x == x else 0.0
    except (TypeError, ValueError):
        return 0.0


def _add(s, t_ms, price, size, side):
    m = t_ms - t_ms % 60000
    b = s['mins'].setdefault(m, [0.0, 0.0, 0.0])
    b[{'buy': 0, 'sell': 1, 'mid': 2}[side]] += size
    k = round(price, 2)
    s['vap'][k] = s['vap'].get(k, 0.0) + size


def _trade(s, tk, price, size, t_ms):
    price, size = _num(price), _num(size)
    if price <= 0 or size <= 0:
        return
    bid, ask = _num(getattr(tk, 'bid', 0)), _num(getattr(tk, 'ask', 0))
    if 0 < bid <= ask:
        side = 'buy' if price >= ask else 'sell' if price <= bid else 'mid'
    else:
        side = 'mid'
    with _lock:
        if t_ms < s['day']:
            return
        _add(s, t_ms, price, size, side)
        s[side] += size
        s['n'] += 1
        s['shares'] += size
        s['last_trade'] = t_ms
        avg = s['shares'] / s['n']
        usd = price * size
        big = usd >= BIG_EARLY_USD if s['n'] < 200 else (size >= BIG_X * avg and usd >= BIG_MIN_USD)
        if big:
            s['prints'].append({'t': t_ms, 'p': price, 's': int(size), 'side': side})
            del s['prints'][:-40]


def _on_update(s, tk):
    try:
        for x in list(getattr(tk, 'ticks', None) or []):
            if getattr(x, 'tickType', None) in (48, 77):
                tm = getattr(x, 'time', None)
                t_ms = int(tm.timestamp() * 1000) if hasattr(tm, 'timestamp') else int(time.time() * 1000)
                _trade(s, tk, x.price, x.size, t_ms)
        bid, ask = _num(getattr(tk, 'bid', 0)), _num(getattr(tk, 'ask', 0))
        night = getattr(tk.contract, 'exchange', '') == 'OVERNIGHT'
        h = watch.et(time.time() * 1000).hour
        if 0 < bid <= ask and (not s.get('two') or night == (h >= 20 or h < 4)):   # the venue that is open now
            with _lock:
                s['q'] = {'bid': bid, 'ask': ask, 'bs': int(_num(getattr(tk, 'bidSize', 0))),
                          'as': int(_num(getattr(tk, 'askSize', 0))), 'ts': int(time.time() * 1000),
                          'venue': 'overnight' if night else 'regular'}
                if s['state'] == 'starting':
                    s['state'] = 'live'
    except Exception:
        pass


def _seed(s, bars, cut_ms):
    """Estimate the part of the day before watching began from 1-minute bars."""
    for b in bars:
        t = int(b.date.timestamp() * 1000) if hasattr(b.date, 'timestamp') else 0
        v, o, c, h, lo = _num(b.volume), _num(b.open), _num(b.close), _num(b.high), _num(b.low)
        if not t or t < s['day'] or t >= cut_ms or v <= 0 or c <= 0:
            continue
        side = 'buy' if c > o else 'sell' if c < o else 'mid'
        m = t - t % 60000
        bk = s['mins'].setdefault(m, [0.0, 0.0, 0.0])
        bk[{'buy': 0, 'sell': 1, 'mid': 2}[side]] += v
        s[side] += v
        lo, h = (lo or c), (h or c)
        steps = max(1, min(40, int(round((h - lo) * 100)) + 1))
        for i in range(steps):
            k = round(lo + (h - lo) * i / max(1, steps - 1), 2) if steps > 1 else round(c, 2)
            s['vap'][k] = s['vap'].get(k, 0.0) + v / steps


async def _start(ib, s, seed):
    import iblink
    from ib_async import ContFuture, Stock
    sym = s['sym']
    if sym.endswith('=F'):
        import chartpage
        root = sym[:-2]
        q = await asyncio.wait_for(ib.qualifyContractsAsync(ContFuture(root, chartpage.FUT[root])), 10)
        if not q or q[0] is None:
            raise RuntimeError(f'IBKR did not recognise {sym}')
        cons = [q[0]]
    else:
        c = await iblink.stock(ib, sym)
        cons = [c]
        try:
            on = Stock(c.symbol, 'OVERNIGHT', 'USD', primaryExchange=c.primaryExchange)
            q2 = await asyncio.wait_for(ib.qualifyContractsAsync(on), 10)
            if q2 and q2[0] is not None and on.conId:
                cons.append(on)
        except Exception:
            pass
    now = int(time.time() * 1000)
    if seed:
        secs = max(60, min(86400, (now - s['day']) // 1000 + 60))
        for c in cons:
            try:
                bars = await ib.reqHistoricalDataAsync(c, '', f'{secs} S', '1 min', 'TRADES', False, 2, timeout=25) or []
                with _lock:
                    _seed(s, bars, now)
            except Exception:
                pass
        s['est_until'] = now
    if id(ib) not in _hooked:
        ib.errorEvent += _on_error
        _hooked.add(id(ib))
    s['two'] = len(cons) > 1
    tks = []
    for c in cons:
        tk = ib.reqMktData(c, '233', False, False)
        tk.updateEvent += (lambda t, s=s: _on_update(s, t))
        tks.append(tk)
    with _lock:
        s.update(ib=ib, tickers=tks, con={c.conId for c in cons}, since=s['since'] or now, started_at=time.time())
        if s['state'] in ('off', 'error'):
            s['state'] = 'starting'


def _on_error(req_id, code, msg, contract=None):
    if code not in NO_LIVE or contract is None:
        return
    cid = getattr(contract, 'conId', None)
    with _lock:
        for s in _subs.values():
            if cid in s['con'] and s['state'] != 'live':
                s['state'] = 'nolive'
                s['msg'] = f'Your IBKR data plan has no live prices for {s["sym"]}, so its trades can\'t be sorted into buyers and sellers.'


def _stop(s):
    import iblink
    tks, s['tickers'] = s['tickers'], []
    ib = s['ib']
    if not tks or ib is None:
        return

    async def go(_ib):
        for tk in tks:
            try:
                _ib.cancelMktData(tk.contract)
            except Exception:
                pass
    try:
        if ib is iblink._st.get('ib'):
            iblink.run(go, timeout=10)
    except Exception:
        pass


def _supported(sym):
    import chartpage
    if not sym or '^' in sym or '-USD' in sym or sym.endswith('=X'):
        return 'Order flow needs a stock, ETF or future with live IBKR prices (not indexes, crypto or currencies).'
    if sym.endswith('=F') and sym[:-2] not in chartpage.FUT:
        return 'Order flow only knows the IBKR futures ES, NQ, YM, RTY, MES, MNQ, GC and CL.'
    return ''


def _sweep():
    now = time.time()
    with _lock:
        old = [k for k, s in _subs.items() if now - s['used'] > IDLE_S]
        while len(_subs) - len(old) > MAX_SYMS:
            rest = sorted((s['used'], k) for k, s in _subs.items() if k not in old)
            old.append(rest[0][1])
        gone = [_subs.pop(k) for k in old]
    for s in gone:
        _stop(s)


def snapshot(sym):
    import iblink
    why = _supported(sym)
    if why:
        return {'sym': sym, 'state': 'unsupported', 'msg': why}
    now_ms = int(time.time() * 1000)
    ds = day_start(now_ms, sym.endswith('=F'))
    with _lock:
        s = _subs.get(sym)
        if s and s['day'] != ds:          # a new board day began at 8 pm: start over
            _subs.pop(sym)
            old, s = s, None
        else:
            old = None
        if s is None:
            s = _subs[sym] = _new(sym, ds)
        s['used'] = time.time()
    if old:
        _stop(old)
    _sweep()
    iblink.start()
    link = iblink._st.get('ib')
    if s['ib'] is not link or not s['tickers']:
        if not iblink.connected():
            with _lock:
                s['state'], s['msg'] = 'error', 'IB Gateway is not connected right now, so order flow is paused.'
        elif time.time() - s.get('try_at', 0) > 20:
            s['try_at'] = time.time()
            try:
                iblink.run(lambda ib: _start(ib, s, seed=not s['since']), timeout=60)
            except Exception as e:
                with _lock:
                    s['state'], s['msg'] = 'error', 'IBKR: ' + str(e)[:140]
    with _lock:
        if s['state'] == 'starting' and s['started_at'] and time.time() - s['started_at'] > 25 and not s['q']:
            s['state'] = 'nolive'
            s['msg'] = s['msg'] or f'IBKR sent no live bid or ask for {sym} (your data plan may not cover it, or the market is closed).'
        mins = sorted(s['mins'].items())
        cum, press = 0.0, []
        for m, (b, sl, _) in mins:
            cum += b - sl
            press.append([m + 60000, round(cum)])
        vap = sorted([round(k, 2), round(v)] for k, v in s['vap'].items() if v >= 1)
        return {'sym': sym, 'state': s['state'], 'msg': s['msg'], 'day': s['day'], 'since': s['since'],
                'est_until': s['est_until'], 'quote': s['q'], 'buy': round(s['buy']), 'sell': round(s['sell']),
                'mid': round(s['mid']), 'press': press, 'vap': vap, 'prints': list(reversed(s['prints'])),
                'trades': s['n'], 'avg': round(s['shares'] / s['n']) if s['n'] else 0, 'at': now_ms}
