/* Alex Hale's Board -- Report Card "trust" tags on the mood meters (jb-reportcard).
 * Next to the Futures mood, Picks mood and "Mood since the 4 pm close" meters: a small tag like
 * "right 61% (140)": how often that meter, read over the window you picked, pointed the right way
 * by the next close (the open for the 4 pm mood). Live grades once there are 10, the 2-year replay
 * before that. Tap it to open the Report Card. Hooked in by one <script> line on index.html and phone.html.
 */
(function () {
  'use strict';
  if (window.__jbRcb) return;
  window.__jbRcb = true;
  var B = null, phone = !document.getElementById('panes');
  var css = document.createElement('style');
  css.textContent = '.rcb{display:inline-block !important;min-height:0 !important;min-width:0 !important;line-height:1.5 !important;flex:none !important;align-self:center !important;height:auto !important;margin-left:8px;padding:1px 7px !important;border-radius:9px;border:1px solid #164060;font-size:10px;font-weight:600;' +
    'color:#8fd8ff;text-decoration:none;white-space:nowrap;vertical-align:middle;letter-spacing:0;text-transform:none}' +
    '.rcb.g{color:#00ff41;border-color:#1f5a3a}.rcb.m{color:#ffb648;border-color:#5a4520}.rcb.b{color:#ff3b30;border-color:#5a2020}';
  document.head.appendChild(css);
  function tag(x) {
    var a = document.createElement('a');
    a.className = 'rcb';
    a.href = phone ? 'report.html' : './#report';
    if (!phone) a.onclick = function (e) { e.preventDefault(); var t = document.querySelector('#bar .tab[data-tab="report"]'); if (t) t.click(); };
    return a;
  }
  function fill(a, x) {
    if (!x) { a.textContent = 'grading…'; a.className = 'rcb'; a.title = 'The Report Card has no grades for this yet.'; return; }
    a.textContent = 'right ' + x.pct + '% (' + x.n + ')';
    a.className = 'rcb ' + (x.n < 20 ? '' : x.pct >= 55 ? 'g' : x.pct >= 45 ? 'm' : 'b');
    a.title = 'Report Card: right ' + x.pct + '% of ' + x.n + ' readings by the ' + String(x.h || '').toLowerCase() +
      (x.src === 'replay' ? ' (2-year replay; live grades take over after 10)' : ' (live)') + '. 50% = a coin flip.';
  }
  function apply() {
    if (!B) return;
    [['futures', 'fut_mood'], ['picks', 'picks_mood']].forEach(function (m) {
      var box = document.getElementById('jbm-' + m[0]); if (!box) return;
      var t = box.querySelector('.jbm-t'); if (!t) return;
      var on = box.querySelector('.jbm-w button.on'), w = on ? on.dataset.w : 'close';
      var a = t.querySelector('.rcb'); if (!a) { a = tag(); t.appendChild(a); }
      fill(a, (B[m[1]] || {})[w]);
    });
    var mood = document.getElementById('mood');
    var h = mood && mood.parentNode && mood.parentNode.querySelector('h2');
    if (h) { var a = h.querySelector('.rcb'); if (!a) { a = tag(); var ln = h.querySelector('a'); h.insertBefore(a, ln); } fill(a, (B.mood || {})['']); }
  }
  function load() {
    fetch('/api/plus/reportcard/badges', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d && !d.error) { B = d; apply(); } }).catch(function () {});
  }
  load();
  setInterval(load, 10 * 60000);
  setInterval(apply, 1500);
})();
