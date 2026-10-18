/* Alex Hale's Board -- extra scanner columns (jb-mdata). Adds, never removes:
 *   Float / Rel vol (one column)  shares that trade freely (Yahoo; small floats move faster), and
 *            volume so far this session vs the same time on the last ~9 days
 *   tags     after the symbol: why it is moving (EARNINGS, ANALYST, 8-K, SECTOR, NO NEWS...),
 *            and GAP x/5 = how many of the 5 pre-market gapper checks it passes (Night desk)
 * Data: /api/plus/mdata/scan. Hooked in by one <script src="mdata-scan.js"> line on movers.html.
 */
(function () {
  'use strict';
  var body = document.getElementById('rows'), head = document.querySelector('#board thead tr');
  if (!body || !head || location.protocol.indexOf('http') !== 0) return;
  var D = {}, CLS = { 'NO NEWS': 'md-d', EARNINGS: 'md-a', FDA: 'md-a', DEAL: 'md-a' };
  var st = document.createElement('style');
  st.textContent = '.md-tag{display:inline-block;margin-left:5px;padding:0 4px;border-radius:3px;font-size:9.5px;font-weight:700;letter-spacing:.3px;border:1px solid #1e5a78;background:#0e2a3a;color:#7fd6ff;vertical-align:middle;}' +
    '.md-tag.md-d{border-color:#2a3d52;background:#14202e;color:#9fb3c8;} .md-tag.md-a{border-color:#7a5c00;background:#2e2400;color:#ffc400;} .md-tag.md-g{border-color:#145c2c;background:#062a14;color:#5dff8f;}' +
    'td.md-c,th.md-c{text-align:right;font-size:11px;line-height:1.25;padding-top:4px;padding-bottom:4px;} .md-rv{color:var(--dim,#9fb3c8);} .md-rv.md-hot{color:#5dff8f;font-weight:700;} #board{overflow-x:auto;}';
  document.head.appendChild(st);
  var th = document.createElement('th'); th.className = 'md-c'; th.innerHTML = 'Float<br>Rel vol';
  th.title = 'Float = shares that trade freely (Yahoo); under about 20M is small and can move fast. Rel vol = volume so far this session vs the same time of day on the last ~9 days; 2x or more (green) = unusually busy. Overnight it shows the last full day.';
  head.appendChild(th);
  function big(n) { return n == null ? '—' : n >= 1e9 ? (n / 1e9).toFixed(1) + 'B' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(n); }
  function syms() { return [].map.call(body.querySelectorAll('tr'), function (tr) { var c = tr.querySelector('td.sym'); return c ? c.firstChild && c.firstChild.nodeValue : null; }).filter(Boolean); }
  function decorate() {
    [].forEach.call(body.querySelectorAll('tr'), function (tr) {
      var c = tr.querySelector('td.sym');
      if (!c) { var e = tr.querySelector('td.empty'); if (e && e.colSpan === 8) e.colSpan = 9; return; }
      if (tr.querySelector('td.md-c')) return;
      var s = c.firstChild && c.firstChild.nodeValue, d = D[s] || {};
      (d.tags || []).forEach(function (t) { var b = document.createElement('span'); b.className = 'md-tag ' + (CLS[t] || ''); b.textContent = t; c.appendChild(b); });
      if (d.gap_score != null) { var g = document.createElement('span'); g.className = 'md-tag ' + (d.gap_score >= 4 ? 'md-g' : 'md-d'); g.textContent = 'GAP ' + d.gap_score + '/5'; g.title = 'Passes ' + d.gap_score + ' of the 5 pre-market gapper checks (details on the Night desk)'; c.appendChild(g); }
      var f = document.createElement('td'); f.className = 'md-c';
      f.appendChild(document.createTextNode(big(d.float)));
      var r = document.createElement('div'); r.className = 'md-rv' + (d.rv >= 2 ? ' md-hot' : ''); r.textContent = d.rv != null ? d.rv.toFixed(1) + 'x vol' : '—'; f.appendChild(r);
      if (d.short_pct != null) f.title = d.short_pct.toFixed(1) + '% of the float is sold short' + (d.short_ratio != null ? ', ' + d.short_ratio + ' days to cover' : '');
      tr.appendChild(f);
    });
  }
  new MutationObserver(decorate).observe(body, { childList: true });
  function load() {
    if (document.hidden) return;
    var list = syms(); if (!list.length) return;
    fetch('/api/plus/mdata/scan?syms=' + encodeURIComponent(list.join(',')), { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d && d.syms) { D = d.syms; [].forEach.call(body.querySelectorAll('td.md-c'), function (td) { td.remove(); }); [].forEach.call(body.querySelectorAll('.md-tag'), function (t) { t.remove(); }); decorate(); } })
      .catch(function () {});
  }
  setTimeout(load, 3000); setInterval(load, 60000);
})();
