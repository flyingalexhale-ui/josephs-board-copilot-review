"""Bar-by-bar backtester: walks historical OHLCV bars, evaluates a Strategy's entry/exit
rules on EACH BAR USING ONLY DATA UP TO AND INCLUDING THAT BAR (no lookahead), and fills
simulated orders on LATER bars -- not the bar the signal fired on -- because in real trading
you can't act on a bar until it has actually finished forming.

Orders follow the same trading options as paper trading (algo/options.py):
  * sessions: rules only run, and orders only fill, in regular hours plus whichever of
    pre-market / after-hours / overnight are turned on for paper trading (paper_sessions)
  * market orders (regular hours only) fill at the next bar's open, like before
  * limit orders (price = signal bar's close +/- limit_offset_pct, at least one tick; exits
    always priced to fill -- the same rule as live) fill on a later bar only if
    that bar's low (buy) or high (sell) reaches the limit -- at the limit, or at the bar's
    open if it opened better -- within cancel_after_s; otherwise they are counted as unfilled.
    A limit order only fills in the session it was sent in.
  * fills outside regular hours cost extended_hours_cost_pct more (wider spreads), on top of
    the slippage setting
  * trailing stop (trailing_stop_pct): triggered when a bar's low falls that far below the
    best price since entry (the bar's high for shorts); the exit order then fills from the
    next bar on. The best price is updated AFTER each bar's check (conservative).
  * stop-loss / take-profit are checked on each bar's close; with market orders in regular
    hours they close at that close (as before), otherwise an exit order is sent like above

Produces: a Portfolio (trade log + equity curve) and a metrics dict (total return, CAGR,
max drawdown, win rate, profit factor, Sharpe -- see `compute_metrics()`). A strategy with a hedge: section
also gets metrics['hedge'] (algo/autohedge.py): the hedge leg from `hedge_bars`, and the result with it.
"""
import math

import numpy as np  # noqa: F401  (kept: callers import it from here in older notebooks)
import pandas as pd

from . import options as opt
from .portfolio import Portfolio
from .runstate import session_at
from .strategy import Strategy


RISK_EXITS = ('stop-loss', 'take-profit', 'trailing-stop')


def bar_sessions(index, step=None):
    """For intraday bars: (session at each bar's open, session at each bar's close). Times
    without a zone are read as US/Eastern (the usual convention for exported US market data)."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        idx = idx.tz_localize('America/New_York', ambiguous='NaT', nonexistent='NaT')
    if step is None:
        step = pd.Timedelta(minutes=1)
        if len(idx) > 2:
            step = pd.Series(idx[1:] - idx[:-1]).median()
    last = step - pd.Timedelta(seconds=1)
    opens, closes = [], []
    for ts in idx:
        if ts is pd.NaT:
            opens.append(None)
            closes.append(None)
            continue
        opens.append(session_at(ts.to_pydatetime()))
        closes.append(session_at((ts + last).to_pydatetime()))
    return opens, closes


def run_backtest(strategy: Strategy, bars: pd.DataFrame, starting_cash: float = 100_000.0,
                 commission_per_share: float = 0.0, slippage_pct: float = 0.0, options: dict = None,
                 hedge_bars: pd.DataFrame = None, no_short_sessions=()) -> dict:
    """`bars` must have a DatetimeIndex and columns at least 'open','high','low','close'
    ('volume' too if any configured indicator needs it), sorted ascending in time.
    `options`: trading options (algo/options.py); default: the strategy file's trading: section.
    `no_short_sessions`: sessions the broker takes no short sales in (Webull: overnight) -- no short entry then, as in
    paper and live trading.
    Returns {'portfolio': Portfolio, 'metrics': dict, 'bars': the bars DataFrame with every
    computed indicator column added, 'options': the options used}."""
    opts = opt.validate(options if options is not None else getattr(strategy, 'trading_raw', None))
    bars = bars.copy()
    strategy.compute_indicators(bars)
    portfolio = Portfolio(starting_cash=starting_cash, commission_per_share=commission_per_share,
                          slippage_pct=slippage_pct)
    n = len(bars)
    allowed = {'regular'} | set(opts['paper_sessions'])
    step = pd.Series(bars.index[1:] - bars.index[:-1]).median() if n > 2 else pd.Timedelta(days=1)
    intraday = step < pd.Timedelta(hours=12)
    if intraday:
        s_open, s_close = bar_sessions(bars.index, step)
        wait_bars = max(1, math.ceil(opts['cancel_after_s'] / max(step.total_seconds(), 1)))
    else:
        s_open = s_close = ['regular'] * n              # daily bars: one regular session per bar
        wait_bars = 1
    ext_pct = opts['extended_hours_cost_pct']
    trail_pct = opts.get('trailing_stop_pct')
    stats = {'orders': 0, 'filled': 0, 'unfilled': 0}
    by_session = {}

    rearm_at = -1
    def eff(side, price, sess):
        pct = slippage_pct + (ext_pct if sess != 'regular' else 0.0)
        adj = price * pct / 100.0
        return price + adj if side == 'buy' else price - adj

    def make_order(kind, side, ref, sess, i, reason):
        typ = opts['order_type']
        if typ == 'market' and sess != 'regular':
            typ = 'limit'
        limit = None
        if typ == 'limit':
            limit = opt.limit_price(ref, side, opts['limit_offset_pct'], closing=kind == 'exit')
        stats['orders'] += 1
        return {'kind': kind, 'side': side, 'type': typ, 'limit': limit, 'session': sess, 'reason': reason,
                'last_i': i + (1 if typ == 'market' else wait_bars)}

    order = None
    opened_at = -1                     # bar on which the open position was entered
    for i in range(n):
        time = bars.index[i]
        o = float(bars['open'].iloc[i])
        h = float(bars['high'].iloc[i])
        lo = float(bars['low'].iloc[i])
        c = float(bars['close'].iloc[i])

        # 1. The order sent on an earlier bar: fill it on this bar if it can, or let it lapse.
        if order is not None:
            sess = s_open[i]
            if i > order['last_i'] or sess != order['session']:
                stats['unfilled'] += 1
                order = None
            else:
                side, fill = order['side'], None
                if order['type'] == 'market':
                    fill = eff(side, o, sess)
                elif side == 'buy' and eff('buy', lo, sess) <= order['limit']:
                    fill = min(eff('buy', o, sess), order['limit'])
                elif side == 'sell' and eff('sell', h, sess) >= order['limit']:
                    fill = max(eff('sell', o, sess), order['limit'])
                if fill is not None:
                    if order['kind'] == 'enter':
                        shares = strategy.position_size(portfolio.equity(fill), fill)
                        if shares > 0:
                            portfolio.open_position(time, 'long' if side == 'buy' else 'short', shares, fill,
                                                    'entry', exact=True)
                            portfolio.position.peak = fill
                            opened_at = i
                    else:
                        portfolio.close_position(time, fill, order['reason'], exact=True)
                    stats['filled'] += 1
                    by_session[sess] = by_session.get(sess, 0) + 1
                    order = None

        pos = portfolio.position
        ok = s_close[i] in allowed

        # 2. Trailing stop: this bar's low/high against the best price up to the previous bar.
        if pos is not None and trail_pct and order is None and ok and i != opened_at:
            if pos.peak is None:
                pos.peak = pos.entry_price
            if pos.side == 'long':
                stop = pos.peak * (1 - trail_pct / 100.0)
                if lo <= stop:
                    order = make_order('exit', 'sell', min(o, stop), s_close[i], i, 'trailing-stop')
            else:
                stop = pos.peak * (1 + trail_pct / 100.0)
                if h >= stop:
                    order = make_order('exit', 'buy', max(o, stop), s_close[i], i, 'trailing-stop')
        if pos is not None and portfolio.position is pos and i != opened_at:
            # (not on the entry bar: its high/low may have come before the fill)
            pos.peak = max(pos.peak if pos.peak is not None else pos.entry_price, h) if pos.side == 'long' else \
                min(pos.peak if pos.peak is not None else pos.entry_price, lo)

        # 3. Stop-loss / take-profit on this bar's close.
        if pos is not None and order is None and ok:
            hit = portfolio.stop_take_hit(c, strategy.stop_loss_pct, strategy.take_profit_pct)
            if hit:
                if opts['order_type'] == 'market' and s_close[i] == 'regular':
                    portfolio.close_position(time, c, hit)
                    stats['orders'] += 1
                    stats['filled'] += 1
                    by_session['regular'] = by_session.get('regular', 0) + 1
                else:
                    order = make_order('exit', 'sell' if pos.side == 'long' else 'buy', c, s_close[i], i, hit)

        # 4. This bar's rules, using data up to and including bar i only (no lookahead).
        if order is None and ok:
            if portfolio.position is None:
                trs = portfolio.trades
                # after a stop-loss / take-profit / trailing stop, wait until the entry rule has been false once,
                # exactly as the paper/live trader does (jb-ready)
                if trs and trs[-1].reason in RISK_EXITS and rearm_at != len(trs):
                    if not strategy.check_entry(bars, i):
                        rearm_at = len(trs)
                elif strategy.check_entry(bars, i):
                    if not (strategy.entry_side == 'sell' and s_close[i] in no_short_sessions):
                        order = make_order('enter', strategy.entry_side, c, s_close[i], i, 'entry')
            elif strategy.check_exit(bars, i):
                p = portfolio.position
                order = make_order('exit', 'sell' if p.side == 'long' else 'buy', c, s_close[i], i, 'exit-rule')

        portfolio.mark(time, c)

    if order is not None:
        stats['unfilled'] += 1
    # Close out anything still open at the end of the data, at the last close, so metrics
    # reflect a fully realized run rather than leaving an ambiguous open position.
    if portfolio.position is not None:
        portfolio.close_position(bars.index[-1], float(bars['close'].iloc[-1]), 'end-of-data')
        portfolio.mark(bars.index[-1], float(bars['close'].iloc[-1]))

    metrics = compute_metrics(portfolio, bars)
    if getattr(strategy, 'hedge', None):        # jb-hedge: the strategy's hedge: leg, from the board's record of it
        from .autohedge import backtest_hedge
        metrics['hedge'] = backtest_hedge(strategy, portfolio, hedge_bars, opts, slippage_pct)
    if isinstance(metrics, dict):
        metrics['orders_sent'] = stats['orders']
        metrics['orders_unfilled'] = stats['unfilled']
        metrics['fills_by_session'] = by_session
    return {'portfolio': portfolio, 'metrics': metrics, 'bars': bars, 'options': opts}


def compute_metrics(portfolio: Portfolio, bars: pd.DataFrame) -> dict:
    curve = pd.Series([e for _, e in portfolio.equity_curve],
                      index=[t for t, _ in portfolio.equity_curve])
    if len(curve) < 2:
        return {'note': 'not enough bars to compute metrics'}

    total_return_pct = (curve.iloc[-1] / curve.iloc[0] - 1) * 100

    days = max((curve.index[-1] - curve.index[0]).total_seconds() / 86400, 1)
    years = days / 365.25
    # a yearly rate from a few days of data is meaningless (+1% in 2 days "is" +500%/yr)
    cagr_pct = ((curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1) * 100 \
        if days >= 90 and curve.iloc[0] > 0 else float('nan')

    running_max = curve.cummax()
    drawdown = (curve - running_max) / running_max
    max_drawdown_pct = drawdown.min() * 100

    # Sharpe on the equity curve's own bar-to-bar returns, annualized by the bar count per
    # year implied by the data's own average spacing -- so this is correct whether `bars`
    # is daily or 1-minute, without you having to tell it which.
    rets = curve.pct_change().dropna()
    if len(rets) > 1 and rets.std() != 0:
        avg_gap_days = days / max(len(curve) - 1, 1)
        periods_per_year = 365.25 / avg_gap_days if avg_gap_days > 0 else float('nan')
        sharpe = (rets.mean() / rets.std()) * math.sqrt(periods_per_year) if periods_per_year == periods_per_year else float('nan')
    else:
        sharpe = float('nan')

    closed_trades = _pair_trades(portfolio.trades)
    wins = [t for t in closed_trades if t['pnl'] > 0]
    losses = [t for t in closed_trades if t['pnl'] <= 0]
    win_rate_pct = (len(wins) / len(closed_trades) * 100) if closed_trades else float('nan')
    gross_profit = sum(t['pnl'] for t in wins)
    gross_loss = -sum(t['pnl'] for t in losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else float('nan')

    return {
        'total_return_pct': round(total_return_pct, 2),
        'cagr_pct': round(cagr_pct, 2) if cagr_pct == cagr_pct else None,
        'max_drawdown_pct': round(max_drawdown_pct, 2),
        'sharpe': round(sharpe, 2) if sharpe == sharpe else None,
        'num_trades': len(closed_trades),
        'win_rate_pct': round(win_rate_pct, 2) if win_rate_pct == win_rate_pct else None,
        'profit_factor': round(profit_factor, 2) if profit_factor not in (float('inf'),) and profit_factor == profit_factor else profit_factor,
        'final_equity': round(curve.iloc[-1], 2),
        'starting_equity': round(curve.iloc[0], 2),
    }


def _pair_trades(trades) -> list:
    """Pairs each entry with its exit(s) and returns each round trip's realized P&L. Used for
    win rate / profit factor -- separate from the ledger's own cash tracking so those numbers
    are cross-checkable against each other."""
    pairs = []
    entry = None
    for t in trades:
        if entry is None:
            entry = t
            continue
        was_long = entry.side == 'buy'
        pnl = (t.price - entry.price) * t.shares if was_long else (entry.price - t.price) * t.shares
        pnl -= entry.commission * (t.shares / entry.shares if entry.shares else 1) + t.commission
        pairs.append({'entry': entry, 'exit': t, 'pnl': pnl})
        if '(partial)' not in (t.reason or ''):
            entry = None
    return pairs
