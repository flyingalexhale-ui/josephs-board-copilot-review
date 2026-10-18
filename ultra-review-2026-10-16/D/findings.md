# Reviewer D: delta hedging (Net Exposure, Hedge Calculator, Hedge Partners, Hedge Ideas, Auto-Hedge)

Work folder: /tmp/claude-0/-home-claude/d5f7f21c-dc3e-504d-8a31-86f6d5f4dac4/scratchpad/rev/D
New tests (run against a copy of the repo in repo/, never the original):
* tests/d-autohedge-test.py `<algo dir> <ibkr|webull>`: 14 checks on algo/autohedge.py + core_trader.py with a fake broker
  (13 FAIL = findings, 1 control PASS). Output: out-autohedge-ibkr.txt (14 checks), out-autohedge-webull.txt (13; the futures
  roll check is IBKR only).
* tests/d-hedge-gateway-test.py (JB_REPO=repo): 9 checks on gateway/hedge.py using the made-up record of tests/hedge-test.py
  (all 9 FAIL = findings, plus INFO lines with the numbers quoted below). Output: out-hedge-gateway.txt.
* tests/_probe_live_restart.py, tests/_probe_restart.py: first repros of D1 / D7 (output quoted in D1).

## Coverage

Read fully: gateway/hedge.py (1632 lines); ibkr-algo/algo/autohedge.py and webull-algo/algo/autohedge.py (diff: identical);
ibkr-algo/algo/core_trader.py and webull-algo/algo/core_trader.py (diff: identical); ibkr-algo/algo/live_broker.py;
webull-algo/algo/live_broker.py; web/hedge-ui.js; web/hedge-algo.js; HEDGE-2026-10-12.md; tests/hedge-test.py; web/legend.js
Delta Hedging section (lines 55-66) and the Auto-Hedge lines at 235/240. Read the parts autohedge uses: both paper_trader.py
(hedge streaming, _place_hedge, _hedge_leftovers, live start-up order of checks, Webull relay ticks), algo/runstate.py
(state save/restore, jsonsafe), algo/portfolio.py (Position), algo/guards.py check_order, gateway/mdata.py DEFAULT_PAIRS/pairs(),
gateway/portfolio.py positions/_last_price, gateway/brokerpos.py _ibkr/_webull/merge, gateway/trading.py
_algo_today/check_limits/trading_day/_last_price, gateway/killswitch.py futures tick step, lib/jb_webull_relay.py client,
bridges relay/follow, bin/run-service.sh + jb LIVE_RUNNING handling, tests/algo-stress-test.py hedge sections.

Tests run (baseline, on the repo copy): tests/hedge-test.py 70/70 PASS (baseline-hedge-test.txt); tests/algo-stress-test.py
ibkr 125 PASS / 0 FAIL and webull 158 PASS / 0 FAIL (baseline-stress-*.txt). These pass, but they miss D1 because the stress
test puts a Timestamp straight into the hedge record instead of reading it back from JSON (algo-stress-test.py:1459, 1468).

Checked and FINE:
* Leverage table (mdata.DEFAULT_PAIRS + EXTRA_FUNDS): every pairing is a real bull/bear pair on the stated index: TQQQ/SQQQ,
  SPXL/SPXS, UPRO/SPXU, UDOW/SDOW, TNA/TZA, SOXL/SOXS (SOXX as stand-in for the ICE semis index), TECL/TECS, FAS/FAZ,
  LABU/LABD, TMF/TMV, QLD/QID, SSO/SDS, DDM/DXD, UWM/TWM, USD/SSG, ROM/REW, UYG/SKF, ERX/ERY (2x since 2020), NUGT/DUST (2x),
  NVDL/NVDQ, TSLL/TSLZ, MSTU/MSTZ, BITX/SBIT; PSQ/SH/DOG/RWM -1x. Index lists (SQQQ/QID/PSQ, SPXU/SPXS/SDS/SH, bulls) and
  SECTOR_POOL are all real inverses. Futures multipliers MNQ $2, NQ $20, MES $5, ES $50, M2K $5, RTY $50, MYM $0.50, YM $5: right.
* Signs: inverse = -leverage; short positions via negative shares (value < 0); short inverse = positive; futures sign by qty;
  3x sector = 3 x measured beta of SOXX/XLK... to QQQ; single-stock 2x = 2 x measured stock beta; a plain stock = its own beta.
* Extended hours: rows use the board's latest recorded price (IBKR's overnight record included); no regular-hours-only price.
* Calculator math: units = want / (price x leverage x beta / 100); futures price x multiplier x beta / 100; fund hedges are only
  ever bought (a short is refused); rounding shown as "Leaves $X per 1%"; decay (L^2-L)/2 x daily variance + fee is the right
  formula (6x var for -3x, 1x var for -1x). Futures shown IBKR only; Webull futures refused in calc, ideas, Auto-Hedge.
* Hedge Ideas backtest: entries use only prices at/after the session start (no look-ahead in prices), exits 15 min before the
  session end, costs per fill on both legs; the generated strategy file's clock windows match the backtest's windows.
* Auto-Hedge: fires on the first 2-s tick after the position is booked (waits for the position's own order); size = dollar
  ratio x shares x price / hedge price; while the record is intact it follows the share count with a 25% band (no
  hedge/unhedge cycling on price moves); needs a hedge price under 10 min old; Stop / daily loss (KILL) close it, SELL ALL
  sends nothing and books it once gone, halted/cooldown/doubt/safe mode block new hedges, closes still allowed; buying power
  and order cap applied; CME 5-6 pm pause; the 4 am wait after an overnight refusal works (control check PASS); Webull leftover
  detection by jbhedge id / recorded id / symbol, and adoption of recorded fills (apart from D1); "outside" shares block a
  second hedge and are never sold. The hedge's result is in the algo's daily loss (gateway/trading.py _algo_today).
* Division by zero: every division in hedge.py / autohedge.py is guarded (stats vx/vy, _cut s0, unit_delta, bp/bh, px, main_shares).
* Time zones: ET via zoneinfo / watch.at_et, daily history points are 4 pm ET closes, early closes handled, the hedge day
  (8 pm ET start) matches trading.trading_day except its weekend roll-forward (harmless: nothing trades then).

## Findings (most severe first)

| ID | Severity | Title |
|---|---|---|
| D1 | HIGH | Restarting a live algo sells its open hedge (and buys it back) |
| D2 | HIGH (latent) | IBKR futures hedge doubles itself when the front month rolls |
| D9 | HIGH (logic) | Auto-Hedge defaults (Auto = inverse twin, 100%) make every algo trade a guaranteed small loss |
| D3 | MEDIUM | One empty position answer -> hedge forgotten and bought again (two hedges) |
| D4 | MEDIUM | A partly filled hedge is never topped up |
| D10 | MEDIUM (logic) | Calculator / Partners recommend the costliest way to get flat, never "sell part of what you hold" |
| D11 | MEDIUM | Futures without a board price counted at 1/multiplier; non-index futures counted as Nasdaq-100 |
| D12 | MEDIUM | Unknown inverse funds counted as LONG until history arrives |
| D13 | MEDIUM (logic) | Hedge Ideas ranking surfaces luck (9 of 20 ideas "profitable" on a record with no edge) |
| D5 | MEDIUM (latent) | Futures hedge orders sent while CME is closed (Fri 6-8 pm, early closes) |
| D6 | LOW | Only the first hedge rejection of a run is pushed |
| D7 | LOW | Paper hedge loses its position link on restart, then resizes on price moves |
| D8 | LOW | A part exit can sell more hedge than the account holds (IBKR: short) |
| D15 | LOW | Refused-before-sending hedge tries count toward the 30/day limit and pause live trading |
| D16 | LOW | IBKR: hours-old snapshot taken as the hedge's fresh price |
| D17 | LOW | IBKR start-up does not adopt a hedge fill from the previous run |
| D18 | LOW | Paper hedge costs/results unrealistic (futures charged 0.1%; no-price close at $0) |
| D14 | LOW | Short TQQQ: SQQQ labelled "Bull Twin" |

### D1 HIGH: Restarting a live algo sells its open hedge (and buys it back if live hedging is still on)
What happens: after any restart of a live run while the algo holds a position and its hedge, the first tick decides the hedge
belongs to "a new position" and sells all of it. If the live-hedging switch still counts, it buys the same hedge right back
(two real orders, two spreads and fees, extended-hours spreads); after a fresh Go Live / ./jb live (switch off for the new
run) the hedge is sold and NOT replaced, so the position is left unhedged with no push.
Detail: autohedge.py tick() line 473 `elif self.st.get('for_entry') and pos.entry_time != self.st['for_entry']`. In live,
filled() -> _mark_entry() stores pos.entry_time (a pandas Timestamp) and _order_done() saves it; atomic_write_json turns it
into an ISO string. After the restart the core's position is restored with entry_time = pd.Timestamp (runstate._ts) while the
hedge record has the string, and pd.Timestamp != str is always True. Same for an adopted Webull leftover fill (startup() copies
o['for_entry'], also a string from JSON) -> adopted and immediately sold. The stress test passes only because it puts a
Timestamp object straight into the record (algo-stress-test.py:1459) instead of going through JSON. Paper is not hit only by
accident: the paper path never saves for_entry (book() saves before _mark_entry()), see D7; fixing D7 alone would expose paper to D1 too.
CONFIRMED: tests/_probe_live_restart.py output: run 2 after restart sent [('sell', 1000, 'SQQQ'), ('buy', 1000, 'SQQQ')],
trips "close the hedge: a new position" then "hedge for the position".
Fix: compare as timestamps (pd.Timestamp(st['for_entry']) == pd.Timestamp(pos.entry_time)) everywhere for_entry is compared
(tick, target), and save after _mark_entry().
(D1 also confirmed by tests/d-autohedge-test.py, both algos: out-autohedge-ibkr.txt / out-autohedge-webull.txt, checks "D1 live
restart (same live run)", "D1 live restart (new live run...)": held 1000 -> 0, orders [('sell', 1000, 'SQQQ')], 200 TQQQ left
unhedged; "D1 adopted leftover (record read back from disk)": adopted, then sold and re-bought.)

### D2 HIGH (latent while one MNQ is above the $25k order cap): IBKR futures hedge doubles itself when the front month rolls
What happens: the algo remembers its futures hedge only as "MNQ", not as the December or March contract. When IB Gateway
reconnects (nightly) after IBKR has moved its "front month" to the next contract, the algo reads 0 contracts of the new month,
decides the hedge was closed outside the algo, books it flat and sells a fresh set in the new month. The old-month contracts stay
short in the account, untracked; when the position closes only the new ones are bought back. The account is left with a naked
short futures position (e.g. 12 MNQ = about $480k at NDX 20,000) until expiry.
Detail: ibkr-algo/algo/paper_trader.py _hedge_subscribe() lines 121-125 re-qualifies ContFuture(want,'CME') on every
subscribe(force=True) (called from subscribe() at each reconnect, line 260); hedge.position = broker.position(self.hedge_contract)
(conId of the newly qualified month); autohedge.py tick() -> _check_gone() line 556 `b == 0` -> _mark_flat() -> next tick
`not held` -> _send(want). st has no conId/expiry. Paper: the paper hedge's open P&L jumps by the calendar spread (new month price
vs old avg), which also feeds the algo's daily loss limit (gateway/trading.py _algo_today 'unrealized').
CONFIRMED (logic) by tests/d-autohedge-test.py "futures roll": account old month -12 + new month -12 = -24 MNQ (wanted -12).
TRACED (trigger): ContFuture re-lookup on reconnect from code reading; real IBKR roll timing not run.
Fix: store the hedge contract's conId/localSymbol in the hedge record and read the position (and close) by that contract; roll
explicitly, or refuse to re-qualify while a futures hedge is open.

### D9 HIGH (logic): Auto-Hedge at its defaults turns the algo's trade into a guaranteed small loss
What happens: for the Webull paper algo (TQQQ), Auto-Hedge's "Auto" pick is SQQQ at $1.00 per $1 of TQQQ and the default ratio
is 100%. Long TQQQ + the same dollars of SQQQ is market-flat: every trade can only lose both funds' spreads/fees and the two
funds' decay. The board's own test shows exactly this (twin pair held through after-hours: about -0.4% per trade, 0% wins), yet
the box offers it as the default with no warning. A partial ratio with the twin is just "trade smaller, at higher cost".
Detail: hedge.py algo_post() line 1518 picks max cut_day among funds -> the inverse twin always wins (cut 100%);
hedge_choices() -> "SQQQ Inverse Twin $1.0 cut 100.0, SPXU Index Inverse $1.264". Applies to any 3x/2x fund with a twin.
CONFIRMED: out-hedge-gateway.txt "Auto picks SQQQ at $1.0 per $1 of TQQQ: 3x*1 + (-3x)*1.0 = 0.00x left".
Fix: never auto-pick the holding's own inverse twin; warn that a twin hedge at 100% cancels the trade; prefer smaller size, an
index hedge for sector funds, or futures.

### D3 MEDIUM: One empty position answer from the broker makes the algo forget its hedge and buy a second one
What happens: every 30 s the algo asks the broker how much of the hedge fund the account holds. A single answer of 0 (e.g.
Webull's position list coming back empty for a moment) is taken at face value: the hedge is booked as sold and a new full hedge
is bought on the next tick. The account then holds two hedges; the first one is never sold by the algo.
Detail: autohedge.py _check_gone() lines 547-557 acts on one read; contrast core_trader._guardian_tick which needs 2 mismatches
(`g['miss'] >= 2`) before it acts on the main position. Webull's hedge.position = broker.position(sym) (webull paper_trader.py
line 233) returns 0 for any list without the symbol. Once the account holds more than the record, nothing notices (_check_gone
only handles "less").
CONFIRMED (logic) by tests/d-autohedge-test.py "one empty position answer": account 1000 -> 2000 SQQQ, orders [('buy', 1000, 'SQQQ')].
TRACED (realism): whether Webull really returns an empty list transiently was not tested.
Fix: require two reads 30 s apart (as the core does) before booking a hedge as gone, and treat "more than the record" as a
mismatch to report.

### D4 MEDIUM: A partly filled hedge is never topped up
What happens: if the broker fills only part of the hedge order (common for limit orders outside regular hours), the algo books
the part and then treats it as the full hedge for this position; the rest is never bought. The tab shows "hedging" while e.g.
only 60% is hedged.
Detail: autohedge.py filled() lines 704-711 -> book(k) then _mark_entry() stores main_shares = pos.shares; target() line 422-425
then follows the share count: want = round(units x shares / main_shares) = units, diff 0.
CONFIRMED: tests/d-autohedge-test.py "partial fill": first fill 600, after 5 more ticks 600 of 1000.
Fix: only call _mark_entry() when the fill reached the target (or store the target units, not the filled units).

### D10 MEDIUM (logic): Calculator and Hedge Partners recommend the most expensive way to get flat, never the cheapest
What happens: for 500 TQQQ ($30,000) the calculator's BEST MATCH is "buy 1000 SQQQ" (the highest daily decay of the fund choices,
$45.92/day here vs PSQ $25.79/day) while TQQQ keeps decaying $23.53/day; Hedge Partners marks SQQQ (the inverse twin) as BEST
PARTNER. Nowhere does the page say that selling part of the TQQQ you hold gets you just as flat with no new decay, one spread
instead of two later, and buying power freed rather than used (on a margin account a 3x fund typically needs 75% maintenance margin, FINRA's 25% x leverage).
For a cost-averse trader this is the default suggestion he will follow.
Detail: hedge.py calc() lines 778-786 (Best = biggest swing cut, ties -> least buying power, so always the 3x fund; decay not
used); the swing cut for SQQQ/QID/PSQ is computed from QQQ's series x leverage (_hedge_series), so they always tie; partners()
line 968 picks lowest decay_week within 3 points, but the twin's 99-100% cut is always more than 3 points above index hedges.
CONFIRMED: out-hedge-gateway.txt INFO lines + FAIL "calculator: offers the cheapest way..." and "Hedge Partners: the BEST
partner for TQQQ is not its own inverse twin" (best = SQQQ (Inverse Twin), decay $229.61/week).
Fix: list "sell N of what you hold" first when the exposure comes from a fund you hold; rank by total holding cost (decay +
fee + financing), not buying power; label the twin "same as selling, but costs more".

### D11 MEDIUM: Futures without a board price are counted at a fraction of their size; non-index futures count as Nasdaq-100
What happens: (a) when the board has no NQ=F / ES=F price yet (gateway just restarted, bridge down), an MNQ position is counted
at half its size, MES at 1/5, ES at 1/50. (b) Any IBKR future the list does not know (gold GC, Treasuries ZN, crude CL, micro
bitcoin...) is shown at 1/multiplier of its value and counted as Nasdaq-100 exposure ("QQQ (Same Index)").
Detail: brokerpos.py line 77 already stores avg = avgCost / multiplier (per point); hedge.py _row() line 477
`px = float(h['avg']) / (mult or 1)` divides by the multiplier a second time. Line 481 `follows = base or 'QQQ'` for unknown roots.
CONFIRMED: out-hedge-gateway.txt: short 1 MNQ @ 20,100 -> price 10050.0, value -20100.0, d_ndx -201.0 (want -402);
GC: value 2400.0 (real ~$240,000), d_ndx +24.0, note 'GC Future, 100 $ Per Point: QQQ (Same Index)'.
Fix: use avg as the price (no second division); for an unknown root show the row as "not an index future: not counted".

### D12 MEDIUM: Inverse funds the board doesn't know are counted as LONG until history arrives
What happens: an inverse fund missing from the twins list (FNGD, SARK, TSLQ, YANG, NVDD, TSLS...) with no board or
Yahoo history yet gets a fixed beta of +1.0, so 100 FNGD shows as +$10 per 1% (you would gain if the market rises) instead of a
loss. It is marked "Estimated Beta", but the total line and the calculator use it (the calculator may then suggest buying even
more inverse funds). Unknown long 3x funds are likewise counted at 1x.
Detail: hedge.py fund_of() -> (sym, 1.0); eff_beta() line 420 `est = DEFAULT_BETA[idx].get(base, 1.0)`. Measured betas fix it
once Yahoo daily closes arrive (background, up to 5 min+), and never if Yahoo is unreachable.
CONFIRMED: out-hedge-gateway.txt: {"FNGD": [10.0, "FNGD, Beta 1 Estimated"], "SARK": [30.0, ...], "TSLQ": [20.0, ...]}.
Fix: leave rows with no beta out of the total (shown as "unknown"), and add the common leveraged/inverse funds to the list.

### D13 MEDIUM (logic): "Ranked by a backtest" ranks luck: on a record with no edge, 9 of 20 shown ideas look profitable
What happens: Hedge Ideas tests dozens of pair x rule x session combinations on ~25-28 trades each and shows the best session of
each, ranked by t-score. On the test's made-up record (pure random walks, every fund exactly L x its index, so no idea can have
an edge) the list still shows "#1 SOXS-MES hold overnight: 28 trades, 64% wins, +1.0% average", and 9 of the 20 shown ideas have
a positive average after costs. The hedge size is also fitted on the same days it is tested on (in-sample).
Detail: hedge.py compute_ideas() lines 1194-1230 (best of 3 sessions x 3 rules per pair, sorted by t); pair_stats() ratio from the
full record; no out-of-sample split, no multiple-testing note. The "RESEARCH, NOT ADVICE" banner is there, but nothing tells Alex
that a score of ~1.5 is what pure chance gives here.
CONFIRMED: out-hedge-gateway.txt INFO "Hedge Ideas on a made-up record with NO edge..." and FAIL line.
Fix: fit the ratio on the first half and score on the second; show a "chance level" (best score on shuffled data) next to the
list, and hide ideas below it.

### D5 MEDIUM: Futures hedge orders are sent while CME is closed (Friday 6-8 pm ET, early-close days)
What happens: the algo only knows CME's daily 5-6 pm pause. Friday after 5 pm (CME shut until Sunday 6 pm) and after CME's
1:15 pm early close (day after Thanksgiving, Christmas Eve) are still the board's after-hours session, so MNQ/MES hedge orders go
out and are rejected. A closing order is retried every 60 s, so three rejections stop live trading, and the short futures hedge
stays open over the weekend while the fund position may already be closed.
Detail: autohedge.py cme_paused() line 219 `return t.hour == 17`; _session_ok() line 512.
CONFIRMED: tests/d-autohedge-test.py: cme_paused(Fri 2026-10-16 18:30 ET) -> False; cme_paused(2026-11-27 14:30 ET) -> False.
Fix: model CME Globex hours (Sun 6 pm - Fri 5 pm, daily 5-6 pm pause, CME holiday/early-close calendar).

### D6 LOW: Only the first hedge rejection of a run is pushed
What happens: "Hedge order rejected ... The position is not hedged right now" reaches the phone once per run; any later
rejection (another day, another cause) is silent, although Alex relies on pushes while away.
Detail: autohedge.py line 740 `if 'rejected' not in self.pushed: self.pushed.add('rejected')`; self.pushed is never cleared.
CONFIRMED: tests/d-autohedge-test.py "push": pushes after 1st rejection 1, after 2nd 1.
Fix: clear the flag when a hedge order fills (or push once per position / per day).

### D7 LOW: Paper hedge forgets which position it belongs to after a restart, and then resizes on price moves
What happens: in paper, the hedge record on disk never gets the "for this position / this many shares" fields, so after a restart
the kept paper hedge is resized whenever prices drift 25% from its size (an extra paper trade with costs), unlike live.
Detail: autohedge.py _send() paper branch lines 643-645: book() saves, then _mark_entry() changes st in memory only.
CONFIRMED: tests/d-autohedge-test.py: on disk for_entry None, main_shares 0; after restart with TQQQ +15% / SQQQ -15%:
units 3750 -> 5074 ('hedge resized').
Fix: self.save() at the end of _mark_entry().

### D8 LOW: A part exit can sell more of the hedge fund than the account holds (IBKR: a short sale on margin)
What happens: when the position gets smaller, the hedge is cut by the algo's own record without asking the broker. If you sold
some of the hedge fund in the broker app in the last 30 s, the cut can sell more than is there; at IBKR that opens a short.
Detail: autohedge.py tick() lines 497-502 -> _send(diff, closing=True) with no _broker_units() check (only _close() checks);
_check_gone() runs at most every 30 s (BROKER_CHECK_S). The doc/legend say "it never sells more than the broker holds".
CONFIRMED: tests/d-autohedge-test.py "part exit": record 1000, broker held 300, orders [('sell', 500, 'SQQQ')], broker now -200.
Fix: cap every hedge sell at min(record, broker units) read just before sending (as _close does).

### D15 LOW: Hedge orders the trader refuses itself still count toward the 30-orders-a-day limit, which then pauses live trading
What happens: each time a hedge try is refused before it reaches the broker (IBKR: "does not trade on IBKR's overnight venue",
"has not looked up the hedge yet"), it is still counted as a sent order. It is retried every minute while it has a price; after
30 such tries in 24 h the next new order trips "30 orders in the last 24 hours. Live trading paused".
Detail: autohedge.py _send_live(): core._guardian_block(spec) (line 688, appends to core._sent) runs before self.place()
(line 759); ibkr paper_trader._place_hedge() raises GuardRejected (lines 99, 102) -> caught at line 760, retry in 60 s.
CONFIRMED: tests/d-autohedge-test.py: after 31 refused tries halted = 'order speed limit: 30 orders in the last 24 hours...'.
Fix: count an order in _sent only after place() accepted it (or check the overnight route before _guardian_block).

### D16 LOW: IBKR: the hedge's first price after Auto-Hedge is switched on can be hours old, and is used as "fresh"
What happens: when Auto-Hedge is turned on (or the hedge instrument changes) during a run, IBKR first sends the fund's last trade
as a snapshot. The algo takes it as a live price for 10 minutes: the paper hedge is filled at it (overnight that can be the 8 pm
price), and live orders are sized and limited from it.
Detail: ibkr paper_trader.py _hedge_ticks() line 150 skips ticks only for `now - self.sub_at >= 3`, but sub_at is the MAIN
symbol's subscribe time (line 240), not the hedge's (_hedge_subscribe from the timer, line 436). autohedge.on_price() ignores
ts_ms and stamps time.time(). TRACED (no IB Gateway here).
Fix: keep a hedge_sub_at set in _hedge_subscribe, and use the tick's own time for freshness.

### D17 LOW: IBKR start-up does not take over a hedge that filled while the algo was stopping (Webull does)
What happens: if an IBKR hedge order fills as the run stops (or was left "unconfirmed"), the next start only sees "more of the
fund than the record", shows it as shares held outside the algo, blocks hedging, and never sells them, even after the position
is closed. The fund then sits in the account unmanaged; it is only a note on the algo tab, no push.
Detail: ibkr paper_trader.py run() line 513 calls hedge.startup(position) with no lookups (IB Gateway does show today's
executions with orderRef jb-hedge, which could be used); autohedge.startup() lines 877-884 -> st['outside'], log only.
TRACED.
Fix: at IBKR, look up today's executions tagged jb-hedge for the recorded order ids and adopt them as Webull does; push when
"outside" shares are found.

### D18 LOW: Paper hedge results are not realistic in two ways
(a) Paper MNQ/MES hedges are charged the stock extended-hours cost (0.1% of about $40-50k per contract per fill, about $40-50)
instead of the 0.02% the backtest uses (autohedge.py _send() line 642 core.sim_price vs FUT_COST_PCT line 54).
(b) A paper hedge closed while it has no fresh price is booked at its own average price, i.e. with $0 result
(_paper_close() line 574 `px = self.fresh_price() or self.st.get('avg')`), which also feeds the paper daily loss limit.
TRACED (code reading).

### D14 LOW: Short TQQQ: SQQQ is labelled "Bull Twin"
Detail: partner_candidates() line 826 relabels by `long_market` only. CONFIRMED: out-hedge-gateway.txt (label 'Bull Twin',
text "SQQQ moves with TQQQ here: it would have to be sold short"). Sizing is right (dollars sign), only the label is wrong.

