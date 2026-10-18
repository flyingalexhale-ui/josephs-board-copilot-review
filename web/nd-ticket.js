/* Night desk section: the order ticket (jb-ticket). The form itself is web/ticket-ui.js. */
(function () {
  'use strict';
  var el = window.ND.section('t-ticket', 'Order ticket (IBKR and Webull, limit orders)');
  var s = document.createElement('script'); s.src = 'ticket-ui.js';
  s.onload = function () { window.JBTicket.mount(el); };
  document.head.appendChild(s);
})();
