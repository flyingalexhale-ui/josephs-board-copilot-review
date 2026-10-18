/* Night desk section: automatic IB Gateway login (jb-ibc). Data: /api/plus/ibauto */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var el = ND.section('ibauto', 'IB Gateway auto-login');
  var LOOK = { 'logged in': 'c-g', 'waiting for approval': 'c-a', 'starting': 'c-b', 'login failed': 'c-r', 'unknown': 'c-d' };
  function draw(d) {
    if (!d.on) {
      el.innerHTML = '<p class="note">Off. ' + (d.setup ? 'Your login is saved; switch it back on with' : 'To set it up, run this on the Mac:') + ' <code>cd ~/josephs-board && ./jb ibkr-autologin</code>. ' +
        'It starts IB Gateway and types your IBKR login for you; you only tap Approve in IBKR Mobile, about once a week and after a restart of the Mac.</p>';
      return;
    }
    el.innerHTML = '<div class="card"><span class="chip ' + (LOOK[d.state] || 'c-d') + '">' + esc(d.state.toUpperCase()) + '</span> ' +
      (d.since ? '<span class="note">since ' + esc(ND.time(d.since)) + '</span>' : '') +
      ' · IBKR board ' + (d.bridge ? '<span class="up">connected</span>' : '<span class="dn">not connected</span>') +
      (d.state === 'waiting for approval' ? '<p><b>Open IBKR Mobile on your phone and tap Approve.</b> It asks again every few minutes until you do.</p>' : '') +
      (d.state === 'login failed' ? '<p class="err">IBKR refused the username or password. On the Mac run <code>./jb ibkr-autologin</code> again to type them fresh.</p>' : '') +
      '<p class="note">Last login: ' + (d.last_login ? esc(ND.time(d.last_login)) : 'not seen yet') + ' · last approval request: ' + (d.last_2fa ? esc(ND.time(d.last_2fa)) : 'none seen') + '</p>' +
      (d.lines && d.lines.length ? '<details class="note"><summary>Latest log lines</summary><pre style="white-space:pre-wrap;font-size:11px">' + esc(d.lines.join('\n')) + '</pre></details>' : '') + '</div>' +
      '<p class="note">IBC (free, open source) starts IB Gateway, logs in, and restarts it every night at 11:50 pm New York time (during IBKR\'s own nightly reset, so you lose about a minute of prices). ' +
      'IBKR asks for a fresh IB Key approval about once a week (usually after the weekend) and whenever IB Gateway starts from scratch (a Mac restart, or <code>./jb restart ibgateway</code>; a plain <code>./jb restart</code> leaves it running). The board pushes you when it is waiting. To stop it: <code>./jb ibkr-autologin off</code>.</p>';
  }
  function load() { ND.api('ibauto').then(draw).catch(function () {}); }
  load();
  setInterval(load, 30000);
})();
