/* Alex Hale's Board -- Market sentiment report on the MACRO tab (jb-sentiment).
 * A "Generate report" button makes one combined short / mid / long term report on the Mac
 * (gateway/sentiment.py, fixed rules, free data the board already has) and shows it here, with
 * Copy, Print / Save as PDF and Ask Claude. Hooked in by one <script src="sentiment.js"> line on macro.html.
 */
(function () {
  'use strict';
  if (location.protocol.indexOf('http') !== 0) return;
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function sg(v) { return v == null ? '–' : (v > 0 ? '+' : '') + v; }
  function cls(v) { return v == null ? 'c-d' : v >= 15 ? 'c-g' : v <= -15 ? 'c-r' : 'c-d'; }
  function col(s) { return s > 0.25 ? '#5dff8f' : s < -0.25 ? '#ff8a80' : '#9fb3c8'; }
  var NAMES = [['short', 'Short term', 'next 1-3 days'], ['mid', 'Mid term', 'next 2-8 weeks'], ['long', 'Long term', 'next 3-12 months']];

  var css = document.createElement('style');
  css.textContent =
    '#sent .sbtn{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0 10px}' +
    '#sent .hz{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:8px;margin-top:10px}' +
    '#sent .hz .card{padding:10px 12px}' +
    '#sent .hh{display:flex;justify-content:space-between;align-items:baseline;gap:6px}' +
    '#sent .hh b{font-size:13px;letter-spacing:.06em;text-transform:uppercase}' +
    '#sent .hs{font-size:22px;font-weight:700}' +
    '#sent .it{display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--line);font-size:12px;line-height:1.4}' +
    '#sent .it:last-child{border-bottom:0}' +
    '#sent .it .sc{min-width:44px;text-align:right;font-weight:700}' +
    '#sent .it .nm{color:var(--white)} #sent .it .vl{color:#7fd6ff} #sent .it .rd{color:var(--dim);display:block}' +
    '#sent .story{font-size:13.5px;line-height:1.5;margin:8px 0 0}' +
    '#sent .ev{padding:5px 0}' +
    '@media print{html,body{background:#fff!important;color:#000!important}' +
    ' body *{visibility:hidden} #sent,#sent *{visibility:visible;color:#000!important;background:#fff!important;border-color:#bbb!important;box-shadow:none!important}' +
    ' #sent{position:absolute;left:0;top:0;width:100%} #sent .sbtn,#sent .noprint{display:none!important}' +
    ' #sent .gauge{background:linear-gradient(90deg,#c0504a,#ddd 45%,#ddd 55%,#3fbf6a)!important} #sent .gauge i{background:#000!important}' +
    ' #sent .hz{display:block} #sent .hz .card{margin-bottom:8px;break-inside:avoid}}';
  document.head.appendChild(css);

  var box = document.createElement('div');
  box.id = 'sent';
  box.innerHTML = '<h2>Market sentiment report</h2>' +
    '<div class="card"><p class="note" style="margin:6px 0">One report on the market\'s mood over three time frames: <b>short term</b> (next 1-3 days), ' +
    '<b>mid term</b> (next 2-8 weeks) and <b>long term</b> (next 3-12 months), combined into one score. Made on the Mac by fixed rules from free data the board ' +
    'already has (futures, VIX, trends, breadth, Fed odds, positioning, headlines, FRED). Rules, not advice.</p>' +
    '<div class="sbtn"><button id="sentGo">Generate report</button>' +
    '<button class="ghost" id="sentCopy" disabled>Copy text</button>' +
    '<button class="ghost" id="sentPrint" disabled>Print / Save as PDF</button>' +
    '<a class="chip c-p" id="sentAsk" style="display:none;font-size:12px;padding:5px 9px;text-decoration:none">Ask Claude about this report ›</a>' +
    '<span class="msg" id="sentMsg"></span></div>' +
    '<div id="sentBody"><p class="note">No report yet. Press <b>Generate report</b>.</p></div></div>' +
    '<div id="sentHist" class="noprint"></div>';
  var anchor = document.getElementById('top');
  if (anchor) anchor.parentNode.insertBefore(box, anchor.nextSibling); else (document.querySelector('.wrap') || document.body).appendChild(box);

  var last = null;
  function $(id) { return document.getElementById(id); }
  function api(sub, post) {
    return fetch('/api/plus/sentiment' + (sub ? '/' + sub : ''), post ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (r.status === 404) throw new Error('missing');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function draw(rep) {
    last = rep;
    if (!rep) return;
    var ch = rep.change || {};
    function delta(k) { var v = ch[k]; return v == null || !rep.change_from ? '' : ' <span class="note">(' + (v > 0 ? '▲ +' + v : v < 0 ? '▼ ' + v : 'no change') + ')</span>'; }
    var h = '<div class="row2" style="margin:4px 0"><span class="big">' + sg(rep.combined) + '</span>' +
      '<span class="chip ' + cls(rep.combined) + '" style="font-size:13px">' + esc(String(rep.label).toUpperCase()) + '</span>' + delta('combined') +
      '<span class="note">made ' + esc(rep.when) + '</span></div>' +
      (rep.combined != null ? '<div class="gauge"><i style="left:' + ((rep.combined + 100) / 2) + '%"></i></div><div class="gl"><span>-100 bearish</span><span>neutral</span><span>bullish +100</span></div>' : '') +
      '<p class="story">' + esc(rep.story) + '</p>' +
      '<div class="hz">' + NAMES.map(function (n) {
        var x = rep.horizons[n[0]] || { items: [], missing: [] };
        return '<div class="card"><div class="hh"><b>' + n[1] + '</b><span class="note">' + n[2] + '</span></div>' +
          '<div class="row2" style="margin:4px 0"><span class="hs">' + sg(x.score) + '</span><span class="chip ' + cls(x.score) + '">' + esc(String(x.label).toUpperCase()) + '</span>' + delta(n[0]) + '</div>' +
          (x.score != null ? '<div class="gauge" style="height:8px;margin:6px 0 8px"><i style="left:' + ((x.score + 100) / 2) + '%;top:-4px;height:16px"></i></div>' : '') +
          x.items.map(function (i) {
            return '<div class="it"><span class="sc" style="color:' + col(i.s) + '">' + (i.s > 0 ? '+' : '') + i.s.toFixed(2) + '</span><span><span class="nm">' + esc(i.name) +
              ':</span> <span class="vl">' + esc(i.value) + '</span><span class="rd">' + esc(i.reading) + '</span></span></div>';
          }).join('') +
          (x.missing && x.missing.length ? '<p class="note" style="margin:6px 0 0">Not available yet: ' + x.missing.map(esc).join('; ') + '</p>' : '') + '</div>';
      }).join('') + '</div>' +
      '<div class="card" style="margin-top:8px"><b>3x funds:</b> ' + esc(rep.letf) + '</div>' +
      (rep.events && rep.events.length ? '<div class="card" style="margin-top:8px"><b>Big events in the next 3 days</b> <span class="note">(can change the short term fast)</span>' +
        rep.events.map(function (e) { return '<div class="ev"><span class="note" style="display:inline-block;min-width:150px">' + esc(e.when) + '</span> ' + esc(e.title) + '</div>'; }).join('') + '</div>' : '') +
      '<p class="note">Each line scores -1 (bearish) to +1 (bullish) by a fixed rule; a time frame\'s score is their average × 100; the combined score is the three time frames averaged equally. ' +
      'Bullish = +40 or more, Leaning bullish = +15, Leaning bearish = -15, Bearish = -40.' + (rep.change_from ? ' ▲▼ = change since the report of ' + esc(rep.change_from) + '.' : '') +
      (rep.prices_as_of ? ' Daily prices up to the close of ' + esc(rep.prices_as_of) + '.' : '') + '</p>';
    $('sentBody').innerHTML = h;
    $('sentCopy').disabled = false;
    $('sentPrint').disabled = false;
    var q = 'Here is the market sentiment report from my board (short, mid and long term, made by fixed rules). What stands out, what could change it, and what does it mean for 3x leveraged and 3x inverse index ETFs?';
    $('sentAsk').href = 'claude.html?parts=sentiment,market,macro&q=' + encodeURIComponent(q);
    $('sentAsk').style.display = '';
  }

  function drawHist(list) {
    if (!list || list.length < 2) { $('sentHist').innerHTML = ''; return; }
    $('sentHist').innerHTML = '<h2>Earlier reports</h2><div class="card tbl"><table><tr><th>Made</th><th>Short</th><th>Mid</th><th>Long</th><th>Combined</th></tr>' +
      list.slice(0, 10).map(function (r) {
        var s = r.scores || {};
        return '<tr><td>' + esc(r.when) + '</td>' + ['short', 'mid', 'long', 'combined'].map(function (k) {
          var v = s[k]; return '<td class="' + (v >= 15 ? 'up' : v <= -15 ? 'dn' : '') + '">' + sg(v) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</table></div>';
  }

  function load() {
    api('').then(function (d) { draw(d.last); drawHist(d.reports); }).catch(function (e) {
      if (e.message === 'missing') $('sentMsg').textContent = 'The report part is not running yet: restart the board (Health tab).';
    });
  }

  $('sentGo').onclick = function () {
    var b = this;
    b.disabled = true; b.textContent = 'Making the report…'; $('sentMsg').textContent = '';
    api('generate', true).then(function (rep) {
      if (rep.error) { $('sentMsg').innerHTML = '<span class="err">' + esc(rep.error) + '</span>'; return; }
      draw(rep);
      $('sentMsg').textContent = 'Done.';
      api('').then(function (d) { drawHist(d.reports); }).catch(function () {});
    }).catch(function (e) {
      $('sentMsg').innerHTML = '<span class="err">' + (e.message === 'missing' ? 'The report part is not running yet: restart the board (Health tab).' : 'Could not reach the Mac.') + '</span>';
    }).then(function () { b.disabled = false; b.textContent = 'Generate report'; });
  };
  $('sentCopy').onclick = function () {
    if (!last || !last.text) return;
    var done = function () { $('sentMsg').textContent = 'Copied.'; };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(last.text).then(done, fallback); else fallback();
    function fallback() {
      var t = document.createElement('textarea'); t.value = last.text; document.body.appendChild(t); t.select();
      try { document.execCommand('copy'); done(); } catch (e) { $('sentMsg').textContent = 'Could not copy.'; }
      t.remove();
    }
  };
  $('sentPrint').onclick = function () { window.print(); };
  load();
})();
