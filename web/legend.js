/* Joseph's Board -- on-page legend.
 *
 * Adds a "? Legend" button to the dashboard's tab bar (index.html). Clicking it opens a panel
 * that explains the colours, tags, boxes and controls of whichever tab is open right now.
 * Self-contained: it brings its own styles and touches nothing else on the page, so the
 * boards work exactly the same with or without it. Hooked in by one line in index.html:
 *     <script src="legend.js"></script>
 */
(function () {
  'use strict';
  if (window.__jbLegend) return;
  window.__jbLegend = true;

  // ------------------------------------------------------------------ swatches
  var G = '#00ff41', R = '#ff3b30', Y = '#ffc400', DIM = '#6f8aa3', FLAG = '#ffb648';
  function dot(c) { return '<span class="lg-dot" style="background:' + c + ';box-shadow:0 0 6px ' + c + '"></span>'; }
  function txt(c, t) { return '<b style="color:' + c + '">' + t + '</b>'; }
  function tint(c) { return '<span class="lg-tint" style="border-left-color:' + c + ';background:' + c + '22"></span>'; }
  function tag(bg, fg, t) { return '<span class="lg-tag" style="background:' + bg + ';color:' + fg + '">' + t + '</span>'; }
  function box(bg, bd, fg, t) { return '<span class="lg-box" style="background:' + bg + ';border-color:' + bd + ';color:' + fg + '">' + t + '</span>'; }
  function rows(list) {
    return '<table class="lg-t">' + list.map(function (r) { return '<tr><td class="lg-k">' + r[0] + '</td><td>' + r[1] + '</td></tr>'; }).join('') + '</table>';
  }
  function sec(title, body) { return '<h3>' + title + '</h3>' + body; }

  // ------------------------------------------------------------------ content
  var TABDOTS = sec('Tab dots (top bar)', rows([
    [dot(G), 'That part is running on the spare Mac.'],
    [dot(R), 'Not running. On the Mac: <code>./jb status</code>, then <code>./jb restart</code>, or tap <b>Restart</b> in the red banner.'],
    [dot(DIM), 'Algo tabs only: the robot isn\'t running, but its last backtest is still shown.'],
    [tag(R, '#fff', 'LIVE'), 'On the Webull Algo / IBKR Algo tab button: that algo is trading real money right now (Go Live or <code>./jb live</code>). It blinks slowly and goes away when live trading stops.'],
    ['Tab colours', 'On the computer each tab has a soft line under it by family: <b style="color:#5aa9ff">blue</b> prices (IBKR, Webull, World, Scanner, Chart), <b style="color:#3ddc84">green</b> trading (the algos, My Trading, Portfolio), <b style="color:#b98cff">purple</b> research (News, Signals, Analysis, Macro, Night Desk, Report Card, Ask Claude, Recap), <b style="color:#96a5b9">grey</b> system (Health). Colours only.'],
    ['⌕ or /', 'The <b>command bar</b> (next to the board\'s name, or press / on the Mac; on the Phone page it is the search box). Type <b>TQQQ</b> for its chart, <b>TQQQ buy</b> or <b>TQQQ sell</b> for the chart with the order ticket, <b>TQQQ news</b>, <b>flip SOXL</b>, <b>TQQQ note</b> your words, or a tab name like <b>world</b>. Any symbol works, also futures (NQ=F) and indexes (^N225), drawn in the board\'s own chart with Yahoo prices.'],
    ['Keys (Mac)', '<b>1</b>-<b>9</b>, <b>0</b> = tabs in order; <b>B</b> / <b>S</b> = buy / sell ticket on the selected symbol (the last tile you clicked or pointed at, or the open chart); <b>Esc</b> closes pop-ups; <b>?</b> shows the list. Not while you are typing in a box.'],
    ['Since you last looked', 'When you come back to the board or the Phone page after 20+ minutes, a small box at the bottom lists what happened meanwhile: fills, alerts, scheduled orders and stops, big moves in your symbols and headlines on what you hold. ✕ closes it.']
  ])) + sec('Futures mood and Picks mood meters', rows([
    ['Where', 'Two meters at the top of the full board, under the tabs, and on the Phone page right above <b>Mood since the 4 pm close</b>. Same look: a score from ' + txt(R, '-100') + ' to ' + txt(G, '+100') + ', a word, and the needle on the red-to-green bar.'],
    ['Futures mood', 'S&P, Nasdaq, Dow and Russell 2000 futures (S&P and Nasdaq count double). <b>Risk-on</b> +25 or more, <b>Mixed</b>, <b>Risk-off</b> -25 or less. Each move is softened so one big mover can\'t swing it alone; the moves are listed underneath.'],
    ['Picks mood', 'The scanner\'s Picks (the ones on the Scanner tab, ETFs or Stocks, whichever mode it is in; up to 12). <b>Bullish</b> +25 or more, <b>Mixed</b>, <b>Bearish</b> -25 or less, with how many are up and down and the average move. Picks prices come from the board\'s own IBKR record first (it has overnight prices), else Yahoo.'],
    ['Window buttons', 'Each meter has its own: <b>Open</b> (since 9:30 am), <b>Close</b> (since 4 pm), <b>After-hrs</b> (since after-hours began at 4 pm, so it reads the same as Close), <b>Overnight</b> (since 8 pm) and <b>Pre-mkt</b> (since 4 am). Each always means the most recent one that has passed, New York time, skipping weekends and market holidays (1 pm on early-close days). Hover a button to see the exact start time. Your choice is remembered on each device.'],
    ['Updates', 'Prices are checked every 5 minutes (Yahoo limits how often we may ask) and the page refreshes every minute. Right after a restart it says Getting prices for about a minute: that is normal.']
  ])) + sec('Phone bar, Chart and Report Card', rows([
    ['Phone bar', 'On a phone every page has a bar at the bottom: <b>Back</b> (the page or tab you came from; on the full board it steps back through the tabs you opened), <b>Home</b> (the Phone page), <b>Chart</b> and <b>Menu</b> (every page and tab as big boxes, one tap away). Computers are unchanged.'],
    ['Chart tab', 'Type any ticker (TQQQ, AAPL, NQ=F, ^N225) for its line chart over 1D, 5D, 1M, 6M, 1Y, 5Y or All (its full history, one point a month). Prices come from <b>IBKR</b> first, including the overnight venue; from Yahoo when IBKR can\'t (indexes, crypto, or stocks your IBKR data plan doesn\'t cover). The line under the chart says which. Shaded with a paler line = outside regular hours; cyan dashed = 9:30 am open; grey dashed = the price at the start of the range. Touch the chart to read a price. Your last 8 symbols are buttons. Also on the phone (Chart in the bottom bar).'],
    ['Chart order flow', 'On the Chart tab at 1D, the <b>Order flow</b> buttons turn four extras on or off (all on at first; turn them all off for a clean chart). <b>Buy/Sell line</b>: shares bought at the ask minus shares sold at the bid, added up since 8 pm ET (6 pm for futures); line going up = buyers are paying up. Dashed part = estimated from 1-minute bars, before the page started watching. <b>Big trades</b>: trades 10x the day\'s average size and worth $100k+, circled on the price line (green buy, red sell, amber in between) and listed. <b>Volume at price</b>: side bars showing how many shares traded at each price today, busiest in amber. <b>Bid/Ask</b>: the best bid and ask right now, with the shares on each side. Free IBKR data, so it only works for symbols with live IBKR prices (not NYSE stocks your plan lacks, indexes or crypto) and up to 3 symbols at a time. It is not the full order book (that needs paid Level 2 data).'],
    ['Your trades on the chart', 'On the Chart tab, your own fills from the My trading journal are small arrows: ' + txt(G, '▲ buy') + ' and ' + txt(R, '▼ sell') + '. Touch or point near one for the shares, price, broker and, for a sale, the profit or loss.'],
    ['Board math on the Chart', 'Buttons under Order flow, each on or off: <b>S/R lines</b> = support (green, S) and resistance (red, R) where the price turned at least twice in the range shown, with how many touches; <b>Patterns</b> = simple shapes the board spots (double top or bottom, bull or bear flag, rising or falling wedge), circled with a label (a possibility, not a promise); <b>Why it moved</b> = the trend arrows, the market/sector/own bar, the why line, a confidence line and the last levels crossed, under the chart.'],
    ['Report Card tab', 'How often each part of the board was <b>right</b>: the mood meters, Picks and each of their signals, the ETF pick, gap scorecard, 4 am gaps, alerts, sentiment report, MACRO score, earnings expected moves, max pain, put/call, politicians\' trades, relative strength, market weather and event days. The board writes down every call and grades it later with free Yahoo prices: 30 and 60 minutes later, the next open, the next close, and 1, 5, 20 and 60 days later. ' + txt(G, 'Green') + ' 55%+ right, ' + txt(FLAG, 'amber') + ' 45-55%, ' + txt(R, 'red') + ' under 45%, grey = fewer than 20 graded. <b>50% = a coin flip.</b> <b>Live</b> = calls saved since install; <b>Replay</b> = the same rules re-run on up to 2 years of past prices (price-only features; runs each evening, takes a few minutes). Tap a card for each part, each session, a line of accuracy over time, and the latest calls with ✓ / ✗. <b>Your trades vs the board</b> shows how your trades did when the Futures mood agreed with you.'],
    ['Report Card summary', 'A strip above the cards: the 3 features right most often and the 3 least (live grades once there are 20, else the 2-year replay). Tap a name to open its card. <b>Sort by</b>: As before (the normal order), Most right, or Newest (the most recently graded first).'],
    ['Rules (if this, then that)', 'On the Phone page, the <b>Rules</b> card, and the Rules page it links to. <b>When</b> a price crosses a level, a symbol is up or down X% since the 4 pm close, a why-tag shows up (OWN MOVE, HEAVY VOL, BREAKOUT...), the Futures mood turns Risk-on or Risk-off, or a time of day comes (ET, weekdays), <b>then</b> send a push, send a push that opens the order ticket filled in (you check it and tap Send yourself), or place a <b>paper</b> order tracked on the Rules page with its profit or loss. Checked every 30 seconds in every session; each rule then stays quiet for the minutes you set. <b>Test</b> runs a rule\'s action once. A rule never sends a real order by itself.'],
    ['Strategy lab', '<b>Strategy lab</b> and <b>Robustness test</b> buttons next to Run backtest on each algo tab. <b>Build a strategy</b>: pick conditions from lists (price, % since the 4 pm close, this session\'s high or low so far, VWAP, averages, RSI, % change, Bollinger, MACD, time, session) and stop, target, trailing stop and sessions; it is saved as strategies/lab-name.yaml, always paper, and Run backtest works on it. <b>Robustness test</b>: about 35 backtests (10 to 20 minutes, in the background) on the board\'s own minute record. A grid of two settings from 60% to 140% (mostly green = robust, one green square = luck), walk-forward (tuned on the older 70%, tested on the newer 30%) and Monte Carlo (trades reshuffled 1,000 times: realistic worst drop and losing streak). Research, not a promise.'],
    ['Report Card explorer', '<b>Explorer, backtest</b> at the top of the Report Card. <b>Explore calls</b>: pick a feature, Live or Replay (2 years), a time point, a kind of call, a symbol, a session and dates; see how often it was right with a likely range (wide when there are few calls), the average when right and when wrong, the result per call, the profit factor, splits by kind, session, symbol and weekday, a month-by-month line and every call with ✓ / ✗. <b>Download CSV</b> exports the filtered calls. <b>Backtest and Monte Carlo</b>: each up/down call as a trade (minus a cost you set), per feature and for the board as a whole, with a growth line, worst drop, Sharpe, 1,000 reshuffles showing the range of results the same edge could have given, and a luck test (coin flips on the same calls). It reruns each evening at 7:15 pm ET. Research, not a promise.'],
    ['right 61% tags', 'Next to the Futures mood, Picks mood and Mood since the 4 pm close meters: how often that meter (over the window you picked) pointed the right way by the next close (the open for the 4 pm mood), with how many readings. Tap it to open the Report Card.'],
    ['Weekly push', 'Fridays 8:30 pm New York: the week\'s best and worst feature. Switch it off at the bottom of the Report Card.']
  ])) + sec('Delta Hedging (October 12)', rows([
    ['Net Exposure', 'On the Portfolio tab (above Positions), the My Trading tab (under the menu) and the Phone page (a card under Your Holdings): your total market delta across IBKR, Webull and the trades you typed in, as <b>dollars gained or lost per 1% move</b> in the Nasdaq-100 and in the S&amp;P 500. ' + txt(G, 'green') + ' = you gain when the market rises (Net Long), ' + txt(R, 'red') + ' = you gain when it falls (Net Short), grey = About Flat. <b>Like Holding $X Of QQQ</b> says the same thing as a QQQ position. Read only: nothing is sent to a broker.'],
    ['How A Row Counts', 'Value x leverage x beta. Leverage: 3x funds count 3, inverse funds count negative (SQQQ = -3). Beta: how much what the fund follows moves with QQQ (or SPY), measured on the board\'s own prices (half-hour moves in regular hours from the minute record, else daily closes; Yahoo\'s free closes only when the board has too little). IBKR futures count at full size (MNQ = $2 a Nasdaq-100 point). <b>Details</b> shows every holding with its numbers; ' + txt(FLAG, 'Estimated Beta') + ' = not enough history yet, a fixed guess is used.'],
    ['Hedge Calculator', 'Portfolio tab (under Positions) and the Phone page (<b>Hedge Calculator</b> button in the Net Exposure card). Pick the index (Nasdaq-100 or S&amp;P 500) and how much to hedge (25-100%). Each choice says how many shares of an inverse fund (SQQQ, QID, PSQ, SPXU, SH... and the sector inverse matched to what you hold, such as SOXS for SOXL) or how many <b>MNQ / MES</b> micro futures (IBKR only) bring you to about flat: what is left per 1%, the buying power it uses (futures: what one contract controls and a rough margin range), its daily decay (leveraged funds lose a little each day in choppy markets; futures have none) and <b>Cuts Swings</b>: how much smaller your half-hour ups and downs would have been with it on the board\'s own record. ' + tag('#0e3a1c', '#7fdc8f', 'BEST MATCH') + ' = cuts swings the most (ties: the least buying power).'],
    ['Copy To Ticket', 'Fills the order ticket with that hedge (broker from the IBKR / Webull switch, Buy, shares, a limit at the current price). On the Phone page it fills the Order ticket card; on the Portfolio tab a ticket opens under the calculator. <b>Nothing is sent</b> until you tap Check order, then the red Send. Futures get <b>Copy Order Text</b> instead (the board\'s ticket takes stocks and ETFs only): paste it into the IBKR app or TWS.'],
    ['Hedge Partners', 'Portfolio tab, under the Hedge Calculator: for each holding, its ' + tag('#0e3a1c', '#7fdc8f', 'BEST PARTNER') + ' (the <b>Inverse Twin</b> such as SQQQ for TQQQ, a <b>Sector Inverse</b> such as SOXS, an <b>Index Inverse</b>, or <b>MNQ / MES</b> at IBKR) and <b>Other Partners</b>. Each shows how well it hedged on the board\'s own record: <b>Corr</b> (-1.00 = always moved exactly the other way), <b>Cuts Daily Swings</b> (daily closes) and <b>Overnight Swings</b> (4 pm close to the 9:30 open), how many $ of it per $1 of the holding, the shares or contracts for your position, and the weekly decay of 3x funds (the holding\'s own decay is on its title line). Best = cut the swings most; close calls go to the cheaper one to hold. Copy To Ticket works as in the calculator.'],
    ['Hedge Ideas (Research)', 'My Trading tab, above the replay (and <b>Hedge Ideas</b> in its menu). ' + box('#2e2400', '#7a5c00', '#ffe9a6', 'RESEARCH, NOT ADVICE') + ' Pair ideas backtested on a few weeks of the board\'s own prices: a fund held (or bought on a 1% or 2% dip vs the 4 pm close) and hedged with its inverse twin, a sector or index inverse, the inverse of a fund it usually moves with, or a micro future (IBKR only). Each runs through <b>After-Hours</b>, <b>Overnight</b> and <b>Pre-Market</b> (entered in the first 30 minutes, closed 15 minutes before the session ends), plus a <b>Day Hold</b> on daily closes, after costs. Shown: trades, win rate, average and total result, <b>Worst Drop</b> (the deepest fall from a high). ★ = the session it is ranked by (steadiest average, at least 8 trades). <b>Run Now</b> works them out again (also every 6 hours).'],
    ['Send To Run Backtest', 'Pick the IBKR or Webull algo and a session: the board writes the idea as a strategy file (with a <b>hedge:</b> section) in that algo\'s strategies folder and starts its backtest on the board\'s own record. The algo tab then shows the result alone and <b>With Hedge</b> (return, max drawdown, win rate, hedge P&amp;L). Futures ideas go to IBKR only, overnight ideas too (no overnight prices on your Webull plan). It does not change what the paper or live algo trades.'],
    ['Auto-Hedge (Algo Tabs)', 'In the Paper / live trading card of each algo tab. <b>Turn On Auto-Hedge</b>: every position the algo opens gets a hedge right after the buy fills (an inverse fund bought, or MNQ / MES micro futures sold, IBKR only), sized by <b>Hedge With</b> (Auto = the fund that cut this symbol\'s swings most on the board\'s record) and <b>Hedge Ratio</b> (100% = fully hedged), and the hedge is closed with the position. <b>Default OFF.</b> Paper first: in paper the hedge orders are simulated (filled at the last price plus the usual paper costs). The line under it shows the open hedge, its result today, and why it is not hedging when it is not.'],
    ['Live Hedging', 'Its own switch under Auto-Hedge: <b>Turn On Live Hedging…</b> asks you to type <b>HEDGE LIVE</b> and only works while <code>./jb live</code> runs for that algo; it counts for that run only (a new ./jb live starts with live hedging off). Turning Auto-Hedge off closes an open hedge; turning only live hedging off keeps the open hedge until the position closes, and opens no new one.'],
    ['Auto-Hedge Safety', 'The hedge follows every safety rule of the algo: <b>Stop</b> and the <b>daily loss limit</b> close it and open no new one (the hedge\'s result counts toward the algo\'s daily loss); while <b>SELL ALL</b> runs no hedge order is sent at all, and SELL ALL closes the hedge too (futures included); the <b>buying power</b> check, <b>max order value</b>, <b>order speed limits</b> and duplicate check count hedge orders; after a <b>rejected order</b> no new hedge for 15 minutes and 3 rejections in a row stop live trading; an order in doubt pauses live trading. A hedge is never left open after the position closes, and it never sells more than the broker holds.'],
    ['Leftover Hedge At Start-Up', 'Every live hedge order is written down before it is sent (Webull: its order id starts with <b>jbhedge</b>; IBKR: tagged jb-hedge). When live trading starts: a hedge order still open from an earlier run stops it (cancel it in the broker app, then start again); one that filled while the algo was off is taken over as the algo\'s hedge; hedge-fund shares the account holds beyond the algo\'s record are shown on the algo tab and no second hedge is opened while they are there (the algo never sells them). Paper keeps its saved hedge after a restart.']
  ])) + sec('Algo safety (October 11)', rows([
    ['Buying power check', 'Live only: before every buy, the algo compares the order with the buying power your broker reports (read every minute). Not enough: the buy is skipped and the algo tab says why. Selling is never blocked.'],
    ['No buy-back after a stop', 'After a stop-loss, take-profit or trailing stop, the algo waits until its buy signal has switched off once before it can buy again (paper, live and backtests alike).'],
    ['Stop works while paused', 'The Stop button now closes the position even when the algo is paused (after 3 rejected orders or the speed limit). It still checks the broker first, so nothing is sold twice.'],
    ['Exits retried', 'An exit order that did not fill is sent again a little later. It no longer pauses the algo while it still holds shares.'],
    ['IBKR prices stopped', 'If the IBKR algo gets no prices for 5 minutes (regular hours; longer at night) it asks IBKR again and pushes your phone. Urgent when it holds a position, since the stop-loss needs prices. Link to IBKR lost (code 1100) = safe mode until it is back.'],
    ['Webull Prices Stopped', 'The Webull algo now works like the IBKR one: no prices for 5 minutes in regular hours (15 pre-market or after-hours, 30 overnight when your plan has overnight prices) and it reconnects to Webull, and your phone gets <b>Webull algo: prices stopped</b> once per outage when the algo may trade then or holds a position (urgent while it holds one).'],
    ['Webull Connection Lost', 'If Webull\'s price stream loses its link (or Webull says another device took over the market data), the Webull algo goes into safe mode like IBKR\'s code 1100: no orders until the stream is back for a minute and the position matches Webull.'],
    ['Webull Short Sales', 'Strategies that sell short (entry side: sell) can now trade live at Webull, only on a margin account: at start-up the algo asks Webull to preview a 1-share short sale (never placed) and refuses to start if Webull says no. Same checks as buys (buying power, order size cap, speed limits, safe mode). Never overnight: Webull takes no short sales then, so paper and backtests skip them too. The algo tab\'s note line says so.'],
    ['Backup stop at the broker', 'Live only: right after each buy, the algo also places a stop order at IBKR or Webull, 1% wider than its own stop-loss (10% if the strategy has none), so the shares stay protected if the Mac or the algo is down. The algo cancels it before it sells, so nothing is sold twice. Hours covered: see Backup stop hours under Review fixes (October 11). Set risk: broker_stop_pct in the strategy file to change it (0 = off).'],
    ['Loss limit pauses algos', 'Daily loss limit on My trading now also pauses both algos when it is hit (turned on October 11).'],
    ['Commissions Paid', 'My Trading tab: the <b>Commissions Paid</b> tile shows what IBKR and Webull charged today, the last 7 days and 30 days, and each finished trade has a <b>Fees</b> column. The board reads them from the brokers with your fills (IBKR every 5 minutes, Webull every 10). P&amp;L there is after fees. Webull usually charges no commission on stocks, so its line is often $0 or only the small regulatory fees on sells.']
  ])) + sec('Review fixes (October 11)', rows([
    ['SELL ALL comes first', 'While SELL ALL is selling a position, that algo sends no sell of its own (no double sell). Pressing Stop during SELL ALL keeps it that way.'],
    ['SELL ALL retry', 'If SELL ALL could not reach the broker when you pressed it (for example IB Gateway was restarting), the automatic follow-up a minute later presses it again. A stock bought after the press is still never sold by the follow-up.'],
    ['Sells have no dollar cap', 'The order ticket\'s per-order limit (JB_TICKET_MAX, $5,000 to start) now applies to buys only. Board stops, Flip and scheduled sells work for any size; a sell is still never more than you hold.'],
    ['Flip and the buy limit', 'Flip\'s buy half still counts as a buy for JB_TICKET_MAX. If the opposite fund would cost more than that, Flip says so before anything is sold (raise JB_TICKET_MAX to flip bigger positions).'],
    ['Rejected order', 'A rejected order pauses the algo\'s BUYS for 15 minutes (3 in a row: until restart). Its stop-loss, exits and trailing stop keep working and retry after 1 minute.'],
    ['Speed limit counts real orders', 'The algo\'s order speed limit (3 a minute, 30 a day) now counts only orders it really sends. Buys it skipped while waiting after a rejection no longer add up to a false "too many orders" pause.'],
    ['LIVE trader watched', 'While ./jb live runs, your phone gets a loud push if its Terminal window is gone (closed, crash, Mac restart) or it stops checking in. Always on.'],
    ['LIVE trader crash', 'If the live trader stops with an error while its Terminal window stays open, your phone also gets one loud push (it no longer watches the stop-loss). Ctrl+C sends no push.'],
    ['Backup stop hours', 'Each backup stop line on the algo tab says which hours it covers. IBKR: 4 am - 8 pm (IBKR takes no stop orders overnight). Webull: asked for all sessions, falling back to 9:30 - 4 if Webull refuses; check the order in the Webull app. Overnight at IBKR, only the algo\'s own stop-loss protects the shares, so keep live trading running. Overnight at Webull (from October 12): the algo asks for the stop in Webull\'s night session; if Webull refuses, the day stop stays and the night one is not asked for again for a week. The stop is switched at 8 pm and 3:50 am (the old one cancelled first, never two at once).'],
    ['Backup stop no answer', 'If the broker gives no answer about the backup stop, the algo\'s exit waits (selling then could sell twice) and your phone is told once. The stop itself still protects the shares.'],
    ['Backup stop gone', 'If the broker gives no answer about the backup stop but its own list of open orders shows no such stop (checked twice, 10 seconds apart) while you still hold all the shares, the stop is gone, so the algo\'s exit sells instead of waiting forever.'],
    ['Overnight prices', 'If prices stop overnight, the algo asks again for the overnight prices too (IBKR overnight venue; Webull only if your plan has overnight).'],
    ['Webull board overnight', 'If the Webull board gets no prices at all for 15 minutes overnight (when your Webull plan has overnight prices), it reconnects by itself, as it already did in the day. The board\'s own safety stops on Webull stocks need those prices.'],
    ['Stops after a restart', 'A board safety stop that was selling when the board restarted goes back on, and sells only what you still hold.'],
    ['No stacked sells', 'Before a board stop, Flip or scheduled sell sends its sell, it cancels the board\'s own open sell orders for that stock (for example one sent just before a restart). If one can\'t be cancelled, nothing new is sent and your phone is told: this way the board never has more shares up for sale than you hold.'],
    ['IBKR stop cancelled first', 'If you placed a real stop at IBKR from the protection check (Place at IBKR) and the board\'s own safety stop sells first (overnight), the board now cancels that IBKR stop before it sells. Before, the IBKR stop stayed open and could sell the same shares again at 4 am, leaving you short.'],
    ['Monday numbers', 'The board now keeps 100 hours of prices, so Friday\'s close is still there on Monday: vs-close numbers, morning cards and the loss limit work on Mondays and after holidays. The last close knows holidays and 1 pm early closes.'],
    ['Loss limit can lag', 'The daily loss limit also counts positions in symbols that are on no board (Yahoo price). Fills arrive every 5 min (IBKR) or 10 min (Webull), so the total can lag by that much.'],
    ['Loss limit and futures', 'Only stocks and ETFs on no board are priced from Yahoo. A future (such as CL=F) counts toward the daily loss limit only while it is on the IBKR board: Yahoo would have looked up a different company (CLF) and shown a huge false loss.'],
    ['Password needed to trade', 'With no JB_PASSWORD set, the board refuses orders, SELL ALL and algo changes (anyone on your Wi-Fi could otherwise press them). Requests from hidden frames on other websites are refused.'],
    ['Login works again', 'The October 11 hidden-frame protection also refused the board\'s own login form (browsers send it the same way), so a new phone or browser could not log in. The login page is allowed again; orders, SELL ALL and algo changes from hidden frames are still refused.'],
    ['Phone tiles fit', 'On a phone, the long chips on the IBKR and Webull board tiles (SPREAD, AH PEAK) now wrap onto two lines inside the tile instead of being cut off and making the page scroll sideways.'],
    ['Paper exits while paused', 'In paper mode too, a pause (speed limit, rejected orders) stops new buys only; the paper stop-loss and exits still run, the same as live.'],
    ['Your own broker orders', 'When a board stop, Flip or scheduled sell sells shares, the board never cancels sell or stop orders YOU placed in the IBKR or Webull app, but the push now says if one is still open for that stock, so you can cancel it before it sells again.'],
    ['Odd links hidden', 'News, press release, Truth Social and web-watch items only show a link when it is a normal web address (http or https).']
  ])) + sec('Moved and merged (October 10)', rows([
    ['Fewer tabs', 'Three tabs now sit inside others, with a switch at the top of the page: <b>Signals | Health</b> on the Signals tab, <b>Portfolio | My Trading</b> on the Portfolio tab. The <b>Recap</b> video plays near the top of the <b>Night Desk</b> (pick a recap from the list, or Make One Now; the Recap Page link opens the full page). Nothing was removed: old links and the phone Menu still land on them.'],
    ['Portfolio from my accounts', 'Your Positions on the Portfolio tab now fill themselves every 5 minutes from your IBKR account and your Webull Individual Margin account (shares and average cost; read only, nothing is ever sold or bought). Each row is tagged IBKR, Webull, or Typed for a trade you entered that neither account holds. The bar above the list shows when it last read each account, with Refresh Now and Turn Off (back to only your typed-in trades).'],
    ['World to Macro', 'The Fed meeting odds (next 4 meetings), Nasdaq-100 breadth, Bitcoin order book and the economic calendar moved from World to the <b>Macro</b> tab, above The indicators. World keeps its VIX curve warning and the Next big event line.'],
    ['Scanner tabs', 'A second row next to Movers | Picks: <b>Top 5</b>, <b>Ask Claude</b>, <b>ETF Pick</b>, <b>Gappers</b>, <b>Twins</b>, <b>Politics</b>. The For research only box above the Picks is gone. ETF Pick (with its push switch), Gappers (pre-market gapper checklist), Twins (leveraged ETF twins) and Politics (Politicians\' trades plus Big policy posts, full width) are the very same parts as on the Night Desk, so a switch changed on one page changes on the other.'],
    ['Top 5', 'The scanner\'s five strongest Picks that lean up or down, ranked like the Picks (rechecked every minute). Each card: rank, lean, price now and when picked, <b>Since Picked (Its Way)</b> (a down call gains when the price falls), Vs 4 PM Close, a line of the price since it was picked, <b>Performance Rank</b> (the five ordered by how they did since picked), signals agreeing and the board\'s record on that symbol. Above them, the <b>Board Grade</b> (A to F from its 1-hour results after 10 graded calls) and how often it was right after 30 minutes, 1 hour, 4 hours and 1 day. Below, a line of the running total over time and the recent calls. Kept 45 days in data/top5.json. Research, not advice.'],
    ['Ask Claude box', 'On the Scanner: a stripped-back Ask Claude. Type a question and press <b>Ask Claude With Live Data</b>: the scanner\'s numbers at that moment (biggest movers, latest flags, Picks, Top 5) and the board\'s market data always go with it. It copies and opens the Claude app (free; nothing is sent until you press send there). Claude cannot watch a live stream for free, so each press takes a fresh snapshot.'],
    ['Algo notes', 'On both algo tabs, a card with two boxes in your own words: <b>Algorithm Themes</b> and <b>Backtesting Ideas</b> (saved on the Mac as you type). <b>Copy for Claude: Build It</b> sends them with the strategy file, its format and the last backtest so Claude writes a strategy file; <b>Copy for Claude: Check Before We Run</b> asks for a review first. Paste Claude\'s file under <b>Paste the strategy Claude wrote</b>: the Mac checks it with the algo\'s own code and saves it paper only as claude-&lt;name&gt;.yaml, ready in the Run backtest list. It never switches the running algo or trades live.'],
    ['Night vs day', 'On the Night Desk\'s Tonight tab, Night vs day (holding overnight vs in the day) now sits above Overnight venue.'],
    ['Page titles', 'Page names (Macro, Night Desk, World...) use a formal engraved font with a soft gradient inside the letters: silver on the dark looks, navy on Clean light. Same on the phone.']
  ])) + sec('Phone (October 10)', rows([
    ['Phone page buttons', 'Top right, three buttons the same size and colour: <b>▶ Channel</b>, <b>▦ Board</b> (the full board laid out like the computer; pinch to zoom, tap Phone at the top to come back) and the look button.'],
    ['IBKR groups on the Phone page', 'Under <b>Mood since the 4 pm close</b>, a live copy of the IBKR board\'s <b>US EQUITIES</b> and <b>FUTURES</b> groups: the same tiles, group menu and WHY? buttons. A change made there (removing a symbol, picking a group) also changes the IBKR board.'],
    ['Chips and holdings', 'The Moves, Ticket, My trading, Alerts and Coming up chips fit the screen in one even row. <b>Your holdings</b> now sits under the Order ticket.'],
    ['Board tiles', 'On a phone the IBKR and Webull boards show the computer\'s ticker boxes, two across: price, VS 4PM CLOSE, OVERNIGHT, 9:30 GAP, the range bars and all ten change boxes. <b>List</b> switches to the compact rows (and Tiles back).'],
    ['Meters scroll away', 'On the full board on a phone, the Futures mood and Picks mood meters slide up out of the way as you scroll down, and come back at the top.'],
    ['Tab list', '<b>☰ Tabs</b> opens big boxes for every tab with the one you are on marked in green, a <b>✕ Close</b> button and <b>‹ Back to</b> the tab you were on.'],
    ['Sideways drift', 'If a page slides left or right, it glides back to the middle as soon as you let go. The bottom bar sits right at the bottom on every page, and on System check shows all four buttons again.'],
    ['Even rows', 'The Night Desk section tabs (Tonight, Trade, ETF Edge...) and Portfolio\'s Add a trade boxes line up in even rows on a phone.']
  ])) + sec('Roku TV view', rows([
    ['What', 'A TV app for your Roku called <b>Alex Hale\'s Board</b>. Top: the Market mood, Futures mood and Picks mood meters. Middle: the IBKR board\'s stocks and futures as tiles, in two groups, just like the IBKR board: they flash green or red as the price moves, show the same change boxes as the IBKR board (1M, 5M, 15M, 45M, 4H, 8H, 1D, 3D, 1W, 1MO and O/N), and have the O/N, 1W and 52W range sliders (up to 24 tiles, prices every 2 seconds). Bottom: Top movers, a <b>Futures 24 h</b> chart (one coloured line per IBKR future, % change over the last 24 hours, best first in its key), Picks, and big reports coming up with the latest alerts, then the market weather. The rest refreshes every 15 seconds, and the Roku screensaver stays away.'],
    ['Open it', 'Press Home on the Roku remote and pick <b>Alex Hale\'s Board</b> (at the end of your channels; to move it, highlight it, press the star button and choose Move channel).'],
    ['Read only', 'The TV can only look. It cannot trade, press SELL ALL, pause algos or change settings, and it never shows your account money or positions (anyone in the room can see a TV).'],
    ['Where it works', 'The Roku can\'t run Tailscale, so it tries the Mac on the home Wi-Fi first and, if that fails, your ts.net link (any Wi-Fi). The top line says which one it is using. It uses its own TV key, not the board password, and that key opens only this TV summary.'],
    ['OK button', 'Press <b>OK</b> on the Roku remote to reconnect right away (it only asks the board again; it changes nothing). If the Mac\'s Wi-Fi address changes, the TV learns the new one by itself through the ts.net link, so nothing needs remaking.'],
    ['If it still can\'t reach the Mac', 'Check the Mac is on and awake. As a last resort, in Terminal run <b>bash ~/josephs-board/roku/make-tv-app.sh</b> and send the new <b>Board-TV.zip</b> from Downloads to the Roku again.'],
    ['Lock out the TV', 'Delete data/tv_key.txt, restart the gateway, and make the TV app again.']
  ])) + sec('Alex\'s channel', rows([
    ['What', 'Your board as a TV news channel, made from the board\'s own numbers. Two anchors read what is on screen, segment by segment: BREAKING when a board alert fires, the opening with your mood meters, futures with a chart that draws itself, Top movers, Picks with their reasons, the news for your symbols, sectors, Coming up and the market weather, then 2 or 3 <b>deep dives</b>. A ticker crawls along the bottom and the corner shows ES, NQ and the clock. Shows are only made while someone is watching.'],
    ['Deep dives', 'Taken in turn from a pool so the next loop is different (a full cycle runs about 10 to 15 minutes before anything repeats): one of your 3x funds in depth, futures in depth, sector rotation, rates / dollar / yen / oil, the calendar with earnings, the desk view, Asia and Europe, big traders\' positions (CFTC), crypto, and Fed odds. Each says <b>why</b> (linked causes, and the board\'s own move breakdown when installed), <b>compared to normal</b> (vs its usual overnight move, days in a row, where it sits in its 1-week and 52-week range), the <b>session story</b> (after-hours, overnight, pre-market) and <b>what to watch</b> (overnight high and low, the 4 pm close, the options magnet, the next big event).'],
    ['What changed', 'The anchor remembers what it last said. If a number barely moved it says "still" or skips it; if it moved it leads with that ("Since my last update 9 minutes ago, Nasdaq futures slipped another half percent"). Numbers are rounded the way people say them, companies are said by name (Nvidia, not N V D A), and each line has several wordings so it never repeats word for word.'],
    ['Show schedule', 'The show name is next to LIVE. <b>Headline Hour</b> at the top of each hour (3 deep dives), <b>Quick Update</b> at :15, :30 and :45 (only what changed), the <b>Closing Bell Wrap</b> at 4 pm, the <b>Overnight Open</b> at 8 pm, the <b>Asia and Europe Check</b> at midnight, <b>QUIET HOURS</b> from 1 to 3 am, the <b>Pre-market Preview</b> at 4 am, the <b>Opening Bell Preview</b> at 9:15, the <b>Sunday Futures Open</b> at 6 pm Sunday, and the <b>Weekend Desk</b> when markets are closed.'],
    ['Quiet hours', 'From 1 to 3 am ET a big purple <b>QUIET HOURS</b> band with a moon sits at the top of the screen for the whole show, the anchor says it is quiet hours, and the deep dives are swapped for short <b>explainers</b> built from your board\'s own numbers: why 3x funds lose value in choppy weeks, how often overnight gaps hold into the open, how much of the move happens overnight vs in the day, and how often there are big days. The Weekend Desk shows the same band and one explainer.'],
    ['Specials', 'Up to 3 hours before a big report (CPI, jobs, the Fed, PCE, GDP, retail sales) a SPECIAL REPORT with a countdown, the forecast and last time; in the hour after it, WHAT JUST HAPPENED with how Nasdaq and S&P futures moved since. The same for earnings of your own symbols (the options expected move and the usual earnings move, then the real move).'],
    ['Desk view', 'THE DESK VIEW: the board\'s rules in one line (leaning risk-on, risk-off or neutral, and whether the meters agree), with its Report Card record ("the futures mood has been right 61 percent of the time") and whether its last graded call was right. Rules only, not advice.'],
    ['Voices', '<a href="channel-voices.html">Voices page</a> (channel-voices.html, or Voices at the top of the channel): try each voice with ▶ Sample and pick anchor 1 (markets) and anchor 2 (news and coming up), the speed, and the chime. <b>Natural</b> voices are free Piper voices made on the Mac itself, no internet (Lessac, Ryan and Amy). If they are not installed, too slow on this Mac, or ever fail, the channel uses the Mac voice (Samantha, or JB_CHANNEL_VOICE / JB_RECAP_VOICE in settings.env). A short chime made on the Mac starts each story (a longer one for BREAKING).'],
    ['On the TV', 'In the Roku app <b>Alex Hale\'s Board</b>, press the <b>right arrow</b> for the channel and the <b>left arrow</b> (or Back) for the board. In the channel: <b>OK</b> or <b>Play</b> turns the sound off and on, the <b>right arrow</b> skips to the next segment.'],
    ['On the phone and Mac', '<a href="channel.html" target="_blank">Open Alex\'s channel</a> (channel.html), or click the small <b>▶ Channel</b> button next to the look button (top right of the board and the Phone page), or tap <b>Watch Alex\'s Channel</b> on the Phone page or <b>Alex\'s Channel</b> in the phone Menu. Tap <b>Tune in with sound</b> (phones only play sound after a tap). Turn the phone sideways for a bigger picture. Touch or move the mouse for Board, Voices, Mute and Next. On the start screen, <b>‹ Back to mobile home</b> goes back to the Phone page.'],
    ['Captions', 'When the sound is off (OK on the Roku remote, Mute or Watch muted on the phone and Mac), the anchor\'s words show as closed captions near the bottom of the screen, two lines at a time, paced like the voice, with tickers written normally.'],
    ['Read only', 'It only reads what the board already shows. No account money or positions are shown or spoken, since a TV can be seen and heard by anyone in the room. The TV uses its TV key; the phone and Mac use the board login. Sound files are kept in data/channel/ and old ones are deleted by themselves.']
  ]));

  function priceBoard(isWebull) {
    var tags = isWebull ? [
      [box('#1f1800', '#7a5c00', Y, 'No overnight prices here…'), 'Your Webull plan has no overnight session (8 pm – 4 am ET), so stocks stop updating overnight. Day, pre-market and after-hours still work. The IBKR tab shows overnight prices.'],
      ['<span style="color:#ff8a80">no Webull futures data…</span>', 'On a futures tile (ES=F etc.): normal. Futures come from the IBKR tab.'],
      [box('#0b2238', '#164060', '#f2f2f2', '100 symbols'), 'Webull streams at most 100 symbols. Extra ones may not update.']
    ] : [
      [tag(Y, '#04101f', 'DELAYED'), 'No live-data subscription for this symbol (e.g. CL=F, GC=F), so IBKR\'s free prices are shown, about 10–20 minutes old. The price looks slightly faded. The algo never trades on these.'],
      [tag(Y, '#04101f', 'DLY'), 'Same thing, in the scrolling ticker tape.']
    ];
    return sec('How I trade overnight (quick start)', '<ol class="lg-steps">' +
      '<li><b>Futures first.</b> Look at ES, NQ and YM in the ticker tape (after the yellow FUTURES label): their change <b>vs. close</b> tells you how the whole market is leaning.</li>' +
      '<li><b>Overnight movers.</b> On the IBKR board, check the <b>VS 4PM CLOSE</b> and <b>OVERNIGHT</b> boxes, or pick <b>moving overnight ±1%</b> in the US EQUITIES menu. The Scanner\'s Overnight chip and Picks show more.</li>' +
      '<li><b>News since 4 pm.</b> On the News tab press <b>Since 4 pm close</b> to see what is behind the moves.</li>' +
      '<li><b>Set alerts.</b> Alerts button: a price level or a % move vs. close. Your phone gets a push even with the board closed.</li>' +
      '<li><b>Before the open.</b> Read the 7:00 and 9:00 am Morning cards, and watch the O/N range bar: a dot outside the overnight high or low is a breakout into the open.</li></ol>') +
    sec('Price colours', rows([
      [txt(G, '123.45') + ' ' + txt(R, '123.45'), 'The <b>price number</b>: green = up, red = down, over the <b>last 1 minute</b>.'],
      [tint(G) + tint(R), 'The <b>tile background tint and left edge</b>: green = up, red = down, over the <b>last 2 minutes</b>.'],
      ['<span class="lg-flash"></span>', 'A quick green or red <b>flash</b> = the price just changed.']
    ])) +
    sec('Change boxes', rows([
      [box('#04101f', '#5a4a10', '#ffc400', 'VS 4PM CLOSE'), 'Change since the last <b>4:00 pm ET close</b>. Includes after-hours, overnight and pre-market, so before the open it is the move you will see at 9:30. This is "today\'s change" in most apps.'],
      [box('#04101f', '#5a4a10', '#ffc400', 'OVERNIGHT'), 'The move during the <b>overnight session, 8 pm to 4 am ET</b> (the most recent one; it updates live while it runs).' + (isWebull ? ' Shows n/a for stocks here while your Webull plan has no overnight prices.' : '')],
      [box('#04101f', '#5a4a10', '#ffc400', '9:30 GAP'), 'Today\'s <b>9:30 am ET opening price</b> vs. the previous 4:00 pm close. Says "at 9:30" until the market opens.'],
      ['<b style="color:#ffc400">O/N</b> L ━●━ H', 'The <b>overnight range</b>: lowest (L) and highest (H) price of that overnight session, with a dot at the price now. ' + txt(G, 'Green dot') + ' = above the overnight high, ' + txt(R, 'red dot') + ' = below the low.'],
      ['<b style="color:#9fb3c8">1W</b> / <b style="color:#9fb3c8">52W</b> L ━●━ H', 'The two small bars under O/N: the <b>1-week</b> and <b>52-week</b> range, lowest (L) and highest (H) price, with a dot at the price now. They count after-hours and overnight too (the board\'s own price record plus Yahoo\'s daily highs and lows). ' + txt(G, 'Green dot') + ' = at a new high for that range, ' + txt(R, 'red dot') + ' = at a new low. Hover (or long-press) a bar for the dates and how far the price is from the high. The 52W bar appears once Yahoo\'s year of prices arrives (a minute or two for a new symbol).'],
      ['1M 5M 15M 45M 4H 8H 24H 3D 1W 1MO', 'The % change over that much time, counted back from right now (1 minute … 1 month). <b>24H</b> = the last 24 hours, not "today" (it used to say 1D). Green = up, red = down.'],
      ['<i style="opacity:.5">…</i>', 'Not enough history yet for that window. It fills in by itself.'],
      ['List view', 'The big number on each row is the change <b>vs. the 4 pm close</b>. Tap a row to see all the boxes.'],
      ['WHY? and chips', 'Every stock tile has a <b>WHY?</b> button: a best guess at why it is moving, from its latest headlines, SEC filings, its sector, the whole market and social buzz. Small chips show up when there is something to see: ' + box('#3a2a00', '#7a5c00', '#ffc400', 'EARNINGS') + ' report day, with its typical earnings move and its move so far (solid yellow once it moves more than typical); ' + box('#2a1740', '#5b3a8a', '#d6a8ff', 'BUZZ 4x') + ' StockTwits chatter vs normal; ' + box('#0e2a3a', '#1e5a78', '#7fd6ff', '8-K') + ' a new SEC filing (tap to open it). Details on the Signals tab.'],
      ['Night chips', 'Small chips under the session boxes, only when there is something to see: ' + box('#062a14', '#145c2c', '#5dff8f', 'SPREAD 2¢ (0.05%) · 300×500') + ' IBKR board: the gap between the best bid and ask, with the shares on each side (green = tight, amber = wide, red = over 0.5%: a market-style order would cost a lot); ' + box('#0e2a3a', '#1e5a78', '#7fd6ff', 'AH PEAK +8.0% · now +3.0%') + ' after a 1%+ after-hours move: its biggest point vs the 4 pm close and where it is now (amber = it gave back much of it); ' + box('#14202e', '#2a3d52', '#9fb3c8', 'NO TRADES SINCE 2:14 am') + ' no trade for 5+ minutes while the market is open. A red bar under the header means the overnight venue looks down. The line under the session badge shows the exchanges\' overnight session (from Dec 6, 2026: 9 pm - 4 am, pause 8 - 9 pm).'],
      ['More chart buttons', '<b>Pre-mkt H/L</b> = high and low of the latest 4:00 - 9:30 am pre-market; <b>After-hrs H/L</b> = high and low of the latest 4 - 8 pm after-hours; <b>Busy night hours</b> shades 9 - 10 pm and 3 - 4 am ET, when overnight trading is busiest.'],
      ['Price chart', 'Click a tile (in List view: open a row and tap <b>CHART</b>) for a line chart of its price. Pick 1H, 4H, 1D, 1W or 1M; hover or touch the chart to read the price at any time. Green = up over that range, red = down. Shaded = outside regular hours (overnight, pre-market, after-hours); the cyan line marks each 9:30 am ET open, so the overnight move into the open is easy to see. Without Webull\'s overnight data plan the Webull board has no overnight prices (flat line 8 pm – 4 am ET); use IBKR for overnight.']
      ,['Chart indicators', 'Buttons under 1H … 1M on the price chart, all off until you tap them: <b>VWAP</b> (white dashed) = the average price paid since 4 am ET today, weighted by volume; many traders treat it as a fair price for the day. <b>MA 20 / MA 50</b> = average of the last 20 / 50 bars (1-minute bars on 1H to 1D, 5-minute on 1W, 30-minute on 1M); price above its average = trending up. <b>RSI</b> = a strip under the chart from 0 to 100; over 70 = stretched up, under 30 = stretched down. <b>Prev day H/L</b> = the last full regular session\'s high and low.']
      ,['Second chip row', 'Under the first chips, only when there is something to show: <b style="color:#5dff8f">●</b> 3x / 2x funds: trend light from the matching futures over the last 1-2 hours (green up, grey flat, red down; flipped for bear funds), keeps working overnight; <b>FAIR +0.8</b> = trading this many points above (+) or below (-) what the futures or index say it should since 4 pm (amber at 0.5+); <b>BUYERS 68%</b> / <b>SELLERS</b> = share of the last 15 minutes\' IBKR trades at the ask vs the bid; <b>PRICES DISAGREE</b> = IBKR and Webull differ by 0.75%+ (the algos skip new entries); <b>REDDIT #12</b> = ApeWisdom rank (purple when mentions doubled); <b>NOTE</b> = your note on this symbol (tap to read); <b>ⓘ</b> = the fund card (what it tracks, fees, size, top holdings).']
      ,['Why row (board math)', 'A row on each tile from the board\'s own prices and volume, no outside analysis: four arrows = the trend on 5-minute, hourly, daily and weekly bars (' + txt(G, '▲') + ' up, ' + txt(R, '▼') + ' down, ▬ flat; all four the same = a strong trend). The small bar splits the move since 4 pm into <b style="color:#7fd6ff">market</b> (the futures move times how much this symbol usually moves with them, learned over 20 days), <b style="color:#d6a8ff">sector or index</b> (for 2x/3x funds: the leverage times its index) and <b style="color:#ffc400">its own</b>. <b>VOL 3x</b> / <b>THIN</b> = volume in the last 15 minutes vs the same time on past days (overnight vs overnight). <b>↑ ON HIGH</b>-style chips = the last key level it crossed (overnight, yesterday\'s high/low/close, VWAP, 20/50/200-day averages, 52-week, opening range, round numbers). <b>HOT</b> / <b>STRETCHED</b> = a fast move or far from its average price. Tap the bar for the full explanation: breakdown, volume, levels, stretch, trend or range, who moved first, which holdings drive a fund, similar setups on this board and a confidence line. The WHY? panel shows the same under Board math, and move pushes end with a short Why line.']
      ,['Options lines (QQQ, SPY)', 'Dotted lines on the QQQ and SPY charts from Yahoo\'s free options data: <span style="color:#7fd6ff">options magnet</span> = a price the market tends to stick to, <span style="color:#ff8a80">options fast</span> = where moves can speed up, <span style="color:#ffb648">gamma flip</span> = above it moves tend to calm, below they tend to speed up.']
      ,['<span style="color:#6f8fb0">blue-grey line</span>', 'On the chart: prices the board filled in from IBKR\'s history after the Mac or the board was off or offline, so there are no holes. Its own recorded prices stay green / red.']
    ])) +
    sec('Tags and messages', rows(tags.concat([
      [box('#2a0a0a', '#5a1a1a', '#ff8a80', 'red box'), 'A real problem for the whole board. It says which command to run on the Mac.'],
      ['<span style="color:#ff8a80">harmless: IBKR was slow… (366)</span>', 'IBKR took too long to send a stock\'s chart history, so the board stopped waiting and asks again later. IBKR then replies that the old request is gone. Nothing is wrong: live prices keep coming, and the note clears by itself.'],
      ['<span style="color:#ff8a80">red bottom line</span>', 'A problem with that one symbol (e.g. unknown symbol).']
    ]))) +
    sec('Bottom line of a tile', rows([
      [isWebull ? 'webull · live' : 'ibkr · live', 'Live price from ' + (isWebull ? 'Webull' : 'IBKR') + '.'],
      isWebull ? ['webull · overnight', 'Price from Webull\'s overnight session.'] : ['ibkr · delayed ~15m', 'Free delayed price (see DELAYED above).'],
      ['3s ago', 'How old the last price is.']
    ])) +
    sec('Top of the board', rows([
      ['Ticker tape', 'Every symbol A–Z with its price, its 1-minute change, and its change <b>vs. the 4 pm close</b>. Futures are grouped at the end after the yellow FUTURES label.' + (isWebull ? ' ES, NQ and YM come from the IBKR feed, since Webull has no futures data.' : '')],
      ['Session badge', 'Under the clock: which market session it is in New York (overnight, pre-market, regular hours, after-hours, closed) and how long until the next one. Market holidays aren\'t known, so on a holiday it shows a normal day.'],
      ['A–Z order', 'Stocks and futures are listed alphabetically.'],
      ['<b style="color:#ff8bd1">HELD</b> 10 sh @ $220.50 · now $232.80 · +$123.00 (+5.58%)', 'Only on symbols you hold (Portfolio tab), in one line: your shares, your average cost, the price now, and your gain or loss right now in $ and %.'],
      ['Volume bars', 'In the price chart: grey bars along the bottom = how many shares (or contracts) traded in each slice of time, as recorded by the Mac since this update. Tall bars overnight = unusual overnight interest.'],
      ['Chart extras', 'In the price chart: ' + txt(Y, 'yellow dotted') + ' = last 4 pm close; ' + txt('#b39dff', 'purple dotted') + ' = overnight high and low; ' + txt(Y, '▼') + ' along the top = a news headline (point at it to read it). <b>Compare</b> (top right) draws a second symbol (e.g. SPY or ES=F) as a dashed blue line scaled to the same % move, so you can see if a move is the stock or the whole market.'],
      ['Alerts', 'Button next to List. Set "goes above", "goes below" (a price) or "moves ± % vs. 4 pm close". The spare Mac checks every 2 seconds, day and night, even with every page closed. A hit pops up on the board and is pushed to your phone through the free <b>ntfy</b> app (the panel shows your private topic and a Send a test button). Above/below alerts fire once; % alerts fire once per day and re-arm after each close.'],
      [box('#0d1606', '#5a4a10', '#ffc400', 'MORNING CARD'), 'Made at <b>7:00 am and 9:00 am ET</b> on weekdays and sent to your phone: futures, the biggest moves vs. the 4 pm close, and which symbols are above their overnight high or below their overnight low. Shown at the top of the board until 10:30 am. <b>Copy for Claude</b> copies it as text; × hides it. "Make morning card now" in Alerts makes one any time.'],
      (isWebull ? ['Faded tile overnight', 'While your Webull plan has no overnight prices, from 8 pm to 4 am ET stock tiles fade and say "no overnight prices on Webull": the price is frozen, not quiet. Use the IBKR tab overnight. Overnight data right now: <b class="lg-ovn">Checking…</b> (asked again every 3 hours and at 7:45 pm ET: once the plan has it, the tiles show overnight prices by themselves).'] : ['Overnight prices', 'IBKR streams stocks overnight too (8 pm – 4 am ET), so this tab is the one to watch at night.']),
      [dot(G) + ' equities / futures', 'Prices are arriving; shows when the last one came in.'],
      [dot(R) + ' equities / futures', 'Nothing new for 45+ seconds, or the connection dropped.']
    ])) +
    sec('Controls', rows([
      ['Add symbol', 'Type a stock (AAPL) or a future (ES=F, NQ=F, YM=F) and press Add.'],
      ['×', 'Removes that symbol.'],
      ['List / Tiles', 'Switches between big tiles and a compact list.'],
      ['US EQUITIES ▾ all', 'The small menu in the US EQUITIES heading shows only some of your stocks: <b>holding</b> (in your Portfolio), <b>with alerts</b> (an alert not yet hit), or <b>moving overnight ±1%</b> (moved at least 1% in the overnight session). The count then reads e.g. "3 of 12". Nothing is removed; <b>all</b> shows everything again.'],
      ['Connection', 'Leave it blank. Only for unusual direct connections.'],
      ['SELL ALL', 'Red button next to Connection. Opens a window showing the account (paper or <b>LIVE</b>), every position and open order, and how it would sell right now. Nothing happens until you type SELL ALL and press the red button. Then: pauses this broker\'s algo, cancels every open order (also ones from the broker\'s app), and closes every stock position (sells longs, buys back shorts; IBKR futures too). Regular hours: market orders. Pre-market, after-hours and overnight: brokers refuse market orders, so it sends a limit 1% through the last price (fills at the best price available), then 2%, then 3% if not filled in 20 seconds. Markets closed: it only cancels and pauses. Options are left alone. It never sends a new try until the broker confirms the last one is cancelled, and a problem with one position doesn\'t stop the others. A running live algo leaves the selling to SELL ALL (it checks the real position first, so nothing is sold twice). A phone push says what happened.'],
      ['Your list', 'Shared by all your devices. The Mac records prices around the clock.']
    ])) + TABDOTS;
  }

  function algo(name) {
    return sec('Status', rows([
      [tag('#164060', '#f2f2f2', 'PAPER'), 'Simulated trading on the Mac. Nothing is sent to ' + name + '.'],
      [tag(R, '#fff', 'LIVE'), 'Real orders, real money.'],
      [box('#2a0a0a', '#5a1a1a', '#ff8a80', 'LIVE TRADING PAUSED'), 'The robot stopped itself because it couldn\'t confirm an order. Check the ' + name + ' app before doing anything.'],
      ['Feed', txt(G, '● connected') + ' = live prices are coming in. ' + txt(FLAG, 'Yellow') + ' = connected, but read the note after it. ' + txt(R, 'Red') + ' = no prices; the message says why.'],
      ['Session now', 'Which market session it is (regular, pre-market, after-hours, overnight, closed).'],
      ['Position / P&L', 'What the robot holds and how it\'s doing. ' + txt(G, 'Green') + ' = gain, ' + txt(R, 'red') + ' = loss.'],
      ['Last order', '"unfilled" means a limit order wasn\'t filled in time and was cancelled. Not an error.'],
      ['Last backtest', 'Results of the latest test on past prices: total return, worst drop (max drawdown), win rate and an equity line chart.'],
      ['Run backtest', 'Under Last backtest: pick a strategy file and press <b>Run backtest</b>. The Mac tests it on the last 10 days of 1-minute prices from ' + name + ' (no Terminal needed) and the results replace Last backtest, usually within a minute or two. It uses the Paper &amp; backtest sessions below. One at a time.'],
      ['Use This Strategy', 'Next to Run backtest: the paper algo runs the strategy picked in the list from now on (it restarts, about 30 seconds; the Strategy line updates). It asks first and says if you have not backtested that strategy yet. Not while the paper algo holds a position (press Stop algo first) or while ./jb live runs. Paper only: going live is still ./jb live. Plain English: write it in Algo notes, Copy for Claude: Build It, paste the result, Run backtest, then Use This Strategy.'],
      [tag('#5a1010', '#fff', 'Stop &amp; close position'), 'The kill switch. The robot closes its position at once (paper: at the last price; live: with an exit order, only in a session ticked for Live) and opens nothing new. It stays stopped, even after a restart, until you press <b>Resume trading</b>.'],
      ['Paper trading report', 'One line per finished trade: when it closed (New York time), the sessions it was bought and sold in (e.g. after-hrs → regular), shares, entry and exit price, profit or loss in $ and %, and the running total. Grouped by day with each day\'s total; the line chart is the running total. Use it to judge a strategy before ever going live.']
    ].concat(name === 'IBKR' ? [['Delayed prices', 'If IBKR only sends delayed prices for the symbol, the robot won\'t trade and says so here.']] :
      [['Overnight Data (Webull)', 'Right now: <b class="lg-ovn">Checking…</b><br>Without Webull\'s overnight data plan (8 pm – 4 am ET), the Feed line says so in yellow and the robot never trades overnight, even if overnight is ticked below; day, pre-market and after-hours still work. The note line under the trades says <b>Overnight Prices (Webull): On / Off</b>. The algo, the Webull board and the scanner ask Webull again every 3 hours and at 7:45 pm ET, so once the plan has it, overnight turns on by itself (no <code>./jb restart</code> needed; a restart makes it immediate). Then the robot trades overnight when ticked below, its backup stop is tried in Webull\'s night session (a day stop if Webull refuses), and Auto-Hedge hedges overnight if Webull takes the fund then.'],
       ['Overnight Gap (Webull)', 'A Webull copy of the IBKR overnight gap strategy: <code>webull-algo/strategies/overnight_gap.yaml</code>. Backtest it here on Webull data; it needs overnight prices to watch its stop-loss at night.']]))) +
    sec('Go Live (Real Money)', rows([
      ['Live Trading Card', 'Under the top two cards of each algo tab. It starts live trading for this broker without Terminal, as a background job on the Mac (like the other parts of the board), with the strategy the paper algo runs.'],
      ['Before You Can Press Go Live', 'A board password (JB_PASSWORD in settings.env); the strategy file must say <b>confirm_live: true</b> (you add that line yourself) and have a <b>risk: max_order_notional_usd</b>; and you type <b>I_UNDERSTAND_THE_RISK</b> in the box. The card shows the <b>Go-Live Checklist</b> (the same checks as <code>./jb live</code>): you can still go live with !! lines, but it is safer to fix them first.'],
      ['Go Live', 'Asks once more, then starts. The paper algo stops while live runs. The live trader first runs its own start-up checks, exactly like <code>./jb live</code>: an order or Auto-Hedge order still open at the broker, or a position that differs from its record, stops it, and the card shows why (Last Live Run: Refused To Start, with its log lines). Refused while live trading already runs (Go Live or <code>./jb live</code>).'],
      [box('#5a0a0a', '#ff3b30', '#fff', 'LIVE'), 'While it runs: a red LIVE banner at the top of the algo tab and of the Phone page, and LIVE on the tab button. Your phone gets a loud push if it stops on its own (crash, power cut), the same as for <code>./jb live</code>.'],
      ['Stop Live', 'Ends live trading cleanly: orders still working are cancelled, no order is sent by stopping, and the position stays open at the broker (the card shows it as Live Position Left Open). The paper algo starts again. A live run in a Terminal window is stopped there with Ctrl+C.'],
      ['After A Mac Restart', 'Live trading does NOT start again by itself after a restart or power cut, unless you tick <b>Start Live Trading Again After A Mac Restart</b> (off by default; only while it was running when the Mac went down).'],
      ['./jb live Still Works', 'The Terminal way (<code>./jb live webull strategies/x.yaml</code>) works as before, as a backup. Only one live run per broker at a time.']
    ])) +
    (name === 'IBKR' ? sec('Overnight gap strategy (IBKR)', rows([
      ['The idea', 'In the last 30 minutes of after-hours (7:30 – 8:00 pm ET), Monday to Thursday and never on the evening before a market holiday, if the stock is still up at least 0.5% vs. the 4 pm close, buy (25% of the paper account). Hold overnight and sell at the 9:30 open.'],
      ['Safety', 'Sells early if it falls 2% below the buy price (stop-loss) or rises 4% above it (take-profit), checked all night. Paper only: it never sends a real order.'],
      ['Sessions', 'It needs Pre-market, After-hours and Overnight ticked in the <b>Paper &amp; backtest</b> column below (its strategy file ticks them unless this panel was saved).'],
      ['Change it', 'The file is <code>ibkr-algo/strategies/overnight_gap.yaml</code> (symbol, 0.5%, 2%, 4%). Ask Claude to change it for you.']
    ])) : '') +
    sec('Trading options', rows([
      ['Sessions', 'Regular hours (9:30 am – 4:00 pm ET) are always on. Tick pre-market, after-hours and overnight separately for <b>Paper &amp; backtest</b> and for <b>Live</b>.'],
      ['Order type', 'Limit (default) or Market. Market only 9:30 am – 4:00 pm; otherwise a limit order is sent.'],
      ['Limit price', 'How far from the last price (default 0.05%) so it fills quickly.'],
      ['Cancel after', 'Seconds to wait (default 60) before cancelling an unfilled order.'],
      ['Trailing stop', 'Optional: exit when price falls this % from its best. Only works while the robot runs.'],
      ['Extra cost', 'Paper/backtest only: extra cost per fill outside regular hours (default 0.1%).'],
      ['Save', 'The robot picks changes up within about 5 seconds.'],
      ['Use the strategy file\'s settings', 'Clears this panel so the strategy file\'s settings apply again.']
    ])) + TABDOTS;
  }

  function scanner() {
    return sec('Mode', rows([
      ['ETFs | Stocks', 'Which list is scanned: your ETF list, or 30 heavily traded stocks. Switching restarts the scanner; it\'s back in about 10 seconds and remembers your choice.'],
      ['Flag levels', 'ETFs flag at a 0.5% move in 5 minutes (1% in 15). Stocks swing more, so they flag at 1% (2% in 15).']
      /*picks*/,
      ['Movers | Picks', '<b>Movers</b> = the live table and recent flags (the last few minutes). <b>Picks</b> = research ideas from each symbol\'s daily chart. The number on Picks is how many there are now.'],
      ['Vs close', 'New column: change since the last 4:00 pm ET close, including after-hours, overnight and pre-market. Click the heading to sort by the biggest moves.'],
      ['All sessions · Overnight · Pre-market · Regular · After-hours', 'Above Recent flags: show only the flags from one session. Nothing is removed; All shows everything as before.'],
      ['volume 3.1× this session · 1.2× the last hour', 'Under a flag: its volume compared two ways. <b>This session</b> = vs. earlier 5-minute windows from the same session (fair overnight, when trading is thin). <b>Last hour</b> = vs. the 12 windows just before it, whatever session (right after 9:30 this compares with quiet pre-market, so it looks big). Hover it for the same reminder.'],
    ])) +
    sec('Picks (research ideas, not advice)', rows([
      ['What they are', 'ETFs or stocks whose daily chart shows well-known technical signals, worked out by simple rules on the Mac (no outside AI service). Ideas to look into, never buy or sell advice.'],
      [txt(G, '● Live'), 'While the market is open, picks are rechecked every few seconds using live prices, so a signal can appear or vanish during the day. A year of daily history is reloaded every 5 minutes. When closed, they use the last close.'],
      [txt(G, '▲ Leaning up') + '<br>' + txt(R, '▼ Leaning down'), 'Which way most of its signals point. Coloured left edge on the card, same meaning.'],
      [txt(FLAG, '◆ Mixed') + '<br>' + txt(FLAG, '● Heads-up'), 'Mixed = signals point both ways. Heads-up = only no-direction signals (like RSI over 70 or a squeeze).'],
      ['Several signals agree', 'Shown first. Signals come in 5 kinds (the small tags): price action, trend, momentum, volatility and volume. Different kinds agreeing is a stronger pattern than one kind repeating itself.'],
      ['Daily | 5 min | 1 min', 'Top left of Picks: which chart the picks look at. <b>Daily</b> = the daily chart (below). <b>5 min</b> and <b>1 min</b> = the same idea on 5-minute or 1-minute bars, rechecked every few seconds in <b>every session, overnight included</b>: new 20-bar high or low, an unusually big bar, EMA 9/21 cross, crossing the session VWAP (the volume-weighted average price), RSI crossing 50 or stretched (under 25 / over 75), MACD cross, a close outside the Bollinger Bands, a squeeze (quietest in 100 bars), and a volume burst (3x normal). The % next to the price is vs. the 4 pm close. Minute signals come and go fast and fail often: ideas to look at, not trades.'],
      ['Copy for Claude', 'Copies all the picks as text. Paste it into the Claude app and ask about them (news, risks, what would confirm the pattern). Free; nothing is sent anywhere by itself.'],
      ['Overnight setups', 'Outside regular hours, picks also check where the price is now: <b>Trading above / below the last day\'s high / low</b> (on course to gap at the open), <b>Holding its overnight gain / drop</b> and <b>Fading its overnight gain / drop</b> (in pre-market). The purple line on a card shows the price now and its change vs. the close.'],
      ['📅 Next earnings', 'When the company reports next, from Nasdaq\'s free earnings calendar, refreshed every 6 hours. Reporting today or tomorrow also adds an <b>Earnings</b> signal. <b>Upcoming earnings</b> at the top lists every scanned symbol reporting in the next 3 weeks.'],
      ['📰 News in the last hour', 'The newest headline from the News tab, if one came out in the last hour.'],
      ['Track record', 'At 3:55 pm each weekday the picks are saved, and the next day each up/down pick is checked: did it move the way it leaned by the next close? Shows the hit rate and the best kinds of signal. It needs a few weeks of history to mean much.'],
      ['Breakout / Breakdown', 'Price action. Price went above its highest (or below its lowest) price of the last 20 trading days.'],
      ['52-week high / low', 'Price action. Highest (or lowest) price in a year.'],
      ['Gap up / down', 'Price action. Opened at least 1% (ETFs) or 2% (stocks) away from the previous close, usually after news.'],
      ['Big move (σ)', 'Price action. Today\'s move is at least 2.5 standard deviations (σ) of its normal daily moves: rare for that symbol.'],
      ['20 / 50-day average crossed', 'Trend. SMA = the average close over that many days. Short crossing above long = trend turning up; below = turning down.'],
      ['Golden / Death cross', 'Trend. The same for the 50- and 200-day averages: the best-known long-term trend signal.'],
      ['Crossed the 200-day', 'Trend. Price crossed its 200-day average, the line many investors use for the long-term trend.'],
      ['Strong trend (ADX)', 'Trend. ADX over 25 and rising = a strong trend, in the direction shown.'],
      ['RSI 50 cross', 'Momentum. RSI (0–100) measures how hard it has moved lately. Crossing above 50 = momentum turning up; below = down.'],
      [txt(FLAG, '● Oversold / Overbought'), 'Momentum. RSI under 30 = fallen a lot fast; over 70 = risen a lot fast. A heads-up, not a direction.'],
      ['MACD cross', 'Momentum. MACD (the gap between 12- and 26-day averages) crossing its 9-day signal line: momentum turning.'],
      ['Stochastic cross', 'Momentum. Where today\'s close sits in its 14-day range (0–100). A cross up from under 20 or down from over 80 = a possible turn.'],
      ['StochRSI', 'Momentum. The stochastic of RSI: quicker and jumpier. Turning up from under 20 or down from over 80.'],
      ['CCI ±100', 'Momentum. How far price is from its 20-day typical level, in units of its normal wobble. Over +100 = unusually strong; under −100 = unusually weak.'],
      ['Bollinger Band / z-score', 'Volatility. Bands 2 standard deviations around the 20-day average. z = how many standard deviations price is from the average; outside a band is unusual.'],
      [txt(FLAG, '● Squeeze'), 'Volatility. The bands are the narrowest in about 6 months: a quiet spell that often comes before a big move. A heads-up until it breaks out.'],
      ['Squeeze breakout', 'Volatility. After a squeeze, price broke out of the bands, in the direction shown.'],
      ['Unusual volume', 'Volume. At least 1.5× its normal daily shares traded. It backs up that day\'s move.'],
      ['Money flow (CMF)', 'Volume. Chaikin Money Flow: whether shares traded over 20 days mostly closed near the day\'s high (buying) or low (selling).'],
      ['What this means', 'The plain-English explanation of a signal, shown the first time it appears in the list.'],
      ['Bigger picture', 'Context, not counted: the long-term trend, whether it passes the Minervini trend template (a checklist for a healthy uptrend), and how it did vs the S&P 500 (SPY) over 3 months.'],
      [txt(FLAG, 'Yellow note'), 'That ETF behaves differently (inverse, leveraged or volatility), so read its signals with care.']/*picks-end*/
    ])) +
    sec('Why they are moving (board math)', rows([
      ['WHY line and filters', 'Above the table: one line saying what most movers have in common right now (for example: most chip names tonight are moving with Nasdaq futures, on light volume). The buttons below it show only the rows with that tag: <b>Own move</b>, <b>Heavy volume</b>, <b>Breakouts</b>, <b>Market-led</b>, <b>Sector-led</b>, <b>Stretched</b>, <b>Trending</b>. All shows everything. Worked out from the scanner\'s own 5-minute windows and the board\'s price record, no outside commentary.'],
      [tag('#d6a8ff', '#1c1430', 'MARKET-LED') + ' ' + tag('#d6a8ff', '#1c1430', 'SECTOR-LED') + ' ' + tag('#ffc400', '#2e2400', 'OWN MOVE'), 'After the symbol: what explains most of its move since the 4 pm close. Market = the futures move times how much it usually moves with them; sector = its sector fund, or the index for a 2x/3x fund; own = what is left (news, flows). Tap a tag for the full why line.'],
      [tag('#9fb3c8', '#14202e', 'LAGS INDEX') + ' ' + tag('#ffc400', '#2e2400', 'AHEAD OF INDEX'), 'Moving less (or more) than it should: a 3x fund moving under about 2.25x its index, or a stock barely following a market move.'],
      [tag('#5dff8f', '#062a14', 'HEAVY VOL') + ' ' + tag('#9fb3c8', '#14202e', 'THIN'), 'The last 15 minutes\' volume is 2x (or under half) the same session\'s normal. Thin moves reverse more easily.'],
      [tag('#5dff8f', '#062a14', 'BREAKOUT') + ' ' + tag('#ff7a8a', '#2a0c12', 'BREAKDOWN'), 'Above this session\'s high (or below its low) so far.'],
      [tag('#ffc400', '#2e2400', 'STRETCHED'), 'Far from this session\'s average price paid (VWAP) for this symbol: moves this far often pause or snap back.'],
      [tag('#5dff8f', '#062a14', 'TRENDING UP') + ' ' + tag('#ff7a8a', '#2a0c12', 'TRENDING DOWN'), 'A steady one-way hour, with little back-and-forth.']
    ])) +
    sec('Table', rows([
      ['Symbol / Last', 'The ETF or stock and its latest price.'],
      ['Window %', 'Its % change in the current scan window. ' + txt(G, 'Green') + ' = up, ' + txt(R, 'red') + ' = down. Sorted biggest move first.'],
      ['Window vol', 'Shares traded in the current window.'],
      ['Baseline', 'How many past windows it compares against. Flags start once there are a few.'],
      ['Feed / Updated', 'Where the price came from and when.'],
      ['Float / Rel vol', 'Added by the Night desk, one column at the right (scroll sideways on a small screen): <b>Float</b> = shares that trade freely (small floats move faster; hover for how much is sold short). <b>Rel vol</b> = volume so far this session vs the same time on the last ~9 days (green = 2x or more). Tags after the symbol say why it may be moving (EARNINGS, ANALYST, 8-K, SECTOR, NO NEWS...); GAP 4/5 = passes 4 of the 5 pre-market gapper checks.'],
      ['<span class="lg-tint" style="border-left-color:' + FLAG + ';background:' + FLAG + '22"></span> ' + tag(FLAG, '#241800', 'FLAG'), 'That symbol made an unusual move in the last 5 minutes.'],
      ['Column headings', 'Click to sort; click again to flip the order.']
    ])) +
    sec('Recent flags', rows([
      ['O H L C · vol', 'Open, high, low and close of the flagged window, and its volume.'],
      [txt('#00e5ff', 'price delta'), 'A big price jump compared with normal.'],
      [txt(FLAG, 'volume surge'), 'Far more shares traded than normal.'],
      [txt('#c792ea', 'range expansion'), 'The high-to-low range is much wider than normal.'],
      [txt('#7fd1b9', 'cumulative delta'), 'Strong one-sided buying or selling pressure.'],
      [txt('#5dff8f', 'breakout volume'), 'Closed past this session\'s high or low with at least 2x the normal volume behind it.'],
      [txt('#7fd6ff', 'vwap cross'), 'Crossed the session\'s average price paid (VWAP) after at least 15 minutes on the other side: reclaimed it or lost it.'],
      [txt('#5dff8f', 'squeeze release'), 'After 30 minutes of unusually narrow windows, it broke out of that box.'],
      [txt('#ffc400', 'high on falling volume'), 'A new 1-hour high (or low) while volume keeps falling: buyers (or sellers) thinning. A warning, not a reason to chase.'],
      [txt('#7fd6ff', 'three strong'), '3 windows in a row the same way, each a solid move: a steady push.'],
      [txt('#ffc400', 'fade'), 'Gave back more than half of a big move from the last 30 minutes.']
    ])) +
    sec('Top line', rows([
      [txt(G, '● live'), 'Connected to the scanner.'],
      [txt(R, '● scanner offline'), 'Not connected; it retries by itself. If it stays red: <code>./jb restart scanner</code>.']
    ])) + TABDOTS;
  }

  function news() {   // News tab (added by add-news.py)
    return sec('What this page is', rows([
      ['Headlines', 'Recent news about every symbol on the board: both watchlists, the scanner\'s ETFs and stocks, and the two algos\' symbols. Newest first, last 3 days.'],
      ['Where from', 'Free public news feeds, no account needed. Stocks and ETFs: <b>Yahoo Finance</b>. Futures: a <b>Google News</b> search for the market behind it (e.g. ES=F = S&amp;P 500 futures, CL=F = crude oil).'],
      ['How fresh', 'The Mac re-checks each symbol about every 15 minutes, one at a time. The page updates by itself every 2 minutes.'],
      ['Not advice', 'Headlines only. Nothing here buys or sells, and the algos don\'t read it.'],
      [txt('#d6a8ff', 'ANALYSIS'), 'Analysis articles: opinion and analysis from Nasdaq.com\'s free feed (Zacks, Motley Fool and others) for your 2x/3x funds, the 5 biggest stocks inside each, and what you hold. <b>about NVDA (in SOXL)</b> = the article is about a stock inside that fund. The <b>Analysis</b> button shows only these; <b>Analysis: my 3x funds</b> only the ones linked to your 2x/3x funds. Each feed is checked every 3 hours.']
    ])) +
    sec('Tags and filters', rows([
      [txt('#00e5ff', 'ES=F') + ' ' + txt(G, 'AAPL') + ' ' + txt(FLAG, 'SPY'), 'Which symbol the story is about. ' + txt('#00e5ff', 'Blue') + ' = future, ' + txt(G, 'green') + ' = stock, ' + txt(FLAG, 'orange') + ' = ETF. Tap one to show only that symbol.'],
      ['All · Futures · Stocks · ETFs · Algos', 'Show one group. The small number is how many headlines it has.'],
      ['Every symbol ▾', 'Pick one symbol from the list.'],
      [txt(Y, 'Since 4 pm close'), 'Only stories published after the last 4:00 pm ET close: the news behind overnight moves. Tap again to show everything.'],
      ['<b style="color:#ffc400">earnings</b>', 'Words that often move a stock (earnings, guidance, FDA, downgrade, merger…) are in bold yellow.'],
      ['12m · 3h · 2d', 'How long ago the story was published.'],
      ['Top line', txt(G, '●') + ' = working; how many symbols were checked and when. ' + txt(R, '●') + ' = can\'t reach the board: <code>./jb restart gateway</code>.']
    ])) + TABDOTS;
  }

  function portfolio() {   // Portfolio tab (added by add-portfolio.py)
    return sec('Your holdings', rows([
      ['Add a trade', 'Type the date, Buy or Sell, the symbol, how many shares and the price you paid (or got), an optional note, then Add. Do this each day you trade. It is only a record on the Mac: nothing is sent to a broker.'],
      ['Positions', 'What you hold now: shares, <b>average cost</b> (buys raise it; sells lower the share count at the same average cost), the price now, the value (shares × price now), and your gain or loss in $ and %. ' + txt(G, 'Green') + ' = gain, ' + txt(R, 'red') + ' = loss. "Price now" comes from the boards, so a symbol needs to be on the IBKR or Webull board.'],
      ['Realized', 'Gain or loss already locked in by sells.'],
      ['Trades', 'Every trade you entered, newest first. × deletes one (typo fixes).'],
      ['On the boards', 'A tile for a symbol you hold shows a <b>HELD</b> line: shares, average cost, and gain/loss now. The price chart draws your average cost as a ' + txt('#ff8bd1', 'pink dotted line') + ' and each buy ▲ / sell ▼ you entered.']
    ])) +
    sec('Backtest your basket', rows([
      ['What it does', 'Shows how your current holdings (same share counts) would have done if you had held them through the last 1 month … 2 years, day by day, as a line chart. The dashed line is the S&amp;P 500 (SPY) with the same starting value.'],
      ['Type a basket', 'The box fills in with your holdings by itself (e.g. <code>AAPL:10, NVDA:5</code>). <b>Use my holdings</b> fills it again and runs it. Change or add symbols to try a different basket.'],
      ['Numbers', '<b>Return</b> = start to end. <b>Worst drop</b> = the biggest fall from a high along the way. <b>Best / worst day</b> and how many days were up.'],
      ['Where from', 'Free daily closing prices from Yahoo Finance (adjusted for splits and dividends). Past results don\'t predict the future.']
    ])) + TABDOTS;
  }

  function recap() {   // Recap tab (added by add-recap.py)
    return sec('What this page is', rows([
      ['The video', 'A 2-3 minute narrated video of everything since the last 4 pm close, in order: ' + txt('#8a7dff', 'after-hours') + ' 4-8 pm (IBKR\'s normal SMART route), ' + txt('#00e5ff', 'overnight') + ' 8 pm-3:50 am (Blue Ocean, IBKR\'s overnight venue), ' + txt('#ffb648', 'pre-market') + ' 4-9:30 am (SMART), then where things stand.'],
      ['Each session', 'Bars = how much each stock moved in that session (' + txt(G, 'green') + ' up, ' + txt(R, 'red') + ' down). The line chart draws the biggest mover through the session; the dashed line is where it started. The voice names the biggest movers, the busiest stock, the futures, and your holdings.'],
      ['Where things stand', 'Every stock vs. the 4 pm close, which ones broke above their overnight high or below their overnight low in pre-market, your holdings\' gain or loss, and how long until the 9:30 open.'],
      ['When', 'Made every trading day at <b>8:05 am</b> New York time and sent to your phone through the ntfy app (tap it to watch). Change the time with JB_RECAP_TIME in settings.env (e.g. 7:30, or off). No recap on market holidays.'],
      ['Make one now', 'Makes one straight away covering the close until now (takes a few minutes on the Mac). Also sent to your phone.'],
      ['Where from', 'The board\'s own price record (IBKR first, Webull for symbols IBKR doesn\'t have; Webull has no overnight prices on your plan) and the volume the Mac saw. The voice is the Mac\'s built-in one. Free; nothing is sent anywhere except the phone notice. The last 14 videos are kept.']
      ,['Weekly recap', "Every Friday at <b>8:15 pm</b> New York time (JB_WEEKLY_TIME in settings.env; catches up until Sunday 6 pm if the Mac was asleep), or press <b>Make weekly one now</b>: the week's best and worst, the market weather for the week, how the Picks did by the next day's close, your own trades and the best paper algo strategy, then next week's earnings for your stocks and the big economic events. Marked WEEK in the list; the last 8 are kept."]
      ,['Market weather', "The morning recap also says the weather: each sector fund gets sunny, partly sunny, cloudy, rainy or stormy from its move since the close."]
    ])) + TABDOTS;
  }

  function world() {   // World tab (added by add-world.py)
    return sec('Overnight mood', rows([
      ['The gauge', 'One number from ' + txt(R, '-100') + ' (risk-off: sellers in charge) to ' + txt(G, '+100') + ' (risk-on: buyers in charge), from moves since the last 4 pm close. S&P and Nasdaq futures, Asia, Europe and Bitcoin push it up when they rise; the fear index (VIX), the US dollar and gold push it the other way, because they usually rise when people are nervous. Above +25 = risk-on, below -25 = risk-off, in between = mixed.'],
      ['Crowd line', 'Under the gauge (and under Futures and fear on the Phone page): <b>Fear &amp; Greed</b> = CNN\'s 0-100 crowd gauge from 7 parts (momentum, new highs vs lows, breadth, put/call, VIX, junk bonds, safe havens; tap the line for each part), with a week and a month ago. <b>AAII</b> = the share of individual investors bullish and bearish (weekly, Thursdays). <b>NAAIM</b> = how invested active fund managers are (weekly, Wednesdays; 100 = fully). An amber note (and one push) at extremes: very gloomy crowds often come near lows, very happy ones near highs. Free public numbers.'],
      ['The list under it', 'What is pushing the needle most, biggest first. \u25b2 = pushing toward risk-on, \u25bc = toward risk-off.']
    ])) + sec('Markets', rows([
      ['Market clock', dot(G) + ' open now / grey = closed, with the local time in Tokyo, Hong Kong, Shanghai, Sydney, India, London, Frankfurt and New York (plus which US session it is). Shows the next high-impact US event with a countdown.'],
      ['Overnight futures', 'S&P, Nasdaq, Dow and Russell futures plus oil, natural gas, gold, silver and copper. These trade almost all night (Sunday 6 pm to Friday 5 pm New York time, with a short break each day).'],
      ['Rates and currencies', 'The VIX (fear index), the 10-year interest rate, the dollar, the euro and the yen.'],
      ['Asia / Europe', 'The big stock indexes. Asia trades during the US evening and night; Europe opens around 3 am New York time.'],
      ['Crypto', 'Bitcoin and Ether trade 24/7, so on a weekend they are the first hint of the mood before futures open Sunday at 6 pm.'],
      ['Since 4 pm', 'Change since the last US 4 pm close (Friday 4 pm on weekends). ' + txt(G, 'green') + ' up, ' + txt(R, 'red') + ' down.'],
      ['Day', 'Change vs that market\'s own previous day close.'],
      ['Little line', 'The last 24 hours.'],
      ['Where from', 'Free, delayed prices from Yahoo Finance (about 10-20 minutes for futures and indexes), refreshed every 5 minutes. For live ES / NQ / YM use the IBKR tab.']
    ])) + sec('Economic calendar', rows([
      [tag(R, '#fff', 'HIGH') + ' ' + tag('#ffb648', '#04101f', 'MED'), 'How much the event usually moves the market. HIGH: jobs report, inflation (CPI), Fed decisions.'],
      ['Forecast / Prev', 'What economists expect, and the last number. A result far from the forecast is what moves prices. Actual numbers show when the free feed has them; otherwise check the News tab.'],
      ['Fed', 'Fed interest-rate decisions (2 pm) and Fed speeches.'],
      ['Treasury auctions', 'The US government borrowing money. Weak demand at 10-, 20- and 30-year auctions can push interest rates up and stocks down. Size = how much is being sold.'],
      ['Where from', 'Forex Factory\'s free calendar, TreasuryDirect and the Federal Reserve. No phone alerts for these.']
    ])) + sec('Added in the final ideas', rows([
      ['VIX curve warning', 'An amber line under the header when short-term VIX is above 3-month VIX (fear about the next days): selloffs tend to be bigger then. Optional push on the Night desk.'],
      ['Fed meeting odds', 'Kalshi\'s odds of a cut, hold or hike at each of the next meetings, with this week\'s change (like Bloomberg WIRP).'],
      ['Nasdaq-100 breadth', 'How many of the 100 stocks are up vs down, and how many are above their 50-day average. TQQQ up while most stocks are down = a narrow, fragile move.'],
      ['Bitcoin order book', 'From Gemini\'s free order book: bitcoin waiting to buy vs sell within 1% and 2% of the price, and the biggest walls. Heavy selling on a weekend often comes before a rough Sunday 6 pm futures open. Yahoo gives the price if Gemini does not answer.']
    ])) + sec('Sunday futures push', rows([
      ['When', 'Sunday evening, just after futures open at 6 pm New York time, your phone gets one notice: where S&P, Nasdaq and Dow futures opened vs Friday 4 pm, and the overnight mood.']
    ])) + TABDOTS;
  }

  function signals() {   // Signals tab (added by add-signals.py)
    return sec('Phone alerts', rows([
      ['How pushes read', 'Said like a person, with the same numbers: <i>Heads up: SOXL just dropped in the last 15 minutes (-3.1%), now 27.40 (overnight). Why: moving with Nasdaq futures, on heavy volume.</i> The Why part is the board\'s own math.'],
      ['Big move', 'A stock moves 3%+ vs the 4 pm close; once per session (after-hours, overnight, pre-market, regular).'],
      ['Fast move', '1.5%+ within 15 minutes; at most once an hour per stock.'],
      ['Heavy volume', 'This hour\'s volume so far is 3x the normal for the same hour (last 5 trading days, as seen by the Mac).'],
      ['Yesterday\'s high / low', 'Crossed above yesterday\'s regular-session high or below its low.'],
      ['4 am gap list', 'One push at 4 am: every stock 2%+ away from the 4 pm close.'],
      ['Buzz spike', 'StockTwits posts 3x their normal pace (needs about 6 hours to learn normal).'],
      ['SEC filings', '8-K = big company news; insider BUY = an executive or director buying with their own money; 13D = an investor owning 5%+ who may push for changes. From SEC EDGAR every 15 minutes.'],
      ['Earnings move', 'On a report day, a move bigger than the stock\'s typical earnings move (average of its last 4 reports).'],
      ['Overnight summary', 'One push at 7:30 am on trading days: overnight movers, futures, mood, today\'s events and earnings. JB_SUMMARY_TIME in settings.env changes the time.'],
      ['Switches', 'Turn each one on or off and change the numbers, then press Save. The numbers above are the starting values.']
    ])) + sec('Lists', rows([
      ['Recent signals', 'Everything that was sent, newest first.'],
      ['Social buzz', 'Posts per hour on StockTwits vs normal; bull / bear = how the last 30 posters tagged themselves. Reddit blocks free automatic reading, so it is not included.'],
      ['Earnings', 'Stocks on your boards reporting today or last night, with their typical move and the move so far.']
    ])) + TABDOTS;
  }

  function analysis() {   // Analysis tab (added by add-analysis.py)
    return sec('Sections', rows([
      ['Show', '<b>My boards + holdings</b> = stocks on the IBKR / Webull boards, the algos and your Portfolio. <b>Everything</b> adds the scanner lists.'],
      ['Gap scorecard', 'A gap = the 9:30 am open is 1%+ away from the 4 pm close. For each stock: how often past gaps like today\'s <b>held</b> (closed on the gap side), <b>filled</b> (came back to the old close during the day) and the average move after the open. Past year from Yahoo daily prices; <b>your record</b> is built by the Mac from its own prices at 4:05 pm each day.'],
      ['Session split chart', 'Its own chart (your normal charts are unchanged): one bar per session since the 4 pm close: after-hours (orange), overnight (purple), pre-market (cyan), regular (green). Right = up, left = down.'],
      ['Sector heatmap', 'Boxes colored by move (green up, red down; bigger and brighter = bigger move). Pick the session at the top. Top row: the 11 sector funds; below: your stocks grouped by sector.'],
      ['Relative strength', '<b>vs SPY / QQQ</b> = the stock\'s move minus the market\'s. <b>Beta</b> = how much it usually moves per 1% market move. <b>Own move</b> = today\'s move minus what beta predicts: the part that is about the stock itself.'],
      ['Move together', 'Correlation of daily moves over 60 trading days: 1 = always together, 0 = no link, negative = opposite. 0.7+ means holding both is close to one bigger bet. The grid covers your holdings and board stocks.'],
      ['Options', 'Yahoo\'s free delayed options for the nearest expiry. <b>Max pain</b> = the price where option buyers collect the least at expiry; prices sometimes drift toward it near expiry. <b>Put/call</b> = puts ÷ calls: above 1 cautious, below 0.7 upbeat. <b>Walls</b> = strikes with the most open contracts; can act like a ceiling (calls) or floor (puts). Type any symbol to look it up.']
    ])) + TABDOTS;
  }

  function trading() {   // My trading tab (added by add-trading.py)
    return sec('Sections', rows([
      ['Trading day', 'Starts at 8 pm ET the evening before: overnight, pre-market, regular, after-hours. Today and Daily totals use it.'],
      ['Journal', 'Your IBKR fills come in every 5 minutes and Webull every 10 (when your Webull keys allow reading orders); or add one by hand. Fills are paired first-in-first-out into finished trades. Pick a <b>tag</b> (gap play, earnings, news...) and add a note.'],
      ['By session', 'Your results split by the session you got in (or out): overnight, pre-market, regular, after-hours. Also by tag and by stock: which setups make you money.'],
      ['Daily totals', 'Each trading day\'s result, split by session. The line is your running total.'],
      ['Size calculator', 'Shares = money you\'re willing to lose ÷ (entry − stop). Also shows the position size and 1:1, 2:1, 3:1 targets.'],
      ['Loss limit', 'Phone push when today\'s closed results plus the open P&amp;L of positions opened today, plus <b>the move today</b> (since 8 pm ET last evening) of positions carried from earlier days, are down more than your limit (once a day). Sales of shares bought before the journal started (shown with – as open P&amp;L), don\'t count. Optional: pause both algos at the same time. Each paper algo can also be paused on its own limit. Paused = position closed, nothing new until Resume on its tab.'],
      ['Leaderboard', 'Every trading day at 8:30 pm ET each strategy file of both algos is run on paper over the board\'s own minute prices (35 days, overnight included). Ranked by the last 20 days; green days = days it made money.'],
      ['Scorecard', 'Profit factor (dollars won ÷ dollars lost), expectancy (what an average trade makes), average win vs loss, longest losing streak, results by weekday, and your last trades with the note the board wrote at each fill (session, VIX, futures, distance from the last price).'],
      ['Exposure and exit plans', 'Section above Daily loss limit (and a card on the Phone page). <b>Real exposure</b>: everything you hold on both brokers in index terms: $10,000 of TQQQ counts as $30,000 of Nasdaq-100, SQQQ counts as short. Amber CHECK when you hold both sides of the same index, or more than the size you set. <b>Cool-down</b>: after 2 losing trades in a row, or at half your daily loss limit, a push and an amber note on the order ticket suggest a 30-minute break. It never blocks an order. <b>Earnings moves for your ETFs</b>: when a big holding of a 2x/3x fund on your boards reports today after the close or before the next open, the options\' expected move x its weight x the leverage (also an <b>EARN</b> chip on the fund\'s tile, and a push at 3:30 pm ET).'],
      ['Exit plans', 'Named plans like <b>Overnight scalp</b>: sell a third at +2%, a third at +4%, trail the rest 2%, stop -2%, stop to break-even after +1.5%. Pick one in the order ticket\'s More section (it starts once the buy fills, and a push confirms it) or put one on what you hold. The board\'s own safety stops do the watching, so the Mac must be on. Make your own with New exit plan.'],
      ['Wash-sale watch', 'A loss sale bought back within 30 days postpones that loss for taxes (it is added to the cost of the new shares). The ticket shows a note when a buy would do that, and this section keeps the total for the year. An estimate from your journal, information only.'],
      ['Overnight price record', 'Every day the after-hours, overnight and pre-market minutes from the board\'s price record are kept for good in data/history (about 10 MB a month; the record itself still keeps 35 days). <b>Download CSV</b> exports one symbol. Free sites don\'t keep overnight prices, so this is the only copy.'],
      ['Replay', 'Replay a recorded night (4 pm to 10:30 am ET) at 10x to 900x. Buy / Short / Flat practise with 100 shares; nothing goes to a broker.']
    ])) + TABDOTS;
  }

  function health() {   // Health tab, phone page, themes (added by add-extras.py)
    return sec('Health', rows([
      ['LIVE Banner (Phone)', 'A red LIVE line at the top of the Phone page while the Webull or IBKR algo trades real money (Go Live or <code>./jb live</code>), with its symbol and position.'],
      ['Prices coming in', "When IBKR and Webull last sent the board a price. Amber = it should be coming in now but hasn't for 5 minutes; grey = that broker is closed now, so that's normal (Webull has no prices 8 pm-4 am on your plan)."],
      ['Parts', "Each part of the board on the Mac: " + dot(G) + " running, " + dot(R) + " stopped or keeps stopping, grey = off in settings.env on purpose. 'Last lines of its log' shows what it said most recently."],
      ['Restart', "Same as <code>./jb restart name</code> in Terminal: stops that part and starts it again. A price bridge takes about 30 seconds to get prices flowing. Restarting the board itself blanks the page for a few seconds."],
      ['System check', 'The box at the top of this tab and of the Phone page. <b>Run check</b> tests everything live in about 10-20 seconds (every part of the board, both brokers and IB Gateway, phone alerts and each free website the board reads) and pushes the answer to your phone: <b>ALL LIVE</b>, or what is down and how to fix it. <b>Details</b> opens the full list, a <b>Send test push</b> button, and a <b>Push me when something breaks</b> switch (checks every 5 minutes; pushes when a part stays down 10+ minutes and again when it is fixed).'],
      ['Internet drops', 'At the bottom: each time the Mac had no internet for 2+ minutes (checked every 30 s), also pushed to your phone when it is back.'],
      ['Mac guardian', 'System check also shows: power-cut restart setting, memory, the Mac clock, macOS updates that could restart it overnight, services restarting over and over (held back an hour), algos that stopped checking in, and the last nightly backup.'],
      ['Charger plugged in', 'System check row: yes when the Mac is on its charger. On battery, your phone gets a push (again at 20% and 10%, and when the charger is back), because a flat battery puts the Mac to sleep and the board goes dark.'],
      ['Accounts and protection', 'Phone page card (also on this tab): for IBKR and Webull, the account type, <b>buying power</b> and the <b>margin call risk</b> (IBKR cushion = money above the margin you need, as a % of the account: under 25% amber, under 10% red with a push). Both accounts are margin accounts; since June 4, 2026 there is no pattern day trader rule, but a margin call that isn\'t covered can limit the account to closing trades for 90 days. Below it, each position you hold: <b>AT BROKER</b> (a real stop sits at the broker and works even if the Mac is off), <b>MAC ONLY</b> (only a board safety stop, which stops working if the Mac goes down; you get a push once a day) or <b>NO STOP</b>. <b>Place at IBKR</b> sends a real stop-limit order, good until you cancel it, that works 4 am - 8 pm; your board safety stop still covers overnight. Checked every 5 minutes.'],
      ['Backup internet', 'Type your iPhone Personal Hotspot name (and password) and tick the box: if the home internet is down for 90 seconds, the Mac joins the hotspot and pushes you, then tries home Wi-Fi again every 15 minutes. <b>Test now</b> switches over and back. Your iPhone must be at home with Personal Hotspot on. Uses a little phone data.'],
      ['Overnight session check', 'Nasdaq and NYSE start their own overnight session on Sunday Dec 6, 2026. That night at 9:20 pm and 12:30 am the board checks overnight prices are flowing and IBKR still takes its OVERNIGHT route, and pushes the result. <b>Run now</b> does it any time.'],
      ['Disk space', "Free space on the Mac. Under 5 GB the price record and recap videos may stop saving."]
    ])) + sec('Phone page', rows([
      ['Restart from the page', 'When something is not running, the red line at the top of the Phone page has a <b>Restart</b> button for each part, and the red banner on the IBKR and Webull boards has one too. Same as the Health tab.'],
      ['Mac not reachable', 'Opened from the https ts.net link (add that to your Home Screen), the Phone page keeps its last copy. If the Mac can\'t be reached (asleep, power cut, internet down) you see that copy with a red <b>Mac not reachable</b> line and the time it is from, instead of a blank page. Use the IBKR or Webull app to act on a position then. (The plain 100.x address can\'t do this: phones only allow it on https.)'],
      ['Why lines', 'Under each row of Biggest moves and Your holdings, a short line from the board\'s own math (market, sector or its own move, volume, the last level crossed). Tap it for the full explanation.'],
      ['Jump chips', 'Under the search box on the Phone page: <b>Moves</b>, <b>Ticket</b>, <b>My trading</b>, <b>Alerts</b>, <b>Coming up</b>. Tap one to jump straight to that card. Nothing moved; it is just quicker to get around.'],
      ['Fold a card', 'Tap a card\'s title on the Phone page (the small ▾ in front) to fold it to one line; tap again to open it. The phone remembers. Everything starts open, and a jump chip opens a folded card.'],
      ['Desk notes', 'A card on the Phone page: a short note for what you hold and the biggest moves, written by the board from its own numbers: the move since the close and by session, where it sits in its overnight, 1-week and 52-week range, market, sector or own move, volume, the trend, earnings coming up (the stock, or a big holding of a fund) and the newest headline. The same note is at the top of the WHY? panel on the IBKR and Webull boards.'],
      ['Link boxes', 'At the bottom of the Phone page, ten equal cyan boxes: <b>Night Desk</b>, <b>Order Ticket</b> (jumps down to the ticket card), <b>IBKR Algo</b>, <b>Webull Algo</b>, <b>Full Board</b>, <b>Macro</b>, <b>Ask Claude</b>, <b>Recap Video</b>, <b>System Check</b> and <b>Health</b>.'],
      ['Phone', "The <b>Phone</b> link in the top bar opens one screen with the essentials: the mood, futures and VIX, market weather, the biggest moves on your boards (vs 4 pm and overnight), your holdings, today's trading, the latest alerts and the next big events. In Safari: Share, then Add to Home Screen."],
      ['Futures tiles', "Tap a tile under Futures and fear (S&P, Nasdaq, Dow, VIX, oil, bitcoin) for the details: price, move since the 4 pm close in % and points, since yesterday, the 4 pm close, the last 24 hours' high and low, and a 24-hour line chart with the 4 pm close as a dashed line."],
    ])) + sec('Emergency (phone page)', rows([
      ['SELL ALL IBKR / Webull', "Same as the red SELL ALL button on that broker's board: shows what it will do and asks you to type SELL ALL first."]
    ])) + sec('On a phone', rows([
      ['☰ Tabs', "Left end of the top bar (phones only): a list of every tab, so you don't have to swipe the bar to find one. The bar still swipes as before."],
      ['Wide tables', "The first column (the symbol) stays put while you swipe the rest sideways; 'swipe sideways for more →' marks those tables."],
      ['Typing', "Boxes you type in no longer make the iPhone zoom in, and small buttons are easier to tap."],
      ['Bigger buttons and print', "On a phone, the mood meters' session buttons (Open, Close, After-hrs...) and the ticket's $ buttons are taller, and small print on the Phone page, Report Card and Night desk is at least 12 pixels. The search hint is shorter so it fits."]
    ])) + sec('Look', rows([
      ['Look button', "Top right of the board and the phone page: tap it for a list of looks. Each phone or computer remembers its own choice. The looks only change colours, never what the board does."],
      ['◐ Polished', "The normal look: calmer green and red, rounded tiles with a little space between them, a clean font for labels and buttons. Prices keep the same number font."],
      ['● Pure black', "Polished on a true black background: good on an iPhone at night."],
      ['○ Clean light', "Polished in grey and white for daytime; green still means up and red down."],
      ['● Night', "Everything dim red: easy on the eyes in a dark room. Works on every page, and the phone's bottom bar stays. (Classic and Old light were taken out on Oct 9, 2026: they copied Polished and Clean light.)"],
      ['◑ Auto', "Night from 9 pm to 6 am (this phone's or computer's clock), Polished otherwise."]
    ])) + sec('Market weather', rows([
      ['Words', "Each of the 11 sector funds (XLK tech, XLE energy...) gets a word from its move since the 4 pm close: ☀️ sunny (up 1% or more), 🌤️ partly sunny, ☁️ cloudy (about flat), 🌧️ rainy, ⛈️ stormy (down 1% or more). For the week, the steps are 2.5 times wider. The recap videos read it out."]
    ])) + TABDOTS;
  }

  function night() {   // Night desk tab (added by add-night.py and later Night desk installs)
    return sec('What it is', rows([
      ['Night desk', 'Everything for trading outside regular hours, from the October 2026 ideas list. Each part shows up once it is installed. The parts sit in tabs (Tonight, Trade, ETF Edge, News & Feeds, Market Data, Alerts, Tools, Mac & Safety); the board remembers the last one you opened. The Night desk guide (PDF) explains every part in plain words.']
    ])) + sec('Session alerts', rows([
      ['Overnight high / low', 'After 9:30 am, a phone push when a stock breaks its overnight (8 pm - 4 am) high or low.'],
      ['Pre-market high / low', 'After 9:30 am, a push when a stock breaks its 4:00 - 9:30 am high or low.'],
      ['Venue looks down', 'Overnight, a push if every overnight stock stops at once while futures keep moving (the venue itself stopped), and another when prices come back.']
    ])) + sec('Busy hours and the new exchange session', rows([
      ['Busy overnight hours', 'Overnight volume peaks around 9 pm and 3 am ET. The table also counts your own fills by hour.'],
      ['Dec 6, 2026', 'Nasdaq and NYSE trade 9 pm - 4 am themselves, pause 8 - 9 pm, limit orders only, 20% price bands; trades 9 pm - midnight carry the next day\'s date. Your journal counts them toward the next trading day.']
    ])) + sec('Alerts (part 2)', rows([
      ['Trading halts', 'A push when a stock on your boards or that you hold is halted or resumes, with the reason in plain words (news pending, volatility pause, SEC suspension...). Today\'s halts are listed with HELD / ON BOARD tags. Source: Nasdaq\'s public halts feed.'],
      ['Before-bed card', 'Each evening before a trading day (7:45 pm unless you change JB_BEDTIME): what you hold, earnings tonight or before the open, big data before 10:30 am, Fed events, holidays and early closes. HEADS-UP = something touches what you hold.'],
      ['Tap to open', 'Tapping a phone alert opens that stock\'s chart on the IBKR board (or the Night desk). Uses the Mac\'s Tailscale address.'],
      ['Combo alerts', 'Two conditions that must both be true, e.g. NVDA above its overnight high AND ES=F up 0.5%. Once per session.'],
      ['Data reactions', 'How S&P / Nasdaq / Dow futures moved 1 and 5 minutes after a big US number, next to actual vs expected.'],
      ['Fed, CPI and jobs odds', 'What Kalshi\'s public prediction markets price for the next Fed decision, CPI and jobs numbers (roughly the chance of each outcome). Read only.'],
      ['Mac heartbeat', 'The Mac pings healthchecks.io every 5 minutes; if it stops, healthchecks.io pushes your phone. Needs a free healthchecks.io account: paste its ping link in the box on Night Desk > Mac & Safety.']
    ])) + sec('Market data', rows([
      ['Leveraged ETF twins', 'Each 3x or 2x bull fund next to its inverse twin and the index (or stock) behind it, with the move since the 4 pm close. An <b>off</b> chip = it is not moving about 3x (or 2x) the index right now. 3x / 2x / All buttons; add or remove pairs right there. Your boards\' live prices first (overnight too on IBKR), otherwise Yahoo every 2 minutes (nothing 8 pm - 4 am).'],
      ['Gapper checklist', 'For each stock gapping 2%+ before the open: gap 2-10%, a catalyst, pre-market volume 5x+ its usual, price $5-100, holding the top half of its pre-market range. Score out of 5.'],
      ['Short interest and float', 'Short % of float, days to cover and change vs the last report (published twice a month; checked every 3 hours, push when new). Short volume = FINRA\'s daily share of trades that were short sales vs its 10-day average.'],
      ['Unusual options', 'Option contracts trading at least 500 and twice their open interest today (new bets), every 30 minutes in market hours, with a push once per stock per day.'],
      ['Analyst ratings', 'Upgrades, downgrades and new coverage for your stocks, with a push.'],
      ['Board chips', 'SHORT 25% = a crowded short; UPGRADE / DOWNGRADE firm = a rating change in the last 2 days; UNUSUAL OPTIONS = new big option bets today; WHY: ... = catalyst tags when a stock moves 2%+.']
    ])) + sec('ETF pick of the hour', rows([
      ['What it is', 'Every hour on the hour (Sunday 8 pm to Friday 8 pm New York time) the board picks one 3x fund from the twins list by fixed rules: the index move since the 4 pm close (up to 40 points), the last hour going the same way (30), the World tab mood agreeing (20) and heavy volume (10). Under 35 = no clear pick, stay flat. Free: rules, not AI, and not advice.'],
      ['Track record', 'Each pick is graded one hour later (how much the fund moved), so you can see how often the rules are right.'],
      ['Ask Claude about this', 'Opens the Claude app with the pick and its reasons filled in, for a free second opinion.'],
      ['Push', 'Optional phone push for each pick (off until you switch it on).']
    ])) + sec('Big money', rows([
      ['Who owns it', '% held by institutions and insiders, the biggest institutions and funds and whether each added or cut in its latest quarterly 13F report (can be 45+ days old). Checked every 12 hours per stock on your boards or held.'],
      ['Insider trades', 'Officers and directors buying or selling their own stock in the last 90 days (SEC Form 4). Buys are the ones traders watch.'],
      ['Block trades', 'Single trades of $1 million or 20,000 shares or more as they print on your IBKR board, any session (JB_BLOCK_DOLLARS / JB_BLOCK_SHARES in settings.env).'],
      ['Dark pools', 'Shares traded off-exchange per week (FINRA, published 2-4 weeks late). A week far above the others can mean a fund building or selling a position.'],
      ['Politicians', 'Stock trades reported by members of Congress (official House Clerk and Senate filings, checked every 6 hours), trades in your stocks first. Reported up to 45 days late: background, not a signal.']
    ])) + sec('Earnings and trading tools', rows([
      ['Game plan', 'A note you write the evening before. Pushed to your phone at 9:00 am New York time on trading days (JB_PLAN_TIME in settings.env); also on the phone page, where you can edit it.'],
      ['Expected move', 'For stocks reporting in the next 8 days: the move the options market is pricing in (at-the-money call + put on the first expiry after the report), next to the stock\'s usual move on its last reports.'],
      ['Call clock', 'When the results and the earnings call come out, with a countdown, and a push 10 minutes before the call.'],
      ['Limit price helper', 'Bid, ask and a fair limit price for a buy or sell (a quarter of the spread past the middle), the cost for your shares, and the exchanges\' 20% overnight price band.'],
      ['Stress test', 'What your holdings would gain or lose if the S&P 500 fell 1%, 3% or 5%, or moved like the futures have since 4 pm (value × beta × move).'],
      ['Night vs day', 'A year of holding only overnight (4 pm close to 9:30 open, purple) vs only in the day (open to close, gold) for any symbol, before trading costs.'],
      ['Fill quality', 'Each of your fills vs the middle of the bid and ask at that minute (IBKR board symbols). + = better than the middle, − = you paid up.']
    ])) + sec('Order ticket', rows([
      ['Order ticket', 'Buy or sell a stock or ETF on IBKR or Webull from the Night desk or the phone page. Limit orders only, good for today, in any session (overnight: IBKR OVERNIGHT venue / Webull NIGHT session). Tap bid, ask or last to use that price. A stock or ETF that isn\'t on your boards gets its last price from Yahoo (can lag a minute, no overnight prices), and the limit may then be up to 15% away.'],
      ['Two steps', '<b>Check order</b> asks the broker for your account (REAL MONEY or paper), what you hold and the route, and sends nothing. The red <b>Send</b> button then sends exactly that order (good for 2 minutes).'],
      ['Safety', 'Refused: a limit over 5% from the last price (typing mistakes), more than JB_TICKET_MAX dollars per order (settings.env, 5000 to start), selling more than you hold, part shares. Every order sent also comes as a phone push.'],
      ['Recent tickets', 'Your last orders and their status (checked every 30 s), with a Cancel button while one is open.']
    ])) + sec('Trading and levels on the chart', rows([
      ['LEVELS button', 'Under the pop-out chart. Tap LEVELS, then tap the chart at a price to add a yellow line there (or type a price). Levels are saved on the Mac, so they stay on that symbol\'s chart on the board and the phone until you remove them.'],
      ['Level alerts', 'Tap alert off / alert on next to a level to get a phone push when the price crosses it (once, then it switches itself off; switch it on again to re-arm).'],
      ['TRADE button', 'Opens the order ticket under the chart for that symbol. Tap the chart to set the limit price at that spot, or use the -2% ... +2% and now buttons around the current price; "all I hold" fills in your whole position to sell. Same two steps and safety limits as the order ticket.'],
      ['Lines on the chart', 'Yellow = your levels (a bell = alert on), pink = what you paid, green / red = your open buy / sell orders and the limit price you are setting (labelled open ... or your ... limit).']
    ])) + sec('Web watch', rows([
      ['What it is', 'A free crawler for the stocks on your boards and the ones you hold, filling the gaps between the news feeds. Most useful for stocks; 3x ETFs rarely have their own news.'],
      ['Press releases', 'GlobeNewswire, PR Newswire and Business Wire, every 2 minutes. Companies publish here first, often minutes before the news sites.'],
      ['Reddit', 'New posts in wallstreetbets, stocks, options and StockMarket, every 3 minutes. A spike = a ticker mentioned far more this hour than its usual hour. Crowd attention, not facts.'],
      ['SEC filings', 'Every 5 minutes: filings by your companies beyond the 8-K / insider / 13D on the Signals tab (offerings like S-3 and 424B, 5% holders, late reports ...), and other companies\' 8-Ks that mention yours (deals, partners).'],
      ['Attention', 'Wikipedia page views (a day 3x the usual) every 15 minutes, and the Google Trends US trending list every 30 minutes (unofficial, best effort).'],
      ['Unexplained moves', 'A stock of yours moving 3%+ since the 4 pm close with nothing found anywhere: no headline, filing, rating, earnings, press release or spike. Be extra careful with these.'],
      ['Pushes', 'One switch per kind, all off until you turn them on. The sources table shows when each was last checked and any problem.']
    ])) + sec('IB Gateway auto-login', rows([
      ['What it does', 'IBC (free, open source) starts IB Gateway on the Mac and types your IBKR username and password. You only tap <b>Approve</b> in the IBKR Mobile app (IB Key) when IBKR asks: about once a week, and after the Mac or IB Gateway is restarted.'],
      ['Phone push', 'When IBKR is waiting for your Approve tap, the board pushes you (at most every 20 minutes while it waits), and again once it has logged in.'],
      ['Nightly restart', 'IB Gateway restarts itself at 11:50 pm New York time, during IBKR\'s own nightly reset, so you lose about a minute of prices.'],
      ['Set up / stop', 'On the Mac: <code>cd ~/josephs-board && ./jb ibkr-autologin</code> (asks your login once; the password is saved only in ibc/config.ini, readable only by your Mac account). <code>./jb ibkr-autologin off</code> stops it.'],
      ['Night desk', 'The IB Gateway auto-login section shows if it is logged in, waiting for your approval, or if the login failed.']
    ])) + sec('Orders: stops, flip, schedules', rows([
      ['Safety stop / target', 'Set on a buy in the ticket (More) or on the Night desk. The board watches the price in every session and sells when it is hit (two readings in a row, so one bad print does not sell). It is watched by the board, not placed at the broker, so the Mac must be on.'],
      ['Trailing stop / break-even', 'Trailing: the stop follows the price up by the % you pick, overnight too. Break-even: after +x% the stop moves to what you paid.'],
      ['Sell now steps', 'A stop sells a little under the price, then steps 0.5%, 1%, 2%, 3% under until it fills.'],
      ['Flip', 'Sells a 3x fund and buys its twin (TQQQ ↔ SQQQ) for the same dollars, after a check you confirm with a code.'],
      ['Chase', 'Moves an open limit order toward the price a few times (at most 1%) so it fills.'],
      ['Ticket extras', 'More: good for this session / overnight + tomorrow (IBKR) / until cancelled, split into a ladder of limits, size buttons in dollars, an IBKR preview of cost and margin, and a warning when you send the same order twice.'],
      ['Scheduled orders', 'An order at the open, 4 am, 4 pm, 8 pm or a time you pick, optionally only if conditions hold (e.g. NQ=F up 0.3%+). Paper by default. "Sell all at the open" is one of them.'],
      ['Fill push', 'A push for every fill (ticket, chart, algo, SELL ALL) with the price, how far it was from your limit or the last price, and an automatic note.'],
      ['Switches', 'Cancel old or previous-session orders, a heads-up push 10 minutes before big news (with what you hold), an optional algo pause for big news, the Sunday IB sign-in reminder. The previous-session cancel and the news heads-up start on; the algo pause starts off.']
    ])) + sec('3x ETF edge', rows([
      ['Fair value', '3x and 2x funds vs what their futures or index say they should be since 4 pm. Thin overnight trading often lags the futures.'],
      ['Rebalance and decay', 'The fund\'s end-of-day rebalance in dollars (it must buy on up days, sell on down days), and how much it lost to daily resetting over 5 and 20 days.'],
      ['Drift, opex, gamma', 'How the fund usually moves by session and weekday; options expiry days; options magnet / fast / flip levels for QQQ and SPY.'],
      ['Macro', 'Fed meeting odds (Kalshi), net liquidity (FRED), Treasury auction results (push on weak ones), CFTC futures positioning, yen and VIX pushes.'],
      ['History and fund card', 'A price history table with CSV download, and a fund card for each ETF.']
    ])) + sec('Feeds', rows([
      ['Top news', 'One ranked list from every feed, repeats folded together, your holdings first.'],
      ['IBKR newswire', 'Free Briefing.com and Dow Jones headlines. If it says not subscribed: Client Portal > Settings > Research Subscriptions, add the free BRFG, BRFUPDN and DJNL.'],
      ['Crowd and odds', 'Reddit rank (ApeWisdom), Bluesky posts (optional free account), Polymarket odds, policy posts (Truth Social / White House, best effort).'],
      ['Pressure and cross-check', 'Buy vs sell pressure from IBKR trades, and IBKR vs Webull price differences.']
    ])) + sec('Tools and reliability', rows([
      ['Alert manager', 'Every alert and switch on one page, with a test push. Urgent pushes (SELL ALL, a stop firing, the loss limit) use top priority: allow ntfy in your iPhone Focus once.'],
      ['Scorecard, notes, reports', 'The journal scorecard, your notes per symbol, and a weekly PDF report every Friday 8:30 pm.'],
      ['Lock Screen widget', 'The same Scriptable script also fits the iPhone Lock Screen (one line, round or small box): S&P, Nasdaq and Dow futures and a green, amber or red dot for the board\'s health. No profit or loss there. Copy the script again after this update.'],
      ['Menu bar (Mac)', 'A dot in the Mac\'s top menu bar (free xbar or BitBar app): green, amber or red for the board\'s health. Click it for each part of the board and each data feed (fresh or not), anything needing attention, and shortcuts to System check, Health and Restart the board.'],
      ['Lock Screen check time', 'The middle line reads IBKR Up · 8:26 PM · Webull Up: the time is when the widget last got data from the board. If the board can\'t be reached, the widget shows a large Board Down, Can\'t Reach The Board and the time the phone tried.'],  // jb-widgettime
      ['Home Screen chart backup', 'If the Yahoo prices are missing, the mini futures chart uses the board\'s own live futures prices from IBKR. Chart Waiting For Prices means there is nothing to draw yet; it fills in by itself. If the board can\'t be reached, the whole widget turns red and says Board Down with the time the phone tried. Copy the script into Scriptable again after updates.'],
      ['Weekly IBKR login', 'IBKR needs a full login with your password about once a week. The board now does it at a planned time, every Sunday at 5 PM ET (an hour before futures open): IB Gateway restarts, the auto-login types the password, and your phone gets a Weekly IBKR Login push. Tap Approve in IBKR Mobile. If IB Gateway ever says its security tokens expired, the board does the same at once (IBKR Login Expired push). Don\'t log in to IB Gateway by hand while the auto-login is on: two logins knock each other off in a loop.'],  // jb-ibweekly
      ['iPhone widget and Siri', 'Setup steps with your own link: a home-screen widget (free Scriptable app) and "Hey Siri, how\'s the board". Read-only.'],
      ['Go-live checklist', 'What should be true before an algo trades real money. ./jb live runs it first and asks you to type LIVE.'],
      ['Mac guardian', 'Checks, nightly backups (2:30 am, last 7, optional iCloud), internet drops, early loss warnings at 50% and 75%, SELL ALL re-checked after 60 s, Sunday 5 pm self-test, Saturday 7 am quiet-time refresh.'],
      ['Algo safety', 'Both algos write a heartbeat (push if one stops for 3 min) and skip new entries while IBKR and Webull disagree on the price. Paper and live: safe mode while the connection is lost and an order speed limit, so paper results match how live would behave. Live also checks the position against the broker every minute.']
    ]))/*night-more*/ + TABDOTS;
  }

  function macro() {   // MACRO tab (added by add-macro.py)
    return sec('What it is', rows([
      ['MACRO', 'The slow-moving economic backdrop for the stock market, from free official data (FRED, the St. Louis Fed): rates, inflation, jobs, growth and market stress. It changes over weeks and months: a background tailwind or headwind for your 3x funds, not a signal for tonight.']
    ])) + sec('Reading it', rows([
      ['Big number', 'Each indicator gets a score from -1 (hurts stocks) to +1 (helps) by fixed rules; the big number is their average × 100. <b>Tailwind</b> = +20 or more, <b>Headwind</b> = -20 or less, otherwise Mixed. The bars show each group.'],
      ['Indicator cards', 'Latest value, HELPS / HURTS / NEUTRAL with its score, a line chart of the last months or years, and one plain sentence on why. Daily ones (rates, VIX, credit spreads) update each evening; monthly ones (jobs, inflation) when the government publishes them.'],
      ['Score over time', 'The big number saved once a day, as a line.'],
      ['Macro news', 'The last 7 days of Fed, inflation, jobs, growth, rates and tariff headlines (Google News and the Fed\'s own releases, every 30 minutes). Green / red dot = upbeat / worried words in the headline (a simple word count).'],
      ['Ask Claude', 'Opens the Ask Claude page with the macro data ticked and a question filled in.']
    ])) + sec('Market sentiment report', rows([
      ['Generate report', 'Makes one combined report on the market\'s mood on the Mac, by fixed rules from free data the board already has: <b>Short term</b> (next 1-3 days: overnight mood meter, VIX level and move, VIX curve, last 5 days, today\'s breadth, 2 days of macro headlines), <b>Mid term</b> (next 2-8 weeks: S&amp;P and Nasdaq vs their 50-day average, last 20 days, Nasdaq-100 stocks above their 50-day, Fed odds, hedge fund positioning, a week of headlines, net liquidity) and <b>Long term</b> (next 3-12 months: the MACRO score, S&amp;P vs its 200-day average, 50-day vs 200-day, 52-week range, last 12 months, 6 months of net liquidity).'],
      ['Scores', 'Each line scores -1 (bearish) to +1 (bullish); a time frame is their average × 100; the combined score is the three averaged equally. <b>Bullish</b> +40 or more, <b>Leaning bullish</b> +15, <b>Neutral</b>, <b>Leaning bearish</b> -15, <b>Bearish</b> -40 or less. ▲▼ = change since the last report. One sentence sums up how the time frames fit together (for example a short-term dip in a long-term uptrend), one line says what it means for 3x bull and 3x inverse funds, and big events in the next 3 days are listed. Rules, not advice.'],
      ['Buttons', '<b>Copy text</b> copies the report; <b>Print / Save as PDF</b> prints just the report (in Safari\'s print window choose PDF to save it); <b>Ask Claude about this report</b> opens the Ask Claude page with the report ticked. <b>Earlier reports</b> lists the last 10 (60 kept on the Mac).']
    ])) + TABDOTS;
  }

  function claudePage() {   // Ask Claude tab (added by add-macro.py)
    return sec('What it is', rows([
      ['Ask Claude', 'Type a question; the button copies it plus the board\'s live numbers you tick and opens the Claude app (or claude.ai) with it filled in. Free: no API key. The board sends nothing; you press send in the Claude app, and it counts toward your normal Claude plan.']
    ])) + sec('Using it', rows([
      ['Board data', 'Tick what Claude should see: market now, your board symbols, 3x pairs, your positions, the ETF pick, earnings, halts and odds, MACRO, big money, web watch, headlines. <i>show what is sent</i> shows the exact text.'],
      ['Too long?', 'If it does not fit in the link, the app opens with your question and you paste the rest (already on your clipboard) as the next message.'],
      ['Quick questions', 'Ready-made questions to tap; your last 12 questions are kept on that phone or computer.'],
      ['Remember', 'Claude sees only what you send and can be wrong. Not financial advice.']
    ])) + TABDOTS;
  }

  var TABS = {
    'night': { title: 'Night desk', html: function () { return night() + recap(); } },
    'macro': { title: 'MACRO', html: macro },
    'claude': { title: 'Ask Claude', html: claudePage },
    'health': { title: 'Health', html: health },
    'trading': { title: 'My trading', html: trading },
    'analysis': { title: 'Analysis', html: analysis },
    'signals': { title: 'Signals and Health', html: function () { return signals() + health(); } },
    'world': { title: 'World', html: world },
    'recap': { title: 'Recap', html: recap },
    'portfolio': { title: 'Portfolio and My Trading', html: function () { return portfolio() + trading(); } },
    'news': { title: 'News', html: news },
    'ibkr': { title: 'IBKR board', html: function () { return priceBoard(false); } },
    'webull': { title: 'Webull board', html: function () { return priceBoard(true); } },
    'scanner': { title: 'Scanner', html: scanner },
    'algo-webull': { title: 'Webull Algo', html: function () { return algo('Webull'); } },
    'algo-ibkr': { title: 'IBKR Algo', html: function () { return algo('IBKR'); } }
  };

  // ------------------------------------------------------------------ styles
  var css =
    '#lgBtn{margin-left:auto;align-self:center;margin-bottom:6px;background:transparent;border:1px solid #164060;color:#6f8aa3;' +
      'font:inherit;font-size:12px;font-weight:600;padding:5px 10px;border-radius:6px;cursor:pointer;white-space:nowrap;flex-shrink:0;position:sticky;right:0;background:#071a30;box-shadow:-10px 0 10px #071a30;}' +
    '#lgBtn:hover{color:#f2f2f2;border-color:#6f8aa3;}' +
    '#lgWrap{position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,.55);display:none;align-items:flex-start;justify-content:center;' +
      'padding:56px 12px 12px;}' +
    '#lgWrap.open{display:flex;}' +
    '#lgPanel{background:#071a30;border:1px solid #164060;border-radius:10px;max-width:640px;width:100%;max-height:100%;overflow-y:auto;' +
      '-webkit-overflow-scrolling:touch;padding:14px 16px 18px;color:#f2f2f2;font-size:12.5px;line-height:1.45;}' +
    '#lgPanel .lg-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;}' +
    '#lgPanel h2{font-size:14px;margin:0;letter-spacing:.06em;}' +
    '#lgPanel h3{font-size:11px;color:#6f8aa3;letter-spacing:.1em;text-transform:uppercase;margin:16px 0 6px;}' +
    '#lgClose{background:transparent;border:1px solid #164060;color:#f2f2f2;font:inherit;font-size:16px;border-radius:6px;width:32px;height:32px;cursor:pointer;}' +
    '.lg-steps{margin:4px 0 10px;padding-left:20px;line-height:1.55;font-size:12px;} .lg-steps li{margin:3px 0;}' +
    '.lg-t{width:100%;border-collapse:collapse;}' +
    '.lg-t td{padding:6px 6px;border-top:1px solid #0f2c47;vertical-align:top;}' +
    '.lg-t td.lg-k{width:38%;white-space:normal;color:#cfe0ee;}' +
    '.lg-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px;}' +
    '.lg-tint{display:inline-block;width:26px;height:16px;border-left:3px solid;margin-right:6px;vertical-align:-3px;}' +
    '.lg-tag{display:inline-block;padding:1px 5px;font-size:9px;font-weight:700;letter-spacing:.5px;border-radius:2px;vertical-align:1px;}' +
    '.lg-box{display:inline-block;padding:2px 6px;border:1px solid;border-radius:4px;font-size:11px;}' +
    '.lg-flash{display:inline-block;width:26px;height:16px;vertical-align:-3px;animation:lgFlash 1.6s infinite;}' +
    '@keyframes lgFlash{0%{background:rgba(0,255,65,.45)}25%{background:rgba(0,255,65,0)}50%{background:rgba(255,59,48,.45)}75%,100%{background:rgba(255,59,48,0)}}' +
    '#lgPanel code{background:#000;padding:1px 4px;border-radius:3px;font-size:11.5px;}' +
    '#lgPanel .lg-foot{margin-top:14px;color:#6f8aa3;font-size:11px;}' +
    '@media (max-width:560px){#lgWrap{padding:44px 6px 6px;} .lg-t td.lg-k{width:42%;}}';

  // ------------------------------------------------------------------ build
  function activeTab() {
    var b = document.querySelector('#bar .tab.active');
    return b ? b.getAttribute('data-tab') : 'ibkr';
  }

  // Is Webull's overnight data on right now? (hedge-2026-10-12: /api/algo/webull overnight_data, what the Webull board,
  // scanner and algo last found)
  function fillOvernight() {
    var body = document.getElementById('lgBody');
    if (!body || !body.querySelectorAll || !window.fetch) return;
    var els = body.querySelectorAll('.lg-ovn');
    if (!els.length) return;
    fetch('/api/algo/webull', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var o = d && d.overnight_data, parts = (o && o.parts) || {}, names = { board: 'Board', scanner: 'Scanner', algo: 'Algo' }, t;
      var seen = Object.keys(parts).map(function (k) { var p = parts[k];
        return (names[k] || k) + ' ' + (p.on ? 'On' : 'Off') + (p.checked_at ? ' (Checked ' + new Date(p.checked_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + ')' : ''); });
      if (!o || o.on == null) t = txt(DIM, 'Not Known Yet') + ' (Webull Has Not Answered Since The Last Restart)';
      else t = (o.on ? txt(G, 'ON') + ': Webull Streams The Overnight Session' : txt(FLAG, 'OFF') + ': Your Webull Plan Has No Overnight Session') +
        (seen.length ? ' · ' + seen.join(' · ') : '') + ' · Checked Again Every ' + (o.recheck_hours || 3) + ' Hours And At ' + (o.recheck_et || '7:45 pm') + ' ET';
      for (var i = 0; i < els.length; i++) els[i].innerHTML = t;
    }).catch(function () {});
  }

  function init() {
    var bar = document.getElementById('bar');
    if (!bar) return;
    var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

    var btn = document.createElement('button');
    btn.id = 'lgBtn'; btn.type = 'button'; btn.textContent = '? Legend';
    btn.title = 'What the colours, tags and boxes on this tab mean';
    bar.appendChild(btn);

    var wrap = document.createElement('div'); wrap.id = 'lgWrap';
    wrap.innerHTML = '<div id="lgPanel" role="dialog" aria-modal="true" aria-labelledby="lgTitle">' +
      '<div class="lg-head"><h2 id="lgTitle"></h2><button id="lgClose" type="button" aria-label="Close">×</button></div>' +
      '<a class="lg-pdf" href="./Josephs-Board-Map-Legend.pdf" target="_blank" rel="noopener" style="display:block;margin:2px 0 10px;padding:8px 10px;border:1px solid #164060;border-radius:7px;color:#00e5ff;text-decoration:none;font-size:13px;">&#128196; Full map legend (PDF): every service, tab, setting and feature &rsaquo;</a><a class="lg-pdf" href="./Night-Desk-Guide.pdf" target="_blank" rel="noopener" style="display:block;margin:-4px 0 10px;padding:8px 10px;border:1px solid #2e2560;border-radius:7px;color:#b39dff;text-decoration:none;font-size:13px;">&#128196; Night desk guide (PDF): the Night desk, MACRO, Ask Claude, chart trading, IB Gateway auto-login, System check &rsaquo;</a><div id="lgBody"></div><div class="lg-foot">Tap outside this box or press Esc to close.</div></div>';
    document.body.appendChild(wrap);

    function open() {
      var t = TABS[activeTab()] || TABS.ibkr;
      document.getElementById('lgTitle').textContent = 'LEGEND · ' + t.title.toUpperCase();
      document.getElementById('lgBody').innerHTML = t.html();
      fillOvernight();
      wrap.classList.add('open');
      document.getElementById('lgPanel').scrollTop = 0;
    }
    function close() { wrap.classList.remove('open'); }
    btn.addEventListener('click', function () { if (wrap.classList.contains('open')) close(); else open(); });
    document.getElementById('lgClose').addEventListener('click', close);
    wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' || e.keyCode === 27) close(); });
    // switching tabs while the legend is open shows the new tab's legend
    bar.addEventListener('click', function (e) {
      var t = e.target && e.target.closest ? e.target.closest('.tab') : null;
      if (t && wrap.classList.contains('open')) setTimeout(open, 0);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
