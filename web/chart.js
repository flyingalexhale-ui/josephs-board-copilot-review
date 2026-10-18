/* Joseph's Board -- pop-out price chart.
 *
 * Click a tile (or tap "Chart" in an opened list row) to open a panel with a line chart of that
 * symbol's price. Ranges 1H / 4H / 1D / 1W / 1M. The chart draws from the price history the
 * board already keeps (the spare Mac's 35-day record + the broker's daily closes), so it needs
 * no extra data feed, and it keeps updating live while it is open.
 *
 * Self-contained: it brings its own styles and only reads the board's data, so the board works
 * exactly the same with or without it. Hooked in by one line after board.js in ibkr.html and
 * webull.html:     <script src="chart.js"></script>
 */
(function () {
  'use strict';
  if (window.__jbChart) return;
  window.__jbChart = true;

  var RANGES = [
    { key: '1h', label: '1H', ms: 3600e3 }, { key: '4h', label: '4H', ms: 4 * 3600e3 },
    { key: '1d', label: '1D', ms: 24 * 3600e3 }, { key: '1w', label: '1W', ms: 7 * 24 * 3600e3 },
    { key: '1mo', label: '1M', ms: 30 * 24 * 3600e3 }
  ];
  var GREEN = '#00ff41', RED = '#ff3b30', GREEN_X = '#a8ffc0', RED_X = '#ffa39c', DIM = '#6f8aa3', GRID = '#0f2c47', TEXT = '#cfe0ee';
  var LSKEY = 'jb_chart_range';
  function lsGet() { try { return localStorage.getItem(LSKEY); } catch (e) { return null; } }
  function lsSet(v) { try { localStorage.setItem(LSKEY, v); } catch (e) {} }

  var range = RANGES.filter(function (r) { return r.key === lsGet(); })[0] || RANGES[2];   // default 1D
  var sym = null, hover = null, timer = 0;
  var CMPKEY = 'jb_chart_cmp', cmp = (function () { try { return localStorage.getItem(CMPKEY) || ''; } catch (e) { return ''; } })();
  var news = { at: 0, items: [] };
  var vol = { sym: null, at: 0, rows: [] };
  function loadVolume() {
    var J = window.__jb;
    if (!sym || !J || location.protocol.indexOf('http') !== 0 || (vol.sym === sym && Date.now() - vol.at < 60e3)) return;
    vol.sym = sym; vol.at = Date.now(); var want = sym;
    fetch('/api/volume?b=' + (J.broker || 'ibkr') + '&s=' + encodeURIComponent(sym), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d && want === sym) { vol.rows = d.rows || []; draw(); } }).catch(function () {});
  }

  // ------------------------------------------------------------------ styles + panel
  var css =
    '.tile{cursor:pointer;}' +
    '.tile:hover{outline:1px solid #164060;outline-offset:-1px;}' +
    '.jbc-open{background:transparent;border:1px solid #164060;color:#00e5ff;font:inherit;font-size:10px;font-weight:700;' +
      'letter-spacing:.5px;padding:4px 10px;cursor:pointer;border-radius:3px;}' +
    '#jbcWrap{position:fixed;inset:0;z-index:900;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center;padding:12px;}' +
    '#jbcWrap.open{display:flex;}' +
    '#jbcPanel{background:#071a30;border:1px solid #164060;border-radius:10px;width:100%;max-width:860px;padding:12px 14px 10px;color:#f2f2f2;}' +
    '#jbcPanel .jbc-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;}' +
    '#jbcSym{font-size:18px;font-weight:700;color:#2f7a44;letter-spacing:.5px;}' +
    '#jbcPx{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums;}' +
    '#jbcChg{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums;}' +
    '#jbcSrc{font-size:10px;color:#6f8aa3;}' +
    '#jbcClose{margin-left:auto;background:transparent;border:1px solid #164060;color:#f2f2f2;font:inherit;font-size:16px;' +
      'border-radius:6px;width:32px;height:32px;cursor:pointer;align-self:center;}' +
    '#jbcRanges{display:flex;gap:4px;margin:8px 0 6px;}' +
    '#jbcRanges button{background:transparent;border:1px solid #164060;color:#6f8aa3;font:inherit;font-size:11px;font-weight:700;' +
      'padding:5px 12px;cursor:pointer;border-radius:3px;}' +
    '#jbcRanges button.on{background:#1e5631;border-color:#1e5631;color:#f2f2f2;}' +
    '#jbcBox{position:relative;height:340px;}' +
    '#jbcCanvas{display:block;width:100%;height:100%;touch-action:pan-y;}' +
    '#jbcEmpty{position:absolute;inset:0;display:none;align-items:center;justify-content:center;color:#6f8aa3;font-size:12px;text-align:center;padding:20px;}' +
    '#jbcNote{font-size:10px;color:#6f8aa3;margin-top:6px;}' +
    '#jbcCmp{margin-left:auto;background:#04101f;border:1px solid #164060;color:#cfe0ee;font:inherit;font-size:11px;padding:4px 6px;border-radius:3px;}' +
    '#jbcNews{font-size:11px;color:#ffc400;min-height:15px;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}' +
    '@media (max-width:560px){#jbcWrap{padding:6px;align-items:flex-start;padding-top:40px;} #jbcBox{height:260px;}' +
      ' #jbcRanges button{flex:1;padding:8px 0;}}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  var wrap = document.createElement('div'); wrap.id = 'jbcWrap';
  wrap.innerHTML = '<div id="jbcPanel" role="dialog" aria-modal="true" aria-labelledby="jbcSym">' +
    '<div class="jbc-head"><span id="jbcSym"></span><span id="jbcPx"></span><span id="jbcChg"></span><span id="jbcSrc"></span>' +
    '<button id="jbcClose" type="button" aria-label="Close">×</button></div>' +
    '<div id="jbcRanges"></div>' +
    '<div id="jbcInd"></div>' +
    '<div id="jbcBox"><canvas id="jbcCanvas"></canvas><div id="jbcEmpty"></div></div>' +
    '<canvas id="jbcRsi"></canvas>' +
    '<div id="jbcNews"></div>' +
    '<div id="jbcNote">Shaded, with a paler line = outside regular hours (overnight, pre-market, after-hours). Cyan line = 9:30 am ET open. Yellow dotted = last 4 pm close; purple dotted = overnight high / low; yellow ▼ at the top = a news headline (point at it to read it). Compare draws a second symbol as a dashed blue line, scaled to the same % move. Indicator buttons (off until you tap them): VWAP = average price paid since 4 am ET today, weighted by volume (white dashed); MA 20 / MA 50 = average of the last 20 / 50 bars (1-minute bars on 1H to 1D, 5-minute on 1W, 30-minute on 1M); RSI = strip under the chart, over 70 = stretched up, under 30 = stretched down; Prev day H/L = the last full regular session\'s high and low. Pre-mkt H/L / After-hrs H/L = the latest pre-market (4 - 9:30 am) / after-hours (4 - 8 pm) high and low; Busy night hours shades 9 - 10 pm and 3 - 4 am ET, the busiest overnight hours. Times are your local time. Tap outside or press Esc to close.</div></div>';
  document.body.appendChild(wrap);
  var $ = function (id) { return document.getElementById(id); };
  var canvas = $('jbcCanvas');

  RANGES.forEach(function (r) {
    var b = document.createElement('button'); b.type = 'button'; b.textContent = r.label; b.dataset.key = r.key;
    b.onclick = function () { range = r; lsSet(r.key); hover = null; paintRanges(); draw(); };
    $('jbcRanges').appendChild(b);
  });
  var cmpSel = document.createElement('select'); cmpSel.id = 'jbcCmp'; cmpSel.title = 'Draw a second symbol on the same chart, scaled to the same % move';
  $('jbcRanges').appendChild(cmpSel);
  cmpSel.onchange = function () { cmp = cmpSel.value; try { localStorage.setItem(CMPKEY, cmp); } catch (e) {} draw(); };
  function fillCmp() {
    var J = window.__jb, list = (J && J.state.symbols || []).slice();
    Object.keys(J && J.extra ? J.extra.history : {}).forEach(function (s) { if (list.indexOf(s) < 0) list.push(s); });
    list = list.filter(function (s) { return s !== sym; }).sort();
    cmpSel.innerHTML = '<option value="">Compare: off</option>' + list.map(function (s) {
      return '<option value="' + s.replace(/"/g, '') + '"' + (s === cmp ? ' selected' : '') + '>vs ' + s.replace(/</g, '') + '</option>'; }).join('');
  }
  function loadNews() {
    if (Date.now() - news.at < 180e3 || location.protocol.indexOf('http') !== 0) return;
    news.at = Date.now();
    fetch('/api/news', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.items) { news.items = d.items; draw(); } }).catch(function () {});
  }
  function paintRanges() {
    [].forEach.call($('jbcRanges').children, function (b) { b.classList.toggle('on', b.dataset.key === range.key); });
  }

  // ------------------------------------------------------------------ open / close
  function open(s) {
    sym = s; hover = null;
    $('jbcSym').textContent = s;
    vol.rows = []; vol.sym = null; paintRanges(); fillCmp(); loadNews(); loadVolume(); wrap.classList.add('open'); draw();
    clearInterval(timer); timer = setInterval(draw, 1000);
  }
  function close() { wrap.classList.remove('open'); sym = null; clearInterval(timer); timer = 0; }
  $('jbcClose').onclick = close;
  wrap.addEventListener('click', function (e) { if (e.target === wrap) close(); });
  document.addEventListener('keydown', function (e) { if ((e.key === 'Escape' || e.keyCode === 27) && sym) close(); });
  window.addEventListener('resize', function () { if (sym) draw(); });

  // tiles: click anywhere on the tile except its × button
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    if (t.closest('.remove')) return;
    var tile = t.closest('.tile');
    if (tile && tile.dataset.sym) open(tile.dataset.sym);
    var btn = t.closest('.jbc-open');
    if (btn) { e.stopPropagation(); var row = btn.closest('.row'); if (row && row.dataset.sym) open(row.dataset.sym); }
  });
  // list rows (phones): add a "Chart" button to each row's opened section
  function addRowButtons() {
    [].forEach.call(document.querySelectorAll('.row .detail .foot'), function (f) {
      if (f.querySelector('.jbc-open')) return;
      var b = document.createElement('button'); b.type = 'button'; b.className = 'jbc-open'; b.textContent = 'CHART';
      f.appendChild(b);
    });
  }
  setInterval(addRowButtons, 1000); addRowButtons();

  // ------------------------------------------------------------------ data
  function board() { return window.__jb && window.__jb.state; }
  // points inside the range, starting with the last known price at the range's start
  function series(now, which) {
    var S = board(), J = window.__jb, w = which || sym, hist = S && (S.history[w] || (J && J.extra && J.extra.history[w]));
    if (!hist || !hist.length) return [];
    var start = now - range.ms, out = [], i = 0;
    while (i < hist.length && hist[i].t < start) i++;
    if (i > 0) out.push({ t: start, p: hist[i - 1].p });
    for (; i < hist.length; i++) out.push(hist[i]);
    if (out.length) out.push({ t: now, p: out[out.length - 1].p });   // carry the last price to "now"
    return out;
  }
  // keep the shape when there are far more points than pixels: per pixel column, keep the
  // lowest and highest price in time order
  function thin(pts, x0, x1, t0, t1) {
    var cols = Math.max(1, Math.round(x1 - x0)), out = [], col = -1, lo = null, hi = null;
    if (pts.length <= cols * 2) return pts;
    function flushCol() { if (!lo) return; if (lo === hi) out.push(lo); else if (lo.t < hi.t) out.push(lo, hi); else out.push(hi, lo); }
    pts.forEach(function (p) {
      var c = Math.floor((p.t - t0) / (t1 - t0) * cols);
      if (c !== col) { flushCol(); col = c; lo = hi = p; }
      else { if (p.p < lo.p) lo = p; if (p.p > hi.p) hi = p; }
    });
    flushCol();
    out.push(pts[pts.length - 1]);
    return out;
  }

  // ------------------------------------------------------------------ formatting
  function fmtPrice(p) {
    if (p == null) return '—';
    if (p >= 1000) return p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    var dec = (String(+p.toFixed(6)).split('.')[1] || '').length;
    return p.toFixed(Math.max(2, Math.min(6, dec)));
  }
  function fmtTime(t, withDay) {
    var d = new Date(t), hm = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    return withDay ? d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) + ' ' + hm : hm;
  }
  function fmtAxis(t) {
    var d = new Date(t);
    if (range.ms <= 24 * 3600e3) return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  }
  function niceStep(span, n) {
    var raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
    return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
  }

  // ------------------------------------------------------------------ market sessions (US/Eastern)
  // minutes ET is ahead of UTC at time t (negative: -240 in summer, -300 in winter)
  var ET = null;
  try { ET = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric' }); } catch (e) {}
  function etOffset(t) {
    if (!ET) return -300;
    var o = {}; ET.formatToParts(new Date(t)).forEach(function (x) { o[x.type] = +x.value; });
    return Math.round((Date.UTC(o.year, o.month - 1, o.day, o.hour % 24, o.minute) - Math.floor(t / 60000) * 60000) / 60000);
  }
  // regular sessions (9:30 am - 4:00 pm ET, Mon-Fri) that touch [t0, t1], as real timestamps
  function regularSessions(t0, t1) {
    var out = [], day = 86400e3, off = etOffset(t0), d = Math.floor((t0 + off * 60e3) / day) * day;
    for (; d - off * 60e3 <= t1 + day; d += day) {
      var wd = new Date(d).getUTCDay(); if (wd === 0 || wd === 6) continue;
      off = etOffset(d + 12 * 3600e3 - off * 60e3);
      out.push({ open: d + 9.5 * 3600e3 - off * 60e3, close: d + 16 * 3600e3 - off * 60e3 });
    }
    return out;
  }
  function sessionName(t) {
    var off = etOffset(t), w = new Date(t + off * 60e3), wd = w.getUTCDay(), m = w.getUTCHours() * 60 + w.getUTCMinutes();
    if (wd === 6 || (wd === 0 && m < 1200) || (wd === 5 && m >= 1200)) return 'weekend';
    if (m < 240 || m >= 1200) return 'overnight';
    if (m < 570) return 'pre-market';
    if (m < 960) return 'regular hours';
    return 'after-hours';
  }

  // ------------------------------------------------------------------ drawing
  // ------------------------------------------------------------------ indicators (jb-analysis)
  // Buttons under the ranges turn on: VWAP, 20 and 50-bar moving averages, RSI (its own strip
  // under the chart) and the previous regular session's high / low. All off until you turn them on.
  var IND = [
    { k: 'vwap', label: 'VWAP', c: '#f2f2f2' }, { k: 'ma20', label: 'MA 20', c: '#ffd166' },
    { k: 'ma50', label: 'MA 50', c: '#ff9e40' }, { k: 'rsi', label: 'RSI', c: '#d6a8ff' },
    { k: 'pday', label: 'Prev day H/L', c: '#9fb3c8' },
    { k: 'pmhl', label: 'Pre-mkt H/L', c: '#5dd6c0' }, { k: 'ahhl', label: 'After-hrs H/L', c: '#ff9ecb' },
    { k: 'busy', label: 'Busy night hours', c: '#b39dff' }
  ];
  var INDKEY = 'jb_chart_ind', ind = {};
  try { (localStorage.getItem(INDKEY) || '').split(',').forEach(function (k) { if (k) ind[k] = true; }); } catch (e) {}
  (function () {
    var s = document.createElement('style');
    s.textContent = '#jbcInd{display:flex;gap:4px;flex-wrap:wrap;margin:0 0 6px;}' +
      '#jbcInd button{background:transparent;border:1px solid #164060;color:#6f8aa3;font:inherit;font-size:10px;font-weight:700;padding:4px 8px;cursor:pointer;border-radius:3px;}' +
      '#jbcInd button.on{color:#04101f;}' +
      '#jbcRsi{display:none;width:100%;height:70px;margin-top:2px;}';
    document.head.appendChild(s);
    var row = $('jbcInd');
    IND.forEach(function (d) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = d.label; b.dataset.k = d.k;
      b.title = { vwap: 'Volume-weighted average price since 4 am ET today', ma20: 'Average of the last 20 bars', ma50: 'Average of the last 50 bars',
        rsi: 'Relative Strength Index (14 bars): over 70 = stretched up, under 30 = stretched down', pday: 'High and low of the last full regular session (9:30 am - 4 pm)',
        pmhl: 'High and low of the latest pre-market (4:00 - 9:30 am)', ahhl: 'High and low of the latest after-hours (4 - 8 pm)',
        busy: 'Shades 9-10 pm and 3-4 am ET, when overnight trading is busiest (Asia opens / closes)' }[d.k];
      b.onclick = function () {
        ind[d.k] = !ind[d.k]; paintInd();
        try { localStorage.setItem(INDKEY, Object.keys(ind).filter(function (k) { return ind[k]; }).join(',')); } catch (e) {}
        draw();
      };
      row.appendChild(b);
    });
    paintInd();
  })();
  function paintInd() {
    [].forEach.call($('jbcInd').children, function (b) {
      var d = IND.filter(function (x) { return x.k === b.dataset.k; })[0], on = !!ind[d.k];
      b.classList.toggle('on', on); b.style.background = on ? d.c : 'transparent'; b.style.borderColor = on ? d.c : '#164060';
    });
    $('jbcRsi').style.display = ind.rsi ? 'block' : 'none';
  }
  function barMs() { return range.ms <= 24 * 3600e3 ? 60e3 : range.ms <= 7 * 24 * 3600e3 ? 5 * 60e3 : 30 * 60e3; }
  // last price in each bar of barMs(), only bars that had prices (so nights and weekends with no
  // trades don't flatten the averages)
  function indBars(from) {
    var S = board(), J = window.__jb, hist = S && (S.history[sym] || (J && J.extra && J.extra.history[sym])) || [], bs = barMs(), out = [];
    hist.forEach(function (p) {
      if (p.t < from) return;
      var b = Math.floor(p.t / bs) * bs;
      if (out.length && out[out.length - 1].b === b) out[out.length - 1].p = p.p; else out.push({ b: b, t: b + bs, p: p.p });
    });
    return out;
  }
  function sma(bars, n) {
    var out = [], sum = 0;
    for (var i = 0; i < bars.length; i++) {
      sum += bars[i].p; if (i >= n) sum -= bars[i - n].p;
      if (i >= n - 1) out.push({ t: bars[i].t, p: sum / n });
    }
    return out;
  }
  function rsiOf(bars, n) {
    var out = [], g = 0, l = 0;
    for (var i = 1; i < bars.length; i++) {
      var ch = bars[i].p - bars[i - 1].p, up = Math.max(ch, 0), dn = Math.max(-ch, 0);
      if (i <= n) { g += up / n; l += dn / n; } else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; }
      if (i >= n) out.push({ t: bars[i].t, v: l === 0 ? 100 : 100 - 100 / (1 + g / l) });
    }
    return out;
  }
  function since4am(now) {
    var off = etOffset(now), day = 86400e3, d = Math.floor((now + off * 60e3) / day) * day, t = d + 4 * 3600e3 - off * 60e3;
    return t > now ? t - day : t;
  }
  function vwapLine(now) {
    if (vol.sym !== sym || !vol.rows.length) return [];
    var S = board(), hist = (S && S.history[sym]) || [], a = since4am(now), pv = 0, vv = 0, out = [], j = 0, last = null;
    vol.rows.slice().sort(function (x, y) { return x[0] - y[0]; }).forEach(function (r) {
      if (r[0] < a || !r[1]) return;
      while (j < hist.length && hist[j].t <= r[0] + 60e3) { last = hist[j].p; j++; }
      if (last == null) return;
      pv += last * r[1]; vv += r[1]; out.push({ t: r[0] + 60e3, p: pv / vv });
    });
    return out;
  }
  function ydayRefs(refs, now) {
    if (!ind.pday) return;
    var S = board(), hist = (S && S.history[sym]) || [];
    var done = regularSessions(now - 6 * 86400e3, now).filter(function (r) { return r.close <= now; });
    var s = done[done.length - 1]; if (!s) return;
    var lo = Infinity, hi = -Infinity;
    hist.forEach(function (p) { if (p.t >= s.open && p.t <= s.close) { if (p.p < lo) lo = p.p; if (p.p > hi) hi = p.p; } });
    if (hi < lo) return;
    refs.push({ p: hi, c: '#9fb3c8', l: 'prev day H', x: 220 }); refs.push({ p: lo, c: '#9fb3c8', l: 'prev day L', x: 220 });
  }
  function drawInd(ctx, now, X, Y, t0, t1, x0, x1, y0, y1) {
    var need = ind.ma20 || ind.ma50 || ind.rsi;
    var bars = need ? indBars(t0 - 60 * barMs() * 3) : [];
    var lines = [];
    if (ind.ma20) lines.push({ pts: sma(bars, 20), c: '#ffd166', l: 'MA 20' });
    if (ind.ma50) lines.push({ pts: sma(bars, 50), c: '#ff9e40', l: 'MA 50' });
    if (ind.vwap) lines.push({ pts: vwapLine(now), c: '#f2f2f2', l: 'VWAP' });
    ctx.save(); ctx.beginPath(); ctx.rect(x0, y0, x1 - x0, y1 - y0); ctx.clip();
    var key = [];
    lines.forEach(function (L) {
      var p = L.pts.filter(function (q) { return q.t >= t0 && q.t <= t1; });
      key.push({ c: L.c, l: L.l + (p.length ? ' ' + fmtPrice(+p[p.length - 1].p.toFixed(p[p.length - 1].p < 10 ? 4 : 2)) : ' (not enough data)') });
      if (p.length < 2) return;
      ctx.strokeStyle = L.c; ctx.lineWidth = 1.2; ctx.setLineDash(L.l === 'VWAP' ? [6, 3] : []); ctx.beginPath();
      p.forEach(function (q, i) { if (i === 0) ctx.moveTo(X(q.t), Y(q.p)); else ctx.lineTo(X(q.t), Y(q.p)); });
      ctx.stroke(); ctx.setLineDash([]);
    });
    ctx.restore();
    ctx.font = '10px "IBM Plex Mono", ui-monospace, Menlo, monospace'; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    var kx = x0 + 4, kw = 0;
    key.forEach(function (k) { kw += ctx.measureText(k.l).width + 14; });
    if (key.length) { ctx.fillStyle = 'rgba(4,16,31,.85)'; ctx.fillRect(x0 + 1, y0 + 10, kw, 15); }
    key.forEach(function (k) { ctx.fillStyle = k.c; ctx.fillText(k.l, kx, y0 + 12); kx += ctx.measureText(k.l).width + 14; });
    // RSI strip
    var rc = $('jbcRsi');
    if (!ind.rsi) return;
    var W = rc.clientWidth, H = rc.clientHeight, dpr = window.devicePixelRatio || 1;
    if (rc.width !== Math.round(W * dpr) || rc.height !== Math.round(H * dpr)) { rc.width = Math.round(W * dpr); rc.height = Math.round(H * dpr); }
    var c = rc.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
    var ry0 = 4, ry1 = H - 4, RY = function (v) { return ry1 - v / 100 * (ry1 - ry0); };
    c.fillStyle = 'rgba(214,168,255,.07)'; c.fillRect(x0, RY(70), x1 - x0, RY(30) - RY(70));
    c.strokeStyle = '#0f2c47'; c.lineWidth = 1; c.strokeRect(x0 + .5, ry0 + .5, x1 - x0 - 1, ry1 - ry0 - 1);
    c.font = '10px "IBM Plex Mono", ui-monospace, Menlo, monospace'; c.fillStyle = '#6f8aa3'; c.textBaseline = 'middle'; c.textAlign = 'left';
    [30, 70].forEach(function (v) { c.setLineDash([2, 3]); c.strokeStyle = '#6f8aa3'; c.beginPath(); c.moveTo(x0, Math.round(RY(v)) + .5); c.lineTo(x1, Math.round(RY(v)) + .5); c.stroke(); c.setLineDash([]); c.fillText(String(v), x1 + 6, RY(v)); });
    var r = rsiOf(bars, 14).filter(function (q) { return q.t >= t0 && q.t <= t1; });
    if (r.length < 2) { c.fillText('RSI: not enough prices yet for this range', x0 + 6, H / 2); return; }
    c.strokeStyle = '#d6a8ff'; c.lineWidth = 1.2; c.beginPath();
    r.forEach(function (q, i) { if (i === 0) c.moveTo(X(q.t), RY(q.v)); else c.lineTo(X(q.t), RY(q.v)); });
    c.stroke();
    var lv = r[r.length - 1].v;
    c.fillStyle = '#d6a8ff'; c.textBaseline = 'top';
    c.fillText('RSI 14 · ' + lv.toFixed(0) + (lv >= 70 ? ' stretched up' : lv <= 30 ? ' stretched down' : ''), x0 + 6, ry0 + 3);
  }

  function draw() {
    if (!sym) return;
    var S = board();
    if (S && S.symbols.indexOf(sym) < 0 && S.gotWatchlist) { close(); return; }
    var box = $('jbcBox'), W = box.clientWidth, H = box.clientHeight, dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    }
    var ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var now = Date.now(), pts = series(now), empty = $('jbcEmpty');
    var src = S && S.lastSource[sym];
    $('jbcSrc').textContent = src === 'ibkr-delayed' ? 'ibkr · delayed ~15m' : src ? src.replace('-', ' · ') : '';
    if (pts.length < 2) {
      $('jbcPx').textContent = ''; $('jbcChg').textContent = '';
      empty.textContent = 'No prices yet for ' + sym + '. The chart fills in as prices arrive.';
      empty.style.display = 'flex'; return;
    }
    empty.style.display = 'none';

    var first = pts[0], last = pts[pts.length - 1], up = last.p >= first.p, color = up ? GREEN : RED;
    var pct = first.p ? (last.p - first.p) / first.p * 100 : 0;
    $('jbcPx').textContent = fmtPrice(last.p);
    $('jbcChg').textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '% ' + range.label;
    $('jbcChg').style.color = color;

    // plot area (price labels on the right, times along the bottom)
    var padL = 8, padR = 70, padT = 10, padB = 24;
    var x0 = padL, x1 = W - padR, y0 = padT, y1 = H - padB;
    var t0 = now - range.ms, t1 = now;
    // if the history doesn't reach back the whole range yet, start the chart where it begins
    if (first.t > t0) t0 = first.t;
    if (t1 - t0 < 60e3) t0 = t1 - 60e3;
    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) { if (p.p < lo) lo = p.p; if (p.p > hi) hi = p.p; });
    // reference lines: last 4 pm close and the overnight high / low (pulled into view on 1D and longer)
    var J = window.__jb, refs = [];
    if (J && J.lastCloseMs) {
      var hist = S.history[sym] || [], cl = null, ct = J.lastCloseMs(now);
      for (var q = hist.length - 1; q >= 0; q--) if (hist[q].t <= ct) { cl = hist[q].p; break; }
      if (cl != null) refs.push({ p: cl, c: '#ffc400', l: 'close' });
      var sv = J.sessionVals(sym);
      if (sv && sv.range) { refs.push({ p: sv.range.hi, c: '#b39dff', l: 'O/N H' }); refs.push({ p: sv.range.lo, c: '#b39dff', l: 'O/N L' }); }
      var HD = J.holdings ? J.holdings() : null, mine = HD && HD.pos[sym];
      if (mine && mine.shares > 0 && mine.avg) refs.push({ p: mine.avg, c: '#ff8bd1', l: 'your cost' });
      ydayRefs(refs, now);
      if (window.__jbPlusRefs) window.__jbPlusRefs(refs, now, sym, ind);   // pre-market / after-hours lines (plus.js)
      if (range.ms >= 24 * 3600e3) refs.forEach(function (r) { if (r.p < lo) lo = r.p; if (r.p > hi) hi = r.p; });
    }
    // compare: the other symbol's % move from the range start, drawn on this symbol's scale
    var cpts = [];
    if (cmp && cmp !== sym) {
      var cs = series(now, cmp).filter(function (p) { return p.t >= Math.max(now - range.ms, first.t); });
      if (cs.length > 1) cpts = cs.map(function (p) { return { t: p.t, p: first.p * p.p / cs[0].p }; });
      cpts.forEach(function (p) { if (p.p < lo) lo = p.p; if (p.p > hi) hi = p.p; });
    }
    if (hi - lo < hi * 1e-5) { hi += hi * 5e-4 || 1; lo -= lo * 5e-4 || 1; }
    var padP = (hi - lo) * 0.08; lo -= padP; hi += padP;
    function X(t) { return x0 + (t - t0) / (t1 - t0) * (x1 - x0); }
    function Y(p) { return y1 - (p - lo) / (hi - lo) * (y1 - y0); }
    if (window.__jbPlusScale) window.__jbPlusScale(sym, lo, hi, y0, y1);   // price under a tap (plus.js order ticket)

    ctx.font = '10px "IBM Plex Mono", ui-monospace, Menlo, monospace';
    ctx.textBaseline = 'middle';
    // price grid
    var step = niceStep(hi - lo, Math.max(3, Math.floor((y1 - y0) / 55)));
    ctx.strokeStyle = GRID; ctx.lineWidth = 1; ctx.fillStyle = DIM; ctx.textAlign = 'left';
    for (var v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      var gy = Math.round(Y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(x0, gy); ctx.lineTo(x1, gy); ctx.stroke();
      ctx.fillText(fmtPrice(+v.toPrecision(10)), x1 + 6, gy);
    }
    // shade everything outside regular hours (overnight, pre-market, after-hours, weekends) and
    // mark each 9:30 am ET open with a cyan line, so the overnight move into the open stands out
    var sess = regularSessions(t0, t1), prev = t0;
    ctx.fillStyle = 'rgba(0,0,0,.28)';
    sess.concat([{ open: t1, close: t1 }]).forEach(function (r) {
      var a = Math.max(prev, t0), b = Math.min(r.open, t1);
      if (b > a) ctx.fillRect(X(a), y0, X(b) - X(a), y1 - y0);
      prev = Math.max(prev, r.close);
    });
    if (window.__jbPlusShade) window.__jbPlusShade(ctx, X, t0, t1, y0, y1, ind);   // busy night hours (plus.js)
    ctx.strokeStyle = 'rgba(0,229,255,.55)'; ctx.fillStyle = 'rgba(0,229,255,.8)'; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    sess.forEach(function (r) {
      if (r.open <= t0 || r.open >= t1) return;
      var ox = Math.round(X(r.open)) + 0.5;
      ctx.beginPath(); ctx.moveTo(ox, y0); ctx.lineTo(ox, y1); ctx.stroke();
      if (range.ms <= 7 * 24 * 3600e3) ctx.fillText('open', ox + 3, y1 - 13);
    });
    ctx.strokeStyle = GRID; ctx.fillStyle = DIM;
    // time labels
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillStyle = DIM;
    var nLab = Math.max(2, Math.floor((x1 - x0) / 110));
    for (var k = 0; k <= nLab; k++) {
      var tt = t0 + (t1 - t0) * k / nLab, gx = Math.round(X(tt)) + 0.5;
      ctx.beginPath(); ctx.moveTo(gx, y0); ctx.lineTo(gx, y1); ctx.stroke();
      ctx.textAlign = k === 0 ? 'left' : k === nLab ? 'right' : 'center';
      ctx.fillText(fmtAxis(tt), gx, y1 + 6);
    }
    // dashed line at the range's starting price
    ctx.setLineDash([3, 4]); ctx.strokeStyle = DIM;
    ctx.beginPath(); ctx.moveTo(x0, Math.round(Y(first.p)) + 0.5); ctx.lineTo(x1, Math.round(Y(first.p)) + 0.5); ctx.stroke();
    ctx.setLineDash([]);

    // the price line, stepped (a price holds until the next trade) with a soft fill under it.
    // Outside regular hours (overnight, pre-market, after-hours) the line is a paler shade.
    var draw_pts = thin(pts.filter(function (p) { return p.t >= t0; }), x0, x1, t0, t1);
    function regular(t) { for (var s = 0; s < sess.length; s++) if (t >= sess[s].open && t < sess[s].close) return true; return false; }
    ctx.beginPath();
    draw_pts.forEach(function (p, i) {
      var x = X(p.t), y = Y(p.p);
      if (i === 0) ctx.moveTo(x, y); else { ctx.lineTo(x, Y(draw_pts[i - 1].p)); ctx.lineTo(x, y); }
    });
    ctx.lineTo(X(draw_pts[draw_pts.length - 1].t), y1); ctx.lineTo(X(draw_pts[0].t), y1); ctx.closePath();
    var g = ctx.createLinearGradient(0, y0, 0, y1);
    g.addColorStop(0, up ? 'rgba(0,255,65,.18)' : 'rgba(255,59,48,.18)'); g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g; ctx.fill();
    ctx.lineWidth = 1.6; ctx.lineJoin = 'round';
    var light = up ? GREEN_X : RED_X, run = null;
    for (var i = 1; i < draw_pts.length; i++) {
      var a = draw_pts[i - 1], b = draw_pts[i], c = (a.f || b.f) ? '#6f8fb0' : regular(a.t) ? color : light;   // blue-grey = filled in after downtime (jb-ideas)
      if (c !== run) {
        if (run) ctx.stroke();
        run = c; ctx.strokeStyle = c; ctx.beginPath(); ctx.moveTo(X(a.t), Y(a.p));
      }
      ctx.lineTo(X(b.t), Y(a.p)); ctx.lineTo(X(b.t), Y(b.p));
    }
    if (run) ctx.stroke();

    // reference lines + labels
    ctx.font = '10px "IBM Plex Mono", ui-monospace, Menlo, monospace'; ctx.textBaseline = 'bottom'; ctx.textAlign = 'left';
    refs.forEach(function (r) {
      if (r.p < lo || r.p > hi) return;
      var ry = Math.round(Y(r.p)) + 0.5;
      ctx.setLineDash([2, 3]); ctx.strokeStyle = r.c; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x0, ry); ctx.lineTo(x1, ry); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = r.c; ctx.fillText(r.l + ' ' + fmtPrice(r.p), r.x != null ? x0 + r.x : r.l === 'close' ? x0 + 4 : r.l === 'your cost' ? x0 + 300 : x0 + 150, ry - 2);
    });
    // compare line
    if (cpts.length > 1) {
      var cd = thin(cpts, x0, x1, t0, t1), cl2 = cpts[cpts.length - 1], cpct = (cl2.p - first.p) / first.p * 100;
      ctx.setLineDash([5, 3]); ctx.strokeStyle = '#4fc3ff'; ctx.lineWidth = 1.3; ctx.beginPath();
      cd.forEach(function (p, i) { if (i === 0) ctx.moveTo(X(p.t), Y(p.p)); else ctx.lineTo(X(p.t), Y(p.p)); });
      ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = '#4fc3ff'; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
      ctx.fillText(cmp + ' ' + (cpct >= 0 ? '+' : '') + cpct.toFixed(2) + '%', x1 - 4, y1 - 2);
    }
    drawInd(ctx, now, X, Y, t0, t1, x0, x1, y0, y1);
    // volume bars along the bottom (recorded by the Mac; summed into ~120 slices)
    loadVolume();
    if (vol.sym === sym && vol.rows.length) {
      var nb = 120, bw = (t1 - t0) / nb, sums = new Array(nb).fill(0), mx = 0;
      vol.rows.forEach(function (r) { if (r[0] >= t0 && r[0] < t1) { var k = Math.floor((r[0] - t0) / bw); sums[k] += r[1]; } });
      sums.forEach(function (v) { if (v > mx) mx = v; });
      if (mx > 0) {
        var vh = (y1 - y0) * 0.18, pw = (x1 - x0) / nb;
        ctx.fillStyle = 'rgba(160,180,200,.28)';
        sums.forEach(function (v, k) { if (v > 0) { var bh = Math.max(1, v / mx * vh); ctx.fillRect(x0 + k * pw + 0.5, y1 - bh, Math.max(1, pw - 1), bh); } });
        ctx.fillStyle = '#6f8aa3'; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
        ctx.fillText('vol (max ' + Math.round(mx).toLocaleString() + ')', x0 + 4, y1 - vh - 2);
      }
    }
    // your buys (▲) and sells (▼) from the Portfolio tab
    var HDx = J && J.holdings ? J.holdings() : null;
    if (HDx) HDx.trades.forEach(function (tr) {
      if (tr.sym !== sym) return;
      var tt = new Date(tr.date + 'T12:00:00').getTime();
      if (tt < t0 || tt > t1 || tr.price < lo || tr.price > hi) return;
      var mx2 = X(tt), my = Y(tr.price);
      ctx.fillStyle = tr.qty > 0 ? '#ff8bd1' : '#ffc400'; ctx.beginPath();
      if (tr.qty > 0) { ctx.moveTo(mx2, my - 1); ctx.lineTo(mx2 - 5, my + 8); ctx.lineTo(mx2 + 5, my + 8); }
      else { ctx.moveTo(mx2, my + 1); ctx.lineTo(mx2 - 5, my - 8); ctx.lineTo(mx2 + 5, my - 8); }
      ctx.closePath(); ctx.fill();
    });
    // news markers along the top
    var marks = [];
    news.items.forEach(function (it) {
      var nt = it.ts * 1000;
      if (it.syms && it.syms.indexOf(sym) >= 0 && nt >= t0 && nt <= t1) marks.push({ x: X(nt), it: it });
    });
    ctx.fillStyle = '#ffc400';
    marks.forEach(function (m) { ctx.beginPath(); ctx.moveTo(m.x - 4, y0); ctx.lineTo(m.x + 4, y0); ctx.lineTo(m.x, y0 + 7); ctx.closePath(); ctx.fill(); });
    var near = null;
    if (hover != null) marks.forEach(function (m) { if (Math.abs(m.x - hover) <= 6 && (!near || Math.abs(m.x - hover) < Math.abs(near.x - hover))) near = m; });
    $('jbcNews').textContent = near ? '▼ ' + fmtTime(near.it.ts * 1000, true) + '  ' + near.it.title : (marks.length ? marks.length + ' headline' + (marks.length > 1 ? 's' : '') + ' in this range: point at a ▼ to read' : '');

    // last price marker on the right axis
    var ly = Y(last.p);
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(X(last.t), ly, 3, 0, Math.PI * 2); ctx.fill();
    ctx.fillRect(x1 + 2, ly - 8, padR - 4, 16);
    ctx.fillStyle = '#04101f'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.font = 'bold 10px "IBM Plex Mono", ui-monospace, Menlo, monospace';
    ctx.fillText(fmtPrice(last.p), x1 + 6, ly);

    // crosshair: price at the pointer's time
    if (hover != null && hover >= x0 && hover <= x1) {
      var ht = t0 + (hover - x0) / (x1 - x0) * (t1 - t0), hp = null;
      for (var j = pts.length - 1; j >= 0; j--) { if (pts[j].t <= ht) { hp = pts[j].p; break; } }
      if (hp != null) {
        var hx = Math.round(hover) + 0.5, hy = Y(hp);
        ctx.strokeStyle = TEXT; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
        ctx.beginPath(); ctx.moveTo(hx, y0); ctx.lineTo(hx, y1); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = TEXT; ctx.beginPath(); ctx.arc(hx, hy, 3.5, 0, Math.PI * 2); ctx.fill();
        var hpct = first.p ? (hp - first.p) / first.p * 100 : 0;
        var label = fmtPrice(hp) + '  ' + (hpct >= 0 ? '+' : '') + hpct.toFixed(2) + '%  ' + fmtTime(ht, range.ms > 24 * 3600e3) + '  ' + sessionName(ht);
        ctx.font = '11px "IBM Plex Mono", ui-monospace, Menlo, monospace';
        var tw = ctx.measureText(label).width + 12, bx = Math.min(Math.max(hx - tw / 2, x0), x1 - tw);
        ctx.fillStyle = '#020a14'; ctx.strokeStyle = '#164060';
        ctx.fillRect(bx, y0, tw, 18); ctx.strokeRect(bx + 0.5, y0 + 0.5, tw - 1, 17);
        ctx.fillStyle = TEXT; ctx.textAlign = 'left'; ctx.fillText(label, bx + 6, y0 + 9);
      }
    }
  }

  function pointer(e) {
    var r = canvas.getBoundingClientRect(), p = e.touches ? e.touches[0] : e;
    hover = p ? p.clientX - r.left : null; draw();
  }
  canvas.addEventListener('mousemove', pointer);
  canvas.addEventListener('mouseleave', function () { hover = null; draw(); });
  canvas.addEventListener('touchstart', pointer, { passive: true });
  canvas.addEventListener('touchmove', pointer, { passive: true });
})();
