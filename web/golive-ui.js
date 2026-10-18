/* Alex Hale's Board -- Go Live / Stop Live (jb-golive).
 *   algo.html   a "Live Trading (Real Money)" card under the top two cards: the go-live checklist (the same one ./jb live
 *               prints), the strategy the paper algo runs and whether its file allows live trading (confirm_live: true),
 *               a box to type I_UNDERSTAND_THE_RISK, Go Live, Stop Live, the start-after-a-Mac-restart option (default off),
 *               how the last live run ended and the position it left. While live runs: a red LIVE banner at the top.
 *   phone.html  a red LIVE banner at the top while either algo trades live.
 *   index.html  a red LIVE badge on the Webull Algo / IBKR Algo tab buttons while that algo trades live.
 * Data: /api/algo/<name>/live and /api/algo/live (gateway/livectl.py). Hooked in by one <script src="golive-ui.js">
 * line on each of those pages. Nothing here sends an order: Go Live starts the live trader (./jb live-service). */
(function () {
  'use strict';
  if (window.__jbGoLive) return;
  window.__jbGoLive = true;
  var page = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }
  function tcase(t) { return String(t == null ? '' : t).replace(/(^|[\s(\/-])([a-z])/g, function (m, a, b) { return a + b.toUpperCase(); }); }
  function money(v) { return v == null || isNaN(v) ? '—' : (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 }); }
  function when(ms) { return ms ? new Date(ms).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''; }
  function api(url, body) {
    return fetch(url, body ? { method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      : { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (r.status === 401) { (window.top || window).location.href = '/login?next=/'; throw new Error('Log In First'); }
      return r.text().then(function (t) { var j = null; try { j = JSON.parse(t); } catch (e) {}
        if (!r.ok) throw new Error((j && j.error) || t || ('HTTP ' + r.status)); return j; });
    });
  }
  function posText(p) { return p && p.shares ? (p.side === 'short' ? 'Short ' : 'Long ') + p.shares + (p.symbol ? ' ' + esc(p.symbol) : '') + ' @ ' + (+p.entry_price).toFixed(2) : 'Flat'; }
  var st = document.createElement('style');
  st.textContent =
    '.glv-ban{background:#5a0a0a;border:2px solid #ff3b30;color:#fff;border-radius:8px;padding:9px 12px;margin:0 0 12px;font-size:13px;font-weight:700;line-height:1.45;overflow-wrap:anywhere;}' +
    '.glv-ban b{background:#ff3b30;color:#fff;border-radius:4px;padding:1px 7px;margin-right:6px;letter-spacing:.06em;}' +
    '.glv-badge{display:inline-block;background:#ff3b30;color:#fff;font-size:9px;font-weight:800;letter-spacing:.06em;border-radius:3px;padding:0 4px;margin-left:5px;vertical-align:1px;animation:glvP 2s infinite;}' +
    '@keyframes glvP{50%{opacity:.55}}' +
    '#glvCard .glv-row{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin:8px 0;font-size:12.5px;min-width:0;}' +
    '#glvCard .glv-k{color:#6f8aa3;font-size:10px;letter-spacing:.08em;font-weight:700;}' +
    '#glvCard ul{list-style:none;margin:6px 0;padding:0;font-size:12px;line-height:1.5;}' +
    '#glvCard li{padding:3px 0;border-top:1px solid #0d2740;overflow-wrap:anywhere;} #glvCard li .fix{display:block;color:#6f8aa3;font-size:11px;}' +
    '#glvCard .ok{color:#00ff41;font-weight:700;} #glvCard .no{color:#ffb648;font-weight:700;} #glvCard .bad{color:#ff8a80;} #glvCard .good{color:#00ff41;}' +
    '/*glv-colorfix*/#glvCard input#glvWord{color:#fff !important;-webkit-text-fill-color:#fff !important;caret-color:#fff !important;background:#000 !important;opacity:1 !important;text-shadow:none !important;}#glvCard input#glvWord::placeholder{color:#6f8aa3 !important;-webkit-text-fill-color:#6f8aa3 !important;opacity:1;}' +
    '#glvCard input[type=text]{font:inherit;font-size:15px;padding:8px 10px;border-radius:6px;border:1px solid #164060;background:#000;color:#fff;width:100%;max-width:300px;min-width:0;}' +
    '#glvCard button{font:inherit;font-weight:700;font-size:13px;padding:9px 16px;border-radius:6px;border:1px solid #a33;background:#5a1010;color:#fff;cursor:pointer;}' +
    '#glvCard button.stop{background:#1f5f8b;border-color:#1f5f8b;} #glvCard button:disabled{opacity:.45;cursor:default;}' +
    '#glvCard label{display:flex;align-items:center;gap:6px;cursor:pointer;} #glvCard input[type=checkbox]{width:18px;height:18px;accent-color:#ff3b30;}' +
    '#glvCard pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:10.5px;color:#9fb3c8;background:#000;border-radius:6px;padding:6px 8px;margin:6px 0 0;max-height:220px;overflow-y:auto;}' +
    '#glvCard .glv-n{font-size:11px;color:#6f8aa3;line-height:1.5;margin-top:6px;}';
  document.head.appendChild(st);

  // ---------------------------------------------------------------- the algo tab
  function algoTab() {
    var name = (new URLSearchParams(location.search).get('name') || '').toLowerCase() === 'ibkr' ? 'ibkr' : 'webull';
    var NICE = name === 'ibkr' ? 'IBKR' : 'Webull';
    var rep = document.getElementById('rep-card'), grid = rep && rep.parentNode;
    if (!grid) return;
    var card = document.createElement('div'); card.className = 'card wide'; card.id = 'glvCard';
    card.innerHTML = '<h2>Live Trading (Real Money) <span class="tag" id="glvTag">—</span></h2><div id="glvBody">Loading…</div>';
    grid.insertBefore(card, rep);
    var hdr = document.querySelector('header'), ban = document.createElement('div');
    ban.className = 'glv-ban'; ban.id = 'glvBan'; ban.style.display = 'none';
    if (hdr) hdr.parentNode.insertBefore(ban, hdr.nextSibling);
    var body = document.getElementById('glvBody'), tag = document.getElementById('glvTag');
    var d = null, busy = false, msg = '', msgCls = '', typed = '', fastUntil = 0, n = 0;

    function draw() {
      if (!d) return;
      var f = d.facts || {}, cl = d.checklist || {}, live = d.live || {}, last = d.last || null;
      tag.textContent = d.running ? 'LIVE' : (d.starting ? 'Starting' : 'Off');
      tag.className = 'tag' + (d.running || d.starting ? ' live' : '');
      ban.style.display = d.running ? '' : 'none';
      if (d.running) ban.innerHTML = '<b>LIVE</b>Real Money: The ' + NICE + ' Algo Is Trading ' + esc(live.symbol || f.symbol || '') + ' Live' +
        (d.by === 'service' ? ' (Go Live' : ' (./jb live In A Terminal Window') + (d.started_at ? ', Since ' + esc(when(d.started_at)) : '') + '). Position: ' + posText(live.position) +
        (live.halted ? ' · PAUSED: ' + esc(live.halted) : '') + (live.safe_mode ? ' · Safe Mode: ' + esc(tcase(live.safe_mode)) : '');
      var h = '';
      if (d.running) {
        h += '<div class="glv-row"><span class="glv-k">STATUS</span><b class="bad">Live Trading Is Running</b>' +
          (d.by === 'service' ? ' (Started With Go Live)' : ' (./jb live In A Terminal Window)') + '</div>' +
          '<div class="glv-row"><span class="glv-k">STRATEGY</span>' + esc(d.strategy || live.strategy_name || '—') + '</div>' +
          '<div class="glv-row"><span class="glv-k">POSITION</span>' + posText(live.position) +
          (live.unrealized_pnl != null && live.position ? ' · Open ' + money(live.unrealized_pnl) : '') +
          ' · Feed ' + (live.connected ? '<span class="good">Connected</span>' : '<span class="bad">' + esc(tcase(live.last_error || 'Not Connected')) + '</span>') + '</div>' +
          (d.by === 'service' ? '<div class="glv-row"><button type="button" class="stop" id="glvStop">Stop Live</button>' +
            '<span class="glv-n">Ends Live Trading Cleanly: Orders Still Working Are Cancelled, No Order Is Sent By Stopping, The Position Stays Open At ' + NICE + ' And Is Shown Here. The Paper Algo Starts Again.</span></div>'
            : '<div class="glv-n">It Runs In A Terminal Window On The Mac: Stop It There With Ctrl+C.</div>');
      } else {
        var ready = d.password_set && f.exists && f.confirm_live && f.max_order_usd;
        h += '<div class="glv-row"><span class="glv-k">STRATEGY</span>' + (d.strategy ? esc(d.strategy) + ' (The One The Paper Algo Runs)' : '<span class="bad">None Set</span>') +
          (f.symbol ? ' · ' + esc(f.symbol) : '') + (f.side === 'sell' ? ' · Sells Short' : '') + '</div>' +
          '<div class="glv-row"><span class="glv-k">CONFIRM_LIVE</span>' + (f.confirm_live ? '<span class="good">confirm_live: true</span>'
            : '<span class="bad">Not In The File: Add confirm_live: true To ' + esc(d.strategy || 'The Strategy File') + ' Yourself When You Mean It To Trade Real Money</span>') +
          ' · <span class="glv-k">MAX ORDER</span>' + (f.max_order_usd ? money(f.max_order_usd) : '<span class="bad">Not Set (risk: max_order_notional_usd)</span>') + '</div>' +
          (d.password_set ? '' : '<div class="glv-row bad">Go Live Only Works With A Board Password: Set JB_PASSWORD In settings.env, Then ./jb restart gateway.</div>');
        if (cl.items) {
          h += '<div class="glv-row"><span class="glv-k">GO-LIVE CHECKLIST</span>' + cl.passed + ' Of ' + cl.total + ' Passed (The Same Checks As ./jb live)</div><ul>' +
            cl.items.map(function (i) { return '<li><span class="' + (i.ok ? 'ok">OK' : 'no">!!') + '</span> ' + esc(tcase(i.name)) + ': ' + esc(tcase(i.detail)) +
              (i.fix ? '<span class="fix">' + esc(tcase(i.fix)) + '</span>' : '') + '</li>'; }).join('') + '</ul>' +
            (cl.ready ? '' : '<div class="glv-n">Not Every Check Passed. You Can Still Go Live, But It Is Safer To Fix The !! Lines First.</div>');
        } else if (cl.error) h += '<div class="glv-n bad">Checklist Not Available: ' + esc(cl.error) + '</div>';
        h += '<div class="glv-row"><label for="glvWord" class="glv-k">TYPE ' + esc(d.confirm_words) + '</label>' +
          '<input type="text" id="glvWord" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="Type The Words Here" value="' + esc(typed) + '">' +
          '<button type="button" id="glvGo"' + (ready && !busy ? '' : ' disabled') + '>Go Live</button></div>' +
          '<div class="glv-n">Starts LIVE Trading At ' + NICE + ' In The Background (No Terminal Needed), With This Strategy. The Paper Algo Stops While It Runs. ' +
          'It First Runs Its Own Start-Up Checks, Like ./jb live: An Order Or Auto-Hedge Order Still Open At ' + NICE + ', Or A Position That Differs From Its Record, Stops It, And This Card Says Why.</div>';
        if (d.record_position) h += '<div class="glv-row"><span class="glv-k">LIVE POSITION LEFT OPEN</span><b>' + posText(d.record_position) + '</b> (From The Live Record; Check The ' + NICE + ' App)</div>';
      }
      h += '<div class="glv-row"><label><input type="checkbox" id="glvAuto"' + (d.autostart ? ' checked' : '') + (d.password_set ? '' : ' disabled') + '>' +
        'Start Live Trading Again After A Mac Restart</label><span class="glv-n" style="margin:0">Off By Default: After A Restart Live Trading Stays Off Until You Press Go Live.</span></div>';
      if (last && !d.running) {
        var how = { stopped: 'Stopped By You (Stop Live)', shutdown: 'Stopped By macOS (Shut Down Or Restart)', refused: 'Refused To Start',
                    exited: 'Stopped On Its Own (Exit Code ' + esc(last.rc) + ')' }[last.how] || esc(last.how);
        h += '<div class="glv-row"><span class="glv-k">LAST LIVE RUN</span><span class="' + (last.how === 'stopped' ? '' : 'bad') + '">' + how + '</span>' +
          (last.at ? ' · ' + esc(when(last.at * 1000)) : '') + (last.strategy ? ' · ' + esc(last.strategy) : '') + '</div>';
      } else if (d.starting) h += '<div class="glv-row"><b class="no">Starting…</b> The Live Trader Is Running Its Start-Up Checks (About 20 Seconds).</div>';
      if (msg) h += '<div class="glv-row ' + msgCls + '">' + esc(msg) + '</div>';
      if (d.log && d.log.length && (d.running || d.starting || last)) h += '<details' + (last && last.how === 'refused' ? ' open' : '') + '><summary class="glv-n">Live Trader Log (Latest Lines)</summary><pre>' + esc(d.log.join('\n')) + '</pre></details>';
      body.innerHTML = h;
      var w = document.getElementById('glvWord');
      if (w) w.oninput = function () { typed = w.value; };
      var go = document.getElementById('glvGo');
      if (go) go.onclick = function () {
        if (typed.trim() !== d.confirm_words) { msg = 'Type ' + d.confirm_words + ' Exactly (Capital Letters And Underscores).'; msgCls = 'bad'; draw(); return; }
        if (!confirm('Start LIVE trading at ' + NICE + ' with REAL MONEY?\n\nStrategy: ' + d.strategy + '\n\nIt keeps running in the background until you press Stop Live.')) return;
        busy = true; msg = 'Starting…'; msgCls = ''; draw();
        api('/api/algo/' + name + '/live', { action: 'start', confirm: typed.trim() }).then(function (j) { d = j; typed = ''; msg = j.message || 'Starting.'; msgCls = 'good'; })
          .catch(function (e) { msg = 'Not Started: ' + e.message; msgCls = 'bad'; }).then(function () { busy = false; draw(); poll(); });
      };
      var sp = document.getElementById('glvStop');
      if (sp) sp.onclick = function () {
        if (!confirm('Stop LIVE trading at ' + NICE + '?\n\nNo order is sent by stopping. Orders still working are cancelled. The position stays open at ' + NICE + '.')) return;
        busy = true; msg = 'Stopping… (Up To A Minute)'; msgCls = ''; draw();
        api('/api/algo/' + name + '/live', { action: 'stop' }).then(function (j) { d = j; msg = j.message || 'Stopped.'; msgCls = 'good'; })
          .catch(function (e) { msg = 'Not Stopped: ' + e.message; msgCls = 'bad'; }).then(function () { busy = false; draw(); poll(); });
      };
      var au = document.getElementById('glvAuto');
      if (au) au.onchange = function () {
        if (au.checked && !confirm('Start LIVE trading again by itself after a Mac restart?\n\nOnly while it was running when the Mac went down. Default is off.')) { au.checked = false; return; }
        api('/api/algo/' + name + '/live', { action: 'autostart', on: au.checked }).then(function (j) { d = j; msg = ''; draw(); })
          .catch(function (e) { msg = 'Not Saved: ' + e.message; msgCls = 'bad'; draw(); });
      };
    }
    function load() {
      if (busy || document.hidden) return;
      var a = document.activeElement;
      api('/api/algo/' + name + '/live').then(function (j) {
        d = j;
        if (a && a.id === 'glvWord' && document.activeElement === a) { tag.textContent = j.running ? 'LIVE' : tag.textContent; return; }   // don't redraw while typing
        draw();
      }).catch(function (e) { body.innerHTML = '<span class="bad">Not Available: ' + esc(e.message) + '</span>'; });
    }
    function poll() { fastUntil = Date.now() + 60000; load(); }      // after Go Live / Stop Live: every 2 s for a minute
    load();
    setInterval(function () { n++; if (Date.now() < fastUntil || n % 4 === 0) load(); }, 2000);
  }

  // ---------------------------------------------------------------- both algos at once (phone banner, tab badges)
  function summary(cb) {
    function go() { if (document.hidden) return; api('/api/algo/live').then(cb).catch(function () {}); }
    go(); setInterval(go, 20000);
  }
  function phone() {
    var hdr = document.querySelector('header'), ban = document.createElement('div');
    ban.className = 'glv-ban'; ban.id = 'glvBan'; ban.style.display = 'none';
    if (hdr) hdr.parentNode.insertBefore(ban, hdr.nextSibling); else document.body.insertBefore(ban, document.body.firstChild);
    summary(function (s) {
      var on = ['webull', 'ibkr'].filter(function (n) { return s[n] && s[n].running; });
      ban.style.display = on.length ? '' : 'none';
      ban.innerHTML = on.map(function (n) { var x = s[n];
        return '<div><b>LIVE</b>' + (n === 'ibkr' ? 'IBKR' : 'Webull') + ' Algo Trading Real Money' + (x.symbol ? ' · ' + esc(x.symbol) : '') + ' · ' + posText(x.position) +
          (x.halted ? ' · PAUSED' : '') + '</div>'; }).join('');
    });
  }
  function tabs() {
    summary(function (s) {
      ['webull', 'ibkr'].forEach(function (n) {
        var t = document.querySelector('#bar .tab[data-tab="algo-' + n + '"]');
        if (!t) return;
        var b = t.querySelector('.glv-badge');
        if (s[n] && s[n].running && !b) { b = document.createElement('span'); b.className = 'glv-badge'; b.textContent = 'LIVE'; b.title = 'Trading real money'; t.appendChild(b); }
        if (!(s[n] && s[n].running) && b) b.parentNode.removeChild(b);
      });
    });
  }
  function init() {
    if (page === 'algo.html') algoTab();
    else if (page === 'phone.html') phone();
    else if (document.getElementById('bar')) tabs();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
