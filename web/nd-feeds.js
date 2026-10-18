/* Night desk section: top news (ranked, merged), IBKR newswire, Reddit rank, Polymarket, policy
 * posts, Bluesky buzz, buy vs sell pressure, price cross-check (jb-feeds). Data: /api/plus/feeds */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var top = ND.section('f-top', 'Top news, ranked (all feeds merged)');
  var crowd = ND.section('f-crowd', 'Crowd: Reddit rank and Bluesky buzz');
  var odds = ND.section('f-odds', 'Polymarket odds');
  var pol = ND.section('f-policy', 'Big policy posts (Truth Social, White House)');
  var tape = ND.section('f-tape', 'Buy vs sell pressure and price cross-check');
  var sw = ND.section('f-sw', 'Feed alerts');
  // only web links open (review 2026-10-11, second check): a javascript: or other odd link from a feed shows as plain text
  function link(u, t) { return u && /^https?:\/\//i.test(String(u)) ? '<a href="' + esc(u) + '" target="_blank" rel="noopener">' + esc(t) + '</a>' : esc(t); }
  function drawTop(items, ib) {
    top.innerHTML = (ib && ib.off ? '<p class="note warn">IBKR newswire: ' + esc(ib.off) + '</p>' : '') + (items.length ? '<div class="card">' + items.slice(0, 30).map(function (i) {
      return '<div class="ev"><span class="tm">' + esc(ND.time(i.ts)) + '</span><span class="k">' + esc(i.sym || '') + '</span><span class="tx">' + link(i.url, i.title) +
        ' <span class="note">' + esc(i.src || '') + (i.also ? ' · +' + i.also + ' similar' : '') + '</span></span></div>';
    }).join('') + '</div>' : '<p class="note">Loading.</p>') +
      '<p class="note">One list from the News tab, the IBKR newswire (Briefing.com analyst actions, Dow Jones), press releases and SEC filings (Web watch), the Fed and macro news and big policy posts. ' +
      'Repeats of the same story are folded into one ("+2 similar"). Ranked by: your holdings first, then your boards, the big stocks inside your ETFs, words like halt, guidance, FDA, tariff, downgrade, and how fresh it is.</p>';
  }
  function drawCrowd(d) {
    var r = d.reddit || [], b = d.bsky || {};
    crowd.innerHTML = (r.length ? '<div class="card tbl"><table><tr><th>Symbol</th><th>Reddit rank</th><th>24 h ago</th><th>Mentions</th><th>24 h ago</th>' + (b.rows ? '<th>Bluesky posts (24 h)</th>' : '') + '</tr>' + r.map(function (x) {
      var jump = x.mentions >= Math.max(2 * (x.mentions_24h || 0), (x.mentions_24h || 0) + 15);
      return '<tr><td><b>' + esc(x.sym) + '</b>' + (x.mine ? '' : ' <span class="note">top</span>') + '</td><td>#' + x.rank + '</td><td>' + (x.rank_24h ? '#' + x.rank_24h : '–') + '</td><td>' + (jump ? '<b class="hot">' + x.mentions + '</b>' : x.mentions) + '</td><td>' + (x.mentions_24h || 0) + '</td>' +
        (b.rows ? '<td>' + ((b.rows[x.sym] || {}).n != null ? b.rows[x.sym].n : '–') + '</td>' : '') + '</tr>';
    }).join('') + '</table></div>' : '<p class="note">Loading Reddit ranks.</p>') +
      '<p class="note">Reddit: ApeWisdom counts mentions on WallStreetBets, r/stocks, r/options and more (ETFs too). Purple = mentions at least doubled in 24 h. Your symbols are shown, plus the overall top 15. ' +
      (b.off ? esc(b.off) : 'Bluesky: posts with $TICKER in the last 24 hours.') + '</p>';
  }
  function drawOdds(p) {
    var ev = (p && p.events) || [];
    odds.innerHTML = ev.length ? '<div class="card">' + ev.map(function (e) {
      return '<div class="ev"><span class="tx"><b>' + link(e.slug ? 'https://polymarket.com/event/' + e.slug : '', e.title) + '</b>' + (e.end ? ' <span class="note">ends ' + esc(e.end) + '</span>' : '') + '<br>' + e.markets.map(function (m) {
        return '<span class="chip c-d">' + esc(m.q || 'Yes') + ': ' + m.p + '%' + (m.ch6h ? ' (' + (m.ch6h > 0 ? '+' : '') + m.ch6h + ' in 6 h)' : '') + '</span>';
      }).join('') + '</span></div>';
    }).join('') + '</div><p class="note">The busiest open Polymarket questions about the Fed, rates, recession, tariffs, inflation and elections. Compare with the Kalshi odds in the Alerts section: when the two disagree, or one moves fast overnight, something is happening.</p>' : '<p class="note">Loading Polymarket.</p>';
  }
  function drawPolicy(p) {
    var it = (p && p.items) || [];
    pol.innerHTML = (it.length ? '<div class="card">' + it.slice(0, 15).map(function (x) {
      return '<div class="ev"><span class="tm">' + esc(ND.time(x.ts)) + '</span><span class="k">' + esc(x.src.split(' (')[0]) + '</span><span class="tx">' + link(x.url, x.text.slice(0, 280)) +
        (x.hits && x.hits.length ? ' ' + x.hits.map(function (h) { return '<span class="chip c-a">' + esc(h) + '</span>'; }).join('') : '') + '</span></div>';
    }).join('') + '</div>' : '<p class="note">Nothing loaded yet.</p>') + ((p && p.errors && p.errors.length) ? '<p class="note">Not reached: ' + esc(p.errors.join('; ')) + '</p>' : '') +
      '<p class="note">Best effort: Truth Social sells a fast feed now, so the free public page can be slow or blocked. Amber words are what the push looks for (tariffs, the Fed, China, chips, your symbols...).</p>';
  }
  function drawTape(d) {
    var f = d.flow || {}, ks = Object.keys(f).sort(function (a, b) { return (f[b].buy + f[b].sell) - (f[a].buy + f[a].sell); }).slice(0, 25);
    var dis = d.disagree || {}, dk = Object.keys(dis);
    tape.innerHTML = (dk.length ? '<div class="card" style="border-color:#ffb648;padding:8px 12px;margin-bottom:8px"><b class="warn">Prices disagree right now:</b> ' + dk.map(function (s) {
      return esc(s) + ' (IBKR ' + dis[s].ibkr + ' vs Webull ' + dis[s].webull + ', ' + dis[s].diff + '%)';
    }).join(', ') + '</div>' : '') +
      (ks.length ? '<div class="card tbl"><table><tr><th>Symbol</th><th>Bought at the ask</th><th>Sold at the bid</th><th>Pressure (15 min)</th><th>Bid vs ask size</th></tr>' + ks.map(function (s) {
        var x = f[s];
        return '<tr><td><b>' + esc(s) + '</b></td><td>' + x.buy.toLocaleString() + '</td><td>' + x.sell.toLocaleString() + '</td><td><div class="bar" style="width:120px;display:inline-block;vertical-align:middle"><i style="width:' + x.pct + '%;background:' + (x.pct >= 60 ? '#2e9e5b' : x.pct <= 40 ? '#c0453b' : '#7c6cf0') + '"></i></div> ' + x.pct + '% buyers</td><td>' + (x.book != null ? x.book + '% bid' : '–') + '</td></tr>';
      }).join('') + '</table></div>' : '<p class="note">No trades counted yet (needs the IBKR board running).</p>') +
      '<p class="note">Pressure: of the shares traded in the last 15 minutes on IBKR\'s free feed, how many traded at the ask (buyers in a hurry) vs at the bid (sellers in a hurry). Bid vs ask size: shares waiting at the best bid vs best ask. ' +
      'The closest free thing to the paid order book. Cross-check: when IBKR and Webull disagree by more than 0.75% on the same symbol for 20+ seconds, the tile says so and the algos skip new entries in it.</p>';
  }
  function drawSw(s) {
    if (sw.dataset.drawn) return; sw.dataset.drawn = 1;
    ND.switches(sw, [['news_push', 'IBKR newswire push', 'Briefing.com / Dow Jones headlines about stocks you hold.'],
      ['reddit_push', 'Reddit buzz push', 'When one of your symbols\' Reddit mentions double in 24 hours.'],
      ['policy_push', 'Policy post push', 'A Truth Social or White House post that mentions tariffs, the Fed, China, chips or your symbols.'],
      ['poly_push', 'Polymarket odds push', 'When a watched Polymarket question moves 10+ points in 6 hours.'],
      ['check_push', 'Price cross-check push', 'When IBKR and Webull disagree on a symbol\'s price.']], s, function (v) { return ND.post('feeds/settings', v); });
  }
  function load() {
    ND.api('feeds').then(function (d) { drawCrowd(d); drawOdds(d.poly); drawPolicy(d.policy); drawTape(d); drawSw(d.settings || {}); ND.api('feeds/top').then(function (t) { drawTop(t.items || [], d.ibnews); }); }).catch(function () {});
  }
  load(); setInterval(function () { if (!document.hidden) load(); }, 60000);
})();
