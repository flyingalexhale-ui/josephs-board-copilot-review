# Reviewer B findings (Webull algo), saved by the thread from B's handback, 2026-10-17
Tests in rev/B/tests (bharness.py + t1..t15, each with .out.txt; PASS = safe, FAIL = finding). Baseline stress 158/158. t11 = real bridge + fake SDK on 9123 + real WebullTrader.
Checked FINE: no duplicate orders (no SDK auto-retry; coid lookup after 5xx); partial fills; closing orders skip cooldown/halt/notional; SELL ALL blocks algo, cancels backup stop, not re-placed while killed; NIGHT/day stop switch at 8 pm / 3:50 am never two stops; no overnight shorts; jbhedge leftovers refused; SIGTERM cancels; calendar; SMA/EMA/RSI; backtest no lookahead/no repaint; bridge restart -> prices back in ~7 s. A0 does NOT apply (stream + timer start after connect_api; place_live set after check_matches_broker; t13). A6 IBKR form does NOT apply (order detail gives real filled qty, booked + pushed).

HIGH
- B2 (=A2) unconfirmed exit pauses silently, shares unprotected. core_trader 650-657 no push; 808 step 4 skipped while pending; 367 check_kill returns while busy; 882 position check skipped; live_broker 365; no late-fill path on Webull; hedge path does push (autohedge 755). CONFIRMED t2_unconfirmed_exit.py.
- B18 (=A1) safe mode: stop-loss cancels backup stop then sell blocked, stop not re-placed; push text implies broker stop still there. core 508-515, 609, 912-916, 808. CONFIRMED t13 (connection + position cases).
- B6 backup stop whose send timed out but reached Webull is untracked (_bs_unsure, paper_trader 369 / 346); exit within 5 min doesn't cancel it -> GTC SELL left resting after flat; next entry adopts it with wrong qty/price (core ~822). CONFIRMED t6_bstop_unsure.py, t6b_bstop_unsure_adopt.py (algo SELL 220 @101.92 vs Webull SELL 249 @91). Webull behaviour with oversized stop TRACED.
- B16 Stop and daily-loss pause send nothing outside live sessions, retry silently every 30 s, while board pushes "stopped and its position closed" (gateway/trading.py 506-507, also says "paper algo" in live); per-algo limit on by default $1,000 (trading.py:45); core 394-400 blocked no push; paper closes at any hour at last price (353-360). CONFIRMED t14.
- B9 (=A3) refused live orders (expired token 401, 429, any 4xx) never pushed; halt after 3 silent; refused stop-loss retried every 60 s silently; no token-expiry warning. live_broker 287; core 636-648. CONFIRMED t8_sizing_token.py.
- B10 outside regular hours stop-loss exits don't fill on a falling price in paper/backtest: 0.1% cost in fill test vs 0.05% limit offset (options.py 36,39; core 550-555; backtest 126-129). Current TQQQ paper is regular-hours only so unaffected. CONFIRMED t9 (24/25 orders unfilled in backtest).

MEDIUM
- B17 (=A11) hand sale in Webull app -> algo sells full record within first 1-2 min (2 checks 60 s apart, core 882-895); backup stop stays old size (797). CONFIRMED t13. Webull refuse-vs-short TRACED.
- B3 bridge down/crashed: no "prices stopped" push (paper_trader 649 returns while not session_ok), no push on safe mode (core 867-869), backup stop not placed/checked while bridge link down (core 724 gated on connected), fills not booked; Go Live has no bridge check. CONFIRMED t3, t13, t15.
- B12 relay hides bridge's Webull link state: jb_webull_relay 147-170 handles only fticks/follow_ok/err; bridge broadcasts only 'stale' (webull_bridge 428-443); 1002 never forwarded; algo stays connected until 5-15 min stall timer; Webull-disconnect safe-mode code dead in production. CONFIRMED t11 e2e, t10.
- B11 sma_crossover_tqqq.yaml has no trading: section -> 8% stop / 20% TP only checked 9:30-4:00 though it holds 3x ETF overnight (core 288-292). CONFIRMED t10 (holds 200 after -12% after-hours).
- B20 (=A10) Go Live checklist pools nightly backtests of every Webull strategy file, not the strategy going live; no live-vs-paper sessions; no bridge check. bin/golive.py 35-47; trading.py 552-593. CONFIRMED t15.
- B4 tick times not checked for age/order: hours-old tick sets last price, fires trailing stop, resets stall timer; one future-stamped tick stops bars closing. paper_trader 515-519, 529; bridges/common.py 559-574 relay() no clamp. CONFIRMED t4. Real stream behaviour TRACED.
- B1 1-share short preview never runs: live_broker 181 new_orders= vs real preview_order(account_id, preview_orders); TypeError -> "could not be made" -> start proceeds; HTTP 200 body unread; fake SDK trade_client.py:42 and stress test :1697 copy wrong signature. Current strategy long-only. CONFIRMED t1 with real SDK signature.

LOW
- B7 live starts when open-order list unreadable (paper_trader 193-199; live_broker 212-236 one page of 100, deprecated endpoint, no pagination). CONFIRMED t7.
- B8 (=A5) $100k ledger sizing (jb:224, run-service.sh:94, paper_trader 729); stress test uses 300k so doesn't test live. CONFIRMED t8.
- B19 (=A4) >10,000 shares blocked not clamped; paper ignores caps (guards 60-64; core 463-464). CONFIRMED t13.
- B5 overnight auto-enable race: bridge answers from cached flag; at bridge start says yes before asking Webull; algo then waits 3 h (webull_bridge 158-167; paper_trader 127-153). CONFIRMED t5.
- B15 backup stop FILLED with no quantity treated as gone (core 764 vs live_broker 344). CONFIRMED t12. Field name TRACED.
- B13 3:59 pm bar never acted on in paper/live (handed over at 4:00:02 = after-hours; core 286-292); backtest does act. CONFIRMED t12.
- B14 vwap resets at UTC midnight (indicators 97-105); unused by current strategies. CONFIRMED t12.
- TRACED notes: 2-s timer holds lock during HTTP calls (up to 15 s) delaying ticks; account check 'MARGIN' in kind accepts "NON_MARGIN"; no history backfill after outage (SMA spans gap); backtest uses only 1,200 M1 bars (~1-2 days); Resume after Stop/SELL ALL re-buys at once (state rule); background Go Live start can trigger repeated phone approvals while token pending; Bollinger sample std; IBKR stall check also skips while disconnected.
