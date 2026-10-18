/* Alex Hale's Board -- desk notes and crowd gauges (jb-insight, deep search ideas 27, 30, 31). Adds, never removes:
 *   IBKR / Webull boards: a "Desk note" at the top of the WHY? panel
 *   Phone page: a "Desk notes" card (what you hold and the 3 biggest moves) and one crowd line under Futures and fear
 *   World tab: one crowd line (Fear & Greed, AAII, NAAIM) under the mood meter; tap it for Fear & Greed's 7 parts
 * Data: /api/plus/insight/{note,notes,crowd}. Hooked in by one <script src="insight.js"> line on each page.
 */
(function () {
  'use strict';
  if (location.protocol.indexOf('http') !== 0) return;
  var API = '/api/plus/insight';
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function getJ(u) { return fetch(u, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }); }
  var st = document.createElement('style');
  st.textContent = '.in-note{font-size:13px;line-height:1.5;margin:4px 0 8px;} .in-crowd{font-size:12px;color:var(--dim,#9fb3c8);line-height:1.5;margin:8px 0 0;cursor:pointer;}' +
    '.in-crowd b{color:var(--white,#f2f2f2);font-weight:600;} .in-crowd .x{color:#ffc400;} .in-parts{display:none;margin:4px 0 0;font-size:11.5px;} .in-crowd.open .in-parts{display:block;}' +
    '.in-dn{padding:6px 0;border-top:1px solid var(--line,#1c2b3d);font-size:13px;line-height:1.45;} .in-dn:first-child{border-top:0;} .in-dn b{color:#7fd6ff;margin-right:6px;}';
  document.head.appendChild(st);

  // ---- crowd line (World tab and Phone page)
  function crowdHtml(c) {
    if (!c || !c.line) return '';
    var fg = c.fg, h = '<b>Crowd</b> ' + esc(c.line);
    (c.notes || []).forEach(function (n) { h += '<br><span class="x">' + esc(n) + '</span>'; });
    if (fg && fg.parts && fg.parts.length) h += '<div class="in-parts">Fear &amp; Greed parts: ' + fg.parts.map(function (p) { return esc(p.name) + ' <b>' + esc(p.rating) + '</b>'; }).join(' · ') +
      '<br>0-100: under 25 extreme fear, over 75 extreme greed (CNN, unofficial). AAII = individual investors, Thursdays; NAAIM = active managers\' stock exposure, Wednesdays.</div>';
    return h;
  }
  function crowd(where) {
    var el = document.createElement('div'); el.className = 'in-crowd'; el.title = 'Tap for Fear & Greed\'s parts';
    el.onclick = function () { el.classList.toggle('open'); };
    where(el);
    function load() { if (!document.hidden) getJ(API + '/crowd').then(function (c) { el.innerHTML = crowdHtml(c); el.style.display = el.innerHTML ? '' : 'none'; }).catch(function () {}); }
    load(); setInterval(load, 10 * 60 * 1000);
  }
  var moodWhy = $('moodWhy');
  if (moodWhy) crowd(function (el) { moodWhy.parentNode.appendChild(el); });          // World tab: inside the mood card
  var fut = $('fut');
  if (fut && $('mov')) crowd(function (el) { fut.parentNode.appendChild(el); });       // Phone page: inside Futures and fear

  // ---- desk notes card (Phone page)
  if ($('mov')) {
    var card = document.createElement('div'); card.className = 'card'; card.id = 'inNotes';
    card.innerHTML = '<h2>Desk notes <span>board math, your symbols</span></h2><div id="inNotesB" class="empty">Writing notes…</div>';
    var anchor = $('heldc') || $('mov').parentNode;
    anchor.parentNode.insertBefore(card, anchor.nextSibling);
    var syms = function () {
      var out = [];
      ['held', 'mov'].forEach(function (id) {
        var box = $(id); if (!box) return;
        [].slice.call(box.querySelectorAll('tr')).forEach(function (tr) {
          var td = tr.querySelector('td'); if (!td || tr.classList.contains('wy-l')) return;
          var s = (td.childNodes[0] && td.childNodes[0].textContent || '').trim();
          if (/^[A-Z][A-Z0-9.=\-]{0,9}$/.test(s) && out.indexOf(s) < 0 && (id === 'held' || out.length < 6)) out.push(s);
        });
      });
      return out.slice(0, 8);
    };
    var loadNotes = function () {
      if (document.hidden) return;
      var list = syms(); if (!list.length) return;
      getJ(API + '/notes?syms=' + encodeURIComponent(list.join(','))).then(function (d) {
        if (!d || !d.notes) return;
        $('inNotesB').className = '';
        $('inNotesB').innerHTML = list.filter(function (s) { return d.notes[s]; }).map(function (s) { var n = d.notes[s]; if (n.indexOf(s + ' ') === 0) n = n.slice(s.length + 1); return '<div class="in-dn"><b>' + esc(s) + '</b>' + esc(n) + '</div>'; }).join('');
      }).catch(function () {});
    };
    setTimeout(loadNotes, 4000); setInterval(loadNotes, 3 * 60 * 1000);
  }

  // ---- desk note in the WHY? panel (IBKR / Webull boards)
  var tries = 0;
  (function hook() {
    if (typeof window.JBWhyBox !== 'function') { if (++tries < 40) setTimeout(hook, 500); return; }
    if (window.JBWhyBox.jbInsight) return;
    var orig = window.JBWhyBox;
    var wrapped = function (sym, box) {
      orig(sym, box);
      var sec = document.createElement('div'); sec.innerHTML = '<h3>Desk note</h3><p class="in-note dim">Writing…</p>';
      var ans = box.querySelector('.ans'); if (ans) ans.parentNode.insertBefore(sec, ans.nextSibling); else box.insertBefore(sec, box.firstChild);
      getJ(API + '/note?sym=' + encodeURIComponent(sym)).then(function (o) { sec.querySelector('p').className = 'in-note'; sec.querySelector('p').textContent = (o && o.note) || 'No note right now.'; }).catch(function () {});
    };
    wrapped.jbInsight = true;
    window.JBWhyBox = wrapped;
  })();
})();
