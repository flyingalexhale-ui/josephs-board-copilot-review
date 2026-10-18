/* Alex Hale's Board -- hedging on the algo tabs (jb-hedge).
 *   With Hedge   under Last backtest: when the backtested strategy has a hedge: section (for example a Hedge Idea
 *                sent from the My Trading tab), the result with its hedge leg next to the result alone.
 *   Auto-Hedge   in the Paper / live trading card: one click turns automatic hedging on for this algo's position (an
 *                inverse fund bought, or MNQ / MES sold at IBKR), with the instrument and hedge ratio in a small box.
 *                Default OFF. Paper first: in paper the hedge orders are simulated. Live hedging has its own switch:
 *                it asks you to type HEDGE LIVE and works only while ./jb live runs (for that run only).
 * Data: /api/algo/<name> (backtest_status.json metrics.hedge, from algo/autohedge.py), /api/plus/hedge/algo.
 * Hooked in by one <script src="hedge-algo.js"> line on algo.html. */
(function () {
  'use strict';
  if (window.__jbHedgeAlgo) return;
  window.__jbHedgeAlgo = true;
  var algo = (new URLSearchParams(location.search).get('name') || '').toLowerCase() === 'ibkr' ? 'ibkr' : 'webull';
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }
  function pc(v) { return v == null ? '—' : (v > 0 ? '+' : '') + (+v).toFixed(2) + '%'; }
  function cls(v) { return v == null ? '' : v > 0 ? 'up' : v < 0 ? 'down' : ''; }
  function usd(v) { return v == null ? '—' : (v < 0 ? '-$' : '+$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 }); }
  // Title Case for the algo's own plain notes (tickers, numbers and symbols stay as they are)
  function tcase(t) { return String(t == null ? '' : t).replace(/(^|[\s(\/-])([a-z])/g, function (m, a, b) { return a + b.toUpperCase(); }); }
  var st = document.createElement('style');
  st.textContent = '.hga{background:#0b2238;border:1px solid #164060;border-radius:8px;padding:8px 10px;margin:6px 0;font-size:12px;line-height:1.55;color:#cfd8e3;overflow-wrap:anywhere;}' +
    '.hga b{color:#fff;} .hga .hga-k{font-size:10px;letter-spacing:.08em;color:#6f8aa3;font-weight:700;margin-right:6px;} .hga .hga-n{font-size:11px;color:#6f8aa3;}' +
    '.hga .hga-row{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin:4px 0;}' +
    '.hga select{font:inherit;font-size:13px;padding:6px 8px;border-radius:6px;border:1px solid #164060;background:#000;color:#fff;max-width:100%;min-width:0;}' +
    '.hga label{display:flex;flex-wrap:wrap;align-items:center;gap:4px 6px;max-width:100%;min-width:0;}' +
    '.hga button{font:inherit;font-weight:700;font-size:12.5px;padding:7px 12px;border-radius:6px;border:1px solid #164060;background:#0f3320;color:#fff;cursor:pointer;}' +
    '.hga button.on{background:#2f7a44;border-color:#2f7a44;} .hga button.live{background:#5a1010;border-color:#a33;}' +
    '.hga .hga-st{font-size:12px;} .hga .up{color:#00ff41;} .hga .down{color:#ff3b30;} .hga .warn{color:#ffb648;} .hga .bad{color:#ff8a80;}';
  document.head.appendChild(st);

  // ---------------------------------------------------------------- backtest with its hedge leg
  var metrics = document.getElementById('bt-metrics');
  var hb = document.createElement('div'); hb.className = 'hga'; hb.id = 'hgaBt'; hb.style.display = 'none';
  if (metrics) metrics.parentNode.insertBefore(hb, metrics.nextSibling);
  function drawBt(bt) {
    var h = bt && bt.metrics && bt.metrics.hedge, m = (bt && bt.metrics) || {};
    if (!h) { hb.style.display = 'none'; return; }
    hb.style.display = '';
    if (h.error) { hb.innerHTML = '<span class="hga-k">WITH HEDGE</span>' + esc(h.symbol) + ': ' + esc(h.error); return; }
    hb.innerHTML = '<span class="hga-k">WITH HEDGE</span><b>' + (h.side === 'sell' ? 'Sell ' : 'Buy ') + esc(h.symbol) + '</b> ($' + (+h.dollar_ratio).toFixed(2) + ' Per $1 Held, ' + (+h.ratio_pct) + '%)' +
      ' · Return <b class="' + cls(h.combined_return_pct) + '">' + pc(h.combined_return_pct) + '</b> (Alone <span class="' + cls(m.total_return_pct) + '">' + pc(m.total_return_pct) + '</span>)' +
      ' · Max Drawdown <b class="down">' + pc(h.combined_max_drawdown_pct) + '</b> (Alone ' + pc(m.max_drawdown_pct) + ')' +
      ' · Win Rate ' + (h.combined_win_rate_pct == null ? '—' : Math.round(h.combined_win_rate_pct) + '%') + ' · ' + h.trips + ' Hedge Legs, Hedge P&amp;L ' + usd(h.pnl) +
      '<div class="hga-n">The Hedge Leg Uses The Board\'s Own Record Of ' + esc(h.record) + ', Opened With Each Entry And Closed With Each Exit, At The Same Costs.' +
      (h.note ? ' ' + esc(h.note) + '.' : '') + ' Backtest Only: Paper And Live Trading Hedge Only When Auto-Hedge Is On.</div>';
  }
  function load() {
    if (document.hidden) return;
    fetch('/api/algo/' + algo, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) drawBt(d.backtest); }).catch(function () {});
  }

  // ---------------------------------------------------------------- Auto-Hedge box (paper first, live has its own switch)
  var stopRow = document.getElementById('k-msg'); stopRow = stopRow && stopRow.closest('.ctl');
  var ab = document.createElement('div'); ab.className = 'hga'; ab.id = 'hgaAuto';
  if (stopRow) stopRow.parentNode.insertBefore(ab, stopRow.nextSibling);
  var ad = null, amsg = '', abusy = false;
  function api(p, body) {
    return fetch('/api/plus/hedge/' + p, body ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.json().then(function (j) { if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status)); return j; }); });
  }
  function px(v) { return v == null ? '—' : (+v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 }); }
  function drawAuto(d) {
    ad = d;
    var c = d.settings || {}, on = !!c.on, live = !!c.live_on, st = d.status || {}, ch = d.choices || [];
    var inst = (c.instrument || 'auto').toUpperCase(), ratio = c.ratio_pct || 100;
    var firstEtf = ch.filter(function (x) { return x.kind === 'etf'; }).sort(function (a, b) { return (b.cut_day == null ? -999 : b.cut_day) - (a.cut_day == null ? -999 : a.cut_day); })[0];
    if (c.on && inst === 'AUTO' && c.symbol) firstEtf = ch.filter(function (x) { return x.symbol === c.symbol; })[0] || firstEtf;
    var opts = '<option value="auto"' + (inst === 'AUTO' ? ' selected' : '') + '>Auto' + (firstEtf ? ': ' + esc(firstEtf.symbol) + ' (' + esc(firstEtf.label) + ')' : '') + '</option>' +
      ch.map(function (x) { return '<option value="' + esc(x.symbol) + '"' + (inst === x.symbol ? ' selected' : '') + '>' + (x.side === 'sell' ? 'Sell ' : 'Buy ') + esc(x.symbol) + ' · ' + esc(x.label) +
        ' · $' + (+x.dollar_ratio).toFixed(2) + ' Per $1' + '</option>'; }).join('');
    var held = st.units ? (st.units > 0 ? 'Bought ' : 'Sold ') + Math.abs(st.units) + ' ' + esc(st.held_symbol) + ' @ ' + px(st.avg) +
      ' · Open <span class="' + cls(st.unrealized) + '">' + usd(st.unrealized) + '</span>' : 'No Hedge Open';
    ab.innerHTML = '<div class="hga-row"><span class="hga-k">AUTO-HEDGE</span>' +
      '<button type="button" id="hgaOn" class="' + (on ? 'on' : '') + '">' + (on ? 'On' : 'Turn On Auto-Hedge') + '</button>' +
      (on ? '<button type="button" id="hgaOff">Turn Off</button>' : '') +
      '<span class="hga-st">' + (on ? (st.mode === 'live' ? (st.active ? '<b class="up">Hedging (Live)</b>' : '<b class="warn">On In Paper Only</b>') : '<b class="up">On (Paper: Simulated)</b>') : 'Off (Default)') + '</span></div>' +
      '<div class="hga-row"><label class="hga-n">Hedge With <select id="hgaInst">' + opts + '</select></label>' +
      '<label class="hga-n">Hedge Ratio <select id="hgaRatio">' + [25, 50, 75, 100, 125, 150].map(function (r) { return '<option value="' + r + '"' + (+ratio === r ? ' selected' : '') + '>' + r + '%</option>'; }).join('') + '</select></label></div>' +
      (on ? '<div class="hga-row hga-st">' + held + (st.realized_today ? ' · Today <span class="' + cls(st.realized_today) + '">' + usd(st.realized_today) + '</span>' : '') +
        (st.pending ? ' · <b class="warn">Order Working</b>' : '') + '</div>' : '') +
      (on && !st.on ? '<div class="hga-n">Saved: The Algo Picks It Up Within A Few Seconds' + (d.symbol ? '' : ' Once It Runs') + '.</div>' : '') +
      ((st.why && on && st.on) ? '<div class="hga-n warn">Not Hedging New Positions: ' + esc(tcase(st.why)) + '</div>' : '') +
      (st.note && on ? '<div class="hga-n">Last: ' + esc(tcase(st.note)) + '</div>' : '') +
      '<div class="hga-row" style="margin-top:6px;border-top:1px solid #164060;padding-top:6px"><span class="hga-k">LIVE HEDGING</span>' +
      (live && d.live_ok ? '<b class="down">On For This Live Run</b> <button type="button" id="hgaLiveOff">Turn Off Live Hedging</button>'
        : '<span class="hga-st">Off</span> <button type="button" class="live" id="hgaLiveOn"' + (on ? '' : ' disabled title="Turn Auto-Hedge on and try it in paper first"') + '>Turn On Live Hedging…</button>') + '</div>' +
      '<div class="hga-n">' + (d.live_running ? 'Live Trading Is Running For This Algo (Go Live Or ./jb live).' : 'Live Trading Is Not Running (Go Live Or ./jb live): Live Hedging Can\'t Be Turned On.') +
      ' Live Hedging Needs Its Own Switch, Asks You To Type ' + esc(d.confirm_words) + ', And Ends With That Live Run.</div>' +
      (amsg ? '<div class="hga-n bad">' + esc(amsg) + '</div>' : '') +
      '<div class="hga-n">' + (d.symbol ? 'This Algo Trades ' + esc(d.symbol) + '. ' : '') + 'Every Position Gets The Hedge, Sized From The Board\'s Own Prices; It Is Closed With The Position. ' +
      'Stop, SELL ALL, The Daily Loss Limit, The Buying Power Check, The Order Speed Limits And The Pause After Rejected Orders All Apply To The Hedge. Futures (MNQ / MES) Only At IBKR.</div>';
    var $ = function (id) { return document.getElementById(id); };
    function save(onVal) {
      if (abusy) return; abusy = true; amsg = '';
      api('algo', { name: algo, on: onVal, ratio_pct: +$('hgaRatio').value, instrument: $('hgaInst').value })
        .then(drawAuto).catch(function (e) { amsg = e.message; drawAuto(ad); }).then(function () { abusy = false; });
    }
    $('hgaOn').onclick = function () { save(true); };
    if ($('hgaOff')) $('hgaOff').onclick = function () { save(false); };
    $('hgaInst').onchange = $('hgaRatio').onchange = function () { if (on) save(true); };
    var lo = $('hgaLiveOn'), lf = $('hgaLiveOff');
    if (lo) lo.onclick = function () {
      var w = window.prompt('LIVE HEDGING sends REAL hedge orders at ' + (algo === 'ibkr' ? 'IBKR' : 'Webull') + ' for this live run.\n\nType ' + d.confirm_words + ' to turn it on:');
      if (w == null) return;
      api('algo/live', { name: algo, on: true, confirm: w }).then(function (r) { amsg = ''; drawAuto(r); }).catch(function (e) { amsg = e.message; drawAuto(ad); });
    };
    if (lf) lf.onclick = function () { api('algo/live', { name: algo, on: false }).then(drawAuto).catch(function (e) { amsg = e.message; drawAuto(ad); }); };
  }
  function loadAuto() {
    if (document.hidden || !stopRow || abusy) return;
    var a = document.activeElement;
    if (a && a.tagName === 'SELECT' && ab.contains(a)) return;          // don't redraw while a list is open
    api('algo?name=' + algo).then(drawAuto).catch(function (e) { ab.innerHTML = '<span class="hga-k">AUTO-HEDGE</span><span class="bad">Not Available: ' + esc(e.message) + '</span>'; });
  }
  loadAuto();
  setInterval(loadAuto, 10000);
  window.JBHedgeAlgo = { algo: algo, esc: esc, reload: load };
  load();
  setInterval(load, 10000);
})();
