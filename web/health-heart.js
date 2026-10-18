/* Health tab addition (jb-ndtabs): the Mac heartbeat (healthchecks.io), the same as on Night Desk > Mac & Safety.
 * Data: /api/plus/alerts2. Hooked in by one <script> line on health.html. */
(function () {
  'use strict';
  var svcs = document.getElementById('svcs'); if (!svcs) return;
  var wrap = svcs.parentNode;
  var h = document.createElement('h2'); h.textContent = 'Mac heartbeat';
  var box = document.createElement('div');
  wrap.appendChild(h); wrap.appendChild(box);
  function ago(ms) { var m = Math.round((Date.now() - ms) / 60000); return m < 1 ? 'just now' : m < 60 ? m + ' min ago' : Math.round(m / 60) + ' h ago'; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function chip(t, bg, fg, bd) { return '<span style="display:inline-block;border-radius:3px;padding:1px 6px;font-size:10.5px;font-weight:700;border:1px solid ' + bd + ';background:' + bg + ';color:' + fg + '">' + t + '</span> '; }
  function paint(d) {
    var b = d.heartbeat || {};
    box.innerHTML = '<div style="background:var(--panel,#071a30);border:1px solid var(--line-bright,#164060);border-radius:8px;padding:8px 12px;font-size:12px;line-height:1.6">' +
      (!d.heartbeat_set ? chip('NOT SET UP', '#2e2400', '#ffc400', '#7a5c00') + 'Paste your healthchecks.io ping link on Night Desk &gt; Mac &amp; Safety.' :
        b.ok ? chip('OK', '#062a14', '#5dff8f', '#145c2c') + 'last ping ' + esc(ago(b.at)) + '.' :
        b.ok === false ? chip('PING FAILED', '#3a0b0b', '#ff8a80', '#7a1c1c') + esc(b.err) : 'Waiting for the first ping…') +
      (d.heartbeat_set ? ' <button id="hhNow" style="margin-left:6px">Ping now</button>' : '') + '</div>' +
      '<p class="note">The Mac pings healthchecks.io every 5 minutes. If the pings stop (power cut, internet down, Mac asleep or crashed), healthchecks.io pushes your phone through ntfy. Change the link on Night Desk &gt; Mac &amp; Safety.</p>';
    var n = document.getElementById('hhNow');
    if (n) n.onclick = function () {
      n.textContent = 'Pinging…';
      fetch('/api/plus/alerts2/heartbeat', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .then(function (r) { return r.json(); }).then(paint).catch(function () { n.textContent = 'Ping now'; });
    };
  }
  function load() {
    fetch('/api/plus/alerts2', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) paint(d); else h.style.display = box.style.display = 'none'; }).catch(function () {});
  }
  load(); setInterval(function () { if (!document.hidden) load(); }, 60000);
})();
