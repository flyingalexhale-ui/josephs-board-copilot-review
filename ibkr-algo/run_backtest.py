#!/usr/bin/env python3
"""CLI entry point for backtesting a strategy file. Two ways to get historical bars in:

  1. --csv path/to/bars.csv    a local CSV with columns date/time,open,high,low,close,volume
                                (column names are matched loosely -- see _load_csv below).
  2. --ibkr                    fetch real historical bars from IB Gateway/TWS for the
                                strategy's own symbol (IB Gateway/TWS must be running and
                                logged in, same as ibkr_bridge.py).

Either way, prints a metrics summary, writes the full trade log to a CSV next to the
strategy file, and (unless --no-plot) saves an equity-curve PNG.

Examples:
    python3 run_backtest.py --strategy strategies/sma_crossover.yaml --csv aapl_daily.csv
    python3 run_backtest.py --strategy strategies/rsi_reversal.yaml --ibkr --duration "2 Y" --bar-size "1 day"
    python3 run_backtest.py --strategy strategies/rsi_reversal.yaml --ibkr --sessions all
    python3 run_backtest.py --strategy strategies/rsi_reversal.yaml --recorded --sessions overnight

Sessions and order handling (limit/market, trailing stop, cost outside regular hours) come
from the dashboard's Trading options panel ("Paper & backtest" column), or the strategy's
trading: section; the --sessions / --order-type / ... flags change them for one run.

The default is 1-MINUTE bars (the last 10 days), because that is what the paper and live
traders run on: in a strategy, sma_20 means 20 minutes there. A daily backtest
(--bar-size "1 day") tests a different strategy -- sma_20 would mean 20 days.
"""
import argparse
import asyncio
import calendar
import datetime as _dt
import json
import os
import sys
import tempfile

import pandas as pd

from algo import bt_cli
from algo.backtest import run_backtest
from algo.strategy import Strategy

PROJECT = os.path.dirname(os.path.abspath(__file__))

_COLUMN_ALIASES = {
    'open': ('open', 'o', 'openprice'),
    'high': ('high', 'h', 'highprice'),
    'low': ('low', 'l', 'lowprice'),
    'close': ('close', 'c', 'closeprice', 'last'),
    'volume': ('volume', 'v', 'vol'),
}
_TIME_ALIASES = ('date', 'time', 'timestamp', 't', 'datetime', 'tradetime')


def _find_col(columns_lower, aliases):
    for a in aliases:
        if a in columns_lower:
            return columns_lower[a]
    return None


def _load_csv(path: str) -> pd.DataFrame:
    raw = pd.read_csv(path)
    lower = {c.lower(): c for c in raw.columns}

    time_col = _find_col(lower, _TIME_ALIASES)
    if time_col is None:
        raise SystemExit(
            f"{path}: couldn't find a date/time column (looked for any of {_TIME_ALIASES} "
            f"case-insensitively). Columns found: {list(raw.columns)}")

    out = {}
    for target, aliases in _COLUMN_ALIASES.items():
        col = _find_col(lower, aliases)
        if col is None:
            if target == 'volume':
                out['volume'] = 0.0
                continue
            raise SystemExit(
                f"{path}: couldn't find a '{target}' column (looked for any of {aliases} "
                f"case-insensitively). Columns found: {list(raw.columns)}")
        else:
            # .to_numpy(), not the raw Series itself -- building a DataFrame from Series
            # that still carry raw's original integer index, against a *different*
            # `index=idx` below, makes pandas align by label instead of position and
            # silently fills everything with NaN. Bare arrays sidestep that entirely.
            out[target] = raw[col].astype(float).to_numpy()

    try:
        idx = pd.to_datetime(raw[time_col])
    except (ValueError, TypeError):      # offsets that change mid-file (daylight saving): all to UTC
        idx = pd.to_datetime(raw[time_col], utc=True)
    if getattr(idx.dt, 'tz', None) is None:
        # times without a zone are US Eastern (the usual convention for exported US market data)
        idx = idx.dt.tz_localize('America/New_York', ambiguous='NaT', nonexistent='NaT')
    idx = pd.DatetimeIndex(idx).tz_convert('UTC')
    df = pd.DataFrame(out, index=idx).sort_index()
    df = df[df.index.notna()]
    df = df[~df.index.duplicated(keep='last')]
    return df


def _fetch_ibkr_bars(strategy: Strategy, host: str, port, client_id: int, duration: str,
                      bar_size: str, use_rth: bool, lookback_bars: int, overnight=False) -> pd.DataFrame:
    """Fetches real historical bars for the strategy's symbol from IB Gateway/TWS, using the
    exact same ib_async connect/qualify pattern already confirmed and running in
    ibkr_bridge.py and algo/paper_trader.py's warm_up() -- including the same
    calendar.timegm(...UTC...) bar-time construction for formatDate=2 historical data. IB
    Gateway/TWS must already be running and logged in with the API enabled, same as the
    board's own IBKR bridge -- this is a one-off connection made just for this fetch, then
    disconnected, using its own client id so it never collides with a paper_trader.py or
    ibkr_bridge.py instance that's also connected at the time."""
    from ib_async import IB, Stock

    async def _fetch():
        ib = IB()
        ports = [port] if port else [4001, 4002, 7496, 7497]
        last_err = None
        for p in ports:
            try:
                await ib.connectAsync(host, p, clientId=client_id, timeout=5)
                break
            except Exception as e:
                last_err = e
        else:
            raise SystemExit(f'--ibkr: cannot reach IB Gateway/TWS ({last_err!r}) -- is it '
                              f'running, logged in, and API access enabled?')
        try:
            contract = Stock(strategy.symbol.replace('.', ' '), 'SMART', 'USD')
            q = await ib.qualifyContractsAsync(contract)
            if not q or q[0] is None or not contract.conId:
                raise SystemExit(f'IBKR does not recognise {strategy.symbol!r}')
            bars = await ib.reqHistoricalDataAsync(
                contract, '', duration, bar_size, 'TRADES', use_rth, 2, timeout=120)
            if not bars:
                raise SystemExit('IBKR returned no history (check the log above for an IBKR error, e.g. '
                                 'no market-data permission or an invalid --duration/--bar-size pair)')
            night = []
            if overnight and not use_rth:
                # SMART history covers 4:00-20:00 ET; the overnight trades live on IBKR's OVERNIGHT
                # venue. Not every account/stock has that history -- then the test runs without it.
                try:
                    on = Stock(contract.symbol, 'OVERNIGHT', 'USD', primaryExchange=contract.primaryExchange)
                    q2 = await ib.qualifyContractsAsync(on)
                    if q2 and q2[0] is not None and on.conId:
                        night = await ib.reqHistoricalDataAsync(
                            on, '', duration, bar_size, 'TRADES', False, 2, timeout=120) or []
                except Exception as e:
                    print(f'(overnight history not available: {e!r})')
                if not night:
                    print('note: IBKR returned no overnight-venue history, so this backtest has no overnight bars. '
                          'The board\'s own record has them: add --recorded instead of --ibkr.')
            return bars, night
        finally:
            ib.disconnect()

    raw, night = asyncio.run(_fetch())
    rows = []
    for b in raw:
        t = calendar.timegm(b.date.timetuple()) * 1000
        rows.append({'t': t, 'open': b.open, 'high': b.high, 'low': b.low,
                     'close': b.close, 'volume': b.volume})
    if night:
        from algo.runstate import session_at
        known = {r['t'] for r in rows}
        added = 0
        for b in night:
            t = calendar.timegm(b.date.timetuple()) * 1000
            when = _dt.datetime.fromtimestamp(t / 1000, _dt.timezone.utc)
            if t not in known and session_at(when) == 'overnight':
                rows.append({'t': t, 'open': b.open, 'high': b.high, 'low': b.low,
                             'close': b.close, 'volume': b.volume})
                added += 1
        rows.sort(key=lambda r: r['t'])
        print(f'added {added} overnight bars from IBKR\'s overnight venue')
    rows = rows[-lookback_bars:]
    if not rows:
        raise SystemExit('IBKR returned no usable historical bars for this symbol/bar size.')
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop('t'), unit='ms', utc=True)
    return df.sort_index()


def _write_backtest_status(status_path, strategy, bars, metrics, portfolio, opts=None, opts_source=None, source=None):
    """Same atomic write-then-rename handoff as paper_trader.py's write_status() -- this is
    the file algo_status_server.py reads back for the dashboard's Trading Algo tab's
    backtest panel. Overwritten by every run, so the dashboard always shows the LAST
    backtest you ran, whichever strategy it was."""
    if not status_path:
        return
    curve = portfolio.equity_curve
    if len(curve) > 600:                      # thin evenly so the chart shows the whole run
        step = len(curve) / 600.0
        curve = [curve[int(i * step)] for i in range(600)] + [curve[-1]]
    snapshot = {
        'strategy_name': strategy.name,
        'symbol': strategy.symbol,
        'bars_used': len(bars),
        'range_start': bars.index[0].isoformat(),
        'range_end': bars.index[-1].isoformat(),
        'metrics': metrics,
        'options': opts, 'options_source': opts_source, 'source': source,
        'trades': [{
            'time': t.time.isoformat() if hasattr(t.time, 'isoformat') else str(t.time),
            'side': t.side, 'shares': t.shares, 'price': t.price, 'reason': t.reason,
        } for t in portfolio.trades[-200:]],
        'equity_curve': [[
            (t.isoformat() if hasattr(t, 'isoformat') else str(t)), e,
        ] for t, e in curve],
        'ran_at': _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    try:
        from algo.runstate import atomic_write_json
        atomic_write_json(status_path, snapshot)
        print(f"Dashboard status written to: {status_path}")
    except Exception as e:
        print(f"(couldn't write dashboard status, continuing anyway: {e!r})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--strategy', required=True, help='path to a strategy YAML file')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--csv', help='path to a local CSV of historical OHLCV bars')
    src.add_argument('--ibkr', action='store_true',
                      help="fetch the strategy's symbol's history from IB Gateway/TWS instead")
    src.add_argument('--recorded', action='store_true',
                      help="use the IBKR board's own minute-by-minute price record (up to 35 days, "
                           "overnight included; one price per minute)")
    ap.add_argument('--duration', default='10 D',
                     help="IBKR durationStr for --ibkr, e.g. '10 D', '6 M', '2 Y' (default: '10 D')")
    ap.add_argument('--bar-size', default='1 min',
                     help="IBKR barSizeSetting for --ibkr, e.g. '1 min' (default, like the live trader), "
                          "'5 mins', '1 hour', '1 day'")
    ap.add_argument('--use-rth', action='store_true',
                     help='regular trading hours only for --ibkr (default: off -- 4:00-20:00 ET, so the '
                          'pre-market and after-hours sessions can be tested too)')
    ap.add_argument('--host', default='127.0.0.1', help='IB Gateway/TWS host for --ibkr')
    ap.add_argument('--port', type=int, default=None,
                     help='IB Gateway/TWS port for --ibkr (default: try 4001, 4002, 7496, 7497)')
    ap.add_argument('--client-id', type=int, default=57,
                     help="IBKR API client id for --ibkr -- default 57, distinct from "
                          "ibkr_bridge.py's 17, the mover scanner's 37, and paper_trader.py's "
                          "47, so a backtest fetch never collides with those if run at the same time")
    ap.add_argument('--lookback-bars', type=int, default=50000,
                     help='max bars to use, most-recent first (default: 50000)')
    ap.add_argument('--starting-cash', type=float, default=100_000.0)
    ap.add_argument('--commission-per-share', type=float, default=0.0)
    ap.add_argument('--slippage-pct', type=float, default=0.0,
                     help='fill slippage as a percent of quoted price, always against you '
                          '(default: 0, i.e. perfect fills -- unrealistic but a clean baseline)')
    ap.add_argument('--no-plot', action='store_true', help='skip writing the equity-curve PNG')
    ap.add_argument('--out-dir', default=None,
                     help='where to write the trade-log CSV and equity PNG (default: next to '
                          'the strategy file)')
    ap.add_argument('--status-path', default=os.environ.get('ALGO_STATUS_PATH'),
                     help='where to write a JSON status snapshot for the dashboard\'s Trading '
                          'Algo tab (default: <strategy dir>/../logs/backtest_status.json; '
                          'pass an empty string to disable)')
    bt_cli.add_option_args(ap)
    args = ap.parse_args()

    strategy = Strategy.from_yaml(args.strategy)

    opts, opts_source = bt_cli.options_for(args, strategy, PROJECT)
    if args.csv:
        bars, source = _load_csv(args.csv), 'csv'
    elif args.recorded:
        bars, source = bt_cli.load_recorded(strategy.symbol, 'ibkr'), 'recorded'
    else:
        bars = _fetch_ibkr_bars(strategy, args.host, args.port, args.client_id, args.duration,
                                 args.bar_size, args.use_rth, args.lookback_bars,
                                 overnight='overnight' in opts['paper_sessions'])
        source = 'ibkr'

    if len(bars) < 5:
        raise SystemExit(f'only {len(bars)} usable bars -- need more history to backtest anything.')
    step = pd.Series(bars.index[1:] - bars.index[:-1]).median()
    print(f'{len(bars)} bars, {bars.index[0]} -> {bars.index[-1]}, one bar = {step}. '
          + ('Same bar size as the paper/live trader.' if step <= pd.Timedelta(minutes=1) else
             'NOTE: the paper/live trader uses 1-minute bars, so the same strategy there reacts to '
             'minutes, not these bars.'))

    hedge_bars = bt_cli.load_hedge(strategy)             # jb-hedge: the strategy's hedge: leg (board's own record)
    result = run_backtest(strategy, bars, starting_cash=args.starting_cash,
                           commission_per_share=args.commission_per_share,
                           slippage_pct=args.slippage_pct, options=opts, hedge_bars=hedge_bars)
    portfolio, metrics = result['portfolio'], result['metrics']

    print(f"\n=== {strategy.name} ({strategy.symbol}) ===")
    print(f"Bars used: {len(bars)}  ({bars.index[0]} -> {bars.index[-1]})")
    print(f"Trades: {metrics.get('num_trades')}")
    for k in ('total_return_pct', 'cagr_pct', 'max_drawdown_pct', 'sharpe',
              'win_rate_pct', 'profit_factor', 'starting_equity', 'final_equity'):
        if k in metrics:
            print(f"  {k:20s}: {metrics[k]}")
    bt_cli.print_extra(metrics, opts, opts_source)
    bt_cli.print_hedge(metrics)

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.strategy)) or '.'
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.strategy))[0]
    stamp = _dt.datetime.now().strftime('%Y%m%d_%H%M%S')

    trades_path = os.path.join(out_dir, f'{stem}_trades_{stamp}.csv')
    trades_df = pd.DataFrame([{
        'time': t.time, 'side': t.side, 'shares': t.shares, 'price': t.price,
        'reason': t.reason, 'commission': t.commission, 'notional': t.notional,
    } for t in portfolio.trades])
    trades_df.to_csv(trades_path, index=False)
    print(f"\nTrade log written to: {trades_path}")

    if not args.no_plot:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            curve = pd.Series([e for _, e in portfolio.equity_curve],
                               index=[t for t, _ in portfolio.equity_curve])
            fig, ax = plt.subplots(figsize=(10, 4))
            curve.plot(ax=ax)
            ax.set_title(f'{strategy.name} -- equity curve')
            ax.set_ylabel('Equity ($)')
            fig.tight_layout()
            plot_path = os.path.join(out_dir, f'{stem}_equity_{stamp}.png')
            fig.savefig(plot_path, dpi=120)
            print(f"Equity curve chart:  {plot_path}")
        except ImportError:
            print("(matplotlib not installed -- skipping the equity-curve chart; "
                  "pip install matplotlib to get one, or pass --no-plot to silence this)")

    if args.status_path is None:
        status_path = os.path.join(out_dir, '..', 'logs', 'backtest_status.json')
    else:
        status_path = args.status_path or None
    _write_backtest_status(status_path, strategy, bars, metrics, portfolio, opts, opts_source, source)


if __name__ == '__main__':
    main()
