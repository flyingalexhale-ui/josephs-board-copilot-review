/* Alex Hale's Board -- link boxes at the bottom of the phone page (jb-calendar, matched in jb-sentiment).
 * All ten boxes in one even grid, in the cyan Night Desk / Order Ticket style:
 *   Night Desk · Order Ticket · IBKR Algo · Webull Algo · Full Board · Macro · Ask Claude ·
 *   Recap Video · System Check · Health
 * Two equal columns on a phone, five on a wider screen. "Order Ticket" scrolls down to the ticket
 * card, which stays where it is. Hooked in by one <script src="phone-links.js"> line on phone.html.
 */
(function () {
  'use strict';
  var LINKS = [['Night Desk', './#night'], ['Order Ticket', '#ticket'], ['IBKR Algo', './#algo-ibkr'], ['Webull Algo', './#algo-webull']];
  var NAMES = { 'full board': 'Full Board', 'recap video': 'Recap Video', 'system check': 'System Check' };
  var css = document.createElement('style');
  css.textContent =
    '.jb-qlinks{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0 0 8px}' +
    '@media (min-width:700px){.jb-qlinks{grid-template-columns:repeat(5,1fr)}}' +
    '.jb-qlinks a{display:flex;align-items:center;justify-content:center;min-height:44px;text-align:center;padding:10px 8px;border-radius:8px;' +
    'border:1px solid #164060;background:#071a30;color:#00e5ff;text-decoration:none;font-size:14px;font-weight:700;line-height:1.2;flex:none}';
  document.head.appendChild(css);
  var row = document.createElement('div');
  row.className = 'jb-qlinks';
  LINKS.forEach(function (l) {
    var a = document.createElement('a');
    a.href = l[1]; a.textContent = l[0];
    if (l[1] === '#ticket') a.onclick = function (e) {
      e.preventDefault();
      var t = document.getElementById('tkBox'), c = t && t.closest('.card');
      if (c) c.scrollIntoView({ behavior: 'smooth', block: 'start' });
      else { a.textContent = 'Ticket still loading…'; setTimeout(function () { a.textContent = 'Order Ticket'; }, 2000); }
    };
    row.appendChild(a);
  });
  var links = document.querySelector('.links'), h = document.querySelector('header');
  if (links) {
    links.parentNode.insertBefore(row, links);
    Array.prototype.slice.call(links.querySelectorAll('a')).forEach(function (a) {   // same links, moved into the grid
      var n = NAMES[a.textContent.trim().toLowerCase()];
      if (n) a.textContent = n;
      a.removeAttribute('style');
      row.appendChild(a);
    });
    links.style.display = 'none';
  }
  else if (h) h.parentNode.insertBefore(row, h.nextSibling); else document.body.insertBefore(row, document.body.firstChild);
})();
