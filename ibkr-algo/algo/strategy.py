"""Loads a strategy from a YAML file and evaluates it against a bar of indicator data.

This is the "plug in a trading theory without writing code" layer: a strategy file
describes which indicators to compute and two rule strings (entry/exit) written against
those indicators' column names, plus position sizing and optional stop-loss/take-profit.
See strategies/*.yaml for worked examples, and STRATEGY_FORMAT.md for the full schema.
"""
import yaml

from . import autohedge
from . import indicators as ind
from . import options
from .rules import compile_rule, RuleError


class StrategyError(Exception):
    pass


class Strategy:
    def __init__(self, config: dict, source_path: str = '<dict>'):
        self.source_path = source_path
        self.name = config.get('name', source_path)
        self.symbol = config.get('symbol')
        if not self.symbol:
            raise StrategyError(f"{source_path}: missing required 'symbol'")

        self.indicator_specs = config.get('indicators', [])
        if not isinstance(self.indicator_specs, list):
            raise StrategyError(f"{source_path}: 'indicators' must be a list")

        entry = config.get('entry')
        exit_ = config.get('exit')
        if not entry or 'when' not in entry:
            raise StrategyError(f"{source_path}: missing required 'entry.when'")
        if not exit_ or 'when' not in exit_:
            raise StrategyError(f"{source_path}: missing required 'exit.when'")

        try:
            self.entry_side = entry.get('side', 'buy').lower()
            self.entry_rule = compile_rule(entry['when'])
            self.exit_rule = compile_rule(exit_['when'])
        except RuleError as e:
            raise StrategyError(f"{source_path}: {e}")
        if self.entry_side not in ('buy', 'sell'):
            raise StrategyError(f"{source_path}: entry.side must be 'buy' or 'sell', got {self.entry_side!r}")

        sizing = config.get('position_size', {})
        if isinstance(sizing, (int, float)):
            sizing = {'shares': sizing}
        self.size_shares = sizing.get('shares')
        self.size_pct_of_equity = sizing.get('pct_of_equity')
        if self.size_shares is None and self.size_pct_of_equity is None:
            raise StrategyError(
                f"{source_path}: 'position_size' needs either 'shares' (a fixed share "
                f"count) or 'pct_of_equity' (e.g. 10 for 10% of current portfolio value)")

        risk = config.get('risk', {})
        self.stop_loss_pct = risk.get('stop_loss_pct')
        self.take_profit_pct = risk.get('take_profit_pct')
        self.broker_stop_pct = risk.get('broker_stop_pct')   # live backup stop at the broker (core_trader.py; 0 = off)
        self.max_positions = config.get('max_open_positions', 1)

        # Live-trading-only fields; harmless/unused in backtest or paper mode. See
        # guards.py and live_broker.py -- these are read there, not here.
        self.max_order_notional_usd = risk.get('max_order_notional_usd')
        self.confirm_live = config.get('confirm_live', False) is True

        # Optional  trading:  section -- sessions and order handling (see algo/options.py and
        # STRATEGY_FORMAT.md). The dashboard's Trading options panel overrides it once saved.
        trading = config.get('trading')
        try:
            options.validate(trading or {}, f'{source_path}: trading')
            self.trading_raw = dict(trading or {})
        except (options.OptionsError, TypeError, ValueError) as e:
            raise StrategyError(str(e))

        # Optional  hedge:  section (jb-hedge, algo/autohedge.py): a second fund held with each position in backtests.
        # Paper and live trading hedge only when Auto-Hedge is turned on on the algo tab.
        try:
            self.hedge = autohedge.parse(config.get('hedge'), self.symbol, source_path)
        except ValueError as e:
            raise StrategyError(str(e))

    @classmethod
    def from_yaml(cls, path: str) -> "Strategy":
        with open(path) as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            raise StrategyError(f"{path}: not a strategy file (expected name:, symbol:, indicators: ...)")
        return cls(config, source_path=path)

    def compute_indicators(self, df):
        """Adds every configured indicator to `df` (a bars DataFrame with at least
        'close', ideally 'high'/'low'/'volume' too) as new columns, named
        '<kind>_<period>' for single-value indicators (e.g. 'rsi_14', 'sma_50') or
        '<kind>_<subcolumn>' for multi-column ones (e.g. 'macd_hist', 'bollinger_upper').
        Mutates and returns df."""
        for spec in self.indicator_specs:
            kind = spec.get('kind')
            if kind in ind.REGISTRY:
                period = spec.get('period')
                col_name = spec.get('as') or (f"{kind}_{period}" if period else kind)
                df[col_name] = ind.REGISTRY[kind](df, spec)
            elif kind in ind.REGISTRY_MULTI:
                sub = ind.REGISTRY_MULTI[kind](df, spec)
                prefix = spec.get('as', kind)
                for col in sub.columns:
                    df[f'{prefix}_{col}'] = sub[col]
            else:
                raise StrategyError(
                    f"{self.source_path}: unknown indicator kind {kind!r}. Available: "
                    f"{sorted(ind.REGISTRY) + sorted(ind.REGISTRY_MULTI)}")
        return df

    def row_dict(self, df, i: int) -> dict:
        """The columns available to a rule when evaluating bar `i` of `df`: every
        indicator column plus the raw OHLCV columns that exist."""
        row = {}
        for col in df.columns:
            v = df[col].iloc[i]
            row[col] = float(v) if v == v else float('nan')  # keep NaN as NaN, not skip it
        return row

    @staticmethod
    def _ready(rule, row):
        """A rule can run once every column IT uses has a value (an indicator still warming
        up, or one that is undefined right now, only blocks the rules that use it)."""
        for n in getattr(rule, 'names', row.keys()):
            v = row.get(n)
            if v is not None and v != v:
                return False
        return True

    def check_entry(self, df, i: int) -> bool:
        row = self.row_dict(df, i)
        if not self._ready(self.entry_rule, row):
            return False
        return self.entry_rule(row)

    def check_exit(self, df, i: int) -> bool:
        row = self.row_dict(df, i)
        if not self._ready(self.exit_rule, row):
            return False
        return self.exit_rule(row)

    def position_size(self, equity: float, price: float) -> int:
        """Whole shares to trade, given current portfolio equity and the current price."""
        if self.size_shares is not None:
            shares = self.size_shares
        else:
            shares = (self.size_pct_of_equity / 100.0) * equity / price
        return max(0, int(shares))
