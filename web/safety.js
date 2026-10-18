/* Safety extras (jb-safety). One file, used on several pages:
 *   phone.html   "Accounts and protection" card (buying power, margin call risk, stop at broker or Mac only,
 *                Place at IBKR), Restart buttons when something is not running, and the last snapshot when the
 *                Mac can't be reached (needs the https ts.net link: phones only allow this on https).
 *   ibkr.html / webull.html   a Restart button inside the red "bridge is not running" banner.
 *   health.html  Backup internet (iPhone hotspot) settings, the Dec 6 overnight check, the account lines.
 * Data: /api/plus/safety, /api/health/page, /api/health/restart. */
(function () {
  'use strict';
  var page = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function money(v) { return v == null ? '--' : '$' + Math.round(v).toLocaleString('en-US'); }
  function chip(t, kind) {
    var c = { ok: ['#062a14', '#5dff8f', '#145c2c'], watch: ['#2e2400', '#ffc400', '#7a5c00'], bad: ['#3a0b0b', '#ff8a80', '#7a1c1c'],
              info: ['#0e2a3a', '#7fd6ff', '#1e5a78'] }[kind] || ['#14202e', '#9fb3c8', '#2a3d52'];
    return '<span style="display:inline-block;border-radius:3px;padding:1px 6px;font-size:11px;font-weight:700;border:1px solid ' + c[2] + ';background:' + c[0] + ';color:' + c[1] + '">' + esc(t) + '</span> ';
  }
  function getJ(u) { return fetch(u, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }); }
  function postJ(u, b) {
    return fetch(u, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b || {}) })
      .then(function (r) { return r.json(); });
  }
  function et(ms) { return new Date(ms).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }); }
  var BTN = 'style="font:inherit;font-size:12px;padding:5px 10px;min-height:32px;border-radius:5px;border:1px solid #1e5a78;background:#0e2a3a;color:#cfefff;cursor:pointer;margin:2px 4px 2px 0"';

  // ---------------------------------------------------------------- Restart (idea 13)
  var SVC = { IBKR: 'ibkr', WEBULL: 'webull' };
  function restart(name, btn) {
    if (!confirm('Restart ' + name + '? It takes about 30 seconds.')) return;
    btn.disabled = true; btn.textContent = 'Restarting…';
    postJ('/api/health/restart', { name: name }).then(function (r) {
      btn.textContent = r.ok ? 'Restarting… (about 30 s)' : (r.error || 'Could not restart');
      setTimeout(function () { btn.disabled = false; btn.textContent = 'Restart'; }, 30000);
    }).catch(function () { btn.textContent = 'Restarting…'; });
  }
  function boardBanner() {
    var w = $('warn'); if (!w) return;
    function fix() {
      var m = /The (IBKR|WEBULL) bridge is not running/.exec(w.textContent || '');
      if (!m || w.querySelector('.jb-rs')) return;
      var b = document.createElement('button'); b.className = 'jb-rs'; b.textContent = 'Restart';
      b.setAttribute('style', 'margin-left:10px;font:inherit;font-size:12px;padding:3px 10px;border-radius:4px;border:1px solid #ff8a80;background:#3a0b0b;color:#ffd0cc;cursor:pointer');
      b.onclick = function () { restart(SVC[m[1]], b); };
      w.appendChild(b);
    }
    new MutationObserver(fix).observe(w, { childList: true, characterData: true, subtree: true });
    fix();
  }
  function phoneDown() {
    var d = $('down'); if (!d) return;
    var busy = false;
    function fix() {
      if (busy || !d.querySelector('.card.down') || d.querySelector('.jb-rs')) return;
      busy = true;
      getJ('/api/health/page').then(function (h) {
        busy = false;
        var box = d.querySelector('.card.down'); if (!h || !box || box.querySelector('.jb-rs')) return;
        var down = (h.services || []).filter(function (s) { return s.on && s.state !== 'running'; });
        var row = document.createElement('div'); row.className = 'jb-rs'; row.style.marginTop = '6px';
        down.forEach(function (s) {
          var b = document.createElement('button'); b.textContent = 'Restart ' + (s.title || s.name);
          b.setAttribute('style', BTN.slice(7, -1)); b.onclick = function () { restart(s.name, b); };
          row.appendChild(b);
        });
        if (down.length) box.appendChild(row);
      }).catch(function () { busy = false; });
    }
    new MutationObserver(fix).observe(d, { childList: true, subtree: true });
    fix();
  }

  // ---------------------------------------------------------------- accounts and protection card
  function accLine(a) {
    if (!a) return '';
    var name = a.broker === 'ibkr' ? 'IBKR' : 'Webull';
    if (!a.ok) return '<div style="margin:3px 0">' + chip(name, 'info') + '<span style="color:var(--dim,#8aa)">' + esc(a.note || 'not read yet') + '</span></div>';
    var k = a.level === 'high' ? 'bad' : a.level === 'watch' ? 'watch' : a.level === 'ok' || a.level === 'none' ? 'ok' : '';
    var extra = a.cushion != null ? ' · cushion ' + a.cushion + '%' : a.util != null ? ' · margin used ' + a.util + '%' : '';
    return '<div style="margin:3px 0">' + chip(name + ' ' + (a.type || ''), 'info') + 'Buying power <b>' + money(a.bp) + '</b>' + esc(extra) +
      ' ' + chip(a.risk || '', k) + '</div>';
  }
  function protLine(p) {
    var k = { broker: 'ok', mac: 'watch', none: 'bad', unknown: '' }[p.status];
    var t = { broker: 'AT BROKER', mac: 'MAC ONLY', none: 'NO STOP', unknown: 'UNKNOWN' }[p.status];
    return '<div style="margin:4px 0">' + chip(t, k) + '<b>' + esc(p.sym) + '</b> ' + esc(p.broker.toUpperCase()) + ' · ' + esc(p.qty) +
      ' sh. <span style="color:var(--dim,#8aa)">' + esc(p.text) + '</span>' +
      (p.can_place ? ' <button class="jb-place" data-sym="' + esc(p.sym) + '" data-stop="' + esc(p.stop || '') + '" ' + BTN + '>Place at IBKR</button>' : '') + '</div>';
  }
  function placeStop(sym, stop) {
    var s = stop || prompt('Stop price for ' + sym + '?'); if (!s) return;
    postJ('/api/plus/safety/stop/check', { sym: sym, stop: s }).then(function (r) {
      if (r.error) { alert(r.error); return; }
      if (!confirm(r.text + '\n\nSend it to IBKR now?')) return;
      postJ('/api/plus/safety/stop/go', { code: r.code }).then(function (g) { alert(g.error || g.text); load(); });
    });
  }
  var card;
  function paint(d) {
    if (!card || !d) return;
    var acc = d.accounts || {}, prot = d.protection || [];
    var h = '<h2>Accounts and protection <span>margin accounts</span></h2>';
    h += accLine(acc.ibkr) + accLine(acc.webull);
    if (!acc.ibkr && !acc.webull) h += '<div class="empty">Reading your accounts (every 5 minutes)…</div>';
    if (prot.length) h += '<div style="margin-top:6px;border-top:1px solid var(--line,#123);padding-top:4px">' + prot.map(protLine).join('') + '</div>';
    else h += '<div style="color:var(--dim,#8aa);font-size:12px;margin-top:4px">No open positions in your journal, so nothing needs a stop.</div>';
    card.innerHTML = h;
  }
  function phoneCard() {
    var trd = $('trd'); if (!trd) return;
    card = document.createElement('div'); card.className = 'card'; card.id = 'jbSafety';
    var host = trd.closest('.card'); host.parentNode.insertBefore(card, host.nextSibling);
    card.addEventListener('click', function (e) { var b = e.target.closest('.jb-place'); if (b) placeStop(b.dataset.sym, b.dataset.stop); });
    load(); setInterval(function () { if (!document.hidden) load(); }, 60000);
  }
  function load() { getJ('/api/plus/safety').then(function (d) { paint(d); if (page === 'health.html') healthPaint(d); }).catch(function () {}); }

  // ---------------------------------------------------------------- last snapshot when the Mac is down (idea 60)
  function offline() {
    if (!('serviceWorker' in navigator)) return;            // phones allow this only on the https (ts.net) link
    navigator.serviceWorker.register('/sw.js').catch(function () {});
    var real = window.fetch;
    window.fetch = function (u, o) {
      return real.apply(this, arguments).then(function (r) {
        if (String(u).indexOf('/api/phone') >= 0) {
          var saved = r.headers.get('X-JB-Saved');
          var b = $('jbOff');
          if (saved) {
            if (!b) {
              b = document.createElement('div'); b.id = 'jbOff'; b.className = 'card down';
              var top = $('down'); top.parentNode.insertBefore(b, top);
            }
            b.innerHTML = '<b>Mac not reachable.</b> Showing the last snapshot from ' + esc(et(+saved)) + ' ET: prices are old. ' +
              'To act on a position now, use the IBKR or Webull app.';
          } else if (b) b.remove();
        }
        return r;
      });
    };
  }

  // ---------------------------------------------------------------- Health tab
  var hbox;
  function healthPaint(d) {
    if (!hbox || !d) return;
    var hs = d.hotspot || {}, d6 = d.dec6;
    var h = '<h2>Margin accounts</h2><div class="card" style="padding:8px 12px">' + accLine((d.accounts || {}).ibkr) + accLine((d.accounts || {}).webull) +
      '<p class="note">Both accounts are margin accounts. Since June 4, 2026 there is no pattern day trader rule (no $25k minimum, no day-trade count). ' +
      'What matters now is intraday buying power: a margin call that isn\'t covered can limit the account to closing trades for 90 days. ' +
      'Cushion = money above the margin you need, as a % of the account (IBKR): under 25% = watch, under 10% = high risk, with a push.</p></div>';
    h += '<h2>Backup internet (iPhone hotspot)</h2><div class="card" style="padding:8px 12px;line-height:1.7">' +
      (hs.on_hotspot ? chip('ON BACKUP INTERNET', 'watch') : hs.on ? chip('READY', 'ok') : chip('OFF', '')) +
      (hs.home ? ' home Wi-Fi: <b>' + esc(hs.home) + '</b>' : '') + '<br>' +
      'Hotspot name <input id="hsS" value="' + esc(hs.ssid) + '" placeholder="e.g. Alex\'s iPhone" style="width:160px;font-size:16px"> ' +
      'password <input id="hsP" type="password" placeholder="' + (hs.has_password ? 'saved' : 'hotspot password') + '" style="width:130px;font-size:16px"><br>' +
      '<label><input type="checkbox" id="hsOn"' + (hs.on ? ' checked' : '') + '> Switch to the hotspot when home internet is down for 90 seconds</label><br>' +
      '<button id="hsSave" ' + BTN + '>Save</button><button id="hsTest" ' + BTN + '>Test now</button>' +
      ((hs.log || []).length ? '<div style="font-size:12px;color:var(--dim,#8aa)">' + hs.log.map(function (x) { return esc(et(x.at)) + ' ' + esc(x.text); }).join('<br>') + '</div>' : '') +
      '<p class="note">Your iPhone must be at home with Personal Hotspot on (Settings &gt; Personal Hotspot &gt; Allow Others to Join). ' +
      'The Mac then tries home Wi-Fi again every 15 minutes and pushes you each time it switches. It uses a little of your phone plan\'s data. ' +
      'The password is kept only on the Mac (data/safety.json) and never shown again.</p></div>';
    h += '<h2>Nasdaq/NYSE overnight session check (Dec 6)</h2><div class="card" style="padding:8px 12px;line-height:1.6">' +
      'Runs by itself on ' + esc(d.dec6_date) + ' at 9:20 pm and 12:30 am ET and pushes the result. ' +
      '<button id="d6Run" ' + BTN + '>Run now</button>' +
      (d6 ? '<div style="margin-top:4px">' + chip(d6.ok ? 'ALL OK' : 'NEEDS A LOOK', d6.ok ? 'ok' : 'watch') + 'last run ' + esc(et(d6.at)) + ' ET<br>' +
        d6.lines.map(esc).join('<br>') + '</div>' : '') + '</div>';
    hbox.innerHTML = h;
    $('hsSave').onclick = function () {
      var b = { ssid: $('hsS').value, on: $('hsOn').checked }; if ($('hsP').value) b.password = $('hsP').value;
      postJ('/api/plus/safety/hotspot', b).then(healthPaint);
    };
    $('hsTest').onclick = function () { postJ('/api/plus/safety/hotspot/test', {}).then(function (r) { alert(r.error || r.text); }); };
    $('d6Run').onclick = function () { $('d6Run').textContent = 'Checking…'; postJ('/api/plus/safety/dec6', {}).then(function () { load(); }); };
  }
  function healthCard() {
    var svcs = $('svcs'); if (!svcs) return;
    hbox = document.createElement('div'); svcs.parentNode.appendChild(hbox);
    load(); setInterval(function () { if (!document.hidden && !(document.activeElement && document.activeElement.tagName === 'INPUT')) load(); }, 60000);
  }

  if (page === 'phone.html') { offline(); phoneDown(); phoneCard(); }
  else if (page === 'ibkr.html' || page === 'webull.html') boardBanner();
  else if (page === 'health.html') healthCard();
})();
