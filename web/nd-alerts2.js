/* Night desk section: halts, before-bed card, combo alerts, heartbeat, 8:30 data, Fed / CPI odds,
 * tap-to-open (jb-alerts2). Data: /api/plus/alerts2 */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var sw = ND.section('a2-switches', 'More phone alerts');
  var bed = ND.section('a2-bed', 'Before-bed card');
  var hal = ND.section('a2-halts', 'Trading halts today');
  var cmb = ND.section('a2-combo', 'Combo alerts');
  var odd = ND.section('a2-odds', 'Fed, CPI and jobs odds (Kalshi)');
  var rx = ND.section('a2-830', 'Data reactions (8:30 am and other big releases)');
  var hb = ND.section('a2-heart', 'Mac heartbeat');
  var lg = ND.section('a2-log', 'Recent alerts from this section');
  var SW = [
    ['halts', 'Trading halts', 'A push when a stock on your boards or that you hold is halted (news pending, volatility pause, SEC...) and when it resumes.'],
    ['halts_all', '…for every US stock, not just mine', 'Very noisy on busy days. Off = only stocks on your boards, scanner, algos and holdings.'],
    ['bedtime', 'Before-bed card', 'Each evening before a trading day (time: JB_BEDTIME in settings.env, now <b class="bt"></b>): what you hold and what could hit it before the open.'],
    ['combos', 'Combo alerts', 'The two-condition alerts you set up below.'],
    ['data_830', 'Data reactions', 'Five minutes after a big US number comes out (jobs, CPI, Fed...): how S&P / Nasdaq / Dow futures moved.'],
    ['tap', 'Tap a notification to open it', 'A stock alert opens that stock\'s chart on the IBKR board; anything else opens the Night desk.']
  ];
  var drawn = false, kinds = {};
  function condRow(i) {
    var opts = Object.keys(kinds).map(function (k) { return '<option value="' + k + '">' + esc(kinds[k]) + '</option>'; }).join('');
    return '<div class="row2" data-c="' + i + '"><b>' + (i ? 'AND' : 'IF') + '</b><input type="text" class="cs" placeholder="' + (i ? 'ES=F' : 'NVDA') + '" size="8" autocapitalize="characters">' +
      '<select class="ck">' + opts + '</select><input type="number" class="cv" step="any" placeholder="value" style="width:90px"></div>';
  }
  function setVis(row) { var k = row.querySelector('.ck').value; row.querySelector('.cv').style.display = /^(above|below|up_pct|down_pct|vol_x)$/.test(k) ? '' : 'none'; }
  function drawCombo(d) {
    kinds = d.kinds || {};
    cmb.innerHTML = '<p class="note">Both conditions must be true at the same time. Each alert fires at most once per stock per session (after-hours, overnight, pre-market, regular). Use ES=F / NQ=F for the market.</p>' +
      '<div class="card" style="padding:8px 12px">' + condRow(0) + condRow(1) + '<div class="row2"><button id="cAdd">Add combo alert</button><span class="msg" id="cMsg"></span></div></div><div id="cList"></div>';
    [].forEach.call(cmb.querySelectorAll('[data-c]'), function (r) { var s = r.querySelector('.ck'); s.onchange = function () { setVis(r); }; setVis(r); });
    cmb.querySelector('[data-c="1"] .ck').value = 'up_pct'; setVis(cmb.querySelector('[data-c="1"]'));
    document.getElementById('cAdd').onclick = function () {
      var conds = [].map.call(cmb.querySelectorAll('[data-c]'), function (r) { return { sym: r.querySelector('.cs').value.trim().toUpperCase(), kind: r.querySelector('.ck').value, value: r.querySelector('.cv').value }; });
      var m = document.getElementById('cMsg'); m.textContent = 'Saving…';
      ND.post('alerts2/combo', { conds: conds }).then(function (d2) { m.textContent = 'Added.'; paint(d2); }).catch(function (e) { m.textContent = e.message; });
    };
  }
  function paint(d) {
    if (!drawn) { drawn = true; ND.switches(sw, SW, d.settings, function (v) { return ND.post('alerts2/settings', { settings: v }); }); drawCombo(d); }
    [].forEach.call(sw.querySelectorAll('.bt'), function (b) { b.textContent = d.bedtime; });
    var notes = [];
    if (!d.topic_set) notes.push('<span class="err">Phone alerts aren\'t set up (no JB_NTFY_TOPIC in settings.env).</span>');
    notes.push(d.phone_url ? 'Tap links open <b>' + esc(d.phone_url) + '</b> (your Mac over Tailscale). <button class="ghost" id="tapTest">Send a test</button>' :
      '<span class="warn">Tap links are off: the Mac\'s Tailscale address wasn\'t found. Add JB_PHONE_URL=http://100.x.x.x:8090 to settings.env.</span>');
    var nEl = document.getElementById('a2note');
    if (!nEl) { nEl = document.createElement('p'); nEl.className = 'note'; nEl.id = 'a2note'; sw.appendChild(nEl); }
    nEl.innerHTML = notes.join('<br>');
    var tb = document.getElementById('tapTest'); if (tb) tb.onclick = function () { ND.post('alerts2/test-tap', {}).then(function () { tb.textContent = 'Sent'; }).catch(function (e) { tb.textContent = e.message; }); };

    var c = d.card;
    bed.innerHTML = '<div class="card" style="padding:10px 12px;white-space:pre-line;line-height:1.55">' + (c ? '<span class="chip ' + (c.risky ? 'c-a' : 'c-g') + '">' + (c.risky ? 'HEADS-UP' : 'QUIET') + '</span> <span class="note">made ' + esc(ND.time(c.at)) + ' for ' + esc(c.for) + '</span>\n' + esc(c.text) : 'Not made yet tonight.') +
      '</div><div class="row2"><button class="ghost" id="bedNow">Make it now</button></div>';
    document.getElementById('bedNow').onclick = function () { ND.post('alerts2/card', {}).then(paint).catch(function () {}); };

    var hs = d.halts || [];
    hal.innerHTML = (d.halts_err ? '<p class="note err">Could not read the halts feed: ' + esc(d.halts_err) + '</p>' : '') +
      (hs.length ? '<div class="card tbl"><table><tr><th>Stock</th><th>Halted (ET)</th><th>Why</th><th>Resumes</th></tr>' + hs.slice(0, 60).map(function (h) {
        return '<tr><td>' + esc(h.sym) + (h.held ? ' <span class="chip c-r">HELD</span>' : h.mine ? ' <span class="chip c-b">ON BOARD</span>' : '') + '</td><td>' + esc(ND.time(h.halted)) +
          '</td><td style="text-align:left;white-space:normal">' + esc(h.why) + ' <span class="note">' + esc(h.code) + '</span></td><td>' + (h.resumes ? esc(ND.time(h.resumes)) : '<span class="warn">not yet</span>') + '</td></tr>';
      }).join('') + '</table></div>' : '<p class="note">No halts in the last day' + (d.halts_at ? ' (checked ' + ND.ago(d.halts_at) + ')' : '') + '.</p>') +
      '<p class="note">Source: Nasdaq Trader\'s public halts feed, which lists halts on every US exchange. Checked every minute.</p>';

    var list = document.getElementById('cList');
    if (list) list.innerHTML = (d.combos || []).length ? '<div class="card">' + d.combos.map(function (a) {
      return '<div class="ev"><span class="tx">' + esc(a.text) + '</span><button class="ghost" data-rm="' + esc(a.id) + '">Remove</button></div>'; }).join('') + '</div>' : '<p class="note">No combo alerts yet.</p>';
    [].forEach.call(cmb.querySelectorAll('[data-rm]'), function (b) { b.onclick = function () { ND.post('alerts2/combo/remove', { id: b.dataset.rm }).then(paint); }; });

    var o = d.odds || {}, keys = ['fed', 'cpi', 'jobs'];
    odd.innerHTML = keys.filter(function (k) { return o[k]; }).map(function (k) {
      var e = o[k];
      return '<div class="card" style="padding:8px 12px;margin-bottom:8px"><b>' + esc(e.title) + '</b> <span class="note">' + esc(e.sub) + '</span>' +
        '<div class="tbl"><table>' + e.rows.map(function (r) { return '<tr><td>' + esc(r.label) + '</td><td>' + r.p + '%</td><td style="width:45%"><div class="bar"><i style="width:' + r.p + '%"></i></div></td></tr>'; }).join('') + '</table></div></div>';
    }).join('') + (keys.some(function (k) { return o[k]; }) ? '' : '<p class="note">' + (d.odds_err ? '<span class="err">' + esc(d.odds_err) + '</span>' : 'Loading…') + '</p>') +
      '<p class="note">The % is what traders on Kalshi\'s public prediction market are paying: roughly the chance they give each outcome. ' +
      'For CPI and jobs, each row is "above this number". Read only from the public site; it never touches any account. Updated every 30 min' + (d.odds_at ? ' (last ' + ND.ago(d.odds_at) + ')' : '') + '.</p>';

    var rs = d.reactions || [];
    rx.innerHTML = rs.length ? '<div class="card tbl"><table><tr><th>Release</th><th>Actual / expected</th><th>S&amp;P 1 / 5 min</th><th>Nasdaq 1 / 5 min</th></tr>' + rs.map(function (r) {
      var m = r.moves || {}, f = function (n) { return m[n] ? ND.pc(m[n].m1) + ' / ' + ND.pc(m[n].m5) : '–'; };
      return '<tr><td style="white-space:normal;text-align:left">' + esc(ND.time(r.t)) + ' ' + esc(r.title) + '</td><td>' + esc(r.actual || '?') + ' / ' + esc(r.forecast || '?') + '</td><td>' + f('S&P') + '</td><td>' + f('Nasdaq') + '</td></tr>';
    }).join('') + '</table></div>' : '<p class="note">None yet. After each big US release (from the World tab\'s calendar), the futures reaction is filled in here 5 minutes later.</p>';

    var h = d.heartbeat || {};
    hb.innerHTML = '<div class="card" style="padding:10px 12px;line-height:1.55">' + (d.heartbeat_set ?
      (h.ok ? '<span class="chip c-g">OK</span> last ping ' + esc(ND.ago(h.at)) + '.' : h.ok === false ? '<span class="chip c-r">PING FAILED</span> ' + esc(h.err) : 'Waiting for the first ping…') +
      ' <button class="ghost" id="hbNow">Ping now</button>' :
      '<span class="chip c-a">NOT SET UP</span> Paste your free healthchecks.io ping link here and press Save.') +
      '<div class="row2" style="margin:8px 0 0"><input type="text" id="hbUrl" placeholder="https://hc-ping.com/…" style="flex:1;min-width:220px" autocapitalize="off" autocorrect="off" spellcheck="false">' +
      '<button id="hbSave">' + (d.heartbeat_set ? 'Change link' : 'Save') + '</button><span class="msg" id="hbMsg"></span></div>' +
      '<div class="note" style="margin:6px 0 0">The Mac pings healthchecks.io every 5 minutes. If the pings stop (power cut, internet down, Mac asleep or crashed), healthchecks.io sends a push to your phone through ntfy.</div></div>';
    var hbb = document.getElementById('hbNow'); if (hbb) hbb.onclick = function () { ND.post('alerts2/heartbeat', {}).then(paint); };
    document.getElementById('hbSave').onclick = function () {      // jb-ndtabs: saves the link into settings.env and pings once
      var m = document.getElementById('hbMsg'), u = document.getElementById('hbUrl').value.trim();
      if (!u) { m.textContent = 'Paste the link first.'; return; }
      m.textContent = 'Saving…';
      ND.post('alerts2/heartbeat', { url: u }).then(function () { return ND.api('alerts2'); }).then(paint).catch(function (e) { m.textContent = e.message; });
    };
    lg.innerHTML = ND.log(d.log);
  }
  function load() { ND.api('alerts2').then(paint).catch(function () {}); }
  load(); setInterval(load, 30000);
})();
