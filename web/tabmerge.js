/* Alex Hale's Board -- merged tabs (jb-desk). Three tabs now live inside others:
 *    Health      -> the Signals tab, on-page tab "Health"
 *    My Trading  -> the Portfolio tab, on-page tab "My Trading"
 *    Recap       -> a video player near the top of the Night Desk tab (the Recap page itself still works)
 * Their buttons are hidden from the top bar (nothing is deleted), and anything that still opens them
 * (old links like ./#health, the phone Menu, a remembered tab) lands in the right place.
 * On a wide computer screen the remaining 15 tabs sit in two even rows (8 + 7).
 * Hooked in by one <script src="tabmerge.js"> line on index.html. */
(function () {
  'use strict';
  var MAP = { health: ['signals', 'health'], trading: ['portfolio', 'trading'], recap: ['night', null] };
  var css = document.createElement('style');
  css.textContent = '#bar .tab[data-tab="health"],#bar .tab[data-tab="trading"],#bar .tab[data-tab="recap"]{display:none !important;}' +
    '@media (min-width:1100px){' +
    '#bar{grid-template-columns:max-content repeat(8,minmax(0,1fr)) max-content max-content max-content !important;}' +
    '#bar .xlink{grid-column:10 !important;} #bar #jb-theme{grid-column:10 !important;} #bar #lgBtn{grid-column:11 !important;} #bar #jbChanBtn{grid-column:12 !important;}}';
  document.head.appendChild(css);
  function sub(page, tab) {
    if (!tab) return;
    try { localStorage.setItem('jb_sub_' + page, tab); } catch (e) {}
    var f = document.querySelector('#panes iframe[src^="' + page + '.html"]');
    if (f) try { f.contentWindow.postMessage({ type: 'jb-subtab', page: page, tab: tab }, location.origin); } catch (e) {}
  }
  function go(name) {
    var m = MAP[name]; if (!m) return false;
    sub(m[0] === 'signals' ? 'signals' : m[0] === 'portfolio' ? 'portfolio' : m[0], m[1]);
    var b = document.querySelector('#bar .tab[data-tab="' + m[0] + '"]');
    if (b) b.click(); else location.hash = m[0];
    return true;
  }
  // a remembered or linked merged tab
  try { var s = localStorage.getItem('jb_dash_tab'); if (MAP[s]) { localStorage.setItem('jb_dash_tab', MAP[s][0]); sub(MAP[s][0], MAP[s][1]); } } catch (e) {}
  var h = (location.hash || '').slice(1);
  if (MAP[h]) { sub(MAP[h][0], MAP[h][1]); history.replaceState(null, '', '#' + MAP[h][0]); }
  window.addEventListener('hashchange', function () { var n = (location.hash || '').slice(1); if (MAP[n]) { history.replaceState(null, '', '#' + MAP[n][0]); go(n); } });
  // a click on a hidden button (keyboard shortcuts, the phone Menu, the tab list)
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('#bar .tab');
    if (b && MAP[b.dataset.tab]) { e.stopPropagation(); e.preventDefault(); go(b.dataset.tab); }
  }, true);
})();
