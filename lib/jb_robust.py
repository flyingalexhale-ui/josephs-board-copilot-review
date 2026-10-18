#!/usr/bin/env python3
"""Robustness test for one algo strategy file (jb-lab, deep search idea 56). Started by the Strategy lab page
(gateway/stratlab.py); writes <algo>/logs/robust_status.json as it goes.

  1. Settings grid: the backtest re-run with two of the strategy's numbers changed (60% to 140% of what the file says,
     5 steps each). A good strategy makes money across most of the grid; one green square in a red grid = luck.
  2. Walk-forward: the best of a 3 x 3 grid (60%, 100%, 140%) is picked on the older 70% of the data, then tested on the newer 30% it never saw.
  3. Monte Carlo: the base run's trades reshuffled 1,000 times: realistic worst drawdown and losing streak.

Data: the board's own minute record of the symbol (35 days, every session), the same as Run backtest's "recorded".
    python3 lib/jb_robust.py --algo webull --strategy strategies/x.yaml [--knobs "indicators.0.period,risk.stop_loss_pct"]
"""
import argparse
import copy
import io
import json
import os
import random
import re
import sys
import time
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEPS = [0.6, 0.8, 1.0, 1.2, 1.4]
WF_STEPS = [0.6, 1.0, 1.4]                      # walk-forward tunes on a smaller 3 x 3 grid (keeps the test quicker)


def knobs_of(cfg):
    """Every number a person would tune: indicator settings, numbers inside the rules, stop / target / trailing."""
    out = []
    for i, ind in enumerate(cfg.get('indicators') or []):
        for k, v in (ind or {}).items():
            if k not in ('kind', 'as') and isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append({'path': f'indicators.{i}.{k}', 'label': f"{ind.get('kind')} {k}", 'value': v})
    for part in ('entry', 'exit'):
        rule = str((cfg.get(part) or {}).get('when') or '')
        for j, m in enumerate(re.finditer(r'(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w.])', rule)):
            v = float(m.group(1))
            if re.search(r'(session|weekday|next_day_open|minute_et)\s*[=<>!]+\s*$', rule[:m.start()]):
                continue                              # sessions, weekdays and clock times are labels, not settings
            out.append({'path': f'{part}.num.{j}', 'label': f'{part} rule number {m.group(1)}', 'value': v})
    for k in ('stop_loss_pct', 'take_profit_pct'):
        v = (cfg.get('risk') or {}).get(k)
        if isinstance(v, (int, float)):
            out.append({'path': f'risk.{k}', 'label': k.replace('_', ' '), 'value': v})
    v = (cfg.get('trading') or {}).get('trailing_stop_pct')
    if isinstance(v, (int, float)):
        out.append({'path': 'trading.trailing_stop_pct', 'label': 'trailing stop pct', 'value': v})
    return out


def set_knob(cfg, path, value):
    c = copy.deepcopy(cfg)
    p = path.split('.')
    if p[0] == 'indicators':
        orig = c['indicators'][int(p[1])][p[2]]
        c['indicators'][int(p[1])][p[2]] = max(1, int(round(value))) if isinstance(orig, int) else round(value, 4)
        ind = c['indicators'][int(p[1])]
        if 'as' not in ind and p[2] == 'period' and ind['kind'] in ('sma', 'ema', 'rsi', 'atr', 'roc'):   # keep the column name (sma_20 stays sma_20)
            ind['as'] = f"{ind['kind']}_{orig}"
    elif p[1] == 'num':
        rule = str(c[p[0]]['when'])
        ms = list(re.finditer(r'(?<![\w.])(-?\d+(?:\.\d+)?)(?![\w.])', rule))
        m = ms[int(p[2])]
        c[p[0]]['when'] = rule[:m.start()] + f'{value:g}' + rule[m.end():]
    else:
        c.setdefault(p[0], {})[p[1]] = round(value, 4)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--algo', required=True, choices=['webull', 'ibkr'])
    ap.add_argument('--strategy', required=True)
    ap.add_argument('--knobs', default='')
    a = ap.parse_args()
    try:
        os.nice(10)                                    # let the board and the algos go first
    except OSError:
        pass
    adir = os.path.join(ROOT, f'{a.algo}-algo')
    sys.path.insert(0, adir)
    os.chdir(adir)
    import yaml
    from algo import bt_cli
    from algo.backtest import run_backtest, _pair_trades
    from algo.strategy import Strategy
    status_path = os.path.join(adir, 'logs', 'robust_status.json')
    os.makedirs(os.path.dirname(status_path), exist_ok=True)
    st = {'strategy': os.path.basename(a.strategy), 'started': int(time.time() * 1000), 'running': True, 'step': 'Loading the price record'}

    def write():
        tmp = status_path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(st, f)
        os.replace(tmp, status_path)
    write()
    try:
        cfg = yaml.safe_load(open(a.strategy))
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                bars = bt_cli.load_recorded(cfg['symbol'], a.algo)
            except SystemExit:                         # no record from this broker: use the other one's
                bars = bt_cli.load_recorded(cfg['symbol'], 'ibkr' if a.algo == 'webull' else 'webull')
        st['bars'] = len(bars)
        st['from'], st['to'] = str(bars.index[0])[:16], str(bars.index[-1])[:16]

        def bt(c, b):
            s = Strategy(copy.deepcopy(c), source_path=a.strategy)
            with contextlib.redirect_stdout(io.StringIO()):
                r = run_backtest(s, b)
            m = r['metrics']
            return r, {'ret': m.get('total_return_pct'), 'trades': m.get('num_trades'), 'dd': m.get('max_drawdown_pct'),
                       'win': m.get('win_rate_pct')}

        ks = knobs_of(cfg)
        st['knobs'] = ks
        want = [k for k in a.knobs.split(',') if k] or [k['path'] for k in ks[:2]]
        pick = [k for k in ks if k['path'] in want][:2]
        if not pick:
            raise SystemExit('this strategy has no numbers to vary')
        st['step'] = 'Base run'
        write()
        base_r, base = bt(cfg, bars)
        st['base'] = base
        # 1. grid
        st['grid'] = {'x': pick[0], 'y': pick[1] if len(pick) > 1 else None, 'steps': STEPS, 'cells': []}
        ys = STEPS if len(pick) > 1 else [1.0]
        for yi, fy in enumerate(ys):
            row = []
            for xi, fx in enumerate(STEPS):
                st['step'] = f'Settings grid {yi * len(STEPS) + xi + 1} of {len(ys) * len(STEPS)}'
                write()
                c = set_knob(cfg, pick[0]['path'], pick[0]['value'] * fx)
                if len(pick) > 1:
                    c = set_knob(c, pick[1]['path'], pick[1]['value'] * fy)
                try:
                    row.append(bt(c, bars)[1])
                except Exception as e:
                    row.append({'error': str(e)[:80]})
            st['grid']['cells'].append(row)
        cells = [x for r in st['grid']['cells'] for x in r if x.get('ret') is not None]
        st['grid']['share_up'] = round(100 * sum(1 for x in cells if x['ret'] > 0) / len(cells)) if cells else None
        # 2. walk-forward
        cut = bars.index[int(len(bars) * 0.7)]
        train, test = bars[bars.index < cut], bars[bars.index >= cut]
        best, bestv = None, None
        wys = WF_STEPS if len(pick) > 1 else [1.0]
        for yi, fy in enumerate(wys):
            for xi, fx in enumerate(WF_STEPS):
                st['step'] = f'Walk-forward: tuning on the older 70% ({yi * len(WF_STEPS) + xi + 1} of {len(wys) * len(WF_STEPS)})'
                write()
                c = set_knob(cfg, pick[0]['path'], pick[0]['value'] * fx)
                if len(pick) > 1:
                    c = set_knob(c, pick[1]['path'], pick[1]['value'] * fy)
                try:
                    v = bt(c, train)[1]
                except Exception:
                    continue
                if v['ret'] is not None and (bestv is None or v['ret'] > bestv['ret']):
                    best, bestv = (fx, fy, c), v
        st['step'] = 'Walk-forward: testing on the newer 30%'
        write()
        wf = {'cut': str(cut)[:16], 'train_best': bestv}
        if best:
            wf['best_x'], wf['best_y'] = round(pick[0]['value'] * best[0], 4), (round(pick[1]['value'] * best[1], 4) if len(pick) > 1 else None)
            wf['test_best'] = bt(best[2], test)[1]
        wf['test_base'] = bt(cfg, test)[1]
        st['walk'] = wf
        # 3. Monte Carlo on the base run's trades
        st['step'] = 'Monte Carlo'
        write()
        pls = [t['pnl'] for t in _pair_trades(base_r['portfolio'].trades)]
        start = base_r['portfolio'].starting_cash if hasattr(base_r['portfolio'], 'starting_cash') else 100000.0
        if len(pls) >= 5:
            rnd = random.Random(5)
            dds, streaks, finals = [], [], []
            for _ in range(1000):
                seq = [pls[rnd.randrange(len(pls))] for _ in pls]
                eq = peak = start
                mdd, run, worst = 0.0, 0, 0
                for p in seq:
                    eq += p
                    peak = max(peak, eq)
                    mdd = min(mdd, eq / peak - 1)
                    run = run + 1 if p <= 0 else 0
                    worst = max(worst, run)
                dds.append(mdd * 100)
                streaks.append(worst)
                finals.append((eq / start - 1) * 100)
            dds.sort()
            streaks.sort()
            finals.sort()
            st['mc'] = {'trades': len(pls), 'dd_p50': round(dds[500], 2), 'dd_p5': round(dds[50], 2), 'streak_p50': streaks[500], 'streak_p95': streaks[950],
                        'ret_p5': round(finals[50], 2), 'ret_p50': round(finals[500], 2), 'ret_p95': round(finals[950], 2),
                        'lose_pct': round(100 * sum(1 for x in finals if x < 0) / 1000, 1)}
        else:
            st['mc'] = {'trades': len(pls), 'note': 'fewer than 5 trades: not enough to reshuffle'}
        st['step'] = 'Done'
    except SystemExit as e:
        st['error'] = str(e)
    except Exception as e:
        st['error'] = f'{type(e).__name__}: {e}'[:300]
    st['running'] = False
    st['ended'] = int(time.time() * 1000)
    write()


if __name__ == '__main__':
    main()
