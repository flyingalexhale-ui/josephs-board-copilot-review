"""Why it's moving, from the board's own math (jb-why, deep search batch C). No outside analysis: only the
board's own minute prices (the 35-day price record and the last 40 hours in memory), the volume the bridges
save (7 days) and Yahoo daily bars the board already reads for the 20/50/200-day averages and the 52-week
range. Nothing here places trades.

  41 Move breakdown     the move since the 4 pm close split into market (the symbol's usual sensitivity
                        to Nasdaq or S&P futures over the last 20 days, times the futures move), sector or
                        index (for 2x/3x funds: leverage x the index, the "3x check") and its own part.
  42 Volume behind it   volume in the last 15 minutes vs the same 15 minutes on past days (overnight vs
                        overnight), the buy/sell split where IBKR has it, and "price up while volume fades".
  43 Key levels         a time-stamped list of what the price just crossed: overnight high/low, yesterday's
                        high/low/close, the session's average price (VWAP), round numbers, the 20/50/200-day
                        averages, the 52-week high/low and the first 15 minutes' range.
  44 Stretch and speed  15-minute move vs normal for this session, distance from the session's average price
                        in normal daily ranges, short-term RSI, and how often stretches like it snapped back.
  45 Who moved first    the last 30 minutes: futures, the fund's big holdings, or the symbol itself.
  46 Holdings driving   each big holding's share of a fund's move (weight x move).
  47 Trend or range     "trending up since 9:05 pm" or "stuck between 52.10 and 53.40 for 3 hours".
  48 Similar setups     past days on the board's own record that looked like now, and how they ended.
  49 Confidence line    one line from the parts above.
  53 Why line           one short line for pushes, tiles, the WHY panel, the phone and the Channel.
  59 Timeframes         trend arrows on 5-minute, hourly, daily and weekly bars.
(58, support / resistance lines and patterns, is drawn on the Chart page itself, in web/why.js.)

  GET /api/plus/why/explain?sym=   everything for one symbol
  GET /api/plus/why/tiles          short version for every symbol on the boards (refreshed each minute)
  GET /api/plus/why/lines?syms=    the why line for some symbols
  GET /api/plus/why/brief          the biggest moves with their why lines (for the Channel)
Files: data/why_stats.json (rebuilt each evening at 5:30 pm ET), data/why_events.json.
"""
import bisect
import glob
import gzip
import os
import threading
import time
from datetime import timedelta

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
STATS = os.path.join(DATA, 'why_stats.json')
EVENTS = os.path.join(DATA, 'why_events.json')
HIST = os.path.join(DATA, 'history')
MIN = 60000
DAY = 86400000
SESSIONS = ('overnight', 'pre-market', 'regular', 'after-hours')
NICE = {'NQ=F': 'Nasdaq futures', 'ES=F': 'S&P futures', 'YM=F': 'Dow futures', 'RTY=F': 'Russell futures',
        'QQQ': 'the Nasdaq-100', 'SPY': 'the S&P 500'}
SHORT = {'Nasdaq-100': 'Nasdaq', 'S&P 500': 'S&P', 'Semiconductors': 'chips', 'Technology': 'tech', 'Financials': 'banks',
         'Biotech': 'biotech', 'Russell 2000': 'small caps', 'Dow': 'the Dow', '20+ yr Treasuries': 'bonds', 'Energy': 'energy',
         'Gold miners': 'gold miners'}

_lock = threading.Lock()
_st = {'started': False, 'stats': {}, 'stats_at': 0, 'building': False, 'tiles': {}, 'tiles_at': 0, 'events': {},
       'seen': {}, 'vol': {}, 'vol_at': 0, 'levels': {}, 'daily_want': set()}


def _now():
    return int(time.time() * 1000)


# ------------------------------------------------------------------ small helpers
def session_of(ms):
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    if 240 <= m < 570:
        return 'pre-market'
    if 570 <= m < 960:
        return 'regular'
    if 960 <= m < 1200:
        return 'after-hours'
    return 'overnight'


def session_start(ms):
    d = watch.et(ms)
    m = d.hour * 60 + d.minute
    day = d.date()
    if m < 240:
        day -= timedelta(days=1)
        return watch.at_et(day, 20, 0)
    for hh, mm, lo in ((20, 0, 1200), (16, 0, 960), (9, 30, 570), (4, 0, 240)):
        if m >= lo:
            return watch.at_et(day, hh, mm)
    return watch.at_et(day, 4, 0)


def hist(sym):
    with watch._lock:
        h = watch._px.get(sym)
        return [(t, p) for t, p in h] if h else []


def at(h, ms):
    """Last price at or before ms from [(t, p)] sorted, else None."""
    i = bisect.bisect_right(h, (ms, float('inf')))
    return h[i - 1][1] if i else None


def pct(a, b):
    return None if a is None or not b else (a - b) / b * 100


def hm(ms):
    d = watch.et(ms)
    return d.strftime('%-I:%M ') + ('am' if d.hour < 12 else 'pm')


def _pairs():
    try:
        import mdata
        return mdata.pairs()
    except Exception:
        return []


def fund_of(sym):
    """-> (index name, base, leverage) for a 2x/3x fund (leverage negative for a bear fund), else None."""
    for name, base, lev, bull, bear in _pairs():
        if sym == bull:
            return name, base, lev
        if sym == bear:
            return name, base, -lev
    return None


def since_close(sym, now):
    h = hist(sym)
    if h and now - h[-1][0] < 15 * MIN:
        r = pct(h[-1][1], at(h, watch.last_close_ms(now)))
        if r is not None:
            return r
    try:
        import edge
        m, _ = edge.since_close(sym)
        return m
    except Exception:
        return None


def bars(h, step_min, t0=None):
    """Closing price per step from minute points: [(bar_start_ms, close)]."""
    out, cur = [], None
    for t, p in h:
        if t0 is not None and t < t0:
            continue
        b = t // (step_min * MIN) * step_min * MIN
        if cur is not None and b == cur:
            out[-1] = (b, p)
        else:
            out.append((b, p))
            cur = b
    return out


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    g = l = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        g += max(d, 0)
        l += max(-d, 0)
    if g + l == 0:
        return 50.0
    return round(100 * g / (g + l), 0)


def _daily(sym):
    """Yahoo daily bars the board already keeps (never asks Yahoo here: a missing symbol is fetched by the loop)."""
    try:
        import analysis
        with analysis._lock:
            hit = analysis._c['daily'].get(sym)
        if hit and hit.get('rows'):
            if time.time() - hit['at'] > 20 * 3600:
                _st['daily_want'].add(sym)
            return hit['rows']
    except Exception:
        pass
    _st['daily_want'].add(sym)
    return []


# ------------------------------------------------------------------ volume (idea 42)
def _volume():
    """{sym: {minute: shares}} from the bridges' 7-day volume record, re-read at most every 3 minutes."""
    if time.time() - _st['vol_at'] < 180 and _st['vol']:
        return _st['vol']
    out = {}
    for b in ('webull', 'ibkr'):               # IBKR last so it wins where both have a symbol
        d = watch._read(os.path.join(DATA, b + '_volume.json'), {})
        if isinstance(d, dict):
            for s, rows in d.items():
                try:
                    out[s] = {int(m): float(v) for m, v in rows}
                except (TypeError, ValueError):
                    pass
    _st['vol'], _st['vol_at'] = out, time.time()
    return out


def volume_view(sym, now, move15=None):
    v = _volume().get(sym)
    if not v:
        return None
    end = now // MIN * MIN
    cur = sum(v.get(end - k * MIN, 0) for k in range(1, 16))
    prev = sum(v.get(end - k * MIN, 0) for k in range(16, 31))
    past = []
    for dd in range(1, 8):
        e = end - dd * DAY
        s = sum(v.get(e - k * MIN, 0) for k in range(1, 16))
        if s > 0:
            past.append(s)
    out = {'now': round(cur), 'normal': None, 'x': None, 'label': '', 'fading': False}
    if past:
        past.sort()
        normal = past[len(past) // 2]
        out['normal'] = round(normal)
        if normal > 0:
            x = cur / normal
            out['x'] = round(x, 1)
            out['label'] = 'heavy' if x >= 2 else 'thin' if x < 0.6 else 'normal'
    if move15 is not None and abs(move15) >= 0.2 and prev > 0 and cur < 0.6 * prev:
        out['fading'] = True
    try:
        import feeds
        f = feeds.flow(sym)
        if f and f.get('buy', 0) + f.get('sell', 0) >= 500:
            out['flow'] = {'buy': f['buy'], 'sell': f['sell'], 'pct': f['pct']}
    except Exception:
        pass
    return out


def vwap(sym, h, t0, now):
    v = _volume().get(sym) or {}
    num = den = 0.0
    for t, p in h:
        if t0 <= t <= now:
            w = v.get(t // MIN * MIN, 0)
            num += p * w
            den += w
    if den > 0:
        return num / den, 'VWAP'
    pts = [p for t, p in h if t0 <= t <= now]
    return (sum(pts) / len(pts), 'session average') if pts else (None, '')


# ------------------------------------------------------------------ the nightly statistics (beta, normal moves, past days)
def _read_day_files(days=35):
    """{sym: [(minute, price)]} from the price record (IBKR over Webull) plus the kept history for older days."""
    cut = time.strftime('%Y-%m-%d', time.gmtime(time.time() - days * 86400))
    merged = {}
    for broker in ('webull', 'ibkr'):
        for p in sorted(glob.glob(os.path.join(DATA, broker + '_ticks', '*.csv'))):
            if os.path.basename(p)[:-4] < cut:
                continue
            try:
                with open(p, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        a = line.split(',')
                        if len(a) == 3:
                            try:
                                merged.setdefault(a[0], {})[int(a[1])] = float(a[2])
                            except ValueError:
                                pass
            except OSError:
                pass
    return {s: sorted(d.items()) for s, d in merged.items()}


def _returns(series, step=5):
    """{bar_start: % return over the bar} on a 5-minute grid, only where both ends exist within the bar."""
    b = bars(series, step)
    out = {}
    for (t0, p0), (t1, p1) in zip(b, b[1:]):
        if t1 - t0 == step * MIN and p0:
            out[t1] = (p1 - p0) / p0 * 100
    return out


def _beta(a, b):
    keys = [k for k in a if k in b]
    if len(keys) < 200:
        return None, None
    xa = [a[k] for k in keys]
    xb = [b[k] for k in keys]
    ma, mb = sum(xa) / len(xa), sum(xb) / len(xb)
    vb = sum((x - mb) ** 2 for x in xb)
    va = sum((x - ma) ** 2 for x in xa)
    if not vb or not va:
        return None, None
    cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb))
    return round(cov / vb, 2), round(cov * cov / (va * vb), 2)


def build_stats():
    """Once a day: each symbol's sensitivity to the futures, its normal moves per session, its normal daily range,
    how often stretches snapped back, and a day-by-day table for similar setups."""
    if _st['building']:
        return
    _st['building'] = True
    try:
        data = _read_day_files(35)
        rets = {}
        for m in ('NQ=F', 'ES=F', 'QQQ', 'SPY'):
            if m in data:
                rets[m] = _returns(data[m])
        stats = {}
        for sym, s in data.items():
            if len(s) < 600:
                continue
            r5 = _returns(s)
            st = {'n': len(s)}
            best = None
            for m in ('NQ=F', 'ES=F', 'QQQ', 'SPY'):
                if m == sym or m not in rets:
                    continue
                b, r2 = _beta(r5, rets[m])
                if b is not None and (best is None or r2 > best[2]):
                    best = (m, b, r2)
            if best:
                st['mkt'], st['beta'], st['r2'] = best
            # normal 15-minute and 1-hour moves per session (median of absolute moves)
            b15 = bars(s, 15)
            per = {k: [] for k in SESSIONS}
            for (t0, p0), (t1, p1) in zip(b15, b15[1:]):
                if t1 - t0 == 15 * MIN and p0:
                    per[session_of(t1 - 1)].append(abs(p1 - p0) / p0 * 100)
            st['n15'] = {k: round(sorted(v)[len(v) // 2], 3) for k, v in per.items() if len(v) >= 20}
            b60 = bars(s, 60)
            h1 = [abs(p1 - p0) / p0 * 100 for (t0, p0), (t1, p1) in zip(b60, b60[1:]) if t1 - t0 == 60 * MIN and p0]
            if len(h1) >= 20:
                st['n60'] = round(sorted(h1)[len(h1) // 2], 3)
            # normal daily range (high - low over each 8 pm -> 8 pm trading day, as % of the price)
            days = {}
            for t, p in s:
                k = watch.et(t + 4 * 3600000).date().isoformat()
                lo, hi = days.get(k, (p, p))
                days[k] = (min(lo, p), max(hi, p))
            rng = sorted((hi - lo) / lo * 100 for lo, hi in days.values() if lo > 0)
            if len(rng) >= 5:
                st['range'] = round(rng[len(rng) // 2], 2)
            # stretch snap-backs: half a normal daily range away from the 60-minute average, back a third within the hour
            if st.get('range'):
                b5 = bars(s, 5)
                n = back = 0
                skip_until = 0
                for i in range(12, len(b5) - 12):
                    t, p = b5[i]
                    if t < skip_until:
                        continue
                    avg = sum(x[1] for x in b5[i - 12:i]) / 12
                    d = (p - avg) / avg * 100
                    if abs(d) >= st['range'] / 2:
                        n += 1
                        later = [x[1] for x in b5[i + 1:i + 13]]
                        if later and any(abs((q - avg) / avg * 100) <= abs(d) * 2 / 3 for q in later):
                            back += 1
                        skip_until = t + 60 * MIN
                if n >= 3:
                    st['snap'] = [back, n]
            stats[sym] = st
        stats['_built'] = _now()
        watch._write(STATS, stats)
        with _lock:
            _st['stats'], _st['stats_at'] = stats, _now()
        print(f'why: statistics rebuilt for {len(stats) - 1} symbols', flush=True)
    finally:
        _st['building'] = False


def stats(sym):
    if not _st['stats']:
        v = watch._read(STATS, {})
        if isinstance(v, dict):
            _st['stats'], _st['stats_at'] = v, v.get('_built', 0)
    return _st['stats'].get(sym) or {}


# ------------------------------------------------------------------ similar setups (idea 48)
def _day_series(sym):
    """Minute prices for the last 35 days plus the kept history outside regular hours (idea 5), for one symbol."""
    pts = {}
    for p in sorted(glob.glob(os.path.join(HIST, '*', '*.csv.gz'))):
        try:
            with gzip.open(p, 'rt', encoding='utf-8', errors='replace') as g:
                for line in g:
                    if line.startswith(sym + ','):
                        a = line.strip().split(',')
                        try:
                            pts[int(a[1])] = float(a[2])
                        except (ValueError, IndexError):
                            pass
        except OSError:
            pass
    for broker in ('webull', 'ibkr'):
        for p in sorted(glob.glob(os.path.join(DATA, broker + '_ticks', '*.csv'))):
            try:
                with open(p, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        if line.startswith(sym + ','):
                            a = line.split(',')
                            try:
                                pts[int(a[1])] = float(a[2])
                            except (ValueError, IndexError):
                                pass
            except OSError:
                pass
    return sorted(pts.items())


_sim_cache = {}


def similar(sym, now, move):
    """'The last 12 times TQQQ was up 2%+ vs the 4 pm close by this time of night, it held into the open 8 times.'"""
    if move is None or abs(move) < 0.5:
        return None
    key = (sym, now // (30 * MIN), round(move * 4) / 4)
    if key in _sim_cache:
        return _sim_cache[key]
    s = _sim_cache.get(('series', sym))
    if not s or time.time() - s[0] > 6 * 3600:
        s = (time.time(), _day_series(sym))
        _sim_cache[('series', sym)] = s
    series = s[1]
    if len(series) < 1000:
        return None
    d = watch.et(now)
    tod = d.hour * 60 + d.minute
    pre_open = session_of(now) != 'regular'
    close_now = watch.last_close_ms(now)
    n = held = 0
    for k in range(1, 400):
        c = close_now - k * DAY
        cd = watch.et(c)
        if cd.weekday() >= 5:
            continue
        if c < series[0][0]:
            break
        p0 = at(series, c)
        if not p0:
            continue
        # the same clock time after that close
        tt = c + ((tod - 960) % 1440) * MIN
        if watch.et(tt).weekday() >= 5 and not (watch.et(tt).weekday() == 6 and watch.et(tt).hour >= 18):
            continue
        i = bisect.bisect_right(series, (tt, float('inf')))
        if not i or tt - series[i - 1][0] > 30 * MIN:
            continue                                  # no price near that time (weekend, holiday, gap in the record)
        p1 = series[i - 1][1]
        m = (p1 - p0) / p0 * 100
        if m * move <= 0 or abs(m) < abs(move) * 0.75:
            continue
        nd = watch.et(tt).date()
        if pre_open:
            while watch.et(watch.at_et(nd, 9, 29)).weekday() >= 5 or watch.at_et(nd, 9, 29) < tt:
                nd += timedelta(days=1)
            end = watch.at_et(nd, 9, 29)
        else:
            end = watch.at_et(nd, 16, 0)
        p2 = at(series, end)
        if not p2 or end > now:
            continue
        n += 1
        m2 = (p2 - p0) / p0 * 100
        if m2 * move > 0 and abs(m2) >= abs(m) / 2:
            held += 1
    out = None
    if n >= 4:
        out = {'n': n, 'held': held, 'end': 'the open' if pre_open else 'the close',
               'text': f"The last {n} times {sym} was {'up' if move > 0 else 'down'} {abs(move) * 0.75:.1f}%+ vs the 4 pm close by "
                       f"{hm(now)}, it held at least half of it into {'the open' if pre_open else 'the close'} {held} times."}
    _sim_cache[key] = out
    if len(_sim_cache) > 400:
        for k in list(_sim_cache)[:200]:
            if k[0] != 'series':
                _sim_cache.pop(k, None)
    return out


# ------------------------------------------------------------------ key levels and their crossings (idea 43)
def levels(sym, now, h=None):
    h = h if h is not None else hist(sym)
    out = []
    if not h:
        return out
    s, e = watch.overnight_window(now)
    if s <= now:
        on = [p for t, p in h if s <= t < min(e, now)]
        if len(on) > 5:
            out += [('overnight high', max(on)), ('overnight low', min(on))]
    rows = _daily(sym)
    today = watch.et(now).date().isoformat()
    past = [r for r in rows if r[0] < today]
    if past:
        y = past[-1]
        out += [("yesterday's high", y[2]), ("yesterday's low", y[3]), ("yesterday's close", y[4])]
        closes = [r[4] for r in past]
        for n in (20, 50, 200):
            if len(closes) >= n:
                out.append((f'{n}-day average', sum(closes[-n:]) / n))
        if len(past) >= 200:
            out += [('52-week high', max(r[2] for r in past[-252:])), ('52-week low', min(r[3] for r in past[-252:]))]
    d = watch.et(now)
    o = watch.at_et(d.date(), 9, 30)
    if now > o + 15 * MIN and session_of(now) in ('regular', 'after-hours'):
        orng = [p for t, p in h if o <= t < o + 15 * MIN]
        if orng:
            out += [('opening-range high', max(orng)), ('opening-range low', min(orng))]
    v, name = vwap(sym, h, session_start(now), now)
    if v:
        out.append((name, v))
    p = h[-1][1]
    step = 1 if p < 20 else 5 if p < 100 else 10 if p < 500 else 50
    r = round(p / step) * step
    for x in (r - step, r, r + step):
        if x > 0:
            out.append((f'${x:g}', x))
    return out


def _scan_crossings(sym, now):
    """New minutes since the last look: record each level the price crossed."""
    h = hist(sym)
    if len(h) < 3:
        return
    last_t = _st['seen'].get(sym, h[-1][0] - 10 * MIN)
    new = [x for x in h if x[0] > last_t]
    if not new:
        return
    _st['seen'][sym] = new[-1][0]
    if len(new) > 15:
        new = new[-15:]
    i0 = bisect.bisect_left(h, new[0])
    prev = h[i0 - 1][1] if i0 else None
    if prev is None:
        return
    lv = levels(sym, now, h[:i0])                   # the levels as they stood before these minutes
    ev = _st['events'].setdefault(sym, [])
    for t, p in new:
        for name, x in lv:
            if prev < x <= p or prev > x >= p:
                up = p > prev
                if 'high' in name and not up or 'low' in name and up:
                    continue                         # a high is "broken" going up, a low going down
                if any(e['name'] == name and t - e['t'] < 30 * MIN for e in ev):
                    continue
                vol = (volume_view(sym, t + MIN) or {}).get('label') or ''
                verb = ('broke above' if up else 'fell below') if ('average' in name or 'VWAP' in name or name.startswith('$')) else 'broke'
                if 'close' in name:
                    verb = 'crossed above' if up else 'crossed below'
                nm = name if name.startswith(('$', 'yesterday')) else 'the ' + name
                ev.append({'t': t, 'name': name, 'level': round(x, 4), 'up': up, 'vol': vol,
                           'text': f"{hm(t)} {verb} {nm} ({x:.2f})" + (f' on {vol} volume' if vol in ('heavy', 'thin') else '')})
        prev = p
    del ev[:-25]


# ------------------------------------------------------------------ who moved first (45), holdings (46), trend (47), timeframes (59)
def _first_move(h, t0, direction, frac=0.5):
    pts = [(t, p) for t, p in h if t >= t0]
    if len(pts) < 5:
        return None, 0
    p0 = pts[0][1]
    tot = (pts[-1][1] - p0) / p0 * 100
    if tot * direction <= 0:
        return None, tot
    for t, p in pts:
        if ((p - p0) / p0 * 100) * direction >= abs(tot) * frac:
            return t, tot
    return None, tot


def leader(sym, now, mkt):
    h = hist(sym)
    t0 = now - 30 * MIN
    pts = [p for t, p in h if t >= t0]
    if len(pts) < 10:
        return None
    m = (pts[-1] - pts[0]) / pts[0] * 100
    if abs(m) < 0.3:
        return None
    d = 1 if m > 0 else -1
    me, _ = _first_move(h, t0, d)
    cands = []
    f = fund_of(sym)
    if mkt:
        cands.append((mkt, NICE.get(mkt, mkt), d * (-1 if f and f[2] < 0 else 1)))
    if f:
        try:
            import edge
            for r in edge.holdings(f[1])[:6]:
                cands.append((r['sym'], r['sym'], d * (-1 if f[2] < 0 else 1)))
        except Exception:
            pass
        cands.append((f[1], f[1], d * (-1 if f[2] < 0 else 1)))
    best = None
    for s, name, dd in cands:
        hs = hist(s)
        if not hs or now - hs[-1][0] > 10 * MIN:
            continue
        t, tot = _first_move(hs, t0, dd)
        if t and me and t <= me - 2 * MIN and abs(tot) >= 0.1 and (not best or t < best[1]):
            best = (name, t)
    if best:
        mins = round((me - best[1]) / MIN)
        return {'who': best[0], 'mins': mins, 'text': f"{best[0]} started moving {mins} minutes before {sym}."}
    if me:
        return {'who': sym, 'mins': 0, 'text': f"{sym} moved first: futures and its big holdings followed or stayed put."}
    return None


def drivers(sym):
    f = fund_of(sym)
    base = f[1] if f else sym
    try:
        import edge
        if not edge.holdings(base):
            return None
        v = edge.holdings_view(sym)
    except Exception:
        return None
    rows = [r for r in v.get('rows') or [] if r.get('contrib') is not None]
    tot = sum(r['contrib'] for r in rows)
    if not rows or abs(tot) < 0.05:
        return None
    same = sorted((r for r in rows if r['contrib'] * tot > 0), key=lambda r: -abs(r['contrib']))
    top = same[:2]
    share = round(sum(r['contrib'] for r in top) / tot * 100) if top else 0
    return {'rows': [{'sym': r['sym'], 'w': r['w'], 'move': r['move'], 'contrib': r['contrib']} for r in rows[:10]],
            'text': (f"{' and '.join(r['sym'] for r in top)} explain about {min(share, 100)}% of {base}'s move "
                     f"({'+' if tot > 0 else ''}{tot:.2f}% from its top holdings).") if top and share >= 25 else ''}


def trend(sym, now, st, h=None):
    h = h if h is not None else hist(sym)
    b = [x for x in bars(h, 5) if x[0] >= now - 3 * 3600000]
    if len(b) < 8:
        return None
    ps = [p for _, p in b]
    path = sum(abs(y - x) for x, y in zip(ps, ps[1:]))
    net = ps[-1] - ps[0]
    er = abs(net) / path if path else 0
    n60 = (st.get('n60') or 0.6) / 100
    if er >= 0.4 and abs(net) / ps[0] >= n60:
        if net > 0:
            i = min(range(len(ps)), key=lambda k: ps[k])
            return {'kind': 'up', 'text': f"trending up since {hm(b[i][0])}"}
        i = max(range(len(ps)), key=lambda k: ps[k])
        return {'kind': 'down', 'text': f"trending down since {hm(b[i][0])}"}
    # how long has it stayed inside a tight band (about 1.5 normal hourly moves)?
    lo = hi = ps[-1]
    k = len(b) - 1
    while k > 0:
        lo2, hi2 = min(lo, ps[k - 1]), max(hi, ps[k - 1])
        if (hi2 - lo2) / lo2 > 1.5 * n60:
            break
        lo, hi, k = lo2, hi2, k - 1
    mins = (b[-1][0] - b[k][0]) / MIN
    if mins >= 60:
        hrs = mins / 60
        return {'kind': 'range', 'lo': lo, 'hi': hi,
                'text': f"stuck between {lo:.2f} and {hi:.2f} for {('about ' + str(round(hrs)) + ' hours') if hrs >= 1.75 else 'an hour'}"}
    return {'kind': 'chop', 'text': 'chopping back and forth'}


def _slope_sign(ps, thr):
    if len(ps) < 3:
        return 'flat'
    n = len(ps)
    mx = (n - 1) / 2
    my = sum(ps) / n
    sl = sum((i - mx) * (p - my) for i, p in enumerate(ps)) / sum((i - mx) ** 2 for i in range(n))
    ch = sl * (n - 1) / my * 100
    return 'up' if ch > thr else 'down' if ch < -thr else 'flat'


def timeframes(sym, now, st, h=None):
    h = h if h is not None else hist(sym)
    out = {}
    b5 = [p for t, p in bars(h, 5) if t >= now - 3600000]
    out['5m'] = _slope_sign(b5, max(0.15, (st.get('n15') or {}).get(session_of(now), 0.15)))
    b60 = [p for t, p in bars(h, 60) if t >= now - 8 * 3600000]
    out['1h'] = _slope_sign(b60, max(0.3, (st.get('n60') or 0.3) * 1.5))
    rows = _daily(sym)
    closes = [r[4] for r in rows]
    if h:
        closes = closes + [h[-1][1]]
    rng = st.get('range') or 1.5
    out['D'] = _slope_sign(closes[-6:], rng) if len(closes) >= 6 else 'flat'
    wk = closes[-21::5] if len(closes) >= 21 else []
    out['W'] = _slope_sign(wk, rng * 2) if wk else 'flat'
    agree = len({v for v in out.values() if v != 'flat'}) == 1 and all(v != 'flat' for v in out.values())
    return {'arrows': out, 'agree': agree}


# ------------------------------------------------------------------ the full explanation
def explain(sym, now=None, light=False):
    now = now or _now()
    sym = sym.upper()
    h = hist(sym)
    st = stats(sym)
    out = {'sym': sym, 'at': now, 'session': session_of(now)}
    if not h or now - h[-1][0] > 30 * MIN:
        out['note'] = f'No fresh board price for {sym} right now, so the board can\'t explain its move.'
        return out
    last = h[-1][1]
    move = pct(last, at(h, watch.last_close_ms(now)))
    m15 = pct(last, at(h, now - 15 * MIN))
    out.update(last=last, move=None if move is None else round(move, 2), move15=None if m15 is None else round(m15, 2))
    # 41 breakdown
    f = fund_of(sym)
    mkt = st.get('mkt') or ('NQ=F' if hist('NQ=F') else 'ES=F' if hist('ES=F') else None)
    beta = st.get('beta')
    mm = since_close(mkt, now) if mkt else None
    bd = None
    if move is not None and mm is not None:
        if f:
            name, base, lev = f
            bm = since_close(base, now)
            b_beta = stats(base).get('beta') or 1.0
            mpart = (beta if beta is not None else lev * b_beta) * mm
            if bm is not None:
                idx = lev * bm
                bd = {'market': round(mpart, 2), 'sector': round(idx - mpart, 2), 'own': round(move - idx, 2), 'sector_name': SHORT.get(name, name),
                      'check': round(move / bm, 1) if abs(bm) >= 0.15 else None, 'lev': lev, 'base': base, 'base_move': round(bm, 2)}
            else:
                bd = {'market': round(mpart, 2), 'sector': 0.0, 'own': round(move - mpart, 2), 'sector_name': SHORT.get(name, name), 'lev': lev}
        else:
            b = beta if beta is not None else 1.0
            mpart = b * mm
            sec_m, sec_name = None, ''
            try:
                import signals
                sec_name = ((watch._read(signals.STATE, {}).get('sectors') or {}).get(sym) or {}).get('sector') or ''
                etf = signals.SECTOR_ETF.get(sec_name)
                if etf and etf != sym:
                    sec_m = since_close(etf, now)
            except Exception:
                pass
            spart = (sec_m - mpart) if sec_m is not None and sym not in ('QQQ', 'SPY', 'NQ=F', 'ES=F') else 0.0
            if sym in (mkt, 'QQQ', 'SPY') or sym.endswith('=F'):
                mpart, spart = move, 0.0
            bd = {'market': round(mpart, 2), 'sector': round(spart, 2), 'own': round(move - mpart - spart, 2), 'sector_name': sec_name.lower()}
        bd['mkt'], bd['mkt_name'], bd['mkt_move'], bd['beta'] = mkt, NICE.get(mkt, mkt), round(mm, 2), beta
    out['breakdown'] = bd
    # 42 volume
    out['volume'] = volume_view(sym, now, m15)
    # 43 levels
    out['events'] = list(reversed(_st['events'].get(sym, [])[-8:]))
    # 44 stretch and speed
    n15 = (st.get('n15') or {}).get(session_of(now))
    v, vname = vwap(sym, h, session_start(now), now)
    b5 = [p for t, p in bars(h, 5)]
    r = rsi(b5)
    sx = {'speed': round(abs(m15) / n15, 1) if n15 and m15 is not None else None, 'rsi': r}
    if v and st.get('range'):
        sx['dist'] = round((last - v) / v * 100 / st['range'], 2)
        sx['from'] = vname
    label = ''
    if sx['speed'] and sx['speed'] >= 3:
        label = f"running hot (a {sx['speed']:.0f}x-normal 15-minute move)"
    elif (sx.get('dist') is not None and abs(sx['dist']) >= 0.5) or (r is not None and (r >= 75 or r <= 25)):
        label = 'stretched ' + ('above' if (sx.get('dist') or (r or 50) - 50) > 0 else 'below') + f" its {vname or 'average'}"
        if st.get('snap'):
            b_, n_ = st['snap']
            label += f"; stretches like this snapped back within the hour {b_} of {n_} times on this board"
    sx['label'] = label
    out['stretch'] = sx
    # 47 trend, 59 timeframes
    out['trend'] = trend(sym, now, st, h)
    out['tf'] = timeframes(sym, now, st, h)
    if not light:
        out['leader'] = leader(sym, now, mkt)
        out['drivers'] = drivers(sym) if f or sym in [p[1] for p in _pairs()] else None
        out['similar'] = similar(sym, now, move)
    if out['trend'] and out['trend']['kind'] in ('up', 'down') and not sx['label']:
        sx['label'] = 'steady trend'
    elif out['trend'] and out['trend']['kind'] in ('range', 'chop') and not sx['label']:
        sx['label'] = 'chopping'
    out['confidence'] = confidence(out)
    out['line'] = why_line(out)
    return out


def confidence(o):
    bd, vol, sx = o.get('breakdown'), o.get('volume') or {}, o.get('stretch') or {}
    mv = o.get('move')
    if mv is None or abs(mv) < 0.3:
        return {'level': 'quiet', 'text': 'Quiet: not much of a move to explain.'}
    good, bad = [], []
    if bd:
        broad = (bd['market'] + bd['sector']) * mv > 0 and abs(bd['market'] + bd['sector']) >= abs(mv) * 0.5
        if broad:
            good.append('market and sector agree' if abs(bd['sector']) >= 0.2 and bd['sector'] * mv > 0 else 'moving with the market')
        elif abs(bd['own']) >= abs(mv) * 0.6:
            bad.append('a one-stock move' if not o.get('sym', '').endswith('=F') else 'its own move')
    if vol.get('label') == 'heavy':
        good.append('heavy volume')
    elif vol.get('label') == 'thin':
        bad.append('thin volume')
    if vol.get('fading'):
        bad.append('volume fading')
    recent = [e for e in o.get('events') or [] if o['at'] - e['t'] < 60 * MIN and (e['up'] == (mv > 0))]
    if recent:
        good.append('broke a key level')
    if sx.get('label', '').startswith(('stretched', 'running hot')):
        bad.append('stretched')
    score = len(good) - len(bad)
    level = 'strong' if score >= 2 else 'weak' if score <= -1 else 'moderate'
    head = {'strong': 'Strong, broad move' if 'market and sector agree' in good or 'moving with the market' in good else 'Strong move',
            'moderate': 'Mixed', 'weak': 'Weak'}[level]
    bits = good + bad
    return {'level': level, 'text': head + (': ' + ', '.join(bits) if bits else '') + '.'}


def why_line(o):
    """'chips leading (sector), heavy volume (3x normal), broke the overnight high at 2:14 am'"""
    mv = o.get('move')
    if mv is None:
        return ''
    bits = []
    bd = o.get('breakdown')
    if bd:
        parts = [('market', bd['market']), ('sector', bd['sector']), ('own', bd['own'])]
        lead = max(parts, key=lambda x: x[1] * (1 if mv > 0 else -1))
        if abs(mv) >= 0.3 and lead[1] * mv > 0:
            if lead[0] == 'market':
                bits.append(f"moving with {bd['mkt_name']} ({bd['mkt_move']:+.1f}%)")
            elif lead[0] == 'sector' and bd.get('sector_name'):
                bits.append(f"{bd['sector_name']} leading")
            else:
                bits.append('its own move, not the market' if not o['sym'].endswith('=F') else 'its own move')
        if bd.get('check') is not None and bd.get('lev') and abs(bd['check'] - bd['lev']) >= max(0.6, abs(bd['lev']) * 0.25) and abs(mv) >= 0.5:
            bits.append(f"moving {bd['check']:+.1f}x its index (a {bd['lev']:+d}x fund)")
    v = o.get('volume') or {}
    if v.get('label') == 'heavy':
        bits.append(f"heavy volume ({v['x']:g}x normal)")
    elif v.get('label') == 'thin':
        bits.append('thin volume, could reverse')
    if v.get('fading'):
        bits.append('volume fading')
    ev = [e for e in o.get('events') or [] if o['at'] - e['t'] < 90 * MIN]
    if ev:
        e = ev[0]
        bits.append(e['text'].split(' ', 2)[2].split(' (')[0] + ' at ' + ' '.join(e['text'].split(' ')[:2]))
    sx = (o.get('stretch') or {}).get('label') or ''
    if sx.startswith(('stretched', 'running hot')):
        bits.append(sx.split(';')[0])
    t = o.get('trend')
    if t and t['kind'] in ('up', 'down') and len(bits) < 4:
        bits.append(t['text'])
    return ', '.join(bits[:4])


def line(sym):
    """The why line for pushes (empty when the board can't say)."""
    try:
        o = explain(sym, light=True)
        return o.get('line') or ''
    except Exception:
        return ''


def tile(sym, now):
    o = explain(sym, now, light=True)
    if 'note' in o:
        return None
    bd = o.get('breakdown') or {}
    ev = (o.get('events') or [None])[0]
    return {'line': o.get('line'), 'move': o.get('move'), 'bar': {k: bd.get(k) for k in ('market', 'sector', 'own')} if bd else None,
            'sector_name': bd.get('sector_name', ''), 'vol': {k: (o.get('volume') or {}).get(k) for k in ('label', 'x')},
            'level': ev if ev and now - ev['t'] < 3 * 3600000 else None, 'tf': o['tf'], 'trend': o.get('trend'),
            'stretch': o['stretch'].get('label'), 'conf': o['confidence']['level']}


# ------------------------------------------------------------------ loop
def _board_syms():
    with watch._lock:
        return [s for s, h in watch._px.items() if h and _now() - h[-1][0] < 30 * MIN]


def _loop():
    time.sleep(40)
    ev = watch._read(EVENTS, {})
    if isinstance(ev, dict):
        _st['events'] = {s: [e for e in v if _now() - e['t'] < 2 * DAY] for s, v in ev.items() if isinstance(v, list)}
    stats('')
    n = 0
    while True:
        now = _now()
        try:
            d = watch.et(now)
            if now - (_st['stats_at'] or 0) > 20 * 3600000 and not (d.hour == 9 and 25 <= d.minute <= 45):
                threading.Thread(target=build_stats, name='why-stats', daemon=True).start()
                _st['stats_at'] = now                           # don't start another while it runs
            elif d.hour == 17 and 30 <= d.minute < 32 and now - _st['stats_at'] > 3600000:
                threading.Thread(target=build_stats, name='why-stats', daemon=True).start()
                _st['stats_at'] = now
            syms = _board_syms()
            for s in syms:
                try:
                    _scan_crossings(s, now)
                except Exception as e:
                    print(f'why: levels {s}: {e!r}', flush=True)
            tiles = {}
            for s in syms:
                try:
                    t = tile(s, now)
                    if t:
                        tiles[s] = t
                except Exception as e:
                    print(f'why: {s}: {e!r}', flush=True)
            with _lock:
                _st['tiles'], _st['tiles_at'] = tiles, now
            if n % 5 == 0:
                watch._write(EVENTS, _st['events'])
            if _st['daily_want'] and n % 2 == 0:              # one Yahoo daily lookup every 2 minutes at most
                s = _st['daily_want'].pop()
                try:
                    import analysis
                    analysis.daily(s)
                except Exception:
                    pass
        except Exception as e:
            print(f'why: {e!r}', flush=True)
        n += 1
        time.sleep(60)


def start():
    if _st['started']:
        return
    _st['started'] = True
    threading.Thread(target=_loop, name='why', daemon=True).start()


def _q(query, k):
    v = (query or {}).get(k)
    if isinstance(v, list):
        v = v[0] if v else ''
    return str(v or '')


def _clean(s):
    return ''.join(ch for ch in s.upper() if ch.isalnum() or ch in '.=^-')[:12]


def get(sub, query):
    if sub == 'explain':
        sym = _clean(_q(query, 'sym'))
        if not sym:
            return 400, {'error': 'Pick a symbol.'}
        return 200, explain(sym)
    if sub == 'tiles':
        with _lock:
            return 200, {'at': _st['tiles_at'], 'tiles': _st['tiles']}
    if sub == 'lines':
        out = {}
        for s in [_clean(x) for x in _q(query, 'syms').split(',')][:40]:
            if s:
                t = _st['tiles'].get(s)
                out[s] = t['line'] if t else line(s)
        return 200, {'lines': out}
    if sub == 'brief':
        with _lock:
            t = dict(_st['tiles'])
        rows = sorted(({'sym': s, 'move': v['move'], 'line': v['line'], 'conf': v['conf']} for s, v in t.items() if v.get('move') is not None),
                      key=lambda r: -abs(r['move']))[:10]
        return 200, {'at': _st['tiles_at'], 'rows': rows}
    if sub == 'levels':
        sym = _clean(_q(query, 'sym'))
        return 200, {'sym': sym, 'levels': [{'name': n, 'p': round(x, 4)} for n, x in levels(sym, _now())], 'events': list(reversed(_st['events'].get(sym, [])))}
    return 404, {'error': 'not found'}
