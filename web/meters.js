/* Futures mood and Picks mood meters (jb-meters). Same look as "Mood since the 4 pm close".
   Full board: a strip at the top, under the tabs. Phone page: two cards right above the 4 pm mood card.
   Each meter has its own window buttons (Open, Close, After-hrs, Overnight, Pre-mkt), remembered on this device.
   Data: /api/plus/meters (gateway/meters.py), every minute. */
(function(){
  'use strict';
  var phone = !!document.getElementById('mood') && !document.getElementById('panes');
  var panes = document.getElementById('panes');
  if(!phone && !panes) return;
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function cls(v){ return v == null ? 'flat' : v > 0.004 ? 'up' : v < -0.004 ? 'dn' : 'flat'; }
  function pct(v){ return v == null ? '–' : (v > 0 ? '+' : '') + v.toFixed(2) + '%'; }
  function when(ms){
    if(!ms) return '';
    return new Date(ms).toLocaleString('en-US', {timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit'}) + ' ET';
  }
  function get(k, d){ try{ return localStorage.getItem(k) || d; }catch(e){ return d; } }
  function put(k, v){ try{ localStorage.setItem(k, v); }catch(e){} }

  var css = '' +
    '.jbm .jbm-h{display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;margin:0 0 8px;}' +
    '.jbm .jbm-t{font-size:11px;letter-spacing:.1em;color:var(--dim,#6f8aa3);text-transform:uppercase;font-weight:600;}' +
    '.jbm .jbm-w{display:flex;gap:3px;flex-wrap:wrap;}' +
    '.jbm .jbm-w button{font:inherit;font-size:10px!important;line-height:1!important;padding:4px 6px!important;margin:0!important;' +
      'min-height:0!important;height:auto!important;width:auto!important;border-radius:9px!important;cursor:pointer;' +
      'background:transparent!important;color:var(--dim,#6f8aa3)!important;border:1px solid var(--line-bright,#164060)!important;}' +
    '.jbm .jbm-w button.on{background:#0b2a44!important;color:var(--cyan,#00e5ff)!important;border-color:var(--cyan,#00e5ff)!important;}' +
    '.jbm .jbm-m{display:flex;align-items:center;gap:10px;}' +
    '.jbm .jbm-m b{font-size:24px;}' +
    '.jbm .jbm-bar{flex:1;height:8px;border-radius:4px;background:linear-gradient(90deg,#ff3b30,#6f8aa3 50%,#00ff41);position:relative;margin-top:2px;}' +
    '.jbm .jbm-bar i{position:absolute;top:-4px;width:4px;height:16px;border-radius:2px;background:#fff;transform:translateX(-2px);}' +
    '.jbm .jbm-p{font-size:11px;color:var(--dim,#6f8aa3);margin-top:7px;line-height:1.5;display:flex;flex-wrap:wrap;gap:0 8px;}' +
    '.jbm .jbm-p>span{white-space:nowrap;}' +
    '.jbm .up{color:var(--green,#00ff41);} .jbm .dn{color:var(--red,#ff3b30);} .jbm .flat{color:var(--dim,#6f8aa3);}' +
    '.jbm .jbm-e{color:var(--dim,#6f8aa3);font-size:12px;}' +
    '#jbm-strip{display:flex;gap:8px;padding:6px 8px;flex-wrap:wrap;flex:none;}' +
    '#jbm-strip .jbm{flex:1 1 300px;min-width:0;background:var(--panel,#071a30);border:1px solid var(--line-bright,#164060);border-radius:10px;padding:8px 12px;}' +
    '#jbm-strip .jbm-m b{font-size:22px;}' +
    '#jbm-strip .jbm-p{margin-top:5px;}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  var METERS = [
    {key: 'futures', title: 'Futures mood', store: 'jb_meter_fut'},
    {key: 'picks', title: 'Picks mood', store: 'jb_meter_picks'}
  ];
  var boxes = {};
  var host;
  if(phone){
    var moodCard = document.getElementById('mood').parentNode;
    METERS.forEach(function(m){
      var c = document.createElement('div'); c.className = 'card jbm'; c.id = 'jbm-' + m.key;
      moodCard.parentNode.insertBefore(c, moodCard); boxes[m.key] = c;
    });
  } else {
    host = document.createElement('div'); host.id = 'jbm-strip';
    panes.parentNode.insertBefore(host, panes);
    METERS.forEach(function(m){
      var c = document.createElement('div'); c.className = 'jbm'; c.id = 'jbm-' + m.key;
      host.appendChild(c); boxes[m.key] = c;
    });
  }

  var last = null;
  function draw(){
    METERS.forEach(function(m){
      var box = boxes[m.key], d = last, win = get(m.store, 'close');
      var wins = (d && d.windows) || [{key:'open',name:'Open'},{key:'close',name:'Close'},{key:'after',name:'After-hrs'},{key:'overnight',name:'Overnight'},{key:'premarket',name:'Pre-mkt'}];
      var w = wins.filter(function(x){ return x.key === win; })[0] || wins[1];
      var r = d && d[m.key] && d[m.key][w.key];
      var h = '<div class="jbm-h"><span class="jbm-t">' + m.title + ' ' + esc(w.what || '') + '</span><span class="jbm-w">' +
        wins.map(function(x){
          return '<button data-w="' + x.key + '"' + (x.key === w.key ? ' class="on"' : '') + ' title="' + esc((x.what || '') + (x.from_ms ? ': ' + when(x.from_ms) : '')) + '">' + esc(x.name) + '</button>';
        }).join('') + '</span></div>';
      if(r){
        h += '<div class="jbm-m"><b class="' + cls(r.score) + '">' + (r.score > 0 ? '+' : '') + r.score + '</b><div style="flex:1"><div>' + esc(r.label) +
          (m.key === 'picks' ? ' <span class="jbm-e">' + r.up + ' up · ' + r.down + ' down · avg <span class="' + cls(r.avg) + '">' + pct(r.avg) + '</span></span>' : '') +
          '</div><div class="jbm-bar"><i style="left:' + ((r.score + 100) / 2) + '%"></i></div></div></div>';
        h += '<div class="jbm-p">' + r.parts.map(function(p){
          return '<span>' + esc(p.name || p.sym) + ' <b class="' + cls(p.pct) + '" style="font-size:inherit">' + pct(p.pct) + '</b></span>';
        }).join('') + '<span>from ' + esc(when(w.from_ms)) + '</span></div>';
      } else {
        var why = !d ? 'Loading…' : d.note || (m.key === 'picks' && !d.n_picks ? 'No Picks from the scanner yet.' : 'Waiting for prices…');
        h += '<div class="jbm-e">' + esc(why) + '</div>';
      }
      box.innerHTML = h;
    });
  }
  Object.keys(boxes).forEach(function(k){
    boxes[k].addEventListener('click', function(e){
      var b = e.target.closest && e.target.closest('button[data-w]');
      if(!b) return;
      var m = METERS.filter(function(x){ return x.key === k; })[0];
      put(m.store, b.getAttribute('data-w')); draw();
    });
  });
  function load(){
    fetch('/api/plus/meters', {credentials: 'same-origin', cache: 'no-store'}).then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){ if(d && !d.error_code){ last = d; } draw(); }).catch(function(){ draw(); });
  }
  draw(); load(); setInterval(load, 60000);
})();
