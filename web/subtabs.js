/* Alex Hale's Board -- on-page tabs (jb-desk). Puts a second page inside a tab on this page, so two
 * board tabs become one with a switch at the top:
 *    Signals page:   Signals | Health        (health.html)
 *    Portfolio page: Portfolio | My Trading  (trading.html)
 * Nothing is removed: each page still works on its own, and the inner page is the real page.
 * The choice is remembered (jb_sub_<page>); the full board can also ask for a tab ({type:'jb-subtab'}).
 * Pages opened inside a tab (?embed=1) hide their own title. Hooked in by one <script src="subtabs.js">
 * line on signals.html, portfolio.html, health.html and trading.html. */
(function () {
  'use strict';
  if (window.__jbSub) return;
  window.__jbSub = true;
  var P = new URLSearchParams(location.search);
  if (P.get('embed') === '1') {                         // the inner page: no title, no second phone bar
    var s = document.createElement('style');
    s.textContent = '.wrap > header{display:none !important;} body{padding-top:0 !important;padding-bottom:0 !important;}' +
      '#jbNav,#jbNavSheet,#jbNavDim{display:none !important;}';
    document.head.appendChild(s);
    return;
  }
  var page = (location.pathname.split('/').pop() || '').replace('.html', '');
  var CONF = { signals: [['main', 'Signals'], ['health', 'Health', 'health.html']],
               portfolio: [['main', 'Portfolio'], ['trading', 'My Trading', 'trading.html']] }[page];
  if (!CONF) return;
  var KEY = 'jb_sub_' + page;
  var css = document.createElement('style');
  css.textContent =
    '#jbSubBar{display:flex;gap:0;margin:12px 4px 4px;border:1px solid var(--line-bright,#164060);border-radius:9px;overflow:hidden;max-width:420px;}' +
    '#jbSubBar button{flex:1;font:inherit;font-size:13px;font-weight:700;letter-spacing:.04em;padding:9px 12px;border:0;border-radius:0;margin:0;' +
    'background:transparent;color:var(--dim,#6f8aa3);cursor:pointer;min-width:0;}' +
    '#jbSubBar button+button{border-left:1px solid var(--line-bright,#164060);}' +
    '#jbSubBar button.on{background:var(--line-bright,#164060);color:#fff;}' +
    '.wrap.jb-sub-on > :not(header):not(#jbSubBar):not(#jbSubFrame){display:none !important;}' +
    '#jbSubFrame{display:none;width:1px;min-width:100%;border:0;min-height:70vh;background:transparent;}' +
    '.wrap.jb-sub-on #jbSubFrame{display:block;}' +
    '@media (max-width:640px){#jbSubBar{max-width:none;margin:10px 0 4px;}}';
  document.head.appendChild(css);
  var wrap = document.querySelector('.wrap'), hdr = wrap && wrap.querySelector('header');
  if (!wrap || !hdr) return;
  var bar = document.createElement('div'); bar.id = 'jbSubBar';
  bar.innerHTML = CONF.map(function (t) { return '<button type="button" data-t="' + t[0] + '">' + t[1] + '</button>'; }).join('');
  hdr.parentNode.insertBefore(bar, hdr.nextSibling);
  var frame = null, cur = null;
  function fit() {
    if (!frame || cur === 'main') return;
    try { var d = frame.contentDocument; if (d && d.body) frame.style.height = Math.max(400, d.documentElement.scrollHeight) + 'px'; } catch (e) {}
  }
  function show(t, save) {
    var def = CONF.filter(function (x) { return x[0] === t; })[0] || CONF[0];
    cur = def[0];
    [].forEach.call(bar.children, function (b) { b.classList.toggle('on', b.dataset.t === cur); });
    wrap.classList.toggle('jb-sub-on', cur !== 'main');
    if (cur !== 'main') {
      var src = def[2] + '?embed=1';
      if (!frame) { frame = document.createElement('iframe'); frame.id = 'jbSubFrame'; frame.title = def[1]; bar.parentNode.insertBefore(frame, bar.nextSibling); frame.addEventListener('load', fit); }
      if (frame.getAttribute('src') !== src) frame.setAttribute('src', src);
      try { frame.contentWindow.postMessage({ type: 'jb-visibility', visible: true }, location.origin); } catch (e) {}
    } else if (frame) { try { frame.contentWindow.postMessage({ type: 'jb-visibility', visible: false }, location.origin); } catch (e) {} }
    if (save) { try { localStorage.setItem(KEY, cur); } catch (e) {} }
    window.scrollTo(0, 0);
  }
  bar.addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) show(b.dataset.t, true); });
  window.addEventListener('message', function (e) {
    if (e.origin === location.origin && e.data && e.data.type === 'jb-subtab' && e.data.page === page) show(e.data.tab, true);
  });
  var first = null; try { first = localStorage.getItem(KEY); } catch (e) {}
  show(first || 'main', false);
  setInterval(fit, 1000);
})();
