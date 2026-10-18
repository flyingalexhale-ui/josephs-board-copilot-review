"""Alex Hale's Board -- Strategy lab: build a strategy without code, and test how robust any strategy is
(jb-lab, deep search ideas 55 and 56).

Builder: conditions picked from lists (price, % since the 4 pm close, this session's high / low, moving averages, RSI,
VWAP, rate of change, time of day, session) become a normal strategy file, strategies/lab-<name>.yaml, in the chosen
algo's folder. It is always paper (confirm_live: false). The algo tab's Run backtest then works on it as on any file.
Robustness: lib/jb_robust.py (settings grid, walk-forward, Monte Carlo) on the board's own minute record.

GET  /api/plus/stratlab?algo=webull            -> {strategies, robust (last status)}
GET  /api/plus/stratlab/knobs?algo=&strategy=  -> {knobs, yaml}
POST /api/plus/stratlab/build  {algo, spec, backtest}   POST /api/plus/stratlab/robust {algo, strategy, knobs}
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIRS = {'webull': os.path.join(ROOT, 'webull-algo'), 'ibkr': os.path.join(ROOT, 'ibkr-algo')}
_lock = threading.Lock()
_procs = {}

# what each list item means in a rule, and which indicator it needs
SIMPLE = {'price': ('close', None), 'vs_close': ('vs_close_pct', {'kind': 'vs_close'}),
          'session_high': ('session_range_high', {'kind': 'session_range'}), 'session_low': ('session_range_low', {'kind': 'session_range'}),
          'vwap': ('vwap', {'kind': 'vwap'}), 'minute': ('clock_minute_et', {'kind': 'clock'}), 'session': ('clock_session', {'kind': 'clock'}),
          'weekday': ('clock_weekday', {'kind': 'clock'}), 'bb_upper': ('bollinger_upper', {'kind': 'bollinger'}),
          'bb_lower': ('bollinger_lower', {'kind': 'bollinger'}), 'macd_hist': ('macd_hist', {'kind': 'macd'})}
PERIOD = {'sma': (2, 400), 'ema': (2, 400), 'rsi': (2, 100), 'roc': (1, 400)}
OPS = ('>', '<', '>=', '<=')


def _term(t, inds):
    k = (t or {}).get('t')
    if k == 'num':
        v = float(t.get('v'))
        return f'{v:g}'
    if k in SIMPLE:
        col, spec = SIMPLE[k]
        if spec and spec not in inds:
            inds.append(spec)
        return col
    if k in PERIOD:
        n = int(t.get('n') or 0)
        lo, hi = PERIOD[k]
        if not lo <= n <= hi:
            raise ValueError(f'{k} period must be {lo} to {hi}')
        spec = {'kind': k, 'period': n}
        if spec not in inds:
            inds.append(spec)
        return f'{k}_{n}'
    raise ValueError('pick something to compare')


def _rule(conds, inds):
    parts = []
    for c in conds or []:
        op = c.get('op')
        if op not in OPS:
            raise ValueError('pick a comparison')
        parts.append(f"{_term(c.get('a'), inds)} {op} {_term(c.get('b'), inds)}")
    if not parts:
        raise ValueError('add at least one condition')
    return ' and '.join(parts)


def build_yaml(spec):
    sym = ''.join(ch for ch in str(spec.get('symbol') or '').upper() if ch.isalnum() or ch in '.-')[:10]
    if not sym:
        raise ValueError('which symbol?')
    name = re.sub(r'[^A-Za-z0-9 _-]', '', str(spec.get('name') or '')).strip()[:40] or f'{sym} lab'
    inds = []
    entry = _rule(spec.get('entry'), inds)
    exit_ = _rule(spec.get('exit'), inds) if spec.get('exit') else 'close < 0'      # no exit rule: stop / target / trail only
    def num(k, lo, hi):
        v = spec.get(k)
        if v in (None, ''):
            return None
        v = float(v)
        if not lo <= v <= hi:
            raise ValueError(f'{k.replace("_", " ")} must be {lo} to {hi}')
        return v
    pct = num('pct_of_equity', 1, 100) or 25
    stop, tgt, trail = num('stop_loss_pct', 0.1, 50), num('take_profit_pct', 0.1, 200), num('trailing_stop_pct', 0.1, 50)
    sess = [s for s in (spec.get('sessions') or []) if s in ('premarket', 'afterhours', 'overnight')]
    side = 'sell' if spec.get('side') == 'sell' else 'buy'
    lines = ['# Made by the Strategy lab (jb-lab) on the board. Paper only: confirm_live stays false.',
             '# You can edit it like any strategy file (see STRATEGY_FORMAT.md), or make it again in the lab.',
             f'name: "{name}"', f'symbol: "{sym}"', '', 'indicators:' + ('' if inds else ' []')]
    for s in inds:
        lines.append('  - ' + ', '.join(f'{k}: {v}' for k, v in s.items()).replace(', ', '\n    '))
    lines += ['', 'entry:', f'  side: {side}', f'  when: "{entry}"', '', 'exit:', f'  when: "{exit_}"', '',
              'position_size:', f'  pct_of_equity: {pct:g}', '', 'risk:']
    if stop:
        lines.append(f'  stop_loss_pct: {stop:g}')
    if tgt:
        lines.append(f'  take_profit_pct: {tgt:g}')
    lines += ['  max_order_notional_usd: 25000', '', 'max_open_positions: 1', '', 'trading:',
              f'  paper_sessions: [{", ".join(sess)}]', '  live_sessions: []', '  order_type: limit']
    if trail:
        lines.append(f'  trailing_stop_pct: {trail:g}')
    lines += ['', 'confirm_live: false', '']
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:30] or 'strategy'
    return f'lab-{slug}.yaml', '\n'.join(lines)


def _check(algo, path):
    """Load it with the algo's own code (indicators included) to be sure it runs. -> '' or the problem."""
    code = ('import sys; sys.path.insert(0, "."); import pandas as pd, numpy as np\n'
            'from algo.strategy import Strategy\n'
            's = Strategy.from_yaml(sys.argv[1])\n'
            'i = pd.date_range("2026-10-05 13:00", periods=600, freq="1min", tz="UTC")\n'
            'p = 100 + np.cumsum(np.random.randn(600)) * 0.1\n'
            'df = pd.DataFrame({"open": p, "high": p, "low": p, "close": p, "volume": 1000.0}, index=i)\n'
            's.compute_indicators(df)\n'
            'for k in range(len(df)): s.check_entry(df, k); s.check_exit(df, k)\n')
    r = subprocess.run([sys.executable, '-c', code, path], cwd=DIRS[algo], capture_output=True, text=True, timeout=60)
    return '' if r.returncode == 0 else (r.stderr.strip().splitlines() or ['failed'])[-1][:300]


def _status(algo):
    try:
        return json.load(open(os.path.join(DIRS[algo], 'logs', 'robust_status.json')))
    except (OSError, ValueError):
        return None


def get(sub, query):
    q = {k: (v[0] if isinstance(v, list) and v else v or '') for k, v in (query or {}).items()}
    algo = q.get('algo') if q.get('algo') in DIRS else 'webull'
    import algoctl
    if sub == '':
        st = _status(algo)
        p = _procs.get(algo)
        if st and st.get('running') and (not p or p.poll() is not None) and time.time() * 1000 - st.get('started', 0) > 20 * 60000:
            st['running'], st['error'] = False, 'stopped before it finished'
        return 200, {'algo': algo, 'strategies': algoctl._strategies(algo), 'robust': st}
    if sub == 'knobs':
        f = os.path.basename(q.get('strategy') or '')
        if f not in algoctl._strategies(algo):
            return 400, {'error': 'pick a strategy'}
        sys.path.insert(0, os.path.join(ROOT, 'lib'))
        import yaml
        import jb_robust
        path = os.path.join(DIRS[algo], 'strategies', f)
        txt = open(path, encoding='utf-8').read()
        return 200, {'knobs': jb_robust.knobs_of(yaml.safe_load(txt) or {}), 'yaml': txt}
    return 404, {'error': 'unknown'}


def post(sub, data):
    data = data or {}
    algo = data.get('algo') if data.get('algo') in DIRS else 'webull'
    import algoctl
    if sub == 'build':
        try:
            fname, txt = build_yaml(data.get('spec') or {})
        except (ValueError, TypeError) as e:
            return 400, {'error': str(e) or 'check the boxes'}
        path = os.path.join(DIRS[algo], 'strategies', fname)
        tmp = path + '.check.yaml'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(txt)
        try:
            err = _check(algo, tmp)
        finally:
            os.remove(tmp)
        if err:
            return 400, {'error': 'The algo could not run it: ' + err, 'yaml': txt}
        with open(path, 'w', encoding='utf-8') as f:
            f.write(txt)
        out = {'ok': True, 'file': fname, 'yaml': txt}
        if data.get('backtest'):
            code, body = algoctl.post(algo, {'action': 'backtest', 'strategy': fname})
            out['backtest'] = 'started' if code == 200 else json.loads(body).get('error')
        return 200, out
    if sub == 'robust':
        f = os.path.basename(str(data.get('strategy') or ''))
        if f not in algoctl._strategies(algo):
            return 400, {'error': 'pick a strategy'}
        with _lock:
            p = _procs.get(algo)
            if p and p.poll() is None:
                return 409, {'error': 'a robustness test is already running'}
            knobs = ','.join(k for k in (data.get('knobs') or []) if re.match(r'^[a-z_]+\.[a-z0-9_]+(\.[a-z0-9_]+)?$', str(k)))[:200]
            log = open(os.path.join(DIRS[algo], 'logs', 'robust_run.log'), 'w')
            try:
                _procs[algo] = subprocess.Popen([sys.executable, os.path.join(ROOT, 'lib', 'jb_robust.py'), '--algo', algo, '--strategy',
                                                 'strategies/' + f, '--knobs', knobs], cwd=DIRS[algo], stdout=log, stderr=subprocess.STDOUT,
                                                stdin=subprocess.DEVNULL, start_new_session=True)
            finally:
                log.close()
        return 200, {'ok': True}
    return 404, {'error': 'unknown'}
