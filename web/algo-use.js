/* Alex Hale's Board -- "Use This Strategy" button on the algo tabs (jb-usestrat).
 * Next to Run backtest: switches the PAPER algo to the strategy picked in the list, then restarts it
 * (gateway/algoctl.py, action "use"). Live trading is never started or changed: that stays ./jb live.
 * Hooked in by one <script src="algo-use.js"> line on algo.html.
 */
(function () {
  'use strict';
  var run = document.getElementById('b-run'), sel = document.getElementById('b-strat');
  if (!run || !sel || document.getElementById('b-use')) return;
  var algo = (new URLSearchParams(location.search).get('name') || '').toLowerCase() === 'ibkr' ? 'ibkr' : 'webull';
  var NICE = algo === 'ibkr' ? 'IBKR' : 'Webull';
  var btn = document.createElement('button');
  btn.type = 'button'; btn.id = 'b-use'; btn.className = 'go'; btn.textContent = 'Use This Strategy';
  btn.title = 'The paper algo runs the strategy picked in the list (paper only)';
  btn.style.cssText = 'margin-left:6px;background:#1f5f8b;border-color:#1f5f8b';
  var msg = document.createElement('div');
  msg.className = 'msg'; msg.id = 'b-use-msg'; msg.style.cssText = 'margin-top:6px';
  run.parentNode.insertBefore(btn, run.nextSibling);
  run.parentNode.appendChild(msg);
  var busy = false;

  function control(opts) {
    return fetch('/api/algo/' + algo + '/control', Object.assign({credentials: 'same-origin', cache: 'no-store'}, opts || {}))
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); });
  }
  function say(t, cls) { msg.textContent = t; msg.className = 'msg' + (cls ? ' ' + cls : ''); }

  btn.addEventListener('click', function () {
    var f = sel.value; if (!f || busy) return;
    busy = true; say('…');
    control().then(function (c) {
      if (f === c.current) { say('The paper algo already runs ' + f + '.', 'good'); return; }
      var tested = c.run && c.run.strategy === f && c.run.ok;
      var q = 'Switch the ' + NICE + ' PAPER algo to ' + f + '?\n\n' +
        'It restarts the paper algo with this strategy (no real orders). Live trading is not changed.\n' +
        (c.killed ? '\nThe algo is STOPPED by you: it stays stopped until you press Resume trading.\n' : '') +
        (tested ? '\nBacktest of ' + f + ': done (results on this page).'
                : '\nYou have not run a backtest of ' + f + ' on this page yet. Press Cancel, then Run backtest to check it first.');
      if (!confirm(q)) { say(''); return; }
      return control({method: 'POST', headers: {'Content-Type': 'application/json'},
                      body: JSON.stringify({action: 'use', strategy: f})}).then(function () {
        say('Switched ✓ The paper algo is restarting with ' + f + ' (about 30 seconds; the Strategy line above updates).', 'good');
      });
    }).catch(function (e) { say('Not switched: ' + e.message, 'bad'); })
      .then(function () { busy = false; });
  });
})();
