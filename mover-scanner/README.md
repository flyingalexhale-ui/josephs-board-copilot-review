# ETF Mover Scanner

Watches a list of ETFs against live Webull AND IBKR data feeds at once, and flags any that
move enough in a rolling ~5-minute window to be worth a look -- by price change, volume
surge, volatility (range) expansion, or a slower multi-window grind. A separate project from
Joseph's Board and the webull-algo trading framework -- nothing in either of those was
touched to build this.

## What's in here

```
scanner/
  aggregator.py   -- buckets ticks into fixed-length (default 5-min) OHLCV windows per symbol
  detectors.py     -- the 4 move checks: price_delta_pct, cumulative_delta_pct, volume_surge,
                        range_expansion -- pluggable, add your own the same way
  config.py         -- loads and validates watchlist.yaml
  picks.py           -- research picks: daily-chart signals explained in plain English
  scanner.py         -- orchestrator: feeds -> windows -> detectors -> log + WebSocket
  feeds/
    ibkr_feed.py     -- IBKR tick feed (ib_async), same connection pattern as ibkr_bridge.py
    webull_feed.py    -- Webull tick feed, same pattern as webull_bridge.py / paper_trader.py
run_scanner.py    -- CLI entry point, run this
watchlist.yaml    -- YOUR list of ETFs + thresholds, edit this freely
movers.html       -- the live board page, open this in a browser
requirements.txt
com.josephsboard.moverscanner.plist -- launchd job for always-on, self-healing operation
```

## Setup

```bash
cd mover-scanner
pip3 install -r requirements.txt
```

`webull-openapi-python-sdk` isn't on PyPI (see `requirements.txt`'s note) -- install it from
Webull's GitHub if you want the Webull feed. The scanner runs fine on IBKR alone, or Webull
alone, if you'd rather skip one -- see watchlist.yaml's `feeds:` block.

## Edit your watchlist

Open **watchlist.yaml**. The `symbols:` list ships with ~30 common sector/index/leveraged
ETFs as a starting point -- add or remove whatever you actually want watched, any number,
any tickers. Also in there: how big a window to bucket ticks into (`window_minutes`,
default 5) and each detector's threshold.

## Switching between ETFs and stocks

The Scanner tab has **ETFs | Stocks** buttons at the top. Tapping one makes the scanner restart
on the other list (about 10 seconds; the rolling history starts fresh). The choice is saved in
`scan_mode.txt` and survives restarts.

- ETFs mode watches the `symbols:` in **watchlist.yaml**.
- Stocks mode watches the `symbols:` in **stocklist.yaml** (30 heavily traded US stocks to start,
  the same count as the ETF list so IBKR market-data line use doesn't grow). Its `detectors:`
  block, if present, replaces watchlist.yaml's thresholds for stocks; everything else
  (window size, feeds) comes from watchlist.yaml.
- If stocklist.yaml has a mistake, the scanner goes back to ETFs and the page says why.

## How the detectors work

Every ~5 minutes (or whatever `window_minutes` you set), each symbol's window closes and
gets checked against every detector turned on in `watchlist.yaml`:

- **price_delta_pct** -- this window's own open-to-close % move. The plainest reading of
  "moving up or down every 5 minutes." Default: flag at +/-0.5%.
- **cumulative_delta_pct** -- the % move over the last several windows added together (default:
  3 windows = 15 minutes). Catches a slow grind that never has one dramatic single window.
- **volume_surge** -- this window's volume vs. the trailing average of the preceding
  windows. Default: flag at 3x.
- **range_expansion** -- this window's high-low range vs. the trailing average range.
  Catches a volatile window that whipped both ways and closed flat -- something
  price_delta_pct alone would miss.

All four can fire independently on the same window; the scanner reports every one that did,
not just the first. Turn any one off for everything by setting it to `false` in
watchlist.yaml instead of deleting it.

**No detector fires until it has enough history to compare against** (`min_baseline_windows`,
default 3) -- so right after the scanner starts, there's a short quiet period per symbol
before flags can happen. That's expected, not a bug.

## Research picks (the Picks button)

Separate from the 5-minute flags above, the scanner also looks at each symbol's **daily**
chart (about a year of daily prices, from IBKR, or Webull for any symbol IBKR can't supply)
and lists the ones showing well-known technical signals, each explained in plain English.
They are ideas to research, not buy/sell advice. Everything is simple rules in
`scanner/picks.py` -- no outside AI service and no extra data subscription.

19 signals in 5 families (price action, trend, momentum, volatility, volume): 20-day
breakout/breakdown, 52-week high/low, gap, big move (2.5+ standard deviations of the last 60
daily moves), 20/50 SMA cross, golden/death cross, 200-day cross, ADX strong trend, RSI 50
cross, RSI oversold/overbought, MACD signal cross, slow stochastic 14/3/3 turn, StochRSI turn,
CCI +/-100, Bollinger 20/2 band break with z-score, squeeze (narrowest BandWidth in 120 days),
squeeze breakout, unusual volume, Chaikin money flow. Context (not counted): long-term trend,
the Minervini trend template, 3-month strength vs SPY.

Picks are ranked by how many different families agree. While the market is open today's bar
follows the scanner's live regular-session prices and picks are rechecked every 3 seconds
(sent to the page only when something changed); daily history is reloaded every 5 minutes
9:25 am - 4:35 pm ET, hourly otherwise. The thresholds are the constants at the top of `scanner/picks.py`.

## Running it

```bash
export WEBULL_APP_KEY=...
export WEBULL_APP_SECRET=...
python3 run_scanner.py --config watchlist.yaml
```

IBKR needs IB Gateway/TWS running and logged in with the API enabled, same as
`ibkr_bridge.py` -- no extra flags needed here, it connects the same way (tries ports 4001,
4002, 7496, 7497 automatically). It uses a different IBKR client id (37) than
`ibkr_bridge.py`'s own (17) so both can be connected to IB Gateway at the same time without
colliding, if you run this alongside your board.

**Both feeds run at once, independently.** A symbol's rolling window is built from whichever
feed's ticks are actually arriving for it -- if one connection drops, the scanner keeps
working off the other; nothing needs picking or switching by hand.

Then open **movers.html** in a browser, and point it at the scanner:
- `ws://localhost:8767` if the browser is on the same machine running the scanner
- `ws://<spare Mac's Tailscale address>:8767` from your work desktop or phone, same idea as
  the board pages

The address and any token you set are remembered in that browser for next time.

## What you'll see

- A **live table**, one row per watched ETF: last price, this window's %-move-so-far,
  this window's running volume, how many baseline windows are built up, which feed it's
  currently getting ticks from, and how long ago it last updated. Click a column header to
  sort by it (defaults to biggest window %-move first).
- A **recent flags** panel: every window that tripped at least one detector, newest first,
  with the OHLCV for that window and which detector(s) fired and why. A flagged row in the
  table also gets a highlight for 15 seconds.

## Where it's logged

Every window, flagged or not, is appended to `logs/movers.jsonl` (one JSON object per line --
symbol, OHLCV, which detectors fired if any) for anything you want to load into a
spreadsheet or script later. Flagged windows ALSO get a plain-English line in
`logs/movers.log`, e.g.:

```
[2026-09-27 14:35:00] XLE    O:88.120 H:89.400 L:88.050 C:89.310 V:412,000 -- price_delta: up 1.35% in this window (>= 0.5% threshold); volume_surge: volume 412,000 is 4.1x the trailing 12-window average (100,000) -- threshold 3.0x
```

## Running it always-on, alongside your boards and the trading algo

`com.josephsboard.moverscanner.plist` is a launchd job -- same self-healing pattern
(`RunAtLoad` + `KeepAlive`) as everything else on the spare Mac.

```bash
mkdir -p logs
# edit the plist first: your Mac username, WEBULL_APP_KEY/SECRET, and the config path
cp com.josephsboard.moverscanner.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.josephsboard.moverscanner.plist

# check on it:
launchctl list | grep moverscanner
tail -f logs/scanner.out.log
```

This is read-only market-data scanning -- unlike the trading algo, there's no reason to keep
it out of an always-on job; it never places an order or touches your account.
