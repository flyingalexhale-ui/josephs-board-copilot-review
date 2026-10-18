/* Alex Hale's Board -- page titles (jb-desk). Loads titles.css and writes each page title in
   Title Case (NIGHT DESK -> Night Desk), which the engraved title font shows as large and small
   capitals. Short names like IBKR and ETF stay in capitals. Loaded by looks.js on every page. */
(function () {
  'use strict';
  if (window.__jbTitles) return;
  window.__jbTitles = true;
  var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = 'titles.css';
  (document.head || document.documentElement).appendChild(l);
  var KEEP = { IBKR: 1, ETF: 1, ETFS: 1, AI: 1, TV: 1, US: 1 };
  function tc(s) {
    return s.replace(/[A-Za-z][A-Za-z']*/g, function (w) {
      var u = w.toUpperCase();
      if (KEEP[u]) return u === 'ETFS' ? 'ETFs' : u;
      return u.charAt(0) + w.slice(1).toLowerCase();
    });
  }
  function fix(h) {
    for (var n = h.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3 && /[A-Z]{2}/.test(n.nodeValue)) { var v = tc(n.nodeValue); if (v !== n.nodeValue) n.nodeValue = v; }
    }
  }
  function all() { [].forEach.call(document.querySelectorAll('header h1'), function (h) {
    fix(h);
    if (!h.__jbT && window.MutationObserver) { h.__jbT = 1; new MutationObserver(function () { fix(h); }).observe(h, { childList: true, characterData: true, subtree: true }); }
  }); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', all); else all();
})();
