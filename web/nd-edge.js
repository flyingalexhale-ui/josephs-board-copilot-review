/* Night desk section: 3x ETF edge and Bloomberg-style extras (jb-edge): fair value, what's inside the
 * fund, rebalance estimate, holding cost, overnight drift, expiry calendar, VIX curve, Fed odds,
 * options levels, Nasdaq-100 breadth, net liquidity, Treasury auctions, futures positioning, fund
 * card and price history. Data: /api/plus/edge (gateway/edge.py) */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var S = {
    fair: ND.section('x-fair', 'Fair value of 3x ETFs right now'),
    inside: ND.section('x-inside', 'What\'s inside my ETF, fund card and price history'),
    reb: ND.section('x-reb', 'End-of-day rebalance estimate (leveraged funds)'),
    decay: ND.section('x-decay', 'Holding cost of 3x funds (decay)'),
    drift: ND.section('x-drift', 'Overnight vs day: where the gains came from'),
    cal: ND.section('x-cal', 'Options expiry, VIX expiry and index rebalance calendar'),
    risk: ND.section('x-risk', 'VIX curve, Fed meeting odds and Nasdaq-100 breadth'),
    gamma: ND.section('x-gamma', 'Options magnet levels for QQQ and SPY'),
    macro: ND.section('x-macro', 'Net liquidity, Treasury auctions and futures positioning'),
    sw: ND.section('x-sw', 'Edge alerts')
  };
  function pc(v) { return ND.pc(v, 2); }
  function bn(v) { if (v == null) return '–'; var a = Math.abs(v); return (v < 0 ? '-' : '') + '$' + (a >= 1e9 ? (a / 1e9).toFixed(1) + 'B' : a >= 1e6 ? (a / 1e6).toFixed(0) + 'M' : Math.round(a).toLocaleString()); }
  var LIGHT = { up: '<span style="color:#5dff8f">●</span>', down: '<span style="color:#ff8a80">●</span>', flat: '<span style="color:#7f93a8">●</span>' };
  // a small line chart: series = [{name, color, pts: [[label, value], ...]}]
  function line(series, h, fmt) {
    var w = 640; h = h || 160;
    var all = []; series.forEach(function (s) { s.pts.forEach(function (p) { if (p[1] != null) all.push(p[1]); }); });
    if (!all.length) return '';
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all); if (hi === lo) hi = lo + 1;
    var n = Math.max.apply(null, series.map(function (s) { return s.pts.length; }));
    var out = '<svg viewBox="0 0 ' + w + ' ' + (h + 18) + '" style="width:100%;height:auto;display:block">';
    if (lo < 0 && hi > 0) { var y0 = 8 + (hi / (hi - lo)) * (h - 16); out += '<line x1="0" x2="' + w + '" y1="' + y0 + '" y2="' + y0 + '" stroke="#2a3d52" stroke-dasharray="3 3"/>'; }
    series.forEach(function (s) {
      var d = '';
      s.pts.forEach(function (p, i) { if (p[1] == null) return; var x = i / Math.max(1, n - 1) * (w - 60), y = 8 + (hi - p[1]) / (hi - lo) * (h - 16); d += (d ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1); });
      out += '<path d="' + d + '" fill="none" stroke="' + s.color + '" stroke-width="1.8"/>';
      var last = s.pts[s.pts.length - 1];
      if (last && last[1] != null) out += '<text x="' + (w - 56) + '" y="' + (8 + (hi - last[1]) / (hi - lo) * (h - 16) + 4) + '" fill="' + s.color + '" font-size="11">' + esc((fmt || String)(last[1])) + '</text>';
    });
    var f = series[0].pts;
    if (f.length) out += '<text x="0" y="' + (h + 14) + '" fill="#7f93a8" font-size="10">' + esc(f[0][0]) + '</text><text x="' + (w - 130) + '" y="' + (h + 14) + '" fill="#7f93a8" font-size="10">' + esc(f[f.length - 1][0]) + '</text>';
    return out + '</svg><div class="note">' + series.map(function (s) { return '<span style="color:' + s.color + '">■</span> ' + esc(s.name); }).join(' &nbsp; ') + '</div>';
  }
  function drawFair(rows) {
    S.fair.innerHTML = '<div class="card tbl"><table><tr><th>Fund</th><th>Should be</th><th>Is</th><th title="Is minus should-be. Above 0 = trading rich, below = cheap">Gap</th><th>Trend</th><th>Worked out from</th></tr>' + rows.map(function (r) {
      var g = r.gap, hot = g != null && Math.abs(g) >= 0.5;
      return '<tr><td><b>' + esc(r.sym) + '</b> <span class="note">' + (r.lev > 0 ? '+' : '') + r.lev + 'x ' + esc(r.base) + '</span></td><td>' + pc(r.expected) + '</td><td>' + pc(r.actual) + '</td><td>' +
        (g == null ? '–' : '<span class="' + (hot ? 'warn' : '') + '">' + (g > 0 ? '+' : '') + g.toFixed(2) + '</span>') + '</td><td>' + (r.trend ? LIGHT[r.trend.light] + ' <span class="note">' + esc(r.trend.from) + '</span>' : '–') + '</td><td style="text-align:left" class="note">' + esc(r.how) + '</td></tr>';
    }).join('') + '</table></div><p class="note">Moves since the 4 pm close. "Should be" = the leverage x the move of what the fund follows: the futures overnight (they trade almost 24 h), else the index fund, else its biggest stocks. ' +
      'A gap of 0.5 points or more (amber) means the fund is trading rich (+) or cheap (-) vs its index: thin overnight trading often lags the futures. The light is the trend of the futures (or index fund) over the last 1-2 hours, flipped for bear funds. Not a buy or sell signal on its own.</p>';
  }
  var cur = localStorage.getItem('nd_xsym') || 'TQQQ';
  function drawInside() {
    if (!S.inside.dataset.drawn) {
      S.inside.dataset.drawn = 1;
      S.inside.innerHTML = '<div class="row2"><input type="text" id="xSym" size="8" value="' + esc(cur) + '" autocapitalize="characters"><button id="xIn">What\'s inside</button><button class="ghost" id="xCard">Fund card</button><button class="ghost" id="xHist">Price history</button><span class="msg" id="xMsg"></span></div><div id="xOut"></div>';
      var go = function (what) {
        cur = document.getElementById('xSym').value.trim().toUpperCase(); try { localStorage.setItem('nd_xsym', cur); } catch (e) {}
        var m = document.getElementById('xMsg'); m.textContent = 'Loading…';
        ND.api('edge/' + what + '?sym=' + encodeURIComponent(cur)).then(function (d) { m.textContent = ''; ({ holdings: inside, fund: card, history: hist })[what](d); }).catch(function (e) { m.textContent = e.message; });
      };
      document.getElementById('xIn').onclick = function () { go('holdings'); };
      document.getElementById('xCard').onclick = function () { go('fund'); };
      document.getElementById('xHist').onclick = function () { go('history'); };
      go('holdings');
    }
  }
  function inside(d) {
    var o = document.getElementById('xOut');
    if (d.error || !d.rows.length) { o.innerHTML = '<p class="note">' + esc(d.error || 'No holdings list for ' + d.sym + ' yet (fetched once a day from Yahoo).') + '</p>'; return; }
    o.innerHTML = (d.note ? '<p class="note">' + esc(d.note) + '</p>' : '') + '<div class="card tbl"><table><tr><th>Stock</th><th>Weight</th><th>Move</th><th title="Weight x move: how much of the index move it caused">Effect</th><th>Earnings</th></tr>' + d.rows.map(function (r) {
      return '<tr><td><b>' + esc(r.sym) + '</b> <span class="note">' + esc((r.name || '').slice(0, 22)) + '</span></td><td>' + r.w.toFixed(1) + '%</td><td>' + pc(r.move) + '</td><td>' + (r.contrib == null ? '–' : (r.contrib > 0 ? '+' : '') + r.contrib.toFixed(2)) + '</td><td>' + (r.earnings ? '<span class="chip c-a">' + esc(r.earnings) + '</span>' : '') + '</td></tr>';
    }).join('') + '</table></div><p class="note">Top holdings of ' + esc(d.base) + ' (Yahoo, once a day). Moves since the 4 pm close, from your boards when they stream the stock, else Yahoo (can lag, no overnight).</p>';
  }
  function card(c) {
    var o = document.getElementById('xOut');
    if (c.error) { o.innerHTML = '<p class="note err">' + esc(c.error) + '</p>'; return; }
    o.innerHTML = '<div class="card" style="padding:10px 12px"><div class="big">' + esc(c.sym) + '</div><div>' + esc(c.name) + '</div><div class="note">' + esc([c.family, c.category, c.exchange].filter(Boolean).join(' · ')) + '</div>' +
      '<div class="row2">' + [['Leverage', (c.leverage > 0 ? '+' : '') + c.leverage + 'x' + (c.tracks ? ' ' + c.tracks : '')], ['Yearly fee', c.expense != null ? c.expense + '%' : '–'], ['Fund size', bn(c.aum)], ['Avg volume', c.avg_vol ? Math.round(c.avg_vol).toLocaleString() : '–'], ['Since', c.inception ? new Date(c.inception * 1000).getFullYear() : '–']].map(function (x) {
        return '<span class="chip c-d">' + esc(x[0]) + ': ' + esc(x[1]) + '</span>';
      }).join('') + '</div>' + (c.summary ? '<p class="note">' + esc(c.summary) + '</p>' : '') +
      (c.holdings && c.holdings.length ? '<div class="note">Top holdings' + (c.holdings_of ? ' of ' + esc(c.holdings_of) : '') + ': ' + c.holdings.map(function (h) { return esc(h.sym) + ' ' + h.w + '%'; }).join(', ') + '</div>' : '') + '</div>';
  }
  function csv(rows, cols) { return cols.join(',') + '\n' + rows.map(function (r) { return cols.map(function (c) { return r[c]; }).join(','); }).join('\n'); }
  function hist(d) {
    var o = document.getElementById('xOut');
    if (d.error) { o.innerHTML = '<p class="note err">' + esc(d.error) + '</p>'; return; }
    var cols = ['d', 'o', 'h', 'l', 'c', 'v'], text = csv(d.daily, cols);
    o.innerHTML = '<div class="row2"><button class="ghost" id="xCopy">Copy</button><button class="ghost" id="xDl">Download (spreadsheet)</button></div>' +
      (d.sessions && d.sessions.length ? '<div class="card tbl"><table><tr><th>Session</th><th>Open</th><th>High</th><th>Low</th><th>Close</th></tr>' + d.sessions.map(function (s) {
        return '<tr><td>' + esc(s.day.slice(5) + ' ' + s.session) + '</td><td>' + s.o + '</td><td>' + s.h + '</td><td>' + s.l + '</td><td>' + s.c + '</td></tr>';
      }).join('') + '</table></div><p class="note">Sessions from the board\'s own prices (about the last 40 hours).</p>' : '') +
      '<div class="card tbl" style="max-height:340px;overflow:auto"><table><tr><th>Day</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr>' + d.daily.map(function (r) {
        return '<tr><td>' + esc(r.d) + '</td><td>' + r.o + '</td><td>' + r.h + '</td><td>' + r.l + '</td><td>' + r.c + '</td><td>' + (r.v || 0).toLocaleString() + '</td></tr>';
      }).join('') + '</table></div><p class="note">Daily prices for the last year from Yahoo (regular session).</p>';
    document.getElementById('xCopy').onclick = function () { try { navigator.clipboard.writeText(text.replace(/,/g, '\t')); this.textContent = 'Copied'; } catch (e) { this.textContent = 'Copy not allowed here'; } };
    document.getElementById('xDl').onclick = function () { var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type: 'text/csv' })); a.download = d.sym + '-daily.csv'; document.body.appendChild(a); a.click(); a.remove(); };
  }
  function drawReb(r) {
    S.reb.innerHTML = (r.rows.length ? '<div class="card tbl"><table><tr><th>Index</th><th>Move today</th><th>Funds must</th><th>Funds</th></tr>' + r.rows.map(function (x) {
      return '<tr><td><b>' + esc(x.group) + '</b> <span class="note">' + esc(x.base) + '</span></td><td>' + pc(x.move) + '</td><td class="' + (x.flow > 0 ? 'up' : 'dn') + '"><b>' + (x.flow > 0 ? 'BUY ' : 'SELL ') + bn(Math.abs(x.flow)) + '</b></td><td class="note" style="text-align:left">' +
        x.funds.map(function (f) { return esc(f.sym) + ' ' + bn(f.flow); }).join(', ') + '</td></tr>';
    }).join('') + '</table></div>' : '<p class="note">Waiting for fund sizes (fetched once a day from Yahoo).</p>') +
      '<p class="note">' + (r.live ? '<b class="warn">Live now (3-4 pm):</b> ' : 'Most useful from 3 pm on big days. ') + esc(r.note) + '</p>';
  }
  function drawDecay(rows) {
    S.decay.innerHTML = rows.length ? '<div class="card tbl"><table><tr><th>Fund</th><th>5 days: fund</th><th>3x index</th><th>Cost</th><th>20 days: fund</th><th>3x index</th><th>Cost</th></tr>' + rows.map(function (r) {
      return '<tr><td><b>' + esc(r.sym) + '</b> <span class="note">' + (r.lev > 0 ? '+' : '') + r.lev + 'x ' + esc(r.base) + '</span></td><td>' + pc(r.d5.actual) + '</td><td>' + pc(r.d5.naive) + '</td><td>' + pc(r.d5.cost) + '</td><td>' + pc(r.d20.actual) + '</td><td>' + pc(r.d20.naive) + '</td><td>' + pc(r.d20.cost) + '</td></tr>';
    }).join('') + '</table></div><p class="note">Cost = what the fund really did minus simply its leverage x the index. Negative = holding it lost money to the daily reset (choppy markets); positive = a smooth trend helped. Daily closes from Yahoo.</p>' : '<p class="note">Waiting for daily prices (fetched once a day).</p>';
  }
  function drawDrift(list) {
    if (!list.length) { S.drift.innerHTML = '<p class="note">Waiting for daily prices.</p>'; return; }
    var a = list.filter(function (x) { return x.sym === 'QQQ'; })[0] || list[0];
    S.drift.innerHTML = line([{ name: a.sym + ' overnight only (close to next open)', color: '#b39dff', pts: a.cum.map(function (p) { return [p[0], p[1]]; }) },
      { name: a.sym + ' day only (open to close)', color: '#ffb648', pts: a.cum.map(function (p) { return [p[0], p[2]]; }) }], 150, function (v) { return v.toFixed(1) + '%'; }) +
      '<div class="card tbl"><table><tr><th>Fund</th><th>20 days: overnight</th><th>day</th><th>60 days: overnight</th><th>day</th><th>1 year: overnight</th><th>day</th><th>Best weekday overnight</th></tr>' + list.map(function (x) {
        var wd = x.weekday || {}, best = Object.keys(wd).sort(function (p, q) { return wd[q] - wd[p]; })[0];
        return '<tr><td><b>' + esc(x.sym) + '</b></td>' + [20, 60, 250].map(function (n) { return '<td>' + pc(x['n' + n].overnight) + '</td><td>' + pc(x['n' + n].day) + '</td>'; }).join('') + '<td>' + (best ? esc(best) + ' ' + pc(wd[best], 3) : '–') + '</td></tr>';
      }).join('') + '</table></div><p class="note">Overnight = from the 4 pm close to the next 9:30 open; day = 9:30 to 4 pm. Studies find most market gains come overnight; this shows whether it holds right now.</p>';
  }
  function drawCal(c) {
    S.cal.innerHTML = '<div class="card">' + c.map(function (e) {
      return '<div class="ev"><span class="tm">' + esc(new Date(e.d + 'T12:00:00').toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })) + '</span><span class="tx"><b>' + esc(e.title) + '</b><br><span class="note">' + esc(e.why) + '</span></span></div>';
    }).join('') + '</div><p class="note">From the fixed rules (third Friday, etc.), moved earlier for market holidays.</p>';
  }
  function drawRisk(d) {
    var h = '', v = d.vix;
    if (v) h += '<div class="card" style="padding:8px 12px;margin-bottom:8px;' + (v.state === 'inverted' ? 'border-color:#ffb648' : '') + '"><b class="' + (v.state === 'normal' ? '' : 'warn') + '">VIX ' + v.vix + ' vs 3-month VIX ' + v.vix3m + '</b> (ratio ' + v.ratio + ')<br><span class="note">' + esc(v.text) + '</span></div>';
    var o = d.odds;
    if (o && o.meetings && o.meetings.length) {
      h += '<div class="card" style="padding:8px 12px;margin-bottom:8px"><b>Fed meeting odds (Kalshi)</b>' + o.meetings.map(function (m) {
        var b = m.bucket, wk = m.week;
        return '<div style="margin:6px 0"><div class="note">' + esc(m.title) + '</div><div style="display:flex;height:14px;border-radius:3px;overflow:hidden">' +
          '<i style="width:' + b.cut + '%;background:#2e9e5b" title="cut ' + b.cut + '%"></i><i style="width:' + b.hold + '%;background:#5a6b7d" title="hold ' + b.hold + '%"></i><i style="width:' + b.hike + '%;background:#c0453b" title="hike ' + b.hike + '%"></i></div>' +
          '<div class="note">cut ' + b.cut + '% · hold ' + b.hold + '% · hike ' + b.hike + '%' + (wk ? ' · this week: cut ' + (wk.cut >= 0 ? '+' : '') + wk.cut + ', hike ' + (wk.hike >= 0 ? '+' : '') + wk.hike : '') + '</div></div>';
      }).join('') + '</div>';
    }
    var b = d.breadth;
    if (b) h += '<div class="card" style="padding:8px 12px"><b>Nasdaq-100 breadth</b><div style="display:flex;height:14px;border-radius:3px;overflow:hidden;margin:6px 0"><i style="width:' + (b.up / b.n * 100) + '%;background:#2e9e5b"></i><i style="width:' + (b.down / b.n * 100) + '%;background:#c0453b"></i></div>' +
      '<div class="note">' + b.up + ' up · ' + b.down + ' down' + (b.n50 ? ' · ' + b.above50 + ' of ' + b.n50 + ' above their 50-day average' : '') + '. ' + esc(b.note) + '</div></div>';
    S.risk.innerHTML = h || '<p class="note">Loading (first numbers a few minutes after a restart).</p>';
  }
  function drawGamma(g) {
    var syms = Object.keys(g || {});
    S.gamma.innerHTML = syms.length ? syms.map(function (s) {
      var x = g[s];
      return '<div class="card" style="padding:8px 12px;margin-bottom:8px"><b>' + esc(s) + '</b> at ' + x.spot + ' <span class="note">(' + esc(ND.ago(x.at)) + ', expiries ' + esc((x.expiries || []).join(', ')) + ')</span><br>' +
        x.levels.map(function (l) { return '<span class="chip ' + (l.kind === 'magnet' ? 'c-b' : l.kind === 'flip' ? 'c-a' : 'c-r') + '">' + (l.kind === 'magnet' ? 'MAGNET ' : l.kind === 'flip' ? 'FLIP ' : 'FAST ') + l.p + '</span>'; }).join('') + '</div>';
    }).join('') + '<p class="note"><b>Magnet</b>: big options positions where dealers\' hedging tends to hold the price near that level. <b>Fast</b>: levels where their hedging can speed a move up. <b>Flip</b>: above it moves tend to be calmer, below it wilder. ' +
      'Worked out by the board from Yahoo\'s free, delayed options data (nearest 3 expiries), so it is an estimate. Also drawn as dotted lines on the QQQ and SPY charts.</p>' : '<p class="note">Loading from Yahoo options.</p>';
  }
  function drawMacro(d) {
    var h = '', nl = d.netliq;
    if (nl && nl.rows && nl.rows.length) {
      var r = nl.rows, first = r[0], q0 = (r.filter(function (x) { return x[2]; })[0] || [])[2];
      h += '<div class="card" style="padding:8px 12px;margin-bottom:8px"><b>Net liquidity: $' + r[r.length - 1][1].toFixed(2) + ' trillion</b> <span class="note">(' + esc(r[r.length - 1][0]) + ')</span>' +
        line([{ name: 'Net liquidity, change in %', color: '#7fd6ff', pts: r.map(function (x) { return [x[0], (x[1] / first[1] - 1) * 100]; }) },
          { name: 'QQQ, change in %', color: '#ffb648', pts: r.map(function (x) { return [x[0], x[2] && q0 ? (x[2] / q0 - 1) * 100 : null]; }) }], 130, function (v) { return v.toFixed(1) + '%'; }) +
        '<div class="note">The Fed\'s balance sheet minus the Treasury\'s cash account minus reverse repo (FRED, weekly). When it rises the Nasdaq has tended to do well. A gauge, not a timing tool.</div></div>';
    }
    var a = d.auctions;
    if (a && a.rows && a.rows.length) h += '<div class="card tbl" style="margin-bottom:8px"><table><tr><th>Last auction</th><th>Date</th><th>Yield</th><th>Bid-to-cover</th><th>Usual</th><th>Dealers took</th><th>Usual</th><th></th></tr>' + a.rows.map(function (x) {
      return '<tr><td><b>' + esc(x.term) + '</b></td><td>' + esc(x.d) + '</td><td>' + (x.yield || '–') + '%</td><td>' + x.btc.toFixed(2) + '</td><td>' + x.avg_btc + '</td><td>' + (x.dealer == null ? '–' : x.dealer + '%') + '</td><td>' + (x.avg_dealer == null ? '–' : x.avg_dealer + '%') + '</td><td><span class="chip ' + (x.verdict === 'weak' ? 'c-r' : x.verdict === 'strong' ? 'c-g' : 'c-d') + '">' + esc(x.verdict) + '</span></td></tr>';
    }).join('') + '</table></div><p class="note">TreasuryDirect. Weak = fewer bids than usual (bid-to-cover 5% under its last 6 auctions) or dealers had to take 5+ points more than usual. Weak long-bond auctions have caused sharp afternoon Nasdaq drops.</p>';
    var c = d.cftc;
    if (c && c.rows) h += '<div class="card tbl"><table><tr><th>Futures</th><th>Report</th><th>Hedge funds (net)</th><th>Asset managers (net)</th><th>vs last 3 years</th></tr>' + c.rows.map(function (x) {
      return '<tr><td><b>' + esc(x.name) + '</b></td><td>' + esc(x.date) + '</td><td>' + Math.round(x.lev).toLocaleString() + '</td><td>' + Math.round(x.am).toLocaleString() + '</td><td>' + x.rank + '%' + (x.extreme ? ' <span class="chip c-a">' + esc(x.extreme) + '</span>' : '') + '</td></tr>';
    }).join('') + '</table></div><p class="note">CFTC weekly report (positions as of Tuesday, out Friday afternoon), in contracts. "Hedge funds" = leveraged funds. The % shows where today\'s hedge-fund position sits vs the last 3 years: 90%+ or 10%- is crowded, and crowded positions often unwind fast.</p>';
    S.macro.innerHTML = h || '<p class="note">Loading (FRED, TreasuryDirect, CFTC are fetched in the background).</p>';
  }
  function drawSw(s) {
    if (S.sw.dataset.drawn) return; S.sw.dataset.drawn = 1;
    ND.switches(S.sw, [['vix_push', 'VIX curve warning push', 'Once a day when short-term VIX goes above 3-month VIX (a stress signal).'],
      ['yen_push', 'Yen shock push', 'When dollar/yen moves more than 1% since the 4 pm close (once per night).'],
      ['auction_push', 'Weak Treasury auction push', 'When a 10, 20 or 30-year auction looks weak.']], s, function (v) { return ND.post('edge/settings', v); });
  }
  function load() {
    ND.api('edge').then(function (d) {
      drawFair(d.fair || []); drawInside(); drawReb(d.rebalance || { rows: [] }); drawDecay(d.decay || []); drawDrift(d.drift || []);
      drawCal(d.calendar || []); drawRisk(d); drawGamma(d.gamma); drawMacro(d); drawSw(d.settings || {});
      var errs = Object.keys(d.errors || {});
      if (errs.length && !document.getElementById('xErr')) { var p = document.createElement('p'); p.className = 'note'; p.id = 'xErr'; S.sw.appendChild(p); }
      if (document.getElementById('xErr')) document.getElementById('xErr').innerHTML = errs.length ? 'Not loaded right now (tries again by itself): ' + errs.map(function (k) { return esc(k + ': ' + d.errors[k]); }).join('<br>') : '';
    }).catch(function () {});
  }
  load(); setInterval(function () { if (!document.hidden) load(); }, 60000);
})();
