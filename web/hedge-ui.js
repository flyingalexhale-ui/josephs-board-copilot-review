/* Alex Hale's Board -- delta hedging (jb-hedge). Read only: nothing here places an order.
 *   Net Exposure   your total market delta across IBKR, Webull and your typed-in trades, as dollars gained or lost
 *                  per 1% move in the Nasdaq-100 and the S&P 500 (leverage x beta from the board's own prices).
 *                  On the Portfolio tab (above Positions), the My Trading tab (under the menu) and the phone page
 *                  (a card under Your Holdings).
 *   Hedge Calculator   how many shares of an inverse fund (or MNQ / MES micro futures, IBKR only) bring you to about
 *                  flat, with cost, decay and how much of your swings it cut on the board's record. Copy To Ticket
 *                  fills the order ticket (nothing is sent until Check, then Send); futures get their order text to
 *                  copy (the ticket takes stocks and ETFs only). Portfolio tab (under Positions) and the phone card.
 *   Hedge Partners     per holding, the best hedge partner (inverse twin, sector inverse, index inverse, micro
 *                  future) with its record on the board: correlation, daily and overnight swing cut, decay. Portfolio.
 *   Hedge Ideas        RESEARCH, NOT ADVICE: pair ideas ranked by a backtest on the board's own minute and daily
 *                  record, with win rate, worst drop and results per session; Send To Run Backtest writes the idea
 *                  as a strategy file and starts that algo's backtest. My Trading tab, above the replay.
 * Data: /api/plus/hedge, /api/plus/hedge/calc, /api/plus/hedge/pairs, /api/plus/hedge/ideas (gateway/hedge.py). Hooked in by one <script src="hedge-ui.js"> line on portfolio.html,
 * trading.html and phone.html. */
(function () {
  'use strict';
  if (window.__jbHedge) return;
  window.__jbHedge = true;
  var page = (location.pathname.split('/').pop() || '').replace('.html', '') || 'index';
  if (['portfolio', 'trading', 'phone'].indexOf(page) < 0) return;
  var phone = page === 'phone';

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }
  function usd(v, sign) {
    if (v == null) return '—';
    var a = Math.abs(v), t = '$' + a.toLocaleString('en-US', { maximumFractionDigits: a < 100 ? 2 : 0 });
    return (v < 0 ? '-' : sign ? '+' : '') + t;
  }
  function px(v) { return v == null ? '—' : '$' + (+v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: v < 1 ? 4 : 2 }); }
  function cls(v, flat) { return v == null || Math.abs(v) <= (flat || 0) ? 'hx-flat' : v > 0 ? 'hx-up' : 'hx-dn'; }
  function api(p, data) {
    return fetch('/api/plus/hedge' + p, data ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status)); return j; });
    });
  }
  window.JBHedge = { api: api, esc: esc, usd: usd, cls: cls, page: page };

  var css =
    '.hx{background:#071a30;border:1px solid #164060;border-radius:8px;padding:8px 12px;margin:8px 0 10px;font-size:12.5px;line-height:1.5;color:#cfd8e3;max-width:100%;overflow-wrap:anywhere;}' +
    '.hx .hx-k{font-size:10.5px;letter-spacing:.1em;color:#6f8aa3;font-weight:700;margin-right:8px;}' +
    '.hx .hx-main{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 12px;}' +
    '.hx .hx-big{font-size:16px;font-weight:700;} .hx .hx-sub{font-size:11.5px;color:#9fb3c8;margin-top:2px;}' +
    '.hx-up{color:#00ff41;} .hx-dn{color:#ff6b61;} .hx-flat{color:#9fb3c8;}' +
    '.hx button{font:inherit;font-size:11.5px;font-weight:600;padding:4px 10px;border-radius:6px;border:1px solid #2a3d52;background:#0b2238;color:#cfe0ee;cursor:pointer;}' +
    '.hx .hx-rows{margin-top:8px;border-top:1px solid #0f2c47;}' +
    '.hx .hx-r{display:flex;flex-wrap:wrap;gap:0 10px;padding:5px 0;border-bottom:1px solid #0f2c47;font-size:12px;}' +
    '.hx .hx-r b{color:#fff;min-width:64px;} .hx .hx-r .hx-n{flex-basis:100%;font-size:11px;color:#6f8aa3;}' +
    '.hx .hx-src{display:inline-block;padding:0 6px;border-radius:9px;font-size:10px;font-weight:600;background:rgba(92,200,219,.14);color:#5cc8db;}' +
    '.hx .hx-est{color:#ffb648;} .hx .hx-err{color:#ff8a80;}' +
    '.hx .hx-ctl{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin:2px 0 8px;}' +
    '.hx .hx-seg{display:inline-flex;border:1px solid #2a3d52;border-radius:6px;overflow:hidden;}' +
    '.hx .hx-seg button{border:0;border-radius:0;margin:0;background:transparent;color:#9fb3c8;}' +
    '.hx .hx-seg button+button{border-left:1px solid #2a3d52;} .hx .hx-seg button.on{background:#164060;color:#fff;}' +
    '.hx .hx-lbl{font-size:10.5px;color:#6f8aa3;letter-spacing:.06em;}' +
    '.hx .hx-opt{padding:8px 0;border-top:1px solid #0f2c47;} .hx .hx-opt .hx-h{font-size:13.5px;font-weight:700;color:#fff;}' +
    '.hx .hx-opt .hx-d{font-size:11.5px;color:#9fb3c8;margin:2px 0 4px;} .hx .hx-opt .hx-n{font-size:10.5px;color:#6f8aa3;}' +
    '.hx .hx-best{display:inline-block;padding:0 6px;border-radius:3px;font-size:10px;font-weight:700;background:#0e3a1c;color:#7fdc8f;margin-right:6px;vertical-align:1px;}' +
    '.hx .hx-go{background:#1e5631;border-color:#2f7a44;color:#fff;} .hx textarea{width:100%;font:inherit;font-size:12px;background:#000;color:#fff;border:1px solid #2a3d52;border-radius:6px;padding:6px;}' +
    '.hx .hx-foot{font-size:10.5px;color:#6f8aa3;margin-top:6px;line-height:1.5;} .hx .hx-more{margin:4px 0 2px;}' +
    '.hx .hx-warn{border:1px solid #7a5c00;background:#2e2400;color:#ffe9a6;border-radius:6px;padding:6px 10px;margin:0 0 8px;font-weight:700;}' +
    '.hx .hx-rank{display:inline-block;min-width:26px;color:#6f8aa3;font-weight:700;}' +
    '.hx .hx-ses{font-size:11.5px;color:#cfd8e3;padding:2px 0 2px 26px;} .hx .hx-ses b{color:#fff;font-weight:600;}' +
    (phone ? '.card .hx{border:0;padding:0;margin:0;background:transparent;}' : '');
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  // ---------------------------------------------------------------- where the line goes on each page
  function mount() {
    var box = document.createElement('div');
    box.className = 'hx'; box.id = 'hxExpo';
    box.innerHTML = '<span class="hx-k">NET EXPOSURE</span><span class="hx-flat">Loading…</span>';
    if (page === 'portfolio') {
      var pos = document.getElementById('pos');
      var sc = pos && pos.closest('.scroll');
      if (!sc) return null;
      sc.parentNode.insertBefore(box, sc);
    } else if (page === 'trading') {
      var nav = document.querySelector('.wrap nav');
      if (!nav) return null;
      nav.parentNode.insertBefore(box, nav.nextSibling);
    } else {
      var c = document.createElement('div'); c.className = 'card'; c.id = 'hxCard';
      c.innerHTML = '<h2>Net Exposure <span>Per 1% Move</span></h2>';
      c.appendChild(box);
      var held = document.getElementById('heldc'), trd = document.getElementById('trd');
      var at = held || (trd && trd.closest('.card'));
      if (at) at.parentNode.insertBefore(c, at.nextSibling); else document.body.appendChild(c);
    }
    return box;
  }
  var box = mount();
  if (!box) return;
  var open = false, last = null;

  function draw(d) {
    last = d;
    var t = d.total || {}, rows = d.rows || [], g = d.gross || {};
    var flat = Math.max(20, ((g.long || 0) - (g.short || 0)) * 0.05);
    var word = Math.abs(t.ndx || 0) <= flat ? 'About Flat' : t.ndx > 0 ? 'Net Long' : 'Net Short';
    var src = Object.keys(d.by_src || {}).sort().map(function (k) { return esc(k) + ' ' + usd(d.by_src[k].ndx, true); }).join(' · ');
    box.innerHTML = '<div class="hx-main">' + (phone ? '' : '<span class="hx-k">NET EXPOSURE</span>') +
      (rows.length ? '<span class="hx-big ' + cls(t.ndx, flat) + '">' + usd(t.ndx, true) + '</span><span>Per 1% Nasdaq-100 Move</span>' +
        '<span class="hx-big ' + cls(t.spx, flat) + '">' + usd(t.spx, true) + '</span><span>Per 1% S&amp;P 500 Move</span>' +
        '<span class="' + cls(t.ndx, flat) + '" style="font-weight:700">' + word + '</span>'
        : '<span class="hx-flat">Nothing Held Right Now</span>') +
      (rows.length ? ' <button type="button" class="hx-tog">' + (open ? 'Hide Details' : 'Details') + '</button>' : '') + '</div>' +
      (rows.length ? '<div class="hx-sub">Like Holding ' + usd(Math.abs(d.like.ndx)) + ' Of QQQ' + (d.like.ndx < 0 ? ' Short' : '') +
        (src ? ' · ' + src : '') + (d.missing && d.missing.length ? ' · <span class="hx-err">No Price: ' + esc(d.missing.join(', ')) + '</span>' : '') +
        (d.estimated && d.estimated.length ? ' · <span class="hx-est">Estimated Beta: ' + esc(d.estimated.join(', ')) + '</span>' : '') + '</div>' : '') +
      (open && rows.length ? '<div class="hx-rows">' + rows.map(function (r) {
        return '<div class="hx-r"><b>' + esc(r.sym) + '</b><span class="hx-src">' + esc(r.src) + '</span>' +
          '<span>' + (r.kind === 'future' ? (+r.shares) + ' Contract' + (Math.abs(r.shares) === 1 ? '' : 's') : (+r.shares).toLocaleString() + ' Sh') +
          ' · ' + usd(r.value) + '</span><span>× ' + (r.beta_ndx != null ? r.beta_ndx.toFixed(2) : '—') + '</span>' +
          '<span class="' + cls(r.d_ndx) + '">' + usd(r.d_ndx, true) + ' NDX</span><span class="' + cls(r.d_spx) + '">' + usd(r.d_spx, true) + ' SPX</span>' +
          '<span class="hx-n' + (r.est_ndx ? ' hx-est' : '') + '">' + esc(r.note_ndx) + (r.price_src && r.price_src !== 'board' ? ' · Price From ' + esc(r.price_src === 'yahoo' ? 'Yahoo' : 'Avg Cost') : '') + '</span></div>';
      }).join('') + '<div class="hx-r hx-n" style="border:0">Each Row: Value × Leverage × Beta To QQQ (Or SPY) ÷ 100. Index Stand-Ins: ' +
        esc(d.proxies.ndx) + ', ' + esc(d.proxies.spx) + '. Read Only: Nothing Is Sent To A Broker.</div></div>' : '');
    var tg = box.querySelector('.hx-tog');
    if (tg) tg.onclick = function () { open = !open; draw(last); };
    if (window.JBHedge.onExposure) window.JBHedge.onExposure(d);
  }
  function load() {
    if (document.hidden) return;
    api('').then(draw).catch(function (e) { box.innerHTML = '<span class="hx-k">NET EXPOSURE</span><span class="hx-err">Not Available: ' + esc(e.message) + '</span>'; });
  }

  // ---------------------------------------------------------------- hedge calculator (Portfolio tab, phone card)
  function calcBox() {
    if (page === 'portfolio') {
      var sc = document.getElementById('pos'); sc = sc && sc.closest('.scroll');
      if (!sc) return null;
      var h = document.createElement('h2'); h.textContent = 'Hedge Calculator'; h.id = 'hxCalcH';
      var b = document.createElement('div'); b.className = 'hx'; b.id = 'hxCalc';
      sc.parentNode.insertBefore(h, sc.nextSibling); sc.parentNode.insertBefore(b, h.nextSibling);
      return b;
    }
    if (page === 'phone') {
      var card = document.getElementById('hxCard'); if (!card) return null;
      var t = document.createElement('div'); t.style.marginTop = '8px';
      t.className = 'hx'; t.style.cssText = 'margin-top:8px;padding:0;border:0;background:transparent';
      t.innerHTML = '<button type="button" id="hxCalcTog">Hedge Calculator ▸</button>';
      var b2 = document.createElement('div'); b2.className = 'hx'; b2.id = 'hxCalc'; b2.style.display = 'none';
      card.appendChild(t); card.appendChild(b2);
      t.querySelector('button').onclick = function () {
        var on = b2.style.display === 'none'; b2.style.display = on ? '' : 'none';
        this.textContent = on ? 'Hedge Calculator ▾' : 'Hedge Calculator ▸'; if (on) loadCalc();
      };
      return b2;
    }
    return null;
  }
  var cbox = calcBox();
  var cs = { idx: 'ndx', pct: 100, broker: null }, cdata = null;
  try { cs.broker = localStorage.getItem('tk_broker'); } catch (e) {}
  function segHtml(key, opts) {
    return '<span class="hx-seg" data-k="' + key + '">' + opts.map(function (o) {
      return '<button type="button" data-v="' + o[0] + '" class="' + (String(cs[key]) === String(o[0]) ? 'on' : '') + '">' + o[1] + '</button>';
    }).join('') + '</span>';
  }
  function optHtml(o, i) {
    if (!o.units) return '<div class="hx-opt"><div class="hx-h" style="color:#9fb3c8">' + esc(o.sym) + (o.name ? ' · ' + esc(o.name) : '') + '</div><div class="hx-d">' + esc(o.text || '') + '</div></div>';
    var fut = o.kind === 'future', side = o.side === 'buy' ? 'Buy' : 'Sell';
    var cut = o.cut != null ? 'Cuts Swings ' + Math.round(o.cut) + '%' + (o.corr != null ? ' (Corr ' + o.corr.toFixed(2) + ')' : '') : 'Swing Cut: Not Enough Shared History';
    return '<div class="hx-opt"><div class="hx-h">' + (o.best ? '<span class="hx-best">BEST MATCH</span>' : '') + side + ' ' + o.units.toLocaleString() + ' ' + esc(o.sym) +
      (fut ? ' <span class="hx-lbl">' + esc(o.name) + ' · IBKR Only</span>' : '') + ' <span class="hx-lbl">@ ' + px(o.price) + '</span></div>' +
      '<div class="hx-d">' + (fut ? 'Controls ' + usd(o.cost.controls) + ' · Margin About ' + usd(o.cost.margin[0]) + '-' + usd(o.cost.margin[1]) + ' (Estimate: Check IBKR)'
        : 'Uses ' + usd(o.cost.buying_power) + ' Buying Power') + ' · Leaves ' + usd(o.net_after, true) + ' Per 1% · ' + cut + ' · ' +
        (o.decay_day ? 'Decay About ' + usd(o.decay_day) + '/Day' : 'No Daily Decay') + (o.est ? ' · <span class="hx-est">Estimated Beta</span>' : '') + '</div>' +
      '<div class="hx-n">' + esc(o.decay_note) + '. ' + esc(o.hours) + '.</div>' +
      '<div style="margin-top:4px"><button type="button" class="hx-go" data-i="' + i + '">' + (fut ? 'Copy Order Text' : 'Copy To Ticket') + '</button> <span class="hx-msg" data-m="' + i + '"></span></div></div>';
  }
  function drawCalc(c) {
    cdata = c;
    if (!cs.broker) {
      var bs = (last && last.by_src) || {};
      cs.broker = Math.abs((bs.Webull || {}).ndx || 0) > Math.abs((bs.IBKR || {}).ndx || 0) ? 'webull' : 'ibkr';
    }
    cbox.innerHTML = '<div class="hx-ctl">' + segHtml('idx', [['ndx', 'Nasdaq-100'], ['spx', 'S&amp;P 500']]) +
      '<span class="hx-lbl">HEDGE</span>' + segHtml('pct', [[25, '25%'], [50, '50%'], [75, '75%'], [100, '100%']]) +
      '<span class="hx-lbl">TICKET</span>' + segHtml('broker', [['ibkr', 'IBKR'], ['webull', 'Webull']]) + '</div>' +
      '<div style="color:#cfd8e3">' + esc(c.text || '') + '</div>' +
      (c.options || []).map(optHtml).join('') + '<div id="hxTk"></div>' +
      '<div class="hx-foot">Copy To Ticket Only Fills The Order Ticket: Nothing Is Sent Until You Tap Check Order, Then The Red Send. ' +
      'Swing Cut = How Much Smaller Your Half-Hour Ups And Downs Would Have Been With This Hedge, On The Board\'s Own Record (Regular Hours). ' +
      'Decay And Margin Are Estimates. Research, Not Advice.</div>';
    [].forEach.call(cbox.querySelectorAll('.hx-seg button'), function (b) {
      b.onclick = function () {
        var k = b.parentNode.dataset.k; cs[k] = k === 'pct' ? +b.dataset.v : b.dataset.v;
        if (k === 'broker') { try { localStorage.setItem('tk_broker', cs.broker); } catch (e) {} drawCalc(cdata); } else loadCalc();
      };
    });
    [].forEach.call(cbox.querySelectorAll('.hx-go'), function (b) { b.onclick = function () { copyOpt(c.options[+b.dataset.i], +b.dataset.i); }; });
  }
  // Copy To Ticket: fill the ticket already on the page (phone), else open one in `box`. Nothing is sent from here.
  function copyText(text, m) {
    var done = function () { m.innerHTML = 'Copied: Paste It Into The IBKR App Or TWS.'; };
    var show = function () { m.innerHTML = '<textarea rows="3" readonly>' + esc(text) + '</textarea>Select And Copy This Into IBKR.'; var t = m.querySelector('textarea'); if (t) t.select(); };
    try { navigator.clipboard.writeText(text).then(done, show); } catch (e) { show(); }
  }
  function toTicket(fill, m, box) {
    var t = window.JBTicket && window.JBTicket.last;
    var said = function () { m.innerHTML = 'Ticket Filled: Tap Check Order, Then Send.'; };
    if (t && t.el && document.body.contains(t.el)) { t.fill(fill); t.el.scrollIntoView({ behavior: 'smooth', block: 'center' }); said(); return; }
    var go = function () { var h = window.JBTicket.mount(box, { compact: true }); h.fill(fill); box.scrollIntoView({ behavior: 'smooth', block: 'center' }); said(); };
    if (window.JBTicket) go(); else { var sc = document.createElement('script'); sc.src = 'ticket-ui.js'; sc.onload = go; document.head.appendChild(sc); }
  }
  window.JBHedge.copyText = copyText; window.JBHedge.toTicket = toTicket;
  function copyOpt(o, i) {
    var m = cbox.querySelector('[data-m="' + i + '"]');
    if (o.kind === 'future') { copyText(o.copy, m); return; }
    toTicket(Object.assign({ broker: cs.broker, note: 'Filled By The Hedge Calculator: Check It, Then Send.' }, o.ticket), m, cbox.querySelector('#hxTk'));
  }
  function loadCalc() {
    if (!cbox || document.hidden || (page === 'phone' && cbox.style.display === 'none')) return;
    api('/calc?idx=' + cs.idx + '&pct=' + cs.pct).then(drawCalc).catch(function (e) { cbox.innerHTML = '<span class="hx-err">Hedge Calculator Not Available: ' + esc(e.message) + '</span>'; });
  }
  if (cbox) {
    window.JBHedge.onExposure = function () { if (!cbox.querySelector('#hxTk') || !cbox.querySelector('#hxTk').children.length) loadCalc(); };
    loadCalc();
  }

  // ---------------------------------------------------------------- hedge partners (Portfolio tab)
  var pbox = null;
  if (page === 'portfolio' && cbox) {
    var ph = document.createElement('h2'); ph.textContent = 'Hedge Partners'; ph.id = 'hxPairsH';
    pbox = document.createElement('div'); pbox.className = 'hx'; pbox.id = 'hxPairs';
    cbox.parentNode.insertBefore(ph, cbox.nextSibling); cbox.parentNode.insertBefore(pbox, ph.nextSibling);
  }
  var pOpen = {}, pdata = null;
  function pct(v) { return v == null ? '—' : Math.round(v) + '%'; }
  function partnerHtml(r, p, key) {
    var fut = p.kind === 'future';
    var head = (p.best ? '<span class="hx-best">BEST PARTNER</span>' : '') + '<b style="color:#fff">' + esc(p.sym) + '</b> <span class="hx-lbl">' + esc(p.label) + '</span>';
    if (p.corr == null || !p.units) return '<div class="hx-opt"><div class="hx-h">' + head + '</div><div class="hx-d">' + esc(p.text || 'Not Enough Shared History On The Board Yet') + '</div></div>';
    return '<div class="hx-opt"><div class="hx-h">' + head + ' <span class="hx-lbl">' + (p.side === 'buy' ? 'Buy ' : 'Sell ') + p.units.toLocaleString() + (fut ? ' Contract' + (p.units === 1 ? '' : 's') : ' Sh') + '</span></div>' +
      '<div class="hx-d">Corr ' + p.corr.toFixed(2) + ' · Cuts Daily Swings ' + pct(p.cut_day) + (p.cut_night != null ? ' · Overnight Swings ' + pct(p.cut_night) : ' · Overnight: Not Enough Nights Yet') +
      ' · ' + (p.decay_week ? 'Hedge Decay About ' + usd(p.decay_week) + '/Week' : 'No Decay') + '</div>' +
      '<div class="hx-n">' + Math.abs(p.ratio).toFixed(2) + ' $ Of ' + esc(p.sym) + (fut ? ' (Via ' + esc(p.sym === 'MNQ' ? 'NQ=F' : 'ES=F') + ')' : '') + ' Per $1 Of ' + esc(r.sym) +
      ' · From ' + (p.n_day || 0) + ' ' + esc(p.how === 'half-hours' ? 'Half-Hours' : 'Days') + (p.n_night ? ', ' + p.n_night + ' Nights' : '') + ' On The Board\'s Record' + (p.decay_note && p.decay_week ? ' · ' + esc(p.decay_note) : '') + '</div>' +
      '<div style="margin-top:4px"><button type="button" class="hx-go" data-k="' + key + '">' + (fut ? 'Copy Order Text' : 'Copy To Ticket') + '</button> <span class="hx-msg" data-pm="' + key + '"></span></div></div>';
  }
  function drawPairs(d) {
    pdata = d;
    if (!d.rows.length) { pbox.innerHTML = '<span class="hx-flat">Nothing Held Right Now.</span>'; return; }
    pbox.innerHTML = d.rows.map(function (r, i) {
      var best = r.partners.filter(function (p) { return p.best; }), rest = r.partners.filter(function (p) { return !p.best; });
      return '<div class="hx-opt" style="border-top:1px solid #164060"><div class="hx-h">' + esc(r.sym) + ' <span class="hx-src">' + esc(r.src) + '</span> <span class="hx-lbl">' + usd(r.value) +
        (r.lev !== 1 ? ' · ' + r.lev + 'x ' + esc(r.follows) : '') + (r.own_decay_week ? ' · Its Own Decay About ' + usd(r.own_decay_week) + '/Week' : '') + '</span></div>' +
        (best.length ? best.map(function (p) { return partnerHtml(r, p, i + '-' + r.partners.indexOf(p)); }).join('') : '<div class="hx-d">No Partner With Enough Shared History Yet.</div>') +
        (rest.length ? '<button type="button" class="hx-more" data-r="' + i + '">' + (pOpen[r.sym] ? 'Hide Other Partners' : 'Other Partners (' + rest.length + ')') + '</button>' +
          (pOpen[r.sym] ? rest.map(function (p) { return partnerHtml(r, p, i + '-' + r.partners.indexOf(p)); }).join('') : '') : '') + '</div>';
    }).join('') + '<div id="hxTk2"></div><div class="hx-foot">Best Partner = The One That Cut This Holding\'s Swings The Most On The Board\'s Own Record (Daily Closes, And 4 Pm Close To 9:30 Open For Overnight); ' +
      'Close Calls Go To The Cheaper One To Hold. Futures Are Counted In Whole Contracts. Past Hedging Does Not Promise Future Hedging. Research, Not Advice.</div>';
    [].forEach.call(pbox.querySelectorAll('.hx-more'), function (b) { b.onclick = function () { var r = d.rows[+b.dataset.r]; pOpen[r.sym] = !pOpen[r.sym]; drawPairs(pdata); }; });
    [].forEach.call(pbox.querySelectorAll('.hx-go'), function (b) {
      b.onclick = function () {
        var k = b.dataset.k.split('-'), r = d.rows[+k[0]], p = r.partners[+k[1]], m = pbox.querySelector('[data-pm="' + b.dataset.k + '"]');
        if (p.kind === 'future') {
          copyText(p.side.toUpperCase() + ' ' + p.units + ' ' + p.sym + ' (' + (p.sym === 'MNQ' ? 'Micro Nasdaq-100' : 'Micro S&P 500') + ' Future, Front Month) At IBKR To Hedge ' + r.sym +
            '. The Board\'s Ticket Takes Stocks And ETFs Only: Place This In The IBKR App Or TWS.', m);
          return;
        }
        toTicket({ broker: cs.broker || 'ibkr', side: p.side, sym: p.sym, qty: p.units, limit: p.price, note: 'Filled From Hedge Partners: Check It, Then Send.' }, m, pbox.querySelector('#hxTk2'));
      };
    });
  }
  function loadPairs() {
    if (!pbox || document.hidden) return;
    if (pbox.querySelector('#hxTk2') && pbox.querySelector('#hxTk2').children.length) return;    // keep a ticket being filled in
    api('/pairs').then(drawPairs).catch(function (e) { pbox.innerHTML = '<span class="hx-err">Hedge Partners Not Available: ' + esc(e.message) + '</span>'; });
  }
  if (pbox) { loadPairs(); setInterval(loadPairs, 300000); }

  // ---------------------------------------------------------------- hedge ideas (My Trading tab): research, not advice
  var ibox = null;
  if (page === 'trading') {
    var rp = document.getElementById('replay');
    if (rp) {
      var ih = document.createElement('h2'); ih.id = 'hxIdeasH'; ih.textContent = 'Hedge Ideas (Research)';
      ibox = document.createElement('div'); ibox.className = 'hx'; ibox.id = 'hxIdeas';
      rp.parentNode.insertBefore(ih, rp); rp.parentNode.insertBefore(ibox, rp);
      var nv = document.querySelector('.wrap nav');
      if (nv) { var a = document.createElement('a'); a.href = '#hxIdeasH'; a.textContent = 'Hedge Ideas'; nv.appendChild(a); }
    }
  }
  var iAll = false, iOpen = {}, iSend = {}, iMsgAt = 0;
  var SL = { afterhours: 'After-Hours', overnight: 'Overnight', premarket: 'Pre-Market', day: 'Day Hold (Daily Closes)' };
  function signed(v, d) { return v == null ? '—' : (v > 0 ? '+' : '') + (+v).toFixed(d == null ? 2 : d) + '%'; }
  function sesLine(k, m, best) {
    if (!m || !m.n) return '<div class="hx-ses">' + SL[k] + ': No Trades On The Record</div>';
    return '<div class="hx-ses">' + (best ? '<b>★ ' + SL[k] + '</b>' : SL[k]) + ': ' + m.n + ' Trades · Wins ' + Math.round(m.win) + '% · Avg <span class="' + cls(m.avg) + '">' + signed(m.avg) +
      '</span> · Total <span class="' + cls(m.total) + '">' + signed(m.total, 1) + '</span> · Worst Drop <span class="hx-dn">' + signed(m.mdd, 1) + '</span>' +
      (m.n < 8 ? ' <span class="hx-lbl">(Too Few Trades To Rank)</span>' : '') + '</div>';
  }
  function drawIdeas(d) {
    var list = d.ideas || [], shown = iAll ? list : list.slice(0, 12);
    ibox.innerHTML = '<div class="hx-warn">RESEARCH, NOT ADVICE. These Are Backtests On A Few Weeks Of The Board\'s Own Prices; They Do Not Predict The Future And Nothing Here Places An Order.</div>' +
      '<div class="hx-sub" style="margin-bottom:6px">' + (d.at ? 'Worked Out ' + new Date(d.at).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) +
        ' From ' + (d.days || 0) + ' Days Of The Board\'s Record' + (d.from ? ' (' + esc(d.from) + ' To ' + esc(d.to) + ')' : '') + ' · ' + (d.n_tested || 0) + ' Ideas Tested · Costs: ' + esc(d.costs || '') : 'Not Worked Out Yet.') +
        (d.running ? ' · <b>Working Them Out Now…</b>' : '') + ' <button type="button" id="hxIRun">Run Now</button></div>' +
      (shown.length ? shown.map(function (x, i) {
        var bm = x.sessions[x.best_session] || {}, sel = iSend[x.id] || { algo: x.algos.webull ? 'ibkr' : 'ibkr', session: x.best_session };
        iSend[x.id] = sel;
        return '<div class="hx-opt"><div class="hx-h"><span class="hx-rank">#' + (i + 1) + '</span>' + esc(x.title) + ' <span class="hx-lbl">' + esc(x.label) + (x.kind === 'future' ? ' · IBKR Only' : '') + '</span></div>' +
          sesLine(x.best_session, bm, true) +
          (iOpen[x.id] ? ['afterhours', 'overnight', 'premarket', 'day'].filter(function (k) { return k !== x.best_session && x.sessions[k]; }).map(function (k) { return sesLine(k, x.sessions[k], false); }).join('') : '') +
          '<div class="hx-ctl" style="margin:4px 0 0 26px"><button type="button" class="hx-iopen" data-id="' + esc(x.id) + '">' + (iOpen[x.id] ? 'Hide Other Sessions' : 'All Sessions') + '</button>' +
          '<span class="hx-lbl">SEND TO</span><span class="hx-seg" data-id="' + esc(x.id) + '" data-k="algo"><button type="button" data-v="ibkr" class="' + (sel.algo === 'ibkr' ? 'on' : '') + '">IBKR Algo</button>' +
          (x.algos.webull ? '<button type="button" data-v="webull" class="' + (sel.algo === 'webull' ? 'on' : '') + '">Webull Algo</button>' : '') + '</span>' +
          '<span class="hx-seg" data-id="' + esc(x.id) + '" data-k="session">' + ['afterhours', 'overnight', 'premarket'].map(function (k) {
            return '<button type="button" data-v="' + k + '" class="' + (sel.session === k ? 'on' : '') + '">' + SL[k] + '</button>'; }).join('') + '</span>' +
          '<button type="button" class="hx-go hx-isend" data-id="' + esc(x.id) + '">Send To Run Backtest</button> <span class="hx-msg" data-im="' + esc(x.id) + '"></span></div></div>';
      }).join('') : '<div class="hx-flat">No Idea Has ' + 8 + ' Or More Trades On The Record Yet: The Board Needs A Few Weeks Of Its Own Prices For The Funds You Trade.</div>') +
      (list.length > 12 ? '<button type="button" id="hxIAll">' + (iAll ? 'Show Top 12' : 'Show All ' + list.length) + '</button>' : '') +
      '<div class="hx-foot">Each Idea Buys The First Fund And Hedges It With The Second (Or Sells The Micro Future), Held Through The Session (Entered In Its First 30 Minutes, Closed 15 Minutes Before It Ends) Or Bought On A Dip Vs The 4 Pm Close. ' +
      'Hedge Size From The Board\'s Own History. ★ = The Session It Is Ranked By (Steadiest Average With At Least 8 Trades). Send To Run Backtest Writes The Idea As A Strategy File (With A Hedge: Section) In That Algo\'s Strategies Folder And Runs Its Backtest On The Board\'s Record; The Result Shows On The Algo Tab. It Does Not Change What The Paper Or Live Algo Trades.</div>';
    var r = ibox.querySelector('#hxIRun');
    if (r) r.onclick = function () { r.textContent = 'Working…'; api('/ideas/run', {}).then(drawIdeas).catch(function (e) { r.textContent = e.message; }); setTimeout(loadIdeas, 15000); };
    var al = ibox.querySelector('#hxIAll'); if (al) al.onclick = function () { iAll = !iAll; drawIdeas(d); };
    [].forEach.call(ibox.querySelectorAll('.hx-iopen'), function (b) { b.onclick = function () { iOpen[b.dataset.id] = !iOpen[b.dataset.id]; drawIdeas(d); }; });
    [].forEach.call(ibox.querySelectorAll('.hx-seg[data-id] button'), function (b) {
      b.onclick = function () { var g = b.parentNode; iSend[g.dataset.id][g.dataset.k] = b.dataset.v; drawIdeas(d); };
    });
    [].forEach.call(ibox.querySelectorAll('.hx-isend'), function (b) {
      b.onclick = function () {
        var m = ibox.querySelector('[data-im="' + b.dataset.id + '"]'), sel = iSend[b.dataset.id];
        b.disabled = true; m.textContent = 'Sending…'; iMsgAt = Date.now();
        api('/ideas/send', { id: b.dataset.id, algo: sel.algo, session: sel.session })
          .then(function (r) { m.innerHTML = '<span class="hx-up">' + esc(r.text) + '</span>'; })
          .catch(function (e) { m.innerHTML = '<span class="hx-err">' + esc(e.message) + '</span>'; })
          .then(function () { b.disabled = false; });
      };
    });
  }
  function loadIdeas() {
    if (!ibox || document.hidden) return;
    if (Date.now() - iMsgAt < 90000) return;             // keep a Send message on screen for a while
    api('/ideas').then(drawIdeas).catch(function (e) { ibox.innerHTML = '<span class="hx-err">Hedge Ideas Not Available: ' + esc(e.message) + '</span>'; });
  }
  if (ibox) { loadIdeas(); setInterval(loadIdeas, 120000); }
  window.JBHedge.reload = load;
  load();
  setInterval(load, 60000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });
})();
