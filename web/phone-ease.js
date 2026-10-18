/* Alex Hale's Board -- easier on the phone (jb-ease, deep search ideas 14, 15, 16, 19, 20). Adds, never removes:
 *   14 jump chips under the search box (Moves, Ticket, My trading, Alerts, Coming up): one tap to get there
 *   15 taller buttons on phones (the mood meter session chips, the ticket's $ chips)
 *   16 small print raised to 12 px on phones (Phone page, Report Card, Night desk)
 *   19 tap a card's title to fold it to one line (the phone remembers; everything starts open)
 *   20 a shorter search hint that fits the phone
 * Hooked in by one <script src="phone-ease.js"> line on phone.html, report.html and night.html.
 */
(function () {
  'use strict';
  var phone = function () { return window.innerWidth <= 700; };
  var isPhonePage = !!document.getElementById('phSearch') || /phone\.html/.test(location.pathname);
  var st = document.createElement('style');
  st.textContent = '@media (max-width:700px){' +
    '.card.jbm .jbm-w button{min-height:32px!important;padding:6px 10px!important;font-size:12px!important;border-radius:16px!important;}' +
    '.jbm a.rcb{min-height:30px;display:inline-flex;align-items:center;} button.tk-sm{min-height:36px;}' +
    '}' +
    '#jbJump{display:flex;gap:6px;overflow-x:auto;padding:2px 2px 8px;margin:0 0 2px;-webkit-overflow-scrolling:touch;scrollbar-width:none;}' +
    '#jbJump::-webkit-scrollbar{display:none;} #jbJump button{flex:0 0 auto;font:inherit;font-size:12px;min-height:34px;padding:0 12px;border-radius:17px;' +
    'border:1px solid var(--line-bright,#2a3d52);background:rgba(127,214,255,.06);color:var(--white,#f2f2f2);cursor:pointer;}' +
    '.card > h2.jb-ft{position:relative;} .card > h2 .jb-fv{position:absolute;left:-9px;top:50%;margin-top:-7px;line-height:14px;width:10px;opacity:.55;font-size:10px;transition:transform .15s;} .card.jb-fold > h2 .jb-fv{transform:rotate(-90deg);}' +
    '.card.jb-fold > :not(h2){display:none !important;} .card.jb-fold{padding-bottom:6px;} .card > h2.jb-ft{cursor:pointer;-webkit-user-select:none;user-select:none;}';
  document.head.appendChild(st);

  function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem(k) || 'null'); localStorage.setItem(k, JSON.stringify(v)); } catch (e) { return null; } }
  function title(h) { var t = ''; [].forEach.call(h.childNodes, function (n) { if (n.nodeType === 3) t += n.nodeValue; }); return t.trim(); }
  function cardBy(prefix) {
    var cs = document.querySelectorAll('.card > h2');
    for (var i = 0; i < cs.length; i++) if (title(cs[i]).toLowerCase().indexOf(prefix) === 0) return cs[i].parentNode;
    return null;
  }

  // 19 fold cards (Phone page only)
  var folded = store('jbFold') || [];
  function setupFolds() {
    if (!isPhonePage) return;
    [].forEach.call(document.querySelectorAll('.card > h2'), function (h) {
      var t = title(h); if (!t) return;
      if (!h.querySelector('.jb-fv')) { var s = document.createElement('span'); s.className = 'jb-fv'; s.textContent = '▾'; h.insertBefore(s, h.firstChild); h.classList.add('jb-ft'); h.title = 'Tap to fold or unfold this card'; }
      h.parentNode.classList.toggle('jb-fold', folded.indexOf(t) >= 0);
    });
  }
  document.addEventListener('click', function (e) {
    var h = e.target.closest && e.target.closest('.card > h2.jb-ft');
    if (!h || e.target.closest('a,button,input,select,label')) return;
    var t = title(h), i = folded.indexOf(t);
    if (i >= 0) folded.splice(i, 1); else folded.push(t);
    store('jbFold', folded); setupFolds();
  });

  // 14 jump chips
  var JUMPS = [['Moves', 'biggest moves'], ['Ticket', 'order ticket'], ['My trading', 'my trading'], ['Alerts', 'latest alerts'], ['Coming up', 'coming up']];
  function setupJump() {
    var s = document.getElementById('phSearch');
    if (!s || document.getElementById('jbJump')) return;
    var row = document.createElement('div'); row.id = 'jbJump';
    row.innerHTML = JUMPS.map(function (j) { return '<button type="button" data-j="' + j[1] + '">' + j[0] + '</button>'; }).join('');
    s.parentNode.insertBefore(row, s.nextSibling);
    row.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      var c = cardBy(b.dataset.j); if (!c) return;
      if (c.classList.contains('jb-fold')) { folded.splice(folded.indexOf(title(c.querySelector('h2'))), 1); store('jbFold', folded); setupFolds(); }
      window.scrollTo({ top: c.getBoundingClientRect().top + window.pageYOffset - 8, behavior: 'smooth' });
    });
  }

  // 20 shorter search hint
  function hint() { var i = document.getElementById('cbPh'); if (i && phone() && i.placeholder.length > 30) i.placeholder = 'Symbol or tab: TQQQ, world'; }

  // 16 small print to 12 px on phones (tags and badges stay small so rows keep their shape)
  var SKIP = /tag|badge|chip|sw-|md-|jb-fv|dot/;
  function bump() {
    if (!phone()) return;
    var all = document.body.getElementsByTagName('*');
    for (var i = 0; i < all.length; i++) {
      var e = all[i];
      if (e.dataset.jbf || e.closest('svg,canvas,#jbNav,#jbNavSheet,table')) continue;
      var direct = false;
      for (var n = e.firstChild; n; n = n.nextSibling) if (n.nodeType === 3 && n.nodeValue.trim()) { direct = true; break; }
      if (!direct || SKIP.test(e.className && e.className.baseVal === undefined ? e.className : '')) continue;
      var f = parseFloat(getComputedStyle(e).fontSize);
      if (f >= 8.5 && f < 12) { e.style.fontSize = '12px'; }
      e.dataset.jbf = '1';
    }
  }
  function all() { setupJump(); setupFolds(); hint(); bump(); }
  all(); setTimeout(all, 1500); setTimeout(all, 5000); setInterval(all, 8000);
})();
