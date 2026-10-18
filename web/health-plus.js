/* Health tab addition (jb-guardian, idea 46): the internet drops the Mac saw.
 * Data: /api/plus/guardian/outages. Hooked in by one <script> line on health.html. */
(function () {
  'use strict';
  var svcs = document.getElementById('svcs'); if (!svcs) return;
  var wrap = svcs.parentNode;
  var h = document.createElement('h2'); h.textContent = 'Internet drops';
  var box = document.createElement('div');
  wrap.appendChild(h); wrap.appendChild(box);
  var F = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  function load() {
    fetch('/api/plus/guardian/outages', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d) { h.style.display = box.style.display = 'none'; return; }
      var o = d.outages || [];
      box.innerHTML = (d.net_up ? '' : '<p class="note" style="color:#ffb648"><b>The Mac has no internet right now.</b></p>') +
        (o.length ? '<div style="background:var(--panel,#071a30);border:1px solid var(--line-bright,#164060);border-radius:8px;padding:6px 10px">' + o.slice(0, 12).map(function (x) {
          return '<div style="padding:4px 0;border-bottom:1px solid var(--line, #0d2740);font-size:12px">' + F.format(new Date(x.start)) + ' ET · ' + x.mins + ' min without internet</div>';
        }).join('') + '</div>' : '<p class="note">No drops recorded.</p>') +
        '<p class="note">The Mac checks the internet every 30 seconds. Drops of 2 minutes or more are listed and pushed to your phone when it comes back: prices and orders may have missed that stretch (on charts, filled-in stretches are drawn in a dimmer blue-grey).</p>';
    }).catch(function () {});
  }
  load(); setInterval(function () { if (!document.hidden) load(); }, 60000);
})();
