"""Alex Hale's Board -- Report Card explorer, backtests and Monte Carlo (jb-explore, deep search idea 39 + Alex's additions).

Explorer: every call the board has written down (live, since install) and every call the nightly replay re-ran on up to
2 years of past prices, with filters (feature, signal/sub, symbol, session, time point, dates), in-depth breakdowns
(by kind, session, symbol, weekday, month) and the rows themselves for a CSV export.

Backtest: each up/down call is treated as a trade: in at the call, out at the chosen time point, profit = the move the
way the call pointed, minus a cost per trade. Calls on the same day share that day equally. Results per feature and for
the board as a whole (every feature's day weighted the same).

Monte Carlo: 1,000 reshuffles of the daily results (bootstrap) give the range of outcomes the same edge could have
produced (5th / median / 95th percentile, chance of ending down, worst drawdowns). The luck test flips each call's
direction at random 500 times: how often do coin flips do at least as well? Under 5% = unlikely to be luck.

GET /api/plus/rc_explore/features
GET /api/plus/rc_explore/calls?feature=&src=live|replay&h=&sub=&sym=&session=&from=&to=
GET /api/plus/rc_explore/backtest?src=live|replay&h=&cost=          (cached; nightly at 7:15 pm ET, or ?run=1)
"""
import gzip
import json
import math
import os
import random
import threading
import time
from datetime import datetime

import watch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get('JB_DATA_DIR') or os.path.join(ROOT, 'data')
CALLS = os.path.join(DATA, 'rc_calls')
BT = os.path.join(DATA, 'rc_backtest.json')
MAX_ROWS = 20000
_lock = threading.Lock()
_st = {'started': False, 'bt': None, 'running': False, 'last_day': ''}
DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _now():
    return int(time.time() * 1000)


def _rc():
    import reportcard
    return reportcard


# ------------------------------------------------------------------ the replay's calls (written by rc_replay.run)
def save_replay(feats):
    """Called by the nightly replay: keep each replayed call, compact, one gzip file per feature."""
    os.makedirs(CALLS, exist_ok=True)
    for k, v in (feats or {}).items():
        rows = (v or {}).get('_rows') if isinstance(v, dict) else None
        if not rows:
            continue
        out = [[r[0], r[1], r[2], r[3], (r[4] or {}).get('em'), {h: (None if m is None else round(m, 3)) for h, m in (r[5] or {}).items()}] for r in rows]
        tmp = os.path.join(CALLS, f'replay-{k}.json.gz.tmp')
        with gzip.open(tmp, 'wt', encoding='utf-8') as f:
            json.dump({'at': _now(), 'rows': out}, f, separators=(',', ':'))
        os.replace(tmp, os.path.join(CALLS, f'replay-{k}.json.gz'))


def _replay_rows(key):
    try:
        with gzip.open(os.path.join(CALLS, f'replay-{key}.json.gz'), 'rt', encoding='utf-8') as f:
            return json.load(f).get('rows') or []
    except (OSError, ValueError):
        return []


# ------------------------------------------------------------------ one list of calls, live or replay
def calls(key, src):
    """-> [{day, at, sym, sub, dir, kind, em, score, moves: {h: move}}] oldest first."""
    rc = _rc()
    out = []
    if src == 'replay':
        for sub, day, d, kind, em, mv in _replay_rows(key):
            out.append({'day': day, 'at': None, 'sym': '', 'sub': sub, 'dir': d, 'kind': kind, 'em': em, 'score': None, 'moves': mv,
                        'session': 'daily'})
        out.sort(key=lambda c: c['day'])
        return out
    with rc._lock:
        c = rc._db()
        try:
            q = 'SELECT sub, sym, at, dir, kind, info, score, ' + ', '.join(rc.HKEYS) + ' FROM calls WHERE feature = ? ORDER BY at'
            rows = c.execute(q, (key,)).fetchall()
        finally:
            c.close()
    for r in rows:
        try:
            info = json.loads(r[5] or '{}')
        except ValueError:
            info = {}
        mv = {h: r[7 + i] for i, h in enumerate(rc.HKEYS) if r[7 + i] is not None and r[7 + i] > -99998}
        out.append({'day': watch.et(r[2]).date().isoformat(), 'at': r[2], 'sym': r[1] or '', 'sub': r[0] or '', 'dir': r[3] or 0,
                    'kind': r[4] or 'dir', 'em': info.get('em'), 'score': r[6], 'moves': mv, 'session': rc.session_of(r[2])})
    return out


def _right(c, h):
    mv = c['moves'].get(h)
    if mv is None:
        return None
    if c['kind'] == 'range':
        return None if not c['em'] else abs(mv) <= c['em']
    if c['kind'] == 'size' or not c['dir']:
        return None
    return mv * c['dir'] > 0


def _stats(cs, h):
    n = r = 0
    win, loss = [], []
    for c in cs:
        ok = _right(c, h)
        if ok is None:
            continue
        n += 1
        r += ok
        if c['kind'] == 'dir':
            pl = c['moves'][h] * c['dir']
            (win if pl > 0 else loss).append(pl)
    out = {'n': n, 'right': r, 'pct': round(100 * r / n, 1) if n else None}
    if win or loss:
        aw = sum(win) / len(win) if win else 0.0
        al = sum(loss) / len(loss) if loss else 0.0
        out.update(avg_win=round(aw, 2), avg_loss=round(al, 2), expect=round((sum(win) + sum(loss)) / (len(win) + len(loss)), 3),
                   pf=round(sum(win) / -sum(loss), 2) if loss and sum(loss) else None)
    if n >= 10:                         # how sure: a 95% range for the hit rate (Wilson)
        p, z = r / n, 1.96
        d = 1 + z * z / n
        mid, half = (p + z * z / (2 * n)) / d, z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
        out['ci'] = [round(100 * (mid - half), 1), round(100 * (mid + half), 1)]
    return out


def _group(cs, h, keyf, limit=40):
    g = {}
    for c in cs:
        g.setdefault(keyf(c), []).append(c)
    rows = [dict(_stats(v, h), key=k) for k, v in g.items()]
    rows = [x for x in rows if x['n']]
    rows.sort(key=lambda x: -x['n'])
    return rows[:limit]


def explore(q):
    rc = _rc()
    key = q.get('feature') or ''
    f = rc.FMAP.get(key)
    if not f:
        return {'error': 'unknown feature'}
    hs, main = f[3], f[4]
    h = q.get('h') if q.get('h') in hs else main
    src = 'replay' if q.get('src') == 'replay' else 'live'
    cs = calls(key, src)
    subs = sorted({c['sub'] for c in cs if c['sub']})
    syms = sorted({c['sym'] for c in cs if c['sym']})
    if q.get('sub'):
        cs = [c for c in cs if c['sub'] == q['sub']]
    if q.get('sym'):
        cs = [c for c in cs if c['sym'] == q['sym'].upper()]
    if q.get('session'):
        cs = [c for c in cs if c['session'] == q['session']]
    if q.get('from'):
        cs = [c for c in cs if c['day'] >= q['from']]
    if q.get('to'):
        cs = [c for c in cs if c['day'] <= q['to']]
    out = {'feature': key, 'name': f[1], 'what': f[2], 'src': src, 'h': h, 'horizons': [[x, rc.HNAME[x]] for x in hs],
           'subs': subs, 'syms': syms, 'count': len(cs), 'summary': _stats(cs, h),
           'by_h': [dict(_stats(cs, x), key=rc.HNAME[x]) for x in hs],
           'by_sub': _group(cs, h, lambda c: c['sub'] or '(none)'),
           'by_session': _group(cs, h, lambda c: c['session']),
           'by_sym': _group(cs, h, lambda c: c['sym'] or '(all)', 30),
           'by_dow': sorted(_group(cs, h, lambda c: DOW[datetime.strptime(c['day'], '%Y-%m-%d').weekday()]), key=lambda x: DOW.index(x['key'])),
           'by_month': sorted(_group(cs, h, lambda c: c['day'][:7], 120), key=lambda x: x['key'])}
    rows = []
    for c in cs[-MAX_ROWS:]:
        ok = _right(c, h)
        rows.append([c['day'], watch.et(c['at']).strftime('%H:%M') if c['at'] else '', c['sym'], c['sub'], c['dir'], c['kind'],
                     c['score'], c['em']] + [c['moves'].get(x) for x in hs] + [None if ok is None else int(ok)])
    out['columns'] = ['day', 'time_et', 'symbol', 'kind_of_call', 'direction', 'type', 'score', 'expected_move'] + hs + ['right_at_' + h]
    out['rows'] = rows
    out['truncated'] = len(cs) > MAX_ROWS
    return out


# ------------------------------------------------------------------ backtest and Monte Carlo
def _daily_returns(cs, h, cost):
    """{day: average profit % of that day's up/down calls}, and the per-call profits."""
    days, per = {}, []
    for c in cs:
        if c['kind'] != 'dir' or not c['dir'] or c['moves'].get(h) is None:
            continue
        pl = c['moves'][h] * c['dir'] - cost
        days.setdefault(c['day'], []).append(pl)
        per.append((c['day'], c['moves'][h] * c['dir'], c['moves'][h]))
    return {d: sum(v) / len(v) for d, v in days.items()}, per


def _curve(daily, alloc):
    eq, peak, mdd, pts = 1.0, 1.0, 0.0, []
    for d in sorted(daily):
        eq *= 1 + alloc * daily[d] / 100
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        pts.append([d, round((eq - 1) * 100, 2)])
    return pts, round(mdd * 100, 2)


def _bt_stats(daily, per, alloc):
    if not daily:
        return None
    pts, mdd = _curve(daily, alloc)
    rs = [alloc * daily[d] for d in sorted(daily)]
    mean = sum(rs) / len(rs)
    sd = math.sqrt(sum((x - mean) ** 2 for x in rs) / (len(rs) - 1)) if len(rs) > 1 else 0
    wins = [x for _, x, _ in per if x > 0]
    losses = [x for _, x, _ in per if x <= 0]
    return {'trades': len(per), 'days': len(daily), 'total': pts[-1][1], 'max_dd': mdd, 'curve': pts[-400:],
            'win_rate': round(100 * len(wins) / len(per), 1) if per else None,
            'avg_day': round(mean, 3), 'sharpe': round(mean / sd * math.sqrt(252), 2) if sd else None,
            'pf': round(sum(wins) / -sum(losses), 2) if losses and sum(losses) else None}


def _monte_carlo(daily, alloc, runs=1000, seed=7):
    rs = [alloc * daily[d] / 100 for d in sorted(daily)]
    if len(rs) < 20:
        return None
    rnd = random.Random(seed)
    finals, dds = [], []
    for _ in range(runs):
        eq = peak = 1.0
        mdd = 0.0
        for _ in range(len(rs)):
            eq *= 1 + rs[rnd.randrange(len(rs))]
            peak = max(peak, eq)
            mdd = min(mdd, eq / peak - 1)
        finals.append((eq - 1) * 100)
        dds.append(mdd * 100)
    finals.sort()
    dds.sort()
    q = lambda a, p: round(a[min(len(a) - 1, int(p * len(a)))], 1)
    return {'runs': runs, 'p5': q(finals, 0.05), 'p50': q(finals, 0.5), 'p95': q(finals, 0.95),
            'lose_pct': round(100 * sum(1 for x in finals if x < 0) / runs, 1), 'dd_p50': q(dds, 0.5), 'dd_p5': q(dds, 0.05),
            'hist': _hist(finals)}


def _hist(xs, bins=20):
    lo, hi = xs[0], xs[-1]
    if hi <= lo:
        return [[round(lo, 1), len(xs)]]
    w = (hi - lo) / bins
    out = [[round(lo + i * w, 1), 0] for i in range(bins)]
    for x in xs:
        out[min(bins - 1, int((x - lo) / w))][1] += 1
    return out


def _luck(per, runs=500, seed=11):
    """Coin-flip test: random directions on the same calls; how often do they do at least as well?"""
    if len(per) < 30:
        return None
    rnd = random.Random(seed)
    sample = per if len(per) <= 5000 else rnd.sample(per, 5000)
    real = sum(x for _, x, _ in sample)
    moves = [m for _, _, m in sample]
    better = 0
    for _ in range(runs):
        if sum(m if rnd.random() < 0.5 else -m for m in moves) >= real:
            better += 1
    p = better / runs
    return {'runs': runs, 'p': round(p, 3), 'calls': len(sample),
            'text': ('Unlikely to be luck: random coin flips did as well only ' if p < 0.05 else
                     'Could be luck: random coin flips did as well ') + f'{p * 100:.0f}% of the time.'}


def backtest(src='live', h=None, cost=0.05, alloc=1.0):
    rc = _rc()
    feats, composite = [], {}
    for key, name, what, hs, main, can_replay in rc.FEATURES:
        hh = h if h in hs else main
        cs = calls(key, src)
        daily, per = _daily_returns(cs, hh, cost)
        st = _bt_stats(daily, per, alloc)
        if not st:
            continue
        st.update(key=key, name=name, h=hh, h_name=rc.HNAME[hh], mc=_monte_carlo(daily, alloc, runs=500), luck=_luck(per))
        feats.append(st)
        for d, v in daily.items():
            composite.setdefault(d, []).append(v)
    out = {'at': _now(), 'src': src, 'cost': cost, 'features': sorted(feats, key=lambda x: -(x['total'] or 0))}
    if composite:
        cd = {d: sum(v) / len(v) for d, v in composite.items()}
        per = [(d, v, v) for d, v in cd.items()]
        st = _bt_stats(cd, per, alloc)
        st.update(mc=_monte_carlo(cd, alloc), luck=None)
        st['note'] = ('Every feature\'s day counts the same, then the days are chained. '
                      'Each feature uses its own main time point unless you pick one.')
        out['whole'] = st
    return out


def run_all():
    with _lock:
        if _st['running']:
            return
        _st['running'] = True
    try:
        res = {'live': backtest('live'), 'replay': backtest('replay')}
        watch._write(BT, res)
        _st['bt'] = res
    finally:
        _st['running'] = False


def _loop():
    time.sleep(120)
    while True:
        try:
            d = watch.et(_now())
            if d.hour == 19 and d.minute >= 15 and _st['last_day'] != d.date().isoformat():
                _st['last_day'] = d.date().isoformat()
                run_all()
        except Exception as e:
            print(f'rc_explore: {e!r}', flush=True)
        time.sleep(60)


def start():
    with _lock:
        if _st['started']:
            return
        _st['started'] = True
    threading.Thread(target=_loop, name='rc-explore', daemon=True).start()


def _q(query):
    return {k: (v[0] if isinstance(v, list) and v else v or '') for k, v in (query or {}).items()}


def get(sub, query):
    start()
    q = _q(query)
    rc = _rc()
    if sub in ('', 'features'):
        out = []
        for key, name, what, hs, main, can_replay in rc.FEATURES:
            out.append({'key': key, 'name': name, 'what': what, 'horizons': [[x, rc.HNAME[x]] for x in hs], 'main': main,
                        'replay': os.path.exists(os.path.join(CALLS, f'replay-{key}.json.gz'))})
        return 200, {'features': out}
    if sub == 'calls':
        return 200, explore(q)
    if sub == 'backtest':
        src = 'replay' if q.get('src') == 'replay' else 'live'
        if q.get('h') or q.get('cost'):
            try:
                cost = max(0.0, min(1.0, float(q.get('cost') or 0.05)))
            except ValueError:
                cost = 0.05
            return 200, backtest(src, q.get('h') or None, cost)
        if q.get('run') == '1':
            threading.Thread(target=run_all, daemon=True).start()
            return 200, {'started': True}
        if _st['bt'] is None:
            _st['bt'] = watch._read(BT, None)
        if not _st['bt']:
            run_all()
        res = dict((_st['bt'] or {}).get(src) or {})
        res['running'] = _st['running']
        return 200, res
    return 404, {'error': 'unknown'}


def post(sub, data):
    return 404, {'error': 'unknown'}
