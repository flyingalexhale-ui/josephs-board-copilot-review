/* Alex Hale's Board -- final-ideas chips on the price boards (jb-edge, jb-feeds, jb-tools).
 *
 * A second small chip row on each tile (only when there is something to show):
 *   ● trend light   3x / 2x funds: green / grey / red from the matching futures' trend (idea 64)
 *   FAIR +0.8       3x / 2x funds: trading this many points above (+) or below (-) what the futures
 *                   or index say it should (idea 7); amber when 0.5 points or more
 *   BUYERS 68%      share of the last 15 min's IBKR trades that hit the ask (idea 20)
 *   PRICES DISAGREE IBKR and Webull differ by 0.75%+ (idea 24); the algos skip new entries
 *   REDDIT #12      ApeWisdom rank, purple when mentions doubled in 24 h (idea 15)
 *   NOTE            you saved a note on this symbol (idea 71); tap to read it
 *   ⓘ               the fund card (idea 48)
 * On the QQQ and SPY charts: the options magnet / fast / flip levels as dotted lines (idea 65).
 * Command bar / keys (ideas 28, 31, 32): opens the chart of any symbol (Yahoo prices for symbols this
 * board doesn't stream, e.g. NQ=F or ^N225), with the buy / sell ticket or the Flip button when asked.
 * Data: /api/plus/edge/tiles, /api/plus/feeds/tiles, /api/plus/tools/notes. Hooked in by one
 * <script src="plus2.js"> line after plus.js on ibkr.html and webull.html.
 */
(function () {
  'use strict';
  if (window.__jbPlus2) return;
  window.__jbPlus2 = true;
  var E = {}, F = {}, N = {}, G = {};
  var st = document.createElement('style');
  st.textContent = '.plus2{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:2px 0;font-size:10.5px;}.plus2:empty{display:none;}' +
    '.plus2 .pl-chip{cursor:default}.plus2 .tap{cursor:pointer}.p2-dot{font-size:12px;line-height:1}' +
    '.pl-p{background:#22163f;color:#d6a8ff;border-color:#4b3a8a;}' +
    '#p2Pop{position:fixed;left:50%;top:12%;transform:translateX(-50%);z-index:9999;width:min(460px,92vw);max-height:76vh;overflow:auto;background:#071a30;border:1px solid #164060;border-radius:10px;padding:14px;color:#f2f2f2;font-size:13px;box-shadow:0 10px 40px rgba(0,0,0,.6);display:none}' +
    '#p2Pop .x{float:right;cursor:pointer;color:#9fb3c8;font-size:18px}#p2Pop small{color:#7f93a8}';
  document.head.appendChild(st);
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function get(p) { return fetch('/api/plus/' + p, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }); }
  function J() { return window.__jb; }
  var pop = document.createElement('div'); pop.id = 'p2Pop'; document.body.appendChild(pop);
  function show(html) { pop.innerHTML = '<span class="x">✕</span>' + html; pop.style.display = 'block'; pop.querySelector('.x').onclick = function () { pop.style.display = 'none'; }; }
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') pop.style.display = 'none'; });
  function bn(v) { if (!v) return '–'; return v >= 1e9 ? '$' + (v / 1e9).toFixed(1) + 'B' : '$' + (v / 1e6).toFixed(0) + 'M'; }
  window.__jbFundCard = function (sym) {
    show('Loading ' + esc(sym) + '…');
    get('edge/fund?sym=' + encodeURIComponent(sym)).then(function (c) {
      if (!c || c.error) { show(esc((c && c.error) || 'No fund card (is jb-edge installed?)')); return; }
      show('<b style="font-size:16px">' + esc(c.sym) + '</b><br>' + esc(c.name) + '<br><small>' + esc([c.family, c.category].filter(Boolean).join(' · ')) + '</small>' +
        '<p>Leverage <b>' + (c.leverage > 0 ? '+' : '') + c.leverage + 'x</b>' + (c.tracks ? ' of ' + esc(c.tracks) : '') + ' · fee ' + (c.expense != null ? c.expense + '%' : '–') + ' a year · size ' + bn(c.aum) +
        ' · avg volume ' + (c.avg_vol ? Math.round(c.avg_vol).toLocaleString() : '–') + '</p>' + (c.summary ? '<p><small>' + esc(c.summary) + '</small></p>' : '') +
        (c.holdings && c.holdings.length ? '<small>Top holdings' + (c.holdings_of ? ' of ' + esc(c.holdings_of) : '') + ':</small><br>' + c.holdings.map(function (h) { return esc(h.sym) + ' ' + h.w + '%'; }).join(' · ') : ''));
    });
  };
  function chips(sym) {
    var h = '', e = E[sym], f = F[sym], n = N[sym];
    if (e && e.trend) h += '<span class="p2-dot" title="Trend of ' + esc(e.trend.from) + ' over the last 1-2 hours' + (e.lev < 0 ? ' (flipped: this is a bear fund)' : '') + '. Futures trade almost 24 h, so this keeps working overnight." style="color:' +
      ({ up: '#5dff8f', down: '#ff8a80', flat: '#7f93a8' })[e.trend.light] + '">●</span>';
    if (e && e.gap != null) h += '<span class="pl-chip ' + (Math.abs(e.gap) >= 0.5 ? 'pl-a' : 'pl-d') + '" title="Since the 4 pm close it moved ' + e.actual + '%; from ' + esc(e.how) + ' it should have moved about ' + e.expected +
      '% (' + e.lev + 'x). Above 0 = trading rich, below = cheap. Thin overnight trading often lags the futures.">FAIR ' + (e.gap > 0 ? '+' : '') + e.gap.toFixed(1) + '</span>';
    if (f && f.flow && f.flow.buy + f.flow.sell >= 500) {
      var p = f.flow.pct;
      h += '<span class="pl-chip ' + (p >= 60 ? 'pl-g' : p <= 40 ? 'pl-r' : 'pl-d') + '" title="Last 15 minutes on IBKR: ' + f.flow.buy.toLocaleString() + ' shares traded at the ask (buyers) vs ' + f.flow.sell.toLocaleString() + ' at the bid (sellers)' +
        (f.flow.book != null ? '. Shares waiting: ' + f.flow.book + '% on the bid side' : '') + '.">' + (p >= 50 ? 'BUYERS ' + p : 'SELLERS ' + (100 - p)) + '%</span>';
    }
    if (f && f.disagree) h += '<span class="pl-chip pl-r" title="IBKR says ' + f.disagree.ibkr + ', Webull says ' + f.disagree.webull + ' (' + f.disagree.diff + '% apart). One feed may be stuck or wrong; the algos skip new entries until they agree.">PRICES DISAGREE</span>';
    if (f && f.reddit) {
      var r = f.reddit, jump = r.mentions >= Math.max(2 * (r.mentions_24h || 0), (r.mentions_24h || 0) + 15);
      h += '<span class="pl-chip ' + (jump ? 'pl-p' : 'pl-d') + '" title="Reddit rank by mentions (ApeWisdom): ' + r.mentions + ' mentions in 24 h (was ' + (r.mentions_24h || 0) + '), rank was ' + (r.rank_24h || '?') + '.">REDDIT #' + r.rank + '</span>';
    }
    if (n) h += '<span class="pl-chip pl-b tap" data-note="' + esc(sym) + '" title="' + esc(n.text.slice(0, 200)) + '">NOTE</span>';
    if (e) h += '<span class="pl-chip pl-d tap" data-card="' + esc(sym) + '" title="Fund card: what it tracks, fees, size, top holdings">ⓘ</span>';
    return h;
  }
  function paint() {
    var j = J(); if (!j || !j.state) return;
    [].forEach.call(document.querySelectorAll('.tile[data-sym], .row[data-sym]'), function (el) {
      var sym = el.dataset.sym; if (!sym || /=F$/.test(sym)) return;
      var box = el.querySelector('.plus2');
      if (!box) {
        box = document.createElement('div'); box.className = 'plus2';
        var p1 = el.querySelector('.plus'), foot = el.classList.contains('tile') ? el.querySelector(':scope > .foot') : null, det = el.querySelector('.detail');
        if (p1) p1.parentNode.insertBefore(box, p1.nextSibling); else if (foot) el.insertBefore(box, foot); else if (det) det.appendChild(box); else return;
        box.addEventListener('click', function (ev) {
          var c = ev.target.closest('[data-card]'), n = ev.target.closest('[data-note]');
          if (c) { ev.stopPropagation(); window.__jbFundCard(c.dataset.card); }
          if (n) { ev.stopPropagation(); var x = N[n.dataset.note]; show('<b>' + esc(n.dataset.note) + ' note</b> <small>' + esc(new Date(x.at).toLocaleString()) + '</small><p style="white-space:pre-wrap">' + esc(x.text) + '</p>'); }
        });
      }
      var html = chips(sym);
      if (box._h !== html) { box._h = html; box.innerHTML = html; }
    });
  }
  // options levels on the QQQ / SPY chart (idea 65)
  var oldRefs = window.__jbPlusRefs;
  window.__jbPlusRefs = function (refs, now, sym, ind) {
    if (oldRefs) oldRefs(refs, now, sym, ind);
    var g = G[sym]; if (!g || !g.levels) return;
    g.levels.forEach(function (l) {
      refs.push({ p: l.p, c: l.kind === 'magnet' ? '#7fd6ff' : l.kind === 'flip' ? '#ffb648' : '#ff8a80', l: (l.kind === 'magnet' ? 'options magnet ' : l.kind === 'flip' ? 'gamma flip ' : 'options fast ') + l.p, x: 560, dash: true });
    });
  };

  // ------------------------------------------------------------------ command bar, B / S keys, any-symbol chart
  var picked = null, yb = {};
  document.addEventListener('mouseover', function (e) { var t = e.target.closest && e.target.closest('.tile[data-sym], .row[data-sym]'); if (t) picked = t.dataset.sym; }, true);
  document.addEventListener('click', function (e) { var t = e.target.closest && e.target.closest('.tile[data-sym], .row[data-sym]'); if (t) picked = t.dataset.sym; }, true);
  function chartOpen() { var w = document.getElementById('jbcWrap'); return w && w.classList.contains('open') ? (document.getElementById('jbcSym') || {}).textContent : ''; }
  function known(sym) { var j = J(); return j && (j.state.symbols.indexOf(sym) >= 0 || (j.extra && j.extra.history[sym] && j.extra.history[sym].length)); }
  function fake(sym) {
    var d = document.createElement('div'); d.className = 'tile'; d.dataset.sym = sym; d.style.display = 'none';
    document.body.appendChild(d); d.click(); d.remove();
  }
  function side(how) {
    if (!how) return;
    var tries = 0;
    (function go() {
      var bar = document.getElementById('plTrade'), tr = bar && bar.querySelector('.tr');
      if (!tr) { if (++tries < 20) setTimeout(go, 250); return; }
      if (!tr.classList.contains('on')) tr.click();
      (function wait() {
        var tk = bar.querySelector('.tk');
        if (!tk) { if (++tries < 40) setTimeout(wait, 250); return; }
        if (how === 'flip') { var f = tk.querySelector('.tk-flip button'); if (f) f.click(); else if (++tries < 40) setTimeout(wait, 250); return; }
        var b = tk.querySelector('.tk-seg[data-g="side"] button[data-v="' + how + '"]'); if (b) b.click();
      })();
    })();
  }
  // a looked-up symbol is only kept while its chart is open (so it never joins the ticker tape)
  function borrow(sym, pts) {
    var j = J(); j.extra.history[sym] = pts.map(function (p) { return { t: p.t, p: p.p }; }); fake(sym);
    var iv = setInterval(function () { if (chartOpen() !== sym) { clearInterval(iv); if (j.state.symbols.indexOf(sym) < 0) delete j.extra.history[sym]; } }, 1000);
  }
  function openChart(sym, how) {
    sym = String(sym || '').toUpperCase(); if (!sym) return;
    var j = J(); if (!j) return;
    if (known(sym)) { fake(sym); side(how); return; }
    var c = yb[sym];
    if (c && Date.now() - c.at < 60000) { borrow(sym, c.d.points); side(how); return; }
    get('tools/bars?sym=' + encodeURIComponent(sym)).then(function (d) {
      if (!d || !d.points || !d.points.length) { show('<b>' + esc(sym) + '</b><p>' + esc((d && d.error) || 'No prices found (is jb-tools installed?)') + '</p>'); return; }
      yb[sym] = { at: Date.now(), d: d }; borrow(sym, d.points); side(how);
    }).catch(function () {});
  }
  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || !e.data) return;
    if (e.data.type === 'jb-open-chart') openChart(e.data.sym, e.data.side);
    if (e.data.type === 'jb-key') { var s = chartOpen() || picked; if (s) openChart(s, e.data.key === 's' ? 'sell' : 'buy'); }
  });
  function load() {
    if (document.hidden) return;
    get('feeds/tiles').then(function (d) { if (d) F = d; }).catch(function () {});
  }
  function loadSlow() {
    if (document.hidden) return;
    get('edge/tiles').then(function (d) { if (d) E = d; }).catch(function () {});
    get('edge/gamma').then(function (d) { if (d) G = d; }).catch(function () {});
    get('tools/notes').then(function (d) { if (d && d.notes) N = d.notes; }).catch(function () {});
  }
  load(); loadSlow();
  setInterval(load, 10000); setInterval(loadSlow, 60000); setInterval(paint, 2000);
})();
