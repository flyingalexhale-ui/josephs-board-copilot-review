/* Alex Hale's Board -- plain-English boxes on the algo tabs (jb-desk). Adds one card; nothing else
 * on the tab changes. Two boxes in your own words, saved on the Mac:
 *   Algorithm Themes   what you want the algo to do
 *   Backtesting Ideas  how you want it tested
 * Copy for Claude, Build It: your words + this algo's strategy file + the strategy format + the last
 *   backtest, asking Claude to write a strategy file. Paste what Claude writes into "Paste Claude's
 *   strategy": the Mac checks it with the algo's own code and saves it PAPER ONLY as
 *   strategies/claude-<name>.yaml (it then shows in the Run backtest list; the running algo is not switched).
 * Copy for Claude, Check Before We Run: the same plus the paper report, asking for a review first.
 * Free: it copies and opens the Claude app, like Ask Claude. Data: /api/plus/algonotes/<algo>, /api/algo/<algo>.
 * Hooked in by one <script src="algo-notes.js"> line on algo.html. */
(function () {
  'use strict';
  var algo = (new URLSearchParams(location.search).get('name') || '').toLowerCase() === 'ibkr' ? 'ibkr' : 'webull';
  var NAME = algo === 'ibkr' ? 'IBKR' : 'Webull', MAXURL = 6500;
  var grid = document.querySelector('.grid'), rep = document.getElementById('rep-card');
  if (!grid) return;
  var css = document.createElement('style');
  css.textContent =
    '#anCard .an2{display:grid;grid-template-columns:1fr 1fr;gap:12px;}' +
    '#anCard label.an-l{display:block;font-size:12px;font-weight:700;letter-spacing:.06em;margin:4px 0 6px;color:var(--cyan,#00e5ff);}' +
    '#anCard textarea{width:100%;min-height:120px;background:#000;color:#f2f2f2;border:1px solid #164060;border-radius:6px;font:inherit;font-size:13px;line-height:1.45;padding:8px;box-sizing:border-box;}' +
    '#anCard .an-b{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px;}' +
    '#anCard .an-b button{font:inherit;font-size:12px;font-weight:700;border-radius:6px;padding:8px 12px;cursor:pointer;border:1px solid #3d3191;background:#3d3191;color:#fff;}' +
    '#anCard .an-b button.gh{background:#0b2238;border-color:#164060;}' +
    '#anCard .an-m{font-size:12px;color:var(--dim,#6f8aa3);} #anCard .an-m.err{color:#ff8a80;}' +
    '#anCard details{margin-top:12px;border-top:1px solid #0d2740;padding-top:8px;} #anCard summary{cursor:pointer;font-size:12px;font-weight:700;}' +
    '#anCard .an-n{font-size:11px;color:var(--dim,#6f8aa3);line-height:1.5;margin-top:8px;}' +
    '@media (max-width:700px){#anCard .an2{grid-template-columns:1fr;}}';
  document.head.appendChild(css);
  var card = document.createElement('div'); card.className = 'card wide'; card.id = 'anCard';
  card.innerHTML = '<h2>Plain-English Algo Notes <span class="tag">for Claude</span></h2>' +
    '<div class="an2"><div><label class="an-l" for="anT">Algorithm Themes</label><textarea id="anT" placeholder="In your own words: what should this algo trade, when, and why? e.g. Buy TQQQ in the evening when Nasdaq futures are up since the 4 pm close and it holds above the close; sell at the 9:30 open or if it drops 2%."></textarea></div>' +
    '<div><label class="an-l" for="anB">Backtesting Ideas</label><textarea id="anB" placeholder="How should it be tested? e.g. Test the last 10 days, overnight only, with a 0.1% extra cost per fill; compare it with simply holding TQQQ; try stops of 1%, 2% and 3%."></textarea></div></div>' +
    '<div class="an-b"><button type="button" class="gh" id="anSave">Save</button>' +
    '<button type="button" id="anBuild">Copy for Claude: Build It ›</button>' +
    '<button type="button" id="anCheck">Copy for Claude: Check Before We Run ›</button><span class="an-m" id="anMsg"></span></div>' +
    '<details><summary>Paste the strategy Claude wrote</summary>' +
    '<textarea id="anY" style="min-height:140px;margin-top:8px" placeholder="Paste the whole strategy file from Claude here (it starts with name: and symbol:)."></textarea>' +
    '<div class="an-b"><label style="font-size:12px"><input type="checkbox" id="anBt" checked> Run a backtest after saving</label>' +
    '<button type="button" id="anPaste">Check and Save (Paper Only)</button><span class="an-m" id="anPMsg"></span></div></details>' +
    '<div class="an-n">The buttons copy your words together with this algo\'s strategy file, the strategy format and the last backtest, then open the Claude app with it ' +
    '(free, like Ask Claude; nothing is sent until you press send there). A strategy pasted back is checked with the algo\'s own code and saved as paper only ' +
    '(claude-&lt;name&gt;.yaml); it appears in the Run backtest list. It never switches what the algo is running and can never place a live order.</div>';
  grid.insertBefore(card, rep || null);
  var $ = function (id) { return document.getElementById(id); }, info = null, saveT = null;
  function api(path, body) {
    var o = body ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { credentials: 'same-origin', cache: 'no-store' };
    return fetch('/api/plus/algonotes/' + algo + path, o).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || 'HTTP ' + r.status); return j; }); });
  }
  function load() { return api('').then(function (d) { info = d; if (!$('anT').value) $('anT').value = d.themes || ''; if (!$('anB').value) $('anB').value = d.backtest || ''; }); }
  function save(quiet) {
    return api('/save', { themes: $('anT').value, backtest: $('anB').value }).then(function () { if (!quiet) $('anMsg').textContent = 'Saved on the Mac.'; })
      .catch(function (e) { $('anMsg').textContent = 'Could not save: ' + e.message; });
  }
  ['anT', 'anB'].forEach(function (id) { $(id).addEventListener('input', function () { clearTimeout(saveT); saveT = setTimeout(function () { save(true); }, 1500); }); });
  $('anSave').onclick = function () { save(false); };
  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (ok, bad) {
      var t = document.createElement('textarea'); t.value = text; t.style.position = 'fixed'; t.style.opacity = '0'; document.body.appendChild(t);
      t.focus(); t.select(); t.setSelectionRange(0, text.length);
      var done = false; try { done = document.execCommand('copy'); } catch (e) {}
      document.body.removeChild(t); done ? ok() : bad(new Error('copy blocked'));
    });
  }
  function openClaude(text, short) {
    var fits = encodeURIComponent(text).length <= MAXURL;
    var link = 'https://claude.ai/new?q=' + encodeURIComponent(fits ? text : short + '\n\n(The details are on my clipboard; I will paste them in my next message.)');
    copy(text).then(function () { $('anMsg').textContent = fits ? 'Copied and opening Claude…' : 'Copied. Claude opens with the question: paste the details as your next message.'; })
      .catch(function () { $('anMsg').textContent = 'Opening Claude (copy was blocked, so only what fits in the link is included)…'; })
      .then(function () { var a = document.createElement('a'); a.href = link; a.target = '_blank'; a.rel = 'noopener'; document.body.appendChild(a); a.click(); a.remove(); });
  }
  function bundle(withReport) {
    return Promise.all([load().catch(function () {}), fetch('/api/algo/' + algo, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; })])
      .then(function (r) {
        var d = r[1] || {}, bt = d.backtest || null, lv = d.live || null, out = [];
        out.push('## My algorithm themes (in my words)\n' + ($('anT').value.trim() || '(none written yet)'));
        out.push('## My backtesting ideas (in my words)\n' + ($('anB').value.trim() || '(none written yet)'));
        if (info && info.yaml) out.push('## The strategy file this algo runs now (' + info.current + ')\n```yaml\n' + info.yaml.trim() + '\n```');
        if (info && info.strategies) out.push('## Other strategy files it has\n' + info.strategies.join(', '));
        if (bt) out.push('## Last backtest (' + (bt.strategy || '') + ')\n' + JSON.stringify({ metrics: bt.metrics, trades: (bt.trades || []).slice(-12) }).slice(0, 3000));
        if (withReport && lv) out.push('## Paper trading now\n' + JSON.stringify({ mode: lv.mode, strategy: lv.strategy, symbol: lv.symbol, position: lv.position, equity: lv.equity, return_pct: lv.return_pct, trades: (lv.trades || []).slice(-12) }).slice(0, 3000));
        if (info && info.format) out.push('## The strategy file format the algo understands\n' + info.format.trim());
        return out.join('\n\n');
      });
  }
  $('anBuild').onclick = function () {
    save(true);
    bundle(false).then(function (b) {
      var q = 'I run a ' + NAME + ' trading algo on my own board (paper first; I mainly trade 3x leveraged and inverse ETFs, often outside regular hours). ' +
        'Please turn my themes below into ONE strategy file for this algo, in the exact format described at the end, using only the indicators that format lists. ' +
        'Reply with the whole file in one code block, then a short plain-English summary of what it does, and how to backtest it based on my backtesting ideas ' +
        '(the algo backtests on its last 10 days of 1-minute prices). Keep confirm_live: false. I have no programming experience.';
      openClaude(q + '\n\n' + b, q);
    });
  };
  $('anCheck').onclick = function () {
    save(true);
    bundle(true).then(function (b) {
      var q = 'Before I run my ' + NAME + ' trading algo, please review it like a careful risk manager. Does the strategy file match my themes below? ' +
        'What could go wrong (gaps, thin overnight trading, 3x ETF decay, stops that never fill, too many trades)? Is the last backtest meaningful or too short to trust? ' +
        'What exactly should I test next, and what would you change? Answer in plain English with a clear go / wait / no-go at the top. I have no programming experience.';
      openClaude(q + '\n\n' + b, q);
    });
  };
  $('anPaste').onclick = function () {
    var m = $('anPMsg'); m.className = 'an-m'; m.textContent = 'Checking with the algo\'s own code…';
    api('/paste', { yaml: $('anY').value, backtest: $('anBt').checked }).then(function (r) {
      m.textContent = 'Saved as ' + r.file + ' (paper only).' + (r.backtest === 'started' ? ' Backtest started: results show in Last backtest.' : r.backtest ? ' Backtest: ' + r.backtest : '');
    }).catch(function (e) { m.className = 'an-m err'; m.textContent = e.message; });
  };
  load().catch(function (e) { $('anMsg').textContent = 'Notes not available: ' + e.message; });
})();
