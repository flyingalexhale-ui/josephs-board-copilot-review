# Joseph's Board -- Webull trading algo

Plug in a trading theory as a YAML file (no code), backtest it against history, paper-trade it
against Webull's live prices with no money at risk, and only once you're satisfied, flip two
separate switches to let it place real orders. Works exactly like `ibkr-algo/` (same strategy
files, same dashboard tab, same safety rules) -- it just talks to Webull's OpenAPI.

Everything is run with the `./jb` command in the main folder; you never start these files by
hand. Webull access must be approved once with `./jb webull-login`.

## What's in here

```
algo/
  indicators.py   sma, ema, rsi, macd, bollinger, atr, vwap, roc, stochastic
  rules.py        safe parser for rule strings like "rsi_14 < 30 and close > sma_50"
  strategy.py     loads a strategy file, computes its indicators, evaluates its rules
  options.py      trading options: sessions, limit/market orders, trailing stop (see below)
  core_trader.py  ticks -> 1-minute bars -> rules -> simulated or real orders (shared with ibkr-algo)
  portfolio.py    the ledger: cash, position, trades, equity curve
  backtest.py     bar-by-bar, no-lookahead backtester + performance numbers
  runstate.py     saved state, market sessions and the NYSE holiday calendar
  guards.py       the two-switch safety gate every real order must pass
  live_broker.py  the ONLY file that sends real orders to Webull
  paper_trader.py streams Webull prices and runs a strategy in paper or live mode
strategies/       example strategies (sma_crossover, rsi_reversal, bollinger_breakout)
run_backtest.py   the backtest command (./jb backtest runs it)
STRATEGY_FORMAT.md  the strategy file format -- read it before writing your own
trading_options.json  appears once you save the dashboard's Trading options panel
```

The files without broker-specific logic are identical in `ibkr-algo/`, so a strategy file
works unchanged with either.

## 1. Write a strategy

Copy one of `strategies/*.yaml` and read **STRATEGY_FORMAT.md** (indicators, rule syntax,
position size, stop-loss/take-profit, and the optional `trading:` section). `symbol` is a plain
US stock/ETF ticker (`"AAPL"`, `"SPY"`); futures and options aren't traded. Live Webull trading
is long-only (a strategy with `entry: side: sell` can be backtested and paper traded).

## 2. Backtest it

```bash
./jb backtest webull strategies/rsi_reversal.yaml                     # the last 1200 1-minute bars
./jb backtest webull strategies/rsi_reversal.yaml --timespan M5        # 5-minute bars, further back
./jb backtest webull strategies/rsi_reversal.yaml --sessions all       # pre-market, after-hours, overnight too
./jb backtest webull strategies/rsi_reversal.yaml --recorded           # the board's own 35-day record
./jb backtest webull strategies/rsi_reversal.yaml --csv ~/Downloads/spy.csv
```

* The default is 1-minute bars, because that's what paper and live trading run on (`sma_20`
  means 20 minutes there). Webull returns at most 1200 bars per request (about 20 hours of
  minutes, overnight included); for longer tests use `--recorded` (the board's own
  minute-by-minute record, 35 days around the clock, one price per minute so highs/lows are
  rougher) or a CSV.
* Sessions and order handling follow the Trading options panel's "Paper & backtest" column
  (see section 4); `--sessions`, `--order-type`, `--limit-offset-pct`, `--cancel-after-s`,
  `--trailing-stop-pct`, `--extended-hours-cost-pct` change them for one run.
* Results appear on the dashboard's Webull Algo tab, and as a trade-log CSV and chart next to
  the strategy file. Nothing here touches your account.

## 3. Paper trade it

Set `RUN_WEBULL_ALGO=on` and `WEBULL_ALGO_STRATEGY=strategies/<your file>.yaml` in
`settings.env`, then `./jb restart`. It runs in the background (restarted automatically if
anything goes wrong), streams Webull's live prices (overnight included), builds 1-minute bars
and runs your rules -- every order is simulated. The dashboard's Webull Algo tab shows it live.
Its state survives restarts.

**Overnight (8 pm - 4 am ET) needs Webull's overnight data plan.** Without it, Webull refuses the overnight prices and
the algo never trades overnight (the algo tab's note line says *Overnight Prices (Webull): Off*). It asks Webull again
every 3 hours and at 7:45 pm ET -- as do the Webull board and the scanner -- so once the plan has the overnight
session, overnight prices start by themselves and the algo trades overnight when its Trading options allow: **no
`./jb restart` is needed** (a restart only makes it immediate). Then, overnight: the backup stop is asked for in
Webull's night session (if Webull refuses stop orders then, a day stop rests instead and the night one is not asked
for again for a week), Auto-Hedge hedges if Webull takes the fund overnight (if not, it waits for 4 am), and the
"prices stopped" push watches the overnight prices too. `strategies/overnight_gap.yaml` is the Webull copy of the
overnight gap strategy. Short sales are never sent overnight (Webull takes none then).

## 4. Trading options: sessions and orders

On the dashboard's **Webull Algo** tab, the **Trading options** panel sets:

| Setting | What it does |
|---|---|
| Sessions (two columns: *Paper & backtest* and *Live*) | Regular hours (9:30 am - 4:00 pm ET) are always on. Tick **pre-market** (4:00 - 9:30 am), **after-hours** (4:00 - 8:00 pm) and **overnight** (8:00 pm - 3:50 am, Sunday to Thursday nights) separately for paper trading/backtests and for live trading -- e.g. paper-test overnight while live stays off overnight. |
| Order type | **Limit** (default) or **Market**. Market orders are only sent 9:30 am - 4:00 pm; outside those hours the algo sends a limit order instead (Webull takes only limit orders then). |
| Limit price | % from the last trade price (default 0.05): buys above it, sells below it (at least one cent), so they fill quickly. A negative number waits for a better price -- for entries only; exits (exit rule, stop-loss, trailing stop) are always priced to fill. |
| Cancel if not filled after | seconds (default 60). An unfilled order is cancelled and shown as "unfilled" -- not an error; the rule simply fires again later if it still holds. |
| Trailing stop | optional %: the algo exits when the price falls that far from its best since the entry. The algo watches it itself, so it only works while the algo is running -- it is not an order resting at Webull. The exit uses the order type above (a limit exit = a trailing stop-limit). |
| Extra cost outside regular hours | paper/backtest only (default 0.1% per fill): extended-hours spreads are wider. |

Saving writes `trading_options.json` in this folder; the running algo picks it up within about 5
seconds. "Use the strategy file's settings" deletes it, and the strategy file's `trading:`
section (or the built-in defaults) applies again.

How Webull gets each order (see `algo/live_broker.py`): regular hours -- a limit or market order
with trading session CORE; pre-market/after-hours -- a limit order with session ALL (Webull's
extended hours); overnight -- a limit order with session NIGHT (Webull's overnight session runs
until 4:00 am; the algo stops at 3:50 like IBKR's). Time in force DAY; the algo cancels unfilled
orders itself.

Paper trading fills a limit order only if a later trade price reaches it (after slippage and the
extra extended-hours cost), within the cancel time and in the same session; market orders fill
at once.

## 5. Going live (real money -- read all of this first)

Two independent switches must both be on:

1. In the strategy file: `confirm_live: true`, plus `risk: max_order_notional_usd:` (the most
   one order may be worth -- it also caps each trade's size).
2. When you start it: `./jb live webull strategies/<file>.yaml` asks you to type
   I_UNDERSTAND_THE_RISK (that sets `WEBULL_LIVE_TRADING_CONFIRM` for that window only).

**Go Live button (no Terminal):** the algo tab's *Live Trading (Real Money)* card starts the same live trader as a
background job (launchd, `./jb live-service`), with the strategy the paper algo runs. It needs a board password
(JB_PASSWORD), the strategy file's `confirm_live: true` and order cap, and I_UNDERSTAND_THE_RISK typed on the page; it
shows the go-live checklist first. While it runs: LIVE on the tab and the Phone page, the paper trader off, your phone
pushed if it stops on its own. **Stop Live** stops it cleanly (orders working are cancelled, nothing sent, the position
stays). After a Mac restart it does not start again unless you tick that option (off by default). `./jb live` below
still works as a backup; only one live run per broker at a time.

It runs in that Terminal window (Ctrl+C stops it and cancels any order still working; the paper trader pauses meanwhile) and prints a
5-second warning with the sessions and order settings in force. Rules it always follows:

* it refuses to start if your Webull position in the symbol differs from the algo's own record,
  or an order for it is still open; after fixing that, `./jb live-reset webull <file>` clears
  the record
* one order at a time; the ledger books only what Webull reports filled (partial fills included)
* a rejected order: nothing booked, no new order for 15 minutes; 3 rejections in a row stop it
* an order whose outcome can't be confirmed pauses live trading until you've checked (the
  dashboard shows a red banner)
* several accounts on one API key: set `WEBULL_ACCOUNT_ID` in settings.env (it must be one of the key's accounts)
* prices stop (no price for 5 min in regular hours, 15 min pre-market / after-hours, 30 min overnight -- the same
  wait times as the IBKR algo): it reconnects, and your phone gets one push per outage ("Webull algo: prices
  stopped") when it may trade then or holds a position
* the stream loses its link to Webull (or Webull says another session took over the market data): safe mode, no
  orders until it is back and the position matches Webull
* short-selling strategies (`entry: side: sell`): live only on a margin account; at start-up the algo asks Webull to
  preview a 1-share short sale (never placed) and refuses if Webull says no. The opening order is a SHORT, the cover a
  BUY. Never overnight (Webull takes no short sales then; paper and backtests skip them too)

Start with the smallest size your strategy allows, watch the first trades, and always check
fills in the Webull app.

## Safety model, in one paragraph

Backtesting and paper trading never touch your account -- they're arithmetic on an in-memory
ledger. Only `algo/live_broker.py` sends real orders, only after `algo/guards.py`'s two switches
(the strategy file AND the confirmation typed in that window) pass, checked again before every
order, together with a share-count limit and the order-value cap (modelled on Webull's own
reference guards). Nothing here replaces understanding your own strategy: read
STRATEGY_FORMAT.md, backtest, paper trade for real, and go live small.
