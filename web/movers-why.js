/* Alex Hale's Board -- why the scanner's movers are moving (jb-scanwhy, deep search ideas 50 and 52). Adds, never removes:
 *   a one-line summary above the table (what most movers have in common right now)
 *   filter chips: All, Own move, Heavy volume, Breakouts, Market-led, Sector-led, Stretched, Trending
 *   tags after each symbol (OWN MOVE, MARKET-LED, HEAVY VOL, BREAKOUT, STRETCHED...), tap one for the why line
 * Board math only (the scanner's own 5-minute windows and the board's price record). Data: /api/plus/scanwhy.
 * Hooked in by one <script src="movers-why.js"> line on movers.html.
 */
(function () {
  'use strict';
  var body = document.getElementById('rows'), board = document.getElementById('board');
  if (!body || !board || location.protocol.indexOf('http') !== 0) return;
  var D = {}, F = '';
  try { F = localStorage.getItem('jbScanWhyF') || ''; } catch (e) {}
  var CLS = { 'OWN MOVE': 'sw-a', 'HEAVY VOL': 'sw-g', BREAKOUT: 'sw-g', BREAKDOWN: 'sw-r', STRETCHED: 'sw-a', THIN: 'sw-d',
    'TRENDING UP': 'sw-g', 'TRENDING DOWN': 'sw-r', 'LAGS INDEX': 'sw-d', 'AHEAD OF INDEX': 'sw-a' };
  var CHIPS = [['', 'All'], ['OWN MOVE', 'Own move'], ['HEAVY VOL', 'Heavy volume'], ['BREAK', 'Breakouts'], ['MARKET-LED', 'Market-led'],
    ['SECTOR-LED', 'Sector-led'], ['STRETCHED', 'Stretched'], ['TRENDING', 'Trending']];
  var st = document.createElement('style');
  st.textContent = '.sw-tag{display:inline-block;margin-left:5px;padding:0 4px;border-radius:3px;font-size:9.5px;font-weight:700;letter-spacing:.3px;border:1px solid #3a2a5c;background:#1c1430;color:#d6a8ff;vertical-align:middle;cursor:pointer;}' +
    '.sw-tag.sw-d{border-color:#2a3d52;background:#14202e;color:#9fb3c8;} .sw-tag.sw-a{border-color:#7a5c00;background:#2e2400;color:#ffc400;} .sw-tag.sw-g{border-color:#145c2c;background:#062a14;color:#5dff8f;} .sw-tag.sw-r{border-color:#6a1f2a;background:#2a0c12;color:#ff7a8a;}' +
    '#swSum{font-size:12px;line-height:1.4;color:var(--dim,#9fb3c8);margin:8px 10px 6px;} #swSum b{color:#d6a8ff;font-weight:700;letter-spacing:.3px;font-size:10.5px;margin-right:4px;}' +
    '#swChips{display:flex;gap:4px;flex-wrap:wrap;margin:0 10px 8px;} #swChips button{font:inherit;font-size:11px;background:transparent;border:1px solid var(--line-bright,#2a3d52);color:var(--dim,#9fb3c8);border-radius:4px;padding:3px 8px;cursor:pointer;}' +
    '#swChips button.on{background:var(--line-bright,#2a3d52);color:#fff;} #swChips .sw-n{opacity:.7;margin-left:3px;} tr.sw-hide{display:none;}' +
    '#swPop{position:fixed;z-index:60;max-width:300px;background:#0d1622;border:1px solid #3a2a5c;border-radius:6px;padding:8px 10px;font-size:12px;line-height:1.4;color:#dfe8f2;box-shadow:0 6px 18px rgba(0,0,0,.5);display:none;}';
  document.head.appendChild(st);
  var sum = document.createElement('div'); sum.id = 'swSum';
  sum.title = 'Board math from the scanner\'s own 5-minute windows and the board\'s price record, no outside commentary.';
  var chips = document.createElement('div'); chips.id = 'swChips';
  var tbl = board.querySelector('table'); board.insertBefore(sum, tbl); board.insertBefore(chips, tbl);     // inside the table's own box: no layout change
  var pop = document.createElement('div'); pop.id = 'swPop'; document.body.appendChild(pop);
  document.addEventListener('click', function (e) { if (!e.target.classList || !e.target.classList.contains('sw-tag')) pop.style.display = 'none'; });

  function match(t, f) { return !f || (t || []).some(function (x) { return x.indexOf(f) === 0; }); }
  function drawChips() {
    chips.innerHTML = CHIPS.map(function (c) {
      var n = c[0] ? Object.keys(D).filter(function (s) { return match(D[s].tags, c[0]); }).length : 0;
      return '<button data-f="' + c[0] + '" class="' + (c[0] === F ? 'on' : '') + '">' + c[1] + (c[0] && n ? '<span class="sw-n">' + n + '</span>' : '') + '</button>';
    }).join('');
  }
  chips.addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    F = b.dataset.f; try { localStorage.setItem('jbScanWhyF', F); } catch (x) {}
    drawChips(); decorate();
  });
  function decorate() {
    [].forEach.call(body.querySelectorAll('tr'), function (tr) {
      var c = tr.querySelector('td.sym'); if (!c) return;
      var s = c.firstChild && c.firstChild.nodeValue, d = D[s] || {};
      if (!c.querySelector('.sw-tag')) (d.tags || []).slice(0, 3).forEach(function (t, i) {      // at most 3, so the table keeps its shape
        var b = document.createElement('span'); b.className = 'sw-tag ' + (CLS[t] || ''); b.textContent = t;
        if (i === 2 && d.tags.length > 3) b.textContent += ' +' + (d.tags.length - 3);
        b.dataset.sym = s; b.title = d.tags.join(', ') + (d.line ? ': ' + d.line : ''); c.appendChild(b);
      });
      tr.classList.toggle('sw-hide', !!F && !match(d.tags, F));
    });
  }
  body.addEventListener('click', function (e) {
    var t = e.target; if (!t.classList || !t.classList.contains('sw-tag')) return;
    e.stopPropagation();
    var d = D[t.dataset.sym] || {};
    pop.textContent = t.dataset.sym + ' ' + (d.tags || []).join(' \u00b7 ') + ': ' + (d.line || 'No why line yet.');
    var r = t.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(window.innerWidth - 310, r.left)) + 'px'; pop.style.top = (r.bottom + 6) + 'px'; pop.style.display = 'block';
  });
  new MutationObserver(decorate).observe(body, { childList: true });
  function load() {
    if (document.hidden) return;
    fetch('/api/plus/scanwhy', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d) return;
      D = d.tags || {};
      sum.innerHTML = '<b>WHY</b>'; sum.appendChild(document.createTextNode(d.summary || ''));
      drawChips();
      [].forEach.call(body.querySelectorAll('.sw-tag'), function (t) { t.remove(); });
      decorate();
    }).catch(function () {});
  }
  drawChips();
  setTimeout(load, 1500); setInterval(load, 60000);
})();
