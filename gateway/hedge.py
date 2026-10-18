"""Delta hedging (jb-hedge), running inside the gateway as the Plus module 'hedge'. Read only: nothing here
places an order.

  Net Exposure    your total market delta across both brokers, in dollars gained or lost per 1% move in the
                  Nasdaq-100 and in the S&P 500. Each holding counts as
                      value x leverage (3x fund = 3, inverse = -3) x beta of what it follows to QQQ / SPY
                  where the beta is measured on the board's own price history (the IBKR / Webull boards'
                  daily closes and their minute-by-minute record), and IBKR futures count at their full size.

Holdings: the Portfolio tab's positions (gateway/brokerpos.py, data/broker_positions.json, plus the trades you
typed in for symbols neither account holds) and the IBKR futures brokerpos.py reads.

  Hedge Calculator  how many shares of an inverse fund (SQQQ, SPXU, SOXS... matched to what you hold) or how many
                  MNQ / MES micro futures (IBKR only) bring you to about flat, with what each costs (buying power,
                  futures margin estimate, daily decay of leveraged funds) and how much of your swings it would have
                  cut on the board's own record. Copy To Ticket only fills the order ticket; it never sends anything.

  Hedge Partners  for each holding, the best hedge partner (inverse twin, sector inverse, index inverse or micro
                  future), with how well it hedged on the board's record: correlation, how much it cut the daily
                  swings and the overnight swings (4 pm close to the 9:30 open), and the decay cost of 3x funds.

  Hedge Ideas     RESEARCH, NOT ADVICE: pair ideas (a fund and its inverse twin, a fund hedged with an index inverse or a
                  micro future, a fund hedged with the inverse of a fund it usually moves with), held through
                  after-hours, overnight or pre-market (or bought on a dip there), backtested on the board's own
                  minute record (and its daily closes for a day hold), ranked, with win rate, worst drop and results
                  per session. Any idea can be sent to an algo's Run backtest: the board writes it as a strategy file
                  with a hedge: section (algo/autohedge.py) and starts the backtest on the board's record.

  Auto-Hedge      the settings box on each algo tab (default OFF): hedge ratio and instrument, written to
                  <algo folder>/hedge_settings.json for the algo (algo/autohedge.py). Live hedging has its own switch,
                  needs the words HEDGE LIVE, and is refused unless ./jb live is running for that algo; it counts only
                  for that ./jb live run. Futures hedges only for the IBKR algo.

  GET /api/plus/hedge            the Net Exposure line, per holding and per broker
  GET /api/plus/hedge/calc?idx=ndx|spx&pct=100     the Hedge Calculator (pct: how much of the exposure to hedge)
  GET /api/plus/hedge/pairs      Hedge Partners for every holding
  GET /api/plus/hedge/ideas      the ranked Hedge Ideas (worked out in the background, at most every 6 hours)
  POST /api/plus/hedge/ideas/run      work them out again now
  POST /api/plus/hedge/ideas/send     {"id", "session", "algo": "ibkr"|"webull"} -> strategy file + Run backtest
  GET  /api/plus/hedge/algo?name=ibkr|webull       the Auto-Hedge box: settings, choices, what the algo reports
  POST /api/plus/hedge/algo           {"name", "on", "ratio_pct", "instrument"}  (paper Auto-Hedge and its settings)
  POST /api/plus/hedge/algo/live      {"name", "on", "confirm": "HEDGE LIVE"}    (the separate live switch)

Prices: the boards' live prices first, then Yahoo (free, the same lookup the My Trading tab uses), then the
average cost. Betas: half-hour moves in regular hours from the boards' own minute record (data/<broker>_ticks/,
35 days, at least 100 half-hours); else daily closes: the boards' saved broker history
(data/<broker>_ticks_history.json, about 2 months) plus the 4 pm closes in the minute record; when a symbol has
fewer than 15 days there, Yahoo's free daily closes (fetched in the background, at most one call every 2 seconds);
and when even that is missing a fixed estimate is used and the row says so.
"""
import bisect
import json
import math
import os
import threading
import time
from datetime import date, timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
BROKERS = ('ibkr', 'webull')            # the IBKR record wins where both have a minute (it has overnight)
MIN_DAYS = 15                           # daily returns needed before a beta is trusted
CACHE_S = 600                           # minute record / betas are re-read at most every 10 minutes
YAHOO_GAP_S = 2.0
INDEXES = {'ndx': 'Nasdaq-100', 'spx': 'S&P 500'}
# what stands for each index, best first: the ETF if the board records it, else the board's own futures
PROXIES = {'ndx': ('QQQ', 'NQ=F'), 'spx': ('SPY', 'ES=F')}
SAME_INDEX = {'ndx': {'QQQ', 'NQ=F', 'QQQM', 'ONEQ'}, 'spx': {'SPY', 'ES=F', 'IVV', 'VOO', 'SPLG'}}
# extra funds next to mdata.DEFAULT_PAIRS (the Night desk twins list): symbol -> (what it follows, leverage)
EXTRA_FUNDS = {'QQQ': ('QQQ', 1), 'SPY': ('SPY', 1), 'DIA': ('DIA', 1), 'IWM': ('IWM', 1), 'SOXX': ('SOXX', 1),
               'XLK': ('XLK', 1), 'QQQM': ('QQQ', 1), 'IVV': ('SPY', 1), 'VOO': ('SPY', 1),
               'PSQ': ('QQQ', -1), 'SH': ('SPY', -1), 'DOG': ('DIA', -1), 'RWM': ('IWM', -1),
               'SMH': ('SOXX', 1), 'SOXQ': ('SOXX', 1)}
# fixed estimates, used only when neither the board nor Yahoo has enough history (the row says "Estimated")
DEFAULT_BETA = {'ndx': {'SPY': 0.85, 'DIA': 0.7, 'IWM': 0.9, 'SOXX': 1.5, 'XLK': 1.1, 'XLF': 0.75, 'XBI': 0.9,
                        'TLT': -0.1, 'GDX': 0.3, 'XLE': 0.4, 'IBIT': 1.2},
                'spx': {'QQQ': 1.15, 'DIA': 0.85, 'IWM': 1.05, 'SOXX': 1.7, 'XLK': 1.25, 'XLF': 0.95, 'XBI': 1.0,
                        'TLT': -0.1, 'GDX': 0.35, 'XLE': 0.5, 'IBIT': 1.3}}
# IBKR futures: root -> (what it follows, $ per point, the board symbol with its price)
FUTURES = {'MNQ': ('QQQ', 2.0, 'NQ=F'), 'NQ': ('QQQ', 20.0, 'NQ=F'), 'MES': ('SPY', 5.0, 'ES=F'),
           'ES': ('SPY', 50.0, 'ES=F'), 'M2K': ('IWM', 5.0, 'RTY=F'), 'RTY': ('IWM', 50.0, 'RTY=F'),
           'MYM': ('DIA', 0.5, 'YM=F'), 'YM': ('DIA', 5.0, 'YM=F')}
FUT_BASE_PROXY = {'QQQ': 'NQ=F', 'SPY': 'ES=F', 'IWM': 'RTY=F', 'DIA': 'YM=F'}

_lock = threading.Lock()
_st = {'started': False}
_rec = {'files': {}, 'syms': set(), 'at': 0.0, 'data': {}}     # minute record cache (see _minutes)
_hist = {'mt': {}, 'data': {}}                                # board daily history cache
_beta = {}                                                    # (sym, idx) -> (at, beta, corr, n, how)
_yahoo = {}                                                   # sym -> (at, {date: close}) or (at, None)
_want_yahoo = set()
_expo = {'at': 0.0, 'body': None}


def _now():
    return int(time.time() * 1000)


def tc(text):
    """Title Case for display text (the board's rule), leaving tickers, numbers and symbols as they are:
    'beta 1.45 (board record vs QQQ)' -> 'Beta 1.45 (Board Record Vs QQQ)'."""
    out, up = [], True
    for ch in str(text):
        out.append(ch.upper() if up and ch.isalpha() and ch.islower() else ch)
        up = ch in ' -(/' or (up and not ch.isalnum() and ch != "'")
    return ''.join(out)


def _read(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


# ------------------------------------------------------------------ what each fund follows
def funds():
    """symbol -> (what it follows, leverage). From the Night desk twins list (gateway/mdata.py) plus a few 1x funds."""
    out = dict(EXTRA_FUNDS)
    try:
        import mdata
        pairs = mdata.pairs() if hasattr(mdata, 'pairs') else mdata.DEFAULT_PAIRS
    except Exception:
        pairs = []
    for row in pairs:
        try:
            _, base, lev, bull, bear = row[:5]
            lev = float(lev)
        except Exception:
            continue
        if bull:
            out.setdefault(str(bull).upper(), (str(base).upper(), lev))
        if bear:
            out.setdefault(str(bear).upper(), (str(base).upper(), -lev))
    return out


def fund_of(sym):
    """(what it follows, leverage); a plain stock follows itself at 1x."""
    return funds().get(sym, (sym, 1.0))


# ------------------------------------------------------------------ the board's own minute record
def _adjustments(broker):
    adj = _read(os.path.join(DATA, broker + '_ticks', 'adjustments.json'), {})
    return adj if isinstance(adj, dict) else {}


def _minutes(syms):
    """{sym: (sorted minute ms list, price list)} from data/<broker>_ticks/<UTC date>.csv ("SYM,minute_ms,price"),
    IBKR first. Past days' files are read once; today's file again when it grew. Futures rolls are taken out
    (adjustments.json, like the bridge does)."""
    syms = set(syms)
    with _lock:
        if not syms <= _rec['syms']:                  # a symbol not read before: read every file again for all
            _rec['syms'] |= syms
            _rec['files'] = {}
        want = set(_rec['syms'])
        files = dict(_rec['files'])
    seen = set()
    for broker in BROKERS:
        folder = os.path.join(DATA, broker + '_ticks')
        try:
            names = sorted(n for n in os.listdir(folder) if n.endswith('.csv'))
        except OSError:
            continue
        for name in names[-40:]:
            path = os.path.join(folder, name)
            seen.add(path)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if files.get(path, (None,))[0] == size:
                continue
            got = {}
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        parts = line.split(',')
                        if len(parts) != 3 or parts[0] not in want:
                            continue
                        try:
                            got.setdefault(parts[0], {})[int(parts[1])] = float(parts[2])
                        except ValueError:
                            continue
            except OSError:
                continue
            files[path] = (size, got)
    for p in list(files):
        if p not in seen:
            files.pop(p)
    with _lock:
        _rec['files'] = files
    out = {}
    adj = {b: _adjustments(b) for b in BROKERS}
    for sym in syms:
        merged = {}
        for broker in reversed(BROKERS):              # Webull first, so IBKR overwrites the same minute
            rolls = adj[broker].get(sym) or []
            for path, (_, got) in files.items():
                if os.path.basename(os.path.dirname(path)) != broker + '_ticks' or sym not in got:
                    continue
                for m, p in got[sym].items():
                    for r in rolls:
                        try:
                            if m < int(r[0]):
                                p += float(r[1])
                        except (TypeError, ValueError, IndexError):
                            pass
                    merged[m] = p
        ts = sorted(merged)
        out[sym] = (ts, [merged[t] for t in ts])
    return out


def _trading_day(d):
    try:
        from nyse_hours import trading_day
        return trading_day(d)
    except Exception:
        return d.weekday() < 5


def _close_hour(d):
    try:
        from nyse_hours import nyse_early_closes
        return 13 if d in nyse_early_closes(d.year) else 16
    except Exception:
        return 16


def _price_at(ts, px, ms, max_age_ms=None):
    """The last recorded price at or before ms (None if none, or older than max_age_ms)."""
    i = bisect.bisect_right(ts, ms) - 1
    if i < 0 or (max_age_ms is not None and ms - ts[i] > max_age_ms):
        return None
    return px[i]


def closes_from_minutes(ts, px):
    """{date: the 4 pm ET close (1 pm on early-close days)} for each trading day the record covers."""
    out = {}
    if not ts:
        return out
    d, end = watch.et(ts[0]).date(), watch.et(ts[-1]).date()
    while d <= end:
        if _trading_day(d):
            at = watch.at_et(d, _close_hour(d), 0)
            p = _price_at(ts, px, at, 20 * 60000)
            if p is not None and at <= ts[-1] + 60000:
                out[d.isoformat()] = p
        d += timedelta(days=1)
    return out


def _board_daily(sym):
    """{date: close} from the boards' saved broker history (data/<broker>_ticks_history.json 'daily')."""
    out = {}
    for broker in reversed(BROKERS):
        path = os.path.join(DATA, broker + '_ticks_history.json')
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        with _lock:
            hit = _hist['mt'].get(path)
            data = _hist['data'].get(path) if hit == mt else None
        if data is None:
            data = _read(path, {})
            data = data if isinstance(data, dict) else {}
            with _lock:
                _hist['mt'][path], _hist['data'][path] = mt, data
        e = data.get(sym)
        for pt in (e.get('daily') if isinstance(e, dict) else None) or []:
            try:
                out[watch.et(int(pt['t'])).date().isoformat()] = float(pt['p'])
            except (TypeError, ValueError, KeyError):
                continue
    return out


def daily_closes(sym, rec=None):
    """({date: close}, where from). Board history, with the minute record's 4 pm closes filling recent days;
    Yahoo's free daily closes (background) when the board has fewer than MIN_DAYS + 1."""
    d = _board_daily(sym)
    ts, px = (rec or _minutes([sym])).get(sym, ([], []))
    for k, v in closes_from_minutes(ts, px).items():
        d.setdefault(k, v)
    if len(d) > MIN_DAYS:
        return d, 'board'
    y = _yahoo.get(sym)
    if y and y[1] and len(y[1]) > MIN_DAYS:
        return y[1], 'yahoo'
    if not y or time.time() - y[0] > 6 * 3600:
        _want_yahoo.add(sym)
    return d, 'board'


# ------------------------------------------------------------------ beta
def returns(a, b):
    """Daily returns of two {date: close} series over the days both have (consecutive shared days only)."""
    days = sorted(set(a) & set(b))
    ra, rb = [], []
    for d0, d1 in zip(days, days[1:]):
        if (date.fromisoformat(d1) - date.fromisoformat(d0)).days > 5:
            continue                                    # a hole in the record: no return across it
        if a[d0] > 0 and b[d0] > 0:
            ra.append(a[d1] / a[d0] - 1)
            rb.append(b[d1] / b[d0] - 1)
    return ra, rb


def stats(x, y):
    """(beta of x to y, correlation, n) or (None, None, n)."""
    n = len(x)
    if n < MIN_DAYS:
        return None, None, n
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / (n - 1)
    vx = sum((a - mx) ** 2 for a in x) / (n - 1)
    vy = sum((b - my) ** 2 for b in y) / (n - 1)
    if vy <= 0 or vx <= 0:
        return None, None, n
    return cov / vy, cov / math.sqrt(vx * vy), n


def index_series(idx, rec=None):
    """({date: close}, proxy symbol, where from) for the Nasdaq-100 or the S&P 500."""
    best = ({}, PROXIES[idx][0], 'board')
    for p in PROXIES[idx]:
        d, src = daily_closes(p, rec)
        if len(d) > MIN_DAYS:
            return d, p, src
        if len(d) > len(best[0]):
            best = (d, p, src)
    return best


def half_hours(ts, px):
    """{(date, k): price} at 9:30, 10:00, ... 4:00 pm ET (k = 0..13) of each trading day in the record, using the
    last price no more than 10 minutes old. Regular hours only: before and after them prices are thin and lag."""
    out = {}
    if not ts:
        return out
    d, end = watch.et(ts[0]).date(), watch.et(ts[-1]).date()
    while d <= end:
        if _trading_day(d):
            last = (_close_hour(d) - 9) * 2 - 1           # 13 half-hours to 4 pm, 7 to 1 pm
            for k in range(last + 1):
                at = watch.at_et(d, 9, 30) + k * 30 * 60000
                p = _price_at(ts, px, at, 10 * 60000)
                if p is not None:
                    out[(d, k)] = p
        d += timedelta(days=1)
    return out


def intraday_returns(a, b):
    """Half-hour returns of two half_hours() series, within each day only (no overnight jump)."""
    ra, rb = [], []
    for key in sorted(set(a) & set(b)):
        prev = (key[0], key[1] - 1)
        if prev in a and prev in b and a[prev] > 0 and b[prev] > 0:
            ra.append(a[key] / a[prev] - 1)
            rb.append(b[key] / b[prev] - 1)
    return ra, rb


MIN_HALF_HOURS = 100


def beta(sym, idx, rec=None):
    """(beta, correlation, n, how) of sym's moves to the index, measured. beta None if too little history.
    First choice: half-hour moves in regular hours from the board's minute record (about 13 a day, so a few weeks
    already give a steady number); else daily closes (board, then Yahoo)."""
    if sym in SAME_INDEX[idx]:
        return 1.0, 1.0, None, 'same index'
    key = (sym, idx)
    hit = _beta.get(key)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1:]
    rec = rec if rec is not None and sym in rec else _minutes({sym, *PROXIES[idx]})
    b = c = None
    for p in PROXIES[idx]:
        if p == sym or not (rec.get(p) or ([],))[0]:
            continue
        b, c, n = stats(*intraday_returns(half_hours(*rec.get(sym, ([], []))), half_hours(*rec[p])))
        if b is not None and n >= MIN_HALF_HOURS:
            how = f"{n} half-hours of the board's minute record vs {p}"
            break
        b = c = None
    if b is None:
        s, src = daily_closes(sym, rec)
        ix, proxy, isrc = index_series(idx, rec)
        b, c, n = stats(*returns(s, ix))
        how = (f"{n} days of {'Yahoo' if 'yahoo' in (src, isrc) else 'board'} daily closes vs {proxy}"
               if b is not None else f'only {n} shared days of history')
    out = (round(b, 3) if b is not None else None, round(c, 3) if c is not None else None, n, how)
    _beta[key] = (time.time(),) + out
    return out


def eff_beta(sym, idx, rec=None):
    """How many dollars of the index one dollar of sym moves like: leverage x beta of what it follows.
    -> (effective beta, note, estimated?)"""
    base, lev = fund_of(sym)
    b, _, n, how = beta(base, idx, rec)
    lv = f'{lev:g}x ' if base != sym else ''
    if b is not None:
        return lev * b, tc(f'{lv}{base}, beta {b:.2f} ({how})' if how != 'same index' else f'{lv}{base} (same index)'), False
    if base != sym:                                     # what it follows has no history: measure the fund itself
        b2, _, n2, how2 = beta(sym, idx, rec)
        if b2 is not None:
            return b2, tc(f'{sym} itself, beta {b2:.2f} ({how2})'), False
    est = DEFAULT_BETA[idx].get(base, 1.0)
    return lev * est, tc(f'{lv}{base}, beta {est:g} estimated (not enough history yet)'), True


# ------------------------------------------------------------------ holdings
def _price(sym):
    """(price, where from)."""
    try:
        with watch._lock:
            h = watch._px.get(sym)
            if h:
                return float(h[-1][1]), 'board'
    except Exception:
        pass
    try:
        import trading
        p = trading._last_price(sym)          # board, else Yahoo for plain stock symbols (review 2026-10-11 #11)
        if p:
            return float(p), 'yahoo'
    except Exception:
        pass
    return None, None


def holdings():
    """[{sym, src, shares, avg, kind: 'stock'|'future', mult}] -- the Portfolio tab's positions plus IBKR futures."""
    out = []
    try:
        import portfolio
        for p in portfolio.positions():
            sh = float(p.get('shares') or 0)
            if abs(sh) < 1e-9:
                continue
            out.append({'sym': p['sym'], 'src': p.get('src') or 'Typed', 'shares': sh, 'avg': p.get('avg'),
                        'last': p.get('last'), 'kind': 'stock', 'mult': 1.0})
    except Exception as e:
        print(f'hedge: positions: {e!r}', flush=True)
    try:
        import brokerpos
        d = brokerpos._read()
        if d.get('on', True):
            for r in (d.get('ibkr') or {}).get('futs') or []:
                if float(r.get('qty') or 0):
                    out.append({'sym': str(r.get('sym') or '').upper(), 'src': 'IBKR', 'shares': float(r['qty']),
                                'avg': r.get('avg'), 'last': None, 'kind': 'future', 'local': r.get('local'),
                                'mult': float(r.get('mult') or FUTURES.get(str(r.get('sym')).upper(), (0, 1.0))[1])})
    except Exception as e:
        print(f'hedge: futures: {e!r}', flush=True)
    return out


def _row(h, rec):
    sym = h['sym']
    if h['kind'] == 'future':
        base, mult, board = FUTURES.get(sym, (None, h['mult'], None))
        px, src = _price(board) if board else (None, None)
        if px is None and h.get('avg'):
            px, src = float(h['avg']) / (mult or 1), 'avg cost'
        value = h['shares'] * mult * px if px else None
        r = {'sym': h.get('local') or sym, 'root': sym, 'src': h['src'], 'kind': 'future', 'shares': h['shares'],
             'price': px, 'price_src': src, 'value': round(value, 2) if value is not None else None, 'mult': mult}
        follows = base or 'QQQ'
        for idx in INDEXES:
            b, note, est = eff_beta(follows, idx, rec)           # MNQ follows QQQ: 1.0 to the Nasdaq-100
            if est and board:                                    # QQQ not recorded: the board's own futures
                b2, _, _, how2 = beta(board, idx, rec)
                if b2 is not None:
                    b, note, est = b2, tc(f'{board}, beta {b2:.2f} ({how2})'), False
            note = tc(f'{sym} future, {mult:g} $ per point: ') + note
            r['beta_' + idx], r['note_' + idx], r['est_' + idx] = round(b, 3), note, est
            r['d_' + idx] = round(value * b / 100, 2) if value is not None else None
        return r
    px, src = (h['last'], 'board') if h.get('last') else _price(sym)
    if px is None and h.get('avg'):
        px, src = float(h['avg']), 'avg cost'
    value = h['shares'] * px if px else None
    base, lev = fund_of(sym)
    r = {'sym': sym, 'src': h['src'], 'kind': 'stock', 'shares': h['shares'], 'price': px, 'price_src': src,
         'value': round(value, 2) if value is not None else None, 'follows': base, 'lev': lev}
    for idx in INDEXES:
        b, note, est = eff_beta(sym, idx, rec)
        r['beta_' + idx], r['note_' + idx], r['est_' + idx] = round(b, 3), note, est
        r['d_' + idx] = round(value * b / 100, 2) if value is not None else None
    return r


def _usd(v):
    return ('+' if v >= 0 else '-') + f'${abs(v):,.0f}'


def exposure():
    """The Net Exposure line: per holding, per broker and in total, $ per 1% move in each index."""
    hs = holdings()
    syms = set()
    for h in hs:
        base = FUTURES.get(h['sym'], (None, None, None))[2] if h['kind'] == 'future' else fund_of(h['sym'])[0]
        syms |= {h['sym'], base or h['sym']}
    for idx in INDEXES:
        syms |= set(PROXIES[idx])
    rec = _minutes(syms)
    rows = [_row(h, rec) for h in hs]
    total = {k: 0.0 for k in INDEXES}
    gross = {'long': 0.0, 'short': 0.0}
    by_src, missing = {}, []
    for r in rows:
        if r['value'] is None:
            missing.append(r['sym'])
            continue
        b = by_src.setdefault(r['src'], {k: 0.0 for k in INDEXES})
        for idx in INDEXES:
            total[idx] += r['d_' + idx]
            b[idx] += r['d_' + idx]
        gross['long' if r['d_ndx'] >= 0 else 'short'] += r['d_ndx']
    total = {k: round(v, 2) for k, v in total.items()}
    proxies = {}
    for idx in INDEXES:
        d, p, src = index_series(idx, rec)
        proxies[idx] = tc(f'{p} ({len(d)} days of closes, {src})')
    if not rows:
        line = 'Net Exposure: Nothing Held'
    else:
        line = (f"Net Exposure: {_usd(total['ndx'])} Per 1% Nasdaq-100 Move · {_usd(total['spx'])} Per 1% S&P 500 Move")
    return {'at': _now(), 'rows': rows, 'total': total, 'gross': {k: round(v, 2) for k, v in gross.items()},
            'by_src': {k: {i: round(x, 2) for i, x in v.items()} for k, v in by_src.items()},
            'line': line, 'missing': missing, 'proxies': proxies,
            'like': {'ndx': round(total['ndx'] * 100, 0), 'spx': round(total['spx'] * 100, 0)},
            'estimated': sorted({r['sym'] for r in rows if r.get('est_ndx') or r.get('est_spx')})}


def exposure_cached(max_age=10):
    with _lock:
        hit = _expo['body'] if time.time() - _expo['at'] < max_age else None
    if hit is not None:
        return hit
    body = exposure()
    with _lock:
        _expo['at'], _expo['body'] = time.time(), body
    return body



# ------------------------------------------------------------------ part 2: hedge calculator
# per index: funds that move against it (bear) and with it (bull), best first, and its micro future
INDEX_FUNDS = {'ndx': {'bear': ('SQQQ', 'QID', 'PSQ'), 'bull': ('TQQQ', 'QLD', 'QQQ'), 'future': 'MNQ'},
               'spx': {'bear': ('SPXU', 'SPXS', 'SDS', 'SH'), 'bull': ('UPRO', 'SPXL', 'SSO', 'SPY'), 'future': 'MES'}}
MICROS = ('MNQ', 'MES')
FUT_NAME = {'MNQ': 'Micro Nasdaq-100 Future', 'MES': 'Micro S&P 500 Future'}
EXPENSE_PCT = 0.95              # most leveraged / inverse funds charge about 0.95% a year
MARGIN_PCT = (5.0, 10.0)        # IBKR's futures margin is roughly this % of what one contract controls (estimate)
FLAT_USD = 5.0                  # $ per 1% below which there is nothing worth hedging


def _pairs():
    try:
        import mdata
        return mdata.pairs() if hasattr(mdata, 'pairs') else mdata.DEFAULT_PAIRS
    except Exception:
        return []


def matched_funds(rows, idx, want_bear):
    """Sector / single-stock funds that move against (want_bear) or with what you hold: the bear (or bull) fund of each
    group your holdings follow, heaviest first. -> [(sym, $ per 1% of yours it matches)]"""
    weight = {}
    for r in rows:
        if r.get('kind') != 'stock' or r.get('d_' + idx) is None:
            continue
        base = r.get('follows') or r['sym']
        for row in _pairs():
            try:
                _, pb, lev, bull, bear = row[:5]
            except Exception:
                continue
            if str(pb).upper() != base or float(lev) < 3:
                continue
            pick = bear if want_bear else bull
            if pick:
                weight[str(pick).upper()] = weight.get(str(pick).upper(), 0.0) + abs(r['d_' + idx])
            break
    return sorted(weight.items(), key=lambda kv: -kv[1])


def daily_vol(sym, rec=None):
    """Typical daily move (standard deviation, as a fraction) of sym: daily closes, else half-hours scaled, else 1.2%."""
    d, _ = daily_closes(sym, rec)
    ds = sorted(d)
    r = [d[b] / d[a] - 1 for a, b in zip(ds, ds[1:]) if d[a] > 0 and (date.fromisoformat(b) - date.fromisoformat(a)).days <= 5]
    if len(r) >= MIN_DAYS:
        m = sum(r) / len(r)
        return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1))
    rec = rec if rec is not None and sym in rec else _minutes({sym})
    hh = half_hours(*rec.get(sym, ([], [])))
    keys = sorted(hh)
    r = [hh[k] / hh[(k[0], k[1] - 1)] - 1 for k in keys if (k[0], k[1] - 1) in hh and hh[(k[0], k[1] - 1)] > 0]
    if len(r) >= MIN_HALF_HOURS:
        m = sum(r) / len(r)
        return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1) * 13) * 1.15   # + the moves outside regular hours
    return 0.012


def decay_per_day(sym, value, rec=None):
    """Rough holding cost per day of a leveraged fund vs an ideal L x the index: the volatility drag (L^2 - L)/2 x
    daily variance of what it follows, plus the fund fee. 0 for plain 1x funds and stocks. -> ($, note)"""
    base, lev = fund_of(sym)
    if abs(lev) <= 1 and lev > 0:
        return 0.0, 'No Leverage Decay'
    sd = daily_vol(base, rec)
    drag = (lev * lev - lev) / 2 * sd * sd
    fee = EXPENSE_PCT / 100 / 252
    return abs(value) * (drag + fee), tc(f'{lev:g}x fund: about {(drag + fee) * 100:.3f}% a day '
                                        f'(daily moves of {base} about {sd * 100:.1f}%, plus a {EXPENSE_PCT:g}% yearly fee)')


def _book_series(rows, idx, rec):
    """Your holdings' combined dollar move per half-hour (regular hours) on the board's record: {key: $}."""
    parts = []
    for r in rows:
        if r.get('value') is None:
            continue
        if r['kind'] == 'future':
            base = FUTURES.get(r['root'], ('QQQ', 1, 'NQ=F'))
            series = half_hours(*rec.get(base[0], ([], []))) or half_hours(*rec.get(base[2], ([], [])))
            parts.append((r['value'], series))
        else:
            base, lev = fund_of(r['sym'])
            series = half_hours(*rec.get(base, ([], [])))
            if not series:                              # what it follows is not recorded: the holding itself
                series, lev = half_hours(*rec.get(r['sym'], ([], []))), 1.0
            parts.append((r['value'] * lev, series))
    parts = [p for p in parts if p[1]]
    if not parts:
        return {}
    keys = set(parts[0][1])
    for _, sr in parts[1:]:
        keys &= set(sr)
    out = {}
    for k in keys:
        prev = (k[0], k[1] - 1)
        if all(prev in sr and sr[prev] > 0 for _, sr in parts):
            out[k] = sum(v * (sr[k] / sr[prev] - 1) for v, sr in parts)
    return out


def _hedge_series(sym, rec):
    """{key: return of 1 $ of sym per half-hour} (a future: 1 $ of what it follows)."""
    if sym in FUTURES:
        base, _, board = FUTURES[sym]
        sr = half_hours(*rec.get(base, ([], []))) or half_hours(*rec.get(board, ([], [])))
        lev = 1.0
    else:
        base, lev = fund_of(sym)
        sr = half_hours(*rec.get(base, ([], [])))
        if not sr:
            sr, lev = half_hours(*rec.get(sym, ([], []))), 1.0
    return {k: lev * (sr[k] / sr[(k[0], k[1] - 1)] - 1) for k in sr if (k[0], k[1] - 1) in sr and sr[(k[0], k[1] - 1)] > 0}


def swing_cut(book, hedge_ret, dollars):
    """(correlation of the hedge's dollar moves with yours, % of your half-hour swings the hedge of `dollars` (signed, + held
    long) would have taken out, n)."""
    keys = sorted(set(book) & set(hedge_ret))
    if len(keys) < MIN_HALF_HOURS:
        return None, None, len(keys)
    b = [book[k] for k in keys]
    h = [dollars * hedge_ret[k] for k in keys]          # the hedge's own dollar move: negative corr = moves against you
    _, corr, _ = stats(b, h)
    hedged = [x + y for x, y in zip(b, h)]

    def sd(v):
        m = sum(v) / len(v)
        return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    s0 = sd(b)
    return (round(corr, 3) if corr is not None else None), (round((1 - sd(hedged) / s0) * 100, 1) if s0 > 0 else None), len(keys)


def _limit(px):
    return round(px, 2) if px >= 1 else round(px, 4)


def calc(idx='ndx', pct=100.0, expo=None):
    """The Hedge Calculator: for each fitting fund or micro future, how much brings your exposure to the index to about
    flat (pct = how much of it to hedge), what it costs, and how much of your swings it would have cut."""
    idx = idx if idx in INDEXES else 'ndx'
    pct = max(10.0, min(150.0, float(pct or 100)))
    e = expo or exposure_cached()
    net = float(e['total'][idx])
    want = -net * pct / 100                       # $ per 1% move the hedge should add
    out = {'at': _now(), 'idx': idx, 'index': INDEXES[idx], 'net': round(net, 2), 'pct': pct, 'want': round(want, 2),
           'options': [], 'missing': e.get('missing') or []}
    if abs(want) < FLAT_USD:
        out['text'] = tc('Already about flat: nothing worth hedging' if e['rows'] else 'Nothing held: nothing to hedge')
        return out
    bear = want < 0
    names = list(INDEX_FUNDS[idx]['bear' if bear else 'bull'])
    matched = matched_funds(e['rows'], idx, bear)
    for sym, _ in matched:
        if sym not in names:
            names.append(sym)
    syms = set(names) | {fund_of(n)[0] for n in names} | set(PROXIES['ndx']) | set(PROXIES['spx'])
    for r in e['rows']:
        syms |= {r['sym'], r.get('follows') or r['sym']}
    syms |= {'QQQ', 'SPY', 'NQ=F', 'ES=F'}
    rec = _minutes(syms)
    book = _book_series(e['rows'], idx, rec)
    matched_w = dict(matched)
    for sym in names + list(MICROS):
        fut = sym in FUTURES
        if fut:
            base, mult, board = FUTURES[sym]
            px, src = _price(board)
            b, note, est = eff_beta(base, idx, rec)
            if est:
                b2, _, _, how2 = beta(board, idx, rec)
                if b2 is not None:
                    b, note, est = b2, tc(f'{board}, beta {b2:.2f} ({how2})'), False
            unit_delta = px * mult * b / 100 if px else None          # $ per 1% of one contract held long
        else:
            px, src = _price(sym)
            b, note, est = eff_beta(sym, idx, rec)
            unit_delta = px * b / 100 if px else None                  # $ per 1% of one share
        o = {'sym': sym, 'kind': 'future' if fut else 'etf', 'name': FUT_NAME.get(sym, ''), 'price': px, 'price_src': src,
             'beta': round(b, 3), 'note': note, 'est': est, 'brokers': ['ibkr'] if fut else ['ibkr', 'webull'],
             'matched': round(matched_w.get(sym, 0.0), 2), 'index_fund': sym in INDEX_FUNDS[idx]['bear' if bear else 'bull']}
        if not unit_delta:
            o['text'] = tc(f'no price for {sym} right now' if not px else f'{sym} does not move with the {INDEXES[idx]}')
            out['options'].append(o)
            continue
        units = want / unit_delta                     # signed: + buy / long, - sell / short
        if not fut and units < 0:
            o['text'] = tc(f'{sym} would have to be sold short: not offered (the board never opens a short)')
            out['options'].append(o)
            continue
        n = int(round(abs(units)))
        side = 'buy' if units > 0 else 'sell'
        added = (n if side == 'buy' else -n) * unit_delta
        dollars = (n if side == 'buy' else -n) * px * (mult if fut else 1)
        o.update(side=side, units=n, delta_added=round(added, 2), net_after=round(net + added, 2),
                 value=round(abs(dollars), 2))
        if n == 0:
            o['text'] = tc(f'your exposure is smaller than one {sym}, which moves ${abs(unit_delta):,.0f} per 1%')
        hr = _hedge_series(sym, rec)
        corr, cut, nn = swing_cut(book, hr, dollars) if n else (None, None, 0)
        o.update(corr=corr, cut=cut, cut_n=nn)
        if fut:
            o['cost'] = {'controls': round(abs(dollars), 2), 'margin': [round(abs(dollars) * m / 100) for m in MARGIN_PCT]}
            o['decay_day'], o['decay_note'] = 0.0, tc('futures carry a small built-in interest cost instead of decay')
            o['hours'] = tc('IBKR only. Trades nearly 24 hours Sunday 6 pm to Friday 5 pm ET, with a 5-6 pm pause each day')
            o['copy'] = (f"{side.upper()} {n} {sym} ({FUT_NAME[sym]}, front month) at IBKR, about {px:,.2f}. "
                         f"The board's ticket takes stocks and ETFs only: place this in the IBKR app or TWS.")
        else:
            dd, dn = decay_per_day(sym, dollars)
            o['cost'] = {'buying_power': round(abs(dollars), 2)}
            o['decay_day'], o['decay_note'] = round(dd, 2), dn
            o['hours'] = tc('IBKR: pre-market, after-hours and overnight (8 pm - 3:50 am, if the fund trades there). ' +
                            ('Webull: 4 am - 8 pm, and overnight (8 pm - 4 am) now that your Webull plan has it'
                             if _webull_overnight() else 'Webull: 4 am - 8 pm (no overnight on your plan)'))
            o['ticket'] = {'sym': sym, 'side': side, 'qty': n, 'limit': _limit(px)} if n else None
        out['options'].append(o)
    good = [o for o in out['options'] if o.get('units')]
    etfs = [o for o in good if o['kind'] == 'etf']
    if etfs:
        # the biggest swing cut; within 2 points of it (funds that follow the same index cut the same), the one that
        # ties up the least buying power -- the decay of each is shown next to it
        top = max((o.get('cut') if o.get('cut') is not None else -999) for o in etfs)
        near = [o for o in etfs if (o.get('cut') if o.get('cut') is not None else -999) >= top - 2]
        best = min(near, key=lambda o: o['cost']['buying_power'])
        best['best'] = True
    out['options'].sort(key=lambda o: (not o.get('units'), not o.get('best'), o['kind'] == 'future',
                                       -(o.get('cut') if o.get('cut') is not None else -999)))
    out['book_n'] = len(book)
    out['text'] = tc(f"hedging {pct:g}% of your {'+' if net >= 0 else '-'}${abs(net):,.0f} per 1% {INDEXES[idx]} move: "
                     f"each choice below adds about {'-' if want < 0 else '+'}${abs(want):,.0f} per 1%")
    return out


# ------------------------------------------------------------------ part 3: hedge partners per holding
SECTOR_POOL = ('SOXS', 'TECS', 'FAZ', 'LABD', 'TZA', 'SDOW', 'ERY', 'DUST')     # tried on single stocks, best measured wins
KIND_LABEL = {'twin': 'Inverse Twin', 'twin_bull': 'Bull Twin', 'sector': 'Sector Inverse', 'sector_bull': 'Sector Fund',
              'stock': 'Single-Stock Inverse', 'stock_bull': 'Single-Stock Fund', 'index': 'Index Inverse', 'index_bull': 'Index Fund',
              'future': 'Micro Future (IBKR Only)'}
_pairs_cache = {'at': 0.0, 'body': None}


def _twin(sym):
    for row in _pairs():
        try:
            bull, bear = str(row[3]).upper(), str(row[4]).upper()
        except Exception:
            continue
        if sym == bull and bear:
            return bear
        if sym == bear and bull:
            return bull
    return None


def partner_candidates(sym, shares):
    """[(partner, kind)] that move against this holding as held (long a bull fund -> bear funds; long an inverse fund or
    short a bull fund -> bull funds), best known first, plus both micro futures."""
    base, lev = fund_of(sym)
    long_market = (lev > 0) == (shares > 0)
    out, seen = [], {sym}

    def add(c, kind):
        if c and c not in seen:
            seen.add(c)
            out.append((c, kind if long_market else kind + '_bull' if kind in ('twin', 'sector', 'index', 'stock') else kind))
    add(_twin(sym), 'twin')
    for row in _pairs():
        try:
            _, pb, plev, bull, bear = row[:5]
        except Exception:
            continue
        pb = str(pb).upper()
        if pb == base and pb not in ('QQQ', 'SPY'):
            add(str(bear if long_market else bull).upper() or None, 'stock' if pb == sym else 'sector')
    if base == sym and long_market:                  # a plain stock: the sector inverses the board has prices for
        for c in SECTOR_POOL:
            if c not in seen and (_minutes({c}).get(c, ([],))[0] or len(daily_closes(c)[0]) > MIN_DAYS):
                add(c, 'sector')
    for c in (('SQQQ', 'SPXU') if long_market else ('TQQQ', 'UPRO')):
        add(c, 'index')
    for c in MICROS:
        add(c, 'future')
    return out


def overnight_returns(ts, px):
    """{date: return from that day's 4 pm close to the next trading day's 9:30 open} from the minute record."""
    out = {}
    if not ts:
        return out
    d, end = watch.et(ts[0]).date(), watch.et(ts[-1]).date()
    while d < end:
        if _trading_day(d):
            nxt = d + timedelta(days=1)
            while not _trading_day(nxt) and nxt <= end:
                nxt += timedelta(days=1)
            c = _price_at(ts, px, watch.at_et(d, _close_hour(d), 0), 15 * 60000)
            o = _price_at(ts, px, watch.at_et(nxt, 9, 31), 15 * 60000)
            if c and o:
                out[d.isoformat()] = o / c - 1
        d += timedelta(days=1)
    return out


def _sd(v):
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def _cut(h, p, ratio):
    """% of h's swings taken out by holding `ratio` $ of p per $ of h (negative if it made them bigger)."""
    s0 = _sd(h)
    return round((1 - _sd([a + ratio * b for a, b in zip(h, p)]) / s0) * 100, 1) if s0 > 0 else None


def pair_stats(sym, partner, rec):
    """How partner hedged sym on the board's record: daily correlation, the hedge ratio ($ of partner per $ of sym that
    cut the daily swings most), the daily and overnight swing cuts with that ratio."""
    ps = FUTURES[partner][2] if partner in FUTURES else partner          # MNQ -> the board's NQ=F
    a, _ = daily_closes(sym, rec)
    b, _ = daily_closes(ps, rec)
    h, p = returns(a, b)
    how = 'daily closes'
    if len(h) < MIN_DAYS:                                              # too few days: half-hours in regular hours
        h, p = intraday_returns(half_hours(*rec.get(sym, ([], []))), half_hours(*rec.get(ps, ([], []))))
        how = 'half-hours'
        if len(h) < MIN_HALF_HOURS:
            return {'n_day': len(h), 'text': tc(f'not enough shared history of {sym} and {ps} on the board yet')}
    bb, corr, n = stats(h, p)
    if bb is None:
        return {'n_day': n, 'text': tc('not enough shared history yet')}
    ratio = -bb                                                         # cov(h, p) / var(p), sign flipped
    out = {'corr': round(corr, 3), 'ratio': round(ratio, 3), 'cut_day': _cut(h, p, ratio), 'n_day': n, 'how': how,
           '_series': (h, p, None, None)}
    oa = overnight_returns(*rec.get(sym, ([], [])))
    ob = overnight_returns(*rec.get(ps, ([], [])))
    keys = sorted(set(oa) & set(ob))
    if len(keys) >= 8:
        oh, op = [oa[k] for k in keys], [ob[k] for k in keys]
        out.update(cut_night=_cut(oh, op, ratio), n_night=len(keys), _series=(h, p, oh, op))
    return out


def expected_sign(sym, partner, rec=None):
    """+1 when a hedge holds the partner long, -1 short (from leverage x beta): an inverse fund against a bull fund is
    bought, a micro future against it is sold. A measured ratio with the other sign moved WITH the holding: no hedge."""
    em = eff_beta(sym, 'ndx', rec)[0]
    ep = eff_beta(FUTURES[partner][0] if partner in FUTURES else partner, 'ndx', rec)[0]
    return 1 if -em * ep > 0 else -1 if -em * ep < 0 else 0


MIN_HEDGE_CORR = 0.3


def partners(expo=None):
    """Hedge Partners: for each stock / fund you hold, every fitting partner with its record, the best one marked."""
    e = expo or exposure_cached()
    rows = [r for r in e['rows'] if r['kind'] == 'stock' and r.get('value')]
    cand = {r['sym']: partner_candidates(r['sym'], r['shares']) for r in rows}
    syms = set(cand) | {FUTURES[c][2] if c in FUTURES else c for cs in cand.values() for c, _ in cs}
    syms |= {fund_of(c)[0] for cs in cand.values() for c, _ in cs if c not in FUTURES}
    rec = _minutes(syms)
    out = []
    for r in rows:
        sym, value = r['sym'], r['value']
        lst = []
        for c, kind in cand[sym]:
            st = pair_stats(sym, c, rec)
            fut = c in FUTURES
            o = dict(st, sym=c, kind='future' if fut else 'etf', label=KIND_LABEL.get(kind, kind), brokers=['ibkr'] if fut else ['ibkr', 'webull'])
            if st.get('ratio') is not None and (st['ratio'] > 0) != (expected_sign(sym, c, rec) > 0):
                o['text'] = tc(f"{c} moved with {sym} on the board's record (corr {st['corr']:.2f}): not a hedge here")
            elif st.get('ratio') is not None:
                dollars = st['ratio'] * value                            # + buy / - sell, $ of the partner
                if fut:
                    base, mult, board = FUTURES[c]
                    px, _ = _price(board)
                    n = int(round(abs(dollars) / (px * mult))) if px else None
                    o.update(price=px, units=n, side='buy' if dollars > 0 else 'sell', decay_week=0.0,
                             decay_note=tc('futures: no decay'))
                    if px and n == 0:
                        o['text'] = tc(f'too big for this position: one {c} controls ${px * mult:,.0f}')
                    elif n:                                              # whole contracts: the cut they really give
                        h, p, oh, op = st['_series']
                        eff = (n if dollars > 0 else -n) * px * mult / value
                        o['cut_day'] = _cut(h, p, eff)
                        if oh:
                            o['cut_night'] = _cut(oh, op, eff)
                elif dollars <= 0:
                    o['text'] = tc(f'{c} moves with {sym} here: it would have to be sold short (not offered)')
                else:
                    px, _ = _price(c)
                    dd, dn = decay_per_day(c, dollars, rec)
                    o.update(price=px, units=int(round(dollars / px)) if px else None, side='buy',
                             decay_week=round(dd * 5, 2), decay_note=dn)
                o['dollars'] = round(dollars, 2)
            o.pop('_series', None)
            lst.append(o)

        def score(o):
            cuts = [x for x in (o.get('cut_day'), o.get('cut_night')) if x is not None]
            return sum(cuts) / len(cuts) if cuts and o.get('units') else -999
        usable = [o for o in lst if score(o) > -999]
        if usable:
            top = max(score(o) for o in usable)
            near = [o for o in usable if score(o) >= top - 3]
            min(near, key=lambda o: o.get('decay_week') or 0.0)['best'] = True
        lst.sort(key=lambda o: (not o.get('best'), -score(o)))
        base, lev = fund_of(sym)
        own, own_note = decay_per_day(sym, value, rec) if abs(lev) > 1 or lev < 0 else (0.0, '')
        out.append({'sym': sym, 'src': r['src'], 'shares': r['shares'], 'value': value, 'follows': base, 'lev': lev,
                    'own_decay_week': round(own * 5, 2), 'own_decay_note': own_note, 'partners': lst})
    return {'at': _now(), 'rows': out}


def partners_cached(max_age=300):
    with _lock:
        hit = _pairs_cache['body'] if time.time() - _pairs_cache['at'] < max_age else None
    if hit is not None:
        return hit
    body = partners()
    with _lock:
        _pairs_cache['at'], _pairs_cache['body'] = time.time(), body
    return body


# ------------------------------------------------------------------ part 4: hedge ideas (research, not advice)
IDEAS_PATH = os.path.join(DATA, 'hedge_ideas.json')
IDEAS_EVERY_S = 6 * 3600
IDEA_SESSIONS = ('afterhours', 'overnight', 'premarket')
SESSION_LABEL = {'afterhours': 'After-Hours', 'overnight': 'Overnight', 'premarket': 'Pre-Market', 'day': 'Day Hold (Daily Closes)'}
SESSION_CODE = {'overnight': 1, 'premarket': 2, 'afterhours': 4}
RULES = {'hold': None, 'dip1': 1.0, 'dip2': 2.0}       # hold the pair through the session / buy when down X% vs the 4 pm close
MIN_TRADES = 8
ETF_COST_PCT = 0.1           # per fill outside regular hours (the algos' extended_hours_cost_pct default)
DAY_COST_PCT = 0.02          # per fill in regular hours
FUT_COST_PCT = 0.02          # per futures fill
EXIT_BEFORE_MIN = 15         # ideas close 15 minutes before their session ends, so the exit fills inside it
_ideas = {'body': None, 'running': False, 'at': 0.0}


def session_windows(d):
    """{session: (start ms, exit ms, the 4 pm close ms it is measured from)} for the sessions that start on date d
    (overnight = the night after d, only before a trading day; after-hours / pre-market only on trading days)."""
    out = {}
    nxt = d + timedelta(days=1)
    if _trading_day(d):
        ch = _close_hour(d)
        close = watch.at_et(d, ch, 0)
        out['afterhours'] = (close, close + 4 * 3600000 - EXIT_BEFORE_MIN * 60000, close)
        prev = d - timedelta(days=1)
        while not _trading_day(prev):
            prev -= timedelta(days=1)
        out['premarket'] = (watch.at_et(d, 4, 0), watch.at_et(d, 9, 30) - EXIT_BEFORE_MIN * 60000,
                            watch.at_et(prev, _close_hour(prev), 0))
    if _trading_day(nxt):
        last = d
        while not _trading_day(last):
            last -= timedelta(days=1)
        out['overnight'] = (watch.at_et(d, 20, 0), watch.at_et(nxt, 3, 50) - EXIT_BEFORE_MIN * 60000,
                            watch.at_et(last, _close_hour(last), 0))
    return out


def _metrics(rets):
    """Trade returns in % -> n, win rate, average, compounded total, worst drop from a high, best, worst, t-score."""
    n = len(rets)
    if not n:
        return {'n': 0}
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r / 100
        peak = max(peak, eq)
        mdd = min(mdd, (eq / peak - 1) * 100)
    avg = sum(rets) / n
    sd = _sd(rets) if n > 1 else 0.0
    return {'n': n, 'win': round(sum(1 for r in rets if r > 0) / n * 100, 1), 'avg': round(avg, 3),
            'total': round((eq - 1) * 100, 2), 'mdd': round(mdd, 2), 'best': round(max(rets), 2), 'worst': round(min(rets), 2),
            't': max(-99.0, min(99.0, round(avg / sd * math.sqrt(n), 2))) if sd > 1e-9 else (0.0 if abs(avg) < 1e-9 else math.copysign(99.0, avg))}


def backtest_pair(lead, partner, rule, ratio, sign, rec, days):
    """One idea on the board's minute record: per session, the list of trade returns (% of the lead's dollars)."""
    lt, lp = rec.get(lead, ([], []))
    ps = FUTURES[partner][2] if partner in FUTURES else partner
    pt, pp = rec.get(ps, ([], []))
    if not lt or not pt:
        return {}
    dip = RULES[rule]
    c_p = FUT_COST_PCT if partner in FUTURES else ETF_COST_PCT
    cost = 2 * (ETF_COST_PCT + ratio * c_p)
    out = {k: [] for k in IDEA_SESSIONS}
    for d in days:
        for sess, (start, end, close_ms) in session_windows(d).items():
            if end > lt[-1] or start < lt[0]:
                continue
            i0 = bisect.bisect_left(lt, start)
            i1 = bisect.bisect_right(lt, end)
            entry = None
            if dip is None:
                for i in range(i0, i1):
                    if lt[i] > start + 30 * 60000:
                        break
                    q = _price_at(pt, pp, lt[i], 10 * 60000)
                    if q:
                        entry = (lt[i], lp[i], q)
                        break
            else:
                base = _price_at(lt, lp, close_ms, 20 * 60000)
                if not base:
                    continue
                for i in range(i0, i1):
                    if lp[i] <= base * (1 - dip / 100):
                        q = _price_at(pt, pp, lt[i], 10 * 60000)
                        if q:
                            entry = (lt[i], lp[i], q)
                        break
            if not entry:
                continue
            le = _price_at(lt, lp, end, 15 * 60000)
            pe = _price_at(pt, pp, end, 15 * 60000)
            if not le or not pe or entry[0] >= end:
                continue
            r = (le / entry[1] - 1) + sign * ratio * (pe / entry[2] - 1)
            out[sess].append(r * 100 - cost)
    return out


def _day_hold(lead, partner, ratio, sign, rec):
    """The pair held from one 4 pm close to the next, on daily closes (board, else Yahoo)."""
    ps = FUTURES[partner][2] if partner in FUTURES else partner
    a, _ = daily_closes(lead, rec)
    b, _ = daily_closes(ps, rec)
    h, p = returns(a, b)
    c_p = FUT_COST_PCT if partner in FUTURES else DAY_COST_PCT
    return [(x + sign * ratio * y) * 100 - 2 * (DAY_COST_PCT + ratio * c_p) for x, y in zip(h, p)]


def _recorded_days(rec, syms):
    """Dates the board's minute record covers for these symbols."""
    lo, hi = None, None
    for sym in syms:
        ts = rec.get(sym, ([],))[0]
        if ts:
            lo = ts[0] if lo is None else min(lo, ts[0])
            hi = ts[-1] if hi is None else max(hi, ts[-1])
    if lo is None:
        return []
    d, end, out = watch.et(lo).date() - timedelta(days=1), watch.et(hi).date(), []
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _algo_symbols():
    out = set()
    for name in ('ibkr', 'webull'):
        st = _read(os.path.join(ROOT, name + '-algo', 'logs', 'live_status.json'), {})
        if isinstance(st, dict) and st.get('symbol'):
            out.add(str(st['symbol']).upper())
    return out


def idea_leads(rec_syms=None):
    """Funds the ideas start from: what you hold, what the algos trade, and the leveraged funds the board records."""
    leads = set()
    try:
        leads |= {r['sym'] for r in exposure_cached(60)['rows'] if r['kind'] == 'stock'}
    except Exception:
        pass
    leads |= _algo_symbols()
    lev = {s for s, (b, l) in funds().items() if abs(l) >= 2}
    leads |= lev & set(rec_syms or ())
    return sorted(leads)


def _recorded_syms():
    """Every symbol in the board's minute record (from the newest day file of each broker)."""
    out = set()
    for broker in BROKERS:
        folder = os.path.join(DATA, broker + '_ticks')
        try:
            names = sorted(n for n in os.listdir(folder) if n.endswith('.csv'))
        except OSError:
            continue
        for name in names[-2:]:
            try:
                with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as f:
                    for line in f:
                        out.add(line.split(',', 1)[0])
            except OSError:
                pass
    return out


def _moves_with(lead, cands, rec):
    """The bull fund (other than the lead's own family) that moved most like the lead (half-hour correlation >= 0.8)."""
    base = fund_of(lead)[0]
    best, bc = None, 0.8
    hl = half_hours(*rec.get(lead, ([], [])))
    for m in cands:
        if m == lead or fund_of(m)[0] == base or fund_of(m)[1] <= 0:
            continue
        _, c, n = stats(*intraday_returns(hl, half_hours(*rec.get(m, ([], [])))))
        if c is not None and n >= MIN_HALF_HOURS and c > bc:
            best, bc = m, c
    return best, bc


def _rule_text(rule, lead):
    return 'Hold' if RULES[rule] is None else f'Buy {lead} When It Is Down {RULES[rule]:g}% Vs The 4 Pm Close'


def compute_ideas():
    """Every pair idea, backtested on the board's record, ranked by its best session (at least MIN_TRADES trades)."""
    rsyms = _recorded_syms()
    leads = [l for l in idea_leads(rsyms) if l in rsyms]
    plan = []
    for lead in leads:
        cands = [(c, k) for c, k in partner_candidates(lead, 1) if (FUTURES[c][2] if c in FUTURES else c) in rsyms]
        plan.append((lead, cands))
    syms = set(leads) | {FUTURES[c][2] if c in FUTURES else c for _, cs in plan for c, _ in cs}
    syms |= {s for s in rsyms if abs(fund_of(s)[1]) >= 2}
    rec = _minutes(syms)
    days = _recorded_days(rec, leads)
    ideas = []
    for lead, cands in plan:
        mw, mc = _moves_with(lead, [s for s in rsyms if abs(fund_of(s)[1]) >= 2], rec)
        tw = _twin(mw) if mw else None
        if tw and tw in rsyms and tw not in [c for c, _ in cands]:
            cands.append((tw, 'moves_with'))
        for partner, kind in cands:
            st = pair_stats(lead, partner, rec)
            ratio = st.get('ratio')
            if ratio is not None and (ratio > 0) != (expected_sign(lead, partner, rec) > 0):
                continue                                  # moved WITH the fund on the record: not a hedge pair
            if ratio is None:
                bl, bp = eff_beta(lead, 'ndx', rec)[0], eff_beta(FUTURES[partner][0] if partner in FUTURES else partner, 'ndx', rec)[0]
                ratio = -bl / bp if bp else None
            if not ratio:
                continue
            sign = 1.0 if ratio > 0 else -1.0
            if partner not in FUTURES and sign < 0:
                continue                                  # would need a short: never offered
            h = abs(ratio)
            label = KIND_LABEL.get(kind, f'Moves With {mw}' if kind == 'moves_with' else kind)
            if kind == 'moves_with':
                label = f'Inverse Of {mw} (Moves With {lead}, Corr {mc:.2f})'
            day = _metrics(_day_hold(lead, partner, h, sign, rec))
            for rule in RULES:
                per = backtest_pair(lead, partner, rule, h, sign, rec, days)
                sess = {k: _metrics(v) for k, v in per.items()}
                if rule == 'hold':
                    sess['day'] = day
                ok = {k: v for k, v in sess.items() if v.get('n', 0) >= MIN_TRADES and k != 'day'}
                if not ok:
                    continue
                bs = max(ok, key=lambda k: ok[k]['t'])
                verb = 'Buy' if sign > 0 else 'Sell'
                ideas.append({
                    'id': f'{lead}-{partner}-{rule}', 'lead': lead, 'partner': partner, 'kind': 'future' if partner in FUTURES else 'etf',
                    'label': label, 'rule': rule, 'rule_text': _rule_text(rule, lead), 'dollar_ratio': round(h, 3),
                    'partner_side': 'buy' if sign > 0 else 'sell',
                    'title': tc(f"{'hold' if rule == 'hold' else 'buy dips of ' + format(RULES[rule], 'g') + '% in'} {lead}, "
                                f"hedged with {h:.2f} $ of {partner} per $1 ({verb.lower()} {partner})"),
                    'sessions': sess, 'best_session': bs, 'score': ok[bs]['t'],
                    'algos': {'ibkr': True, 'webull': partner not in FUTURES}})
    ideas.sort(key=lambda x: -x['score'])
    top, per_lead = [], {}
    for x in ideas:                               # at most 4 per fund, so one fund can't fill the whole list
        if per_lead.get(x['lead'], 0) < 4:
            top.append(x)
            per_lead[x['lead']] = per_lead.get(x['lead'], 0) + 1
    body = {'at': _now(), 'ideas': top[:30], 'n_tested': len(ideas), 'days': len(days),
            'from': days[0].isoformat() if days else None, 'to': days[-1].isoformat() if days else None,
            'costs': tc(f'{ETF_COST_PCT:g}% per fund fill outside regular hours, {FUT_COST_PCT:g}% per futures fill, '
                        f'{DAY_COST_PCT:g}% per fill for the day hold'),
            'note': tc('research, not advice: past results on a few weeks of the board\'s own record do not predict the future')}
    try:
        with open(IDEAS_PATH + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(body, f)
        os.replace(IDEAS_PATH + '.tmp', IDEAS_PATH)
    except OSError as e:
        print(f'hedge: could not save the ideas: {e!r}', flush=True)
    return body


def ideas_run(background=True):
    with _lock:
        if _ideas['running']:
            return False
        _ideas['running'] = True

    def go():
        try:
            b = compute_ideas()
            with _lock:
                _ideas['body'], _ideas['at'] = b, time.time()
        except Exception as e:
            print(f'hedge: ideas: {e!r}', flush=True)
        finally:
            with _lock:
                _ideas['running'] = False
    if background:
        threading.Thread(target=go, name='hedge-ideas', daemon=True).start()
    else:
        go()
    return True


def ideas():
    with _lock:
        body, running = _ideas['body'], _ideas['running']
    if body is None:
        body = _read(IDEAS_PATH, None)
        if isinstance(body, dict):
            with _lock:
                _ideas['body'] = body
                _ideas['at'] = (body.get('at') or 0) / 1000
    out = dict(body or {'ideas': [], 'at': None})
    out['running'] = running
    return out


def _yaml_str(v):
    return '"' + str(v).replace('\\', '\\\\').replace('"', '\\"') + '"'


def strategy_yaml(idea, session):
    """The idea as a strategy file the algos can backtest (and paper trade), with its hedge: section."""
    code = SESSION_CODE[session]
    lead, partner, rule = idea['lead'], idea['partner'], idea['rule']
    if session == 'afterhours':
        window, exit_ = 'clock_minute_et <= 990', 'clock_session != 4 or clock_minute_et >= 1185'
    elif session == 'premarket':
        window, exit_ = 'clock_minute_et <= 270', 'clock_session != 2 or clock_minute_et >= 555'
    else:
        window = 'clock_minute_et >= 1200 and clock_minute_et <= 1230'
        exit_ = 'clock_session != 1 or (clock_minute_et >= 215 and clock_minute_et < 1200)'
    dip = RULES[rule]
    if dip is None:
        entry = f'clock_session == {code} and {window}'
    else:
        before = {'afterhours': 'clock_minute_et < 1185', 'premarket': 'clock_minute_et < 555',
                  'overnight': '(clock_minute_et >= 1200 or clock_minute_et < 215)'}[session]
        entry = f'clock_session == {code} and vs_close_pct <= -{dip:g} and {before}'
    m = idea['sessions'].get(session) or {}
    name = tc(f"hedge idea: {idea['title']} ({SESSION_LABEL[session]})")
    sess_list = '[' + session + ']'
    return '\n'.join([
        "# Hedge idea from the board's Hedge Ideas list (My Trading tab). RESEARCH, NOT ADVICE.",
        f"# Made {time.strftime('%Y-%m-%d %H:%M')} from the board's own minute record: {SESSION_LABEL[session]}, "
        f"{m.get('n', 0)} trades, win rate {m.get('win', 0)}%, average {m.get('avg', 0)}% per trade (after costs).",
        f"# The algo trades {lead}; the backtest adds the hedge leg ({idea['partner_side']} {partner}) from the hedge: section.",
        "# Paper or live trading hedge only when you turn Auto-Hedge on on the algo tab.",
        f"name: {_yaml_str(name)}",
        f"symbol: {_yaml_str(lead)}",
        "",
        "indicators:",
        "  - kind: clock",
        "  - kind: vs_close",
        "",
        "entry:",
        "  side: buy",
        f"  when: {_yaml_str(entry)}",
        "",
        "exit:",
        f"  when: {_yaml_str(exit_)}",
        "",
        "position_size:",
        "  pct_of_equity: 25",
        "",
        "risk:",
        "  max_order_notional_usd: 5000   # live only: the most one order may be worth",
        "",
        "hedge:",
        f"  symbol: {_yaml_str(partner)}",
        "  ratio_pct: 100",
        f"  dollar_ratio: {idea['dollar_ratio']}",
        f"  side: {idea['partner_side']}",
        "",
        "trading:",
        f"  paper_sessions: {sess_list}",
        "  live_sessions: []",
        "  order_type: limit",
        "",
        "max_open_positions: 1",
        "confirm_live: false",
        ""])


def ideas_send(data):
    import algoctl
    algo = str(data.get('algo') or 'ibkr').lower()
    session = str(data.get('session') or '')
    if algo not in algoctl.DIRS:
        return 400, {'error': 'pick the IBKR or the Webull algo'}
    body = ideas()
    idea = next((i for i in body.get('ideas') or [] if i['id'] == data.get('id')), None)
    if idea is None:
        return 404, {'error': tc('that idea is not on the list any more: press Run Now, then pick it again')}
    if session not in IDEA_SESSIONS:
        session = idea['best_session']
    if algo == 'webull' and idea['kind'] == 'future':
        return 400, {'error': tc('futures hedges are IBKR only: send it to the IBKR algo')}
    if algo == 'webull' and session == 'overnight' and not _webull_overnight():
        return 400, {'error': tc('Webull has no overnight prices on your plan: send overnight ideas to the IBKR algo')}
    safe = ''.join(ch for ch in f"hedge_{idea['lead']}_{idea['partner']}_{idea['rule']}_{session}".lower() if ch.isalnum() or ch == '_')
    fname = safe + '.yaml'
    folder = os.path.join(algoctl.DIRS[algo], 'strategies')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, fname + '.tmp'), 'w', encoding='utf-8') as f:
        f.write(strategy_yaml(idea, session))
    os.replace(os.path.join(folder, fname + '.tmp'), os.path.join(folder, fname))
    code, resp = algoctl.post(algo, {'action': 'backtest', 'strategy': fname, 'recorded': True, 'sessions': [session]})
    r = json.loads(resp) if isinstance(resp, str) else resp
    if code != 200:
        return code, {'error': tc('the strategy file was written but the backtest did not start: ') + str(r.get('error', ''))
                      + f' (strategies/{fname})', 'file': fname}
    return 200, {'ok': True, 'file': fname, 'algo': algo, 'session': session,
                 'text': tc(f"backtest started on the {'IBKR' if algo == 'ibkr' else 'Webull'} algo tab, on the board's own record "
                            f"({SESSION_LABEL[session]}). It shows there when done, with and without the hedge. Strategy file: ")
                 + f'strategies/{fname}'}


# ------------------------------------------------------------------ part 5: Auto-Hedge settings for each algo
LIVE_CONFIRM = 'HEDGE LIVE'
ALGO_FUTURES = ('MNQ', 'MES')


def _algo_dir(name):
    import algoctl
    return algoctl.DIRS.get(name)


def _settings_path(name):
    return os.path.join(_algo_dir(name), 'hedge_settings.json')


def algo_symbol(name):
    """What the algo trades now: its status file (paper or live), else the strategy file it is set to run."""
    d = _algo_dir(name)
    st = _read(os.path.join(d, 'logs', 'live_status.json'), {})
    if isinstance(st, dict) and st.get('symbol'):
        return str(st['symbol']).upper(), st.get('mode') or 'paper'
    try:
        import algoctl
        import yaml
        cur = algoctl._current(name)
        with open(os.path.join(d, 'strategies', cur), encoding='utf-8') as f:
            return str((yaml.safe_load(f) or {}).get('symbol') or '').upper() or None, 'paper'
    except Exception:
        return None, None


def hedge_choices(name, main):
    """The instruments the Auto-Hedge box offers for this algo's symbol, each with its size: $ of the hedge per $1 of
    the position at 100%, from the board's own record (the ratio that cut daily swings most), else from leverage x beta."""
    if not main:
        return []
    out, seen = [], set()
    rec = _minutes({main, 'NQ=F', 'ES=F', 'QQQ', 'SPY'} | {c for c, _ in partner_candidates(main, 1)} |
                   {fund_of(c)[0] for c, _ in partner_candidates(main, 1) if c not in FUTURES})
    for c, kind in partner_candidates(main, 1):
        if c in seen or (c in FUTURES and name != 'ibkr'):
            continue
        seen.add(c)
        st = pair_stats(main, c, rec)
        how = 'board record'
        r = st.get('ratio')
        if r is not None and ((r > 0) != (expected_sign(main, c, rec) > 0) or abs(st.get('corr') or 0) < MIN_HEDGE_CORR):
            r = None                                      # too weak, or moved the same way: size it from leverage x beta
            st = {}
        if r is None:
            bm = eff_beta(main, 'ndx', rec)[0]
            bh = eff_beta(FUTURES[c][0] if c in FUTURES else c, 'ndx', rec)[0]
            r = -bm / bh if bh else None
            how = 'leverage x beta'
        if not r or (c not in FUTURES and r < 0):
            continue
        out.append({'symbol': c, 'kind': 'future' if c in FUTURES else 'etf', 'label': KIND_LABEL.get(kind, kind),
                    'dollar_ratio': round(abs(r), 3), 'side': 'buy' if r > 0 else 'sell', 'how': how,
                    'cut_day': st.get('cut_day'), 'corr': st.get('corr')})
    return out


def _webull_overnight():
    """Does the Webull plan stream the overnight session right now (lib/jb_webull.py overnight_state)? """
    try:
        import jb_webull
        return jb_webull.overnight_state().get('on') is True
    except Exception:
        return False


def _live_lock(name):
    p = os.path.join(_algo_dir(name), 'logs', 'LIVE_RUNNING')
    try:
        return {'pid': open(p).read().strip(), 'mtime': os.stat(p).st_mtime}
    except OSError:
        return None


def algo_get(query):
    import algoctl
    q = {k: (v[0] if isinstance(v, list) else v) for k, v in (query or {}).items()}
    name = str(q.get('name') or '')
    if name not in algoctl.DIRS:
        return 404, {'error': 'no such algo'}
    cfg = _read(_settings_path(name), {})
    cfg = cfg if isinstance(cfg, dict) else {}
    main, mode = algo_symbol(name)
    st = _read(os.path.join(_algo_dir(name), 'logs', 'live_status.json'), {})
    lock, running = _live_lock(name), algoctl._live_running(name)
    live_ok = bool(cfg.get('live_on') and running and lock and isinstance(cfg.get('live_lock'), dict)
                   and str(cfg['live_lock'].get('pid')) == lock['pid'] and abs(float(cfg['live_lock'].get('mtime') or 0) - lock['mtime']) < 2)
    return 200, {'name': name, 'symbol': main, 'mode': mode, 'settings': cfg, 'defaults': {'on': False, 'ratio_pct': 100, 'instrument': 'auto'},
                 'choices': hedge_choices(name, main), 'live_running': running, 'live_ok': live_ok,
                 'status': (st.get('hedge') if isinstance(st, dict) else None), 'confirm_words': LIVE_CONFIRM}


def _write_settings(name, cfg):
    p = _settings_path(name)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=1)
    os.replace(p + '.tmp', p)


def algo_post(data):
    import algoctl
    name = str(data.get('name') or '')
    if name not in algoctl.DIRS:
        return 404, {'error': 'no such algo'}
    cfg = _read(_settings_path(name), {})
    cfg = cfg if isinstance(cfg, dict) else {}
    on = data.get('on') is True
    if not on:
        cfg.update(on=False, live_on=False, live_lock=None, saved_at=_now())
        _write_settings(name, cfg)
        return algo_get({'name': name})
    try:
        ratio = float(data.get('ratio_pct', cfg.get('ratio_pct') or 100))
    except (TypeError, ValueError):
        return 400, {'error': tc('the hedge ratio must be a number (10 to 150%)')}
    if not 10 <= ratio <= 150:
        return 400, {'error': tc('the hedge ratio must be between 10% and 150%')}
    inst = str(data.get('instrument') or cfg.get('instrument') or 'auto').upper()
    if inst in ALGO_FUTURES and name != 'ibkr':
        return 400, {'error': tc('futures hedges are IBKR only: pick a fund for the Webull algo')}
    main, _ = algo_symbol(name)
    if not main:
        return 409, {'error': tc('the algo has not started yet, so the board does not know what it trades: start it first')}
    choices = hedge_choices(name, main)
    etfs = [c for c in choices if c['kind'] == 'etf']
    # auto: the fund that cut this symbol's daily swings most on the board's record (the twin first when there is no record)
    pick = (max(etfs, key=lambda c: c['cut_day'] if c.get('cut_day') is not None else -999 - etfs.index(c)) if etfs else None) \
        if inst == 'AUTO' else next((c for c in choices if c['symbol'] == inst), None)
    if pick is None:
        return 400, {'error': tc(f"{inst.lower() if inst == 'AUTO' else inst} can't hedge {main} here: pick one from the list")}
    live_keep = cfg.get('live_on') and cfg.get('symbol') == pick['symbol'] and cfg.get('for_symbol') == main
    cfg.update(on=True, ratio_pct=ratio, instrument='auto' if inst == 'AUTO' else pick['symbol'], symbol=pick['symbol'],
               for_symbol=main, dollar_ratio=round(pick['dollar_ratio'] * ratio / 100, 4), side=pick['side'],
               mult=FUTURES[pick['symbol']][1] if pick['symbol'] in FUTURES else 1.0, how=pick['how'], saved_at=_now())
    if not live_keep:                                  # another hedge than the one live hedging was confirmed for
        cfg.update(live_on=False, live_lock=None)
    _write_settings(name, cfg)
    return algo_get({'name': name})


def algo_live(data):
    import algoctl
    name = str(data.get('name') or '')
    if name not in algoctl.DIRS:
        return 404, {'error': 'no such algo'}
    cfg = _read(_settings_path(name), {})
    cfg = cfg if isinstance(cfg, dict) else {}
    if data.get('on') is not True:
        cfg.update(live_on=False, live_lock=None, saved_at=_now())
        _write_settings(name, cfg)
        return algo_get({'name': name})
    if not cfg.get('on'):
        return 409, {'error': tc('turn Auto-Hedge on (and try it in paper) before live hedging')}
    if str(data.get('confirm') or '').strip() != LIVE_CONFIRM:
        return 400, {'error': tc(f'type {LIVE_CONFIRM} to turn on live hedging')}
    if not algoctl._live_running(name):
        return 409, {'error': tc('live hedging only works while live trading runs for this algo: press Go Live (or run ') +
                     f'./jb live {name}' + tc(' in Terminal) first')}
    lock = _live_lock(name)
    if not lock:
        return 409, {'error': tc('could not read the live trader\'s LIVE_RUNNING file')}
    cfg.update(live_on=True, live_lock=lock, live_at=_now())
    _write_settings(name, cfg)
    return algo_get({'name': name})

# ------------------------------------------------------------------ background: Yahoo daily closes
def _yahoo_tick():
    """One Yahoo daily-close lookup for a symbol the board has too little history of (free; slow on purpose)."""
    while _want_yahoo:
        sym = _want_yahoo.pop()
        y = _yahoo.get(sym)
        if y and time.time() - y[0] < 6 * 3600:
            continue
        try:
            import portfolio
            rows = portfolio.daily(sym)
            _yahoo[sym] = (time.time(), {d: c for d, c in rows[-260:]} or None)
        except Exception as e:
            _yahoo[sym] = (time.time(), None)
            print(f'hedge: Yahoo daily {sym}: {e!r}', flush=True)
        return True
    return False


def _loop():
    time.sleep(30)
    while True:
        try:
            exposure_cached(0)                    # also lists the symbols Yahoo should fill in
            if time.time() - _ideas['at'] > IDEAS_EVERY_S and not _ideas['running']:
                ideas()                           # the saved list first
                if time.time() - _ideas['at'] > IDEAS_EVERY_S:
                    ideas_run(background=False)
            while _yahoo_tick():
                time.sleep(YAHOO_GAP_S)
        except Exception as e:
            print(f'hedge: {e!r}', flush=True)
        time.sleep(300)


# ------------------------------------------------------------------ plus module hooks
def start():
    if _st['started']:
        return
    _st['started'] = True
    threading.Thread(target=_loop, name='hedge', daemon=True).start()


def get(sub, query):
    sub = (sub or '').strip('/')
    if sub in ('', 'exposure'):
        return 200, json.dumps(exposure_cached())
    q = {k: (v[0] if isinstance(v, list) else v) for k, v in (query or {}).items()}
    if sub == 'calc':
        try:
            pct = float(q.get('pct') or 100)
        except ValueError:
            pct = 100.0
        return 200, json.dumps(calc(str(q.get('idx') or 'ndx'), pct))
    if sub == 'pairs':
        return 200, json.dumps(partners_cached(5 if q.get('fresh') else 300))
    if sub == 'ideas':
        return 200, json.dumps(ideas())
    if sub == 'algo':
        code, body = algo_get(query)
        return code, json.dumps(body)
    return 404, json.dumps({'error': 'unknown'})


def post(sub, data):
    sub = (sub or '').strip('/')
    if sub == 'ideas/run':
        started = ideas_run()
        return 200, json.dumps(dict(ideas(), started=started))
    if sub == 'ideas/send':
        code, body = ideas_send(data or {})
        return code, json.dumps(body)
    if sub in ('algo', 'algo/live'):
        code, body = (algo_post if sub == 'algo' else algo_live)(data or {})
        return code, json.dumps(body)
    return 400, json.dumps({'error': 'unknown action'})
