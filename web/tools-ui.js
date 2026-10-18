/* Alex Hale's Board -- command bar, keyboard keys and "what changed" banner (jb-tools).
 *
 * Command bar (idea 28): press / on the Mac (or tap ⌕ next to the board's name; on the phone page
 * it is the search box under the clock). Type then Enter:
 *   TQQQ            open its chart (any symbol, also futures like NQ=F and indexes like ^N225)
 *   TQQQ buy / sell open the chart with the order ticket on buy or sell
 *   TQQQ news       its latest headlines
 *   flip SOXL       the ticket with the Flip button (SOXL <-> SOXS)
 *   world, health, night, signals ... any tab name
 * Keys (idea 31, Mac only, not while typing): 1-9 and 0 switch tabs, B / S open buy / sell on the
 * selected symbol (the last tile you clicked or pointed at), Esc closes pop-ups, ? shows the list.
 * Banner (idea 72): what happened since you last looked (fills, alerts, big moves, news on what you hold).
 * One <script src="tools-ui.js"> line on index.html and phone.html. Nothing else on the page moves.
 */
(function () {
  'use strict';
  if (window.__jbToolsUi) return;
  window.__jbToolsUi = true;
  var PHONE = !document.getElementById('panes');
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function ls(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  var st = document.createElement('style');
  st.textContent = '#cbWrap{position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.55);display:none;align-items:flex-start;justify-content:center;padding-top:12vh}#cbWrap.open{display:flex}' +
    '#cbBox{width:min(560px,92vw);background:#071a30;border:1px solid #164060;border-radius:10px;box-shadow:0 10px 40px rgba(0,0,0,.6);overflow:hidden;font-family:inherit}' +
    '#cbIn,#cbPh{width:100%;background:#000;color:#f2f2f2;border:0;border-bottom:1px solid #164060;font:inherit;font-size:16px;padding:12px 14px;outline:none;text-transform:uppercase;box-sizing:border-box}' +
    '#cbList{max-height:50vh;overflow:auto;font-size:13px}#cbList div{padding:8px 14px;cursor:pointer;color:#cfe0ee}#cbList div.on,#cbList div:hover{background:#0b2238}#cbList small{color:#6f8aa3;margin-left:8px}' +
    '#cbOut{padding:10px 14px;font-size:12px;color:#cfe0ee;max-height:50vh;overflow:auto}#cbOut a{color:#7fd6ff}' +
    '.cbBtn{background:transparent;border:0;color:inherit;font:inherit;cursor:pointer;padding:0 0 0 8px;font-size:13px}' +
    '#chgBan{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:9998;width:min(560px,94vw);background:#071a30;border:1px solid #164060;border-left:3px solid #7fd6ff;border-radius:8px;padding:10px 12px;font-size:12px;color:#f2f2f2;box-shadow:0 8px 30px rgba(0,0,0,.5)}' +
    '#chgBan b{font-size:11px;letter-spacing:.08em;color:#9fb3c8}#chgBan .x{float:right;cursor:pointer;color:#9fb3c8;font-size:16px;line-height:1}#chgBan ul{margin:6px 0 0 16px;padding:0;max-height:30vh;overflow:auto}#chgBan li{margin:2px 0}' +
    '#phSearch{margin:4px 0 0}#phSearch input::placeholder{text-transform:none;font-size:14px}#phSearch input{width:100%;box-sizing:border-box;background:#000;color:#f2f2f2;border:1px solid #164060;border-radius:8px;font:inherit;font-size:16px;padding:9px 10px;text-transform:uppercase}';
  document.head.appendChild(st);

  // ------------------------------------------------------------------ tabs (full board only)
  function tabs() { return [].slice.call(document.querySelectorAll('.tab')).filter(function (b) { return !b.hidden; }); }
  function tabBtn(name) { name = name.toLowerCase(); return tabs().filter(function (b) { return b.dataset.tab === name || b.textContent.trim().toLowerCase() === name || b.textContent.trim().toLowerCase().replace(/\s+/g, '') === name.replace(/\s+/g, ''); })[0]; }
  function frame(name) {
    var b = tabBtn(name); if (!b) return null;
    return [].filter.call(document.querySelectorAll('#panes iframe'), function (f) { return f.getAttribute('src') === b.dataset.src; })[0] || null;
  }
  function send(tab, msg) {
    var b = tabBtn(tab); if (!b) return;
    b.click();
    var tries = 0;
    (function go() {
      var f = frame(tab);
      var ready = f && f.contentWindow && f.contentWindow.__jbPlus2 && f.contentWindow.__jb && f.contentWindow.__jb.state;
      if (ready) { f.contentWindow.postMessage(msg, location.origin); return; }
      if (++tries < 60) setTimeout(go, 250);
    })();
  }
  function boardFor(sym) {
    var order = [ls('jb_dash_tab'), 'ibkr', 'webull'].filter(function (t) { return t === 'ibkr' || t === 'webull'; });
    for (var i = 0; i < order.length; i++) {
      var f = frame(order[i]);
      try { if (f && f.contentWindow.__jb && f.contentWindow.__jb.state.symbols.indexOf(sym) >= 0) return order[i]; } catch (e) {}
    }
    return tabBtn('ibkr') ? 'ibkr' : 'webull';
  }

  // ------------------------------------------------------------------ the command bar
  var wrap = document.createElement('div'); wrap.id = 'cbWrap';
  wrap.innerHTML = '<div id="cbBox"><input id="cbIn" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="TQQQ · TQQQ buy · TQQQ news · flip SOXL · world"><div id="cbList"></div><div id="cbOut"></div></div>';
  document.body.appendChild(wrap);
  var inp = wrap.querySelector('#cbIn'), list = wrap.querySelector('#cbList'), out = wrap.querySelector('#cbOut'), sel = 0, opts = [];
  function openBar(v) { wrap.classList.add('open'); inp.value = v || ''; out.innerHTML = ''; suggest(); setTimeout(function () { try { window.focus(); } catch (e) {} inp.focus(); }, 0); }
  function closeBar() { wrap.classList.remove('open'); inp.blur(); }
  wrap.addEventListener('click', function (e) { if (e.target === wrap) closeBar(); });
  function symbols() {
    var s = {};
    ['ibkr', 'webull'].forEach(function (t) { try { (frame(t).contentWindow.__jb.state.symbols || []).forEach(function (x) { s[x] = 1; }); } catch (e) {} });
    return Object.keys(s).sort();
  }
  function parse(v) {
    var w = v.trim().split(/\s+/).filter(Boolean), a = (w[0] || '').toLowerCase();
    if (!w.length) return null;
    if (a === 'flip' && w[1]) return { act: 'flip', sym: w[1].toUpperCase() };
    var rest = w.slice(1).join(' ').toLowerCase();
    if (!rest && tabBtn(v.trim())) return { act: 'tab', tab: v.trim() };
    if (!rest && /^(night|nightdesk|trading|mytrading|algo)$/.test(a)) return { act: 'tab', tab: a === 'mytrading' ? 'trading' : a === 'nightdesk' ? 'night' : a === 'algo' ? 'algo-ibkr' : a };
    if (!rest && /^(phone|system|syscheck|check)$/.test(a)) return { act: 'page', url: a === 'phone' ? 'phone.html' : 'syscheck.html' };
    var sym = w[0].toUpperCase().replace(/[^A-Z0-9.=^-]/g, '');
    if (!sym) return null;
    if (/^(buy|b)$/.test(rest)) return { act: 'chart', sym: sym, side: 'buy' };
    if (/^(sell|s)$/.test(rest)) return { act: 'chart', sym: sym, side: 'sell' };
    if (/^news|^n$/.test(rest)) return { act: 'news', sym: sym };
    if (/^note/.test(rest)) return { act: 'note', sym: sym, text: v.trim().split(/\s+/).slice(2).join(' ') };
    return { act: 'chart', sym: sym };
  }
  function label(c) {
    return c.act === 'tab' ? 'Open the ' + c.tab + ' tab' : c.act === 'page' ? 'Open ' + c.url : c.act === 'flip' ? 'Flip ' + c.sym + ' (ticket with the Flip button)' :
      c.act === 'news' ? c.sym + ' news' : c.act === 'note' ? (c.text ? 'Save note on ' + c.sym + ': ' + c.text : 'Read the note on ' + c.sym) :
      c.side ? c.sym + ' chart with a ' + c.side + ' ticket' : c.sym + ' chart';
  }
  function suggest() {
    var v = inp.value.trim(), up = v.toUpperCase(), first = parse(v);
    opts = first ? [first] : [];
    if (v && !/\s/.test(v)) {
      symbols().filter(function (s) { return s.indexOf(up) === 0 && s !== up; }).slice(0, 5).forEach(function (s) { opts.push({ act: 'chart', sym: s }); });
      tabs().filter(function (b) { return b.textContent.trim().toLowerCase().indexOf(v.toLowerCase()) === 0; }).slice(0, 3).forEach(function (b) { var t = { act: 'tab', tab: b.dataset.tab }; if (!first || first.act !== 'tab') opts.push(t); });
    }
    if (!v) opts = [{ hint: 'TQQQ', t: 'chart' }, { hint: 'TQQQ buy', t: 'chart with a buy ticket' }, { hint: 'SOXL news', t: 'headlines' }, { hint: 'flip SOXL', t: 'switch to the other side' }, { hint: 'NQ=F', t: 'Nasdaq futures chart (Yahoo)' }, { hint: 'TQQQ note earnings Thu', t: 'save a note' }, { hint: 'world', t: 'any tab by name' }];
    sel = 0;
    list.innerHTML = opts.map(function (o, i) { return '<div data-i="' + i + '" class="' + (i === 0 && !o.hint ? 'on' : '') + '">' + (o.hint ? esc(o.hint) + '<small>' + esc(o.t) + '</small>' : esc(label(o))) + '</div>'; }).join('');
  }
  list.addEventListener('click', function (e) { var d = e.target.closest('[data-i]'); if (!d) return; var o = opts[+d.dataset.i]; if (o.hint) { inp.value = o.hint; suggest(); inp.focus(); } else run(o); });
  inp.addEventListener('input', suggest);
  inp.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeBar(); e.preventDefault(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, Math.min(opts.length - 1, sel + (e.key === 'ArrowDown' ? 1 : -1))); [].forEach.call(list.children, function (c, i) { c.classList.toggle('on', i === sel); }); return; }
    if (e.key === 'Enter') { e.preventDefault(); var o = opts[sel]; if (o && !o.hint) run(o); }
  });
  function api(p, body) {
    return fetch('/api/plus/' + p, body ? { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.json(); });
  }
  function showNews(sym, el) {
    el.innerHTML = 'Loading ' + esc(sym) + ' news…';
    Promise.all([fetch('/api/news', { credentials: 'same-origin' }).then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; }),
      api('feeds/top').catch(function () { return {}; })]).then(function (r) {
      var seen = {}, items = [];
      (r[0].items || []).forEach(function (i) { if ((i.syms || []).indexOf(sym) >= 0) items.push({ ts: i.ts * 1000, title: i.title, url: i.url || i.link, src: i.source || i.src }); });
      (r[1].items || []).forEach(function (i) { if (i.sym === sym) items.push(i); });
      items = items.filter(function (i) { var k = (i.title || '').toLowerCase().slice(0, 60); if (seen[k]) return false; seen[k] = 1; return true; }).sort(function (a, b) { return b.ts - a.ts; }).slice(0, 15);
      el.innerHTML = '<b>' + esc(sym) + ' news</b>' + (items.length ? items.map(function (i) {
        return '<div style="margin:6px 0">' + (i.url ? '<a href="' + esc(i.url) + '" target="_blank" rel="noopener">' + esc(i.title) + '</a>' : esc(i.title)) + '<br><small style="color:#6f8aa3">' + esc(i.src || '') + ' · ' + new Date(i.ts).toLocaleString() + '</small></div>';
      }).join('') : '<p>No recent headlines (the News tab only follows symbols on your boards and holdings).</p>');
    });
  }
  function run(c) {
    if (c.act === 'tab') { if (PHONE) location.href = './#' + (tabBtn(c.tab) ? tabBtn(c.tab).dataset.tab : c.tab); else { var b = tabBtn(c.tab); if (b) b.click(); } closeBar(); return; }
    if (c.act === 'page') { location.href = c.url; return; }
    if (c.act === 'news') { list.innerHTML = ''; showNews(c.sym, out); return; }
    if (c.act === 'note') {
      if (c.text) api('tools/notes', { sym: c.sym, text: c.text }).then(function () { out.textContent = 'Saved the note on ' + c.sym + '.'; }).catch(function () { out.textContent = 'Could not save (is jb-tools installed?)'; });
      else api('tools/notes').then(function (d) { var n = (d.notes || {})[c.sym]; out.innerHTML = n ? '<b>' + esc(c.sym) + '</b><p style="white-space:pre-wrap">' + esc(n.text) + '</p>' : 'No note on ' + esc(c.sym) + '. Type "' + esc(c.sym) + ' note your words" to save one.'; });
      list.innerHTML = ''; return;
    }
    var msg = { type: 'jb-open-chart', sym: c.sym, side: c.side || (c.act === 'flip' ? 'flip' : '') };
    if (PHONE) { location.href = './?open=' + encodeURIComponent(c.sym) + (msg.side ? '&side=' + msg.side : '') + '#' + boardFor(c.sym); return; }
    closeBar(); send(boardFor(c.sym), msg);
  }

  // ------------------------------------------------------------------ keys (full board)
  var KEYS = '<b>Keyboard keys</b><table style="margin-top:6px;font-size:12px">' + [['/', 'command bar'], ['1 - 9, 0', 'tabs 1 to 10 (in order across the top)'], ['B', 'buy ticket on the selected symbol'], ['S', 'sell ticket on the selected symbol'],
    ['Esc', 'close a pop-up or chart'], ['?', 'this list']].map(function (r) { return '<tr><td style="padding:3px 14px 3px 0;color:#ffd166">' + r[0] + '</td><td>' + r[1] + '</td></tr>'; }).join('') + '</table><p style="color:#6f8aa3">The selected symbol is the last tile you clicked or pointed at, or the open chart.</p>';
  function typing(e) { var t = e.target; return t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)); }
  function onKey(e, fromWin) {
    if (e.metaKey || e.ctrlKey || e.altKey || typing(e)) return;
    var k = e.key;
    if (k === '/') { e.preventDefault(); openBar(''); return; }
    if (PHONE) return;
    if (k === '?') { e.preventDefault(); openBar(''); list.innerHTML = ''; out.innerHTML = KEYS; return; }
    if (k === 'Escape') { closeBar(); ban && ban.remove && (ban.style.display = 'none'); return; }
    if (/^[0-9]$/.test(k)) { var t = tabs()[k === '0' ? 9 : +k - 1]; if (t) { e.preventDefault(); t.click(); } return; }
    if ((k === 'b' || k === 's' || k === 'B' || k === 'S') && fromWin) {
      var cur = ls('jb_dash_tab');
      if (cur === 'ibkr' || cur === 'webull') { e.preventDefault(); var f = frame(cur); if (f) f.contentWindow.postMessage({ type: 'jb-key', key: k.toLowerCase() }, location.origin); }
    }
  }
  document.addEventListener('keydown', function (e) { onKey(e, window); });
  if (!PHONE) {
    // the boards live in frames on the same site: listen to their keys too
    var hooked = [];
    function hook() {
      [].forEach.call(document.querySelectorAll('#panes iframe'), function (f) {
        try { var w = f.contentWindow; if (!w || hooked.indexOf(w) >= 0 || !w.document) return; hooked.push(w); w.document.addEventListener('keydown', function (e) { onKey(e, w); }); } catch (x) {}
      });
    }
    document.getElementById('panes').addEventListener('load', hook, true);
    setInterval(hook, 3000);
    var brand = document.querySelector('#bar .brand');
    if (brand) { var b = document.createElement('button'); b.className = 'cbBtn'; b.type = 'button'; b.title = 'Command bar ( / ): type a symbol or a tab name'; b.textContent = '⌕'; b.onclick = function () { openBar(''); }; brand.appendChild(b); }
    // ./?open=TQQQ&side=buy#ibkr (from the phone page's search box)
    var q = new URLSearchParams(location.search);
    if (q.get('open')) {
      var tab = (location.hash || '#ibkr').slice(1);
      setTimeout(function () { send(tab === 'webull' ? 'webull' : 'ibkr', { type: 'jb-open-chart', sym: q.get('open').toUpperCase(), side: q.get('side') || '' }); }, 800);
      try { history.replaceState(null, '', location.pathname + location.hash); } catch (e) {}
    }
  } else {
    var hdr = document.querySelector('header');
    if (hdr) {
      var box = document.createElement('div'); box.id = 'phSearch';
      box.innerHTML = '<input id="cbPh" type="search" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="Search a symbol or tab: TQQQ, TQQQ buy, world">';
      hdr.parentNode.insertBefore(box, hdr.nextSibling);
      var ph = box.querySelector('input');
      ph.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); var c = parse(ph.value); if (c) { if (c.act === 'news' || c.act === 'note') { openBar(ph.value); run(c); } else run(c); } ph.blur(); } });
    }
  }

  // ------------------------------------------------------------------ what changed since you last looked (72)
  var ban = null, KEY = 'jb_last_seen';
  function stamp() { ls(KEY, String(Date.now())); }
  function banner() {
    var since = +ls(KEY) || 0;
    if (!since) { stamp(); return; }
    if (Date.now() - since < 20 * 60000) return;
    api('tools/changes?since=' + since).then(function (d) {
      var it = (d && d.items) || [];
      if (!it.length) { stamp(); return; }
      ban = document.createElement('div'); ban.id = 'chgBan';
      var mins = Math.round((Date.now() - since) / 60000);
      ban.innerHTML = '<span class="x" title="Got it">✕</span><b>SINCE YOU LAST LOOKED (' + (mins < 120 ? mins + ' MIN' : Math.round(mins / 60) + ' H') + ' AGO)</b><ul>' +
        it.slice(0, 10).map(function (i) { return '<li>' + esc(i.text) + '</li>'; }).join('') + '</ul>';
      document.body.appendChild(ban);
      ban.querySelector('.x').onclick = function () { ban.style.display = 'none'; stamp(); };
    }).catch(function () {});
  }
  banner();
  document.addEventListener('visibilitychange', function () { if (document.hidden) { if (!ban || ban.style.display === 'none') stamp(); } else if (!ban || ban.style.display === 'none') { ban = null; banner(); } });
  window.addEventListener('pagehide', function () { if (!ban || ban.style.display === 'none') stamp(); });
})();
