/* Alex Hale's Board -- the one board engine behind ibkr.html and webull.html (desktop + phone).
 *
 * The symbol list lives on the bridge (the always-on Mac), so every device shows the same board.
 * Prices arrive as batched 'ticks'; history arrives per symbol on every (re)connect. All change
 * windows are computed here from samples stamped with the time they were received.
 *
 * Connection: when served by the gateway (http/https) the page connects to /ws/<broker> on the
 * same address automatically -- nothing to type. Opened as a local file, it falls back to
 * ws://localhost:<port>, and "Connection settings" lets you point it anywhere (+ a token).
 */
(function () {
  'use strict';

  var CFG = window.BOARD_CONFIG || { broker: 'ibkr' };
  var B = CFG.broker === 'webull' ? 'webull' : 'ibkr';
  if (CFG.subtitle) CFG.subtitle = CFG.subtitle.replace('1D', '24H');
  var LS = 'jb_' + B + '_';
  function lsGet(k, d) { try { var v = localStorage.getItem(LS + k); return v == null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { if (v == null || v === '') localStorage.removeItem(LS + k); else localStorage.setItem(LS + k, v); } catch (e) {} }

  var WINDOWS = [
    { key: '1m', label: '1M', ms: 60e3 }, { key: '5m', label: '5M', ms: 5 * 60e3 },
    { key: '15m', label: '15M', ms: 15 * 60e3 }, { key: '45m', label: '45M', ms: 45 * 60e3 },
    { key: '4h', label: '4H', ms: 4 * 3600e3 }, { key: '8h', label: '8H', ms: 8 * 3600e3 },
    { key: '1d', label: '24H', ms: 24 * 3600e3 }, { key: '3d', label: '3D', ms: 3 * 24 * 3600e3 },
    { key: '1w', label: '1W', ms: 7 * 24 * 3600e3 }, { key: '1mo', label: '1MO', ms: 30 * 24 * 3600e3 }
  ];
  var PRIMARY = WINDOWS[6];           // 24H rolling (the phone-row headline now shows the change since the 4 pm close)
  var ZONE_MS = 2 * 60e3;             // tile background tint = 2-minute change
  var COLOR_MS = 60e3;                // price colour = 1-minute change
  var RAW_MS = 3600e3, MIN_TIER_MS = 24 * 3600e3, MAX_KEEP_MS = 35 * 24 * 3600e3;
  var LABELS = { ibkr: 'ibkr · live', 'ibkr-delayed': 'ibkr · delayed ~15m', webull: 'webull · live', 'webull-overnight': 'webull · overnight' };

  var X = { history: {} };   // Webull board only: ES / NQ / YM from the IBKR feed, for the ticker tape
  var S = {
    symbols: [], history: {}, lastPrice: {}, lastSource: {}, dataTs: {}, lastObs: {},
    firstLive: {}, histAsked: {}, symStatus: {}, expanded: {}, gotWatchlist: false
  };
  var els = {};            // sym -> element refs for the current layout
  var layout = null;       // 'tiles' | 'rows'
  var paused = false;      // hidden dashboard tab / backgrounded page: keep recording, skip drawing

  // ------------------------------------------------------------------ page skeleton
  function h(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function isFut(s) { return /=F$/.test(s); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }

  document.body.innerHTML =
    '<div class="tape-wrap"><div class="tape" id="tape"></div></div>' +
    '<div class="cmdbar">ALEX HALE\'S BOARD · ' + esc(CFG.name || B.toUpperCase()) + ' <span class="go">&lt;GO&gt;</span></div>' +
    '<div class="wrap">' +
    '<header><div class="brand"><span class="mark">ALEX HALE\'S BOARD</span><span class="sub">' + esc(CFG.subtitle || '') + '</span></div>' +
    '<div class="clock"><div class="time" id="clockTime">--:--:--</div><div class="date" id="clockDate"></div>' +
    '<div class="sess-badge" id="sessBadge"></div>' +
    '<div class="status"><span><span class="dot idle" id="dotEq"></span><span id="statEq">equities</span></span>' +
    '<span><span class="dot idle" id="dotFu"></span><span id="statFu">futures</span></span></div></div></header>' +
    '<div class="controls">' +
    '<div class="add-row"><div><label for="addSym">Add symbol</label><input type="text" id="addSym" placeholder="AAPL or ES=F" autocapitalize="characters" autocomplete="off" spellcheck="false"></div>' +
    '<button class="btn" id="addBtn">Add</button></div><div class="spacer"></div>' +
    '<button class="btn ghost" id="alertBtn" title="Price alerts, pushed to your phone">Alerts</button>' +
    '<button class="btn ghost" id="layoutBtn" title="Switch between tiles and a compact list">List</button>' +
    '<button class="btn ghost" id="advBtn">Connection</button>' +
    '<div class="advanced" id="adv"><div><label for="bridgeUrl">Bridge address (blank = automatic)</label><input type="text" id="bridgeUrl" spellcheck="false"></div>' +
    '<div><label for="bridgeToken">Bridge token (only for a direct connection)</label><input type="password" id="bridgeToken" autocomplete="off"></div>' +
    '<button class="btn" id="saveConn">Save</button><div class="hint" id="connHint"></div></div>' +
    '</div>' +
    '<div class="alerts-panel" id="alertPanel"></div>' +
    '<div class="warn" id="warn" style="display:none"></div>' +
    '<div class="toasts" id="toasts"></div>' +
    '<div class="morning" id="morning" style="display:none"></div>' +
    '<section class="group"><div class="group-title"><span>US EQUITIES <select id="grpSel" class="grp-sel" title="Show only a group of your symbols" aria-label="Show group">' +
    '<option value="all">all</option><option value="held">holding</option><option value="alert">with alerts</option><option value="on">moving overnight ±1%</option></select></span><span class="count" id="eqCount">0</span></div><div id="eqBox"></div></section>' +
    '<section class="group"><div class="group-title"><span>FUTURES</span><span class="count" id="fuCount">0</span></div><div id="fuBox"></div></section>' +
    '<footer class="note">' + (CFG.footer || '') +
    '<ul><li><span class="dot live"></span><span class="dot stale"></span> Price number: green/red = up/down over the last 1 minute.</li>' +
    '<li>Tile tint / left edge: green/red = up/down over the last 2 minutes. A brief flash marks each price change.</li>' +
    '<li>Session boxes: VS 4PM CLOSE = change since the last 4:00 pm ET close; OVERNIGHT = the 8 pm–4 am ET move; 9:30 GAP = today\'s open vs. that close. The bar under them is the overnight low–high with a dot at the price now.</li>' +
    '<li>Change boxes 1M–1MO: that window\'s % change, counted back from right now (24H = the last 24 hours, not "today"); "…" = waiting for enough history to cover it.</li>' +
    '<li><span class="badge-delayed">DELAYED</span> No live-data subscription for this symbol, so it shows IBKR\'s free delayed prices (about 10–20 minutes old).</li>' +
    '<li>The symbol list is shared by every device (it lives on the spare Mac), and the Mac keeps recording prices around the clock.</li></ul></footer>' +
    '</div>';
  if (window.top !== window.self) document.body.classList.add('embedded');
  var $ = function (id) { return document.getElementById(id); };

  // ------------------------------------------------------------------ price history + changes
  function recordPrice(sym, price, srcTs, source) {
    if (typeof price !== 'number' || !(price > 0) || S.symbols.indexOf(sym) < 0) return;
    var now = Date.now(), dts = srcTs || now;
    if (dts > now + 5000) dts = now;
    if (S.dataTs[sym] != null && dts < S.dataTs[sym]) return;       // never go backwards in time
    S.dataTs[sym] = dts; S.lastObs[sym] = now;
    if (S.firstLive[sym] == null) S.firstLive[sym] = now;
    if (source) S.lastSource[sym] = source;
    var hist = S.history[sym] || (S.history[sym] = []), last = hist[hist.length - 1];
    if (last && now - last.t < 1000) last.p = price;
    else if (!last || last.p !== price || now - last.t >= 5000) hist.push({ t: now, p: price });
    if (hist.length > 40000) hist.splice(0, hist.length - 40000);
    markDirty(sym);
  }

  var dirty = {}, rafId = 0;
  function markDirty(sym) { dirty[sym] = true; if (!rafId && !paused) rafId = requestAnimationFrame(flush); }
  function flush() { rafId = 0; var syms = Object.keys(dirty); dirty = {}; syms.forEach(function (s) { update(s, true); }); }

  function compactAll() {
    var now = Date.now();
    Object.keys(S.history).forEach(function (sym) {
      var hist = S.history[sym], out = [], cur = null;
      for (var i = 0; i < hist.length; i++) {
        var s = hist[i], age = now - s.t;
        if (age > MAX_KEEP_MS) continue;
        if (age <= RAW_MS) { out.push(s); cur = null; continue; }
        var res = age <= MIN_TIER_MS ? 60000 : 300000, key = res + ':' + Math.floor(s.t / res);
        if (key === cur) out[out.length - 1] = s; else { out.push(s); cur = key; }
      }
      S.history[sym] = out;
    });
  }

  // % change over a window = last price vs. the last known price at (now - window). "Last
  // known" is right even across quiet spells: if nothing traded, the price then was the price
  // of the last trade before it.
  function changeFor(sym, ms) {
    var hist = histOf(sym);
    if (!hist || !hist.length) return null;
    var last = hist[hist.length - 1], target = Date.now() - ms;
    if (hist[0].t > target) return null;
    var lo = 0, hi = hist.length - 1;
    while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (hist[mid].t <= target) lo = mid; else hi = mid - 1; }
    return hist[lo].p ? (last.p - hist[lo].p) / hist[lo].p * 100 : null;
  }
  function isPending(sym, ms) {
    var hist = S.history[sym];
    return !!(hist && hist.length && hist[0].t > Date.now() - ms);
  }
  function applyHistory(sym, intraday, daily) {
    // The bridge's record (+ the broker's bars where the record has none) is the truth for
    // everything before this page's first live tick -- it kept recording while the page was
    // closed or the phone was asleep -- so it replaces that part; live ticks since are kept.
    // Daily closes extend it further back than the intraday data, for the 1W / 1MO windows.
    var hist = S.history[sym] || [], cut = S.firstLive[sym] != null ? S.firstLive[sym] : Infinity;
    var firstIntra = Infinity, pre = [];
    intraday.forEach(function (b) { if (b.t < firstIntra) firstIntra = b.t; });
    daily.forEach(function (b) { if (b.t < firstIntra && b.t < cut && b.p > 0) pre.push({ t: b.t, p: b.p }); });
    intraday.forEach(function (b) { if (b.t < cut && b.p > 0) pre.push(b.f ? { t: b.t, p: b.p, f: 1 } : { t: b.t, p: b.p }); });   // f = filled in after downtime (jb-ideas)
    if (!pre.length) return;
    pre.sort(function (a, b) { return a.t - b.t; });
    S.history[sym] = pre.concat(hist.filter(function (p) { return p.t >= cut; }));
    markDirty(sym);
  }

  // ------------------------------------------------------------------ market sessions (New York time)
  // Session boxes are anchored to the clock in New York (daylight saving handled by the
  // browser), not to rolling windows: "vs close" = now vs. the last 4:00 pm ET price,
  // "overnight" = the 8 pm -> 4 am ET session's move, "gap" = today's 9:30 open vs. that close.
  // NYSE holidays and early closes (1 pm) are known: see tradingDay() below.
  var ETF = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour12: false, year: 'numeric', month: 'numeric',
    day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric', weekday: 'short' });
  var DOW = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  function etParts(ms) {
    var o = {}; ETF.formatToParts(new Date(ms)).forEach(function (x) { o[x.type] = x.value; });
    return { y: +o.year, m: +o.month, d: +o.day, hh: (+o.hour) % 24, mm: +o.minute, ss: +o.second, dow: DOW[o.weekday] };
  }
  function etMs(y, m, d, hh, mm) {          // New York wall-clock time -> timestamp
    var guess = Date.UTC(y, m - 1, d, hh, mm);
    for (var i = 0; i < 2; i++) {
      var p = etParts(guess), shown = Date.UTC(p.y, p.m - 1, p.d, p.hh, p.mm);
      guess += Date.UTC(y, m - 1, d, hh, mm) - shown;
    }
    return guess;
  }
  function dayAdd(p, n) { var t = new Date(Date.UTC(p.y, p.m - 1, p.d + n)); return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate(), dow: t.getUTCDay() }; }
  // NYSE holidays and early closes (same rules as the algos' runstate.py), so the session
  // boxes, the badge and the countdown skip market holidays. p = { y, m, d } in New York.
  var HOL = {};
  function ymd(t) { return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate(), dow: t.getUTCDay() }; }
  function key(p) { return p.y * 10000 + p.m * 100 + p.d; }
  function nth(y, m, dow, n) {              // n-th weekday (Sun=0) of a month; n = -1 for the last one
    if (n > 0) { var f = new Date(Date.UTC(y, m - 1, 1)); return ymd(new Date(Date.UTC(y, m - 1, 1 + (dow - f.getUTCDay() + 7) % 7 + 7 * (n - 1)))); }
    var l = new Date(Date.UTC(y, m, 0)); return ymd(new Date(Date.UTC(y, m - 1, l.getUTCDate() - (l.getUTCDay() - dow + 7) % 7)));
  }
  function observed(y, m, d) {
    var t = new Date(Date.UTC(y, m - 1, d)), w = t.getUTCDay();
    return ymd(new Date(Date.UTC(y, m - 1, d + (w === 6 ? -1 : w === 0 ? 1 : 0))));
  }
  function easter(y) {
    var a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25),
      g = Math.floor((b - f + 1) / 3), h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4,
      l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
    return ymd(new Date(Date.UTC(y, Math.floor((h + l - 7 * m + 114) / 31) - 1, (h + l - 7 * m + 114) % 31 + 1)));
  }
  function holidaysOf(y) {
    if (HOL[y]) return HOL[y];
    var h = {}, e = easter(y), ny = new Date(Date.UTC(y, 0, 1)).getUTCDay();
    if (ny === 0) h[y * 10000 + 102] = 1; else if (ny !== 6) h[y * 10000 + 101] = 1;
    [nth(y, 1, 1, 3), nth(y, 2, 1, 3), ymd(new Date(Date.UTC(e.y, e.m - 1, e.d - 2))), nth(y, 5, 1, -1),
     observed(y, 6, 19), observed(y, 7, 4), nth(y, 9, 1, 1), nth(y, 11, 4, 4), observed(y, 12, 25)]
      .forEach(function (p) { h[key(p)] = 1; });
    var early = {}, tg = nth(y, 11, 4, 4), j4 = new Date(Date.UTC(y, 6, 4)).getUTCDay(), c24 = new Date(Date.UTC(y, 11, 24)).getUTCDay();
    early[key(ymd(new Date(Date.UTC(y, 10, tg.d + 1))))] = 1;
    if (j4 >= 2 && j4 <= 5) early[y * 10000 + 703] = 1;
    if (c24 >= 1 && c24 <= 4) early[y * 10000 + 1224] = 1;
    Object.keys(h).forEach(function (k) { delete early[k]; });
    return (HOL[y] = { hol: h, early: early });
  }
  function tradingDay(p) {
    var dow = p.dow != null ? p.dow : new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay();
    return dow >= 1 && dow <= 5 && !holidaysOf(p.y).hol[key(p)];
  }
  function earlyClose(p) { return !!holidaysOf(p.y).early[key(p)]; }
  function closeHour(p) { return earlyClose(p) ? 13 : 16; }
  function weekday(p) { return tradingDay(p); }   // a trading day: Mon-Fri and not an NYSE holiday
  function sessionAt(ms) {
    var p = etParts(ms), mins = p.hh * 60 + p.mm, today = tradingDay(p), tomorrow = tradingDay(dayAdd(p, 1));
    if (mins < 240) return today ? 'overnight' : 'closed';   // 0:00-4:00 = the night before a trading day
    if (!today) return mins >= 1200 && tomorrow ? 'overnight' : 'closed';
    var close = closeHour(p) * 60;
    if (mins < 570) return 'pre';
    if (mins < close) return 'regular';
    if (mins < close + 240) return 'after';
    return mins >= 1200 && tomorrow ? 'overnight' : 'closed';
  }
  function lastCloseMs(now) {               // the most recent trading day's close (4:00 pm, 1:00 pm on early-close days) that has passed
    var p = etParts(now), d = { y: p.y, m: p.m, d: p.d, dow: p.dow };
    if (!(tradingDay(d) && p.hh >= closeHour(d))) { do { d = dayAdd(d, -1); } while (!tradingDay(d)); }
    return etMs(d.y, d.m, d.d, closeHour(d), 0);
  }
  function overnightWindow(now) {           // the latest 8 pm -> 4 am ET session that has started
    var p = etParts(now), d = { y: p.y, m: p.m, d: p.d, dow: p.dow };
    if (p.hh < 20) d = dayAdd(d, -1);
    while (!tradingDay(dayAdd(d, 1))) d = dayAdd(d, -1);   // only the night before a trading day
    var nx = dayAdd(d, 1), start = etMs(d.y, d.m, d.d, 20, 0), end = etMs(nx.y, nx.m, nx.d, 4, 0);
    return { start: start, end: end, live: now < end };
  }
  function todayOpen(now) {                 // { open, close } for today's 9:30 gap, or null before 9:30
    var p = etParts(now);
    if (!weekday(p) || p.hh * 60 + p.mm < 570) return null;
    var open = etMs(p.y, p.m, p.d, 9, 30);
    return { open: open, close: lastCloseMs(open - 1) };
  }
  var SESS_NAME = { overnight: 'OVERNIGHT', pre: 'PRE-MARKET', regular: 'REGULAR HOURS', after: 'AFTER-HOURS', closed: 'MARKET CLOSED' };
  function nextSession(now) {               // { name, at } of the next session change
    var cur = sessionAt(now), p = etParts(now), d = { y: p.y, m: p.m, d: p.d, dow: p.dow };
    for (var k = 0; k < 5; k++, d = dayAdd(d, 1)) {
      var marks = [[4, 0], [9, 30], [13, 0], [16, 0], [17, 0], [20, 0], [24, 0]];
      for (var j = 0; j < marks.length; j++) {
        var t = etMs(d.y, d.m, d.d, marks[j][0] % 24, marks[j][1]) + (marks[j][0] === 24 ? 864e5 : 0);
        if (t > now && sessionAt(t + 1000) !== cur) return { name: sessionAt(t + 1000), at: t };
      }
    }
    return null;
  }
  function histOf(sym) { return S.history[sym] || (X.history[sym]); }
  function priceAt(hist, ms) {              // last known price at or before ms
    if (!hist || !hist.length || hist[0].t > ms) return null;
    var lo = 0, hi = hist.length - 1;
    while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (hist[mid].t <= ms) lo = mid; else hi = mid - 1; }
    return hist[lo].p;
  }
  function pctOf(a, b) { return a != null && b ? (a - b) / b * 100 : null; }
  function noOvernightHere(sym) { return B === 'webull' && S.noOvernight && !isFut(sym); }
  function sessionVals(sym) {
    var hist = histOf(sym), now = Date.now(), out = { close: null, on: null, gap: null, range: null };
    if (!hist || !hist.length) return out;
    var last = hist[hist.length - 1].p;
    out.close = pctOf(last, priceAt(hist, lastCloseMs(now)));
    var w = overnightWindow(now), p0 = priceAt(hist, w.start), end = w.live ? now : w.end;
    if (!noOvernightHere(sym)) {
      out.on = pctOf(w.live ? last : priceAt(hist, w.end), p0);
      var lo = Infinity, hi = -Infinity;
      for (var i = hist.length - 1; i >= 0 && hist[i].t >= w.start; i--) if (hist[i].t <= end) { lo = Math.min(lo, hist[i].p); hi = Math.max(hi, hist[i].p); }
      if (hi >= lo) out.range = { lo: lo, hi: hi, now: last, live: w.live };
    }
    var g = todayOpen(now);
    if (g) {
      var op = null;
      for (var j = hist.length - 1; j >= 0 && hist[j].t >= g.open; j--) if (hist[j].t < g.open + 5 * 60e3) op = hist[j].p;
      if (op == null) op = priceAt(hist, g.open + 60e3);
      out.gap = pctOf(op, priceAt(hist, g.close));
    }
    return out;
  }

  // ------------------------------------------------------------------ rendering
  function fmtPrice(p) {
    if (p == null) return '—';
    if (p >= 1000) return p.toLocaleString(undefined, { maximumFractionDigits: 2 });
    // as many decimals as the price really has (2 to 6): 227.31, 2.901 (NG), 0.006712 (6J)
    var dec = (String(+p.toFixed(6)).split('.')[1] || '').length;
    return p.toFixed(Math.max(2, Math.min(6, dec)));
  }
  function usd(p) { return Number(p).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function fmtPct(p) { return p == null ? '—' : (p >= 0 ? '+' : '') + p.toFixed(2) + '%'; }
  function fmtAge(ts) {
    var s = Math.max(0, Math.round((Date.now() - ts) / 1000));
    return s < 120 ? s + 's ago' : s < 7200 ? Math.round(s / 60) + 'm ago' : s < 172800 ? Math.round(s / 3600) + 'h ago' : Math.round(s / 86400) + 'd ago';
  }
  function setDir(el, v) { el.classList.remove('up', 'down'); if (v > 0) el.classList.add('up'); else if (v < 0) el.classList.add('down'); }
  function flashEl(el, cls) { el.classList.remove('flash-up', 'flash-down'); void el.offsetWidth; el.classList.add(cls); }

  function wantLayout() {
    var pref = lsGet('layout', 'auto');
    if (pref === 'tiles' || pref === 'rows') return pref;
    return 'tiles';   // jb-phone: phones show the computer's ticker boxes too (List switches to rows)
  }

  function makeChanges() {
    var box = h('div', 'changes'), vals = {};
    WINDOWS.forEach(function (w) {
      var c = h('div', 'chg'); c.appendChild(h('div', 'lbl', w.label));
      var v = h('div', 'val', '—'); c.appendChild(v); box.appendChild(c); vals[w.key] = v;
    });
    return { box: box, vals: vals };
  }

  function makeSession() {
    var box = h('div', 'sess-boxes'), vals = {};
    [['close', 'VS 4PM CLOSE', 'Now vs. the last 4:00 pm ET close (includes after-hours, overnight and pre-market)'],
     ['on', 'OVERNIGHT', 'The overnight session\'s move, 8 pm to 4 am ET (the latest one; updates live while it runs)'],
     ['gap', '9:30 GAP', 'Today\'s 9:30 am ET opening price vs. the previous 4:00 pm close. Fills in at the open.']].forEach(function (b) {
      var c = h('div', 'chg sess'); c.title = b[2]; c.appendChild(h('div', 'lbl', b[1]));
      var v = h('div', 'val', '—'); c.appendChild(v); box.appendChild(c); vals[b[0]] = v;
    });
    var rng = h('div', 'on-range'); rng.title = 'Overnight range (8 pm – 4 am ET): low on the left, high on the right, dot = price now';
    var lo = h('span', 'r-lo', ''), bar = h('span', 'r-bar'), dot = h('span', 'r-dot'), hi = h('span', 'r-hi', '');
    bar.appendChild(dot); rng.appendChild(h('span', 'r-lbl', 'O/N')); rng.appendChild(lo); rng.appendChild(bar); rng.appendChild(hi);
    return { box: box, vals: vals, rng: rng, lo: lo, hi: hi, dot: dot };
  }

  // prices from IBKR's free delayed feed (no live-data subscription for the symbol)
  function delayedBadge() {
    var b = h('span', 'badge-delayed', 'DELAYED');
    b.title = 'No IBKR live-data subscription for this symbol: these are IBKR\'s free delayed prices (about 10–20 minutes old)';
    b.style.display = 'none';
    return b;
  }
  function isDelayed(sym) {
    var st = S.symStatus[sym];
    return S.lastSource[sym] === 'ibkr-delayed' || !!(st && st.kind === 'delayed');
  }

  function buildTile(sym) {
    var t = h('div', 'tile'), top = h('div', 'tile-top');
    var symEl = h('span', 'sym', sym), badge = delayedBadge(); symEl.appendChild(badge); top.appendChild(symEl);
    var rm = h('button', 'remove', '×'); rm.setAttribute('aria-label', 'Remove ' + sym); rm.onclick = function () { removeSym(sym); };
    top.appendChild(rm); t.appendChild(top);
    var price = h('div', 'price', '—'); t.appendChild(price);
    var se = makeSession(); t.appendChild(se.box); t.appendChild(se.rng);
    var ch = makeChanges(); t.appendChild(ch.box);
    var held = h('div', 'held'); held.style.display = 'none'; t.appendChild(held);
    var foot = h('div', 'foot'), src = h('span', null, B), age = h('span', null, 'no data');
    foot.appendChild(src); foot.appendChild(age); t.appendChild(foot);
    return { held: held, root: t, price: price, vals: ch.vals, src: src, age: age, primary: null, badge: badge, se: se };
  }

  function buildRow(sym) {
    var r = h('div', 'row'); r.setAttribute('aria-expanded', S.expanded[sym] ? 'true' : 'false');
    var main = h('div', 'row-main');
    main.appendChild(h('span', 'chev', '▸'));
    var left = h('div', 'row-left'), symEl = h('span', 'sym', sym), badge = delayedBadge();
    symEl.appendChild(badge); left.appendChild(symEl);
    var src = h('span', 'src', B); left.appendChild(src);
    var age = h('span', 'age', 'no data'); left.appendChild(age); main.appendChild(left);
    var right = h('div', 'row-right'), price = h('span', 'price', '—'), prim = h('span', 'chg-primary', '— vs close'); prim.title = 'Change since the last 4:00 pm ET close';
    right.appendChild(price); right.appendChild(prim); main.appendChild(right);
    var rm = h('button', 'remove', '×'); rm.setAttribute('aria-label', 'Remove ' + sym);
    rm.onclick = function (e) { e.stopPropagation(); removeSym(sym); };
    main.appendChild(rm);
    main.onclick = function () { S.expanded[sym] = !S.expanded[sym]; r.setAttribute('aria-expanded', S.expanded[sym] ? 'true' : 'false'); };
    r.appendChild(main);
    var det = h('div', 'detail'), se = makeSession(), ch = makeChanges(), held = h('div', 'held'); held.style.display = 'none';
    det.appendChild(se.box); det.appendChild(se.rng); det.appendChild(ch.box); det.appendChild(held);
    var foot = h('div', 'foot'); foot.appendChild(h('span', null, 'tap row to collapse'));
    det.appendChild(foot); r.appendChild(det);
    return { held: held, root: r, price: price, vals: ch.vals, src: src, age: age, primary: prim, badge: badge, se: se };
  }

  // watchlist groups: the menu next to US EQUITIES shows only some of your stocks (nothing is removed)
  var GROUP = lsGet('group', 'all');
  function inGroup(sym) {
    if (GROUP === 'held') { var p = HOLD.pos[sym]; return !!(p && p.shares > 0); }
    if (GROUP === 'alert') return !!(W.data && W.data.alerts.some(function (a) { return a.sym === sym && !a.done; }));
    if (GROUP === 'on') { var v = sessionVals(sym).on; return v != null && Math.abs(v) >= 1; }
    return true;
  }

  function render() {
    var want = wantLayout();
    if (want !== layout) {
      layout = want; els = {};
      document.body.classList.remove('layout-tiles', 'layout-rows');
      document.body.classList.add('layout-' + layout);
      $('layoutBtn').textContent = layout === 'tiles' ? 'List' : 'Tiles';
      $('eqBox').className = $('fuBox').className = layout === 'tiles' ? 'grid' : 'list';
      $('eqBox').innerHTML = ''; $('fuBox').innerHTML = '';
    }
    var eqAll = S.symbols.filter(function (s) { return !isFut(s); }).sort(), fu = S.symbols.filter(isFut).sort();
    var eq = eqAll.filter(inGroup);
    $('eqCount').textContent = eq.length === eqAll.length ? eq.length : eq.length + ' of ' + eqAll.length; $('fuCount').textContent = fu.length;
    [[eq, $('eqBox')], [fu, $('fuBox')]].forEach(function (pair) {
      var list = pair[0], box = pair[1];
      Array.prototype.slice.call(box.children).forEach(function (c) { if (!c.dataset.sym || list.indexOf(c.dataset.sym) < 0) box.removeChild(c); });
      if (!list.length) {
        box.appendChild(h('div', 'empty', box.id === 'eqBox' && GROUP !== 'all' && S.symbols.length ? 'None in this group right now.' : S.gotWatchlist ? 'No symbols. Add one above.' : 'Connecting…'));
        return;
      }
      list.forEach(function (sym) {
        var e = els[sym];
        if (!e) { e = els[sym] = layout === 'tiles' ? buildTile(sym) : buildRow(sym); e.root.dataset.sym = sym; }
        box.appendChild(e.root);          // appendChild also keeps the A-Z order
        update(sym, false);
      });
    });
    Object.keys(els).forEach(function (s) { if (S.symbols.indexOf(s) < 0) delete els[s]; });
    capWarning();
  }

  function update(sym, fromTick) {
    var e = els[sym]; if (!e || paused) return;
    var hist = S.history[sym], st = S.symStatus[sym], dly = isDelayed(sym);
    e.badge.style.display = dly ? '' : 'none';
    e.root.classList.toggle('delayed', dly);
    if (st && (st.kind === 'error' || st.kind === 'stale') && (!hist || !hist.length || Date.now() - st.at < 60000)) {
      e.src.textContent = st.msg; e.src.className = e.primary ? 'src err' : 'err';
    } else {
      var tag = S.lastSource[sym]; e.src.textContent = tag ? (LABELS[tag] || tag) : B; e.src.className = e.primary ? 'src' : '';
    }
    if (!hist || !hist.length) return;
    var last = hist[hist.length - 1], prev = S.lastPrice[sym];
    e.price.textContent = fmtPrice(last.p);
    if (fromTick && prev != null && last.p !== prev) flashEl(e.root, last.p > prev ? 'flash-up' : 'flash-down');
    S.lastPrice[sym] = last.p;
    setDir(e.price, changeFor(sym, COLOR_MS));
    var z = changeFor(sym, ZONE_MS);
    e.root.classList.remove('zone-up', 'zone-down');
    if (z > 0) e.root.classList.add('zone-up'); else if (z < 0) e.root.classList.add('zone-down');
    WINDOWS.forEach(function (w) {
      var el = e.vals[w.key], pct = changeFor(sym, w.ms), pend = pct == null && isPending(sym, w.ms);
      el.textContent = pend ? '…' : fmtPct(pct); el.title = pend ? 'Waiting for history to cover this window' : '';
      el.classList.toggle('pending', pend); setDir(el, pend ? 0 : pct);
    });
    var sv = sessionVals(sym), noOn = noOvernightHere(sym);
    ['close', 'on', 'gap'].forEach(function (k) {
      var el = e.se.vals[k], v = sv[k];
      el.textContent = k === 'on' && noOn ? 'n/a' : k === 'gap' && v == null ? 'at 9:30' : fmtPct(v);
      el.classList.toggle('pending', v == null); setDir(el, v);
    });
    if (sv.range && sv.range.hi > sv.range.lo) {
      var r = sv.range, f = Math.max(0, Math.min(1, (r.now - r.lo) / (r.hi - r.lo)));
      e.se.rng.style.display = ''; e.se.lo.textContent = 'L ' + fmtPrice(r.lo); e.se.hi.textContent = 'H ' + fmtPrice(r.hi);
      e.se.dot.style.left = (f * 100) + '%';
      e.se.dot.className = 'r-dot' + (r.now > r.hi ? ' above' : r.now < r.lo ? ' below' : '');
    } else e.se.rng.style.display = 'none';
    var dim = noOn && sessionAt(Date.now()) === 'overnight';
    e.root.classList.toggle('no-on', dim);
    if (dim) { e.src.textContent = 'no overnight prices on Webull · see IBKR'; e.src.className = e.primary ? 'src' : ''; }
    if (e.primary) {
      var pp = sv.close;
      e.primary.textContent = fmtPct(pp) + ' vs close';
      e.primary.classList.toggle('pending', pp == null); setDir(e.primary, pp);
    }
    e.age.textContent = fmtAge(S.dataTs[sym] != null ? S.dataTs[sym] : last.t);
    var ps = HOLD.pos[sym];
    if (ps && ps.shares > 0 && ps.avg) {
      var g = (last.p - ps.avg) * ps.shares, gp = (last.p - ps.avg) / ps.avg * 100;
      e.held.style.display = '';
      // one line: shares @ your average cost · price now · gain/loss in $ and %
      e.held.innerHTML = '<b>HELD</b> ' + (+ps.shares).toLocaleString() + ' sh @ $' + usd(ps.avg) + ' · now $' + usd(last.p) + ' · <span class="' + (g >= 0 ? 'up' : 'down') + '">' +
        (g >= 0 ? '+$' : '-$') + Math.abs(g).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' (' + fmtPct(gp) + ')</span>';
      e.held.title = 'You hold ' + (+ps.shares).toLocaleString() + ' shares at an average cost of $' + usd(ps.avg) + ' (Portfolio tab)';
    } else e.held.style.display = 'none';
  }

  function renderTape() {
    if (layout !== 'tiles' || paused) return;
    var el = $('tape'); if (!S.symbols.length) { el.innerHTML = '<span class="t-sym">ADD SYMBOLS TO BEGIN</span>'; return; }
    var eqs = S.symbols.filter(function (s) { return !isFut(s); }).sort();
    var fus = S.symbols.filter(isFut).concat(Object.keys(X.history).filter(function (s) { return S.symbols.indexOf(s) < 0; })).sort();
    var item = function (sym) {
      var hist = histOf(sym);
      if (!hist || !hist.length) return '<span><span class="t-sym">' + esc(sym) + '</span><span class="t-px">--</span></span>';
      var pct = changeFor(sym, WINDOWS[0].ms), cls = pct == null ? 'flat' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
      var arrow = pct == null ? '' : pct > 0 ? '▲' : pct < 0 ? '▼' : '';
      return '<span><span class="t-sym">' + esc(sym) + (isDelayed(sym) ? ' <span class="t-dly">DLY</span>' : '') + '</span><span class="t-px">' + fmtPrice(hist[hist.length - 1].p) +
        '</span><span class="t-chg ' + cls + '">' + arrow + ' ' + fmtPct(pct) + '</span>' + tapeClose(sym) + '</span>';
    };
    var html = eqs.map(item).join('') + (fus.length ? '<span class="t-grp">FUTURES</span>' + fus.map(item).join('') : '');
    el.innerHTML = html + html;
  }

  function tapeClose(sym) {
    var v = sessionVals(sym).close, cls = v == null ? 'flat' : v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
    return '<span class="t-cls ' + cls + '" title="since the 4 pm ET close">' + (v == null ? '' : 'vs close ' + fmtPct(v)) + '</span>';
  }

  function capWarning() {
    var eq = S.symbols.filter(function (s) { return !isFut(s); }).length, fu = S.symbols.length - eq, msg = '';
    if (B === 'ibkr' && eq * 2 + fu > 90)
      msg = 'IBKR allows about 100 market-data lines by default; these ' + eq + ' stocks + ' + fu + ' futures use about ' + (eq * 2 + fu) +
        ' (stocks use two each: regular hours + overnight). Extras may not stream.';
    if (B === 'webull' && eq + fu > 90)
      msg = 'Webull streams at most 100 symbols per App Key (shared with the scanner/algo if they use Webull). Extras may not stream.';
    showWarn(msg, false, 'cap');
  }
  var warnSrc = null;
  function showWarn(msg, isErr, src) {
    var w = $('warn');
    if (!msg) { if (warnSrc === src) { w.style.display = 'none'; warnSrc = null; } return; }
    w.textContent = msg; w.className = 'warn' + (isErr ? ' err' : ''); w.style.display = 'block'; warnSrc = src;
  }

  // ------------------------------------------------------------------ status line
  var notes = { eq: null, fu: null };
  function setStatus(which, kind, msg) { notes[which] = { kind: kind, msg: msg, at: Date.now() }; paint(which, kind, msg); }
  function paint(which, kind, msg) {
    $(which === 'eq' ? 'dotEq' : 'dotFu').className = 'dot ' + kind;
    $(which === 'eq' ? 'statEq' : 'statFu').textContent = (which === 'eq' ? 'equities: ' : 'futures: ') + msg;
  }
  function refreshStatus() {
    ['eq', 'fu'].forEach(function (which) {
      var list = S.symbols.filter(function (s) { return which === 'fu' ? isFut(s) : !isFut(s); }), newest = 0;
      list.forEach(function (s) { if (S.lastObs[s] > newest) newest = S.lastObs[s]; });
      var n = notes[which];
      if (!newest) { if (n) paint(which, n.kind, n.msg); return; }
      var age = Math.round((Date.now() - newest) / 1000);
      var extra = n && (n.kind === 'stale' || n.kind === 'error') && Date.now() - n.at < 30000 ? ' · ' + n.msg : '';
      paint(which, age < 45 && !(ws && ws.readyState !== 1) ? 'live' : 'stale', 'last update ' + fmtAge(newest) + extra);
    });
  }

  // ------------------------------------------------------------------ connection
  var ws = null, retryTimer = null, delay = 1000, authChecked = false;
  function autoUrl() {
    if (location.protocol === 'http:' || location.protocol === 'https:')
      return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/' + B;
    return 'ws://localhost:' + (B === 'ibkr' ? 8765 : 8766);
  }
  function url() { return lsGet('url', '') || autoUrl(); }
  function send(o) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(o)); }
  function retry() {
    if (retryTimer) return;
    retryTimer = setTimeout(function () { retryTimer = null; delay = Math.min(delay * 1.6, 15000); connect(); }, delay);
  }
  function checkLogin() {
    if (authChecked || location.protocol.indexOf('http') !== 0 || lsGet('url', '')) return;
    authChecked = true;
    fetch('/api/health', { credentials: 'same-origin' }).then(function (r) {
      if (r.status === 401) (window.top || window).location.href = '/login?next=' + encodeURIComponent((window.top || window).location.pathname);
      else if (r.ok) return r.json().then(function (hj) {
        if (hj[B] && !hj[B].up) showWarn('The ' + B.toUpperCase() + ' bridge is not running on the spare Mac. On the Mac run:  ./jb status  (and ./jb logs ' + B + ')', true, 'conn');
      });
    }).catch(function () {}).then(function () { setTimeout(function () { authChecked = false; }, 30000); });
  }
  function connect() {
    if (ws && (ws.readyState === 0 || ws.readyState === 1)) return;
    var u = url(), tok = lsGet('token', '');
    try { ws = new WebSocket(u); } catch (e) { setStatus('eq', 'error', 'bad bridge address'); setStatus('fu', 'error', 'bad bridge address'); retry(); return; }
    ws.onopen = function () {
      delay = 1000; showWarn('', false, 'conn');
      // (re)connected -- e.g. the phone woke up: ask for history again so the time this page
      // wasn't listening is filled in from the bridge's record instead of left as a gap
      S.histAsked = {}; S.firstLive = {};
      if (tok) send({ type: 'auth', token: tok });
      send({ type: 'hello' });
      setStatus('eq', 'idle', 'connected'); setStatus('fu', 'idle', 'connected');
    };
    ws.onmessage = function (ev) {
      var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === 'ticks') { for (var i = 0; i < m.ticks.length; i++) { var t = m.ticks[i]; recordPrice(t.symbol, t.price, t.ts, t.source); } }
      else if (m.type === 'tick') recordPrice(m.symbol, m.price, m.ts, m.source);
      else if (m.type === 'watchlist') onWatchlist(m.symbols || []);
      else if (m.type === 'history') applyHistory(m.symbol, m.intraday || [], m.daily || []);
      else if (m.type === 'status') onStatus(m);
      else if (m.type === 'adjust') onAdjust(m);
    };
    ws.onclose = function (ev) {
      var why = ev && ev.code === 4001 ? 'bridge rejected the token' : 'bridge offline — retrying';
      setStatus('eq', 'stale', why); setStatus('fu', 'stale', why);
      if (ev && ev.code === 4001) showWarn('The bridge refused the connection token. Open "Connection" and fix the token (or clear the address to use the gateway).', true, 'conn');
      checkLogin(); retry();
    };
  }
  // Webull board: Webull has no futures data on this plan, so ES / NQ / YM for the ticker tape
  // come from the IBKR feed (read only; nothing is added to either watchlist).
  var TAPE_FUT = ['ES=F', 'NQ=F', 'YM=F'], xws = null;
  function connectX() {
    if (B !== 'webull' || location.protocol.indexOf('http') !== 0 || lsGet('url', '')) return;
    try { xws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/ibkr'); } catch (e) { return; }
    xws.onopen = function () { xws.send(JSON.stringify({ type: 'hello' })); };
    xws.onmessage = function (ev) {
      var m; try { m = JSON.parse(ev.data); } catch (e) { return; }
      if (m.type === 'watchlist') (m.symbols || []).forEach(function (s) { if (TAPE_FUT.indexOf(s) >= 0) xws.send(JSON.stringify({ type: 'history', symbol: s })); });
      else if (m.type === 'history' && TAPE_FUT.indexOf(m.symbol) >= 0) {
        var pts = (m.daily || []).concat(m.intraday || []).filter(function (b) { return b.p > 0; }).sort(function (a, b) { return a.t - b.t; });
        var live = (X.history[m.symbol] || []).filter(function (b) { return pts.length && b.t > pts[pts.length - 1].t; });
        X.history[m.symbol] = pts.concat(live);
      } else if (m.type === 'ticks') m.ticks.forEach(function (t) {
        var hx = X.history[t.symbol];
        if (hx && t.price > 0 && TAPE_FUT.indexOf(t.symbol) >= 0) { hx.push({ t: Date.now(), p: t.price }); if (hx.length > 40000) hx.splice(0, 10000); }
      });
    };
    xws.onclose = function () { setTimeout(connectX, 15000); };
  }

  function onWatchlist(list) {
    S.gotWatchlist = true;
    S.symbols = list.slice();
    Object.keys(S.history).forEach(function (s) { if (list.indexOf(s) < 0) { delete S.history[s]; delete S.dataTs[s]; delete S.lastObs[s]; delete S.firstLive[s]; delete S.histAsked[s]; } });
    list.forEach(function (s) { if (!S.histAsked[s]) { S.histAsked[s] = true; send({ type: 'history', symbol: s }); } });
    render();
  }
  function onAdjust(m) {
    // a future rolled to its next contract: shift earlier points by the gap between the two
    // contracts, so the change boxes don't show the roll as a price move
    var hist = S.history[m.symbol];
    if (!hist || typeof m.delta !== 'number') return;
    for (var i = 0; i < hist.length && hist[i].t < m.before; i++) hist[i].p = +(hist[i].p + m.delta).toFixed(6);
    markDirty(m.symbol);
  }
  function onStatus(m) {
    // the bridge found overnight prices on the Webull plan again (hedge-2026-10-12): its notice is cleared, tiles show overnight
    if (m.kind === 'notice' && !m.msg) { S.noOvernight = false; showWarn('', false, 'notice'); render(); return; }
    if (m.kind === 'notice' && /overnight/i.test(m.msg || '')) S.noOvernight = true;
    if (m.kind === 'notice') { showWarn(m.msg, false, 'notice'); $('warn').className = 'warn notice'; return; }   // board-wide notice (e.g. no overnight prices)
    if (m.symbol) { S.symStatus[m.symbol] = { kind: m.kind, msg: m.msg, at: Date.now() }; markDirty(m.symbol); return; }
    setStatus('eq', m.kind || 'idle', m.msg); setStatus('fu', m.kind || 'idle', m.msg);
    showWarn(m.kind === 'error' ? m.msg : '', true, 'bridge');
  }

  // ------------------------------------------------------------------ actions
  function addSym() {
    var inp = $('addSym'), sym = inp.value.trim().toUpperCase();
    if (!sym) return;
    if (!ws || ws.readyState !== 1) { showWarn('Not connected to the bridge yet — try again in a moment.', true, 'add'); return; }
    showWarn('', false, 'add');
    send({ type: 'add', symbol: sym }); inp.value = ''; if (layout === 'rows') inp.blur();
  }
  function removeSym(sym) { send({ type: 'remove', symbol: sym }); }
  $('addBtn').onclick = addSym;
  $('addSym').addEventListener('keydown', function (e) { if (e.key === 'Enter') addSym(); });
  $('layoutBtn').onclick = function () { lsSet('layout', layout === 'tiles' ? 'rows' : 'tiles'); render(); };
  $('advBtn').onclick = function () { $('adv').classList.toggle('open'); };
  $('bridgeUrl').value = lsGet('url', ''); $('bridgeUrl').placeholder = autoUrl(); $('bridgeToken').value = lsGet('token', '');
  $('connHint').textContent = 'Leave both blank when this page is opened from the spare Mac\'s address (the normal case). ' +
    'Only for connecting straight to a bridge, e.g. ws://spare-mac:' + (B === 'ibkr' ? 8765 : 8766) + ' with that bridge\'s token.';
  $('saveConn').onclick = function () {
    lsSet('url', $('bridgeUrl').value.trim()); lsSet('token', $('bridgeToken').value.trim());
    $('adv').classList.remove('open'); if (ws) try { ws.close(); } catch (e) {}
  };
  window.addEventListener('resize', function () { if (lsGet('layout', 'auto') === 'auto' && wantLayout() !== layout) render(); });

  // dashboard tabs tell hidden boards to stop drawing (they keep recording prices)
  function setPaused(p) {
    if (p === paused) return; paused = p;
    if (!p) { Object.keys(els).forEach(function (s) { update(s, false); }); renderTape(); }
  }
  window.addEventListener('message', function (e) {
    if (e.origin === location.origin && e.data && e.data.type === 'jb-visibility') setPaused(!e.data.visible);
  });
  document.addEventListener('visibilitychange', function () { setPaused(document.hidden); });

  // ------------------------------------------------------------------ alerts + morning card (gateway/watch.py)
  // Alerts live on the spare Mac, so they fire (and reach the phone) even with every page closed.
  var W = { data: null, seenLog: null, panel: false };
  function api(body) {
    var opt = body ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { credentials: 'same-origin', cache: 'no-store' };
    return fetch('/api/watch', opt).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || 'failed'); return j; }); });
  }
  function loadWatch() {
    if (location.protocol.indexOf('http') !== 0 || paused) return;
    api().then(function (d) { W.data = d; drawWatch(); }).catch(function () {});
  }
  function drawWatch() {
    var d = W.data; if (!d) return;
    // new alert hits -> a toast on this page (the phone gets its own push)
    var newest = d.log.length ? d.log[0].at : 0;
    if (W.seenLog != null) d.log.filter(function (l) { return l.at > W.seenLog; }).forEach(function (l) { toast('🔔 ' + l.text); });
    W.seenLog = Math.max(W.seenLog || 0, newest);
    // morning card: shown from when it's made until 10:30 am ET that day (× hides it)
    var m = d.morning, box = $('morning'), hid = lsGet('morning_hidden', '');
    var ok = m && m.at && Date.now() - m.at < 5 * 3600e3 && String(m.at) !== hid && !(sessionAt(Date.now()) === 'regular' && etParts(Date.now()).hh * 60 + etParts(Date.now()).mm > 630);
    if (ok) {
      var li = function (r) { return '<span class="m-item ' + (r.close >= 0 ? 'up' : 'down') + '">' + esc(r.sym) + ' ' + fmtPct(r.close) + '</span>'; };
      box.innerHTML = '<div class="m-head">MORNING CARD · ' + esc(m.label) + ' ET<button class="m-copy" id="mCopy" title="Copy this card as text to paste into the Claude app">Copy for Claude</button><button class="m-x" id="mHide" aria-label="Hide">×</button></div>' +
        (m.futures.length ? '<div><b>Futures</b> ' + m.futures.map(li).join('') + '</div>' : '') +
        (m.up.length ? '<div><b>Up most vs. close</b> ' + m.up.map(li).join('') + '</div>' : '') +
        (m.down.length ? '<div><b>Down most vs. close</b> ' + m.down.map(li).join('') + '</div>' : '') +
        (m.broke_hi.length ? '<div><b>Broke overnight high</b> ' + m.broke_hi.map(esc).join(', ') + '</div>' : '') +
        (m.broke_lo.length ? '<div><b>Broke overnight low</b> ' + m.broke_lo.map(esc).join(', ') + '</div>' : '');
      box.style.display = '';
      $('mHide').onclick = function () { lsSet('morning_hidden', String(m.at)); box.style.display = 'none'; };
      $('mCopy').onclick = function () { copyText(m.text + '\n\nWhat stands out for the open, and what news could explain these moves?'); };
    } else box.style.display = 'none';
    if (W.panel) drawPanel();
  }
  function copyText(t) {
    try { navigator.clipboard.writeText(t).then(function () { toast('Copied. Paste it into the Claude app.'); }); }
    catch (e) { window.prompt('Copy this:', t); }
  }
  function toast(msg) {
    var el = h('div', 'toast', msg); $('toasts').appendChild(el);
    setTimeout(function () { el.classList.add('gone'); }, 9000); setTimeout(function () { el.remove(); }, 10000);
  }
  function drawPanel() {
    var d = W.data || { alerts: [], log: [], topic: '' }, p = $('alertPanel');
    p.innerHTML = '<div class="ap-row"><input id="apSym" placeholder="NVDA" autocapitalize="characters" spellcheck="false">' +
      '<select id="apKind"><option value="above">goes above</option><option value="below">goes below</option><option value="move">moves ± % vs. 4 pm close</option></select>' +
      '<input id="apVal" type="number" step="any" placeholder="price or %"><button class="btn" id="apAdd">Add alert</button></div>' +
      '<div class="ap-err" id="apErr"></div>' +
      '<div class="ap-list">' + (d.alerts.length ? d.alerts.map(function (a) {
        return '<div class="ap-item' + (a.done ? ' done' : '') + '"><span>' + esc(a.text) + (a.now != null ? ' <i>(now ' + fmtPrice(a.now) + ')</i>' : '') +
          (a.done ? ' · <b>hit</b>' : a.kind === 'move' ? ' · re-arms after each close' : '') + '</span><button data-id="' + esc(a.id) + '" class="remove" aria-label="Delete">×</button></div>';
      }).join('') : '<div class="ap-none">No alerts yet.</div>') + '</div>' +
      (d.log.length ? '<div class="ap-log"><b>Recent</b>' + d.log.slice(0, 5).map(function (l) { return '<div>' + esc(new Date(l.at).toLocaleString()) + ' · ' + esc(l.text) + '</div>'; }).join('') + '</div>' : '') +
      '<div class="ap-phone">' + (d.topic ? 'Phone: in the free <b>ntfy</b> app, subscribe to the topic <code>' + esc(d.topic) + '</code> (keep it private). <button class="btn ghost" id="apTest">Send a test</button>'
        : 'Phone push is off: no JB_NTFY_TOPIC in settings.env.') + ' <button class="btn ghost" id="apCard" title="Make a morning card right now">Make morning card now</button></div>';
    $('apAdd').onclick = function () {
      api({ action: 'add', sym: $('apSym').value, kind: $('apKind').value, value: $('apVal').value })
        .then(function (j) { W.data = j; drawWatch(); }).catch(function (e) { $('apErr').textContent = e.message; });
    };
    [].forEach.call(p.querySelectorAll('.ap-item .remove'), function (b) {
      b.onclick = function () { api({ action: 'remove', id: b.dataset.id }).then(function (j) { W.data = j; drawWatch(); }); };
    });
    if ($('apTest')) $('apTest').onclick = function () { api({ action: 'test' }).then(function () { toast('Test sent to your phone.'); }).catch(function (e) { $('apErr').textContent = e.message; }); };
    $('apCard').onclick = function () { lsSet('morning_hidden', ''); api({ action: 'card' }).then(function (j) { W.data = j; drawWatch(); }); };
  }
  $('alertBtn').onclick = function () {
    W.panel = !W.panel; $('alertPanel').classList.toggle('open', W.panel);
    if (W.panel) { drawPanel(); loadWatch(); }
  };
  loadWatch(); setInterval(loadWatch, 15000);

  // your holdings (Portfolio tab, gateway/portfolio.py): a HELD line on tiles, and the chart's cost line
  var HOLD = { pos: {}, trades: [] };
  function loadHoldings() {
    if (location.protocol.indexOf('http') !== 0 || paused) return;
    fetch('/api/holdings', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d) return;
      var pos = {}; (d.positions || []).forEach(function (p) { pos[p.sym] = p; });
      HOLD.pos = pos; HOLD.trades = d.trades || [];
    }).catch(function () {});
  }
  loadHoldings(); setInterval(loadHoldings, 30000);

  $('grpSel').value = GROUP; if ($('grpSel').value !== GROUP) { GROUP = 'all'; $('grpSel').value = 'all'; }
  $('grpSel').onchange = function () { GROUP = this.value; lsSet('group', GROUP === 'all' ? '' : GROUP); render(); };
  setInterval(function () { if (GROUP !== 'all' && !paused) render(); }, 15000);

  // ------------------------------------------------------------------ clock + timers
  function tickClock() {
    var d = new Date();
    $('clockTime').textContent = d.toLocaleTimeString('en-US', { hour12: false });
    $('clockDate').textContent = d.toLocaleDateString('en-US', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
    var cur = sessionAt(d.getTime()), nx = nextSession(d.getTime()), sb = $('sessBadge');
    var left = nx ? Math.max(0, Math.round((nx.at - d.getTime()) / 60000)) : 0;
    var when = left >= 1440 ? Math.floor(left / 1440) + 'd ' + Math.floor(left % 1440 / 60) + 'h' : Math.floor(left / 60) + 'h ' + (left % 60) + 'm';
    sb.className = 'sess-badge s-' + cur;
    sb.textContent = SESS_NAME[cur] + (nx ? ' · ' + SESS_NAME[nx.name].toLowerCase() + ' in ' + when : '');
    sb.title = 'Market session now (New York time). Holidays are not known.';
  }
  render(); tickClock();
  setInterval(function () { tickClock(); if (!paused) { Object.keys(els).forEach(function (s) { update(s, false); }); renderTape(); } refreshStatus(); }, 1000);
  setInterval(compactAll, 60000);
  connect(); connectX();

  window.__jb = { state: S, layout: function () { return layout; }, ws: function () { return ws; },
    sessionAt: sessionAt, lastCloseMs: lastCloseMs, overnightWindow: overnightWindow, sessionVals: sessionVals, extra: X, holdings: function () { return HOLD; }, broker: B };   // for troubleshooting
})();
