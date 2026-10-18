/* Night desk section: earnings expected move + call clock, night vs day, stress test, limit price
 * helper, fill quality, game plan (jb-earn). Data: /api/plus/earn */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var pl = ND.section('e-plan', 'Game plan for tomorrow');
  var er = ND.section('e-earn', 'Earnings this week: expected move and call clock');
  var lm = ND.section('e-limit', 'Limit price helper');
  var st = ND.section('e-stress', 'Stress test: what if the market drops?');
  var nd = ND.section('e-nd', 'Night vs day: holding overnight vs in the day');
  var fq = ND.section('e-fills', 'Fill quality: your fills vs the middle price');
  var sw = ND.section('e-switches', 'Earnings and plan alerts');
  var SW = [
    ['call_push', 'Earnings call in 10 minutes', 'A push 10 minutes before the earnings call of a stock on your boards or that you hold.'],
    ['plan_push', 'Morning game plan', 'Your game plan note pushed to your phone before the open on trading days (time: JB_PLAN_TIME in settings.env, now <b class="pt"></b>).']
  ];
  var drawn = false, last = null, ndSym = localStorage.getItem('nd_ndsym') || '';
  function money(v) { return v == null ? '–' : '<span class="' + (v > 0 ? 'up' : v < 0 ? 'dn' : '') + '">' + ND.money(v) + '</span>'; }
  function until(ms) {
    var m = Math.round((ms - Date.now()) / 60000);
    if (m < -90) return 'done';
    if (m < 0) return '<span class="warn">now (started ' + (-m) + ' min ago)</span>';
    return m < 60 ? '<b class="warn">in ' + m + ' min</b>' : m < 1440 ? 'in ' + Math.floor(m / 60) + ' h ' + (m % 60) + ' min' : 'in ' + Math.round(m / 1440) + ' days';
  }
  var DF = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit' });
  function drawPlan(p, t) {
    if (document.activeElement && document.activeElement.id === 'gpText') return;
    pl.innerHTML = '<div class="card" style="padding:10px 12px"><textarea id="gpText" rows="5" style="width:100%;box-sizing:border-box;background:transparent;color:inherit;border:1px solid #2a3d52;border-radius:6px;padding:8px;font:inherit" placeholder="e.g. If TQQQ holds above 80 pre-market, buy 50 at the open, stop 78. If ES=F is down 1%+ at 4 am, stay out.">' + esc(p.text || '') + '</textarea>' +
      '<div class="row2"><button id="gpSave">Save plan</button><span class="msg" id="gpMsg">' + (p.at ? 'Saved ' + esc(ND.ago(p.at)) : '') + '</span></div>' +
      '<div class="note" style="margin:4px 0 0">Write it the evening before, while you are calm. It is pushed to your phone at ' + esc(t) + ' am New York time on trading days (if saved in the last 36 hours) and shown on the phone page.</div></div>';
    document.getElementById('gpSave').onclick = function () {
      var m = document.getElementById('gpMsg'); m.textContent = 'Saving…';
      ND.post('earn/plan', { text: document.getElementById('gpText').value }).then(function () { m.textContent = 'Saved.'; }).catch(function (e) { m.textContent = e.message; });
    };
  }
  function drawEarn(rows) {
    er.innerHTML = (rows.length ? '<div class="card tbl"><table><tr><th>Stock</th><th>Reports</th><th>Call (ET)</th><th title="What options traders are paying for: the at-the-money call + put on the first expiry after the report, as a % of the price">Expected move</th><th title="Average size of its move on its last reports (close before to close after)">Usual move</th></tr>' + rows.map(function (r) {
      var em = r.em, ty = r.typical;
      return '<tr><td style="text-align:left"><b>' + esc(r.sym) + '</b>' + (r.held ? ' <span class="chip c-r">HELD</span>' : r.mine ? '' : ' <span class="note">scanner</span>') + '</td>' +
        '<td style="text-align:left">' + esc(r.date.slice(5)) + ' <span class="note">' + esc(r.when || '') + '</span>' + (r.release ? '<br><span class="note">' + esc(DF.format(new Date(r.release))) + (r.estimate ? ' (estimate)' : '') + '</span>' : '') + '</td>' +
        '<td style="text-align:left">' + (r.call ? esc(DF.format(new Date(r.call))) + '<br>' + until(r.call) : '<span class="note">not listed</span>') + '</td>' +
        '<td>' + (em ? '<b>±' + em.pct.toFixed(1) + '%</b><br><span class="note">$' + em.lo + ' - $' + em.hi + ' by ' + esc(em.expiry) + '</span>' : '<span class="note">–</span>') + '</td>' +
        '<td>' + (ty ? '±' + ty.avg.toFixed(1) + '% <span class="note">(' + ty.n + ')</span>' : '–') + '</td></tr>';
    }).join('') + '</table></div>' : '<p class="note">No earnings in the next 8 days for stocks on your boards, scanner or holdings.</p>') +
      '<p class="note">Expected move = the price of the at-the-money straddle (a call + a put) on the first expiry after the report: what traders pay to bet on a move either way, so roughly the size of move the market expects. ' +
      'If the usual move is bigger than the expected move, options look cheap for this report (and the other way round). Call times come from Yahoo\'s calendar; release times "before the open" / "after the close" from the scanner\'s calendar.</p>';
  }
  function drawStress(s) {
    var head = s.scenarios.map(function (v, i) { return '<th>' + (i === 3 ? 'Futures now<br><span class="note">S&amp;P ' + (v >= 0 ? '+' : '') + v + '%</span>' : 'S&amp;P ' + v + '%') + '</th>'; }).join('');
    st.innerHTML = (s.rows.length ? '<div class="card tbl"><table><tr><th>Holding</th><th>Value</th><th title="How much it usually moves for a 1% move in the S&P 500 (SPY), last 90 days. A 3x fund is about 3.">Beta</th>' + head + '</tr>' +
      s.rows.map(function (r) {
        return '<tr><td style="text-align:left"><b>' + esc(r.sym) + '</b> <span class="note">' + esc(r.from) + '</span></td><td>' + ND.money(r.value) + '</td><td title="' + esc(r.beta_why) + '">' + r.beta + '</td>' + r.moves.map(money).map(function (m) { return '<td>' + m + '</td>'; }).join('') + '</tr>';
      }).join('') + '<tr><td style="text-align:left"><b>Total</b></td><td>' + ND.money(s.value) + '</td><td></td>' + s.total.map(function (m) { return '<td><b>' + money(m) + '</b></td>'; }).join('') + '</tr></table></div>'
      : '<p class="note">Nothing held right now (per your Portfolio tab and the My trading journal).</p>') +
      '<p class="note">A rough guide: each holding\'s value × its beta × the S&amp;P move. Real moves differ, especially overnight and around news; 3x funds can lose more than 3x over several days.</p>';
  }
  function drawFills(f) {
    var r = f.rows || [];
    fq.innerHTML = (r.length ? '<p>Average: <b>' + (f.avg_cents >= 0 ? '<span class="up">' + f.avg_cents + '¢ better</span>' : '<span class="dn">' + (-f.avg_cents) + '¢ worse</span>') + '</b> than the middle per share, ' + money(f.total) + ' in total over these ' + r.length + ' fills.</p>' +
      '<div class="card tbl"><table><tr><th>When (ET)</th><th>Fill</th><th>Bid / ask then</th><th>vs middle</th></tr>' + r.map(function (x) {
        return '<tr><td style="text-align:left">' + esc(ND.time(x.t)) + '</td><td style="text-align:left">' + esc(x.side.toUpperCase() + ' ' + x.qty + ' ' + x.sym) + ' @ ' + x.price + '</td><td>' + x.bid + ' / ' + x.ask + '</td><td>' + (x.cents >= 0 ? '<span class="up">+' : '<span class="dn">') + x.cents + '¢</span></td></tr>';
      }).join('') + '</table></div>' : '<p class="note">' + esc(f.note || 'No fills with a saved bid / ask yet. From now on, each fill on a symbol on your IBKR board is compared with the bid and ask at that minute.') + '</p>') +
      '<p class="note">+ = you bought below the middle of the bid and ask (or sold above it): good. − = you paid up, which is normal for market orders and thin overnight markets. Uses the IBKR bid / ask saved by the Night desk.</p>';
  }
  function drawLimit(syms) {
    if (lm.dataset.done) return; lm.dataset.done = 1;
    lm.innerHTML = '<div class="card" style="padding:10px 12px"><div class="row2"><input type="text" id="lhSym" placeholder="TQQQ" size="8" autocapitalize="characters" list="lhList"><datalist id="lhList">' + syms.map(function (s) { return '<option value="' + esc(s) + '">'; }).join('') + '</datalist>' +
      '<select id="lhSide"><option value="buy">Buy</option><option value="sell">Sell</option></select><input id="lhQty" type="number" placeholder="shares" style="width:80px"><button id="lhGo">Work it out</button></div><div id="lhOut"></div></div>' +
      '<p class="note">Outside regular hours, always use limit orders: spreads are wider and a market-style order can fill far from the last price. "Fair" is a quarter of the spread past the middle, which usually fills quickly; "patient" is the middle; "now" takes the other side at once. From Dec 6 the exchanges reject overnight orders more than 20% from the last price (band shown).</p>';
    function go() {
      var s = document.getElementById('lhSym').value.trim().toUpperCase(), side = document.getElementById('lhSide').value, q = +document.getElementById('lhQty').value || 0, o = document.getElementById('lhOut');
      if (!s) return; o.innerHTML = '<span class="note">…</span>';
      ND.api('earn/limit?sym=' + encodeURIComponent(s) + '&side=' + side).then(function (d) {
        if (d.error) { o.innerHTML = '<p class="err">' + esc(d.error) + '</p>'; return; }
        var cost = function (p) { return q && p ? '<br><span class="note">' + ND.money(p * q) + ' for ' + q + '</span>' : ''; };
        var line = function (k, v, strong) { return '<div style="display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-bottom:1px solid rgba(127,147,168,.15)"><span>' + (strong ? '<b>' + k + '</b>' : k) + '</span><span style="text-align:right">' + (strong ? '<b>' + v + '</b>' : v) + '</span></div>'; };
        var cost2 = function (p) { return q && p ? ' <span class="note">· ' + ND.money(p * q) + '</span>' : ''; };
        o.innerHTML = d.bid != null ? '<div style="margin-top:8px">' + line('Bid / ask', d.bid + ' / ' + d.ask + ' <span class="note">· spread ' + d.spread_pct + '%</span>') +
          line('Fair limit', d.fair + cost2(d.fair), true) + line('Patient (middle)', d.patient + cost2(d.patient)) + line('Fill now (' + (side === 'buy' ? 'ask' : 'bid') + ')', d.now + cost2(d.now)) +
          (d.band ? line('20% overnight band', d.band[0] + ' - ' + d.band[1]) : '') + '</div>'
          : '<p class="note">' + esc(d.note) + ' Last: ' + d.last + (d.band ? '. 20% band: ' + d.band[0] + ' - ' + d.band[1] : '') + '.</p>';
      }).catch(function (e) { o.innerHTML = '<p class="err">' + esc(e.message) + '</p>'; });
    }
    document.getElementById('lhGo').onclick = go;
  }
  function chart(cv, pts) {
    var dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = 220;
    cv.width = w * dpr; cv.height = h * dpr; var c = cv.getContext('2d'); c.scale(dpr, dpr);
    var all = []; pts.forEach(function (p) { all.push(p[1], p[2]); });
    var lo = Math.min(0, Math.min.apply(null, all)), hi = Math.max(0, Math.max.apply(null, all)), pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
    var X = function (i) { return 44 + i / (pts.length - 1) * (w - 54); }, Y = function (v) { return 10 + (hi - v) / (hi - lo) * (h - 34); };
    c.font = '10px ui-monospace,monospace'; c.fillStyle = '#7f93a8'; c.strokeStyle = 'rgba(127,147,168,.25)';
    [lo + pad, 0, hi - pad].forEach(function (v) { c.beginPath(); c.moveTo(44, Y(v)); c.lineTo(w - 10, Y(v)); c.stroke(); c.fillText((v >= 0 ? '+' : '') + v.toFixed(0) + '%', 2, Y(v) + 3); });
    c.fillText(pts[0][0], 44, h - 6); c.textAlign = 'right'; c.fillText(pts[pts.length - 1][0], w - 10, h - 6); c.textAlign = 'left';
    [[1, '#b39dff'], [2, '#ffc400']].forEach(function (k) {
      c.beginPath(); c.strokeStyle = k[1]; c.lineWidth = 1.6;
      pts.forEach(function (p, i) { if (i) c.lineTo(X(i), Y(p[k[0]])); else c.moveTo(X(i), Y(p[k[0]])); }); c.stroke();
    });
  }
  function drawND(syms) {
    if (!nd.dataset.done) {
      nd.dataset.done = 1;
      if (!ndSym) ndSym = syms.indexOf('TQQQ') >= 0 ? 'TQQQ' : syms.indexOf('QQQ') >= 0 ? 'QQQ' : syms[0] || 'SPY';
      nd.innerHTML = '<div class="row2"><input type="text" id="ndSym" size="8" autocapitalize="characters" list="lhList" value="' + esc(ndSym) + '"><button id="ndGo">Show</button><span class="msg" id="ndMsg"></span></div>' +
        '<div class="card" style="padding:8px"><canvas id="ndCv" style="width:100%;height:220px;display:block"></canvas><div id="ndSum" class="note" style="margin:4px 0 0"></div></div>' +
        '<p class="note"><b style="color:#b39dff">Purple</b> = only holding from the 4 pm close to the next 9:30 open, every night for a year. <b style="color:#ffc400">Gold</b> = only holding from the open to the close. Before trading costs: going in and out every day costs spreads and fees, which can eat most of the difference. Yahoo daily prices.</p>';
      document.getElementById('ndGo').onclick = function () { ndSym = document.getElementById('ndSym').value.trim().toUpperCase(); try { localStorage.setItem('nd_ndsym', ndSym); } catch (e) {} loadND(); };
      loadND();
    }
  }
  function loadND() {
    var m = document.getElementById('ndMsg'); m.textContent = 'Loading ' + ndSym + '…';
    ND.api('earn/nightday?sym=' + encodeURIComponent(ndSym)).then(function (d) {
      if (d.error || !d.pts || d.pts.length < 5) { m.textContent = d.error || 'Not enough daily prices for ' + ndSym + ' yet.'; return; }
      m.textContent = ''; chart(document.getElementById('ndCv'), d.pts);
      document.getElementById('ndSum').innerHTML = esc(d.sym) + ' over ' + d.n + ' days: overnight only <b>' + ND.pc(d.night, 1) + '</b> (up ' + d.night_win + '% of nights) · day only <b>' + ND.pc(d.day, 1) + '</b> (up ' + d.day_win + '% of days).';
    }).catch(function (e) { m.textContent = e.message; });
  }
  function paint(d) {
    last = d;
    if (!drawn) { drawn = true; ND.switches(sw, SW, d.settings, function (v) { return ND.post('earn/settings', { settings: v }); }); }
    [].forEach.call(sw.querySelectorAll('.pt'), function (b) { b.textContent = d.plan_time; });
    drawPlan(d.plan || {}, d.plan_time); drawEarn(d.earnings || []); drawLimit(d.symbols || []); drawStress(d.stress); drawND(d.symbols || []); drawFills(d.fills || {});
  }
  function load() { ND.api('earn').then(paint).catch(function () {}); }
  load(); setInterval(load, 60000);
})();
