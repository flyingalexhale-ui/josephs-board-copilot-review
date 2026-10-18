/* Alex Hale's Board -- Night Desk parts on other pages (jb-desk). night.html?only=pick,md-gap shows
 * just those sections (no title, no tabs), so the Scanner tab can carry the ETF pick of the hour,
 * the pre-market gapper checklist and the leveraged ETF twins. They are the very same parts, with
 * their own switches (the ETF pick push included): a change on one page shows on the other.
 * Hooked in by one <script src="nd-only.js"> line in night.html's head. */
(function () {
  'use strict';
  var only = new URLSearchParams(location.search).get('only');
  if (!only) return;
  var ids = only.split(',').filter(function (x) { return /^[a-z0-9-]+$/.test(x); });
  var keep = ids.map(function (i) { return '#h-' + i + ',#s-' + i; }).join(',');
  var st = document.createElement('style');
  st.textContent = '.wrap > header,.wrap > p.note,#ndtabs,#jump,#ndRecap,#foot{display:none !important;}' +
    '.ndpane{display:block !important;} .ndpane > h2,.ndpane > div{display:none !important;}' +
    (keep ? keep + '{display:block !important;}' : '') +
    'body{padding-top:0 !important;padding-bottom:0 !important;} .wrap{max-width:none !important;padding:0 2px 10px !important;}' +
    '#jbNav,#jbNavSheet,#jbNavDim{display:none !important;}';
  (document.head || document.documentElement).appendChild(st);
  window.__ndOnly = ids;
})();
