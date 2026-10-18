/* Night desk sections: alert manager, journal scorecard, symbol notes, iPhone widget and Siri,
 * go-live checklist, weekly reports (jb-tools). Data: /api/plus/tools */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var al = ND.section('t-alerts', 'Alert manager (every alert on one page)');
  var sc = ND.section('t-score', 'Journal scorecard');
  var nt = ND.section('t-notes', 'Symbol notes');
  var wg = ND.section('t-widget', 'iPhone widget and "Hey Siri, how\'s the board?"');
  var gl = ND.section('t-golive', 'Go-live checklist');
  var rp = ND.section('t-reports', 'Weekly trading reports');
  function onoff(on, attrs) { return '<label class="set" style="display:inline-flex;margin:0"><input type="checkbox"' + (on ? ' checked' : '') + ' ' + attrs + '></label>'; }
  function drawAlerts(d) {
    var h = '';
    function rows(list, kind, title, help) {
      if (!list.length) return '';
      return '<div class="card" style="margin-bottom:6px"><b>' + title + '</b>' + list.map(function (a) {
        return '<div class="ev"><span class="k">' + onoff(a.on, 'data-kind="' + kind + '" data-id="' + esc(a.id) + '"' + (kind === 'combo' ? ' disabled' : '')) + '</span><span class="tx">' + esc(a.text) +
          (a.fired ? ' <span class="note">last fired ' + esc(ND.ago(a.fired)) + '</span>' : '') + '</span></div>';
      }).join('') + (help ? '<p class="note">' + help + '</p>' : '') + '</div>';
    }
    h += rows(d.price, 'price', 'Price alerts', 'Unticking a price alert removes it.');
    h += rows(d.levels, 'level', 'Chart level alerts', '');
    h += rows(d.combos, 'combo', 'Combo alerts', 'Combo alerts are added and removed on the Night desk alerts section.');
    h += (d.switches || []).map(function (m) {
      return '<div class="card" style="margin-bottom:6px"><b>' + esc(m.title) + '</b><div style="display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:4px">' + m.items.map(function (i) {
        return '<label style="font-size:12px">' + '<input type="checkbox"' + (i.on ? ' checked' : '') + ' data-kind="switch" data-mod="' + esc(m.module) + '" data-key="' + esc(i.key) + '"> ' + esc(i.label) + '</label>';
      }).join('') + '</div></div>';
    }).join('');
    h += '<div class="row2"><button id="tTest">Send a test push</button><span class="msg" id="tMsg"></span></div>';
    h += '<p class="note">Every alert and alert switch from every tab, so you can see what can wake you up and turn things off in one place. Changes save as soon as you tick or untick. ' +
      'Urgent pushes (SELL ALL, loss limit, the Mac going quiet) now use top priority, so they can break through an iPhone Focus if you allow ntfy in that Focus (Settings > Focus > your Focus > Apps).</p>';
    if (d.recent && d.recent.length) h += '<div class="card"><b>Last alerts</b>' + d.recent.slice(0, 10).map(function (r) { return '<div class="ev"><span class="tm">' + esc(ND.time(r.at)) + '</span><span class="tx">' + esc(r.text) + '</span></div>'; }).join('') + '</div>';
    al.innerHTML = h;
    al.querySelector('#tTest').onclick = function () { var m = al.querySelector('#tMsg'); m.textContent = 'Sending…'; ND.post('tools/alerts', { test: true }).then(function (r) { m.textContent = r.text; }).catch(function (e) { m.textContent = e.message; }); };
    [].forEach.call(al.querySelectorAll('input[data-kind]'), function (i) {
      i.onchange = function () {
        var body = { kind: i.dataset.kind, on: i.checked, id: i.dataset.id, module: i.dataset.mod, key: i.dataset.key };
        ND.post('tools/alerts', body).then(drawAlerts).catch(function (e) { al.querySelector('#tMsg').textContent = e.message; });
      };
    });
  }
  function drawScore(d) {
    if (!d.n) { sc.innerHTML = '<p class="note">No finished trades in the journal yet.</p>'; return; }
    function grp(o, title) {
      var ks = Object.keys(o);
      return '<div class="card tbl"><table><tr><th>' + title + '</th><th>Trades</th><th>Won</th><th>Total</th></tr>' + ks.map(function (k) {
        var x = o[k]; return '<tr><td>' + esc(k) + '</td><td>' + x.n + '</td><td>' + Math.round(x.wins / x.n * 100) + '%</td><td class="' + (x.pnl >= 0 ? 'up' : 'dn') + '">' + ND.money(x.pnl) + '</td></tr>';
      }).join('') + '</table></div>';
    }
    sc.innerHTML = '<div class="card">' + d.n + ' trades · won ' + d.win_rate + '% · total <b class="' + (d.total >= 0 ? 'up' : 'dn') + '">' + ND.money(d.total) + '</b><br>' +
      'Profit factor <b>' + (d.profit_factor == null ? '–' : d.profit_factor) + '</b> · average win ' + ND.money(d.avg_win) + ' vs average loss ' + ND.money(d.avg_loss) +
      ' · expectancy <b>' + ND.money(d.expectancy) + '</b> a trade · longest losing streak ' + d.losing_streak + '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px">' + grp(d.weekday, 'Weekday') + grp(d.symbol, 'ETF / stock') + '</div>' +
      '<div class="card tbl"><table><tr><th>Closed</th><th>Trade</th><th>Result</th><th>Notes the board saved</th></tr>' + (d.recent || []).map(function (t) {
        return '<tr><td>' + esc(ND.time(t.exit_t)) + '</td><td>' + esc(t.sym) + ' ' + t.qty + ' · ' + t.entry + ' → ' + t.exit + '<br><span class="note">' + esc(t.entry_session) + ' → ' + esc(t.exit_session) + '</span></td><td class="' + (t.pnl >= 0 ? 'up' : 'dn') + '">' + ND.money(t.pnl) + '</td><td class="note">' +
          esc([t.entry_note && 'In: ' + t.entry_note, t.exit_note && 'Out: ' + t.exit_note].filter(Boolean).join(' · ')) + '</td></tr>';
      }).join('') + '</table></div>' +
      '<p class="note">Profit factor: dollars won ÷ dollars lost (above 1.5 is good). Expectancy: what an average trade makes. The notes are written by the board at each fill (session, VIX, futures, how far from the last price) so you can see what the market was doing.</p>';
  }
  function drawNotes(n) {
    var ks = Object.keys(n || {}).sort();
    nt.innerHTML = (ks.length ? '<div class="card">' + ks.map(function (k) { return '<div class="ev"><span class="k">' + esc(k) + '</span><span class="tx" style="white-space:pre-wrap">' + esc(n[k].text) + ' <span class="note">' + esc(ND.ago(n[k].at)) + '</span></span></div>'; }).join('') + '</div>' : '') +
      '<div class="card"><input type="text" id="tnSym" placeholder="Symbol" style="width:90px"> <textarea id="tnTx" rows="2" style="width:100%;margin-top:4px" placeholder="Your note (leave empty and save to remove)"></textarea>' +
      '<div class="row2"><button id="tnSave">Save note</button><span class="msg" id="tnMsg"></span></div></div><p class="note">A note shows as a NOTE chip on the tile, and on the order ticket for that symbol. Handy for "earnings Thursday", "don\'t hold over the weekend" or why you are in a trade.</p>';
    nt.querySelector('#tnSave').onclick = function () {
      ND.post('tools/notes', { sym: nt.querySelector('#tnSym').value, text: nt.querySelector('#tnTx').value }).then(function (r) { drawNotes(r.notes); }).catch(function (e) { nt.querySelector('#tnMsg').textContent = e.message; });
    };
  }
  function drawWidget() {
    wg.innerHTML = '<p class="note">A small box on your iPhone home screen with futures, today\'s profit or loss, positions and the board\'s health, plus a Siri shortcut that reads the same summary out loud, and a Lock Screen version (futures and health). Read-only: it can never trade.</p><div class="row2"><button id="twShow">Show my setup steps</button></div><div id="twBox"></div>';
    wg.querySelector('#twShow').onclick = function () {
      ND.api('tools/widget/setup').then(function (d) {
        wg.querySelector('#twBox').innerHTML = '<div class="card"><b>Widget (free Scriptable app)</b><ol style="margin:6px 0 6px 18px;padding:0;font-size:12px"><li>Install Scriptable from the App Store.</li><li>Tap Copy script below, open Scriptable, tap +, paste, name it "Board".</li><li>Long-press the home screen, tap Edit > Add Widget, pick Scriptable (small), then tap the widget and choose the script "Board".</li></ol>' +
          '<button id="twCopy">Copy script</button> <span class="msg" id="twMsg"></span><textarea readonly rows="6" style="width:100%;margin-top:6px;font-size:10px">' + esc(d.script) + '</textarea></div>' +
          '<div class="card"><b>Siri</b><ol style="margin:6px 0 0 18px;padding:0;font-size:12px">' + d.siri.map(function (s) { return '<li>' + esc(s) + '</li>'; }).join('') + '</ol></div>' + (d.lock ? '<div class="card"><b>Lock Screen</b><p class="note" style="margin:6px 0 0">' + esc(d.lock) + '</p></div>' : '') + '<p class="note">' + esc(d.note) + '</p>';
        wg.querySelector('#twCopy').onclick = function () { navigator.clipboard.writeText(d.script).then(function () { wg.querySelector('#twMsg').textContent = 'Copied.'; }); };
      }).catch(function (e) { wg.querySelector('#twBox').textContent = e.message; });
    };
  }
  function drawGolive() {
    gl.innerHTML = '<div class="row2"><button data-b="ibkr">Check IBKR</button> <button data-b="webull">Check Webull</button></div><div id="tgBox"></div><p class="note">What should be true before an algo trades real money: enough paper days, a decent win rate, a profit on paper, no day worse than your loss limit, SELL ALL tested, the daily loss limit on with algo pause, and the algo checking in. ./jb live runs this same list first and asks you to type LIVE.</p>';
    [].forEach.call(gl.querySelectorAll('button[data-b]'), function (b) {
      b.onclick = function () {
        ND.api('tools/golive?broker=' + b.dataset.b).then(function (c) {
          gl.querySelector('#tgBox').innerHTML = '<div class="card"><b>' + esc(c.broker.toUpperCase()) + ': ' + c.passed + ' of ' + c.total + ' passed</b>' + c.items.map(function (i) {
            return '<div class="ev"><span class="k" style="color:' + (i.ok ? '#5dff8f' : '#ffb648') + '">' + (i.ok ? '✓' : '!') + '</span><span class="tx"><b>' + esc(i.name) + '</b> ' + esc(i.detail) + (i.fix ? '<br><span class="note">' + esc(i.fix) + '</span>' : '') + '</span></div>';
          }).join('') + '</div>';
        });
      };
    });
  }
  function drawReports(d) {
    rp.innerHTML = '<div class="card">' + ((d.reports || []).length ? d.reports.map(function (r) { return '<a href="/reports/' + esc(r) + '" target="_blank">' + esc(r) + '</a>'; }).join('<br>') : 'None yet.') + '</div>' +
      '<div class="row2"><button id="trMake">Make this week\'s report now</button><span class="msg" id="trMsg"></span></div><p class="note">Every Friday at 8:30 pm New York the board makes a PDF of the week (results by day, session and ETF, every trade, the paper algos) and pushes a link to your phone.</p>';
    rp.querySelector('#trMake').onclick = function () {
      var m = rp.querySelector('#trMsg'); m.textContent = 'Making it…';
      ND.post('tools/report', {}).then(function () { m.textContent = 'Done.'; ND.api('tools').then(drawReports); }).catch(function (e) { m.textContent = e.message; });
    };
  }
  drawWidget(); drawGolive();
  function load() {
    ND.api('tools/alerts').then(drawAlerts).catch(function () {});
    ND.api('tools/scorecard').then(drawScore).catch(function () {});
    ND.api('tools/notes').then(function (d) { if (!nt.querySelector('#tnTx') || !nt.querySelector('#tnTx').value) drawNotes(d.notes); }).catch(function () {});
    ND.api('tools').then(drawReports).catch(function () {});
  }
  load(); setInterval(function () { if (!document.hidden && !al.contains(document.activeElement)) load(); }, 120000);
})();
