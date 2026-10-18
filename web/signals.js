/* Joseph's Board -- signals on the price boards (jb-signals).
 *
 * Adds to every stock tile (and every opened list row): a WHY? button that pops up
 * "Why is it moving?" (headlines, SEC filings, its sector, the market, social buzz, earnings),
 * plus small chips when there is something to see: EARNINGS (typical move vs move so far),
 * BUZZ (StockTwits chatter vs normal) and a fresh SEC filing. Data: /api/signals and /api/why
 * (gateway/signals.py). Self-contained; hooked in by one <script src="signals.js"> line.
 */
(function () {
  'use strict';
  if (window.__jbSignals) return;
  window.__jbSignals = true;
  var data = null;

  var css =
    '.sig{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:6px 0 2px;font-size:10.5px;}' +
    '.sig-why{font:inherit;font-size:10px;font-weight:700;letter-spacing:.5px;color:#cfe0ee;background:#0b2238;border:1px solid #164060;border-radius:4px;padding:2px 6px;cursor:pointer;}' +
    '.sig-why:hover{border-color:#6f8aa3;color:#fff;}' +
    '.sig-chip{border-radius:3px;padding:1px 5px;font-weight:700;letter-spacing:.3px;}' +
    '.sig-earn{background:#3a2a00;color:#ffc400;border:1px solid #7a5c00;} .sig-earn.big{background:#ffc400;color:#04101f;}' +
    '.sig-buzz{background:#2a1740;color:#d6a8ff;border:1px solid #5b3a8a;}' +
    '.sig-sec{background:#0e2a3a;color:#7fd6ff;border:1px solid #1e5a78;text-decoration:none;}' +
    '#sigWrap{position:fixed;inset:0;z-index:1100;background:rgba(0,0,0,.6);display:none;align-items:flex-start;justify-content:center;padding:50px 10px 10px;}' +
    '#sigWrap.open{display:flex;}' +
    '#sigBox{background:#071a30;border:1px solid #164060;border-radius:10px;max-width:560px;width:100%;max-height:100%;overflow-y:auto;padding:14px 16px 18px;color:#f2f2f2;font-size:12.5px;line-height:1.5;}' +
    '#sigBox h2{margin:0;font-size:15px;} #sigBox h3{font-size:11px;color:#6f8aa3;letter-spacing:.1em;text-transform:uppercase;margin:14px 0 4px;}' +
    '#sigBox .hd{display:flex;justify-content:space-between;align-items:center;gap:10px;}' +
    '#sigBox .x{background:transparent;border:1px solid #164060;color:#f2f2f2;font:inherit;font-size:16px;border-radius:6px;width:32px;height:32px;cursor:pointer;}' +
    '#sigBox ul{margin:4px 0;padding-left:18px;} #sigBox li{margin:3px 0;} #sigBox a{color:#7fd6ff;}' +
    '#sigBox .dim{color:#6f8aa3;font-size:11px;} #sigBox .up{color:#00ff41;} #sigBox .dn{color:#ff3b30;}' +
    '#sigBox .ans{background:#0b2238;border-left:3px solid #ffc400;padding:8px 10px;margin-top:10px;border-radius:4px;}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  var wrap = document.createElement('div'); wrap.id = 'sigWrap';
  wrap.innerHTML = '<div id="sigBox" role="dialog" aria-modal="true"></div>';
  document.body.appendChild(wrap);
  wrap.addEventListener('click', function (e) { if (e.target === wrap) wrap.classList.remove('open'); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') wrap.classList.remove('open'); });

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function pc(v) { return v == null ? '–' : '<span class="' + (v > 0 ? 'up' : v < 0 ? 'dn' : '') + '">' + (v > 0 ? '+' : '') + v.toFixed(2) + '%</span>'; }
  function ago(ms) { var m = Math.round((Date.now() - ms) / 60000); return m < 60 ? m + ' min ago' : m < 1440 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' d ago'; }

  function load() {
    if (location.protocol.indexOf('http') !== 0 || document.hidden) return;
    fetch('/api/signals', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) { data = d; paint(); } }).catch(function () {});
  }

  function chips(sym) {
    var out = '';
    if (!data) return out;
    var e = (data.earnings || {})[sym];
    if (e) {
      var big = e.now != null && e.typical && Math.abs(e.now) >= e.typical;
      out += '<span class="sig-chip sig-earn' + (big ? ' big' : '') + '" title="Earnings ' + esc(e.when || '') + '. Typical move = average size of its move around its last ' + (e.n || 0) + ' reports. Now = vs the 4 pm close.">EARNINGS' +
        (e.typical ? ' · typ ±' + e.typical + '%' : '') + (e.now != null ? ' · now ' + (e.now > 0 ? '+' : '') + e.now.toFixed(1) + '%' : '') + '</span>';
    }
    var b = (data.buzz || {})[sym];
    if (b && b.x && b.x >= 2) out += '<span class="sig-chip sig-buzz" title="StockTwits posts per hour vs its normal pace">BUZZ ' + b.x.toFixed(0) + 'x</span>';
    var f = (data.filings || []).filter(function (x) { return x.sym === sym && Date.now() - x.at < 86400000; })[0];
    if (f) out += '<a class="sig-chip sig-sec" target="_blank" rel="noopener" href="' + esc(f.link) + '" title="' + esc(f.text) + '">' + esc(f.form === '4' ? 'INSIDER BUY' : f.form.replace('SCHEDULE', 'SC')) + ' · ' + ago(f.at) + '</a>';
    return out;
  }

  function paint() {
    [].forEach.call(document.querySelectorAll('.tile[data-sym], .row[data-sym]'), function (el) {
      var sym = el.dataset.sym;
      if (!sym || /=F$/.test(sym)) return;
      var box = el.querySelector('.sig');
      if (!box) {
        box = document.createElement('div'); box.className = 'sig';
        var foot = el.classList.contains('tile') ? el.querySelector(':scope > .foot') : null;
        var det = el.querySelector('.detail');
        if (foot) el.insertBefore(box, foot); else if (det) det.appendChild(box); else return;
      }
      var html = '<button type="button" class="sig-why" data-sym="' + esc(sym) + '" title="Why is it moving?">WHY?</button>' + chips(sym);
      if (box._h !== html) { box._h = html; box.innerHTML = html; }
    });
  }

  document.addEventListener('click', function (e) {
    var b = e.target && e.target.closest && e.target.closest('.sig-why, .sig-sec');
    if (!b) return;
    e.stopPropagation();                         // don't also open the chart
    if (b.classList.contains('sig-sec')) return;
    e.preventDefault();
    why(b.dataset.sym);
  }, true);

  function why(sym) {
    var box = document.getElementById('sigBox');
    box.innerHTML = '<div class="hd"><h2>Why is ' + esc(sym) + ' moving?</h2><button class="x" aria-label="Close">×</button></div><p class="dim">Looking…</p>';
    box.querySelector('.x').onclick = function () { wrap.classList.remove('open'); };
    wrap.classList.add('open');
    fetch('/api/why?sym=' + encodeURIComponent(sym), { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
      var h = '<div class="hd"><h2>Why is ' + esc(sym) + ' moving?</h2><button class="x" aria-label="Close">×</button></div>';
      h += '<div class="dim">' + esc(d.session) + ' · vs 4 pm close ' + pc(d.move) + (d.overnight != null ? ' · overnight ' + pc(d.overnight) : '') + '</div>';
      h += '<div class="ans"><b>Best guess:</b><ul>' + (d.reasons || []).map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul></div>';
      h += '<h3>Market and sector</h3><ul>' + Object.keys(d.market || {}).map(function (k) { return '<li>' + esc(k) + ' ' + pc(d.market[k]) + '</li>'; }).join('') +
        (d.sector && d.sector.name ? '<li>Sector: ' + esc(d.sector.name) + (d.sector.etf ? ' (' + esc(d.sector.etf) + ') ' + pc(d.sector.move) : '') + '</li>' : '<li class="dim">Sector not known.</li>') + '</ul>';
      h += '<h3>Latest headlines</h3>' + (d.news && d.news.length ? '<ul>' + d.news.map(function (n) {
        return '<li><a href="' + esc(n.link) + '" target="_blank" rel="noopener">' + esc(n.title) + '</a> <span class="dim">' + esc(n.source) + ' · ' + ago(n.ts * 1000) + '</span></li>'; }).join('') + '</ul>' : '<p class="dim">None in the News tab yet.</p>');
      h += '<h3>SEC filings</h3>' + (d.filings && d.filings.length ? '<ul>' + d.filings.map(function (f) {
        return '<li><a href="' + esc(f.link) + '" target="_blank" rel="noopener">' + esc(f.text) + '</a> <span class="dim">' + ago(f.at) + '</span></li>'; }).join('') + '</ul>' : '<p class="dim">No new 8-K, insider buy or 13D seen since the Signals tab was installed.</p>');
      var b = d.buzz;
      h += '<h3>Social buzz (StockTwits)</h3>' + (b ? '<p>About ' + Math.round(b.rate) + ' posts/hour' + (b.x ? ' (' + b.x.toFixed(1) + 'x normal)' : ' (normal pace not known yet)') +
        (b.bull || b.bear ? '; of the last 30: ' + b.bull + ' bullish, ' + b.bear + ' bearish' : '') + '.</p>' : '<p class="dim">Not checked yet.</p>');
      if (d.earnings) h += '<h3>Earnings</h3><p>Reports ' + esc(d.earnings.date) + ' ' + esc(d.earnings.when) + (d.earnings.typical ? '. Typical move about ±' + d.earnings.typical + '% (last ' + d.earnings.n + ' reports)' : '') + '.</p>';
      h += '<p class="dim">Free sources only: Yahoo / Google News headlines, SEC EDGAR, StockTwits and the board\'s own prices. A best guess, not advice.</p>';
      var bx = document.getElementById('sigBox'); bx.innerHTML = h;
      if (window.JBWhyBox) window.JBWhyBox(sym, bx);      // jb-why: board math (its own prices and volume)
      bx.querySelector('.x').onclick = function () { wrap.classList.remove('open'); };
    }).catch(function () { document.getElementById('sigBox').querySelector('p').textContent = 'Could not ask the Mac right now.'; });
  }

  load(); setInterval(load, 60000);
  setInterval(paint, 2000);
})();
