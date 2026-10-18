/* Alex Hale's Board -- Report Card summary strip (jb-ease, deep search idea 18). Adds, never removes:
 * above the cards, the 3 features that have been right most often and the 3 least (live grades when there are
 * at least 20, else the 2-year replay), and a "Sort by" switch: As before (default), Most right, Newest.
 * Hooked in by one <script src="rc-summary.js"> line on report.html.
 */
(function () {
  'use strict';
  var cards = document.getElementById('cards');
  if (!cards) return;
  var S = null, sort = 'default';
  try { sort = localStorage.getItem('jbRcSort') || 'default'; } catch (e) {}
  var st = document.createElement('style');
  st.textContent = '#rcSum{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:flex-start;margin:0 0 10px;padding:10px 12px;border:1px solid var(--line,#1c2b3d);border-radius:8px;background:rgba(127,214,255,.04);font-size:12px;}' +
    '#rcSum .g{min-width:220px;flex:1;} #rcSum .h{font-size:11px;letter-spacing:.4px;text-transform:uppercase;color:var(--dim,#9fb3c8);margin-bottom:3px;}' +
    '#rcSum .r{display:flex;justify-content:space-between;gap:10px;padding:2px 0;cursor:pointer;} #rcSum .r:hover{color:#fff;} #rcSum .up{color:#5dff8f;} #rcSum .dn{color:#ff8a80;}' +
    '#rcSum .s{color:var(--dim,#9fb3c8);} #rcSort{display:flex;gap:4px;align-items:center;} #rcSort button{font:inherit;font-size:11px;background:transparent;border:1px solid var(--line-bright,#2a3d52);color:var(--dim,#9fb3c8);border-radius:4px;padding:4px 9px;cursor:pointer;}' +
    '#rcSort button.on{background:var(--line-bright,#2a3d52);color:#fff;}';
  document.head.appendChild(st);
  var box = document.createElement('div'); box.id = 'rcSum';
  cards.parentNode.insertBefore(box, cards);

  function score(f) {
    var lv = (f.live || {})[f.main] || {}, rp = f.replay && f.replay.h && f.replay.h[f.main];
    if (f.key === 'econ') return null;                         // a size-of-move card, not right or wrong
    if (lv.pct != null && lv.n >= 20) return { pct: lv.pct, n: lv.n, src: 'live' };
    if (rp && rp.pct != null && rp.n >= 20) return { pct: rp.pct, n: rp.n, src: 'replay' };
    return null;
  }
  function newest(f) { var t = f.trend || []; return t.length ? t[t.length - 1].week : ''; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function row(x) {
    return '<div class="r" data-k="' + esc(x.f.key) + '"><span>' + esc(x.f.name) + '</span><span class="' + (x.s.pct >= 50 ? 'up' : 'dn') + '">' + x.s.pct + '% <span class="s">' +
      x.s.n + ' ' + (x.s.src === 'live' ? 'live' : 'replay') + '</span></span></div>';
  }
  function draw() {
    if (!S) return;
    var rated = S.features.map(function (f) { return { f: f, s: score(f) }; }).filter(function (x) { return x.s; });
    rated.sort(function (a, b) { return b.s.pct - a.s.pct || b.s.n - a.s.n; });
    var top = rated.slice(0, 3), low = rated.length > 3 ? rated.slice(-3).reverse() : [];
    box.innerHTML = (rated.length ? '<div class="g"><div class="h">Right most often</div>' + top.map(row).join('') + '</div>' +
      (low.length ? '<div class="g"><div class="h">Right least often</div>' + low.map(row).join('') + '</div>' : '') :
      '<div class="g"><div class="h">Summary</div><span class="s">Needs at least 20 graded calls per feature (live or replay) to rank them.</span></div>') +
      '<div id="rcSort"><span class="s">Sort by:</span>' + [['default', 'As before'], ['right', 'Most right'], ['newest', 'Newest']].map(function (o) {
        return '<button data-o="' + o[0] + '" class="' + (o[0] === sort ? 'on' : '') + '">' + o[1] + '</button>'; }).join('') + '</div>';
    order();
  }
  function order() {
    if (!S) return;
    var by = {}; S.features.forEach(function (f, i) { by[f.key] = { f: f, i: i, s: score(f) }; });
    var els = [].slice.call(cards.querySelectorAll(':scope > .card[data-k]'));
    if (els.length < 2) return;
    els.sort(function (a, b) {
      var A = by[a.dataset.k] || { i: 99 }, B = by[b.dataset.k] || { i: 99 };
      if (sort === 'right') return ((B.s && B.s.pct) || -1) - ((A.s && A.s.pct) || -1) || A.i - B.i;
      if (sort === 'newest') return (newest(B.f || {}) > newest(A.f || {}) ? 1 : newest(B.f || {}) < newest(A.f || {}) ? -1 : 0) || A.i - B.i;
      return A.i - B.i;
    });
    var now = [].slice.call(cards.querySelectorAll(':scope > .card[data-k]'));
    if (els.every(function (e, i) { return e === now[i]; })) return;      // already in this order (no endless re-sorting)
    els.forEach(function (e) { cards.appendChild(e); });
  }
  box.addEventListener('click', function (e) {
    var b = e.target.closest('#rcSort button');
    if (b) { sort = b.dataset.o; try { localStorage.setItem('jbRcSort', sort); } catch (x) {} draw(); return; }
    var r = e.target.closest('.r[data-k]');
    if (r) { var c = cards.querySelector('.card[data-k="' + r.dataset.k + '"]'); if (c) { c.scrollIntoView({ behavior: 'smooth', block: 'center' }); c.click(); } }
  });
  new MutationObserver(order).observe(cards, { childList: true });
  function load() {
    fetch('/api/plus/reportcard', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.features) { S = d; draw(); } }).catch(function () {});
  }
  load(); setInterval(load, 5 * 60 * 1000);
})();
