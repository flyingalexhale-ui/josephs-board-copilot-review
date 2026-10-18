/* Alex Hale's Board -- Portfolio filled from your accounts (jb-desk). A bar above Your Positions:
 * which accounts filled it and when, Refresh Now, and an On/Off switch. Each position row shows
 * where it came from (IBKR, Webull, or Typed for trades you entered). Data: /api/plus/brokerpos. */
(function () {
  'use strict';
  var pos = document.getElementById('pos');
  if (!pos) return;
  var table = pos.closest('table') || pos;
  var st = document.createElement('style');
  st.textContent =
    '#paBar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin:6px 0 8px;padding:8px 12px;' +
    'border:1px solid #164060;border-radius:8px;background:#071a30;font-size:12.5px;color:#cfd8e3;}' +
    '#paBar b{color:#ffffff;} #paBar .pa-err{color:#ff8a80;} #paBar .pa-btns{display:flex;gap:6px;}' +
    '#paBar button{padding:5px 12px;border-radius:6px;border:1px solid #2f7a44;background:#1e5631;color:#ffffff;font:inherit;font-weight:600;cursor:pointer;}' +
    '#paBar button.off{background:transparent;color:#cfd8e3;border-color:#3b5873;}' +
    '#pos .src{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;font-size:10px;font-weight:600;vertical-align:1px;' +
    'background:rgba(92,200,219,.14);color:#5cc8db;}' +
    '#pos .src.typed{background:rgba(160,160,160,.15);color:#9fb3c8;}';
  document.head.appendChild(st);
  var bar = document.createElement('div');
  bar.id = 'paBar';
  table.parentNode.insertBefore(bar, table);

  function t(ms) { return ms ? new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—'; }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function one(name, b) {
    if (b.error && !b.ok) return name + ': <span class="pa-err" title="' + esc(b.error) + '">Not Read Yet</span>';
    return name + ' <b>' + b.n + '</b>' + (b.error ? ' <span class="pa-err" title="' + esc(b.error) + '">(Last Try Failed)</span>' : '');
  }
  function draw(d) {
    if (!d || d.error) { bar.innerHTML = '<span class="pa-err">Account fill not available: ' + esc(d && d.error) + '</span>'; return; }
    bar.innerHTML = '<span>' + (d.on
      ? 'Filled From My Accounts: ' + one('IBKR', d.ibkr) + ' · ' + one('Webull', d.webull) + ' · Updated ' + t(d.at) + ' (Every 5 Min)'
      : 'Account Fill Is Off: Showing Only Trades You Typed In') + '</span>' +
      '<span class="pa-btns">' + (d.on ? '<button id="paNow">Refresh Now</button>' : '') +
      '<button id="paSw" class="' + (d.on ? 'off' : '') + '">' + (d.on ? 'Turn Off' : 'Fill From My Accounts') + '</button></span>';
    var now = document.getElementById('paNow'), sw = document.getElementById('paSw');
    if (now) now.onclick = function () { now.textContent = 'Reading…'; send('refresh', {}).then(reloadPage); };
    sw.onclick = function () { send('switch', { on: !d.on }).then(function (r) { draw(r); reloadPage(); }); };
  }
  function send(sub, body) {
    return fetch('/api/plus/brokerpos/' + sub, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' })
      .then(function (r) { return r.json(); });
  }
  function reloadPage() { setTimeout(function () { location.reload(); }, 6500); }   // the positions list redraws with the new source
  function load() { fetch('/api/plus/brokerpos/', { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(draw).catch(function (e) { draw({ error: e.message }); }); }
  load();
  setInterval(load, 60000);
})();
