/* Alex Hale's Board -- Rules on the Phone page (jb-lab, deep search idea 54).
 *   A small "Rules" card: rules on, what fired last, open paper P/L, link to the Rules page (rules.html)
 *   #tk=SYM:side:qty:broker in the link of a rule's ticket push fills in the order ticket (you still check it and tap Send)
 * Data: /api/plus/rules (gateway/rules.py). Hooked in by one <script src="rules-ui.js"> line on phone.html.
 */
(function () {
  'use strict';
  if (location.protocol.indexOf('http') !== 0) return;
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // ---- ticket filled in from a rule's push
  function prefill() {
    var m = /[#&]tk=([A-Za-z0-9.=\-]{1,12}):(buy|sell):(\d{0,6}):(ibkr|webull)/.exec(location.hash);
    if (!m) return;
    var tries = 0;
    (function wait() {
      var box = $('tkBox'), sym = box && box.querySelector('.tk-sym');
      if (!sym) { if (++tries < 40) setTimeout(wait, 250); return; }
      var br = box.querySelector('[data-g="broker"] [data-v="' + m[4] + '"]'), sd = box.querySelector('[data-g="side"] [data-v="' + m[2] + '"]');
      if (br) br.click();
      if (sd) sd.click();
      sym.value = m[1].toUpperCase(); sym.dispatchEvent(new Event('input'));
      if (+m[3]) box.querySelector('.tk-qty').value = m[3];
      var msg = box.querySelector('.tk-msg'); if (msg) msg.textContent = 'Filled in by a rule. Set the limit, check it, then Send.';
      box.scrollIntoView({ behavior: 'smooth', block: 'start' });
      try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
    })();
  }
  prefill();
  window.addEventListener('hashchange', prefill);

  // ---- Rules card
  if (!$('mov')) return;
  var card = document.createElement('div'); card.className = 'card'; card.id = 'rulesCard';
  card.innerHTML = '<h2>Rules <span>if this, then that</span></h2><div id="rulesB" class="empty">Loading…</div>';
  var anchor = $('inNotes') || $('heldc') || $('mov').parentNode;
  anchor.parentNode.insertBefore(card, anchor.nextSibling);
  function ago(ms) { var m = Math.round((Date.now() - ms) / 60000); return m < 60 ? m + 'm ago' : m < 1440 ? Math.round(m / 60) + 'h ago' : Math.round(m / 1440) + 'd ago'; }
  function load() {
    if (document.hidden) return;
    fetch('/api/plus/rules', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d) return;
      var on = d.rules.filter(function (r) { return r.on; }).length, pl = 0, open = 0;
      d.paper.forEach(function (o) { if (o.open && !o.test && o.pl != null) { pl += o.pl; open++; } });
      var h = '<div style="font-size:13px">' + on + ' rule' + (on === 1 ? '' : 's') + ' on' + (open ? ' · ' + open + ' open paper order' + (open === 1 ? '' : 's') +
        ' <b style="color:' + (pl >= 0 ? '#39e07a' : '#ff6b5e') + '">' + (pl >= 0 ? '+$' : '-$') + Math.abs(pl).toFixed(2) + '</b>' : '') + '</div>';
      d.log.slice(0, 3).forEach(function (l) { h += '<div style="font-size:12px;color:var(--dim,#9fb3c8);margin-top:4px">' + ago(l.at) + ': <b>' + esc(l.name) + '</b> ' + esc(l.reason) + '</div>'; });
      h += '<div style="margin-top:8px"><a href="rules.html" style="color:#7fd6ff">Make or change rules</a></div>';
      $('rulesB').className = ''; $('rulesB').innerHTML = h;
    }).catch(function () {});
  }
  load(); setInterval(load, 60000);
})();
