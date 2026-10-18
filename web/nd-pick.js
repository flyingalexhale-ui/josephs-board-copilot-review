/* Night desk section: ETF pick of the hour (jb-mdata). Free, fixed rules, graded an hour later.
 * Data: /api/plus/pick */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var el = ND.section('pick', 'ETF pick of the hour');
  var hasClaude = false;
  ND.api('').then(function (d) { hasClaude = (d.modules || []).indexOf('claude') >= 0; }).catch(function () {});
  function askLink(p) {
    var q = 'The board\'s rules picked ' + p.sym + ' (' + p.group + ' 3x ' + (p.side === 'bull' ? 'bull' : 'bear / inverse') + ') as the ETF pick of the hour, score ' + p.score + '/100. Reasons: ' + p.why.join('; ') +
      '. What could go wrong with this trade in the next hour or overnight, and what would you watch?';
    var url = hasClaude ? 'claude.html?q=' + encodeURIComponent(q) : 'https://claude.ai/new?q=' + encodeURIComponent(q);
    return '<a class="chip c-p" href="' + esc(url) + '" target="_blank" rel="noopener">Ask Claude about this ›</a>';
  }
  function card(r, title) {
    var p = r.pick;
    if (!p) return '<div class="card"><b>' + esc(title) + ': no clear pick, stay flat.</b><p class="note">Nothing scored ' + 35 + ' or more out of 100 at ' + esc(ND.time(r.at)) + ' (' + r.checked + ' funds checked). ' +
      (r.runners_up && r.runners_up.length ? 'Closest: ' + r.runners_up.map(function (c) { return esc(c.sym) + ' ' + c.score; }).join(', ') + '.' : '') + '</p></div>';
    return '<div class="card"><div class="row2" style="align-items:center"><span style="font-size:26px;font-weight:700">' + esc(p.sym) + '</span>' +
      '<span class="chip ' + (p.side === 'bull' ? 'c-g' : 'c-r') + '">' + (p.side === 'bull' ? '3x BULL' : '3x BEAR') + '</span>' +
      '<span class="chip ' + (p.score >= 70 ? 'c-g' : p.score >= 50 ? 'c-a' : 'c-d') + '">score ' + p.score + ' / 100</span>' +
      '<span class="note">' + esc(p.group) + (p.price ? ' · $' + p.price.toFixed(2) + ' when picked' : ' · no live price (put it on your IBKR board for overnight prices)') + '</span></div>' +
      '<ul style="margin:6px 0 6px 18px;padding:0">' + p.why.map(function (w) { return '<li>' + esc(w) + '</li>'; }).join('') + '</ul>' +
      '<div class="row2">' + askLink(p) + '<span class="note">' + esc(title) + ', ' + esc(ND.time(r.at)) + '</span></div>' +
      (r.runners_up && r.runners_up.length ? '<p class="note">Runners-up: ' + r.runners_up.map(function (c) { return esc(c.sym) + ' ' + c.score; }).join(', ') + '</p>' : '') + '</div>';
  }
  function draw(d) {
    var rec = d.record || {}, h = d.history || [];
    var top = d.current ? card(d.current, 'This hour\'s pick') :
      (d.preview ? card(d.preview, 'Right now (the next pick is at ' + ND.time(d.next_at) + ')') : '');
    if (!d.open) top = '<p class="note">Picks run Sunday 8 pm to Friday 8 pm New York time. The next one is Sunday at 8 pm.</p>' + top;
    var recHtml = rec.graded ? '<div class="card"><b>Track record</b> <span class="note">(' + rec.graded + ' picks graded)</span><br>' +
      'Up an hour later: <b>' + rec.win_pct + '%</b> of picks · last 20: <b>' + rec.last20_win_pct + '%</b> · average move <b>' + ND.pc(rec.avg) + '</b> · best ' + ND.pc(rec.best) + ' · worst ' + ND.pc(rec.worst) + '</div>'
      : '<p class="note">No graded picks yet: each pick is graded one hour after it is made.</p>';
    var rows = h.slice(0, 12).map(function (r) {
      var p = r.pick;
      return '<tr><td style="text-align:left">' + esc(ND.time(r.at)) + '</td><td style="text-align:left">' + (p ? '<b>' + esc(p.sym) + '</b> <span class="note">' + esc(p.side) + '</span>' : '<span class="note">flat</span>') + '</td>' +
        '<td>' + (p ? p.score : '–') + '</td><td>' + (p && p.price ? '$' + p.price.toFixed(2) : '–') + '</td><td>' + (!p ? '–' : r.result === undefined ? '<span class="note">waiting</span>' : r.result === null ? '<span class="note">no price</span>' : ND.pc(r.result)) + '</td></tr>';
    }).join('');
    el.innerHTML = top + recHtml +
      (rows ? '<div class="card tbl"><table><tr><th style="text-align:left">When</th><th style="text-align:left">Pick</th><th>Score</th><th>Price</th><th title="How much the fund moved in the hour after it was picked">1 hour later</th></tr>' + rows + '</table></div>' : '') +
      '<label class="set"><input type="checkbox" id="pkPush"' + (d.settings && d.settings.push ? ' checked' : '') + '><span class="t">Push each pick to my phone<small>One push per hour while picks run (off by default).</small></span></label>' +
      '<p class="note">How it picks, every hour on the hour, from the 3x pairs in the twins list: the index move since the 4 pm close (up to 40 points), the last hour going the same way (30), ' +
      'the World tab\'s market mood agreeing (20, stock indexes only) and heavy volume in the fund (10). Under 35 means nothing is clear. Fixed rules, not AI and not advice: they know nothing about news, and 3x funds can move fast against you. ' +
      'Tap "Ask Claude about this" for a second opinion in the Claude app (free; it opens the app with the question filled in).</p>';
    document.getElementById('pkPush').onchange = function () { ND.post('pick/settings', { push: this.checked }); };
  }
  function load() { ND.api('pick').then(draw).catch(function (e) { el.innerHTML = '<p class="err">Could not load the pick: ' + esc(e.message) + '</p>'; }); }
  load();
  setInterval(load, 60000);
})();
