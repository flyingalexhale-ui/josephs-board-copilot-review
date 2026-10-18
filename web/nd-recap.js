/* Alex Hale's Board -- the recap video on the Night Desk (jb-desk). The Recap tab was folded into the
 * Night Desk: its player sits near the top of the page (under the intro line, above the section
 * tabs), with the list of recent recaps and Make one now. The full Recap page (recap.html) still works.
 * Data: /api/recap. Hooked in by one <script src="nd-recap.js"> line on night.html. */
(function () {
  'use strict';
  if (new URLSearchParams(location.search).get('only')) return;
  var wrap = document.querySelector('.wrap'), at = document.getElementById('jump') || document.getElementById('ndtabs');
  if (!wrap || !at) return;
  var css = document.createElement('style');
  css.textContent =
    '#ndRecap{margin:14px 4px 4px;background:var(--panel,#071a30);border:1px solid var(--line-bright,#164060);border-radius:10px;padding:12px;}' +
    '#ndRecap .rh{display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:8px;}' +
    '#ndRecap .rh b{font-size:12px;letter-spacing:.1em;color:var(--dim,#6f8aa3);text-transform:uppercase;}' +
    '#ndRecap .rh span{font-size:12px;color:var(--white,#f2f2f2);}' +
    '#ndRecap video{display:block;width:100%;max-width:640px;max-height:360px;margin:0 auto;background:#000;border-radius:8px;}' +
    '#ndRecap .rr{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:center;margin-top:10px;}' +
    '#ndRecap select{max-width:100%;} #ndRecap .rm{font-size:12px;color:var(--dim,#6f8aa3);}' +
    '#ndRecap a{color:#b39dff;font-size:12px;}';
  document.head.appendChild(css);
  var box = document.createElement('div'); box.id = 'ndRecap';
  box.innerHTML = '<div class="rh"><b>Recap Video</b><span id="ndrT">Loading…</span></div>' +
    '<video id="ndrV" controls playsinline preload="metadata"></video>' +
    '<div class="rr"><select id="ndrS" aria-label="Pick a recap"></select><button type="button" class="ghost" id="ndrM">Make One Now</button>' +
    '<a href="recap.html" target="_blank" rel="noopener">Recap Page ›</a><span class="rm" id="ndrMsg"></span></div>';
  wrap.insertBefore(box, at);
  var V = document.getElementById('ndrV'), S = document.getElementById('ndrS'), M = document.getElementById('ndrM'), msg = document.getElementById('ndrMsg');
  var data = null, cur = null, making = false;
  function len(s) { return s ? Math.floor(s / 60) + ':' + ('0' + (s % 60)).slice(-2) : ''; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function pick(r) {
    if (!r) return;
    cur = r.file; V.src = '/api/recap/video?f=' + encodeURIComponent(r.file);
    document.getElementById('ndrT').textContent = r.title + (r.seconds ? ' · ' + len(r.seconds) : '');
    S.value = r.file;
  }
  function api(body) {
    var o = body ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { credentials: 'same-origin', cache: 'no-store' };
    return fetch('/api/recap', o).then(function (r) { return r.json(); });
  }
  function refresh() {
    api().then(function (d) {
      var was = making; data = d; making = !!d.making;
      var rs = d.recaps || [];
      S.innerHTML = rs.map(function (r) { return '<option value="' + esc(r.file) + '">' + (r.weekly ? 'Week · ' : '') + esc(r.title) + '</option>'; }).join('');
      if (!rs.length) { document.getElementById('ndrT').textContent = 'No recap yet'; S.style.display = 'none'; }
      else { S.style.display = ''; if (!cur || (was && !making)) pick(rs[0]); else S.value = cur; }
      M.disabled = making;
      msg.textContent = making ? 'Making a recap… (a few minutes on the Mac)' : d.error ? 'Last try failed: ' + d.error.error : '';
    }).catch(function () { document.getElementById('ndrT').textContent = 'Recap not available'; });
  }
  S.onchange = function () { pick((data.recaps || []).filter(function (r) { return r.file === S.value; })[0]); };
  M.onclick = function () { M.disabled = true; api({ action: 'make' }).then(function (r) { if (!r.ok) msg.textContent = r.error || ''; making = true; refresh(); }); };
  refresh();
  setInterval(function () { if (!document.hidden && making) refresh(); }, 5000);
  setInterval(function () { if (!document.hidden) refresh(); }, 120000);
})();
