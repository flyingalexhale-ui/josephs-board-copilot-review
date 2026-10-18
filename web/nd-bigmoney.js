/* Night desk section: Big money (jb-mdata). Institutions and funds, insider trades, block trades,
 * dark pools, and politicians' trades. All free, read only. Data: /api/plus/bigmoney */
(function () {
  'use strict';
  var ND = window.ND, esc = ND.esc;
  var own = ND.section('bm-own', 'Big money: who owns it');
  var ins = ND.section('bm-ins', 'Insider trades (last 90 days)');
  var blk = ND.section('bm-blocks', 'Block trades (big single trades)');
  var dp = ND.section('bm-dark', 'Dark pools (weekly)');
  var pol = ND.section('bm-pol', 'Politicians\' trades');
  var pick = localStorage.getItem('bm_sym') || '', polAll = false;
  function big(n) { return n == null ? '–' : Math.abs(n) >= 1e9 ? (n / 1e9).toFixed(2) + 'B' : Math.abs(n) >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : Math.abs(n) >= 1e3 ? Math.round(n / 1e3) + 'K' : String(Math.round(n)); }
  function chg(v) { return v == null ? '' : '<span class="' + (v > 0 ? 'up' : v < 0 ? 'dn' : 'note') + '">' + (v > 0 ? 'added ' : v < 0 ? 'cut ' : '') + (v === 0 ? 'same' : Math.abs(v).toFixed(1) + '%') + '</span>'; }
  function amt(a) { return String(a || '').replace(/\$([\d,]+)/g, function (m, n) { return '$' + big(+n.replace(/,/g, '')); }).replace(/\s*-\s*/, '-'); }
  function md(d) { var m = /^(\d{4})-(\d\d)-(\d\d)$/.exec(d || ''); return m ? new Date(+m[1], +m[2] - 1, +m[3]).toLocaleDateString([], { month: 'short', day: 'numeric' }) : (d || ''); }
  function holders(list, title) {
    if (!list.length) return '';
    return '<div class="card tbl"><table><tr><th style="text-align:left">' + esc(title) + '</th><th>% of co.</th><th title="Change vs that holder\'s report before">Change</th></tr>' + list.map(function (h) {
      return '<tr><td style="text-align:left;white-space:normal">' + esc(h.name) + '<br><span class="note">' + big(h.shares) + ' shares · ' + esc(h.date) + '</span></td><td>' + (h.pct != null ? h.pct.toFixed(2) + '%' : '–') + '</td><td>' + (chg(h.chg) || '–') + '</td></tr>';
    }).join('') + '</table></div>';
  }
  function drawOwn(d) {
    var rows = d.rows || [];
    if (!rows.length) { own.innerHTML = '<p class="note">Loading: the Mac checks one stock at a time (every 12 hours each), so the first look takes a few minutes.</p>'; return; }
    if (!rows.some(function (r) { return r.sym === pick; })) pick = (rows.filter(function (r) { return r.held; })[0] || rows[0]).sym;
    var r = rows.filter(function (x) { return x.sym === pick; })[0];
    var sel = '<div class="row2"><select id="bmSym">' + rows.map(function (x) { return '<option' + (x.sym === pick ? ' selected' : '') + '>' + esc(x.sym) + '</option>'; }).join('') + '</select>' +
      (r.held ? '<span class="chip c-r">HELD</span>' : '') + '</div>';
    var summary = r.inst_pct == null && r.insider_pct == null ? '<p class="note">No ownership numbers yet for ' + esc(r.sym) + (r.kind === 'etf' ? ' (funds like 3x ETFs usually have none: their shares are created and redeemed every day)' : ' (still loading, or Yahoo has none)') + '.</p>' :
      '<div class="card"><b>' + (r.inst_pct != null ? r.inst_pct.toFixed(1) + '%' : '–') + '</b> held by institutions' + (r.inst_count ? ' (' + r.inst_count.toLocaleString() + ' firms)' : '') +
      ' · <b>' + (r.insider_pct != null ? r.insider_pct.toFixed(1) + '%' : '–') + '</b> by insiders</div>';
    own.innerHTML = sel + summary + holders(r.inst, 'Biggest institutions') + holders(r.funds, 'Biggest funds') +
      '<p class="note">From the quarterly 13F reports big investors file with the SEC (via Yahoo), so they can be 45+ days old when they come out. "Added / cut" is the change vs that holder\'s report before. Your board stocks and holdings only.</p>';
    document.getElementById('bmSym').onchange = function () { pick = this.value; try { localStorage.setItem('bm_sym', pick); } catch (e) {} drawAll(); };
    var list = [];
    rows.forEach(function (x) { (x.insiders || []).forEach(function (t) { list.push(Object.assign({ sym: x.sym }, t)); }); });
    list.sort(function (a, b) { return b.t - a.t; });
    ins.innerHTML = list.length ? '<div class="card tbl"><table><tr><th style="text-align:left">Stock</th><th style="text-align:left">Who</th><th></th><th>Value</th></tr>' + list.slice(0, 40).map(function (t) {
      return '<tr><td style="text-align:left"><b>' + esc(t.sym) + '</b></td><td style="text-align:left;white-space:normal">' + esc(t.name) + '<br><span class="note">' + esc(t.role) + '</span></td>' +
        '<td><span class="chip ' + (t.kind === 'buy' ? 'c-g' : t.kind === 'sell' ? 'c-r' : 'c-d') + '">' + esc(t.kind === 'other' ? (t.text || 'other').slice(0, 24) : t.kind.toUpperCase()) + '</span></td>' +
        '<td>' + (t.value ? '$' + big(t.value) : '–') + '<br><span class="note">' + big(t.shares) + ' sh · ' + esc(new Date(t.t).toLocaleDateString([], { month: 'short', day: 'numeric' })) + '</span></td></tr>';
    }).join('') + '</table></div><p class="note">Company officers and directors trading their own stock (SEC Form 4, filed within 2 business days). Buys with their own money are the ones traders watch; sales are often planned in advance or for taxes. Grants and option exercises show in grey.</p>'
      : '<p class="note">No insider trades in the last 90 days for your board stocks (or still loading).</p>';
    dp.innerHTML = (function () {
      var w = r.ats || [];
      if (!w.length) return '<p class="note">No dark pool numbers for ' + esc(r.sym) + ' yet (FINRA publishes them weekly, 2-4 weeks late).</p>';
      var mx = Math.max.apply(null, w.map(function (x) { return x.shares; })) || 1;
      return '<div class="card"><b>' + esc(r.sym) + '</b> shares traded on dark pools per week<br>' + w.map(function (x) {
        return '<div class="row2" style="gap:6px;margin:2px 0"><span class="note" style="width:84px;flex:none">' + esc(x.week) + '</span><span style="display:inline-block;height:10px;border-radius:3px;background:#5aa9e6;width:' + Math.max(2, Math.round(x.shares / mx * 50)) + '%"></span><span style="white-space:nowrap">' + big(x.shares) + '</span></div>';
      }).join('') + '</div>';
    })() + '<p class="note">Dark pools are private trading venues big investors use to buy or sell large amounts without showing their hand. A week far above the others can mean a fund building or selling a position. Choose the stock in the box above.</p>';
  }
  function drawBlocks(d) {
    var b = d.blocks || [];
    blk.innerHTML = (b.length ? '<div class="card tbl"><table><tr><th style="text-align:left">Time</th><th style="text-align:left">Symbol</th><th>Shares</th><th>Price</th><th>Value</th></tr>' + b.slice(0, 30).map(function (p) {
      return '<tr><td style="text-align:left">' + esc(ND.time(p.ts)) + '</td><td style="text-align:left"><b>' + esc(p.sym) + '</b></td><td>' + big(p.size) + '</td><td>$' + Number(p.price).toFixed(2) + '</td><td>$' + big(p.value) + '</td></tr>';
    }).join('') + '</table></div>' : '<p class="note">No block trades seen in the last 2 days on your IBKR board symbols.</p>') +
      '<p class="note">Single trades of $1 million or 20,000 shares or more, as they print on your IBKR board (any session, overnight too). Big single trades usually come from institutions. ' +
      'Only symbols on your IBKR board; the size can be changed in settings.env (JB_BLOCK_DOLLARS, JB_BLOCK_SHARES).</p>';
  }
  function drawPol(d) {
    var mine = d.politicians_mine || [], all = d.politicians || [];
    var list = polAll ? all : mine;
    pol.innerHTML = '<div class="row2"><button class="' + (polAll ? 'ghost' : '') + '" data-pa="0">Your stocks (' + mine.length + ')</button><button class="' + (polAll ? '' : 'ghost') + '" data-pa="1">All recent (' + all.length + ')</button></div>' +
      (list.length ? '<div class="card tbl"><table><tr><th style="text-align:left">Who</th><th style="text-align:left">Stock</th><th></th><th>Amount</th></tr>' + list.slice(0, 60).map(function (t) {
        return '<tr><td style="text-align:left;white-space:normal">' + esc(t.name) + '<br><span class="note">' + esc(t.chamber === 'Senate' ? 'Senate' : 'House ' + (t.where || '')) + '</span></td><td style="text-align:left"><b>' + esc(t.sym) + '</b></td>' +
          '<td><span class="chip ' + (t.kind === 'buy' ? 'c-g' : t.kind === 'sell' ? 'c-r' : 'c-d') + '">' + esc(t.kind.toUpperCase()) + '</span></td><td>' + esc(amt(t.amount)) + '<br><span class="note" title="Traded / reported">' + esc(md(t.date)) + ' · rep. ' + esc(md(t.reported)) + '</span></td></tr>';
      }).join('') + '</table></div>' : '<p class="note">' + (polAll ? 'No trades read yet (the Mac checks every 6 hours).' : 'No member of Congress has reported a trade in your board stocks lately.') + '</p>') +
      '<p class="note">From the official filings: the House Clerk\'s site and the Senate\'s eFD site' + (d.house_at ? ' (House read ' + esc(ND.ago(d.house_at)) + (d.senate_at ? ', Senate ' + esc(ND.ago(d.senate_at)) : '') + ')' : '') +
      '. Members have up to 45 days to report, so these are weeks old: background, not a signal. Amounts are ranges because that is how they are filed. Handwritten Senate filings are skipped.</p>';
    [].forEach.call(pol.querySelectorAll('[data-pa]'), function (b) { b.onclick = function () { polAll = b.dataset.pa === '1'; drawPol(d); }; });
  }
  var last = null;
  function drawAll() {
    if (!last) return;
    drawOwn(last); drawBlocks(last); drawPol(last);
    var e = last.errors || {}, k = Object.keys(e);
    if (k.length) pol.insertAdjacentHTML('beforeend', '<p class="note">' + k.slice(0, 4).map(function (x) { return '<span class="err">' + esc(x + ': ' + e[x]) + '</span>'; }).join('<br>') + '</p>');
  }
  function load() { ND.api('bigmoney').then(function (d) { last = d; drawAll(); }).catch(function (e) { own.innerHTML = '<p class="err">Could not load: ' + esc(e.message) + '</p>'; }); }
  load();
  setInterval(load, 120000);
})();
