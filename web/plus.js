/* Alex Hale's Board -- Night desk chips on the price boards (jb-night and later installs).
 *
 * Adds a small row of chips to each stock tile (and each opened list row), only when there is
 * something to see:
 *   SPREAD   the gap between the best bid and ask, in cents and %, with the shares on each side
 *            (IBKR board; green = tight, amber = wide, red = very wide)
 *   AH PEAK  after a 1%+ after-hours move: the after-hours peak vs the 4 pm close and where it
 *            is now (shows from 4 pm until the next 9:30 open)
 *   NO TRADES SINCE   no trade for 5+ minutes while a session is open (thin stocks overnight)
 * plus a line under the session badge about the exchanges' new overnight session (Dec 6, 2026)
 *   SHORT / RATING / OPTIONS / WHY   (jb-mdata) a crowded short, a fresh analyst rating change,
 *            unusual options today, and the catalyst tags when a stock is moving 2%+
 * and a red notice if the overnight venue looks down.
 * On the price chart (jb-ticket): LEVELS (your own price lines, saved on the Mac, with optional
 * cross alerts; tap the chart to add one) and TRADE (the order ticket for that symbol; tap the chart
 * to use that price as the limit). Your limit, open orders and journal cost are drawn as lines. Chart buttons Pre-mkt H/L, After-hrs H/L
 * and Busy night hours are drawn from here too (hooks in chart.js).
 * Data: the board's own prices + /api/plus/night/quotes (gateway/night.py). Hooked in by one
 * <script src="plus.js"> line on ibkr.html and webull.html.
 */
(function () {
  'use strict';
  if (window.__jbPlus) return;
  window.__jbPlus = true;
  var Q = {}, venueDown = false, MD = {}, mdOn = true;
  var EXCH_START = Date.UTC(2026, 11, 7, 2, 0);      // Sun Dec 6, 2026, 9:00 pm New York (EST = UTC-5)

  var css =
    '.plus{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:4px 0 2px;font-size:10.5px;}' +
    '.plus:empty{display:none;}' +
    '.pl-chip{border-radius:3px;padding:1px 5px;font-weight:700;letter-spacing:.3px;border:1px solid;white-space:nowrap;}' +
    // a long chip (SPREAD, AH PEAK) wraps inside a narrow phone tile instead of running past it and scrolling the page sideways
    '.plus{min-width:0;max-width:100%;} .pl-chip{max-width:100%;box-sizing:border-box;}' +
    '@media (max-width:600px){.pl-chip{white-space:normal;overflow-wrap:anywhere;}}' +
    '.pl-g{background:#062a14;color:#5dff8f;border-color:#145c2c;} .pl-a{background:#2e2400;color:#ffc400;border-color:#7a5c00;}' +
    '.pl-r{background:#3a0b0b;color:#ff8a80;border-color:#7a1c1c;} .pl-b{background:#0e2a3a;color:#7fd6ff;border-color:#1e5a78;}' +
    '.pl-d{background:#14202e;color:#9fb3c8;border-color:#2a3d52;}' +
    '#plExch{font-size:10px;color:#9fb3c8;letter-spacing:.04em;margin-top:2px;}' +
    '#plVenue{display:none;background:#3a0b0b;color:#ffb3ad;border:1px solid #7a1c1c;border-radius:6px;padding:6px 10px;margin:6px 8px;font-size:12px;}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  function J() { return window.__jb; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  var ETF = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit', hour12: true });
  function etTime(ms) { return ETF.format(new Date(ms)).toLowerCase().replace(' ', ' '); }
  var ETP = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', weekday: 'short' });
  function etParts(ms) {
    var o = {}; ETP.formatToParts(new Date(ms)).forEach(function (x) { o[x.type] = x.value; });
    return { y: +o.year, m: +o.month, d: +o.day, hh: +o.hour % 24, mm: +o.minute, wd: o.weekday };
  }
  function etMs(p, hh, mm) {                 // New York wall clock on day p -> timestamp
    var base = Date.UTC(p.y, p.m - 1, p.d, hh, mm);
    for (var o = 4; o <= 5; o++) {            // New York is 4 h (summer) or 5 h (winter) behind UTC
      var t = base + o * 3600e3, q = etParts(t);
      if (q.hh === hh && q.mm === mm && q.d === p.d) return t;
    }
    return base + 5 * 3600e3;
  }
  function priceAt(hist, ms) { var best = null; for (var i = 0; i < hist.length && hist[i].t <= ms; i++) best = hist[i].p; return best; }
  function range(hist, a, b) {
    var hi = -Infinity, lo = Infinity, n = 0;
    for (var i = 0; i < hist.length; i++) { var x = hist[i]; if (x.t >= a && x.t < b) { n++; if (x.p > hi) hi = x.p; if (x.p < lo) lo = x.p; } }
    return n ? { hi: hi, lo: lo } : null;
  }
  function pc(v) { return (v >= 0 ? '+' : '') + v.toFixed(1) + '%'; }

  // ------------------------------------------------------------------ data from the Mac
  function loadQuotes() {
    if (location.protocol.indexOf('http') !== 0 || document.hidden) return;
    fetch('/api/plus/night/quotes', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) { Q = d.quotes || {}; venueDown = !!d.venue_down; } }).catch(function () {});
  }

  function loadMdata() {                       // jb-mdata: skipped quietly if it isn't installed
    if (!mdOn || location.protocol.indexOf('http') !== 0 || document.hidden) return;
    var j = J(); if (!j || !j.state) return;
    var syms = Object.keys(j.state.history || {}).filter(function (s) { return !/=F$/.test(s); });
    if (!syms.length) return;
    fetch('/api/plus/mdata/scan?syms=' + encodeURIComponent(syms.join(',')), { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { if (r.status === 404) mdOn = false; return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.syms) MD = d.syms; }).catch(function () {});
  }

  // ------------------------------------------------------------------ chips
  function mdChips(sym, hist, now) {
    var d = MD[sym]; if (!d) return '';
    var h = '', j = J(), close = j && j.lastCloseMs ? priceAt(hist, j.lastCloseMs(now)) : null;
    var mv = close ? (hist[hist.length - 1].p / close - 1) * 100 : null;
    if (d.short_pct != null && d.short_pct >= 15) h += '<span class="pl-chip pl-a" title="' + d.short_pct.toFixed(1) + '% of the shares that trade freely are sold short' + (d.short_ratio != null ? ' (' + d.short_ratio + ' days to cover)' : '') + '. A crowded short can squeeze up fast on good news. Short interest is published twice a month.">SHORT ' + Math.round(d.short_pct) + '%</span>';
    if (d.rating) h += '<span class="pl-chip ' + (d.rating.action === 'up' ? 'pl-g' : d.rating.action === 'down' ? 'pl-r' : 'pl-b') + '" title="' + esc(d.rating.text) + ' (' + esc(new Date(d.rating.t).toLocaleDateString()) + ')">' + (d.rating.action === 'up' ? 'UPGRADE' : d.rating.action === 'down' ? 'DOWNGRADE' : 'NEW COVERAGE') + ' ' + esc(d.rating.firm.split(' ')[0]) + '</span>';
    if (d.unusual) h += '<span class="pl-chip pl-b" title="Options on this traded far more than their open interest today: someone is making a big new bet. Details on the Night desk.">UNUSUAL OPTIONS</span>';
    if (mv != null && Math.abs(mv) >= 2 && d.tags && d.tags.length) h += '<span class="pl-chip ' + (d.tags[0] === 'NO NEWS' ? 'pl-d' : 'pl-b') + '" title="Why it may be moving, from earnings dates, analyst ratings, SEC filings, today\'s headlines and its sector fund. NO NEWS = no reason found: be careful.">WHY: ' + esc(d.tags.join(', ')) + '</span>';
    return h;
  }
  function spreadChip(sym, last) {
    var q = Q[sym]; if (!q || Date.now() - q.ts > 5 * 60000) return '';
    var mid = (q.bid + q.ask) / 2, sp = q.ask - q.bid, pct = mid ? sp / mid * 100 : 0;
    var cls = pct <= 0.15 ? 'pl-g' : pct <= 0.5 ? 'pl-a' : 'pl-r';
    var c = Math.round(sp * 1000) / 10, cents = sp < 1 ? (c < 10 ? c.toFixed(1) : Math.round(c)) + '¢' : '$' + sp.toFixed(2);
    var size = (q.bs || q.as) ? ' · ' + (q.bs || 0) + '×' + (q.as || 0) : '';
    return '<span class="pl-chip ' + cls + '" title="Best bid $' + q.bid + ' (shares wanted: ' + (q.bs || '?') + ') and best ask $' + q.ask +
      ' (shares offered: ' + (q.as || '?') + '). The spread is what a market-style buy-then-sell would cost you. Green = tight (0.15% or less), amber = wide, red = very wide (over 0.5%).">SPREAD ' +
      cents + ' (' + pct.toFixed(2) + '%)' + size + '</span>';
  }
  function ahChip(sym, hist, now) {
    var j = J(); if (!j || !j.sessionAt || !j.lastCloseMs) return '';
    var s = j.sessionAt(now); if (s === 'regular') return '';
    var close = j.lastCloseMs(now), cp = priceAt(hist, close); if (!cp) return '';
    var r = range(hist, close + 1, close + 4 * 3600e3); if (!r) return '';
    var up = r.hi / cp - 1, dn = r.lo / cp - 1, peak = Math.abs(up) >= Math.abs(dn) ? up : dn;
    if (Math.abs(peak) < 0.01) return '';
    var lastAh = priceAt(hist, Math.min(now, close + 4 * 3600e3)), nowv = lastAh / cp - 1;
    var faded = Math.abs(nowv) < Math.abs(peak) * 0.6;
    return '<span class="pl-chip ' + (faded ? 'pl-a' : 'pl-b') + '" title="After-hours (4-8 pm) move vs the 4 pm close: its biggest point and where after-hours ended (or is now). ' +
      (faded ? 'It has given back much of the move.' : 'It is mostly holding the move.') + '">AH PEAK ' + pc(peak * 100) + ' · ' + (s === 'after' ? 'now ' : 'ended ') + pc(nowv * 100) + '</span>';
  }
  function staleChip(sym, hist, now) {
    var j = J(); if (!j || !j.sessionAt) return '';
    var s = j.sessionAt(now); if (s === 'closed') return '';
    if (j.broker === 'webull' && s === 'overnight' && j.state && j.state.noOvernight) return '';
    var t = (j.state.dataTs && j.state.dataTs[sym]) || hist[hist.length - 1].t;
    if (now - t < 5 * 60000) return '';
    return '<span class="pl-chip pl-d" title="No trade has been reported for this stock since then, even though the market is open. Thin stocks often go quiet overnight: the price shown is the last trade, not a current quote.">NO TRADES SINCE ' + esc(etTime(t)) + '</span>';
  }

  function paint() {
    var j = J(); if (!j || !j.state) return;
    var now = Date.now();
    [].forEach.call(document.querySelectorAll('.tile[data-sym], .row[data-sym]'), function (el) {
      var sym = el.dataset.sym; if (!sym || /=F$/.test(sym)) return;
      var hist = j.state.history[sym]; if (!hist || !hist.length) return;
      var box = el.querySelector('.plus');
      if (!box) {
        box = document.createElement('div'); box.className = 'plus';
        var foot = el.classList.contains('tile') ? el.querySelector(':scope > .foot') : null;
        var det = el.querySelector('.detail');
        if (foot) el.insertBefore(box, foot); else if (det) det.appendChild(box); else return;
      }
      var html = (j.broker === 'ibkr' ? spreadChip(sym) : '') + ahChip(sym, hist, now) + staleChip(sym, hist, now) + mdChips(sym, hist, now);
      if (box._h !== html) { box._h = html; box.innerHTML = html; }
    });
    exchLine(now);
    venueNote();
  }

  // ------------------------------------------------------------------ session badge line (Dec 6, 2026)
  function exchLine(now) {
    var b = document.getElementById('sessBadge'); if (!b) return;
    var el = document.getElementById('plExch');
    if (!el) { el = document.createElement('div'); el.id = 'plExch'; b.parentNode.insertBefore(el, b.nextSibling); }
    var j = J(), s = j && j.sessionAt ? j.sessionAt(now) : '', p = etParts(now), m = p.hh * 60 + p.mm, txt = '', tip = '';
    if (now < EXCH_START) {
      if (s === 'overnight' || s === 'after') { txt = 'Nasdaq & NYSE overnight session starts Sun Dec 6'; tip = 'From Sunday Dec 6, 2026, Nasdaq and NYSE trade 9 pm to 4 am ET themselves (pause 8-9 pm). Until then overnight trades happen on side venues, which IBKR reaches through its OVERNIGHT route (8 pm - 4 am).'; }
    } else if (s === 'overnight') {
      if (m >= 1200 && m < 1260) { txt = 'EXCHANGE PAUSE 8-9 pm · Nasdaq/NYSE overnight opens 9 pm'; tip = 'Nasdaq and NYSE pause 8-9 pm ET every trading night. Side venues (IBKR OVERNIGHT) may still trade from 8 pm.'; }
      else { txt = 'NASDAQ/NYSE OVERNIGHT OPEN · 9 pm - 4 am'; tip = 'The exchanges\' own overnight session. Limit orders, 20% price bands. Trades 9 pm-midnight carry the next day\'s date.'; }
    } else if (s === 'after' && m >= 1140) { txt = 'Exchanges pause 8-9 pm, overnight opens 9 pm'; }
    el.textContent = txt; el.title = tip; el.style.display = txt ? '' : 'none';
  }
  function venueNote() {
    var n = document.getElementById('plVenue');
    if (!n) {
      var hdr = document.querySelector('header'); if (!hdr) return;
      n = document.createElement('div'); n.id = 'plVenue';
      n.textContent = 'Overnight prices have stopped for every stock at once while futures keep moving: the overnight venue may be down. Don\'t trust frozen overnight prices; orders may not fill.';
      hdr.parentNode.insertBefore(n, hdr.nextSibling);
    }
    n.style.display = venueDown && J() && J().broker === 'ibkr' ? 'block' : 'none';
  }

  // ------------------------------------------------------------------ chart hooks (chart.js calls these)
  window.__jbPlusRefs = function (refs, now, sym, ind) {
    var j = J(); if (!j || !j.state) return;
    var hist = j.state.history[sym] || []; if (!hist.length) return;
    var p = etParts(now), today = etMs(p, 0, 0);
    if (ind.pmhl) {
      // the latest pre-market (4:00-9:30 am) that has started
      var d = p.hh < 4 ? today - 864e5 : today, tries = 0, r = null;
      while (tries++ < 6) { var dp = etParts(d + 12 * 3600e3); r = range(hist, etMs(dp, 4, 0), etMs(dp, 9, 30)); if (r) break; d -= 864e5; }
      if (r) { refs.push({ p: r.hi, c: '#5dd6c0', l: 'pre-mkt H', x: 300 }); refs.push({ p: r.lo, c: '#5dd6c0', l: 'pre-mkt L', x: 300 }); }
    }
    if (ind.ahhl && j.lastCloseMs) {
      var c = j.lastCloseMs(now), a = range(hist, c + 1, c + 4 * 3600e3);
      if (a) { refs.push({ p: a.hi, c: '#ff9ecb', l: 'after-hrs H', x: 430 }); refs.push({ p: a.lo, c: '#ff9ecb', l: 'after-hrs L', x: 430 }); }
    }
  };
  window.__jbPlusShade = function (ctx, X, t0, t1, y0, y1, ind) {
    if (!ind.busy) return;
    // NYSE research: overnight volume peaks around 9 pm (Asia opens) and 3 am (Asia closes)
    ctx.save(); ctx.fillStyle = 'rgba(179,157,255,.13)';
    for (var d = t0 - 864e5; d < t1 + 864e5; d += 864e5) {
      var p = etParts(d);
      [[21, 0, 22, 0], [3, 0, 4, 0]].forEach(function (w) {
        var a = Math.max(t0, etMs(p, w[0], w[1])), b = Math.min(t1, etMs(p, w[2], w[3]));
        if (b > a) ctx.fillRect(X(a), y0, X(b) - X(a), y1 - y0);
      });
    }
    ctx.restore();
  };

  // tapping a phone alert opens <board>#chart=SYM: open that stock's chart once its prices are in
  (function openFromLink() {
    var m = /[#&]chart=([^&]+)/.exec(location.hash || ''); if (!m) return;
    var sym = decodeURIComponent(m[1]).toUpperCase(), tries = 0;
    var iv = setInterval(function () {
      var j = J();
      if (++tries > 40) { clearInterval(iv); return; }
      if (!j || !j.state || !(j.state.history[sym] || (j.extra && j.extra.history[sym]))) return;
      clearInterval(iv);
      var fake = document.createElement('div'); fake.className = 'tile'; fake.dataset.sym = sym; fake.style.display = 'none';
      document.body.appendChild(fake); fake.click(); document.body.removeChild(fake);   // chart.js opens the chart on a tile click
      try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
    }, 500);
  })();


  // ------------------------------------------------------------------ chart: your levels and the order ticket (jb-ticket)
  var LV = {}, TK = { pos: [], open: [] }, scale = null, mode = '', draft = null, tkHandle = null, plusMods = [];
  function chartSym() { var w = document.getElementById('jbcWrap'), s = document.getElementById('jbcSym'); return w && w.classList.contains('open') && s ? s.textContent : ''; }
  function pj(p, data) {
    return fetch('/api/plus/' + p, data ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); });
  }
  function loadLevels() { if (plusMods.indexOf('levels') < 0 || document.hidden) return; pj('levels').then(function (d) { LV = d.levels || {}; panel(); }).catch(function () {}); }
  function loadTk() { if (plusMods.indexOf('ticket') < 0 || document.hidden) return; pj('ticket/positions').then(function (d) { TK = d; }).catch(function () {}); }
  window.__jbPlusScale = function (sym, lo, hi, y0, y1) { scale = { sym: sym, lo: lo, hi: hi, y0: y0, y1: y1 }; };
  function fmt(p) { return p >= 1 ? p.toFixed(2) : p.toFixed(4); }
  var oldRefs = window.__jbPlusRefs;
  window.__jbPlusRefs = function (refs, now, sym, ind) {
    if (oldRefs) oldRefs(refs, now, sym, ind);
    (LV[sym] || []).forEach(function (l) { refs.push({ p: l.price, c: '#ffd166', l: (l.alert ? '🔔 ' : '') + (l.label || 'level'), x: 40 }); });
    var hasCost = refs.some(function (r) { return r.l === 'your cost'; });
    (TK.positions || []).forEach(function (o) { if (o.sym === sym && !hasCost) refs.push({ p: o.price, c: '#ff8bd1', l: 'your cost (' + o.broker + ')', x: 300 }); });
    (TK.open || []).forEach(function (o) { if (o.sym === sym) refs.push({ p: o.limit, c: o.side === 'buy' ? '#5dff8f' : '#ff8a80', l: 'open ' + o.side + ' ' + o.qty, x: 430 }); });
    if (draft && draft.sym === sym && draft.limit) refs.push({ p: draft.limit, c: draft.side === 'buy' ? '#5dff8f' : '#ff8a80', l: 'your ' + draft.side + ' limit', x: 160 });
  };
  function priceAtY(y) { if (!scale || !(scale.hi > scale.lo)) return null; var p = scale.lo + (scale.y1 - y) / (scale.y1 - scale.y0) * (scale.hi - scale.lo); return p > 0 ? p : null; }
  var ROUND = function (p) { return p >= 1 ? Math.round(p * 100) / 100 : Math.round(p * 10000) / 10000; };
  function setupChart() {
    var news = document.getElementById('jbcNews'), cv = document.getElementById('jbcCanvas');
    if (!news || !cv || document.getElementById('plTrade')) return !!document.getElementById('plTrade');
    var st2 = document.createElement('style');
    st2.textContent = '#plTrade{margin-top:6px;font-size:12px}#plTrade .pl-bar{display:flex;gap:6px;flex-wrap:wrap;align-items:center}' +
      '#plTrade .pl-bar button{background:transparent;border:1px solid #164060;color:#9fb3c8;font:inherit;font-size:11px;font-weight:700;padding:5px 10px;cursor:pointer;border-radius:3px}' +
      '#plTrade .pl-bar button.on{border-color:#ffd166;color:#ffd166}#plTrade .pl-bar button.tr.on{border-color:#5dff8f;color:#5dff8f}' +
      '#plTrade .pl-hint{color:#6f8aa3;font-size:11px}#plTrade .pl-body{margin-top:6px}#plTrade .pl-lv{display:flex;gap:8px;align-items:center;padding:4px 0;border-bottom:1px solid rgba(127,147,168,.15)}' +
      '#plTrade .pl-lv b{color:#ffd166;min-width:70px}#plTrade .pl-lv button,#plTrade .pl-add button{background:#0b2238;border:1px solid #164060;color:#cfe0ee;font:inherit;font-size:11px;padding:3px 8px;border-radius:3px;cursor:pointer}' +
      '#plTrade input{background:#000;color:#f2f2f2;border:1px solid #164060;border-radius:3px;font:inherit;font-size:13px;padding:4px 6px}';
    document.head.appendChild(st2);
    var box = document.createElement('div'); box.id = 'plTrade';
    box.innerHTML = '<div class="pl-bar">' + (plusMods.indexOf('levels') >= 0 ? '<button type="button" class="lv">LEVELS</button>' : '') +
      (plusMods.indexOf('ticket') >= 0 ? '<button type="button" class="tr">TRADE</button>' : '') + '<span class="pl-hint"></span></div><div class="pl-body"></div>';
    news.parentNode.insertBefore(box, news);
    var bl = box.querySelector('.lv'), bt = box.querySelector('.tr');
    if (bl) bl.onclick = function () { mode = mode === 'level' ? '' : 'level'; panel(); };
    if (bt) bt.onclick = function () { mode = mode === 'trade' ? '' : 'trade'; panel(); };
    cv.addEventListener('click', function (e) {
      if (!mode) return;
      var r = cv.getBoundingClientRect(), p = priceAtY(e.clientY - r.top), sym = chartSym();
      if (!p || !sym || scale.sym !== sym) return;
      if (mode === 'level') pj('levels/add', { sym: sym, price: ROUND(p) }).then(function (d) { LV = d.levels || {}; panel(); }).catch(function () {});
      else if (tkHandle) tkHandle.setPrice(p);
    });
    return true;
  }
  var lastSym = '';
  function panel() {
    var box = document.getElementById('plTrade'); if (!box) return;
    var sym = chartSym(); if (!sym) { mode = ''; draft = null; lastSym = ''; return; }
    var bl = box.querySelector('.lv'), bt = box.querySelector('.tr'), hint = box.querySelector('.pl-hint'), body = box.querySelector('.pl-body');
    if (bl) bl.classList.toggle('on', mode === 'level'); if (bt) bt.classList.toggle('on', mode === 'trade');
    hint.textContent = mode === 'level' ? 'Tap the chart to add a level at that price.' : mode === 'trade' ? 'Tap the chart to use that price as your limit.' : '';
    if (mode === 'level') {
      var lv = (LV[sym] || []).slice().sort(function (a, b) { return b.price - a.price; });
      var key = 'L' + sym + JSON.stringify(lv);
      if (body.dataset.k === key) return; body.dataset.k = key;
      body.innerHTML = lv.map(function (l) {
        return '<div class="pl-lv"><b>' + fmt(l.price) + '</b><span style="flex:1;color:#9fb3c8">' + esc(l.label || '') + '</span>' +
          '<button data-al="' + l.id + '" data-on="' + (l.alert ? 1 : 0) + '" title="Push to your phone when the price crosses this level (once)">' + (l.alert ? '🔔 alert on' : 'alert off') + '</button>' +
          (plusMods.indexOf('ticket') >= 0 ? '<button data-tr="' + l.price + '">trade here</button>' : '') + '<button data-rm="' + l.id + '">×</button></div>';
      }).join('') + '<div class="pl-add pl-lv"><input class="pl-p" type="number" step="0.01" inputmode="decimal" placeholder="price" style="width:90px"><input class="pl-l" placeholder="name (optional)" style="width:130px"><button class="pl-go">Add level</button></div>' +
        '<div class="pl-hint">Levels are saved on your Mac, so they stay on this chart (board and phone) until you remove them.</div>';
      [].forEach.call(body.querySelectorAll('[data-rm]'), function (b) { b.onclick = function () { pj('levels/remove', { id: b.dataset.rm }).then(function (d) { LV = d.levels || {}; panel(); }); }; });
      [].forEach.call(body.querySelectorAll('[data-al]'), function (b) { b.onclick = function () { pj('levels/alert', { id: b.dataset.al, alert: b.dataset.on !== '1' }).then(function (d) { LV = d.levels || {}; panel(); }); }; });
      [].forEach.call(body.querySelectorAll('[data-tr]'), function (b) { b.onclick = function () { var p = +b.dataset.tr; mode = 'trade'; panel(); if (tkHandle) tkHandle.setPrice(p); }; });
      body.querySelector('.pl-go').onclick = function () {
        var p = +body.querySelector('.pl-p').value; if (!p) return;
        pj('levels/add', { sym: sym, price: p, label: body.querySelector('.pl-l').value }).then(function (d) { LV = d.levels || {}; panel(); });
      };
    } else if (mode === 'trade') {
      if (body.dataset.k === 'T') { if (tkHandle && lastSym !== sym) tkHandle.setSym(sym); lastSym = sym; return; }
      body.dataset.k = 'T'; body.innerHTML = '<div class="pl-tk">Loading…</div>';
      var go = function () {
        tkHandle = window.JBTicket.mount(body.querySelector('.pl-tk'), { sym: sym, compact: true, onChange: function (s) { draft = s; } });
        draft = tkHandle.state(); lastSym = sym;
      };
      if (window.JBTicket) go(); else { var s = document.createElement('script'); s.src = 'ticket-ui.js'; s.onload = go; document.head.appendChild(s); }
    } else { body.innerHTML = ''; body.dataset.k = ''; draft = null; tkHandle = null; }
  }
  pj('').then(function (d) {
    plusMods = d.modules || [];
    if (plusMods.indexOf('levels') < 0 && plusMods.indexOf('ticket') < 0) return;
    loadLevels(); loadTk();
    setInterval(loadLevels, 20000); setInterval(loadTk, 30000);
    setInterval(function () { if (setupChart()) panel(); }, 700);
  }).catch(function () {});

  loadQuotes(); setInterval(loadQuotes, 5000);
  setTimeout(loadMdata, 4000); setInterval(loadMdata, 60000);
  setInterval(paint, 2000);
})();
