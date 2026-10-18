/* Trading tools (jb-tools2, deep search batch B). One file, used on several pages:
 *   phone.html          "Exposure and exit plans" card: real exposure in index terms, cool-down, earnings moves
 *                       for your ETFs, wash-sale windows, exit plans waiting for a fill.
 *   trading.html        the same in full, plus settings, your exit plans (add / delete / put on what you hold),
 *                       the wash-sale total for the year and the kept overnight price record (export as CSV).
 *   chart.html          your own trades as arrows (green buy, red sell); hover or tap near one for the details.
 *   ibkr.html / webull.html   an EARN chip on a 2x/3x fund tile when a big holding reports soon.
 * Data: /api/plus/tradetools (gateway/tradetools.py). */
(function () {
  'use strict';
  if (window.__jbTools2) return;
  window.__jbTools2 = true;
  var page = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function money(v) { return v == null ? '--' : (v < 0 ? '-$' : '$') + Math.round(Math.abs(v)).toLocaleString('en-US'); }
  function getJ(u) { return fetch(u, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }); }
  function postJ(u, b) {
    return fetch(u, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); });
  }
  function day(ms) { return new Date(ms).toLocaleDateString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric' }); }
  function chip(t, kind) {
    var c = { ok: ['#062a14', '#5dff8f', '#145c2c'], watch: ['#2e2400', '#ffc400', '#7a5c00'], bad: ['#3a0b0b', '#ff8a80', '#7a1c1c'],
              info: ['#0e2a3a', '#7fd6ff', '#1e5a78'] }[kind] || ['#14202e', '#9fb3c8', '#2a3d52'];
    return '<span style="display:inline-block;border-radius:3px;padding:1px 6px;font-size:11px;font-weight:700;border:1px solid ' + c[2] + ';background:' + c[0] + ';color:' + c[1] + '">' + esc(t) + '</span> ';
  }
  var BTN = 'font:inherit;font-size:12px;padding:5px 10px;min-height:32px;border-radius:5px;border:1px solid #1e5a78;background:#0e2a3a;color:#cfefff;cursor:pointer;margin:2px 4px 2px 0';
  var API = '/api/plus/tradetools';

  function exposureHtml(e) {
    if (!e) return '';
    var h = '<div style="margin:3px 0"><b>' + esc(e.text) + '</b></div>';
    (e.warn || []).forEach(function (w) { h += '<div style="margin:3px 0">' + chip('CHECK', 'watch') + esc(w) + '</div>'; });
    return h;
  }
  function earnHtml(list, n) {
    return (list || []).slice(0, n).map(function (r) {
      return '<div style="margin:3px 0">' + chip('EARN ±' + r.move + '%', r.held ? 'watch' : 'info') + esc(r.text) + '</div>';
    }).join('');
  }
  function washOpen(w) {
    return (w && w.open || []).map(function (r) {
      return '<div style="margin:3px 0">' + chip('WASH SALE', 'info') + 'Sold ' + esc(r.sym) + ' at a ' + money(r.loss) + ' loss on ' + day(r.sold) +
        ': buying it back before ' + day(r.until) + ' postpones that loss for taxes.</div>';
    }).join('');
  }

  // ---------------------------------------------------------------- phone card
  function phone() {
    var trd = $('trd'); if (!trd) return;
    var card = document.createElement('div'); card.className = 'card'; card.id = 'jbTools2';
    var host = trd.closest('.card'); host.parentNode.insertBefore(card, host.nextSibling);
    function draw(d) {
      if (!d) return;
      var h = '<h2>Exposure and exit plans <a href="trading.html">My trading ›</a></h2>' + exposureHtml(d.exposure);
      if (d.cooldown && d.cooldown.on) h += '<div style="margin:3px 0">' + chip('COOL-DOWN', 'watch') + esc(d.cooldown.text.replace(/^Cool-down: /, '')) + '</div>';
      h += earnHtml(d.earn, 3) + washOpen(d.wash);
      (d.pending || []).forEach(function (p) { h += '<div style="margin:3px 0">' + chip('EXIT PLAN', 'ok') + esc('“' + p.plan + '” waits for your ' + p.sym + ' buy to fill (' + p.broker.toUpperCase() + ').') + '</div>'; });
      card.innerHTML = h;
    }
    function load() { if (!document.hidden) getJ(API).then(draw).catch(function () {}); }
    load(); setInterval(load, 60000);
  }

  // ---------------------------------------------------------------- My trading section
  function trading() {
    var at = $('limit'); if (!at) return;
    var h2 = document.createElement('h2'); h2.id = 'tools2'; h2.textContent = 'Exposure, exit plans and taxes';
    var box = document.createElement('div');
    at.parentNode.insertBefore(h2, at); at.parentNode.insertBefore(box, at);
    var d = null;
    function lbl(t, inner) { return '<label style="display:inline-flex;flex-direction:row;align-items:center;gap:6px;color:var(--white,#e6eef6);margin:3px 10px 3px 0;font-size:12px">' + t + inner + '</label>'; }
    function draw() {
      if (!d) return;
      var s = d.settings || {}, e = d.exposure || {}, w = d.wash || {}, a = d.archive || {};
      var rows = (e.rows || []).map(function (r) {
        return '<tr><td>' + esc(r.index) + '</td><td class="up">' + money(r.long) + '</td><td class="dn">' + money(r.short) + '</td><td class="' + (r.net >= 0 ? 'up' : 'dn') + '">' + money(r.net) + '</td><td style="white-space:normal;font-size:11px">' +
          esc(r.held.map(function (x) { return x.sym + ' ' + money(x.value) + (x.x !== 1 ? ' x' + x.x : ''); }).join(', ')) + '</td></tr>';
      }).join('');
      var h = '<h3>Real exposure</h3><div class="card">' + exposureHtml(e) +
        (rows ? '<table><tr><th>Index</th><th>Long</th><th>Short</th><th>Net</th><th>From</th></tr>' + rows + '</table>' : '') +
        '<div class="row">' + lbl('Warn me above $', '<input id="t2Max" type="number" min="0" step="1000" style="width:100px" value="' + (s.expo_max || '') + '">') + '<span class="note">in index terms (0 = only warn when I hold both sides)</span></div></div>';
      h += '<h3>Cool-down after losses</h3><div class="card"><div class="row">' +
        lbl('<input type="checkbox" id="t2Cool"' + (s.cool_on ? ' checked' : '') + '>', 'Push me after') + '<input id="t2N" type="number" min="1" max="10" style="width:56px" value="' + s.cool_losses + '"> <span class="note">losing trades in a row,</span> ' +
        lbl('<input type="checkbox" id="t2Half"' + (s.cool_half ? ' checked' : '') + '>', 'or at half my daily loss limit') + lbl('break of', '<input id="t2Min" type="number" min="5" max="240" style="width:60px" value="' + s.cool_min + '"> min') + '</div>' +
        (d.cooldown && d.cooldown.on ? '<div>' + chip('NOW', 'watch') + esc(d.cooldown.text) + '</div>' : '<div class="note">Not on right now. It never blocks an order: the order ticket just shows an amber note.</div>') + '</div>';
      h += '<h3>Earnings moves for your ETFs</h3><div class="card">' + (earnHtml(d.earn, 12) || '<div class="note">No big holding of your 2x/3x funds reports today after the close or before the next open.</div>') +
        '<div class="row">' + lbl('<input type="checkbox" id="t2Earn"' + (s.earn_push ? ' checked' : '') + '>', 'Push me at 3:30 pm ET when there are some') + '</div></div>';
      h += '<div class="row"><button class="btn" id="t2Save">Save these settings</button><span id="t2Msg" class="note"></span></div>';
      h += '<h3>Exit plans</h3><div class="card"><div class="note">Pick one on the order ticket (it starts when the buy fills), or put one on what you already hold. The board watches it in every session, like your safety stops, and the Mac must be on.</div><table>' +
        (d.plans || []).map(function (p, i) { return '<tr><td><b>' + esc(p.name) + '</b></td><td style="white-space:normal">' + esc(p.text) + '</td><td><button data-del="' + i + '" style="' + BTN + '">Delete</button></td></tr>'; }).join('') + '</table>' +
        '<div class="row">' + lbl('Put', '<select id="t2Plan">' + (d.plans || []).map(function (p) { return '<option>' + esc(p.name) + '</option>'; }).join('') + '</select>') +
        lbl('on', '<input id="t2Sym" maxlength="12" style="width:80px;text-transform:uppercase" placeholder="TQQQ">') +
        lbl('at', '<select id="t2Br"><option value="ibkr">IBKR</option><option value="webull">Webull</option></select>') + '<button class="btn" id="t2Apply">Put it on</button><span id="t2AMsg" class="note"></span></div>' +
        '<details style="margin-top:6px"><summary class="note" style="cursor:pointer">New exit plan</summary><div class="row">' + lbl('Name', '<input id="t2Name" maxlength="40" style="width:150px">') +
        lbl('Stop -%', '<input id="t2Stop" type="number" step="0.5" min="0" style="width:60px">') + lbl('Break-even after +%', '<input id="t2Be" type="number" step="0.5" min="0" style="width:60px">') + '</div>' +
        [1, 2, 3].map(function (k) {
          return '<div class="row">' + lbl('Part ' + k + ': % of shares', '<input id="t2P' + k + '" type="number" min="0" max="100" style="width:56px">') +
            lbl('sell at +%', '<input id="t2T' + k + '" type="number" step="0.5" min="0" style="width:60px">') + lbl('or trail %', '<input id="t2R' + k + '" type="number" step="0.5" min="0" style="width:60px">') + '</div>';
        }).join('') + '<div class="row"><button class="btn" id="t2Add">Save plan</button><span id="t2PMsg" class="note"></span></div></details></div>';
      var wr = (w.rows || []).map(function (r) {
        return '<tr><td>' + esc(r.sym) + '</td><td>' + day(r.sold) + '</td><td class="dn">' + money(r.loss) + '</td><td>' + (r.bought_back ? day(r.bought_back) : '') + '</td><td>' + money(r.postponed) + '</td></tr>';
      }).join('');
      h += '<h3>Wash-sale watch</h3><div class="card"><div>Losses postponed for taxes in ' + (w.year || '') + ': <b>' + money(w.postponed || 0) + '</b></div>' + washOpen(w) +
        (wr ? '<table><tr><th>Symbol</th><th>Sold at a loss</th><th>Loss</th><th>Bought back</th><th>Postponed</th></tr>' + wr + '</table>' : '') +
        '<div class="note">A loss sale bought back within 30 days is a wash sale: the loss is not lost, it is added to the cost of the new shares and counts when you sell those. This is an estimate from your journal (both brokers), for information only; your broker’s tax forms are what count.</div></div>';
      h += '<h3>Your own overnight price record</h3><div class="card"><div>' + (a.days ? 'Kept for good: <b>' + a.days + (a.days === 1 ? ' day' : ' days') + '</b> of after-hours, overnight and pre-market minutes since ' + esc(a.first) + ' (' + (a.mb >= 0.1 ? a.mb + ' MB' : 'under 0.1 MB') + ').' :
        'The first days are copied within a few minutes of the board starting.') + '</div><div class="row">' + lbl('Export', '<input id="t2HSym" maxlength="12" style="width:80px;text-transform:uppercase" placeholder="TQQQ">') +
        lbl('last', '<select id="t2HDays"><option>30</option><option selected>90</option><option>365</option><option value="3650">all</option></select> days') + '<button class="btn" id="t2HGo">Download CSV</button><span id="t2HMsg" class="note"></span></div>' +
        '<div class="note">Free sites don’t keep overnight prices, so this record is the only copy. It feeds better gap numbers and overnight backtests over time.</div></div>';
      box.innerHTML = h;
      wire();
    }
    function val(id) { var x = $(id); return x ? x.value : ''; }
    function wire() {
      $('t2Save').onclick = function () {
        postJ(API + '/settings', { expo_max: +val('t2Max') || 0, cool_on: $('t2Cool').checked, cool_losses: +val('t2N') || 2, cool_half: $('t2Half').checked,
          cool_min: +val('t2Min') || 30, earn_push: $('t2Earn').checked }).then(function (x) { d = x; draw(); $('t2Msg').textContent = 'Saved.'; })
          .catch(function (e) { $('t2Msg').textContent = e.message; });
      };
      [].forEach.call(box.querySelectorAll('[data-del]'), function (b) {
        b.onclick = function () { var p = d.plans[+b.dataset.del]; if (p && confirm('Delete the exit plan “' + p.name + '”?')) postJ(API + '/template/off', { name: p.name }).then(function (x) { d = x; draw(); }); };
      });
      $('t2Apply').onclick = function () {
        var m = $('t2AMsg'); m.textContent = 'Setting it…';
        postJ(API + '/plan', { template: val('t2Plan'), sym: val('t2Sym').trim().toUpperCase(), broker: val('t2Br') }).then(function (r) { m.textContent = r.text; }).catch(function (e) { m.textContent = e.message; });
      };
      $('t2Add').onclick = function () {
        var parts = [1, 2, 3].map(function (k) { return { pct: +val('t2P' + k) || 0, target: +val('t2T' + k) || 0, trail: +val('t2R' + k) || 0 }; }).filter(function (p) { return p.pct; });
        postJ(API + '/template', { name: val('t2Name'), stop: +val('t2Stop') || 0, be: +val('t2Be') || 0, parts: parts }).then(function (x) { d = x; draw(); })
          .catch(function (e) { $('t2PMsg').textContent = e.message; });
      };
      $('t2HGo').onclick = function () {
        var sym = val('t2HSym').trim().toUpperCase(), m = $('t2HMsg'); if (!sym) { m.textContent = 'Type a symbol.'; return; }
        m.textContent = 'Reading…';
        getJ(API + '/history?sym=' + encodeURIComponent(sym) + '&days=' + val('t2HDays')).then(function (r) {
          if (!r || !r.rows || !r.rows.length) { m.textContent = 'No kept minutes for ' + sym + ' yet.'; return; }
          var csv = 'time_new_york,minute_ms,price,feed,session\n' + r.rows.map(function (x) {
            return new Date(x[0]).toLocaleString('en-US', { timeZone: 'America/New_York', hour12: false }).replace(',', '') + ',' + x[0] + ',' + x[1] + ',' + x[2] + ',' + x[3];
          }).join('\n') + '\n';
          var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = sym + '-overnight-record.csv';
          document.body.appendChild(a); a.click(); a.remove(); m.textContent = r.rows.length.toLocaleString() + ' minutes saved to your Downloads.';
        }).catch(function () { m.textContent = 'Could not read it.'; });
      };
    }
    function load() { if (!document.hidden && !box.contains(document.activeElement)) getJ(API).then(function (x) { if (x) { d = x; draw(); } }).catch(function () {}); }
    load(); setInterval(load, 120000);
  }

  // ---------------------------------------------------------------- your trades on the Chart page (idea 4)
  function chart() {
    var F = { sym: '', at: 0, list: [] };
    function want(sym) {
      if (!sym || (F.sym === sym && Date.now() - F.at < 60000)) return;
      F.sym = sym; F.at = Date.now();
      getJ(API + '/fills?sym=' + encodeURIComponent(sym)).then(function (r) { if (r && r.sym === F.sym) { F.list = r.fills || []; if (window.JBChartRedraw) window.JBChartRedraw(); } }).catch(function () {});
    }
    window.JBChartLayers = window.JBChartLayers || [];
    window.JBChartLayers.push(function (ctx, g) {
      want(g.sym);
      if (F.sym !== g.sym || !F.list.length) return;
      var near = null;
      F.list.forEach(function (f) {
        if (f.t < g.t0 || f.t > g.t1 || f.price < g.lo || f.price > g.hi) return;
        var x = g.X(f.t), y = g.Y(f.price), buy = f.side === 'buy';
        ctx.fillStyle = buy ? '#5dff8f' : '#ff6b6b'; ctx.strokeStyle = '#04101f'; ctx.lineWidth = 1;
        ctx.beginPath();
        if (buy) { ctx.moveTo(x, y + 3); ctx.lineTo(x - 6, y + 13); ctx.lineTo(x + 6, y + 13); } else { ctx.moveTo(x, y - 3); ctx.lineTo(x - 6, y - 13); ctx.lineTo(x + 6, y - 13); }
        ctx.closePath(); ctx.fill(); ctx.stroke();
        if (g.hover != null && Math.abs(g.hover - x) < 8 && (!near || Math.abs(g.hover - x) < Math.abs(g.hover - near.x))) near = { f: f, x: x, y: y };
      });
      if (!near) return;
      var f = near.f, t = (f.side === 'buy' ? 'BUY ' : 'SELL ') + f.qty + ' @ ' + f.price + ' ' + f.broker.toUpperCase() + (f.pnl != null ? ' · ' + (f.pnl >= 0 ? '+' : '-') + '$' + Math.abs(f.pnl).toFixed(2) : '');
      ctx.font = 'bold 11px "IBM Plex Mono", ui-monospace, Menlo, monospace';
      var w = ctx.measureText(t).width + 12, bx = Math.min(Math.max(near.x - w / 2, g.x0), g.x1 - w), by = f.side === 'buy' ? near.y + 16 : near.y - 34;
      ctx.fillStyle = 'rgba(4,16,31,.92)'; ctx.fillRect(bx, by, w, 18);
      ctx.strokeStyle = f.side === 'buy' ? '#5dff8f' : '#ff6b6b'; ctx.strokeRect(bx + .5, by + .5, w - 1, 17);
      ctx.fillStyle = '#e6eef6'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(t, bx + 6, by + 9);
    });
  }

  // ---------------------------------------------------------------- EARN chip on fund tiles (idea 6)
  function boards() {
    var E = {};
    var st = document.createElement('style');
    st.textContent = '.plus3{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:2px 0;font-size:10.5px}.plus3:empty{display:none}';
    document.head.appendChild(st);
    function paint() {
      [].forEach.call(document.querySelectorAll('.tile[data-sym], .row[data-sym]'), function (el) {
        var sym = el.dataset.sym, list = E[sym] || [];
        var box = el.querySelector('.plus3');
        if (!box) {
          if (!list.length) return;
          box = document.createElement('div'); box.className = 'plus3';
          var p2 = el.querySelector('.plus2') || el.querySelector('.plus'), foot = el.classList.contains('tile') ? el.querySelector(':scope > .foot') : null, det = el.querySelector('.detail');
          if (p2) p2.parentNode.insertBefore(box, p2.nextSibling); else if (foot) el.insertBefore(box, foot); else if (det) det.appendChild(box); else return;
        }
        var html = list.slice(0, 2).map(function (r) { return '<span class="pl-chip pl-a" title="' + esc(r.text) + '">EARN ' + esc(r.sym) + ' ±' + r.move + '%</span>'; }).join('');
        if (box._h !== html) { box._h = html; box.innerHTML = html; }
      });
    }
    function load() {
      if (document.hidden) return;
      getJ(API).then(function (d) { var m = {}; ((d && d.earn) || []).forEach(function (r) { (m[r.etf] = m[r.etf] || []).push(r); }); E = m; }).catch(function () {});
    }
    load(); setInterval(load, 120000); setInterval(paint, 3000);
  }

  if (page === 'phone.html') phone();
  else if (page === 'trading.html') trading();
  else if (page === 'chart.html') chart();
  else if (page === 'ibkr.html' || page === 'webull.html') boards();
})();
