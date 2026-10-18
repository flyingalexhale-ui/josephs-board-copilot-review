/* Alex Hale's Board -- Scanner tab additions (jb-desk). Adds, never removes (only the "For research
 * only" box above the Picks is hidden, as asked). A second row of on-page tabs next to Movers | Picks:
 *   Top 5       the board's five strongest up/down Picks right now, ranked, each graded live against
 *               its price since it was picked, plus the board's own report card over time (30 min,
 *               1 hour, 4 hours, 1 day) and a line of its running result (gateway/top5.py)
 *   Ask Claude  a stripped-back Ask Claude box: type a question, and the scanner's live numbers at
 *               that moment (movers, flags, Picks, Top 5) plus the board's market data always go with it
 *   ETF Pick    the Night Desk's ETF pick of the hour, with its push switch   } the same parts as on the
 *   Gappers     the Night Desk's pre-market gapper checklist               } Night Desk (one copy of the
 *   Twins       the Night Desk's leveraged ETF twins (bull vs bear)        } settings), shown here too
 * Hooked in by one <script src="scanner-plus.js"> line on movers.html. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var head = document.querySelector('header'), modes = $('moversBtn') && $('moversBtn').parentNode;
  if (!head || !modes) return;
  var VIEWS = [['top5', 'Top 5'], ['ask', 'Ask Claude'], ['pick', 'ETF Pick', 'pick'], ['gap', 'Gappers', 'md-gap'], ['twins', 'Twins', 'md-twins'], ['politics', 'Politics', 'bm-pol,f-policy']];
  var css = document.createElement('style');
  css.textContent =
    '.pk-warn{display:none !important;}' +
    '#spView{display:none;padding:12px 16px 24px;} body.spv main,body.spv #picksView{display:none !important;} body.spv #spView{display:block;}' +
    '.sp-pane{display:none;} .sp-pane.on{display:block;}' +
    '.sp-frame{display:block;width:1px;min-width:100%;border:0;min-height:300px;background:transparent;}' +
    '.t5-head{display:flex;flex-wrap:wrap;gap:10px;align-items:stretch;margin-bottom:12px;}' +
    '.t5-grade{display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:110px;border:1px solid var(--line-bright);border-radius:10px;background:var(--panel);padding:8px 12px;}' +
    '.t5-grade b{font-size:34px;line-height:1;color:#ffc400;} .t5-grade span{font-size:10.5px;color:var(--dim);margin-top:4px;text-align:center;}' +
    '.t5-hz{flex:1 1 420px;display:grid;grid-template-columns:repeat(4,1fr);gap:8px;}' +
    '.t5-hz div{border:1px solid var(--line-bright);border-radius:10px;background:var(--panel);padding:8px 10px;text-align:center;}' +
    '.t5-hz small{display:block;font-size:10.5px;color:var(--dim);letter-spacing:.05em;} .t5-hz b{display:block;font-size:20px;margin:3px 0 1px;} .t5-hz i{font-style:normal;font-size:11px;color:var(--dim);}' +
    '.t5-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;}' +
    '@media (max-width:1100px){.t5-grid{grid-template-columns:repeat(3,minmax(0,1fr));}} @media (max-width:640px){.t5-grid{grid-template-columns:1fr;} .t5-hz{grid-template-columns:repeat(2,1fr);} #spView{padding:10px 10px 20px;}}' +
    '.t5{border:1px solid var(--line-bright);border-top:3px solid var(--dim);border-radius:10px;background:var(--panel);padding:10px 12px;min-width:0;}' +
    '.t5.up{border-top-color:var(--up);} .t5.down{border-top-color:var(--down);}' +
    '.t5-top{display:flex;align-items:center;gap:8px;} .t5-rk{font-size:11px;font-weight:700;background:#164060;color:#fff;border-radius:5px;padding:2px 7px;}' +
    '.t5-sym{font-size:18px;font-weight:700;color:#8fd1a8;} .t5-ln{margin-left:auto;font-size:11px;font-weight:700;}' +
    '.t5-px{display:flex;justify-content:space-between;align-items:baseline;margin:6px 0 2px;} .t5-px b{font-size:20px;} .t5-px span{font-size:11px;color:var(--dim);}' +
    '.t5-since{display:flex;justify-content:space-between;font-size:12px;margin:2px 0;} .t5-since span:first-child{color:var(--dim);}' +
    '.t5 svg{display:block;width:100%;height:42px;margin:6px 0;}' +
    '.t5-sum{font-size:11.5px;line-height:1.45;color:#cfe0ee;margin-top:4px;} .t5-ft{font-size:10.5px;color:var(--dim);margin-top:6px;line-height:1.5;}' +
    '.t5-sec{font-size:12px;color:var(--dim);font-weight:600;letter-spacing:.06em;text-transform:uppercase;margin:18px 0 8px;}' +
    '.t5-box{border:1px solid var(--line-bright);border-radius:10px;background:var(--panel);padding:10px 12px;}' +
    '.t5-box svg{display:block;width:100%;height:150px;}' +
    '.t5-tbl{width:100%;border-collapse:collapse;font-size:12px;} .t5-tbl th,.t5-tbl td{padding:6px 8px;text-align:center;border-bottom:1px solid var(--line);position:static;background:none;cursor:default;}' +
    '.t5-tbl th:first-child,.t5-tbl td:first-child{text-align:left;}' +
    '.t5-note{font-size:11px;color:var(--dim);line-height:1.55;margin-top:10px;}' +
    '.ak-box textarea{width:100%;min-height:110px;background:#000;color:#f2f2f2;border:1px solid var(--line-bright);border-radius:8px;font:inherit;font-size:14px;line-height:1.45;padding:10px;box-sizing:border-box;}' +
    '.ak-pr{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;} .ak-pr button{font-weight:400;font-size:11.5px;}' +
    '.ak-go{display:flex;flex-wrap:wrap;gap:8px;align-items:center;} .ak-go .go{font-size:14px;padding:10px 16px;background:#3d3191;}' +
    '.ak-msg{font-size:12px;color:var(--dim);} .ak-live{font-size:11.5px;color:var(--dim);margin-top:10px;line-height:1.5;}';
  document.head.appendChild(css);

  // the second row of on-page tabs
  var row = document.createElement('span'); row.className = 'modes'; row.id = 'spModes';
  row.innerHTML = VIEWS.map(function (v) { return '<button type="button" data-v="' + v[0] + '">' + v[1] + '</button>'; }).join('');
  modes.parentNode.insertBefore(row, modes.nextSibling);
  var view = document.createElement('div'); view.id = 'spView';
  view.innerHTML = VIEWS.map(function (v) { return '<div class="sp-pane" id="sp-' + v[0] + '"></div>'; }).join('');
  var pv = $('picksView'); pv.parentNode.insertBefore(view, pv.nextSibling);
  var cur = null, built = {};
  function ls(v) { try { if (v === undefined) return localStorage.getItem('jb_scanner_sp') || ''; if (v) localStorage.setItem('jb_scanner_sp', v); else localStorage.removeItem('jb_scanner_sp'); } catch (e) { return ''; } }
  function show(v) {
    cur = v || null; ls(cur);
    document.body.classList.toggle('spv', !!cur);
    [].forEach.call(row.children, function (b) { b.classList.toggle('on', b.dataset.v === cur); });
    if (cur) { $('moversBtn').classList.remove('on'); $('picksBtn').classList.remove('on'); }
    else { var pk = document.body.classList.contains('picks'); $('moversBtn').classList.toggle('on', !pk); $('picksBtn').classList.toggle('on', pk); }
    VIEWS.forEach(function (x) { $('sp-' + x[0]).classList.toggle('on', x[0] === cur); });
    if (cur && !built[cur]) { built[cur] = true; BUILD[VIEWS.filter(function (x) { return x[0] === cur; })[0][2] ? 'frame' : cur](cur); }
    if (cur === 'top5') loadTop5();
  }
  row.addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) show(b.dataset.v); });
  $('moversBtn').addEventListener('click', function () { show(null); });
  $('picksBtn').addEventListener('click', function () { show(null); });

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function pc(v, d) { return v == null ? '<span style="color:var(--dim)">–</span>' : '<span class="' + (v > 0 ? 'up' : v < 0 ? 'down' : '') + '">' + (v > 0 ? '+' : '') + Number(v).toFixed(d == null ? 2 : d) + '%</span>'; }
  function px(v) { return v == null ? '–' : v >= 1000 ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : v.toFixed(2); }
  function ago(ms) { var m = Math.round((Date.now() - ms) / 60000); return m < 1 ? 'Just Now' : m < 60 ? m + ' Min Ago' : m < 1440 ? Math.round(m / 60) + ' H Ago' : Math.round(m / 1440) + ' D Ago'; }
  function line(pts, w, h, col, zero) {
    if (!pts || pts.length < 2) return '';
    var lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
    if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (hi === lo) { hi += 1; lo -= 1; }
    var X = function (i) { return 2 + i / (pts.length - 1) * (w - 4); }, Y = function (v) { return h - 3 - (v - lo) / (hi - lo) * (h - 6); };
    var d = pts.map(function (p, i) { return (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(p).toFixed(1); }).join('');
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
      (zero ? '<line x1="0" x2="' + w + '" y1="' + Y(0).toFixed(1) + '" y2="' + Y(0).toFixed(1) + '" stroke="#2a3d52" stroke-dasharray="3 3"/>' : '') +
      '<path d="' + d + '" fill="none" stroke="' + col + '" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>';
  }

  // ---------------------------------------------------------------- Top 5
  var t5 = null;
  function loadTop5() {
    if (cur !== 'top5') return;
    fetch('/api/plus/top5', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { t5 = d; drawTop5(); }).catch(function () {});
  }
  function drawTop5() {
    var el = $('sp-top5'), d = t5;
    if (!d) { el.innerHTML = '<div class="empty">Loading the Top 5…</div>'; return; }
    var s = d.stats || {}, h = '';
    h += '<div class="t5-head"><div class="t5-grade" title="The board\'s grade from its 1-hour results (after 10 graded calls): A 65%+ right, B 58%+, C 50%+, D 42%+, else F"><b>' + esc(d.grade) + '</b><span>Board Grade<br>(1-Hour Calls)</span></div><div class="t5-hz">' +
      ['m30', 'h1', 'h4', 'd1'].map(function (k) {
        var x = s[k] || {};
        return '<div><small>After ' + esc((x.name || k).replace(/\b\w/g, function (c) { return c.toUpperCase(); })) + '</small><b>' + (x.rate == null ? '–' : x.rate + '%') + '</b><i>' +
          (x.n ? x.right + ' Of ' + x.n + ' Right · Avg ' + (x.avg >= 0 ? '+' : '') + x.avg.toFixed(2) + '%' : 'No Graded Calls Yet') + '</i></div>';
      }).join('') + '</div></div>';
    var top = d.top || [];
    if (!top.length) h += '<div class="empty">' + esc(d.error || 'No up or down Picks right now. The Top 5 fills in as soon as the scanner\'s Picks lean one way.') + '</div>';
    else h += '<div class="t5-grid">' + top.map(function (t) {
      var up = t.dir > 0, col = t.since == null ? '#6f8aa3' : t.since >= 0 ? '#00ff41' : '#ff3b30';
      return '<div class="t5 ' + (up ? 'up' : 'down') + '"><div class="t5-top"><span class="t5-rk">#' + t.rank + '</span><span class="t5-sym">' + esc(t.sym) + '</span>' +
        '<span class="t5-ln ' + (up ? 'up' : 'down') + '">' + (up ? '▲ Leaning Up' : '▼ Leaning Down') + '</span></div>' +
        '<div class="t5-px"><b>$' + px(t.now) + '</b><span>Picked $' + px(t.price) + ' · ' + ago(t.at) + '</span></div>' +
        '<div class="t5-since"><span>Since Picked (Its Way)</span>' + pc(t.since) + '</div>' +
        '<div class="t5-since"><span>Vs 4 PM Close</span>' + pc(t.vs_close) + '</div>' +
        line(t.trail, 200, 42, col) +
        '<div class="t5-since"><span>Performance Rank</span><b>' + t.perf_rank + ' Of ' + top.length + '</b></div>' +
        '<div class="t5-since"><span>Signals Agreeing</span><b>' + (t.agree || 1) + '</b></div>' +
        '<div class="t5-sum">' + esc(t.summary) + '</div>' +
        '<div class="t5-ft">' + (t.record ? 'Board Record On ' + esc(t.sym) + ': ' + t.record.right + ' Of ' + t.record.n + ' Right After 1 Hour' : 'First Graded Calls On ' + esc(t.sym) + ' Still Coming') + '</div></div>';
    }).join('') + '</div>';
    var days = d.days || [];
    h += '<div class="t5-sec">How The Board Did Over Time</div><div class="t5-box">' +
      (days.length >= 2 ? line(days.map(function (x) { return x.total; }), 600, 150, '#b39dff', true) +
        '<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--dim)"><span>' + esc(days[0].day) + '</span><span>Running Total Of 1-Hour Results (Its Way, %)</span><span>' + esc(days[days.length - 1].day) + '</span></div>'
        : '<div class="empty">The line starts after two days of graded calls.</div>') + '</div>';
    var rc = d.recent || [];
    if (rc.length) h += '<div class="t5-sec">Recent Calls</div><div class="t5-box" style="overflow-x:auto"><table class="t5-tbl"><tr><th>Symbol</th><th>Lean</th><th>Picked</th><th>Best Rank</th><th>30 Min</th><th>1 Hour</th><th>4 Hours</th><th>1 Day</th><th>Now</th></tr>' +
      rc.map(function (c) {
        var r = c.res || {};
        return '<tr><td><b>' + esc(c.sym) + '</b>' + (c.open ? ' <span style="color:#ffc400;font-size:10px">IN TOP 5</span>' : '') + '</td><td class="' + (c.dir > 0 ? 'up' : 'down') + '">' + (c.dir > 0 ? '▲ Up' : '▼ Down') + '</td>' +
          '<td>' + ago(c.at) + '</td><td>#' + (c.rank || '–') + '</td><td>' + pc(r.m30) + '</td><td>' + pc(r.h1) + '</td><td>' + pc(r.h4) + '</td><td>' + pc(r.d1) + '</td><td>' + pc(c.move) + '</td></tr>';
      }).join('') + '</table></div>';
    h += '<div class="t5-note">The Top 5 are the scanner\'s five strongest Picks that lean up or down (strongest first, the same rules as the Picks view, rechecked every minute). ' +
      'Each time a symbol joins the Top 5 the board writes down the price and grades itself with its own prices: the move <b>its way</b> after 30 minutes, 1 hour, 4 hours and 1 day ' +
      '(a call leaning down is right when the price falls). Moves under 0.02% don\'t count. <b>Performance Rank</b> orders today\'s five by how they did since they were picked. ' +
      'Research, not advice: chart patterns often fail.' + (d.at ? ' Updated ' + ago(d.at) + '.' : '') + '</div>';
    el.innerHTML = h;
  }
  setInterval(function () { if (!document.hidden && cur === 'top5') loadTop5(); }, 20000);

  // ---------------------------------------------------------------- Ask Claude (stripped back)
  var MAXURL = 6500;
  var PROMPTS = ['What is moving right now and why?', 'Which of these setups looks strongest for a 3x ETF, and what would prove it wrong?',
    'Is this overnight move likely to hold into the open?', 'Rate the Top 5 picks: which would you trust and which would you skip?'];
  function buildAsk(id) {
    var el = $('sp-' + id);
    el.innerHTML = '<div class="t5-box ak-box"><textarea id="akQ" placeholder="Ask about anything on the board, e.g. TQQQ is up 2% overnight while futures are flat: is that a gap I should fade at the open?"></textarea>' +
      '<div class="ak-pr">' + PROMPTS.map(function (p, i) { return '<button type="button" class="ghost" data-p="' + i + '">' + esc(p) + '</button>'; }).join('') + '</div>' +
      '<div class="ak-go"><button type="button" class="go" id="akGo">Ask Claude With Live Data ›</button><button type="button" class="ghost" id="akCopy">Copy Only</button><span class="ak-msg" id="akMsg"></span></div>' +
      '<div class="ak-live">Every time you press, the scanner\'s numbers at that moment go with your question: the biggest movers vs the 4 pm close, the latest flags, ' +
      'the Picks, the Top 5 and the board\'s market data (futures, moods, news and the rest of Ask Claude\'s data). It copies and opens the Claude app (free; nothing is sent until you press send there). ' +
      'The full page with choices is the Ask Claude tab.</div></div>';
    var q = $('akQ');
    try { q.value = localStorage.getItem('jb_scan_ask') || ''; } catch (e) {}
    q.oninput = function () { try { localStorage.setItem('jb_scan_ask', q.value); } catch (e) {} };
    [].forEach.call(el.querySelectorAll('[data-p]'), function (b) { b.onclick = function () { q.value = PROMPTS[+b.dataset.p]; q.oninput(); q.focus(); }; });
    $('akGo').onclick = function () { ask(true); };
    $('akCopy').onclick = function () { ask(false); };
  }
  function scannerText() {
    var S = window.__scan ? window.__scan() : null, out = [];
    if (!S) return '';
    var rows = Object.keys(S.rows || {}).map(function (k) { return S.rows[k]; }).filter(function (r) { return r.last_price != null; });
    rows.sort(function (a, b) { return Math.abs(b.vs_close || 0) - Math.abs(a.vs_close || 0); });
    out.push('## Scanner (' + (S.mode === 'stocks' ? 'stocks' : 'ETFs') + '): biggest moves vs the 4 pm close\n' + rows.slice(0, 20).map(function (r) {
      var w = r.window_open ? (r.last_price / r.window_open - 1) * 100 : null;
      return r.symbol + ' ' + r.last_price.toFixed(2) + ' (' + (r.vs_close == null ? '?' : (r.vs_close >= 0 ? '+' : '') + r.vs_close.toFixed(2) + '% vs close') +
        (w == null ? '' : ', ' + (w >= 0 ? '+' : '') + w.toFixed(2) + '% this 5-min window') + ')';
    }).join('\n'));
    var ev = (window.__evAll || []).slice(0, 10);
    if (ev.length) out.push('## Latest scanner flags\n' + ev.map(function (e) {
      return new Date(e.t_end).toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }) + ' ET ' + e.symbol + (e.session ? ' (' + e.session + ')' : '') + ': ' +
        (e.fired || []).map(function (f) { return f.reason.replace(/_/g, ' ') + ' ' + f.detail; }).join('; ');
    }).join('\n'));
    var P = S.picks && S.picks.picks;
    if (P && P.length) out.push('## Scanner Picks (daily chart rules, strongest first)\n' + P.slice(0, 8).map(function (p) {
      return p.symbol + ' $' + p.price.toFixed(2) + ' ' + p.lean + ': ' + p.summary + (p.signals || []).slice(0, 3).map(function (s) { return '\n  - ' + s.title + ': ' + s.why; }).join('');
    }).join('\n'));
    return out.join('\n\n');
  }
  function top5Text(d) {
    if (!d || !(d.top || []).length) return '';
    return '## The board\'s Top 5 right now (graded against price since picked)\n' + d.top.map(function (t) {
      return '#' + t.rank + ' ' + t.sym + ' leaning ' + (t.dir > 0 ? 'up' : 'down') + ', picked $' + px(t.price) + ', now $' + px(t.now) + ', ' + (t.since == null ? '?' : (t.since >= 0 ? '+' : '') + t.since.toFixed(2) + '% its way') + '. ' + (t.summary || '');
    }).join('\n') + (d.stats && d.stats.h1 && d.stats.h1.n ? '\nBoard record: ' + d.stats.h1.rate + '% of ' + d.stats.h1.n + ' calls right after 1 hour.' : '');
  }
  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (ok, bad) {
      var t = document.createElement('textarea'); t.value = text; t.style.position = 'fixed'; t.style.opacity = '0'; document.body.appendChild(t);
      t.focus(); t.select(); t.setSelectionRange(0, text.length);
      var done = false; try { done = document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(t); done ? ok() : bad(new Error('copy blocked'));
    });
  }
  function ask(open) {
    var q = $('akQ'), msg = $('akMsg');
    if (!q.value.trim()) { msg.textContent = 'Type a question first.'; return; }
    msg.textContent = 'Getting the live numbers…';
    var win = open ? window.open('about:blank', '_blank') : null;     // opened right away so the browser allows it
    Promise.all([
      fetch('/api/plus/claude', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
      fetch('/api/plus/top5', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (r) {
      var parts = ((r[0] && r[0].parts) || []).filter(function (p) { return p.on; });
      var NY = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date());
      var body = '\n\n---\nLive data from my trading board, New York time ' + NY + '. I mainly trade 3x leveraged and 3x inverse ETFs, often outside regular hours.\n\n' +
        [scannerText(), top5Text(r[1])].concat(parts.map(function (p) { return '## ' + p.title + '\n' + p.text; })).filter(Boolean).join('\n\n');
      var all = q.value + body, fits = encodeURIComponent(all).length <= MAXURL;
      var link = 'https://claude.ai/new?q=' + encodeURIComponent(fits ? all : q.value + '\n\n(My board\'s live data is on my clipboard; I will paste it in my next message.)');
      copy(all).then(function () { msg.textContent = open ? (fits ? 'Copied and opening Claude…' : 'Copied. Claude opens with your question: paste the board data as your next message.') : 'Copied. Paste it into Claude.'; },
        function () { msg.textContent = 'Could not copy (the browser blocked it); only what fits in the link goes.'; })
        .then(function () { if (win) { try { win.location.href = link; } catch (e) { location.href = link; } } });
    });
  }

  // ---------------------------------------------------------------- Night Desk parts
  var frames = [];
  function buildFrame(id) {
    var v = VIEWS.filter(function (x) { return x[0] === id; })[0];
    var f = document.createElement('iframe'); f.className = 'sp-frame'; f.title = v[1];
    f.src = 'night.html?only=' + v[2];
    $('sp-' + id).appendChild(f);
    var n = document.createElement('div'); n.className = 't5-note';
    n.textContent = 'The same part as on the Night Desk: switches and settings changed here change there too.';
    $('sp-' + id).appendChild(n);
    frames.push(f);
  }
  setInterval(function () {
    frames.forEach(function (f) { try { var d = f.contentDocument; if (d && d.body && f.offsetParent) f.style.height = Math.max(300, d.documentElement.scrollHeight) + 'px'; } catch (e) {} });
  }, 800);
  var BUILD = { top5: function () { drawTop5(); }, ask: buildAsk, frame: buildFrame };
  if (ls()) show(ls());
})();
