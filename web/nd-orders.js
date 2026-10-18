/* Night desk section: safety stops / targets / trailing / break-even, scheduled orders and sell at
 * the open, order switches (stale cancel, news pause, Sunday IB reminder, fill pushes), fills with
 * automatic notes (jb-orders). Data: /api/plus/orders (gateway/orders.py) */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var gd = ND.section('o-guard', 'Safety stops and targets (watched in every session)');
  var sc = ND.section('o-sched', 'Scheduled orders and sell at the open');
  var sw = ND.section('o-switch', 'Order automation switches');
  var fl = ND.section('o-fills', 'Fills with automatic notes');
  var STATUS = { on: 'c-g', waiting: 'c-b', firing: 'c-r', done: 'c-d', failed: 'c-r', off: 'c-d', paper: 'c-p', cancelled: 'c-d', missed: 'c-a', running: 'c-a' };
  function chip(s) { return '<span class="chip ' + (STATUS[s] || 'c-d') + '">' + esc(s) + '</span>'; }
  var formsDrawn = false;
  function forms() {
    if (formsDrawn) return; formsDrawn = true;
    gd.innerHTML = '<div id="ogList"></div><div class="card" style="padding:10px 12px;margin-top:8px">' +
      '<div class="row2"><select id="ogB"><option value="ibkr">IBKR</option><option value="webull">Webull</option></select>' +
      '<input type="text" id="ogS" placeholder="TQQQ" size="7" autocapitalize="characters"><input type="number" id="ogQ" placeholder="shares (blank = all)" style="width:150px"></div>' +
      '<div class="row2">Stop $<input type="number" id="ogStop" step="0.01" style="width:80px"> Target $<input type="number" id="ogTgt" step="0.01" style="width:80px">' +
      ' Trailing %<input type="number" id="ogTr" step="0.5" style="width:60px"> Break-even after +%<input type="number" id="ogBe" step="0.5" style="width:60px"></div>' +
      '<div class="row2"><button id="ogAdd">Add safety stop</button><span class="msg" id="ogMsg"></span></div></div>' +
      '<p class="note">For shares you already hold. The board checks the price every few seconds in every session (overnight too, and on Webull, which has no overnight stop orders of its own). ' +
      'Two readings in a row past the level are needed, so one odd trade can\'t set it off. Then it sells what you still hold with a limit a little under the price (0.5%, then 1%, 2%, 3%, 20 s each) and pushes the result. ' +
      '<b>Target + stop</b> together make a bracket: whichever comes first sells. <b>Trailing</b> moves the stop up as the price rises and never down. <b>Break-even</b> moves the stop up to what you paid once you are up that %. ' +
      'The Mac must be on and IB Gateway / Webull reachable for it to work. To set one with a new buy, use "More" on the order ticket.</p>';
    document.getElementById('ogAdd').onclick = function () {
      var m = document.getElementById('ogMsg'); m.textContent = 'Adding…';
      ND.post('orders/guard', { broker: document.getElementById('ogB').value, sym: document.getElementById('ogS').value, qty: document.getElementById('ogQ').value,
        stop: document.getElementById('ogStop').value, target: document.getElementById('ogTgt').value, trail: document.getElementById('ogTr').value, be: document.getElementById('ogBe').value })
        .then(function (d) { m.textContent = 'Added.'; draw(d); }).catch(function (e) { m.innerHTML = '<span class="err">' + esc(e.message) + '</span>'; });
    };
    sc.innerHTML = '<div id="osList"></div><div class="card" style="padding:10px 12px;margin-top:8px">' +
      '<div class="row2"><select id="osB"><option value="ibkr">IBKR</option><option value="webull">Webull</option></select><select id="osSide"><option value="sell">Sell</option><option value="buy">Buy</option></select>' +
      '<input type="text" id="osS" placeholder="TQQQ" size="7" autocapitalize="characters"><input type="text" id="osQ" placeholder="shares or all" size="10"></div>' +
      '<div class="row2">When <select id="osW"><option value="open">at the 9:30 open</option><option value="pre">at 4 am pre-market</option><option value="after">at 4 pm after-hours</option><option value="night">at 8 pm overnight</option><option value="time">at a time…</option></select>' +
      '<input type="datetime-local" id="osT" style="display:none"> Limit $<input type="number" id="osL" step="0.01" placeholder="blank = market price" style="width:150px"></div>' +
      '<div class="row2">Only if <input type="text" id="osCS" placeholder="NQ=F" size="7" autocapitalize="characters"> <select id="osCO"><option value="">(no condition)</option><option value="up">is up at least %</option><option value="down">is down at least %</option><option value="above">is above $</option><option value="below">is below $</option></select> <input type="number" id="osCV" step="0.01" style="width:80px"></div>' +
      '<div class="row2">and <input type="text" id="osCS2" placeholder="TQQQ" size="7" autocapitalize="characters"> <select id="osCO2"><option value="">(no second condition)</option><option value="up">is up at least %</option><option value="down">is down at least %</option><option value="above">is above $</option><option value="below">is below $</option></select> <input type="number" id="osCV2" step="0.01" style="width:80px"></div>' +
      '<div class="row2"><label><input type="checkbox" id="osR"> Real order (unticked = paper: it only records what it would have done)</label></div>' +
      '<div class="row2"><button id="osAdd">Schedule</button><span class="msg" id="osMsg"></span></div></div>' +
      '<p class="note"><b>Sell at the open</b>: Sell, "all", at the 9:30 open, real. Without a limit it sells like a safety stop (a little under the price, stepping down) so it fills. Times are New York time; holidays and early closes are skipped. Paper is the default, so you can watch a few run before trusting one with money. ' +
      '<b>Conditions</b> are checked at that moment: e.g. at 8 pm, only if NQ=F is up at least 0.3% and TQQQ is up at least 0% (held its gain). Up / down are vs the 4 pm close. If a condition fails the order is skipped and you get a push saying why.</p>';
    document.getElementById('osW').onchange = function () { document.getElementById('osT').style.display = this.value === 'time' ? '' : 'none'; };
    document.getElementById('osAdd').onclick = function () {
      var m = document.getElementById('osMsg'), w = document.getElementById('osW').value;
      if (w === 'time') w = document.getElementById('osT').value;
      m.textContent = 'Saving…';
      var conds = [['osCS', 'osCO', 'osCV'], ['osCS2', 'osCO2', 'osCV2']].map(function (k) { return { sym: document.getElementById(k[0]).value, op: document.getElementById(k[1]).value, val: document.getElementById(k[2]).value }; })
        .filter(function (c) { return c.op; });
      ND.post('orders/schedule', { conds: conds, broker: document.getElementById('osB').value, side: document.getElementById('osSide').value, sym: document.getElementById('osS').value,
        qty: document.getElementById('osQ').value, when: w, limit: document.getElementById('osL').value, real: document.getElementById('osR').checked })
        .then(function (d) { m.textContent = 'Scheduled.'; draw(d); }).catch(function (e) { m.innerHTML = '<span class="err">' + esc(e.message) + '</span>'; });
    };
  }
  function drawSwitches(c) {
    if (sw.dataset.drawn) return; sw.dataset.drawn = 1;
    ND.switches(sw, [
      ['fill_push', 'Push on every fill', 'A phone push each time an order fills (IBKR within about 30 s, Webull within about 3 min), with an automatic note: session, move since the close, QQQ, SPY, VIX.'],
      ['ib_reminder', 'Sunday IB sign-in reminder', 'Sunday after 5 pm New York: a reminder that IBKR wants its weekly sign-in, so you approve the IB Key push before futures open at 6 pm.'],
      ['event_push', 'Heads-up before big news', 'A phone push <b>' + (c.event_before || 10) + ' min</b> before a high-impact event (Fed decision, CPI, jobs report) with what you hold. The algos keep trading.'],
      ['event_guard', 'Also pause the algos for big news', 'Pauses both algos (they close their position) <b>' + (c.event_before || 10) + ' min</b> before the event and resumes them <b>' + (c.event_after || 15) + ' min</b> after. Off unless you switch it on. It never resumes an algo that SELL ALL or you paused.'],
      ['stale_session', 'Cancel orders when the session changes', 'At 4 am, 9:30 am, 4 pm and 8 pm New York: cancels your ticket orders that were good for one session and are still open from the session before, with a push.']
    ], c, function (v) { return ND.post('orders/settings', v); });
    var d = document.createElement('div'); d.className = 'card'; d.style.cssText = 'padding:8px 12px;margin-top:8px';
    d.innerHTML = '<div class="row2">Cancel my today-only orders not filled after <input type="number" id="oStale" style="width:60px" value="' + (c.stale_min || '') + '" placeholder="off"> minutes <button id="oStaleSave">Save</button><span class="msg" id="oStaleMsg"></span></div>' +
      '<div class="row2">Size buttons on the ticket ($): <input type="text" id="oSizes" value="' + esc((window.__ordSizes || []).join(', ')) + '" size="22"> <button id="oSizesSave">Save</button><span class="msg" id="oSizesMsg"></span></div>' +
      '<p class="note">Blank or 0 = never cancel by age. Only orders from your ticket that are good for one session; "until I cancel" orders and the board\'s own orders are left alone. You get a push for each one cancelled.</p>';
    sw.appendChild(d);
    document.getElementById('oStaleSave').onclick = function () {
      var m = document.getElementById('oStaleMsg'); ND.post('orders/settings', { stale_min: document.getElementById('oStale').value || 0 }).then(function () { m.textContent = 'Saved.'; }).catch(function (e) { m.textContent = e.message; });
    };
    document.getElementById('oSizesSave').onclick = function () {
      var m = document.getElementById('oSizesMsg'), v = document.getElementById('oSizes').value.split(/[ ,]+/).filter(Boolean).map(Number);
      ND.post('orders/sizes', { dollars: v }).then(function () { m.textContent = 'Saved.'; }).catch(function (e) { m.textContent = e.message; });
    };
  }
  function draw(d) {
    forms();
    window.__ordSizes = (d.sizes || {}).dollars || [];
    drawSwitches(d.cfg || {});
    var g = d.guards || [];
    document.getElementById('ogList').innerHTML = (d.iblink && !d.iblink.connected ? '<p class="note warn">IB Gateway link is down right now: IBKR prices and sells will wait until it is back.</p>' : '') +
      (g.length ? '<div class="card">' + g.slice().reverse().map(function (x) {
        return '<div class="ev"><span class="tm">' + chip(x.status) + '</span><span class="tx"><b>' + esc(x.text) + '</b>' + (x.last ? ' · now ' + x.last : '') +
          (x.status === 'on' && x.fresh === false ? ' <span class="warn">(no fresh price)</span>' : '') + (x.note ? '<br><span class="note">' + esc(x.note) + '</span>' : '') + '</span>' +
          (x.status === 'on' || x.status === 'waiting' ? '<button class="ghost" data-off="' + esc(x.id) + '">Switch off</button>' : '') + '</div>';
      }).join('') + '</div>' : '<p class="note">No safety stops yet.</p>');
    [].forEach.call(gd.querySelectorAll('[data-off]'), function (b) { b.onclick = function () { ND.post('orders/guard/off', { id: b.dataset.off }).then(draw); }; });
    var s = d.sched || [];
    document.getElementById('osList').innerHTML = s.length ? '<div class="card">' + s.slice().reverse().map(function (x) {
      return '<div class="ev"><span class="tm">' + chip(x.status) + '</span><span class="tx"><b>' + (x.real ? 'REAL ' : 'paper ') + esc(x.side + ' ' + x.qty + ' ' + x.sym) + '</b> on ' + esc(x.broker.toUpperCase()) + ' · ' + esc(x.at_text) +
        (x.limit ? ' · limit ' + x.limit : '') + (x.if_text ? '<br><span class="note">only if ' + esc(x.if_text) + '</span>' : '') + (x.result ? '<br><span class="note">' + esc(x.result) + '</span>' : '') + '</span>' +
        (x.status === 'waiting' ? '<button class="ghost" data-so="' + esc(x.id) + '">Cancel</button>' : '') + '</div>';
    }).join('') + '</div>' : '<p class="note">Nothing scheduled.</p>';
    [].forEach.call(sc.querySelectorAll('[data-so]'), function (b) { b.onclick = function () { ND.post('orders/schedule/off', { id: b.dataset.so }).then(draw); }; });
  }
  function drawFills() {
    ND.api('orders/notes').then(function (n) {
      var rows = Object.keys(n).map(function (k) { return n[k]; }).sort(function (a, b) { return b.t - a.t; }).slice(0, 15);
      fl.innerHTML = (rows.length ? '<div class="card">' + rows.map(function (r) {
        return '<div class="ev"><span class="tm">' + esc(ND.time(r.t)) + '</span><span class="tx"><b>' + esc(r.side.toUpperCase() + ' ' + r.qty + ' ' + r.sym) + '</b> @ ' + r.price + '<br><span class="note">' + esc(r.text) + '</span></span></div>';
      }).join('') + '</div>' : '<p class="note">No fills since this was installed.</p>') +
        '<p class="note">Each fill gets a note written automatically from the board at that moment, so later you can see what the market was doing when you traded. The order log:</p>';
      ND.api('orders').then(function (d) { fl.innerHTML += '<div class="card">' + ND.log((d.log || []).slice(0, 15)) + '</div>'; });
    }).catch(function () {});
  }
  function load() { ND.api('orders').then(draw).catch(function () {}); }
  load(); drawFills();
  setInterval(function () { if (!document.hidden) load(); }, 15000);
  setInterval(function () { if (!document.hidden) drawFills(); }, 60000);
})();
