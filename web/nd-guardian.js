/* Night desk section: the Mac guardian (jb-guardian). Data: /api/plus/guardian */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var chk = ND.section('g-checks', 'Mac guardian checks');
  var bk = ND.section('g-backup', 'Nightly backups');
  var net = ND.section('g-net', 'Internet drops');
  var sw = ND.section('g-sw', 'Guardian switches');
  var COL = { ok: '#5dff8f', warn: '#ffb648', bad: '#ff8a80' };
  function load() {
    ND.api('guardian').then(function (d) {
      chk.innerHTML = '<div class="card">' + (d.checks || []).map(function (i) {
        return '<div class="ev"><span class="k" style="color:' + (COL[i.state] || '#9fb3c8') + '">●</span><span class="tx"><b>' + esc(i.name) + '</b> ' + esc(i.detail) +
          (i.fix && i.state !== 'ok' ? '<br><span class="note">' + esc(i.fix) + '</span>' : '') + '</span></div>';
      }).join('') + '</div><p class="note">Things that would quietly stop the board at night: the Mac not restarting after a power cut, low memory, a wrong clock, macOS installing updates by itself, ' +
        'a service crashing over and over (held back for an hour and pushed to your phone), an algo that stopped checking in, and the last backup. The same list is in System check.</p>';
      var s = d.state || {};
      bk.innerHTML = '<div class="card">Last backup: ' + (s.backup_at ? esc(ND.time(s.backup_at)) + ' · ' + esc(s.backup_file || '') : 'none yet') + (s.backup_note ? '<br><span class="note">' + esc(s.backup_note) + '</span>' : '') +
        '<br><span class="note">Kept: ' + esc((d.backups || []).join(', ') || 'none') + '</span></div><div class="row2"><button id="gBk">Back up now</button><span class="msg" id="gBkMsg"></span></div>' +
        '<p class="note">Every night at 2:30 am New York the board saves settings.env (your keys, so keep backups private), the data folder (journal, notes, alerts) and the algo records (not the program files) to ~/josephs-board/backups, keeping the last 7. Tick iCloud below to also copy each one to iCloud Drive.</p>';
      document.getElementById('gBk').onclick = function () {
        var m = document.getElementById('gBkMsg'); m.textContent = 'Saving…';
        ND.post('guardian/backup', {}).then(function (r) { m.textContent = r.text; }).catch(function (e) { m.textContent = e.message; });
      };
      var o = d.outages || [];
      net.innerHTML = (d.net_up ? '' : '<p class="warn"><b>The internet is down right now</b> (since ' + esc(ND.time(d.net_down_since)) + ').</p>') +
        (o.length ? '<div class="card">' + o.slice(0, 15).map(function (x) { return '<div class="ev"><span class="tm">' + esc(ND.time(x.start)) + '</span><span class="tx">' + x.mins + ' min without internet</span></div>'; }).join('') + '</div>' : '<p class="note">No drops recorded.</p>') +
        '<p class="note">The Mac checks the internet every 30 seconds. A drop of 2 minutes or more is pushed to your phone when it comes back, so you know prices and orders may have missed that stretch.</p>';
      if (!sw.dataset.drawn) {
        sw.dataset.drawn = 1;
        ND.switches(sw, [['heartbeat_push', 'Algo stopped push', 'An algo that has not checked in for 3 minutes.'],
          ['early_loss', 'Early loss warnings', 'Pushes at 50% and 75% of your daily loss limit.'],
          ['confirm_sellall', 'Confirm SELL ALL', 'After SELL ALL, check again after 60 seconds and sell what is left (up to 3 times).'],
          ['backup', 'Nightly backup', '2:30 am New York, last 7 kept.'],
          ['icloud', 'Copy backups to iCloud Drive', 'Only if iCloud Drive is on for this Mac.'],
          ['sunday_test', 'Sunday self-test', 'Sunday 5 pm New York: runs System check before futures open and pushes the result.'],
          ['saturday_refresh', 'Saturday refresh', 'Saturday 7 am New York: restarts the board services one at a time to clear memory.']], d.settings || {}, function (v) { return ND.post('guardian/settings', v); });
      }
    }).catch(function () {});
  }
  load(); setInterval(function () { if (!document.hidden) load(); }, 60000);
})();
