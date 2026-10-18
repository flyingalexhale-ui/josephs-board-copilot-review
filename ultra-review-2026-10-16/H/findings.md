# Reviewer H findings (full fake-broker live runs), saved by the thread from H's handback, 2026-10-17 19:37Z

## Reviewer H: fake-broker live runs, final report

I finished every run in the brief and stopped every service I started. Ports 8090, 8765, 8766, 8767 and 4002 are free, and nothing matches `$W/run`. Nothing under /home/claude/josephs-board was changed. The file tool refused findings.md, so the full findings are in this message only.

Work folder `$W` = /tmp/claude-0/-home-claude/d5f7f21c-dc3e-504d-8a31-86f6d5f4dac4/scratchpad/rev/H. Running notes are in /tmp/claude-0/-home-claude/d5f7f21c-dc3e-504d-8a31-86f6d5f4dac4/scratchpad/rev/H/notes-findings.txt.

**Summary (16 findings, most severe first):**
- **H1 MEDIUM**: a stop-loss exit that doesn't fill is only re-sent every 2 minutes.
- **H2 MEDIUM**: after Yahoo refuses the crumb there is no back-off, so every Yahoo call becomes 3 requests (about 1,700 crumb retries in 18 minutes live).
- **H3 LOW**: the phone page shows the wrong session on holidays, early-close days and the nights before holidays.
- **H4 LOW**: after a slow Webull bridge restart, the Webull algo and scanner reconnect up to 2 minutes late (the algo held a position with no prices).
- **H5 LOW**: the Health page's Webull price card says "market closed, normal" overnight even when the plan streams overnight. It disagrees with System check.
- **H6 LOW**: a 15-minute Webull outage in market hours sends no "Board problem" push.
- **H7 LOW**: two pushes go out for the same Sunday weekly IBKR sign-in.
- **H8 LOW**: System check says "SEC filings ok" and "Earnings ok" while both are failing.
- **H9 LOW**: the Overnight session check says "ALL OK" during the day, on weekends and on Sundays.
- **H10 LOW**: the "since the close" push repeats minutes later at the 8 pm session change.
- **H11 LOW**: an empty morning card is pushed and shown when the gateway starts in the 7:00 or 9:00 window.
- **H12 LOW**: with the default strategy name, the Webull algo page scrolls sideways on an iPhone.
- **H13 LOW**: the safe-mode message prints the Mac's local time with no zone, not New York time.
- **H14 LOW**: Health and System check say "Webull last price 2 min ago" while Webull prices arrive every second.
- **H15 LOW (TRACED)**: the fast-move and big-move alerts have no market-closed guard.
- **H16 LOW (docs/wording)**: stale holiday notes in the legend and badge tooltip, a stale "Webull has no futures" note, and two contradictory text lines.

Known issues seen live: relay hides link loss (run01); "just jumped" push after the 3:50–4:00 gap (run05b); early close expects prices 5–8 pm (run07d); Sunday 17:00 self-test lands in the same second as the IB Gateway restart (run10b); bridge says yes to overnight before asking Webull (run10c); amber dot from unset SEC email / Webull account (run09). Details are at the end.

---

## Coverage

**Code read:**
- gateway: extras, world, syscheck, signals, safety, watch, guardian, ibauto, orders, pick, iblink
- bridges/ibkr_bridge.py, bridges/webull_bridge.py
- lib: jb_webull_relay, jb_webull, nyse_hours
- ibkr-algo and webull-algo: algo/core_trader.py and algo/paper_trader.py
- mover-scanner/scanner/feeds/webull_feed.py
- web: health.html, algo.html, board.js, legend.js, nd-pick.js
- jb, bin/run-service.sh, tests/fake-brokers/*

**Tests run:**
- tests/phone-layout-test.py: 2/2 PASS.
- tests/legend-test.js: 16/16 PASS. Outputs in `$W/tests-out`.
- Legend vs pages: legend-live.py plus night-tabs.py over all 8 Night desk tabs. Every legend feature exists on its page. The only exceptions are the stale notes in H16. Chase and Sell-now steps need real orders, so I couldn't check them.
- My repro tests:
  - `$W/tests/test_phone_session_label.py`: 5 FAIL.
  - `$W/tests/test_yahoo_crumb_backoff.py`: FAIL, 60 requests for 20 calls.
- Each run: api-sweep.py and extra-sweep.py found no 5xx, tracebacks, NaN or slow answers. bcheck.py ran over every web/*.html plus algo.html?name=ibkr|webull at 1440x900 and iPhone 390x844, with clicks and screenshots, and compared logs, pushes, algo decisions, Health and System check.

**Runs:** each was at least 10 minutes on a shifted clock. Outputs are in `$W/<run>-out` and crops in `$W/crops`.

| Run | Shifted time and setup |
|---|---|
| run01 | 10:30 weekday, Webull bridge kill and Webull outage |
| run02 | 15:58 → 4 pm |
| run03 | 19:56 → 20:00 (gap strategy) |
| run04 | 01:00 overnight |
| run05 | 03:45 → 4:00 (original fakes stream 24/7) |
| run05b | 03:30 → 4:00 with realistic sessions |
| run06 | 09:27 → 9:30 with a seeded overnight position |
| run07a | Saturday |
| run07b | Thanksgiving 2026-11-26 |
| run07c | Early close 12:56 → 13:00 |
| run07d | Early close 16:56 → 17:00 |
| run08 | IB Gateway nightly restart: fake_tws killed 23:50:00–23:52:30 |
| run09 | Webull bridge restarts: quick (launchd-style, 10 s) and slow (supervisor paused 150 s) |
| run10a | DST fold, 01:52 EDT → 01:08 EST |
| run10b | Sunday 11-01 16:55 EST, RUN_IB_AUTOLOGIN=on, IB Gateway killed 17:00:05–17:01:35 |
| run10c | FAKE_NO_OVERNIGHT=1 with JB_OVERNIGHT_RECHECK_S=90 at 19:50; bridge, scanner and algo restarted together at 20:09; plan "bought" at 20:13 |
| run10d | Monday 11-02 15:00 EST (first trading day after the clock change) |

**Checked and fine:**
- **Session changes:** the 4 pm, 20:00, 4:00 and 9:30 flips, Saturday, Thanksgiving and the 13:00 early close all work on the boards' badges and in the algos.
- **Paper trading:** decisions and fills are consistent with the round trips. The daily loss limit stopped both algos with one push each.
- **IB Gateway restart:**
  - System check shows "IB Gateway bad" within 1–3 s and recovers within 3–5 s.
  - The IBKR bridge, the scanner's IBKR feed and the IBKR algo reconnect.
  - The algo keeps its position and blocks orders during the outage. Safe mode ends 60 s after reconnecting, and the delayed exit then fills.
- **Webull bridge restart:**
  - Quick restart: everything is back within 15 s.
  - fake_webull.owner only ever showed the bridge's pid, so there was never a second Webull stream.
- **DST fold:** no errors, correct countdowns ("overnight in 18h 54m" at 01:05 EST), no doubled scheduled jobs, and algo bars are labelled in ET after the change.
- **No overnight plan:** the notice, faded tiles, algo fallback and 90 s re-check all work. After the plan is "bought", the bridge picks it up within 10 s and the tiles and algo go live.
- **Pushes fire once,** apart from H7, H10 and the known items. "Board check" pushes come one per user click.
- **bcheck failures:** across all runs, the only failures were expected 400s from empty-form clicks (hotspot test, ticket check, trading Add / Put it on / plan). The one exception is H12.

**Harness problems (not board defects):**
- fake_tws sends a first tick priced at `price_at(now)` on reqMktData even when closed (fake_tws.py:122). This caused 5–6 "(closed)" alert pushes at start in run07a, run10a and run10b. Real IBKR sends the last trade.
- The original fakes stream 24/7 (no 3:50–4:00 pause, prices on weekends). I added H_FAKE_SESSIONS=1 in my copy, used from run05b on.
- clockshift.c didn't shift stat() times, so the algos looked "not running". Fixed in my copy.
- fake_tws.drop accepts connections and then drops them, so the IB Gateway port check stays green. I killed the process instead.
- There is no launchd here:
  - `jb restart ibgateway` fails with "launchctl: command not found".
  - Health says "launchd not reachable".
  - The Go Live crash-loop item can't be exercised.
- Fresh runs have no earlier weeks of data: recap says "no prices recorded this week", and volume alerts can't be exercised.
- Clicking Stocks/ETFs in bcheck restarts the scanner, so it's skipped.
- legend-live originally checked only the first Night desk tab (fixed with night-tabs.py). Its "Copy for Claude" term was my mistake.
- The push recorder's "ny" field is actually UTC.
- External feeds are deliberately unreachable (proxy 127.0.0.1:9). That explains the "16 down" Board checks and the warn rows.

---

## Findings

### H1: MEDIUM: A stop-loss exit that doesn't fill is only re-sent every 2 minutes
- **What happens:** when a paper (and by the same code path, live) exit limit order doesn't fill within its 60 s wait, the next attempt goes out one bar later than it should. In a falling market the stop-loss is retried every 2 minutes, not every minute.
- **Technical detail:**
  - The order is placed at bar close T and expires at T+60 s plus up to a few seconds, in core_trader.py:247-261 (`on_timer`, both algos).
  - The next bar closes at T+60 s, slightly earlier. `_on_bar` skips the decision while `busy()` is true (core_trader.py:~292).
  - The expired order is then dropped 1–2 s later, so the next decision and the re-send happen at T+120 s.
- **Code path:** `on_tick` → `agg` bar → `_on_bar` (busy skip) → `on_timer` expiry.
- **Trigger:** any exit limit that doesn't fill within `wait_s`. This is common outside regular hours with the paper spread model.
- **CONFIRMED** in algo logs:
  - run05: sell 08:01:00, expired 08:02:01, re-sent 08:03:00, expired 08:04:01, re-sent 08:05:00. Stop at -0.4% realised -1.8%.
  - run07c: expired at 18:02:00.865, after the bar decision at 18:02:00.828.
  - run08: Webull algo at 03:50, 03:52 and 03:54.
  - run02: the same pattern at 20:01, 20:02 and 20:03.
- **Fix:** expire the pending order before deciding on the bar (do the expiry check in `_on_bar`), or expire it at `wait_s` minus a few seconds.

### H2: MEDIUM: No back-off after Yahoo refuses the crumb, so Yahoo traffic triples while rate-limited
- **What happens:** once the cookie or crumb fetch fails (for example a 429), every Yahoo call fetches fc.yahoo.com and getcrumb again before the data request. That hammers Yahoo exactly when it is rate-limiting, which can prolong the block.
- **Technical detail:**
  - In world.py:112-158, `_yahoo_renew` sets `crumb=''`.
  - `_yahoo_session` only skips renewing when the crumb is non-empty and less than 6 hours old, so an empty crumb renews on every call.
  - The 10-minute guard in `_get` (161-173) covers only the 401 retry.
- **Trigger:** Yahoo answers 429 or is unreachable.
- **CONFIRMED:**
  - Repro `$W/tests/test_yahoo_crumb_backoff.py`: 20 data calls under 429 sent `{'fc': 20, 'crumb': 20, 'data': 20}`.
  - Live: gateway.log has 1,696 "world: no Yahoo crumb" lines in run09 (~18 min), 1,929 in run08 and 2,353 in run10c.
- **Fix:** after a failed renew, keep `at=now` and don't renew again for 10–15 minutes (with exponential back-off).

### H3: LOW: The phone page shows the wrong session on holidays, early-close days and the nights before holidays
- **What happens:**
  - It shows "Overnight · Blue Ocean" the night before Thanksgiving and the Sunday night before Labor Day.
  - It shows "Regular · 9:30 am - 4 pm" at 1:08 pm on the 13:00 early-close day.
  - It shows "After-hours" at 17:30 that day.
- **Technical detail:** in extras.py:395-416, `_session` returns Overnight before checking for a holiday, never checks whether the next day is a trading day, and ignores early closes. The boards' badge and the algos are correct.
- **Trigger:** any phone page view on those dates and times.
- **CONFIRMED:**
  - `$W/tests/test_phone_session_label.py`: 5 FAIL.
  - Live in run07c: `$W/crops/run07c-phone-phone.png` shows "Regular" at 1:08 PM.
- **Fix:** use lib/nyse_hours (the same session function as the badge) in `_session`.

### H4: LOW: After a slow Webull bridge restart, the Webull algo and scanner reconnect up to 2 minutes late
- **What happens:** when the bridge was down about 2.5 minutes, the gateway and System check were green again within 30 s. The Webull algo and scanner stayed disconnected for a further 86 s. The algo was holding 352 TQQQ with an unfilled stop-loss exit and had no prices from 10:51:00 to 10:55:07.
- **Technical detail:**
  - Retry delays double 1, 2, 4 … 64, 120 s, even for an instant "connection refused" from the local relay: webull-algo/algo/paper_trader.py:713-717 and mover-scanner/scanner/feeds/webull_feed.py:246-249.
  - No push was sent, because 247 s of quiet is below the algo's 300 s stall threshold (paper_trader.py:38).
  - System check showed nothing about the algo.
- **Trigger:** the bridge takes more than about a minute to come back (crash-loop throttling, a slow login).
- **CONFIRMED** in run09: wbkill.log has the bridge back at 10:53:41. webull-algo.log has retries at 14:53:07Z and then "streaming TQQQ" at 14:55:07Z. fastpoll.jsonl shows the timeline.
- **Fix:** cap the relay retry delay at about 5 s (it's a local socket), or reset it whenever port 8766 answers.

### H5: LOW: The Health page's Webull price card says "market closed, normal" overnight even when the plan streams overnight
- **What happens:** at 23:52 ET, with the overnight plan on, the card said "Market closed for this broker now, so no new prices is normal". System check expected Webull prices at that time. So if overnight Webull prices stop, the Health card won't warn.
- **Technical detail:** in health.html:84-91, client-side `expected()` hard-codes Webull to 4:00–20:00 on weekdays. The server's syscheck.expected (82-103) handles the overnight plan and holidays.
- **Trigger:** any night with the Webull overnight plan.
- **CONFIRMED** in run08: `$W/crops/run08-phone-health.png`, with data/webull_overnight.json on=true and System check row "Webull prices ok".
- **Fix:** have /api/health/page return `syscheck.expected(b, now)` for each broker and use it in the card.

### H6: LOW: A 15-minute Webull outage in market hours sends no "Board problem" push
- **What happens:** with "push me when something breaks" turned on (it is off by default), a Webull price outage from 10:58 to 11:13 produced no push.
- **Technical detail:** three delays add up:
  - prices only count as bad after 5 minutes without one (syscheck.py:145-152);
  - the watch loop only samples every 300 s;
  - a problem must stay bad for 10 minutes before it pushes (syscheck.py:506-526).

  Together an outage has to last up to about 20 minutes before anyone is told.
- **Trigger:** an outage of about 10–20 minutes.
- **CONFIRMED** in run01: monitor shows Webull bad 11:03–11:13 and no push. In run07d the push came 11–12 minutes after the rows went bad.
- **Fix:** sample every 60 s, or count the bad time from the last price rather than from the first bad sample.

### H7: LOW: Two pushes go out for the same Sunday weekly IBKR sign-in
- **What happens:** at Sunday 17:00:03 the phone gets "Weekly IBKR Login" and "IB Gateway: weekly sign-in" in the same second, with the same advice (tap Approve).
- **Technical detail:** two features push independently:
  - ibauto.py:67-79 `_weekly`, when RUN_IB_AUTOLOGIN=on;
  - orders.py:845-862 `_step_reminder`, the "ib_reminder" setting, which defaults to True (orders.py:61).
- **Trigger:** auto-login on, Sunday 17:00 ET.
- **CONFIRMED** in run10b: pushes.jsonl at 22:00:03Z has both.
- **Fix:** skip `_step_reminder` when `ibauto.is_on()` (or merge the two messages).

### H8: LOW: System check says "SEC filings ok" and "Earnings ok" while both are failing
- **What happens:** System check shows "SEC filings (Signals): ok, no problem reported" while the signals module records errors['SEC'] = Connection refused. It shows "Earnings data ok" while the scanner's earnings calendar is refused.
- **Technical detail:**
  - syscheck.py:231-234 reads only `signals._st['sec_note']`, not `_st['errors']['SEC']` (set in signals.py:~711).
  - The earnings check at syscheck.py:249-252 reads only `earn._st['errors']`, not the scanner's calendar errors.
- **Trigger:** the SEC or earnings sources are down.
- **CONFIRMED** in run01: GET /api/signals shows errors.SEC, while /api/plus/syscheck says ok. Seen again in run09's snapshot.
- **Fix:** include `signals._st['errors'].get('SEC')`, and the scanner's earnings error, in those items.

### H9: LOW: The Overnight session check says "ALL OK" during the day, on weekends and on Sundays
- **What happens:** "Run now" pushes "OK: overnight prices in the last 10 minutes for 7 of 7 stocks" at 10:31 am, at 4 pm, on a Saturday, and on Sunday 17:02. Any fresh price counts.
- **Technical detail:** safety.py:505-521 `dec6_check` counts any watch price newer than 10 minutes, with no session check.
- **Trigger:** running the check outside 8 pm–4 am.
- **CONFIRMED:** pushes in run01 (14:31Z), run02, run07a, run07d (17:01 ET), run10b (Sunday 17:02).
- **Fix:** outside the overnight session, say "not in the overnight session now" (or count only overnight-venue ticks).

### H10: LOW: The "since the close" push repeats minutes later at the 8 pm session change
- **What happens:** "AAPL down 4.0% since the close" at 19:56 was followed by "AAPL down 3.4%" at 20:00. In run10c, AAPL went 3.1% then 3.4% and AMZN 3.0% then 3.3%. The move had barely changed or even shrunk.
- **Technical detail:** in signals.py:260-266 the key is `big {sym} {_session_key}` (132-138), so a new session means a new push. "Once per session" is documented (signals.py:7), but crossing the boundary minutes later is noise.
- **Trigger:** a stock 3% or more off the close as the session changes.
- **CONFIRMED:** run03 and run10c pushes.jsonl.
- **Fix:** skip it if the same symbol was pushed within the last N hours, unless the move grew by at least 1 point.

### H11: LOW: An empty morning card is pushed and shown when the gateway starts in the 7:00 or 9:00 window
- **What happens:** the gateway started at 9:27:01, and at 9:27:03 it pushed "Morning card 9:00 am ET" with no content. The board showed the empty card.
- **Technical detail:**
  - In watch.py:366-379 `_maybe_card` runs 2 s after start (386-392), when there are no prices yet.
  - The fallback `card['text'].split('\n',1)[-1] or 'No prices yet.'` never fires, because splitting one line returns the title.
- **Trigger:** the gateway starts (restart, reboot) between 7:00 and 7:30 or between 9:00 and 9:30 on a weekday.
- **CONFIRMED** in run06: push at 13:27:03Z, and `$W/crops/run06-desktop-index.png`.
- **Fix:** skip or postpone the card until prices exist (and fix the fallback test).

### H12: LOW: With the default strategy name, the Webull algo page scrolls sideways on an iPhone
- **What happens:** with the default strategy name "SMA 20/50 crossover on TQQQ (long only)", algo.html?name=webull is 394 px wide on a 390 px screen.
- **Technical detail:** algo.html:27 gives `.tag` `white-space:nowrap`. The long text goes into #bt-tag (101) and #rep-tag (111), set at lines 289 and 315.
- **Trigger:** the default or a long strategy name, on a phone.
- **CONFIRMED** in run01: bcheck phone section and `$W/crops/r1-phone-algo-webull-top.png`.
- **Fix:** allow wrapping or ellipsis on `.tag` inside h2 (`max-width:100%; white-space:normal`).

### H13: LOW: The safe-mode message prints the Mac's local time with no zone, not New York time
- **What happens:** at 23:50 ET the algo page said "safe mode: connection to the broker lost at 03:50". The log line "paper safe mode over (… lost at 14:51)" was for a 10:51 ET event. It's only wrong when the Mac isn't set to New York time; everything else on the board shows ET.
- **Technical detail:** core_trader.py:869 (both algos) uses `_dt.datetime.now().strftime('%H:%M')`.
- **Trigger:** losing the broker connection while the Mac's zone is not New York.
- **CONFIRMED** in the harness (process zone UTC): run08 and run09, `$W/crops/run08-desktop-algo-ibkr.png`.
- **Fix:** format with ZoneInfo('America/New_York') and add "ET".

### H14: LOW: Health and System check say "Webull last price 2 min ago" while Webull prices arrive every second
- **What happens:** System check says "Webull prices ok: last price 2 min ago" or "3 min ago" while the Webull tiles show "0s ago".
- **Technical detail:** extras.py:290-294 `_live_price(b)` only counts symbols whose `watch._src == b`. IBKR wins most symbols, so Webull falls back to the recorded minute.
- **Trigger:** both brokers live.
- **CONFIRMED:** run01, run09 (10:54 snapshot) and run10c.
- **Fix:** track each broker's last tick separately from which broker's price is shown.

### H15: LOW (TRACED): The fast-move and big-move alerts have no market-closed guard
- **What happens:** any stray tick while the market is closed (a bridge resubscribe, or a snapshot after a restart such as the Saturday 7:00 refresh) can push "jumped 1.9% in 15 min (closed)".
- **Technical detail:** in signals.py:245-298 `check_prices`, the "yesterday's high/low" check is guarded by `s_name != 'closed'` (279) and the volume check by line 251. The big-move check (260) and fast-move check are not.
- **Trigger:** ticks while closed.
- **TRACED:** read the code. Live pushes in run07a, run10a and run10b came from the harness first-tick artifact, so they are not proof on their own.
- **Fix:** add `s_name != 'closed'` to the big-move and fast-move checks.

### H16: LOW (docs and wording): Stale holiday notes and contradictory text
- **What happens:**
  - legend.js:198 says "Market holidays aren't known, so on a holiday it shows a normal day", and the board.js:759 tooltip says "Holidays are not known." Both are stale: the badge showed MARKET CLOSED on Thanksgiving.
  - The legend says Webull has no futures data, but webull.log shows "RTY=F: following Webull contract RTYZ6".
  - On an early-close day, pushes say "since the 4 pm close" (signals.py:266).
  - Outside picks hours, the Night desk says "the next pick is at Sun 2:00 AM" right under "The next one is Sunday at 8 pm" (pick.py:223 `next_at = next_hour(ms)` even when closed; nd-pick.js:30).
- **CONFIRMED:** `$W/crops/run07b-desktop-index.png`, `$W/crops/run10a-desktop-night.png`, run07d pushes.
- **Fix:** update the texts, and return `next_at` = the next open when picks are closed.

---

## Known issues seen live (evidence)

- **Relay hides the bridge's link loss** (run01): during the Webull outage 10:58–11:13, the Webull algo stayed connected=True with no error, and the scanner logged "subscribed 30 symbols" while the bridge log said "streaming refused".
- **"Just jumped in the last 15 minutes" after a price gap** (run05b): at 04:01:13, "MSFT jumped 1.9% in 15 min (pre-market)" compared against the 03:46 price across the 3:50–4:00 pause.
- **Early close expects prices 5–8 pm** (run07d): System check marked Webull bad from 17:04 and IBKR from 17:05. Pushes at 22:05:57Z ("Board check: 18 down … IBKR prices") and 22:16:10Z ("Board problem: IBKR prices" and "Board problem: Webull prices").
- **Sunday 17:00 self-test collides with the IB Gateway login restart** (run10b): the "Weekly IBKR Login" push, the health.log "restart ibgateway (weekly planned login)" line and the self-test "Board check" push all landed at 22:00:03Z. In the harness `jb restart` failed for lack of launchctl, so the self-test couldn't see IB Gateway down. The timing is confirmed.
- **Bridge says yes to overnight before asking Webull** (run10c): bridge, scanner and algo were restarted together at night on a plan without overnight. overnight-after-restart.json shows scanner on=true and algo on=true ("Webull streams the overnight session") written before the bridge's on=false. The algo then showed "Overnight Prices (Webull): On", trading_now true, err None, with its last price from 19:59.
- **Health dot amber when JB_SEC_EMAIL / WEBULL_ACCOUNT_ID are unset** (run09 snapshot): the warn rows "SEC contact email: not set" and "Webull trading account: not set" are present. Other warns from the harness's unreachable feeds are also present.

**Not reproduced:**
- Delayed IBKR price treated as live: the fake only sends live data.
- "Busy trading" pushes after Nov 1: run10d has no earlier weeks of volume. The cause is traceable at signals.py:214-218 (`start - k*86400000`, the same UTC hour, which is a different ET hour across DST).
- Go Live with `./jb restart` tripping the crash-loop guard: needs launchd.
