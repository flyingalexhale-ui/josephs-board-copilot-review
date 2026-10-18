/* My trading additions (jb-tools): the journal scorecard (ideas 51, 52) above Daily totals.
 * Data: /api/plus/tools/scorecard. Hooked in by one <script> line on trading.html. */
(function () {
  'use strict';
  var at = document.getElementById('days'); if (!at) return;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function money(v) { return v == null ? '–' : (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  var h = document.createElement('h2'); h.id = 'score'; h.textContent = 'Scorecard';
  var box = document.createElement('div');
  at.parentNode.insertBefore(h, at); at.parentNode.insertBefore(box, at);
  function tile(k, v, s, cls) { return '<div class="tile"><div class="k">' + k + '</div><div class="v ' + (cls || '') + '">' + v + '</div>' + (s ? '<div class="s">' + s + '</div>' : '') + '</div>'; }
  function draw(d) {
    if (!d || !d.n) { box.innerHTML = '<p class="note">The scorecard fills in once the journal has finished trades.</p>'; return; }
    var wd = d.weekday || {};
    box.innerHTML = '<div class="tiles">' +
      tile('Profit factor', d.profit_factor == null ? '–' : d.profit_factor, 'dollars won ÷ dollars lost', d.profit_factor >= 1.5 ? 'up' : d.profit_factor != null && d.profit_factor < 1 ? 'dn' : '') +
      tile('Expectancy', money(d.expectancy), 'average result per trade', d.expectancy >= 0 ? 'up' : 'dn') +
      tile('Average win', money(d.avg_win), 'vs average loss ' + money(d.avg_loss), 'up') +
      tile('Losing streak', d.losing_streak, 'longest run of losing trades') + '</div>' +
      '<h3>By weekday</h3><div class="card"><table><tr><th>Day</th><th>Trades</th><th>Won</th><th>Total</th></tr>' + ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].filter(function (k) { return wd[k]; }).map(function (k) {
        var x = wd[k]; return '<tr><td>' + k + '</td><td>' + x.n + '</td><td>' + Math.round(x.wins / x.n * 100) + '%</td><td class="' + (x.pnl >= 0 ? 'up' : 'dn') + '">' + money(x.pnl) + '</td></tr>';
      }).join('') + '</table></div>' +
      '<h3>Last trades with the board\'s notes</h3><div class="card"><table><tr><th>Trade</th><th>Result</th><th>What the market was doing</th></tr>' + (d.recent || []).slice(0, 12).map(function (t) {
        return '<tr><td>' + esc(t.sym) + ' ' + t.qty + ' · ' + t.entry + ' → ' + t.exit + '</td><td class="' + (t.pnl >= 0 ? 'up' : 'dn') + '">' + money(t.pnl) + '</td><td style="white-space:normal;font-size:11px;color:var(--dim)">' +
          esc([t.entry_note && 'In: ' + t.entry_note, t.exit_note && 'Out: ' + t.exit_note].filter(Boolean).join(' · ') || '–') + '</td></tr>';
      }).join('') + '</table></div>' +
      '<p class="note"><b>Profit factor</b> above 1.5 is good, below 1 means losses outweigh wins. <b>Expectancy</b> is what an average trade makes after fees. The notes are written by the board at each fill (session, VIX, Nasdaq futures, distance from the last price). Weekly PDF reports are on the Night desk.</p>';
  }
  function load() { fetch('/api/plus/tools/scorecard', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(draw).catch(function () {}); }
  load(); setInterval(function () { if (!document.hidden) load(); }, 120000);
})();
