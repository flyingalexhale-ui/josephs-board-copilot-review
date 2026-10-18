/* Alex Hale's Board -- colour-coded tab groups on the computer (jb-ease, deep search idea 17). Colours only:
 * prices blue (IBKR, Webull, World, Scanner, Chart), trading green (algos, My Trading, Portfolio),
 * research purple (News, Signals, Analysis, Macro, Night Desk, Report Card, Ask Claude, Recap), system grey (Health).
 * The Phone link gets the same look as the tabs. Hooked in by one <script src="tabgroups.js"> line on index.html.
 */
(function () {
  'use strict';
  var FAM = { ibkr: 'p', webull: 'p', world: 'p', scanner: 'p', chart: 'p', 'algo-webull': 't', 'algo-ibkr': 't', trading: 't', portfolio: 't',
    news: 'r', signals: 'r', analysis: 'r', macro: 'r', night: 'r', report: 'r', claude: 'r', recap: 'r', health: 's' };
  var st = document.createElement('style');
  st.textContent =
    '.tab[data-fam="p"]{box-shadow:inset 0 -2px 0 rgba(90,169,255,.55);background-color:rgba(90,169,255,.05);}' +
    '.tab[data-fam="t"]{box-shadow:inset 0 -2px 0 rgba(61,220,132,.55);background-color:rgba(61,220,132,.05);}' +
    '.tab[data-fam="r"]{box-shadow:inset 0 -2px 0 rgba(185,140,255,.55);background-color:rgba(185,140,255,.05);}' +
    '.tab[data-fam="s"]{box-shadow:inset 0 -2px 0 rgba(150,165,185,.55);background-color:rgba(150,165,185,.05);}' +
    '.tab[data-fam].active{background-color:var(--bg);}' +
    '#bar a.xlink[href="phone.html"]{font-size:13px;color:var(--dim);opacity:1;}' +
    '#bar a.xlink[href="phone.html"]:hover{color:var(--white);}';
  document.head.appendChild(st);
  [].forEach.call(document.querySelectorAll('.tab[data-tab]'), function (b) {
    var f = FAM[b.dataset.tab];
    if (f) { b.dataset.fam = f; b.title = (b.title ? b.title + ' · ' : '') + { p: 'Prices', t: 'Trading', r: 'Research', s: 'System' }[f] + ' tab'; }
  });
})();
