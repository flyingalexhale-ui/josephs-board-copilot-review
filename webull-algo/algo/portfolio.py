"""A simple single-symbol-at-a-time virtual ledger: cash, one open position, a trade log,
and an equity curve. Used identically by the backtester and the paper trader, so a
strategy's simulated behavior is computed the exact same way in both places -- the only
difference between backtest/paper and live is whether live_broker.py's real order calls
also fire alongside this ledger (see paper_trader.py).

Deliberately long/short-agnostic at this layer: 'buy' opens a long, 'sell' (with no
existing position) opens a short, and the matching opposite side always closes whatever is
open. Commission and slippage are modeled simply and explicitly, not hidden -- see the
`commission_per_share` / `slippage_pct` fields.
"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Trade:
    time: datetime
    side: str          # 'buy' or 'sell'
    shares: int
    price: float
    reason: str         # 'entry', 'exit-rule', 'stop-loss', 'take-profit'
    commission: float = 0.0

    @property
    def notional(self) -> float:
        return self.shares * self.price


@dataclass
class Position:
    side: str        # 'long' or 'short'
    shares: int
    entry_price: float
    entry_time: datetime
    peak: float = None   # best price since entry (highest for a long, lowest for a short): trailing stop


@dataclass
class Portfolio:
    starting_cash: float = 100_000.0
    commission_per_share: float = 0.0
    slippage_pct: float = 0.0

    cash: float = field(init=False)
    position: Position = field(default=None, init=False)
    trades: list = field(default_factory=list, init=False)
    equity_curve: list = field(default_factory=list, init=False)  # [(time, equity), ...]

    def __post_init__(self):
        self.cash = self.starting_cash

    def _fill_price(self, side: str, quoted_price: float, exact: bool = False) -> float:
        """Slippage always works against you: buying fills a hair above the quote,
        selling a hair below -- a conservative assumption, on purpose. exact=True: the price
        is already final (a real broker fill, or a simulated limit fill already costed)."""
        if exact or self.slippage_pct == 0:
            return quoted_price
        adj = quoted_price * (self.slippage_pct / 100.0)
        return quoted_price + adj if side == 'buy' else quoted_price - adj

    def open_position(self, time, side: str, shares: int, quoted_price: float, reason: str, exact: bool = False):
        if self.position is not None:
            raise ValueError('a position is already open; close it before opening another')
        if shares <= 0:
            return
        fill = self._fill_price('buy' if side == 'long' else 'sell', quoted_price, exact)
        commission = shares * self.commission_per_share
        cost = shares * fill
        if side == 'long':
            self.cash -= cost + commission
        else:  # short: receive proceeds now, pay commission
            self.cash += cost - commission
        self.position = Position(side=side, shares=shares, entry_price=fill, entry_time=time)
        self.trades.append(Trade(time=time, side='buy' if side == 'long' else 'sell',
                                  shares=shares, price=fill, reason=reason, commission=commission))

    def close_position(self, time, quoted_price: float, reason: str, exact: bool = False):
        if self.position is None:
            return
        pos = self.position
        exit_side = 'sell' if pos.side == 'long' else 'buy'
        fill = self._fill_price(exit_side, quoted_price, exact)
        commission = pos.shares * self.commission_per_share
        proceeds = pos.shares * fill
        if pos.side == 'long':
            self.cash += proceeds - commission
        else:  # covering a short: pay to buy back
            self.cash -= proceeds + commission
        self.trades.append(Trade(time=time, side=exit_side, shares=pos.shares, price=fill,
                                  reason=reason, commission=commission))
        self.position = None

    def reduce_position(self, time, shares: int, quoted_price: float, reason: str, exact: bool = False):
        """Close PART of the open position (an exit order that was only partly filled)."""
        pos = self.position
        if pos is None or shares <= 0:
            return
        if shares >= pos.shares:
            return self.close_position(time, quoted_price, reason, exact)
        exit_side = 'sell' if pos.side == 'long' else 'buy'
        fill = self._fill_price(exit_side, quoted_price, exact)
        commission = shares * self.commission_per_share
        proceeds = shares * fill
        if pos.side == 'long':
            self.cash += proceeds - commission
        else:
            self.cash -= proceeds + commission
        self.trades.append(Trade(time=time, side=exit_side, shares=shares, price=fill,
                                  reason=reason + ' (partial)', commission=commission))
        pos.shares -= shares

    def unrealized_pnl(self, quoted_price: float) -> float:
        if self.position is None:
            return 0.0
        pos = self.position
        diff = (quoted_price - pos.entry_price) if pos.side == 'long' else (pos.entry_price - quoted_price)
        return diff * pos.shares

    def equity(self, quoted_price: float) -> float:
        """Portfolio value mark-to-market at `quoted_price`. For a long, the position adds
        shares*price to cash (you own it outright). For a short, cash already holds the
        sale proceeds from entry, and shares*price is what it would cost to buy them back
        right now to close out -- a LIABILITY, so it's subtracted, not added."""
        if self.position is None:
            return self.cash
        notional = self.position.shares * quoted_price
        return self.cash + notional if self.position.side == 'long' else self.cash - notional

    def mark(self, time, quoted_price: float):
        self.equity_curve.append((time, self.equity(quoted_price)))

    def stop_take_hit(self, quoted_price: float, stop_loss_pct, take_profit_pct):
        """Returns 'stop-loss', 'take-profit', or None for the currently open position."""
        if self.position is None:
            return None
        pos = self.position
        pct_move = ((quoted_price - pos.entry_price) / pos.entry_price * 100) \
            if pos.side == 'long' else ((pos.entry_price - quoted_price) / pos.entry_price * 100)
        if stop_loss_pct is not None and pct_move <= -abs(stop_loss_pct):
            return 'stop-loss'
        if take_profit_pct is not None and pct_move >= abs(take_profit_pct):
            return 'take-profit'
        return None
