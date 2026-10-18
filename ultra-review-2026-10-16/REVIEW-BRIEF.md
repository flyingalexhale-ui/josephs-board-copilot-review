# Shared brief for every reviewer (read fully before starting)

## What this is
"Alex Hale's Board" (aka Joseph's Board): a market dashboard plus two trading algos (IBKR and Webull) that run 24/7 on an old Mac (macOS 12.7.6, Python 3.13 venv, system bash 3.2) in ~/josephs-board, controlled by the `./jb` script, services kept alive by launchd via bin/run-service.sh.
Services: gateway (web server 8090, password login, most features), ibkr bridge 8765, webull bridge 8766, mover scanner 8767, webull-algo, ibkr-algo, ibgateway (IB Gateway via IBC), keepawake.

Code (read-only for you): /home/claude/josephs-board, branch review-2026-10-16, which matches the Mac as of 2026-10-14.
Python venv with all requirements + playwright + pytest: /home/claude/venv/bin/python. Chromium for Playwright: /opt/pw-browsers (already configured; never run `playwright install`).

## The owner and how he trades (needed to judge LOGIC, not just code)
- Alex has no programming background. Findings must be explainable in plain language.
- Trades mostly OUTSIDE regular hours (after-hours 4-8 pm ET, overnight 8 pm-4 am ET, pre-market 4-9:30 am ET), mainly 3x leveraged / inverse ETFs (TQQQ, SQQQ, SOXL, SOXS, etc.). Overnight tracking and session breakdown matter most. Usually away from the Mac, relies on phone page + push alerts (ntfy) + iPhone widgets.
- Both broker accounts are MARGIN accounts. Old PDT rule ended June 4 2026; intraday margin calls matter.
- Both algos currently PAPER. Webull paper: SMA 20/50 long-only on TQQQ. IBKR paper: overnight gap strategy. A $300k readiness review said NOT READY (limits sized for ~$25k). 1-share live tests passed at both brokers. Go Live buttons exist.
- Safety switches default ON; paper algos run the same safety checks as live. Algos must NOT pause/close before big news by default (heads-up push only).
- Core purpose: real-time, board-computed analysis of WHY something is moving, from the board's own price/volume/technicals.

## Broker facts (true, don't report these as bugs)
- Webull allows only ONE market-data connection per account; since jb-onestream the webull bridge is the only Webull stream and the scanner + Webull algo get prices from it (lib/jb_webull_relay.py). A second Webull stream would be a bug.
- IBKR plan lacks live NYSE Network A stocks (LLY, UBER, UNH, XOM); scanner falls back to Webull after 30s. IBKR has an overnight route (8 pm-3:50 am ET). IB Gateway gives only today's fills.
- Webull plan lacks overnight/futures right now (board auto-enables overnight when bought). Webull 403 for overnight/futures and IBKR "(366)" / HMDS farm messages are harmless. Webull rate-limits.
- Broker backup stops are regular-hours only at both brokers (known, shown on page).
- IBKR forces a full login about weekly; gateway/ibauto.py does a planned Sunday 5 pm ET fresh login + auto fresh login on expired token.
- Access: phone via Tailscale; work laptop via a Tailscale Funnel link (that means the gateway is reachable from the public internet through Funnel).

## Already reviewed and fixed (don't re-report unless the fix is wrong or incomplete)
Read /home/claude/josephs-board/REVIEW-2026-10-11.md and FIXES-2026-10-11.md. HEDGE-2026-10-12.md describes the hedging / Go Live / Webull-parity work and its own test results. Code added since Oct 11 has had NO second review: hedging (gateway/hedge.py, */algo/autohedge.py, web/hedge-*.js), Go Live (gateway/livectl.py, bin/golive.py, web/golive-ui.js), Webull parity + overnight auto-enable, the single shared Webull stream (lib/jb_webull_relay.py, bridges/webull_bridge.py, scanner feed, webull paper_trader), fees (gateway/trading.py), widget changes (gateway/tools.py), weekly IBKR login (gateway/ibauto.py). Weight that code heavily.

## Existing tests (all pass right now: 493 checks)
tests/algo-stress-test.py <algo dir> <ibkr|webull>, tests/gateway-fake-broker-test.py, tests/golive-test.py, tests/hedge-test.py, tests/overnight-test.py, tests/phone-layout-test.py, tests/legend-test.js, and a full fake-broker live-run harness in tests/fake-brokers/ (live-run.sh, fake_tws.py, fake Webull SDK in pkgs/, clockshift.c to fake the wall clock, api-sweep.py, browser-check.py). Passing tests are not proof: also judge whether the tests test the right thing.

## What to look for
1. CODE: bugs, crashes, race conditions, wrong state after restarts, unhandled broker errors/statuses, timezone/DST/holiday/early-close mistakes, float/rounding on share counts and money, silent exception swallowing that hides failures, resource leaks, things that break on macOS 12 / bash 3.2 / Python 3.13.
2. LOGIC: does each feature, signal, algo rule, safety check and number actually do what it claims on the page/legend (web/legend.js describes features), and does it make sense for how Alex trades (3x ETFs, extended hours, margin, away from the Mac)? E.g. wrong formula, look-ahead bias in backtests, stops that can't trigger in the session he trades, alerts that can't fire, numbers labelled one thing but computing another, a safety check that can be bypassed, defaults that are dangerous.
3. TESTS: write NEW tests/repro scripts that prove each important finding (a test that fails now). Put them in your own work folder (given below), never inside /home/claude/josephs-board. If you need to run modified code, copy the repo into your work folder first.

## Rules
- Report only. Do NOT modify anything under /home/claude/josephs-board. Do NOT git commit/push. Do NOT write anywhere under /mnt/project-files. Do NOT contact real brokers, ntfy, healthchecks, or any real external service (Yahoo etc. may be unreachable anyway; don't hammer them).
- Do NOT call any mcp__hearthbot__ tool. Do NOT install packages (everything needed is in the venv; if something is missing, note it and work around it).
- Don't start servers on ports 8090, 8765, 8766, 8767, 4002 unless your brief says you own the live run. If you need a server, use a port in your assigned range.
- Never print or save secrets (there are none in the repo; settings.env is not included).
- Be skeptical of your own findings: trace the code path end to end, and run it where possible, before reporting. Mark each finding CONFIRMED (you ran something that shows it) or TRACED (read the code path end to end but couldn't run it).

## Output
Write ONE markdown file at <your work folder>/findings.md with:
- A short "Coverage" section: every file you read fully, every test you ran, and what you checked and found FINE (so we know what's covered).
- Findings, ranked most severe first. Each finding:
  - ID (your prefix + number), Severity: CRITICAL (can lose real money / leave a wrong position / safety off silently) | HIGH (wrong trading decision, missed alert, outage, security hole) | MEDIUM (wrong number on the board, degraded feature) | LOW (cosmetic, minor).
  - Title in plain English (what Alex would notice), 1-2 sentence plain-English "What happens", then technical detail: file:line, the exact code path, trigger conditions, CONFIRMED/TRACED + how (test file path and its output), and a one-line suggested fix.
- Keep it factual; no padding. Your final message back should be a ~10-line summary listing the finding IDs with severity and title.
</content>
</invoke>

## Addendum for reviewer E (added 2026-10-17 from Alex): IBKR auto-restart page
Alex pointed at https://www.ibkrguides.com/traderworkstation/auto-restart-considerations.htm . Key facts from it: IBKR invalidates the saved security tokens every Sunday at 1:00 am ET, and the first login after that must be a full (2FA) login; auto-restart fails if the machine is asleep/hibernating/without power at restart time or is rebooting for OS updates; IBKR may rarely revoke a token for security reasons (manual login needed after the restart); auto-restart works best with OFFLINE Gateway versions (a failed auto-update can break the restart); keeping it running increases exposure, so protect the OS account/machine and use IB Key 2FA.
Check the board against each point: gateway/ibauto.py's Sunday 5 pm ET planned login vs the 1 am invalidation and the Sunday 11:50 pm nightly restart; what happens Sunday 1 am-5 pm if anything restarts Gateway (crash, Mac reboot, ./jb restart) and whether ibauto detects that and pushes; what happens if the 5 pm Approve is missed (does it retry, push repeatedly, leave IBKR down overnight, and could it have waited instead); AutoRestartTime in bin/ib-autologin.sh is computed once in the Mac's local clock at setup (wrong after DST or if the Mac isn't on Eastern time?); token revocation mid-week detection; offline Gateway enforced; Mac sleep / macOS auto-update reboot protections (keepawake, pmset); and the security point given the Funnel link.
