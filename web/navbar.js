/* Alex Hale's Board -- phone bottom bar (jb-navchart).
 * On a phone (or any small touch screen) every page gets a bar fixed to the bottom of the screen:
 *   Back  -- the page or tab you came from (on the full board it steps back through the tabs you opened)
 *   Home  -- the phone page
 *   Chart -- the any-ticker Chart page
 *   Menu  -- every page and tab as big boxes, one tap away
 * Only the top page draws it (tabs inside the full board don't get a second bar). Computers are
 * unchanged. Hooked in by one <script src="navbar.js"> line per page (add-navchart.py).
 */
(function () {
  'use strict';
  if (window.__jbNav) return;
  window.__jbNav = true;
  try { if (window.top !== window) return; } catch (e) { return; }
  var small = window.matchMedia && (window.matchMedia('(max-width: 640px)').matches ||
              window.matchMedia('(pointer: coarse) and (max-width: 1024px)').matches);
  if (!small) return;

  var PAGES = [
    ['Phone Home', 'phone.html'], ['Full Board', './'], ['Order Ticket', 'phone.html#ticket'],
    ['IBKR', './#ibkr'], ['Webull', './#webull'], ['World', './#world'],
    ['Scanner', './#scanner'], ['Signals', './#signals'], ['Night Desk', './#night'],
    ['Macro', './#macro'], ['Analysis', './#analysis'], ['My Trading', './#trading'],
    ['Chart', 'chart.html'], ['Report Card', './#report'], ['Portfolio', './#portfolio'],
    ['News', './#news'], ['IBKR Algo', './#algo-ibkr'], ['Webull Algo', './#algo-webull'],
    ['Ask Claude', './#claude'], ['Recap Video', './#recap'], ['System Check', 'syscheck.html'],
    ['Health', './#health'], ['Alex\'s Channel', 'channel.html']];
  var file = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  var onBoard = file === 'index.html' || file === '';
  var here = file === 'phone.html' ? 'home' : file === 'chart.html' ? 'chart' : '';

  var css = document.createElement('style');
  css.textContent =
    '#jbNav{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;display:flex;background:#04101e;border-top:1px solid #2a6a8a;' +
    'padding-bottom:env(safe-area-inset-bottom,0px);font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;-webkit-user-select:none;user-select:none}' +
    '#jbNav button{flex:1;min-height:56px;background:transparent;border:0;color:#8fd8ff;font:inherit;font-size:11px;padding:6px 0 4px;cursor:pointer;-webkit-tap-highlight-color:transparent}' +
    '#jbNav button i{display:block;font-style:normal;font-size:21px;line-height:24px}' +
    '#jbNav button.on{color:#39e07a;background:#0d2a40}#jbNav button:disabled{opacity:.35}' +
    '#jbNavDim{position:fixed;inset:0;z-index:2147482998;background:rgba(0,0,0,.55);display:none}' +
    '#jbNavSheet{position:fixed;left:0;right:0;bottom:calc(56px + env(safe-area-inset-bottom,0px));z-index:2147482999;display:none;' +
    'background:#0b2238;border-top:2px solid #2a6a8a;border-radius:14px 14px 0 0;padding:12px 12px 14px;max-height:72vh;overflow-y:auto;' +
    'font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace}' +
    '#jbNavSheet h4{margin:0 0 10px;color:#8fd8ff;font-size:12px;letter-spacing:2px;font-weight:700}' +
    '#jbNavSheet .g{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}' +
    '#jbNavSheet a{display:flex;align-items:center;justify-content:center;min-height:46px;text-align:center;padding:6px 4px;border:1px solid #2bb6e6;' +
    'border-radius:8px;color:#bfeeff;text-decoration:none;font-size:13px;line-height:1.2;background:#071a30}' +
    '#jbNavSheet a.on{border-color:#39e07a;color:#39e07a}' +
    'body.jbnav-open #jbNavDim,body.jbnav-open #jbNavSheet{display:block}' +
    '#chgBan{bottom:calc(68px + env(safe-area-inset-bottom,0px)) !important}' +
    (onBoard ? '#panes{margin-bottom:calc(56px + env(safe-area-inset-bottom,0px))}'
             : 'body{padding-bottom:calc(64px + env(safe-area-inset-bottom,0px)) !important}');
  document.head.appendChild(css);

  // ---- Back
  var stack = [], stepping = false;
  function activeTab() { var b = document.querySelector('#bar .tab.active'); return b ? b.dataset.tab : null; }
  if (onBoard) {
    var last = null;
    setTimeout(function () { last = activeTab(); }, 300);
    var note = function () {                       // remember the tab we are leaving
      setTimeout(function () {
        var now = activeTab();
        if (now && last && now !== last && !stepping) stack.push(last);
        if (now) last = now;
        stepping = false;
        paint();
      }, 50);
    };
    document.addEventListener('click', function (e) { if (e.target.closest && e.target.closest('#bar .tab, .mb-menu')) note(); }, true);
    window.addEventListener('hashchange', note);
  }
  function sameSiteRef() { try { return document.referrer && new URL(document.referrer).origin === location.origin; } catch (e) { return false; } }
  function canBack() { return stack.length > 0 || (history.length > 1 && sameSiteRef()) || here !== 'home'; }
  function back() {
    if (stack.length) {
      var t = stack.pop(), b = document.querySelector('#bar .tab[data-tab="' + t + '"]');
      if (b) { stepping = true; b.click(); return; }
    }
    if (history.length > 1 && sameSiteRef()) { history.back(); return; }
    if (here !== 'home') location.href = 'phone.html';
  }

  // ---- bar + menu
  var bar = document.createElement('div'); bar.id = 'jbNav';
  bar.innerHTML = '<button type="button" data-a="back" aria-label="Back"><i>‹</i>Back</button>' +
    '<button type="button" data-a="home"' + (here === 'home' ? ' class="on"' : '') + '><i>⌂</i>Home</button>' +
    '<button type="button" data-a="chart"' + (here === 'chart' ? ' class="on"' : '') + '><i>📈</i>Chart</button>' +
    '<button type="button" data-a="menu"><i>☰</i>Menu</button>';
  var dim = document.createElement('div'); dim.id = 'jbNavDim';
  var sheet = document.createElement('div'); sheet.id = 'jbNavSheet';
  sheet.innerHTML = '<h4>GO TO</h4><div class="g">' + PAGES.map(function (p) {
    var on = (p[1] === 'phone.html' && here === 'home') || (p[1] === 'chart.html' && here === 'chart');
    return '<a href="' + p[1] + '"' + (on ? ' class="on"' : '') + '>' + p[0] + '</a>';
  }).join('') + '</div>';
  function menu(open) {
    document.body.classList.toggle('jbnav-open', open);
    var m = bar.querySelector('[data-a="menu"]');
    m.classList.toggle('on', open); m.innerHTML = open ? '<i>✕</i>Close' : '<i>☰</i>Menu';
  }
  function paint() { bar.querySelector('[data-a="back"]').disabled = !canBack(); }
  bar.addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    var a = b.dataset.a;
    if (a === 'menu') { menu(!document.body.classList.contains('jbnav-open')); return; }
    menu(false);
    if (a === 'back') back();
    else if (a === 'home') { if (here === 'home') window.scrollTo({ top: 0, behavior: 'smooth' }); else location.href = 'phone.html'; }
    else if (a === 'chart') { if (here !== 'chart') location.href = 'chart.html'; }
  });
  dim.addEventListener('click', function () { menu(false); });
  sheet.addEventListener('click', function (e) {
    var a = e.target.closest('a'); if (!a) return;
    menu(false);
    var h = a.getAttribute('href');
    if (h === 'phone.html#ticket' && here === 'home') { e.preventDefault(); ticket(); }
    else if (onBoard && h.indexOf('./#') === 0) {
      e.preventDefault();
      var tb = document.querySelector('#bar .tab[data-tab="' + h.slice(3) + '"]');
      if (tb) tb.click(); else location.hash = h.slice(2);
    }
  });
  function ticket(tries) {                          // phone page: scroll to the order ticket card
    var t = document.getElementById('tkBox'), c = t && t.closest('.card');
    if (c) c.scrollIntoView({ behavior: 'smooth', block: 'start' });
    else if ((tries || 0) < 20) setTimeout(function () { ticket((tries || 0) + 1); }, 300);
  }
  function mount() {
    document.body.appendChild(dim); document.body.appendChild(sheet); document.body.appendChild(bar);
    paint();
    if (here === 'home' && location.hash === '#ticket') ticket();
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') menu(false); });
})();
