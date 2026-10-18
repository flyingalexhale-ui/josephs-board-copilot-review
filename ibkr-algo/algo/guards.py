"""Safety checks that run before ANY live order. Identical logic and shape to
webull-algo/algo/guards.py -- the two-switch pattern isn't specific to either broker -- just
with its own environment variable name so a machine that's confirmed live trading for one
broker's algo never accidentally also confirms it for the other's. Deliberately small and
easy to read in full before you ever trust it with real money -- that's more important here
than being clever.

None of these checks apply to backtest or paper mode -- they only run in live_broker.py,
right before a real order would go out.
"""
import os


class GuardRejected(Exception):
    """Raised to block an order. The order is NOT placed if this is raised -- it's not a
    warning, it's a hard stop."""
    pass


# Two independent switches, both required, before ANY live order can ever be placed -- on
# purpose, so flipping a strategy file's `confirm_live: true` alone is never enough by itself
# to start risking real money; you also have to deliberately set this environment variable on
# the machine actually running it. Named IBKR_-specific (not WEBULL_-) so confirming live
# trading for webull-algo never accidentally also confirms it here, or vice versa.
LIVE_CONFIRM_ENV_VAR = 'IBKR_LIVE_TRADING_CONFIRM'
LIVE_CONFIRM_VALUE = 'I_UNDERSTAND_THE_RISK'


def check_live_trading_enabled(strategy):
    if not strategy.confirm_live:
        raise GuardRejected(
            f"strategy {strategy.source_path!r} does not have 'confirm_live: true' set. "
            f"Live orders are refused until you add that line to the strategy file "
            f"yourself -- this is not something to set casually.")
    cap = getattr(strategy, 'max_order_notional_usd', None)
    try:
        cap_ok = cap is not None and float(cap) > 0
    except (TypeError, ValueError):
        cap_ok = False
    if not cap_ok:
        raise GuardRejected(
            f"strategy {strategy.source_path!r} has no risk: max_order_notional_usd. Live trading "
            f"needs it -- it is the most any single order may be worth (e.g. 2000), and it also "
            f"sets the size of each trade when the strategy sizes by percent of equity.")
    if os.environ.get(LIVE_CONFIRM_ENV_VAR) != LIVE_CONFIRM_VALUE:
        raise GuardRejected(
            f"environment variable {LIVE_CONFIRM_ENV_VAR} is not set to "
            f"{LIVE_CONFIRM_VALUE!r} on this machine. Live orders are refused until both "
            f"this AND the strategy file's confirm_live are set -- two separate switches, "
            f"deliberately, so one typo or one copy-pasted strategy file can never alone "
            f"turn on real trading.")


def check_order(strategy, side: str, symbol: str, shares: int, price: float,
                 max_order_quantity: int = 10_000, max_order_notional_usd: float = None,
                 symbol_whitelist=None, closing=False):
    """Raises GuardRejected with a clear reason if this order shouldn't go out. Call this
    immediately before every single live order, not just once at startup -- strategy state
    or its config could change between orders."""
    if shares <= 0:
        raise GuardRejected(f'computed share count is {shares} (must be positive)')
    if shares > max_order_quantity:
        raise GuardRejected(
            f'order for {shares} shares of {symbol} exceeds max_order_quantity '
            f'({max_order_quantity}). Raise this explicitly in guards.py or the caller if '
            f'you really mean it -- this default is intentionally conservative.')
    notional = shares * price
    effective_cap = max_order_notional_usd if max_order_notional_usd is not None else strategy.max_order_notional_usd
    if effective_cap is not None and notional > effective_cap and not closing:
        # (an order that CLOSES the position is always allowed: after a price rise its value
        # can be above the cap, and refusing it would leave the position stuck open)
        raise GuardRejected(
            f'order notional ${notional:,.2f} ({shares} sh x ${price:.2f}) for {symbol} '
            f'exceeds this strategy\'s risk.max_order_notional_usd (${effective_cap:,.2f})')
    whitelist = symbol_whitelist
    if whitelist and symbol not in whitelist:
        raise GuardRejected(
            f'{symbol} is not in the configured symbol whitelist {sorted(whitelist)}')
    if side not in ('buy', 'sell'):
        raise GuardRejected(f"side must be 'buy' or 'sell', got {side!r}")
