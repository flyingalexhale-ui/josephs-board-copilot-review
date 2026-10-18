/* Night desk section: leveraged ETF twins, pre-market gappers, short interest and float, unusual
 * options, analyst ratings, catalyst tags (jb-mdata). Data: /api/plus/mdata */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var tw = ND.section('md-twins', 'Leveraged ETF twins (bull vs bear)');
  var gp = ND.section('md-gap', 'Pre-market gapper checklist');
  var sh = ND.section('md-short', 'Short interest, float and volume');
  var op = ND.section('md-opts', 'Unusual options today');
  var rt = ND.section('md-rate', 'Analyst rating changes (last 2 weeks)');
  var sw = ND.section('md-switches', 'Market data alerts');
  var lg = ND.section('md-log', 'Recent market data alerts');
  var SW = [
    ['ratings', 'Analyst upgrades and downgrades', 'A push when a firm upgrades, downgrades or starts covering a stock on your boards or that you hold.'],
    ['options', 'Unusual options', 'A push (once per stock per day) when options on a stock or ETF on your boards trade far more than their open interest: someone is making a big new bet.'],
    ['short', 'New short interest numbers', 'A push when a new short interest report comes out for a stock on your boards (twice a month).']
  ];
  var lev = localStorage.getItem('nd_lev') || '3', drawn = false, last = null;
  function big(n) { return n == null ? '–' : n >= 1e9 ? (n / 1e9).toFixed(2) + 'B' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'K' : String(n); }
  function tagChips(t) {
    return (t || []).map(function (x) { return '<span class="chip ' + (x === 'NO NEWS' ? 'c-d' : x === 'EARNINGS' || x === 'FDA' || x === 'DEAL' ? 'c-a' : 'c-b') + '">' + esc(x) + '</span>'; }).join(' ');
  }
  function leg(x, sym) {
    if (!x) return '<td class="note">–</td>';
    if (x.missing) return '<td><b>' + esc(x.sym) + '</b> <span class="note">not found</span></td>';
    var p = x.p, mv = p && p.since_close, off = mv != null && x.expected != null ? mv - x.expected : null;
    var stale = p && p.at && Date.now() - p.at > 20 * 60000 ? ' <span class="note" title="Last price seen at ' + esc(ND.time(p.at)) + ' (Yahoo has no prices 8 pm - 4 am; put it on your IBKR board for live overnight prices)">' + esc(ND.time(p.at)) + '</span>' : '';
    return '<td style="text-align:left;white-space:normal"><b>' + esc(x.sym) + '</b> ' + (p ? ND.pc(mv) + '<br><span class="note">$' + p.last.toFixed(2) + '</span>' : '<span class="note">loading</span>') + stale +
      (off != null && Math.abs(off) >= 0.5 ? ' <span class="chip c-d" title="Expected ' + x.expected.toFixed(2) + '% (' + (x.expected >= 0 ? '' : '') + 'leverage × the index move). It is off by ' + off.toFixed(2) + ' points, from fees, the daily reset, or a stale price.">off ' + (off > 0 ? '+' : '') + off.toFixed(1) + '</span>' : '') + '</td>';
  }
  function drawTwins(rows) {
    var list = rows.map(function (r, i) { r.i = i; return r; }).filter(function (r) { return lev === 'all' || String(r.lev) === lev; });
    tw.innerHTML = '<div class="row2"><span class="note">Show:</span>' + [['3', '3x'], ['2', '2x'], ['all', 'All']].map(function (b) {
      return '<button class="' + (lev === b[0] ? '' : 'ghost') + '" data-lev="' + b[0] + '">' + b[1] + '</button>'; }).join('') + '</div>' +
      '<div class="card tbl"><table><tr><th style="text-align:left">Follows</th><th style="text-align:left">Bull</th><th style="text-align:left">Bear (inverse)</th><th></th></tr>' + list.map(function (r) {
        var b = r.base.p;
        return '<tr><td style="text-align:left;white-space:normal"><b>' + esc(r.group) + '</b> <span class="chip c-p">' + r.lev + 'x</span><br><span class="note">' + esc(r.base.sym) + ' ' + (b ? ND.pc(b.since_close) : '') + '</span></td>' +
          leg(r.bull) + leg(r.bear) + '<td><button class="ghost" data-rm="' + r.i + '" title="Remove this row">×</button></td></tr>';
      }).join('') + '</table></div>' +
      '<details class="note"><summary>Add a pair</summary><div class="row2"><input type="text" id="tpG" placeholder="Name (e.g. Oil)" size="12"><input type="text" id="tpB" placeholder="Follows (USO)" size="7" autocapitalize="characters">' +
      '<select id="tpL"><option>3</option><option>2</option></select><input type="text" id="tpU" placeholder="Bull fund" size="7" autocapitalize="characters"><input type="text" id="tpD" placeholder="Bear fund" size="7" autocapitalize="characters">' +
      '<button id="tpAdd">Add</button><span class="msg" id="tpMsg"></span></div></details>' +
      '<p class="note">% = move since the 4 pm close. The bear fund should move about -' + (lev === '2' ? '2' : '3') + 'x the index; an "off" chip means it isn\'t (fees, the daily reset, or a price that hasn\'t updated). ' +
      'Prices: your boards first (live, overnight too on the IBKR board), otherwise Yahoo every 2 minutes, which has nothing 8 pm - 4 am. Leveraged funds reset every day, so over several days they drift from 3x the index.</p>';
    [].forEach.call(tw.querySelectorAll('[data-lev]'), function (b) { b.onclick = function () { lev = b.dataset.lev; try { localStorage.setItem('nd_lev', lev); } catch (e) {} drawTwins(rows); }; });
    [].forEach.call(tw.querySelectorAll('[data-rm]'), function (b) { b.onclick = function () {
      if (!confirm('Remove this row?')) return;
      ND.post('mdata/pairs', { remove: +b.dataset.rm }).then(function (d) { last.twins = d.twins; drawTwins(d.twins); }); }; });
    document.getElementById('tpAdd').onclick = function () {
      var v = function (id) { return document.getElementById(id).value.trim(); }, m = document.getElementById('tpMsg');
      ND.post('mdata/pairs', { add: { group: v('tpG'), base: v('tpB'), lev: v('tpL'), bull: v('tpU'), bear: v('tpD') } })
        .then(function (d) { last.twins = d.twins; drawTwins(d.twins); }).catch(function (e) { m.textContent = e.message; });
    };
  }
  function yes(v) { return v === true ? '<span class="up">✓</span>' : v === false ? '<span class="dn">✗</span>' : '<span class="note">?</span>'; }
  function drawGap(d) {
    var g = d.gappers || [];
    gp.innerHTML = (g.length ? g.map(function (r) {
      return '<div class="card" style="padding:8px 12px;margin-bottom:8px"><b>' + esc(r.sym) + '</b> ' + ND.pc(r.gap, 1) + ' $' + r.last.toFixed(2) + ' <span class="chip ' + (r.score >= 4 ? 'c-g' : r.score >= 3 ? 'c-a' : 'c-d') + '">' + r.score + ' / 5</span> ' + tagChips(r.tags) +
        '<div class="tbl"><table>' + r.checks.map(function (c) { return '<tr><td style="text-align:left">' + yes(c[1]) + ' ' + esc(c[0]) + '</td><td style="text-align:left" class="note">' + esc(c[2]) + '</td></tr>'; }).join('') + '</table></div></div>';
    }).join('') : '<p class="note">No stock on your boards or scanner is gapping 2% or more vs the 4 pm close right now.</p>') +
      '<p class="note">Five common checks traders use before the open: a gap of 2-10% (bigger gaps often fade), a reason (catalyst), pre-market volume at least 5x its usual pre-market, a price of $5-100, and holding the top half of its pre-market range (bottom half for a gap down). ' +
      'Most useful 4 - 9:30 am; overnight it is an early look. Stocks only. Research help, not a buy signal.</p>';
  }
  function drawShort(d) {
    var rows = (d.rows || []).filter(function (r) { return r.kind === 'stock' || r.short_pct != null || r.rv != null; });
    rows.sort(function (a, b) { return (b.mine - a.mine) || ((b.short_pct || 0) - (a.short_pct || 0)); });
    sh.innerHTML = '<div class="card tbl"><table><tr><th>Stock</th><th>Float</th><th title="Shares sold short as a % of the shares that trade freely">Short % float</th><th title="Days it would take short sellers to buy back, at normal volume">Days to cover</th>' +
      '<th title="Change in shares sold short vs the report before">vs last report</th><th title="Share of the last day\'s trades that were short sales (FINRA), vs its 10-day average">Short volume</th><th title="Volume so far this session vs the same time on the last ~9 days">Rel vol</th><th>Why moving</th></tr>' +
      rows.map(function (r) {
        var sv = r.sv, hot = r.short_pct >= 20;
        return '<tr><td style="text-align:left"><b>' + esc(r.sym) + '</b>' + (r.held ? ' <span class="chip c-r">HELD</span>' : '') + (r.mine ? '' : ' <span class="note">scanner</span>') + '</td>' +
          '<td>' + big(r.float) + '</td><td>' + (r.short_pct != null ? '<span class="' + (hot ? 'warn' : '') + '">' + r.short_pct.toFixed(1) + '%</span>' : '–') + '</td>' +
          '<td>' + (r.short_ratio != null ? r.short_ratio.toFixed(1) : '–') + '</td><td>' + (r.short_ch != null ? ND.pc(r.short_ch, 0) : '–') + '</td>' +
          '<td>' + (sv ? sv.pct.toFixed(0) + '%' + (sv.avg != null ? ' <span class="note">avg ' + sv.avg.toFixed(0) + '%</span>' : '') : '–') + '</td>' +
          '<td>' + (r.rv != null ? '<span class="' + (r.rv >= 2 ? 'up' : '') + '">' + r.rv.toFixed(1) + 'x</span> <span class="note">' + esc({ pre: 'pre', regular: 'day', after: 'AH', day: 'last day' }[r.rv_sess] || '') + '</span>' : '–') + '</td>' +
          '<td style="text-align:left">' + tagChips(r.tags) + '</td></tr>';
      }).join('') + '</table></div>' +
      '<p class="note">Short interest is only published twice a month (FINRA collects it from brokers); the Mac checks Yahoo every 3 hours for your board stocks and pushes when a new number lands. ' +
      'Short volume is FINRA\'s free daily file (after about 6 pm, last: ' + esc(d.sv_last || 'not read yet') + '); about 40-50% is normal because market makers sell short to fill buy orders, so watch for a jump vs the stock\'s own average. ' +
      'Short % of float above 20% (amber) = a crowded short, which can squeeze up fast on good news. Checked so far: ' + d.n_stats + ' symbols.</p>' +
      '<p class="note"><b>Why moving tags:</b> EARNINGS (report today or last night), ANALYST (rating change), 8-K / INSIDER / 13D (SEC filings), FDA, GUIDANCE, DEAL, OFFERING (words in today\'s headlines), SECTOR (its sector fund is moving the same way 1%+), NEWS (other headlines), NO NEWS (moving 2%+ with no reason found: be careful).</p>';
  }
  function drawOpts(d) {
    var rows = (d.rows || []).filter(function (r) { return r.opts && r.opts.unusual.length; });
    var all = (d.rows || []).filter(function (r) { return r.opts; });
    op.innerHTML = (rows.length ? '<div class="card tbl"><table><tr><th>Symbol</th><th>Contract</th><th>Volume</th><th>Open before</th><th>Last price</th></tr>' + rows.map(function (r) {
      return r.opts.unusual.map(function (u, i) {
        return '<tr><td style="text-align:left">' + (i ? '' : '<b>' + esc(r.sym) + '</b> <span class="note">put/call ' + (r.opts.pc != null ? r.opts.pc : '–') + (r.opts.avg ? ', ' + (r.opts.vol / r.opts.avg).toFixed(1) + 'x usual volume' : '') + '</span>') + '</td>' +
          '<td><span class="' + (u.type === 'call' ? 'up' : 'dn') + '">$' + u.strike + ' ' + u.type + '</span> <span class="note">' + esc(u.exp) + '</span></td><td>' + u.vol.toLocaleString() + '</td><td>' + u.oi.toLocaleString() + (u.x ? ' <span class="note">(' + u.x + 'x)</span>' : '') + '</td><td>' + (u.last != null ? '$' + u.last : '–') + '</td></tr>';
      }).join('');
    }).join('') + '</table></div>' : '<p class="note">' + (all.length ? 'Nothing unusual on your boards\' stocks and ETFs today.' : 'Checked every 30 minutes from 9:30 am to 4:15 pm (options don\'t trade at night).') + '</p>') +
      '<p class="note">"Unusual" = at least 500 contracts traded today and at least twice the open interest (contracts that existed before today), so it is mostly new money. Calls = bets on up, puts = bets on down (or protection). Nearest expiry and the next one. Yahoo\'s free delayed data. Stocks and ETFs on your boards and holdings.</p>';
  }
  function drawRate(d) {
    var r = d.ratings || [];
    rt.innerHTML = r.length ? '<div class="card">' + r.map(function (x) {
      return '<div class="ev"><span class="chip ' + (x.action === 'up' ? 'c-g' : x.action === 'down' ? 'c-r' : 'c-b') + '">' + (x.action === 'up' ? 'UP' : x.action === 'down' ? 'DOWN' : 'NEW') + '</span> <span class="tx">' + esc(x.text) + '</span> <span class="note">' + esc(new Date(x.t).toLocaleDateString()) + '</span></div>';
    }).join('') + '</div>' : '<p class="note">No upgrades, downgrades or new coverage in the last 2 weeks for your stocks (checked every 3 hours from Yahoo).</p>';
  }
  function paint(d) {
    last = d;
    if (!drawn) { drawn = true; ND.switches(sw, SW, d.settings, function (v) { return ND.post('mdata/settings', { settings: v }); }); }
    drawTwins(d.twins || []); drawGap(d); drawShort(d); drawOpts(d); drawRate(d);
    lg.innerHTML = (d.slow ? '<p class="note warn">Yahoo asked the Mac to slow down; this catches up by itself in about 10 minutes.</p>' : '') + ND.log(d.log);
  }
  function busy() { var a = document.activeElement; return a && /INPUT|SELECT/.test(a.tagName) && tw.contains(a); }
  function load() { if (!busy()) ND.api('mdata').then(paint).catch(function () {}); }
  load(); setInterval(load, 30000);
})();
