/* Night desk section: Web watch (jb-webwatch). Press releases, Reddit spikes, SEC filings,
 * Wikipedia / Google Trends attention, unexplained moves. Data: /api/plus/webwatch */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var ux = ND.section('ww-unexp', 'Unexplained moves');
  var feed = ND.section('ww-feed', 'Web watch');
  var src = ND.section('ww-src', 'Web watch sources and alerts');
  var filt = localStorage.getItem('ww_f') || 'all';
  var KIND = { wire: ['PRESS', 'c-b'], reddit: ['REDDIT', 'c-a'], sec: ['SEC', 'c-p'], wiki: ['WIKIPEDIA', 'c-d'], trends: ['TRENDING', 'c-d'], unexplained: ['NO NEWS', 'c-r'] };
  var FILTERS = [['all', 'All'], ['wire', 'Press'], ['reddit', 'Reddit'], ['sec', 'SEC'], ['attn', 'Attention']];
  var SW = [
    ['unexplained', 'Unexplained moves', 'A push when a stock of yours moves 3%+ since the 4 pm close and nothing explains it.'],
    ['wires', 'Press releases', 'A push the moment one of your companies puts out a press release.'],
    ['sec', 'SEC filings', 'A push for new filings by your companies (offerings, big holders, late reports ...) or other companies\' filings that mention them.'],
    ['reddit', 'Reddit spikes', 'A push when a ticker of yours is suddenly talked about far more than usual.'],
    ['attention', 'Wikipedia / Google Trends spikes', 'A push when the public suddenly pays attention to one of your companies.']
  ];
  function row(i) {
    var k = KIND[i.kind] || ['WEB', 'c-d'];
    var t = i.url ? '<a href="' + esc(i.url) + '" target="_blank" rel="noopener">' + esc(i.title) + '</a>' : esc(i.title);
    var posts = (i.posts || []).map(function (p) { return '<br><span class="note">r/' + esc(p.sub) + ': <a href="' + esc(p.url) + '" target="_blank" rel="noopener">' + esc(p.title) + '</a></span>'; }).join('');
    return '<div class="ev"><span class="tm">' + esc(ND.time(i.at)) + '</span><span class="k"><b>' + esc(i.sym) + '</b>' + (i.held ? ' <span class="chip c-r">HELD</span>' : '') +
      ' <span class="chip ' + k[1] + '">' + k[0] + '</span></span><span class="tx">' + t + ' <span class="note">' + esc(i.src) + '</span>' + posts + '</span></div>';
  }
  function draw(d) {
    var u = d.unexplained || [];
    ux.innerHTML = (u.length ? u.map(function (x) {
      return '<div class="card" style="padding:8px 12px;margin-bottom:6px"><b>' + esc(x.sym) + '</b> ' + ND.pc(x.move, 1) + ' <span class="note">$' + Number(x.last).toFixed(2) + ' · no news found anywhere</span></div>';
    }).join('') : '<p class="note">Nothing right now: every stock of yours moving 3%+ has a reason the board can see.</p>') +
      '<p class="note">A stock of yours moving 3% or more since the 4 pm close, with no headline, filing, analyst change, earnings, press release, Reddit or attention spike found anywhere. ' +
      'These are the moves where someone may know something the news hasn\'t caught yet, so be extra careful: a move with no reason can reverse just as fast.</p>';
    var items = (d.items || []).filter(function (i) { return i.kind !== 'unexplained' && (filt === 'all' || (filt === 'attn' ? i.kind === 'wiki' || i.kind === 'trends' : i.kind === filt)); });
    feed.innerHTML = '<div class="row2">' + FILTERS.map(function (f) { return '<button class="' + (filt === f[0] ? '' : 'ghost') + '" data-f="' + f[0] + '">' + f[1] + '</button>'; }).join('') + '</div>' +
      (items.length ? '<div class="card">' + items.slice(0, 60).map(row).join('') + '</div>' : '<p class="note">Nothing found for your stocks in the last 2 days' + (d.watching ? '' : ' (no stocks on your boards yet)') + '.</p>') +
      '<p class="note">Watching ' + (d.watching || 0) + ' symbols from your boards and holdings. Press releases often land here minutes before the news sites. Reddit spikes and trending searches are crowd attention, not facts. ' +
      'SEC "mentions" are other companies\' filings that name yours (a deal or a partner). Most useful for stocks; 3x ETFs rarely have their own news.</p>';
    [].forEach.call(feed.querySelectorAll('[data-f]'), function (b) { b.onclick = function () { filt = b.dataset.f; try { localStorage.setItem('ww_f', filt); } catch (e) {} draw(d); }; });
    var s = d.sources || {}, ev = d.every || {};
    var names = ['GlobeNewswire', 'PR Newswire', 'Business Wire', 'Reddit', 'SEC filings', 'SEC full-text search', 'Wikipedia', 'Google Trends', 'Unexplained moves'];
    var how = { 'GlobeNewswire': ev.wires, 'PR Newswire': ev.wires, 'Business Wire': ev.wires, 'Reddit': ev.reddit, 'SEC filings': ev.sec, 'SEC full-text search': ev.sec, 'Wikipedia': ev.wiki, 'Google Trends': ev.trends, 'Unexplained moves': ev.unexplained };
    if (!document.getElementById('wwSw')) { src.innerHTML = '<div id="wwTbl"></div><div id="wwSw"></div>'; ND.switches(document.getElementById('wwSw'), SW, d.settings || {}, function (v) { return ND.post('webwatch/settings', v); }); }
    document.getElementById('wwTbl').innerHTML = '<div class="card tbl"><table><tr><th style="text-align:left">Source</th><th style="text-align:left">Last check</th></tr>' + names.map(function (n) {
      var x = s[n];
      return '<tr><td style="text-align:left">' + esc(n) + '<br><span class="note">every ' + Math.round((how[n] || 0) / 60) + ' min</span></td><td style="text-align:left;white-space:normal">' +
        (!x ? '<span class="note">not yet</span>' : x.ok ? '<span class="up">OK</span> <span class="note">' + esc(ND.ago(x.at)) + (x.note ? ' · ' + esc(x.note) : '') + '</span>' : '<span class="err">' + esc(x.err || 'failed') + '</span>') + '</td></tr>';
    }).join('') + '</table></div>';
  }
  function load() { ND.api('webwatch').then(draw).catch(function (e) { feed.innerHTML = '<p class="err">Could not load: ' + esc(e.message) + '</p>'; }); }
  load();
  setInterval(load, 60000);
})();
