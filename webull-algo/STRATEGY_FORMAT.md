# Strategy file format

A strategy is one YAML file. No code to write -- you describe indicators and two plain-English
rule strings, and the engine does the rest. Start from one of `strategies/sma_crossover.yaml`,
`strategies/rsi_reversal.yaml`, or `strategies/bollinger_breakout.yaml` and edit it.

```yaml
name: "My theory"        # just a label, shows up in backtest output
symbol: "AAPL"            # one symbol per strategy file

indicators:                # list of indicators to compute; add as many as your rules need
  - kind: sma
    period: 20              # -> column "sma_20" (auto-named from kind+period)
  - kind: rsi
    period: 14
    as: "my_rsi"            # optional override -- column is "my_rsi" instead of "rsi_14"

entry:
  side: buy                 # "buy" (go long) or "sell" (go short)
  when: "sma_20 > sma_50"   # a rule string -- see "Rule syntax" below

exit:
  when: "sma_20 < sma_50"   # evaluated only while a position from this strategy is open

position_size:
  shares: 10                 # EITHER a fixed share count...
  # pct_of_equity: 25        # ...OR a percent of current equity (pick one, not both)

risk:
  stop_loss_pct: 8            # closes the position if price moves this % against you,
  take_profit_pct: 20         # or this % in your favor -- checked every bar, independent
                               # of your exit rule (whichever fires first wins)
  max_order_notional_usd: 25000  # LIVE MODE: required. The most one order may be worth; each
                                  # entry is sized down to fit. Ignored in backtest/paper.

trading:                  # OPTIONAL -- sessions and order handling. The dashboard's Trading
                          # options panel replaces this whole section once you save it there.
  paper_sessions: [premarket, afterhours, overnight]   # paper trading + backtests
  live_sessions: [premarket, afterhours]               # live trading (regular hours always on)
  order_type: limit        # limit (default) or market (market only 9:30-16:00 ET)
  limit_offset_pct: 0.05   # limit price: buys this % above the last price, sells this % below
  cancel_after_s: 60       # cancel a limit order not filled within this many seconds
  trailing_stop_pct: 1.5   # optional: exit 1.5% below the best price since entry
  extended_hours_cost_pct: 0.1   # paper/backtest: extra cost per fill outside regular hours

max_open_positions: 1   # currently only 1 is actually supported (single-symbol, single-
                         # position engine) -- reserved for a future multi-position version

confirm_live: false      # must be manually flipped to true, AND the machine running it must
                          # have WEBULL_LIVE_TRADING_CONFIRM=I_UNDERSTAND_THE_RISK set, before
                          # this strategy can EVER place a real order. See guards.py.
```

## Available indicators (`indicators:` list)

Single-column (become one column named `<kind>_<period>`, or your `as:` override):

| kind  | params (all optional, shown with defaults) | notes |
|-------|---------------------------------------------|-------|
| `sma` | `period: 20` | simple moving average of close |
| `ema` | `period: 20` | exponential moving average of close |
| `rsi` | `period: 14` | 0-100, proper Wilder smoothing |
| `atr` | `period: 14` | needs high/low/close |
| `vwap`| (none) | needs high/low/close/volume; resets each calendar day |
| `roc` | `period: 10` | rate of change, as a percent |

Multi-column (become several columns named `<as-or-kind>_<subcolumn>`):

| kind         | params (defaults) | columns produced |
|--------------|--------------------|------------------|
| `macd`       | `fast: 12, slow: 26, signal: 9` | `macd_macd`, `macd_signal`, `macd_hist` |
| `bollinger`  | `period: 20, num_std: 2.0` | `bollinger_mid`, `bollinger_upper`, `bollinger_lower` |
| `stochastic` | `k_period: 14, d_period: 3` | `stochastic_%k`, `stochastic_%d` |

You can always also reference the raw bar columns directly in a rule: `open`, `high`, `low`,
`close`, `volume`.

## Rule syntax (`entry.when` / `exit.when`)

Deliberately small and safe -- these strings are never run through Python's `eval()`; they're
parsed and walked by hand (see `algo/rules.py`) so a rule can only ever compare numbers, never
call a function or do anything else. Allowed:

- column names (from `indicators:`, or the raw OHLCV columns)
- numbers
- arithmetic: `+ - * /`
- comparisons: `< <= > >= == !=`
- boolean combinators: `and`, `or`, `not`, and parentheses

Examples:
```
rsi_14 < 30
sma_20 > sma_50 and volume > 1000000
close > bollinger_upper or rsi_14 > 80
not (macd_hist < 0)
```

A rule only fires once every referenced column has a real (non-NaN) value -- so nothing
triggers during an indicator's warm-up period (e.g. the first 50 bars for a 50-period SMA).

**Important limitation:** `sma_20 > sma_50` is a *state* rule ("fast is currently above
slow"), not a *crossing-edge* rule ("fast just crossed above slow this bar"). With this
engine, once you're in a position, the exit rule (or your stop-loss/take-profit) is what gets
you out -- the entry rule firing again while already in a position does nothing (the engine
only opens a new position when flat). This matches how most people describe these theories in
plain English, but if you specifically want an edge-only trigger, add an explicit indicator for
"was it below last bar" -- that's not built in yet.

## Position sizing

- `shares: N` -- always trade a fixed N shares per entry.
- `pct_of_equity: P` -- trade `P%` of current portfolio equity, converted to whole shares at
  the entry price (e.g. `pct_of_equity: 25` with $100,000 equity and a $50 stock trades 500
  shares). Recomputed fresh at every entry, so it compounds (or shrinks) with your results.

Exactly one of the two must be set.

## Trading sessions and orders (`trading:`)

Everything in `trading:` is optional; left out, the algo trades regular hours only (9:30 am -
4:00 pm ET, 1:00 pm on early-close days, NYSE holidays skipped) with limit orders 0.05% from
the last price, cancelled after 60 seconds. Most people set these on the dashboard instead (the
algo tab's **Trading options** panel); a saved panel overrides this section entirely, and "Use
the strategy file's settings" there switches back to it.

| Key | Values | Meaning |
|---|---|---|
| `paper_sessions` | list of `premarket`, `afterhours`, `overnight` | extra sessions for paper trading and backtests |
| `live_sessions` | same | extra sessions for live trading -- leave `overnight` out to never trade overnight |
| `order_type` | `limit` / `market` | market orders are only sent in regular hours; outside them a limit order is sent |
| `limit_offset_pct` | -5 to 5 (default 0.05) | limit price = last price +/- this %, at least one tick; negative waits for a better price (entries only -- exits are always priced to fill) |
| `cancel_after_s` | 5 to 3600 (default 60) | an unfilled limit order is cancelled ("unfilled", not an error) |
| `trailing_stop_pct` | 0.01 to 50, or leave out | exit when the price falls this % from its best since entry (rises, for a short); watched by the algo while it runs |
| `extended_hours_cost_pct` | 0 to 5 (default 0.1) | paper/backtest only: extra cost per fill outside regular hours |

Sessions (US Eastern): pre-market 4:00-9:30 am; after-hours 4:00-8:00 pm (1:00-5:00 pm on
early-close days); overnight 8:00 pm-3:50 am on the nights before trading days (Sunday to
Thursday nights). Rules are only checked, and orders only sent, in the sessions turned on.

## What this backtester does and doesn't model

- **No lookahead**: a signal detected on bar `i` fills on a later bar, never bar `i`'s own
  close -- the single most common way homegrown backtests silently cheat. A market order fills
  at bar `i+1`'s open; a limit order (limit = bar `i`'s close +/- `limit_offset_pct`) fills on
  the first later bar whose low (buy) or high (sell) reaches it, within `cancel_after_s` and in
  the same session -- otherwise it counts as unfilled.
- **Sessions**: rules run and orders fill only in regular hours plus `paper_sessions`; fills
  outside regular hours cost `extended_hours_cost_pct` more.
- **Trailing stop**: triggered when a bar's low reaches the stop (from the best price up to the
  previous bar); the exit then fills from the next bar on.
- **Stop-loss/take-profit** are checked against each bar's *close* while a position is open,
  not intrabar -- a real stop could also be hit on a wick this never sees, so live results can
  differ, especially on volatile bars. With market orders in regular hours they close at that
  close; otherwise an exit order is sent like any other.
- **Slippage** (if you pass `--slippage-pct` to `run_backtest.py`) always works against you:
  buys fill a bit above the quote, sells a bit below.
- **Commission** (`--commission-per-share`) is a flat per-share cost, charged on both entry
  and exit.
- Single symbol, single open position at a time -- no portfolio-level correlation or margin
  modeling.

None of this replaces paper trading your theory for a while on real, live data before ever
turning `confirm_live` on -- a backtest only ever proves a theory worked on the past.

## Hedge (`hedge:`, optional)

A second fund (or, for the IBKR algo, an MNQ / MES micro future) held next to each position in **backtests**: opened
with the entry, closed with the exit, from the board's own price record of it, at the same costs. The algo tab shows
the result alone and with the hedge. The board's Hedge Ideas (My Trading tab) write this section for you.

```yaml
hedge:
  symbol: SQQQ        # the fund bought as the hedge (MNQ / MES: sold against a long position)
  ratio_pct: 100      # how much of the position to hedge
  dollar_ratio: 1.0   # $ of the hedge per $1 of the position (default ratio_pct / 100)
  side: buy           # buy (funds) or sell (futures); written for buy strategies
```

Paper and live trading hedge only when **Auto-Hedge** is turned on on the algo tab (default off; live hedging has
its own switch). See algo/autohedge.py.
