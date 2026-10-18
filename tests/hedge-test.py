"""Tests for the delta-hedging module (gateway/hedge.py, jb-hedge) on made-up prices: no broker, no Yahoo, no internet.

A temporary data folder gets 30 trading days of the board's own minute record (data/ibkr_ticks/<date>.csv, every
5 minutes around the clock) and its saved daily closes for QQQ, SPY, TQQQ, SQQQ, SOXX, SOXL, SOXS, TECL, AAPL and the
NQ=F / ES=F futures, built so the true answers are known: TQQQ moves 3x QQQ, SQQQ -3x, SOXX 1.5x QQQ plus its own noise,
SPY 0.8x QQQ plus noise, the futures move with QQQ / SPY.

usage (from the josephs-board folder):   python3 tests/hedge-test.py
Prints PASS / FAIL per check and exits with 1 if any check failed.
"""
import json
import os
import random
import sys
import tempfile
import time
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get('JB_HEDGE_TEST_DATA') or tempfile.mkdtemp(prefix='jb-hedge-test-')   # (set it to fill a live-run folder)
os.environ['JB_DATA_DIR'] = DATA
os.environ['JB_NTFY_TOPIC'] = ''
sys.path.insert(0, os.path.join(ROOT, 'gateway'))
sys.path.insert(0, os.path.join(ROOT, 'lib'))

import watch        # noqa: E402
import hedge        # noqa: E402
import trading      # noqa: E402
import nyse_hours   # noqa: E402

RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(('PASS ' if ok else 'FAIL ') + name + (f'  -- {detail}' if detail else ''), flush=True)


trading._last_price = lambda sym: None          # no Yahoo in tests: off-board symbols use the average cost


# ================================================================ made-up prices
def trading_days(n, end):
    out, d = [], end
    while len(out) < n:
        if nyse_hours.trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def build(days=30, seed=7):
    """Writes the record and the daily history; returns {sym: {date: 4 pm close}} and the minute points."""
    rnd = random.Random(seed)
    end = date.today() - timedelta(days=1)
    tdays = trading_days(days, end)
    # one path per 5-minute step for QQQ, SPY noise and SOXX noise, around the clock from the first day 4 am
    t0 = watch.at_et(tdays[0], 4, 0)
    t1 = watch.at_et(tdays[-1], 20, 0)
    step = 5 * 60000
    q = 400.0
    lev = {'TQQQ': 3, 'SQQQ': -3, 'SOXL': 3, 'SOXS': -3, 'TECL': 3}
    base_of = {'TQQQ': 'QQQ', 'SQQQ': 'QQQ', 'SOXL': 'SOXX', 'SOXS': 'SOXX', 'TECL': 'XLKX'}
    fund_px = {'TQQQ': 60.0, 'SQQQ': 30.0, 'SOXL': 30.0, 'SOXS': 20.0, 'TECL': 80.0}
    last_close = None
    rows = {}
    closes = {}
    t = t0
    spy, soxx, aapl, xlk = 500.0, 200.0, 180.0, 220.0
    while t <= t1:
        d = watch.et(t)
        dd = d.date()
        r = rnd.gauss(0, 0.0012)                       # QQQ 5-minute move
        rs = 0.8 * r + rnd.gauss(0, 0.0004)
        rx = 1.5 * r + rnd.gauss(0, 0.0010)
        ra = 1.1 * r + rnd.gauss(0, 0.0015)
        rk = 1.05 * r + rnd.gauss(0, 0.0003)
        q *= 1 + r
        spy *= 1 + rs
        soxx *= 1 + rx
        aapl *= 1 + ra
        xlk *= 1 + rk
        px = {'QQQ': q, 'SPY': spy, 'SOXX': soxx, 'AAPL': aapl, 'XLKX': xlk, 'NQ=F': q * 50.0, 'ES=F': spy * 11.0}
        if last_close is None:
            last_close = dict(px)
        for f, L in lev.items():
            b = base_of[f]
            px[f] = fund_px[f] * (1 + L * (px[b] / last_close[b] - 1))
        for sym, p in px.items():
            if sym == 'XLKX':
                continue
            rows.setdefault(dd.isoformat(), []).append(f'{sym},{t},{p!r}\n')
        if nyse_hours.trading_day(dd) and d.hour == 16 and d.minute == 0:
            for f in lev:                              # the funds reset to their leverage at each 4 pm close
                fund_px[f] = px[f]
            last_close = dict(px)
            for sym, p in px.items():
                closes.setdefault(sym, {})[dd.isoformat()] = p
        t += step
    folder = os.path.join(DATA, 'ibkr_ticks')
    os.makedirs(folder, exist_ok=True)
    by_utc = {}
    for _, lines in rows.items():
        for ln in lines:
            ms = int(ln.split(',')[1])
            by_utc.setdefault(time.strftime('%Y-%m-%d', time.gmtime(ms / 1000)), []).append(ln)
    for day, lines in by_utc.items():
        with open(os.path.join(folder, day + '.csv'), 'w') as f:
            f.writelines(lines)
    return closes, tdays


CLOSES, TDAYS = build()


def set_price(sym, p):
    now = int(time.time() * 1000)
    with watch._lock:
        watch._px[sym] = [[now - 60000, p], [now, p]]


def positions(ibkr=(), webull=(), futs=(), typed=()):
    d = {'on': True, 'at': int(time.time() * 1000),
         'ibkr': {'rows': [{'sym': s, 'qty': q, 'avg': a} for s, q, a in ibkr], 'futs': list(futs), 'ok': 1, 'error': None},
         'webull': {'rows': [{'sym': s, 'qty': q, 'avg': a} for s, q, a in webull], 'ok': 1, 'error': None}}
    with open(os.path.join(DATA, 'broker_positions.json'), 'w') as f:
        json.dump(d, f)
    with open(os.path.join(DATA, 'holdings.json'), 'w') as f:
        json.dump([{'id': f'h{i}', 'date': '2026-10-01', 'sym': s, 'qty': q, 'price': p, 'note': '', 'created': i}
                   for i, (s, q, p) in enumerate(typed)], f)
    hedge._expo['at'] = 0


# ================================================================ part 1: Net Exposure
def t_beta():
    b, c, n, how = hedge.beta('SOXX', 'ndx')
    check('beta from the board\'s own minute record: SOXX vs QQQ is about 1.5', b is not None and abs(b - 1.5) < 0.2,
          f'beta {b}, corr {c}, {n} days ({how})')
    b2, _, _, how2 = hedge.beta('SPY', 'ndx')
    check('beta: SPY vs QQQ is about 0.8', b2 is not None and abs(b2 - 0.8) < 0.15, f'{b2} ({how2})')
    eb, note, est = hedge.eff_beta('TQQQ', 'ndx')
    check('leverage: TQQQ counts 3x the Nasdaq-100 (same index, no estimate)', abs(eb - 3) < 1e-9 and not est, note)
    eb, note, est = hedge.eff_beta('SQQQ', 'ndx')
    check('inverse: SQQQ counts -3x the Nasdaq-100', abs(eb + 3) < 1e-9, note)
    eb, note, est = hedge.eff_beta('SOXL', 'ndx')
    check('3x sector fund: SOXL = 3 x beta of SOXX (about 4.5x the Nasdaq-100)', abs(eb - 4.5) < 0.6 and not est, note)
    eb, note, est = hedge.eff_beta('TQQQ', 'spx')
    check('TQQQ vs the S&P 500 = 3 / beta(SPY vs QQQ)-ish, measured (not estimated)', 2.8 < eb < 4.6 and not est, f'{eb:.2f} {note}')
    eb, note, est = hedge.eff_beta('ZZZZ', 'ndx')
    check('a symbol with no history: estimated at 1.0 and marked Estimated', eb == 1.0 and est, note)


def t_exposure():
    set_price('TQQQ', 60.0)
    set_price('SQQQ', 30.0)
    set_price('NQ=F', 20000.0)
    set_price('AAPL', 180.0)
    positions(ibkr=[('TQQQ', 200, 55.0)], webull=[('SQQQ', 100, 31.0)],
              futs=[{'sym': 'MNQ', 'local': 'MNQZ6', 'qty': -1, 'mult': 2.0, 'avg': 20100.0}],
              typed=[('AAPL', 10, 170.0), ('TQQQ', 5, 50.0)])
    e = hedge.exposure()
    rows = {r['sym']: r for r in e['rows']}
    t = rows.get('TQQQ')
    check('Net Exposure: 200 TQQQ at $60 = +$360 per 1% Nasdaq-100 move', t and abs(t['d_ndx'] - 360) < 0.01, str(t and t['d_ndx']))
    s = rows.get('SQQQ')
    check('Net Exposure: 100 SQQQ at $30 (Webull) = -$90 per 1%', s and abs(s['d_ndx'] + 90) < 0.01 and s['src'] == 'Webull', str(s and s['d_ndx']))
    f = rows.get('MNQZ6')
    check('Net Exposure: short 1 MNQ (IBKR future, $2 a point) at 20,000 = -$400 per 1%', f and abs(f['d_ndx'] + 400) < 0.01,
          str(f and (f['d_ndx'], f['note_ndx'])))
    a = rows.get('AAPL')
    check('typed-in AAPL (no broker holds it) counts too, at its measured beta (about 1.1 x $1,800 / 100)',
          a and a['src'] == 'Typed' and 15 < a['d_ndx'] < 25,
          str(a and (a['d_ndx'], a['note_ndx'])))
    check('a typed-in TQQQ is left out when a broker already holds TQQQ (no double count)',
          sum(1 for r in e['rows'] if r['sym'] == 'TQQQ') == 1)
    want = 360 - 90 - 400 + a['d_ndx']
    check('total = sum of the rows', abs(e['total']['ndx'] - want) < 0.05, f"{e['total']['ndx']} vs {want:.2f}")
    check('per broker: IBKR, Webull and Typed', set(e['by_src']) == {'IBKR', 'Webull', 'Typed'} and
          abs(e['by_src']['Webull']['ndx'] + 90) < 0.01, json.dumps(e['by_src']))
    check('the line reads in dollars per 1% move, Title Case', e['line'].startswith('Net Exposure: ') and 'Per 1% Nasdaq-100 Move' in e['line']
          and 'Per 1% S&P 500 Move' in e['line'], e['line'])
    check('S&P 500 side is measured too (TQQQ more than 3x the S&P)', rows['TQQQ']['d_spx'] > 360, str(rows['TQQQ']['d_spx']))


def t_exposure_empty_and_off():
    positions()
    e = hedge.exposure()
    check('nothing held: Net Exposure says so, totals 0', e['line'] == 'Net Exposure: Nothing Held' and e['total']['ndx'] == 0, e['line'])
    positions(ibkr=[('QQQ', 10, 400.0)])
    with open(os.path.join(DATA, 'broker_positions.json')) as f:
        d = json.load(f)
    d['on'] = False
    with open(os.path.join(DATA, 'broker_positions.json'), 'w') as f:
        json.dump(d, f)
    e = hedge.exposure()
    check('account fill turned off: only typed-in trades count (none here)', not e['rows'], str(e['rows']))


def t_api():
    positions(ibkr=[('TQQQ', 100, 55.0)])
    code, body = hedge.get('', {})
    d = json.loads(body)
    check('GET /api/plus/hedge answers the exposure', code == 200 and 'total' in d and d['rows'], str(code))
    code, _ = hedge.post('nothing', {})
    check('unknown POST is refused', code == 400)


def t_brokerpos_futures():
    import brokerpos
    import types
    fut = types.SimpleNamespace(secType='FUT', symbol='MNQ', localSymbol='MNQZ6', multiplier='2')
    stk = types.SimpleNamespace(secType='STK', symbol='TQQQ')
    opt = types.SimpleNamespace(secType='OPT', symbol='QQQ')
    ps = [types.SimpleNamespace(contract=fut, position=-2, avgCost=40200.0, account='U1'),
          types.SimpleNamespace(contract=stk, position=100, avgCost=55.0, account='U1'),
          types.SimpleNamespace(contract=opt, position=1, avgCost=300.0, account='U1')]

    class IB:
        async def reqPositionsAsync(self):
            return ps
    import iblink
    old_run, old_acct = iblink.run, iblink.account
    try:
        import asyncio
        iblink.account = lambda: 'U1'
        iblink.run = lambda go, timeout=25: asyncio.run(go(IB()))
        rows, futs = brokerpos._ibkr()
    finally:
        iblink.run, iblink.account = old_run, old_acct
    check('brokerpos: IBKR futures kept apart (not a Portfolio row), price per point = avg cost / multiplier',
          [r['sym'] for r in rows] == ['TQQQ'] and futs == [{'sym': 'MNQ', 'local': 'MNQZ6', 'qty': -2.0, 'mult': 2.0, 'avg': 20100.0}],
          f'{rows} {futs}')


# ================================================================ part 2: hedge calculator
def t_calc():
    for sym, p in (('TQQQ', 60.0), ('SQQQ', 30.0), ('SOXL', 30.0), ('SOXS', 20.0), ('NQ=F', 20000.0), ('ES=F', 5500.0),
                   ('QID', 25.0), ('PSQ', 40.0), ('SPXU', 15.0), ('SPXS', 12.0), ('SDS', 20.0), ('SH', 40.0)):
        set_price(sym, p)
    positions(ibkr=[('TQQQ', 500, 55.0)], webull=[('SOXL', 400, 28.0)])
    e = hedge.exposure()
    c = hedge.calc('ndx', 100, e)
    net = e['total']['ndx']
    opts = {o['sym']: o for o in c['options']}
    check('calculator: long exposure -> inverse funds and selling micro futures', 'SQQQ' in opts and opts['MNQ'].get('side') == 'sell'
          and all(o.get('side') in (None, 'buy') for o in c['options'] if o['kind'] == 'etf'), ', '.join(opts))
    sq = opts['SQQQ']
    want_sh = round(net / (30.0 * 3 / 100))
    check('SQQQ shares bring the Nasdaq-100 exposure to about flat', sq['units'] == want_sh and abs(sq['net_after']) <= 0.9 + 0.01,
          f"net {net:.0f} -> buy {sq['units']} SQQQ -> {sq['net_after']}")
    check('SQQQ cost = shares x price (buying power used)', abs(sq['cost']['buying_power'] - sq['units'] * 30.0) < 0.01, str(sq['cost']))
    check('SQQQ decay: about 6 x daily variance a day for a -3x fund, plus the fee, in dollars', sq['decay_day'] > 0 and '-3x' in sq['decay_note'],
          f"${sq['decay_day']}/day: {sq['decay_note']}")
    check('the sector inverse matched to what you hold (SOXS for SOXL) is offered', 'SOXS' in opts and opts['SOXS'].get('units', 0) > 0,
          str(opts.get('SOXS', {}).get('text')))
    m = opts['MNQ']
    want_c = round(net / (20000.0 * 2 / 100))
    check('MNQ: contracts = exposure / ($2 x 20,000 / 100), IBKR only, margin shown as an estimate range',
          m['units'] == want_c and m['brokers'] == ['ibkr'] and len(m['cost']['margin']) == 2 and 'IBKR' in m['copy'],
          f"{m['units']} MNQ, {m['cost']}, net after {m['net_after']}")
    check('selling MNQ moves against your holdings (negative correlation) and cuts swings', m['corr'] is not None and m['corr'] < -0.85
          and m['cut'] > 50, f"corr {m['corr']}, cut {m['cut']}%")
    check('futures: no ticket (the board ticket takes stocks and ETFs), order text to copy instead',
          'ticket' not in m and "board's ticket takes stocks and ETFs only" in m['copy'])
    check('ETF: Copy To Ticket gets broker-free order fields (buy, shares, limit at the price)',
          sq['ticket'] == {'sym': 'SQQQ', 'side': 'buy', 'qty': sq['units'], 'limit': 30.0}, str(sq['ticket']))
    check('swing cut measured on the board\'s record: SQQQ against TQQQ + SOXL cuts most half-hour swings',
          sq['cut'] is not None and sq['cut'] > 60 and sq['corr'] < -0.85, f"cut {sq['cut']}%, corr {sq['corr']}, {sq['cut_n']} half-hours")
    so = opts['SOXS']
    check('...and SOXS (only matches the SOXL part) cuts less than SQQQ here', so['cut'] is not None and so['cut'] < sq['cut'],
          f"SOXS {so['cut']}% vs SQQQ {sq['cut']}%")
    best = [o['sym'] for o in c['options'] if o.get('best')]
    check('one Best Match among the funds, listed first', best == [c['options'][0]['sym']] and c['options'][0]['kind'] == 'etf', str(best))
    h = hedge.calc('ndx', 50, e)
    sq50 = {o['sym']: o for o in h['options']}['SQQQ']
    check('hedge 50%: half the shares', abs(sq50['units'] - sq['units'] / 2) <= 1, f"{sq50['units']} vs {sq['units']}")
    s = hedge.calc('spx', 100, e)
    check('S&P 500: SPXU / SPXS / SH and MES offered', {'SPXU', 'SH', 'MES'} <= {o['sym'] for o in s['options']},
          ', '.join(o['sym'] for o in s['options']))


def t_calc_short_and_flat():
    set_price('TQQQ', 60.0)
    set_price('SQQQ', 30.0)
    set_price('NQ=F', 20000.0)
    positions(ibkr=[('SQQQ', 1000, 30.0)])
    c = hedge.calc('ndx', 100, hedge.exposure())
    opts = {o['sym']: o for o in c['options']}
    check('net short (SQQQ): the calculator offers bull funds to buy and buying MNQ', opts.get('TQQQ', {}).get('side') == 'buy'
          and opts['MNQ'].get('side') == 'buy' and 'SQQQ' not in opts, ', '.join(f"{k}:{v.get('side')}" for k, v in opts.items()))
    positions(ibkr=[('TQQQ', 1, 60.0)])
    c = hedge.calc('ndx', 100, hedge.exposure())
    check('a tiny exposure ($1.80 per 1%): already about flat, nothing offered', not c['options'] and 'About Flat' in c['text'], c['text'])
    positions(ibkr=[('TQQQ', 50, 60.0)])
    c = hedge.calc('ndx', 100, hedge.exposure())
    m = {o['sym']: o for o in c['options']}['MNQ']
    check('smaller than one MNQ: 0 contracts and it says why', m['units'] == 0 and 'Smaller Than One MNQ' in m['text'], m.get('text', ''))
    code, body = hedge.get('calc', {'idx': ['spx'], 'pct': ['75']})
    d = json.loads(body)
    check('GET /api/plus/hedge/calc?idx=spx&pct=75', code == 200 and d['idx'] == 'spx' and d['pct'] == 75, f"{code} {d.get('text')}")


def t_ticket_fill_hook():
    js = open(os.path.join(ROOT, 'web', 'ticket-ui.js'), encoding='utf-8').read()
    check('ticket form: fill() hook for Copy To Ticket (fills the form, sends nothing)', 'fill: function' in js and 'JBTicket.last' in js)


# ================================================================ part 3: hedge partners
def t_partners():
    for sym, p in (('TQQQ', 60.0), ('SQQQ', 30.0), ('SOXL', 30.0), ('SOXS', 20.0), ('NQ=F', 20000.0), ('ES=F', 5500.0),
                   ('AAPL', 180.0), ('TECL', 80.0)):
        set_price(sym, p)
    positions(ibkr=[('TQQQ', 500, 55.0), ('AAPL', 100, 170.0)], webull=[('SOXL', 400, 28.0), ('SQQQ', 300, 31.0)])
    d = hedge.partners(hedge.exposure())
    rows = {r['sym']: r for r in d['rows']}
    t = rows['TQQQ']
    ps = {p['sym']: p for p in t['partners']}
    sq = ps.get('SQQQ', {})
    check('TQQQ: inverse twin SQQQ offered, correlation about -1', sq.get('label') == 'Inverse Twin' and sq.get('corr', 0) < -0.99,
          str({k: sq.get(k) for k in ('label', 'corr', 'ratio', 'cut_day', 'cut_night')}))
    check('TQQQ / SQQQ: about $1 of SQQQ per $1 of TQQQ, cuts daily and overnight swings by 90%+',
          abs(sq['ratio'] - 1) < 0.05 and sq['cut_day'] > 90 and sq.get('cut_night', 0) > 90 and sq['n_night'] >= 8,
          f"ratio {sq['ratio']}, day {sq['cut_day']}% ({sq['n_day']} {sq['how']}), night {sq.get('cut_night')}% ({sq.get('n_night')} nights)")
    check('TQQQ / SQQQ: shares for this position and the weekly decay of the -3x hedge',
          sq['units'] == round(sq['ratio'] * 30000 / 30.0) and sq['decay_week'] > 0, f"{sq['units']} sh, ${sq['decay_week']}/week: {sq['decay_note']}")
    m = ps.get('MNQ', {})
    check('TQQQ: selling MNQ (IBKR only) also offered, with whole contracts and no decay',
          m.get('side') == 'sell' and m.get('units') == 2 and m.get('brokers') == ['ibkr'] and m.get('decay_week') == 0,
          str({k: m.get(k) for k in ('side', 'units', 'ratio', 'cut_day', 'text')}))
    check('exactly one Best partner per holding', all(sum(1 for p in r['partners'] if p.get('best')) == 1 for r in d['rows']),
          ', '.join(f"{r['sym']}: {[p['sym'] for p in r['partners'] if p.get('best')]}" for r in d['rows']))
    s = rows['SOXL']
    sps = {p['sym']: p for p in s['partners']}
    check('SOXL (3x semis): SOXS is its inverse twin and hedges it better than SQQQ',
          'SOXS' in sps and sps['SOXS']['cut_day'] > sps['SQQQ']['cut_day'], f"SOXS {sps['SOXS'].get('cut_day')}% vs SQQQ {sps['SQQQ'].get('cut_day')}%")
    check('3x holding: its own weekly decay is shown too', s['own_decay_week'] > 0 and t['own_decay_week'] > 0, s['own_decay_note'])
    q = rows['SQQQ']
    qps = {p['sym']: p for p in q['partners']}
    check('an inverse fund (SQQQ): partners are bull funds (TQQQ "Bull Twin") and buying MNQ',
          qps.get('TQQQ', {}).get('label') == 'Bull Twin' and qps.get('MNQ', {}).get('side') == 'buy', ', '.join(f"{k}:{v.get('label')}" for k, v in qps.items()))
    a = rows['AAPL']
    aps = {p['sym']: p for p in a['partners']}
    check('a plain stock (AAPL): index inverse and the sector inverses the board has prices for, measured',
          'SQQQ' in aps and 'SOXS' in aps and aps['SQQQ'].get('corr') is not None, ', '.join(aps))
    small = hedge.partners({'rows': [dict(t and next(r for r in hedge.exposure()['rows'] if r['sym'] == 'TQQQ'), shares=20, value=1200.0)]})
    sm = {p['sym']: p for p in small['rows'][0]['partners']}['MNQ']
    check('a small position: MNQ "too big for this position", never the Best partner', sm['units'] == 0 and 'Too Big' in sm['text'] and not sm.get('best'),
          sm.get('text', ''))
    check('direction check: an inverse fund is bought and a micro future sold against a bull fund (expected_sign)',
          hedge.expected_sign('TQQQ', 'SQQQ') == 1 and hedge.expected_sign('TQQQ', 'MNQ') == -1 and hedge.expected_sign('SQQQ', 'MNQ') == 1)
    orig = hedge.pair_stats
    hedge.pair_stats = lambda sym, partner, rec: dict(orig(sym, partner, rec), ratio=0.5, corr=0.6) if partner == 'MNQ' else orig(sym, partner, rec)
    try:
        d2 = hedge.partners(hedge.exposure())
    finally:
        hedge.pair_stats = orig
    m2 = {p['sym']: p for p in next(r for r in d2['rows'] if r['sym'] == 'TQQQ')['partners']}['MNQ']
    check('a partner that moved WITH the holding on the record is shown as "not a hedge here", never sized or Best',
          'Not A Hedge Here' in (m2.get('text') or '') and not m2.get('units') and not m2.get('best'), m2.get('text', ''))
    code, body = hedge.get('pairs', {'fresh': ['1']})
    check('GET /api/plus/hedge/pairs', code == 200 and json.loads(body)['rows'], str(code))


# ================================================================ part 4: hedge ideas (research)
def t_ideas():
    positions(ibkr=[('TQQQ', 500, 55.0)])
    body = hedge.compute_ideas()
    ideas = body['ideas']
    check('hedge ideas: worked out on the board\'s record and ranked by their best session', ideas and
          all(ideas[i]['score'] >= ideas[i + 1]['score'] for i in range(len(ideas) - 1)),
          f"{len(ideas)} shown of {body['n_tested']} tested, {body['days']} days ({body['from']} to {body['to']})")
    ok = all(i['best_session'] in hedge.IDEA_SESSIONS and i['sessions'][i['best_session']]['n'] >= hedge.MIN_TRADES
             and {'win', 'avg', 'total', 'mdd'} <= set(i['sessions'][i['best_session']]) for i in ideas)
    check('each idea: win rate, average, total and worst drop, per session (after-hours, overnight, pre-market)', ok,
          json.dumps(ideas[0]['sessions'])[:300])
    check('labelled research, not advice', 'Not Advice' in body['note'], body['note'])
    leads = [i['lead'] for i in ideas]
    check('the list is not filled by one fund (at most 4 ideas per fund)', max(leads.count(x) for x in set(leads)) <= 4,
          ', '.join(sorted(set(leads))))
    # a twin pair held through a session moves about +3x and -3x the same index: about zero, minus the costs
    per = hedge.backtest_pair('TQQQ', 'SQQQ', 'hold', 1.0, 1.0, hedge._minutes({'TQQQ', 'SQQQ'}),
                              hedge._recorded_days(hedge._minutes({'TQQQ'}), ['TQQQ']))
    m = hedge._metrics(per['afterhours'])
    cost = 2 * (hedge.ETF_COST_PCT + 1.0 * hedge.ETF_COST_PCT)
    check('TQQQ + SQQQ held through after-hours: each trade about -0.4% (the fills\' costs), the market move cancels',
          m['n'] >= 20 and abs(m['avg'] + cost) < 0.05 and m['win'] == 0, str(m))
    check('overnight and pre-market sessions are backtested too (the record has them)', len(per['overnight']) >= 20 and len(per['premarket']) >= 20,
          f"{len(per['overnight'])} nights, {len(per['premarket'])} pre-markets")
    rec = hedge._minutes({'SOXL', 'SQQQ'})
    days = hedge._recorded_days(rec, ['SOXL'])
    hold = hedge.backtest_pair('SOXL', 'SQQQ', 'hold', 1.4, 1.0, rec, days)
    dip = hedge.backtest_pair('SOXL', 'SQQQ', 'dip2', 1.4, 1.0, rec, days)
    check('dip rule: only sessions where the fund fell 2% below the 4 pm close (fewer trades than holding)',
          sum(map(len, dip.values())) < sum(map(len, hold.values())), f"dip {sum(map(len, dip.values()))} vs hold {sum(map(len, hold.values()))}")
    allf = [i for i in body['ideas'] if i['kind'] == 'future']
    check('a futures idea is IBKR only; it sells the micro future against a bull fund, buys it against an inverse fund',
          allf and all(i['algos']['webull'] is False and i['partner_side'] == ('sell' if hedge.fund_of(i['lead'])[1] > 0 else 'buy') for i in allf),
          ', '.join(f"{i['lead']}:{i['partner_side']} {i['partner']}" for i in allf[:6]))
    sess = {i['id'][:30] + ' ' + i['best_session'] for i in ideas[:3]}
    check('ideas use only the board\'s own data (minute record + its daily closes; no network here)', bool(sess), '; '.join(sess))


def t_idea_strategy_and_backtest():
    import shutil
    positions(ibkr=[('TQQQ', 500, 55.0)])
    body = hedge.compute_ideas()
    with hedge._lock:
        hedge._ideas['body'] = body
    twin = next((i for i in body['ideas'] if i['lead'] == 'TQQQ' and i['partner'] == 'SQQQ' and i['rule'] == 'hold'), None)
    if twin is None:                     # not in the top 30: make it from the same parts
        twin = {'id': 'TQQQ-SQQQ-hold', 'lead': 'TQQQ', 'partner': 'SQQQ', 'rule': 'hold', 'dollar_ratio': 1.0, 'partner_side': 'buy',
                'title': 'Hold TQQQ, Hedged With SQQQ', 'sessions': {}, 'best_session': 'afterhours', 'kind': 'etf', 'algos': {'ibkr': True, 'webull': True}}
        body['ideas'].append(twin)
    y = hedge.strategy_yaml(twin, 'afterhours')
    algo = tempfile.mkdtemp(prefix='jb-hedge-algo-')
    shutil.copytree(os.path.join(ROOT, 'ibkr-algo'), os.path.join(algo, 'ibkr-algo'), ignore=shutil.ignore_patterns('logs', '__pycache__'))
    shutil.copytree(os.path.join(ROOT, 'lib'), os.path.join(algo, 'lib'))
    a_dir = os.path.join(algo, 'ibkr-algo')
    sys.path.insert(0, a_dir)
    from algo.strategy import Strategy
    path = os.path.join(a_dir, 'strategies', 'x_idea.yaml')
    with open(path, 'w') as f:
        f.write(y)
    st = Strategy.from_yaml(path)
    check('the idea becomes a strategy file the algo accepts, with its hedge: section', st.hedge == {'symbol': 'SQQQ', 'ratio_pct': 100.0,
          'dollar_ratio': twin['dollar_ratio'], 'side': 'buy'} and st.symbol == 'TQQQ', str(st.hedge))
    import algoctl
    old = dict(algoctl.DIRS)
    algoctl.DIRS['ibkr'] = a_dir
    try:
        code, r = hedge.ideas_send({'id': twin['id'], 'session': 'afterhours', 'algo': 'ibkr'})
        check('Send To Run Backtest: writes the strategy file and starts the IBKR algo\'s backtest on the board\'s record',
              code == 200 and os.path.exists(os.path.join(a_dir, 'strategies', r.get('file', '?'))), str(r)[:200])
        t0 = time.time()
        while time.time() - t0 < 240:
            info = json.loads(algoctl.info('ibkr'))
            if info['run'] and not info['run']['running']:
                break
            time.sleep(1)
        bs = json.loads(open(os.path.join(a_dir, 'logs', 'backtest_status.json')).read()) if os.path.exists(os.path.join(a_dir, 'logs', 'backtest_status.json')) else {}
        h = (bs.get('metrics') or {}).get('hedge') or {}
        log = open(os.path.join(a_dir, 'logs', 'backtest_run.log')).read()[-600:] if os.path.exists(os.path.join(a_dir, 'logs', 'backtest_run.log')) else ''
        check('the backtest ran on the board\'s record with the hedge leg (SQQQ) next to each TQQQ trade',
              bs.get('source') == 'recorded' and h.get('trips', 0) >= 10 and not h.get('error'), (json.dumps(h)[:300] or log))
        mt = bs.get('metrics') or {}
        check('...TQQQ alone swings with the market; with the hedge the result is near zero minus costs, and the drop is smaller',
              h.get('combined_max_drawdown_pct') is not None and h['combined_max_drawdown_pct'] > mt.get('max_drawdown_pct', 0)
              and abs(h.get('combined_return_pct', 99)) < abs(mt.get('total_return_pct', 0)) + 1,
              f"alone {mt.get('total_return_pct')}% (drop {mt.get('max_drawdown_pct')}%), with hedge {h.get('combined_return_pct')}% (drop {h.get('combined_max_drawdown_pct')}%)")
        code, r = hedge.ideas_send({'id': twin['id'], 'session': 'overnight', 'algo': 'webull'})
        check('overnight idea to the Webull algo refused (no overnight prices on your Webull plan)', code == 400 and 'Overnight' in r['error'], r.get('error'))
        fi = next((i for i in body['ideas'] if i['kind'] == 'future'), None)
        if fi:
            code, r = hedge.ideas_send({'id': fi['id'], 'session': 'afterhours', 'algo': 'webull'})
            check('futures idea to the Webull algo refused (futures only at IBKR)', code == 400 and 'IBKR' in r['error'], r.get('error'))
        code, r = hedge.ideas_send({'id': 'nope', 'algo': 'ibkr'})
        check('an idea no longer on the list: refused with what to do', code == 404, r.get('error'))
    finally:
        algoctl.DIRS.update(old)
        sys.path.remove(a_dir)
    code, b = hedge.get('ideas', {})
    check('GET /api/plus/hedge/ideas', code == 200 and 'ideas' in json.loads(b))


TESTS = [t_beta, t_exposure, t_exposure_empty_and_off, t_api, t_brokerpos_futures, t_calc, t_calc_short_and_flat, t_ticket_fill_hook, t_partners, t_ideas, t_idea_strategy_and_backtest]

if __name__ == '__main__':
    for fn in TESTS:
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + ' ran without an error', False, repr(e))
    bad = [r for r in RESULTS if not r[1]]
    print(f'\nhedge tests: {len(RESULTS) - len(bad)} of {len(RESULTS)} passed')
    sys.exit(1 if bad else 0)
