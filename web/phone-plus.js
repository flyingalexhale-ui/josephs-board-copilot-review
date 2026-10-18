/* Alex Hale's Board -- Night desk cards on the phone page (jb-earn and later installs).
 *   Game plan   your note for the next session: read it or change it from the phone (jb-earn)
 *   Order ticket  buy or sell with a limit order on IBKR or Webull (jb-ticket, web/ticket-ui.js)
 * Each card shows only if its part of the Night desk is installed. Hooked in by one
 * <script src="phone-plus.js"> line on phone.html.
 */
(function () {
  'use strict';
  if (location.protocol.indexOf('http') !== 0) return;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function after(id) { var e = document.getElementById(id); return e ? e.closest('.card') : null; }
  function card(html, anchorId) {
    var c = document.createElement('div'); c.className = 'card'; c.innerHTML = html;
    var a = after(anchorId);
    if (a) a.parentNode.insertBefore(c, a.nextSibling); else document.body.appendChild(c);
    return c;
  }
  function api(p, data) {
    return fetch('/api/plus/' + p, data ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }
  // ---------------------------------------------------------------- order ticket (jb-ticket)
  api('ticket').then(function () {
    var c = card('<h2>Order ticket <span>limit orders</span></h2><div id="tkBox"></div>', 'heldc');
    var s = document.createElement('script'); s.src = 'ticket-ui.js';
    s.onload = function () { window.JBTicket.mount(c.querySelector('#tkBox')); };
    document.head.appendChild(s);
  }).catch(function () {});
  // ---------------------------------------------------------------- game plan (jb-earn)
  api('earn/plan').then(function (p) {
    var c = card('<h2>Game plan <a href="./#night">Night desk ›</a></h2><div id="gpView" style="white-space:pre-line;line-height:1.5"></div>' +
      '<textarea id="gpEdit" rows="5" style="display:none;width:100%;box-sizing:border-box;background:transparent;color:inherit;border:1px solid #2a3d52;border-radius:6px;padding:8px;font:inherit;font-size:14px"></textarea>' +
      '<div style="margin-top:6px"><button id="gpBtn" style="font:inherit;padding:6px 12px;border-radius:6px;border:1px solid #2a3d52;background:#14202e;color:#cfe0ee">Edit</button> <span id="gpMsg" style="font-size:12px;color:#7f93a8"></span></div>', 'trd');
    var view = c.querySelector('#gpView'), ed = c.querySelector('#gpEdit'), btn = c.querySelector('#gpBtn'), msg = c.querySelector('#gpMsg');
    function show(p) {
      view.innerHTML = p.text ? esc(p.text) : '<span style="color:#7f93a8">No plan yet. Tap Edit to write one; it is pushed to you at ' + esc(p.plan_time) + ' am before the open.</span>';
      msg.textContent = p.at ? 'saved ' + new Date(p.at).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) : '';
      ed.value = p.text || '';
    }
    show(p);
    btn.onclick = function () {
      if (ed.style.display === 'none') { ed.style.display = ''; view.style.display = 'none'; btn.textContent = 'Save'; ed.focus(); return; }
      msg.textContent = 'Saving…';
      api('earn/plan', { text: ed.value }).then(function (p2) { show(p2); ed.style.display = 'none'; view.style.display = ''; btn.textContent = 'Edit'; })
        .catch(function (e) { msg.textContent = 'Could not save: ' + e.message; });
    };
  }).catch(function () {});
})();
