/* Alex Hale's Board -- "Strategy lab" link on the algo tabs (jb-lab, deep search ideas 55 and 56).
 * Adds two small buttons next to Run backtest: build a strategy from lists, or test how robust one is.
 * Hooked in by one <script src="algo-lab.js"> line on algo.html.
 */
(function () {
  'use strict';
  var run = document.getElementById('b-run'); if (!run) return;
  var algo = (new URLSearchParams(location.search).get('name') || '').toLowerCase() === 'ibkr' ? 'ibkr' : 'webull';
  [['Strategy lab', 'lab.html?algo='], ['Robustness test', 'lab.html?rob=1&algo=']].forEach(function (x) {
    var a = document.createElement('a'); a.className = 'go'; a.textContent = x[0]; a.href = x[1] + algo; a.target = '_top';
    a.style.cssText = 'margin-left:6px;text-decoration:none;display:inline-block';
    run.parentNode.appendChild(a);
  });
})();
