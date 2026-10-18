"""Report Card replay (jb-reportcard): re-runs the board's price-only features on up to 2 years of
past free Yahoo prices, so they have a grade from day one. Runs in the background inside the
gateway once a day (evenings) and takes several minutes; writes data/reportcard_replay.json.

What is replayed, and how close it is to the live feature:
  Scanner Picks + each signal   exact: the scanner's own rules (mover-scanner/scanner/picks.py) on
                                each past day's daily chart, graded vs SPY 1, 5 and 20 days later
  Futures mood meter            the meter's own formula on hourly futures prices (so a reading at a
                                9:30 open uses the 10:00 price; live grades use 5-minute prices)
  Overnight mood                the World mood formula on hourly prices; Asia and Europe use their
                                24-hour change
  Gap scorecard                 its "gaps like this" guess from the year before each gap
  4 am gap list                 the 4-5 am hourly price vs the 4 pm close, 2%+ gaps
  Crossed yesterday high / low  daily highs and lows
  Relative strength             3 strongest / weakest of your symbols vs SPY over 21 days
  Market weather                sector funds at 8 am vs the 4 pm close
  Event days                    Fed decisions and the monthly jobs report (first Friday, approximate)
Not replayable for free (no past data): Picks mood, MACRO, sentiment, alerts that need live volume
or chatter, earnings expected moves (old option prices cost money), options, politicians.
"""
import json
import math
import os
import re
import sys
import time
import urllib.parse
from datetime import date, timedelta

import watch
import reportcard as rc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(rc.DATA, 'reportcard_replay.json')
FOMC_PAST = ['2024-11-07', '2024-12-18', '2025-01-29', '2025-03-19', '2025-05-07', '2025-06-18', '2025-07-30',
             '2025-09-17', '2025-10-29', '2025-12-10']


class Busy(Exception):
    pass


def _get(url):
    import world
    if time.time() < (world._status.get('wait_until') or 0):
        raise Busy('Yahoo asked the board to slow down')
    try:
        body = world._get(url, timeout=25)
    except Exception as e:
        if '429' in str(e) or 'Too Many' in str(e):
            raise Busy('Yahoo asked the board to slow down')
        raise
    time.sleep(2.0)
    return ((body.get('chart') or {}).get('result') or [None])[0] or {}


def daily(sym, rng='2y'):
    """[{'date','open','high','low','close','volume'}] oldest first."""
    import world
    res = _get(f'https://{world.HOSTS[0]}/v8/finance/chart/{urllib.parse.quote(rc._ysym(sym))}?range={rng}&interval=1d')
    q = ((res.get('indicators') or {}).get('quote') or [{}])[0]
    out = []
    for i, t in enumerate(res.get('timestamp') or []):
        try:
            o, h, l, c, v = (q.get(k)[i] for k in ('open', 'high', 'low', 'close', 'volume'))
        except Exception:
            continue
        if None in (o, h, l, c):
            continue
        out.append({'date': watch.et(int(t) * 1000).date().isoformat(), 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v or 0})
    return out


def hourly(sym):
    import world
    res = _get(f'https://{world.HOSTS[0]}/v8/finance/chart/{urllib.parse.quote(rc._ysym(sym))}?range=730d&interval=1h&includePrePost=true')
    q = ((res.get('indicators') or {}).get('quote') or [{}])[0]
    return [(int(t) * 1000, float(o if o is not None else c), float(c))
            for t, o, c in zip(res.get('timestamp') or [], q.get('open') or [], q.get('close') or []) if c is not None]


class Px:
    """Price at a moment from hourly bars, with a pointer that moves forward (fast for long loops)."""
    def __init__(self, bars):
        self.b = bars
        self.t = [x[0] for x in bars]

    def at(self, ms):
        import bisect
        i = bisect.bisect_left(self.t, ms)
        if i < len(self.b) and self.t[i] - ms <= 3600000:
            return self.b[i][1]
        if i == 0:
            return None
        if ms - self.t[i - 1] > 4 * 86400000:
            return None
        return self.b[i - 1][2]


class Book:
    """Collects (sub, day, dir, kind, info, {h: move}) and turns them into grades."""
    def __init__(self, hs, main):
        self.hs, self.main, self.rows = hs, main, []

    def add(self, sub, day, dir_, moves, kind='dir', info=None):
        self.rows.append((sub, day, dir_, kind, info or {}, moves))

    def result(self, note=''):
        if not self.rows:
            return None
        h = {k: rc._tally([(r[2], r[3], r[4], r[5].get(k)) for r in self.rows], k) for k in self.hs}
        subs = {}
        for s in sorted({r[0] for r in self.rows}):
            rs = [r for r in self.rows if r[0] == s]
            subs[s] = {k: rc._tally([(r[2], r[3], r[4], r[5].get(k)) for r in rs], k) for k in self.hs}
        mon = {}
        for r in self.rows:
            ok = rc._right(r[3], r[2], r[5].get(self.main), r[4])
            if ok is None:
                continue
            a = mon.setdefault(r[1][:7], [0, 0])
            a[0] += 1
            a[1] += ok
        days = sorted({r[1] for r in self.rows})
        return {'_rows': self.rows, 'h': h, 'subs': subs, 'monthly': [{'month': m, 'n': a[0], 'pct': round(100 * a[1] / a[0])} for m, a in sorted(mon.items())],
                'from': days[0], 'to': days[-1], 'days': len(days), 'note': note}


def _pct(a, b):
    return (b / a - 1) * 100 if a and b else None


# ------------------------------------------------------------------ Scanner Picks and their signals
def picks(log):
    import news
    sys.path.insert(0, os.path.join(ROOT, 'mover-scanner')) if os.path.join(ROOT, 'mover-scanner') not in sys.path else None
    from scanner import picks as P
    lists = [('etfs', news._yaml_symbols(os.path.join(ROOT, 'mover-scanner', 'watchlist.yaml'))),
             ('stocks', news._yaml_symbols(os.path.join(ROOT, 'mover-scanner', 'stocklist.yaml')))]
    spy = daily('SPY')
    sidx = {b['date']: i for i, b in enumerate(spy)}
    bp, bs = Book(['d1', 'd5', 'd20'], 'd5'), Book(['d1', 'd5', 'd20'], 'd5')
    done = 0
    for mode, syms in lists:
        for sym in syms:
            if sym == 'SPY' and mode == 'stocks':
                continue
            try:
                bars = daily(sym)
            except Busy:
                raise
            except Exception as e:
                log.append(f'{sym}: {e}'[:120])
                continue
            for i in range(260, len(bars) - 1):
                day = bars[i]['date']
                j = sidx.get(day)
                if j is None:
                    continue
                time.sleep(0.002)             # let the rest of the gateway breathe
                try:
                    p = P.analyze(sym, bars[max(0, i - 300):i + 1], mode, today=None, ref=spy[max(0, j - 300):j + 1])
                except Exception:
                    p = None
                if not p:
                    continue
                mv = {}
                for k, n in (('d1', 1), ('d5', 5), ('d20', 20)):
                    if i + n < len(bars) and j + n < len(spy):
                        mv[k] = _pct(bars[i]['close'], bars[i + n]['close']) - _pct(spy[j]['close'], spy[j + n]['close'])
                lean = p.get('lean') or ''
                bp.add(lean, day, 1 if lean == 'up' else -1 if lean == 'down' else 0, mv)
                for sg in p.get('signals') or []:
                    bs.add(sg.get('kind') or '', day, sg.get('dir') or 0, mv, info={'title': sg.get('title')})
            done += 1
    note = f'{done} symbols from your scanner lists, the scanner\'s own rules on each past day, vs SPY.'
    titles = {}
    for r in bs.rows:
        titles.setdefault(r[0], re.sub(r'\s*\(.*?\)\s*$', '', r[4].get('title') or r[0]))
    out = bs.result(note)
    if out:
        out['titles'] = titles
    return bp.result(note), out


# ------------------------------------------------------------------ mood meters (hourly futures)
def _closes_after(days):
    """Trading days with (close of D, D+1 trading day)."""
    return [(d, rc.next_tday(d)) for d in days]


def _trading_days(first_ms, last_ms):
    d, end, out = watch.et(first_ms).date() + timedelta(days=2), watch.et(last_ms).date(), []
    while d <= end:
        if rc.trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _grade_es(es, t, hs):
    p0 = es.at(t)
    mv = {}
    for h in hs:
        T = rc.target(h, t)
        if T > rc._now() - 3600000:
            continue
        mv[h] = _pct(p0, es.at(T))
    return mv


def moods(log):
    import meters
    import world
    hp = {}
    need = {s for s, _, _, _ in meters.FUTURES} | {k for k, _, _, _ in world.MOOD if k not in ('asia', 'europe')}
    groups = {'asia': [s for s, _, g, _ in world.SYMBOLS if g == 'asia'], 'europe': [s for s, _, g, _ in world.SYMBOLS if g == 'europe']}
    for s in sorted(need | set(groups['asia']) | set(groups['europe'])):
        try:
            hp[s] = Px(hourly(s))
        except Busy:
            raise
        except Exception as e:
            log.append(f'{s}: {e}'[:120])
    es = hp.get('ES=F')
    if not es or not es.b:
        return None, None, None
    days = _trading_days(es.t[0], es.t[-1])
    hs = ['m60', 'open', 'close', 'd1']
    bf = Book(hs, 'close')
    bm = Book(['open', 'close', 'd1'], 'open')
    be = Book(['m60', 'close'], 'close')

    def fut_score(start, t):
        tot = wsum = 0.0
        for sym, _, w, full in meters.FUTURES:
            px = hp.get(sym)
            v = _pct(px.at(start), px.at(t)) if px else None
            if v is None:
                continue
            tot += w * math.tanh(v / full)
            wsum += w
        return int(round(100 * tot / wsum)) if wsum else None

    for D in days[:-1]:
        N = rc.next_tday(D)
        c = rc.close_ms(D)
        evals = [('close', c, [watch.at_et(D, 20, 0), watch.at_et(N, 0, 0), watch.at_et(N, 4, 0), watch.at_et(N, 8, 0)]),
                 ('after', c, [watch.at_et(D, 18, 0), watch.at_et(D, 19, 0)]),
                 ('overnight', watch.at_et(D, 20, 0), [watch.at_et(N, 0, 0), watch.at_et(N, 4, 0), watch.at_et(N, 8, 0)]),
                 ('premarket', watch.at_et(N, 4, 0), [watch.at_et(N, 6, 0), watch.at_et(N, 8, 0), watch.at_et(N, 9, 0)]),
                 ('open', watch.at_et(N, 9, 30), [watch.at_et(N, 11, 0), watch.at_et(N, 13, 0), watch.at_et(N, 15, 0)])]
        for key, start, ts in evals:
            for t in ts:
                sc = fut_score(start, t)
                if sc is None:
                    continue
                bf.add(key, N.isoformat(), rc._dir_score(sc), _grade_es(es, t, hs))
        # overnight mood (World formula) at 8 pm, midnight, 4 am, 8 am and 9 am
        for t in (watch.at_et(D, 20, 0), watch.at_et(N, 0, 0), watch.at_et(N, 4, 0), watch.at_et(N, 8, 0), watch.at_et(N, 9, 0)):
            tot = wsum = 0.0
            for k, w, full, sign in world.MOOD:
                if k in groups:
                    vs = [_pct(hp[s].at(t - 86400000), hp[s].at(t)) for s in groups[k] if s in hp]
                    vs = [v for v in vs if v is not None]
                    v = sum(vs) / len(vs) if vs else None
                else:
                    v = _pct(hp[k].at(c), hp[k].at(t)) if k in hp else None
                if v is None:
                    continue
                tot += w * math.tanh(sign * v / full)
                wsum += w
            if wsum:
                bm.add('', N.isoformat(), rc._dir_score(int(round(100 * tot / wsum))), _grade_es(es, t, ['open', 'close', 'd1']))
    # event days: S&P futures move from 15 minutes before the news, vs the same hours on other days
    events = set()
    for d in FOMC_PAST + list(world.FOMC):
        events.add((date.fromisoformat(d), 14, 0, 'fed'))
    for D in days:
        if D.weekday() == 4 and D.day <= 7:
            events.add((D, 8, 30, 'jobs report'))
    ev_days = {(d, hh, mm) for d, hh, mm, _ in events}
    for d, hh, mm, kind in sorted(events):
        t = watch.at_et(d, hh, mm) - 15 * 60000
        if t < es.t[0] or t > rc._now() - 86400000:
            continue
        be.add(kind, d.isoformat(), 0, {h: (abs(x) if x is not None else None) for h, x in _grade_es(es, t, ['m60', 'close']).items()}, kind='size')
    normal = Book(['m60', 'close'], 'close')
    for D in days:
        for hh, mm in ((8, 30), (14, 0)):
            if (D, hh, mm) in ev_days:
                continue
            t = watch.at_et(D, hh, mm) - 15 * 60000
            if t > rc._now() - 86400000:
                continue
            normal.add(f'{hh}:{mm:02d}', D.isoformat(), 0, {h: (abs(x) if x is not None else None) for h, x in _grade_es(es, t, ['m60', 'close']).items()}, kind='size')
    ev = be.result('Fed decisions and the monthly jobs report (first Friday of the month, so a few dates are off). CPI dates are not replayed.')
    nr = normal.result()
    if ev and nr:
        ev['normal'] = nr['h']
    return (bf.result('The meter\'s own formula on hourly futures prices. A reading is right when S&P futures keep moving the same way.'),
            bm.result('The World mood formula on hourly prices (Asia and Europe use their 24-hour change).'), ev)


# ------------------------------------------------------------------ your symbols: gaps, highs/lows, relative strength
def mine(log):
    import analysis
    import signals
    syms = [s for s, k in sorted(analysis.symbols().items()) if '=' not in s and '^' not in s]
    pct4 = float(((signals.settings().get('gap4') or {}).get('pct')) or 2.0)
    spy = daily('SPY')
    sidx = {b['date']: i for i, b in enumerate(spy)}
    bg = Book(['close'], 'close')
    b4 = Book(['open', 'close'], 'open')
    bh = Book(['close', 'd1'], 'close')
    closes = {}
    for sym in syms:
        try:
            bars = daily(sym)
        except Busy:
            raise
        except Exception as e:
            log.append(f'{sym}: {e}'[:120])
            continue
        if len(bars) < 30:
            continue
        closes[sym] = bars
        gaps = []
        for i in range(1, len(bars)):
            pc, b = bars[i - 1]['close'], bars[i]
            # gap scorecard: the year before, gaps this way and this size held / faded?
            g = _pct(pc, b['open'])
            if g is not None and abs(g) >= 1:
                up = g > 0
                held = (b['close'] > pc) if up else (b['close'] < pc)
                like = [x for x in gaps if x[0] > i - 252 and (x[1] > 0) == up and analysis._size_bucket(x[1]) == analysis._size_bucket(g)]
                if len(like) >= 3:
                    guess = sum(x[2] for x in like) / len(like) >= 0.5
                    bg.add('up gaps' if up else 'down gaps', b['date'], 1 if guess else -1, {'close': 1.0 if held else -1.0})
                gaps.append((i, g, held))
            # crossed yesterday's high / low during the day: did it close beyond it?
            ph, pl = bars[i - 1]['high'], bars[i - 1]['low']
            nxt = bars[i + 1]['close'] if i + 1 < len(bars) else None
            if b['open'] <= ph < b['high']:
                bh.add('yesterday high', b['date'], 1, {'close': _pct(ph, b['close']), 'd1': _pct(b['close'], nxt) if nxt else None})
            if b['open'] >= pl > b['low']:
                bh.add('yesterday low', b['date'], -1, {'close': _pct(pl, b['close']), 'd1': _pct(b['close'], nxt) if nxt else None})
        # 4 am gaps (hourly, pre-market): 4-5 am price vs the 4 pm close
        try:
            hb = Px(hourly(sym))
        except Busy:
            raise
        except Exception as e:
            log.append(f'{sym} hourly: {e}'[:120])
            continue
        if not hb.b:
            continue
        byday = {b['date']: b for b in bars}
        for i in range(1, len(bars)):
            D = date.fromisoformat(bars[i]['date'])
            t4 = watch.at_et(D, 4, 30)
            if t4 < hb.t[0]:
                continue
            p4, pc = hb.at(t4), bars[i - 1]['close']
            g = _pct(pc, p4)
            if g is None or abs(g) < pct4 or abs(g) > 60:
                continue
            b = byday[bars[i]['date']]
            b4.add('', b['date'], 1 if g > 0 else -1, {'open': _pct(p4, b['open']), 'close': _pct(p4, b['close'])})
    # relative strength: each day the 3 strongest / weakest over 21 days vs SPY, then the next 5 / 20 days vs SPY
    br = Book(['d5', 'd20'], 'd5')
    idx = {s: {b['date']: i for i, b in enumerate(bs)} for s, bs in closes.items()}
    for j in range(22, len(spy) - 5):
        day = spy[j]['date']
        sr = _pct(spy[j - 21]['close'], spy[j]['close'])
        rows = []
        for s, bs in closes.items():
            i = idx[s].get(day)
            if i is None or i < 21:
                continue
            rows.append((_pct(bs[i - 21]['close'], bs[i]['close']) - sr, s, i))
        if len(rows) < 7:
            continue
        rows.sort()
        for sub, d_, pick in (('strongest', 1, rows[-3:]), ('weakest', -1, rows[:3])):
            for _, s, i in pick:
                bs = closes[s]
                mv = {}
                for k, n in (('d5', 5), ('d20', 20)):
                    if i + n < len(bs) and j + n < len(spy):
                        mv[k] = _pct(bs[i]['close'], bs[i + n]['close']) - _pct(spy[j]['close'], spy[j + n]['close'])
                br.add(sub, day, d_, mv)
    return (bg.result('Each gap of 1%+ on your symbols, guessed from how gaps the same way and size did in the year before.'),
            b4.result(f'Your symbols {pct4:g}%+ away from the 4 pm close at 4-5 am (hourly pre-market prices).'),
            bh.result('Days a stock went above yesterday\'s high (or below the low) after opening inside it.'),
            br.result('Every day, the 3 strongest and 3 weakest of your symbols over 21 days vs SPY.'))


def weather(log):
    import analysis
    b = Book(['close', 'd1'], 'close')
    for sym in analysis.SECTOR_ETF:
        try:
            bars = daily(sym)
            hb = Px(hourly(sym))
        except Busy:
            raise
        except Exception as e:
            log.append(f'{sym}: {e}'[:120])
            continue
        for i in range(1, len(bars) - 1):
            D = date.fromisoformat(bars[i]['date'])
            t = watch.at_et(D, 8, 0)
            if not hb.b or t < hb.t[0]:
                continue
            p8 = hb.at(t)
            chg = _pct(bars[i - 1]['close'], p8)
            if chg is None or abs(chg) < 0.3:
                continue
            b.add(sym, bars[i]['date'], 1 if chg > 0 else -1, {'close': _pct(p8, bars[i]['close']), 'd1': _pct(p8, bars[i + 1]['close'])})
    return b.result('Sector funds at 8 am vs the 4 pm close (what the morning weather report reads), then to the close.')


# ------------------------------------------------------------------ run everything
def run():
    log, feats, t0 = [], {}, time.time()
    old = watch._read(OUT, {})
    steps = [('picks', lambda: picks(log)), ('moods', lambda: moods(log)), ('mine', lambda: mine(log)), ('weather', lambda: weather(log))]
    busy = ''
    for name, fn in steps:
        try:
            r = fn()
        except Busy as e:
            busy = str(e)
            break
        except Exception as e:
            log.append(f'{name}: {type(e).__name__}: {e}'[:160])
            continue
        if name == 'picks':
            feats['picks'], feats['pick_signal'] = r
        elif name == 'moods':
            feats['fut_mood'], feats['mood'], feats['econ'] = r
        elif name == 'mine':
            feats['gap_score'], feats['gaps4am'], feats['alert_hl'], feats['relstr'] = r
        else:
            feats['weather'] = r
    try:                                              # jb-explore: keep each replayed call for the explorer and backtests
        import rc_explore
        rc_explore.save_replay(feats)
    except Exception as e:
        log.append(f'explorer: {e}'[:160])
    for v in feats.values():
        if isinstance(v, dict):
            v.pop('_rows', None)
    merged = dict(old.get('features') or {})
    merged.update({k: v for k, v in feats.items() if v})
    out = {'at': rc._now(), 'secs': int(time.time() - t0), 'features': merged, 'log': log[-40:],
           'note': ('Stopped early: ' + busy + '. The rest runs again tonight.') if busy else ''}
    watch._write(OUT, out)
    return out
