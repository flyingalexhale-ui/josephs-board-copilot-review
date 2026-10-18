/* Why it's moving, from the board's own math (jb-why, deep search batch C). One file, used on several pages:
 *   ibkr.html / webull.html  a row on each tile: trend arrows on 5-minute, hourly, daily and weekly bars (59), a
 *                            three-colour bar splitting the move into market / sector / its own (41), the volume
 *                            label (42) and the last level it crossed (43). Tap the bar for the full explanation.
 *                            The WHY? panel gets a "Board math" section at the top.
 *   phone.html               a why line under each row of Biggest moves and Your holdings (53).
 *   chart.html               support / resistance lines and simple patterns (58), the levels it just crossed and
 *                            the why line under the chart, each with an on/off switch.
 * Data: /api/plus/why (gateway/why.py). Board math only: no outside analysis. */
(function () {
  'use strict';
  if (window.__jbWhy) return;
  window.__jbWhy = true;
  var page = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function getJ(u) { return fetch(u, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }); }
  function ls(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  var API = '/api/plus/why';
  var C = { market: '#7fd6ff', sector: '#d6a8ff', own: '#ffc400', up: '#5dff8f', down: '#ff8a80', flat: '#7f93a8' };
  var ARW = { up: '▲', down: '▼', flat: '▬' };
  function pc(v) { return v == null ? '--' : (v > 0 ? '+' : '') + v.toFixed(2) + '%'; }

  var css = '.plus4{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:2px 0;font-size:10.5px}.plus4:empty{display:none}' +
    '.wy-tf{display:inline-flex;gap:1px;font-size:10px;line-height:1;cursor:help}.wy-tf i{font-style:normal}' +
    '.wy-bar{display:inline-flex;width:54px;height:8px;border-radius:2px;overflow:hidden;background:#14202e;cursor:pointer;border:1px solid #2a3d52;vertical-align:middle}.wy-bar b{display:block;height:100%}' +
    '.wy-box{font-size:13px;line-height:1.5}.wy-box h4{margin:10px 0 4px;font-size:11px;letter-spacing:.08em;color:#9fb3c8;text-transform:uppercase}.wy-box .dim{color:#7f93a8;font-size:12px}' +
    '.wy-key i{display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 4px 0 8px;vertical-align:-1px}' +
    '#wyPop{position:fixed;left:50%;top:10%;transform:translateX(-50%);z-index:9999;width:min(480px,94vw);max-height:80vh;overflow:auto;background:#071a30;border:1px solid #164060;border-radius:10px;padding:14px;color:#f2f2f2;box-shadow:0 10px 40px rgba(0,0,0,.6);display:none}' +
    '#wyPop .x{float:right;cursor:pointer;color:#9fb3c8;font-size:20px;padding:0 4px}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  function tfHtml(tf) {
    if (!tf || !tf.arrows) return '';
    var a = tf.arrows, names = { '5m': '5-minute', '1h': 'hourly', 'D': 'daily', 'W': 'weekly' };
    var title = ['5m', '1h', 'D', 'W'].map(function (k) { return names[k] + ' ' + (a[k] || 'flat'); }).join(', ') + (tf.agree ? '. All four agree: a strong trend.' : '. Mixed: be careful.');
    return '<span class="wy-tf" title="Trend on ' + esc(title) + '">' + ['5m', '1h', 'D', 'W'].map(function (k) { return '<i style="color:' + C[a[k] || 'flat'] + '">' + ARW[a[k] || 'flat'] + '</i>'; }).join('') + '</span>';
  }
  function barHtml(bar, mv, sym) {
    if (!bar) return '';
    var tot = Math.abs(bar.market || 0) + Math.abs(bar.sector || 0) + Math.abs(bar.own || 0);
    if (!tot) return '';
    return '<span class="wy-bar" data-why="' + esc(sym) + '" title="Market ' + pc(bar.market) + ', sector ' + pc(bar.sector) + ', its own ' + pc(bar.own) + '. Tap for why.">' +
      ['market', 'sector', 'own'].map(function (k) { var w = Math.abs(bar[k] || 0) / tot * 100; return w >= 2 ? '<b style="width:' + w + '%;background:' + C[k] + ';opacity:' + ((bar[k] || 0) * (mv || 0) >= 0 ? 1 : 0.45) + '"></b>' : ''; }).join('') + '</span>';
  }
  function explainHtml(o) {
    if (!o) return '<p class="dim">Could not ask the Mac.</p>';
    if (o.note) return '<p class="dim">' + esc(o.note) + '</p>';
    var h = '<div class="wy-box">';
    h += '<div><b>' + esc(o.sym) + ' ' + pc(o.move) + '</b> since the 4 pm close · ' + esc(o.session) + (o.move15 != null ? ' · last 15 min ' + pc(o.move15) : '') + '</div>';
    if (o.confidence) h += '<div style="margin:4px 0"><b style="color:' + ({ strong: C.up, weak: C.down, moderate: '#ffc400', quiet: C.flat })[o.confidence.level] + '">' + esc(o.confidence.text) + '</b></div>';
    if (o.line) h += '<div>' + esc(o.line.charAt(0).toUpperCase() + o.line.slice(1)) + '.</div>';
    var bd = o.breakdown;
    if (bd) {
      h += '<h4>Market, sector or its own</h4><div>' + barHtml(bd, o.move, o.sym).replace('wy-bar', 'wy-bar" style="width:100%;height:12px') + '</div>';
      h += '<div class="wy-key"><i style="background:' + C.market + '"></i>market ' + pc(bd.market) + ' (' + esc(bd.mkt_name || 'futures') + ' ' + pc(bd.mkt_move) + (bd.beta != null ? ' x ' + bd.beta + ', its usual sensitivity over 20 days' : '') + ')' +
        '<br><i style="background:' + C.sector + '"></i>' + (bd.lev ? esc(bd.sector_name || 'index') + ' ' + pc(bd.sector) : 'sector' + (bd.sector_name ? ' (' + esc(bd.sector_name) + ')' : '') + ' ' + pc(bd.sector)) +
        '<br><i style="background:' + C.own + '"></i>its own ' + pc(bd.own) + '</div>';
      if (bd.lev && bd.base_move != null) h += '<div class="dim">' + esc(o.sym) + ' is a ' + (bd.lev > 0 ? '+' : '') + bd.lev + 'x fund on ' + esc(bd.base) + ' (' + pc(bd.base_move) + ')' +
        (bd.check != null ? ': it is moving ' + bd.check + 'x its index' + (Math.abs(bd.check - bd.lev) >= Math.max(0.6, Math.abs(bd.lev) * 0.25) ? ', not the usual ' + bd.lev + 'x (thin trading or a premium/discount).' : ', as expected.') : '.') + '</div>';
    }
    var v = o.volume;
    h += '<h4>Volume behind it</h4>' + (v ? '<div>Last 15 minutes: ' + (v.now || 0).toLocaleString() + (v.normal ? ' vs about ' + v.normal.toLocaleString() + ' normally at this time (' + v.x + 'x, <b>' + esc(v.label) + '</b>)' : ' (no past days to compare yet)') + '.' +
      (v.fading ? ' <b style="color:#ffc400">Price still moving while volume fades.</b>' : '') + (v.flow ? ' Buyers ' + v.flow.pct + '% / sellers ' + (100 - v.flow.pct) + '% (IBKR, last 15 min).' : '') + '</div>' : '<div class="dim">No volume recorded for this symbol.</div>');
    h += '<h4>What it just did</h4>' + ((o.events || []).length ? '<div>' + o.events.slice(0, 6).map(function (e) { return '<div>' + esc(e.text) + '</div>'; }).join('') + '</div>' : '<div class="dim">No key level crossed in the last day.</div>');
    var sx = o.stretch || {};
    h += '<h4>Stretch and speed</h4><div>' + (sx.label ? esc(sx.label.charAt(0).toUpperCase() + sx.label.slice(1)) + '. ' : '') +
      (sx.speed != null ? '15-minute move ' + sx.speed + 'x normal for this session. ' : '') + (sx.dist != null ? 'Distance from the ' + esc(sx.from) + ': ' + sx.dist + ' normal daily ranges. ' : '') + (sx.rsi != null ? 'RSI (5-min) ' + sx.rsi + '.' : '') + '</div>';
    if (o.trend) h += '<h4>Trend or range</h4><div>' + esc(o.trend.text.charAt(0).toUpperCase() + o.trend.text.slice(1)) + '. ' + tfHtml(o.tf) + ' <span class="dim">(5-min, hourly, daily, weekly)</span></div>';
    if (o.leader) h += '<h4>Who moved first</h4><div>' + esc(o.leader.text) + '</div>';
    if (o.drivers && o.drivers.text) h += '<h4>Which holdings are driving it</h4><div>' + esc(o.drivers.text) + '</div><div class="dim">' +
      o.drivers.rows.slice(0, 6).map(function (r) { return esc(r.sym) + ' ' + pc(r.move) + ' × ' + r.w + '%'; }).join(' · ') + '</div>';
    if (o.similar) h += '<h4>Similar setups on this board</h4><div>' + esc(o.similar.text) + '</div>';
    h += '<p class="dim">Board math only: the board’s own prices and volume, nothing from outside. A reading of the move, not advice.</p></div>';
    return h;
  }
  window.JBWhyExplainHtml = explainHtml;
  var pop = null;
  function showPop(sym) {
    if (!pop) { pop = document.createElement('div'); pop.id = 'wyPop'; document.body.appendChild(pop); document.addEventListener('keydown', function (e) { if (e.key === 'Escape') pop.style.display = 'none'; }); }
    pop.innerHTML = '<span class="x">×</span><b>Why is ' + esc(sym) + ' moving?</b><p class="dim">Working it out…</p>'; pop.style.display = 'block';
    pop.querySelector('.x').onclick = function () { pop.style.display = 'none'; };
    getJ(API + '/explain?sym=' + encodeURIComponent(sym)).then(function (o) {
      pop.innerHTML = '<span class="x">×</span><b>Why is ' + esc(sym) + ' moving? (board math)</b>' + explainHtml(o);
      pop.querySelector('.x').onclick = function () { pop.style.display = 'none'; };
    });
  }
  window.JBWhyPop = showPop;

  // ---------------------------------------------------------------- price boards: tile row + WHY panel section
  function boards() {
    var T = {};
    function chips(sym) {
      var t = T[sym]; if (!t) return '';
      var h = tfHtml(t.tf) + ' ' + barHtml(t.bar, t.move, sym);
      var v = t.vol || {};
      if (v.label === 'heavy') h += '<span class="pl-chip pl-g" title="Volume in the last 15 minutes vs the same time on past days">VOL ' + v.x + 'x</span>';
      else if (v.label === 'thin') h += '<span class="pl-chip pl-d" title="Thin volume: moves on thin volume often reverse">THIN</span>';
      if (t.level) h += '<span class="pl-chip ' + (t.level.up ? 'pl-g' : 'pl-r') + '" title="' + esc(t.level.text) + '">' + esc((t.level.up ? '↑ ' : '↓ ') + t.level.name.replace("yesterday's", 'YDAY').replace('overnight', 'ON').replace('opening-range', 'OR').replace(' average', 'D').toUpperCase()) + '</span>';
      if (t.stretch && /^(stretched|running hot)/.test(t.stretch)) h += '<span class="pl-chip pl-a" title="' + esc(t.stretch) + '">' + (/^running/.test(t.stretch) ? 'HOT' : 'STRETCHED') + '</span>';
      return h;
    }
    function paint() {
      [].forEach.call(document.querySelectorAll('.tile[data-sym], .row[data-sym]'), function (el) {
        var sym = el.dataset.sym, html = chips(sym);
        var box = el.querySelector('.plus4');
        if (!box) {
          if (!html) return;
          box = document.createElement('div'); box.className = 'plus4';
          var after = el.querySelector('.plus3') || el.querySelector('.plus2') || el.querySelector('.plus'), foot = el.classList.contains('tile') ? el.querySelector(':scope > .foot') : null, det = el.querySelector('.detail');
          if (after) after.parentNode.insertBefore(box, after.nextSibling); else if (foot) el.insertBefore(box, foot); else if (det) det.appendChild(box); else return;
          box.addEventListener('click', function (ev) { var b = ev.target.closest('[data-why]'); if (b) { ev.stopPropagation(); showPop(b.dataset.why); } });
        }
        if (box._h !== html) { box._h = html; box.innerHTML = html; }
      });
    }
    function load() { if (!document.hidden) getJ(API + '/tiles').then(function (d) { if (d && d.tiles) T = d.tiles; }).catch(function () {}); }
    load(); setInterval(load, 30000); setInterval(paint, 3000);
    window.JBWhyBox = function (sym, box) {
      var sec = document.createElement('div'); sec.innerHTML = '<h3>Board math</h3><p class="dim">Working it out…</p>';
      var ans = box.querySelector('.ans'); if (ans) ans.parentNode.insertBefore(sec, ans.nextSibling); else box.appendChild(sec);
      getJ(API + '/explain?sym=' + encodeURIComponent(sym)).then(function (o) { sec.innerHTML = '<h3>Board math (its own prices and volume)</h3>' + explainHtml(o); });
    };
  }

  // ---------------------------------------------------------------- phone page: why line under each move
  function phone() {
    var L = {};
    function fix() {
      ['mov', 'held'].forEach(function (id) {
        var box = $(id); if (!box) return;
        [].forEach.call(box.querySelectorAll('tr'), function (tr) {
          if (tr.classList.contains('wy-l') || (tr.nextSibling && tr.nextSibling.classList && tr.nextSibling.classList.contains('wy-l'))) return;
          var td = tr.querySelector('td'); if (!td) return;
          var sym = (td.childNodes[0] && td.childNodes[0].textContent || '').trim(); if (!L[sym]) return;
          var r = document.createElement('tr'); r.className = 'wy-l';
          r.innerHTML = '<td colspan="4" style="padding:0 0 6px;font-size:12px;color:#9fb3c8;white-space:normal;border-top:0;cursor:pointer" data-why="' + esc(sym) + '">↳ ' + esc(L[sym]) + '</td>';
          tr.parentNode.insertBefore(r, tr.nextSibling);
        });
      });
    }
    document.addEventListener('click', function (e) { var b = e.target.closest && e.target.closest('.wy-l [data-why]'); if (b) showPop(b.dataset.why); });
    ['mov', 'held'].forEach(function (id) { var b = $(id); if (b) new MutationObserver(fix).observe(b, { childList: true, subtree: true }); });
    function load() { if (!document.hidden) getJ(API + '/tiles').then(function (d) { if (!d || !d.tiles) return; var m = {}; Object.keys(d.tiles).forEach(function (s) { if (d.tiles[s].line && Math.abs(d.tiles[s].move || 0) >= 0.3) m[s] = d.tiles[s].line; }); L = m; [].forEach.call(document.querySelectorAll('.wy-l'), function (x) { x.remove(); }); fix(); }).catch(function () {}); }
    load(); setInterval(load, 60000);
  }

  // ---------------------------------------------------------------- Chart page: S/R lines, patterns, what it did (43, 58)
  function chart() {
    var on = { sr: ls('jb_why_sr') !== '0', pat: ls('jb_why_pat') !== '0', did: ls('jb_why_did') !== '0' };
    var flw = $('flw'); if (!flw) return;
    var row = document.createElement('div'); row.className = 'flw'; row.id = 'wyRow';
    flw.parentNode.insertBefore(row, flw.nextSibling);
    function paintRow() {
      row.innerHTML = '<span class="lbl">Board math:</span>' + [['sr', 'S/R lines'], ['pat', 'Patterns'], ['did', 'Why it moved']].map(function (b) {
        return '<button type="button" data-k="' + b[0] + '"' + (on[b[0]] ? ' class="on"' : '') + '>' + b[1] + '</button>';
      }).join('');
    }
    row.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      on[b.dataset.k] = !on[b.dataset.k]; ls('jb_why_' + b.dataset.k, on[b.dataset.k] ? '1' : '0'); paintRow(); info(); if (window.JBChartRedraw) window.JBChartRedraw();
    });
    paintRow();
    var box = document.createElement('div'); box.id = 'wyInfo'; box.style.cssText = 'margin:6px 0;font-size:12.5px;line-height:1.5;color:#cfe0ee';
    var src = $('src'); if (src) src.parentNode.insertBefore(box, src); else row.parentNode.appendChild(box);
    var cur = '', last = null;
    function info() {
      if (!on.did || !cur) { box.innerHTML = ''; return; }
      if (!last || last.sym !== cur) { box.innerHTML = ''; return; }
      var o = last, h = '';
      if (o.note) { box.innerHTML = '<span style="color:#7f93a8">' + esc(o.note) + '</span>'; return; }
      h += tfHtml(o.tf) + ' ' + barHtml(o.breakdown, o.move, o.sym) + ' <b>' + esc(o.sym) + ' ' + pc(o.move) + '</b> since 4 pm' + (o.line ? ': ' + esc(o.line) : '') + '.';
      if (o.confidence) h += ' <span style="color:#9fb3c8">' + esc(o.confidence.text) + '</span>';
      if ((o.events || []).length) h += '<div style="color:#9fb3c8">' + o.events.slice(0, 4).map(function (e) { return esc(e.text); }).join(' · ') + '</div>';
      box.innerHTML = h;
      var b = box.querySelector('[data-why]'); if (b) b.onclick = function () { showPop(o.sym); };
    }
    function want(sym) {
      if (sym === cur && last && Date.now() - last._at < 60000) return;
      cur = sym;
      getJ(API + '/explain?sym=' + encodeURIComponent(sym)).then(function (o) { if (o && o.sym === cur) { o._at = Date.now(); last = o; info(); } }).catch(function () {});
    }
    // swing points over the visible range
    function pivots(p, k) {
      var hi = [], lo = [];
      for (var i = k; i < p.length - k; i++) {
        var isH = true, isL = true;
        for (var j = i - k; j <= i + k; j++) { if (p[j].p > p[i].p) isH = false; if (p[j].p < p[i].p) isL = false; }
        if (isH) hi.push(p[i]); if (isL) lo.push(p[i]);
      }
      return { hi: hi, lo: lo };
    }
    function levels(pv, tol) {
      var all = pv.hi.concat(pv.lo).map(function (q) { return q.p; }).sort(function (a, b) { return a - b; }), out = [];
      all.forEach(function (x) {
        var c = out.filter(function (o) { return Math.abs(o.p - x) <= tol; })[0];
        if (c) { c.p = (c.p * c.n + x) / (c.n + 1); c.n++; } else out.push({ p: x, n: 1 });
      });
      return out.filter(function (o) { return o.n >= 2; });
    }
    function patterns(p, pv, tol) {
      var out = [], n = p.length, last = p[n - 1].p;
      var H = pv.hi.slice(-3), Lo = pv.lo.slice(-3);
      if (H.length >= 2) {
        var a = H[H.length - 2], b = H[H.length - 1];
        var between = p.filter(function (q) { return q.t > a.t && q.t < b.t; });
        var trough = between.length ? Math.min.apply(null, between.map(function (q) { return q.p; })) : null;
        if (Math.abs(a.p - b.p) <= tol && trough != null && Math.max(a.p, b.p) - trough >= 3 * tol && last < b.p - tol) out.push({ name: 'possible double top', t: b.t, p: b.p, up: false });
      }
      if (Lo.length >= 2) {
        var c = Lo[Lo.length - 2], d = Lo[Lo.length - 1];
        var bt = p.filter(function (q) { return q.t > c.t && q.t < d.t; });
        var peak = bt.length ? Math.max.apply(null, bt.map(function (q) { return q.p; })) : null;
        if (Math.abs(c.p - d.p) <= tol && peak != null && peak - Math.min(c.p, d.p) >= 3 * tol && last > d.p + tol) out.push({ name: 'possible double bottom', t: d.t, p: d.p, up: true });
      }
      // flag: a strong move (the pole) then a tight, slightly opposite drift
      var cut = Math.floor(n * 0.7), pole0 = p[Math.floor(n * 0.45)].p, pole1 = p[cut].p, rest = p.slice(cut);
      var rh = Math.max.apply(null, rest.map(function (q) { return q.p; })), rl = Math.min.apply(null, rest.map(function (q) { return q.p; }));
      var poleMove = pole1 - pole0;
      if (Math.abs(poleMove) >= 6 * tol && rh - rl <= Math.abs(poleMove) * 0.4 && (last - pole1) * poleMove <= 0) out.push({ name: poleMove > 0 ? 'bull flag' : 'bear flag', t: p[cut].t, p: pole1, up: poleMove > 0 });
      // wedge: highs and lows both rising (or both falling) and closing in
      if (pv.hi.length >= 3 && pv.lo.length >= 3) {
        var h3 = pv.hi.slice(-3), l3 = pv.lo.slice(-3);
        var hs = (h3[2].p - h3[0].p) / Math.max(1, h3[2].t - h3[0].t), lsl = (l3[2].p - l3[0].p) / Math.max(1, l3[2].t - l3[0].t);
        var upW = h3[0].p < h3[1].p && h3[1].p < h3[2].p && l3[0].p < l3[1].p && l3[1].p < l3[2].p && lsl > hs * 1.2;
        var dnW = h3[0].p > h3[1].p && h3[1].p > h3[2].p && l3[0].p > l3[1].p && l3[1].p > l3[2].p && hs < lsl * 1.2;
        if (upW) out.push({ name: 'rising wedge', t: h3[2].t, p: h3[2].p, up: false });
        if (dnW) out.push({ name: 'falling wedge', t: l3[2].t, p: l3[2].p, up: true });
      }
      return out;
    }
    window.JBChartLayers = window.JBChartLayers || [];
    window.JBChartLayers.push(function (ctx, g) {
      want(g.sym);
      var p = (g.pts || []).filter(function (q) { return q.t >= g.t0 && q.t <= g.t1; });
      if (p.length < 30 || (!on.sr && !on.pat)) return;
      var k = Math.max(3, Math.round(p.length / 40)), pv = pivots(p, k), lastp = p[p.length - 1].p;
      var tol = Math.max((g.hi - g.lo) * 0.015, lastp * 0.0015);
      ctx.save(); ctx.font = '10px "IBM Plex Mono", ui-monospace, Menlo, monospace'; ctx.textBaseline = 'bottom';
      if (on.sr) {
        var lv = levels(pv, tol), sup = lv.filter(function (o) { return o.p < lastp; }).sort(function (a, b) { return b.n - a.n || b.p - a.p; }).slice(0, 3),
          res = lv.filter(function (o) { return o.p >= lastp; }).sort(function (a, b) { return b.n - a.n || a.p - b.p; }).slice(0, 3);
        sup.concat(res).forEach(function (o) {
          var y = Math.round(g.Y(o.p)) + 0.5, isR = o.p >= lastp;
          ctx.strokeStyle = isR ? 'rgba(255,138,128,.7)' : 'rgba(93,255,143,.6)'; ctx.setLineDash([2, 5]); ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(g.x0, y); ctx.lineTo(g.x1, y); ctx.stroke(); ctx.setLineDash([]);
          ctx.fillStyle = isR ? '#ff8a80' : '#5dff8f'; ctx.textAlign = 'left';
          ctx.fillText((isR ? 'R ' : 'S ') + (o.p >= 1 ? o.p.toFixed(2) : o.p.toFixed(4)) + ' · ' + o.n + ' touches', g.x0 + 4, y - 2);
        });
      }
      if (on.pat) {
        var pats = patterns(p, pv, tol), py = g.y0 + 4;
        pats.forEach(function (o) {
          var x = g.X(o.t), y = g.Y(o.p);
          ctx.strokeStyle = o.up ? '#5dff8f' : '#ff8a80'; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.stroke();
          var t = (o.up ? '↗ ' : '↘ ') + o.name;
          ctx.textAlign = 'right'; ctx.textBaseline = 'top'; var w = ctx.measureText(t).width + 10;
          ctx.fillStyle = 'rgba(4,16,31,.85)'; ctx.fillRect(g.x1 - w - 4, py, w + 4, 15);
          ctx.fillStyle = o.up ? '#5dff8f' : '#ff8a80'; ctx.fillText(t, g.x1 - 4, py + 2); py += 17;
        });
      }
      ctx.restore();
    });
  }

  if (page === 'ibkr.html' || page === 'webull.html') boards();
  else if (page === 'phone.html') phone();
  else if (page === 'chart.html') chart();
})();
