/* Alex Hale's Board -- System check card (jb-syscheck), on the phone page and the Health tab.
 * Shows the overall state (ALL LIVE / to check / down) with a "Run check" button that tests
 * everything live and pushes the answer to the phone; "Details" opens syscheck.html.
 * Hooked in by one <script src="syscheck-card.js"> line on phone.html and health.html.
 */
(function () {
  'use strict';
  if (location.protocol.indexOf('http') !== 0) return;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function api(p, data) {
    return fetch('/api/plus/syscheck' + p, data ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; });
    });
  }
  var COL = { ok: ['#06240f', '#2f7a44', '#00ff41'], warn: ['#2a1d06', '#8a6420', '#ffb648'], bad: ['#2a0a0a', '#ff3b30', '#ff3b30'] };
  var HEAD = { ok: 'ALL LIVE', warn: 'LIVE, a few things to check', bad: 'SOMETHING IS DOWN' };
  var box = document.createElement('div');
  box.style.cssText = 'border:1px solid #164060;border-radius:10px;padding:12px;margin:10px 0;background:#071a30;font-family:inherit';
  box.innerHTML = '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
    '<div style="flex:1;min-width:160px"><div style="font-size:11px;color:#6f8aa3;letter-spacing:.1em">SYSTEM CHECK</div>' +
    '<div class="scBig" style="font-size:17px;font-weight:700;margin-top:2px">…</div><div class="scSub" style="font-size:12px;color:#6f8aa3;margin-top:2px;line-height:1.4"></div></div>' +
    '<button class="scRun" style="font:inherit;font-weight:700;padding:10px 14px;border-radius:8px;border:1px solid #2f7a44;background:#0d4a26;color:#f2f2f2">Run check</button>' +
    '<a href="syscheck.html" style="color:#00e5ff;text-decoration:none;font-size:13px">Details ›</a></div>';
  var big = box.querySelector('.scBig'), sub = box.querySelector('.scSub'), btn = box.querySelector('.scRun');
  function show(d, live) {
    var c = COL[d.overall] || COL.warn, n = d.counts || {};
    box.style.background = c[0]; box.style.borderColor = c[1]; big.style.color = c[2];
    big.textContent = HEAD[d.overall] || '';
    var bad = [];
    (d.groups || []).forEach(function (g) { g.items.forEach(function (i) { if (i.state === 'bad') bad.push(i.name); }); });
    sub.innerHTML = (bad.length ? 'Down: ' + esc(bad.slice(0, 4).join(', ')) + (bad.length > 4 ? '…' : '') + '<br>' : '') +
      esc(n.ok) + ' OK' + (n.warn ? ', ' + esc(n.warn) + ' to check' : '') +
      (live ? (d.push && d.push.sent ? ' · result pushed to your phone' : ' · could not push to your phone') : '');
  }
  var busy = false;
  function load() { if (!busy) api('').then(function (d) { if (!busy) show(d, false); }).catch(function () {}); }
  btn.onclick = function () {
    busy = true; btn.disabled = true; btn.textContent = 'Checking…';
    api('/run', {}).then(function (d) { show(d, true); }).catch(function (e) { sub.textContent = 'Check failed: ' + e.message; })
      .then(function () { busy = false; btn.disabled = false; btn.textContent = 'Run check'; });
  };
  api('').then(function (d) {           // only shows when the System check is installed
    var first = document.querySelector('.card') || document.querySelector('header');
    if (document.querySelector('header') && !document.querySelector('.card')) {
      var h = document.querySelector('header'); h.parentNode.insertBefore(box, h.nextSibling);   // Health tab
    } else if (first) first.parentNode.insertBefore(box, first);                                     // phone page: at the top
    else document.body.insertBefore(box, document.body.firstChild);
    show(d, false);
    setInterval(load, 60000);
  }).catch(function () {});
})();
