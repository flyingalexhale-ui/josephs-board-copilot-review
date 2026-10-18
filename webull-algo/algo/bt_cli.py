"""Backtest command-line helpers shared by both algos' run_backtest.py (identical file in
webull-algo and ibkr-algo): the trading-option flags, and --recorded (the board's own record).
"""
import argparse
import os

import pandas as pd

from . import options as opt


def add_option_args(ap):
    g = ap.add_argument_group(
        'trading options', 'Default: the dashboard\'s Trading options panel (its "Paper & backtest" column), '
                           'or the strategy file\'s trading: section if nothing was saved there. '
                           'These flags change them for this one run.')
    g.add_argument('--sessions', default=None,
                   help='sessions to trade besides regular hours: all, none, or a list such as '
                        'premarket,afterhours,overnight')
    g.add_argument('--order-type', choices=['limit', 'market'], default=None)
    g.add_argument('--limit-offset-pct', type=float, default=None,
                   help='limit price distance from the last price in %% (default 0.05)')
    g.add_argument('--cancel-after-s', type=float, default=None,
                   help='a limit order not filled within this many seconds is dropped (default 60)')
    g.add_argument('--trailing-stop-pct', type=float, default=None, help='0 turns the trailing stop off')
    g.add_argument('--extended-hours-cost-pct', type=float, default=None,
                   help='extra cost per fill outside regular hours in %% (default 0.1)')
    g.add_argument('--options-path', default=None, help=argparse.SUPPRESS)


def options_for(args, strategy, project_dir):
    """(options, where they came from) for this backtest run."""
    path = args.options_path or opt.path_for(project_dir)
    opts, source, problem = opt.effective(strategy.trading_raw, path)
    if problem:
        print(f'note: {problem} -- using the {source}')
    changes = {}
    if args.sessions is not None:
        s = args.sessions.strip().lower()
        changes['paper_sessions'] = list(opt.SESSIONS) if s == 'all' else [] if s in ('none', 'regular', '') else s
    for key in ('order_type', 'limit_offset_pct', 'cancel_after_s', 'trailing_stop_pct', 'extended_hours_cost_pct'):
        v = getattr(args, key)
        if v is not None:
            changes[key] = v
    if changes:
        merged = dict(opts)
        merged.update(changes)
        try:
            opts = opt.validate(merged, 'command line')
        except opt.OptionsError as e:
            raise SystemExit(str(e))
        source += ' + command line'
    return opts, source


def describe(opts):
    return opt.describe(opts, 'paper')


def load_recorded(symbol, broker, data_dir=None):
    """The board's own record of this symbol: the last trade price of every minute the
    <broker> bridge has seen, around the clock (overnight included), for up to 35 days. Only
    one price per minute is kept, so each bar's open/high/low equal its close and volume is 0 --
    strategies that use highs/lows or volume, and limit-order fills, are rougher than with
    real bars."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    d = data_dir or os.environ.get('JB_DATA_DIR') or os.path.join(root, 'data')
    folder = os.path.join(d, f'{broker}_ticks')
    if not os.path.isdir(folder):
        raise SystemExit(f'--recorded: no price record at {folder} -- is the {broker} board running?')
    want = symbol.upper()
    rows = {}
    for name in sorted(n for n in os.listdir(folder) if n.endswith('.csv')):
        with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as f:
            for line in f:
                parts = line.rstrip('\n').split(',')
                if len(parts) != 3 or parts[0].upper() != want:
                    continue
                try:
                    rows[int(parts[1])] = float(parts[2])      # a later line for a minute wins
                except ValueError:
                    continue
    if not rows:
        raise SystemExit(f'--recorded: the {broker} board has no record of {symbol}. Add it to the board\'s '
                         f'watchlist; from then on every minute is recorded (kept 35 days).')
    ts = sorted(rows)
    px = [rows[t] for t in ts]
    idx = pd.to_datetime(ts, unit='ms', utc=True)
    df = pd.DataFrame({'open': px, 'high': px, 'low': px, 'close': px, 'volume': 0.0}, index=idx)
    print(f'--recorded: {len(df)} minutes of {symbol} from the {broker} board ({df.index[0]} -> {df.index[-1]}). '
          f'One price per minute: open = high = low = close, volume 0.')
    return df


def load_hedge(strategy, data_dir=None):
    """The hedge's prices for a strategy with a hedge: section, from the board's own record (IBKR first: it has the
    overnight session; a micro future uses NQ=F / ES=F). None (with a note) if the board has not recorded it."""
    from .autohedge import record_symbol
    h = getattr(strategy, 'hedge', None)
    if not h:
        return None
    sym = record_symbol(h['symbol'])
    for broker in ('ibkr', 'webull'):
        try:
            return load_recorded(sym, broker, data_dir)
        except SystemExit:
            continue
    print(f'hedge: the board has no record of {sym}, so the backtest leaves the hedge out')
    return None


def print_hedge(metrics):
    h = (metrics or {}).get('hedge') if isinstance(metrics, dict) else None
    if not h:
        return
    if h.get('error'):
        print(f"  hedge ({h['symbol']})   : {h['error']}")
        return
    print(f"  hedge ({h['side']} {h['symbol']}, ${h['dollar_ratio']:g} per $1): {h['trips']} legs, P&L ${h['pnl']:,.2f}")
    print(f"  with the hedge     : return {h['combined_return_pct']}%, max drawdown {h['combined_max_drawdown_pct']}%, "
          f"win rate {h['combined_win_rate_pct']}%")
    if h.get('note'):
        print(f"  note               : {h['note']}")


def print_extra(metrics, opts, source):
    print(f"Trading options ({source}): {describe(opts)}")
    if isinstance(metrics, dict) and 'orders_sent' in metrics:
        by = metrics.get('fills_by_session') or {}
        print(f"  orders sent        : {metrics['orders_sent']}  (not filled: {metrics['orders_unfilled']})")
        if by:
            print('  fills by session   : ' + ', '.join(f'{opt.LABELS.get(k, k)} {v}' for k, v in sorted(by.items())))
