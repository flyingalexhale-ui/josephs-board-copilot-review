/* Phone page: tap a tile under "Futures and fear vs 4 pm close" to see everything about it
   (jb-phonefut). Only adds; the tiles look and update exactly as before.
   The pop-up shows the price, the move since the 4 pm close in % and points, the move since
   yesterday, the 4 pm close itself, the last 24 hours' high and low, a 24-hour line chart with the
   4 pm close as a dashed line, whether it is trading now, and when the price is from. Same data as
   the World tab (/api/world, Yahoo, every 5 minutes); for live ES / NQ / YM it links to the IBKR board. */
(function(){
  'use strict';
  var WANT = ['ES=F', 'NQ=F', 'YM=F', 'RTY=F', '^VIX', 'CL=F', 'BTC-USD'];   // same order as the phone page
  var BOARD = {'ES=F': 1, 'NQ=F': 1, 'YM=F': 1, 'RTY=F': 1};
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function num(v){ if(v == null) return '–'; var a = Math.abs(v); return v.toLocaleString('en-US', {maximumFractionDigits: a >= 1000 ? 2 : a >= 10 ? 2 : 4}); }
  function pct(v){ return v == null ? '–' : (v > 0 ? '+' : '') + v.toFixed(2) + '%'; }
  function cls(v, inv){ if(v == null) return ''; if(inv) v = -v; return v > 0.004 ? 'up' : v < -0.004 ? 'dn' : ''; }
  function et(ms){ return new Date(ms).toLocaleString('en-US', {timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit'}) + ' ET'; }

  var css = document.createElement('style');
  css.textContent =
    '#fut .q{cursor:pointer;position:relative;}#fut .q:active{border-color:#00e5ff;}' +
    '#fut .q .n::after{content:" ›";color:#6f8aa3;}' +
    '.pf-ov{position:fixed;inset:0;z-index:9990;background:rgba(0,0,0,.65);display:flex;align-items:flex-start;justify-content:center;' +
      'padding:max(14px,env(safe-area-inset-top)) 10px 14px;overflow-y:auto;}' +
    '.pf-box{background:#071a30;border:1px solid #164060;border-radius:12px;width:100%;max-width:460px;padding:14px;}' +
    '.pf-h{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;}' +
    '.pf-h b{font-size:16px;}.pf-h small{display:block;color:#6f8aa3;font-size:12px;margin-top:2px;}' +
    '.pf-x{background:none;border:0;color:#9fb3c8;font-size:28px;line-height:1;min-width:44px;min-height:44px;cursor:pointer;}' +
    '.pf-big{display:flex;align-items:baseline;gap:12px;margin:8px 0 4px;flex-wrap:wrap;}.pf-big .p{font-size:28px;font-weight:700;}' +
    '.pf-big .c{font-size:17px;font-weight:700;}' +
    '.pf-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin:10px 0;}' +
    '.pf-grid div{background:#04101f;border:1px solid #0d2740;border-radius:7px;padding:6px 8px;}' +
    '.pf-grid span{display:block;font-size:11px;color:#6f8aa3;}.pf-grid b{font-size:14px;}' +
    '.pf-chart{background:#04101f;border:1px solid #0d2740;border-radius:7px;padding:6px;}' +
    '.pf-chart svg{display:block;width:100%;height:150px;}' +
    '.pf-note{font-size:12px;color:#cfd8e3;line-height:1.45;margin-top:8px;}' +
    '.pf-src{font-size:11px;color:#6f8aa3;margin-top:8px;line-height:1.45;}' +
    '.pf-go{display:block;text-align:center;margin-top:10px;padding:12px;border:1px solid #164060;border-radius:8px;color:#f2f2f2;text-decoration:none;font-size:14px;}' +
    '.pf-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#6f8aa3;margin-right:5px;}.pf-dot.on{background:#00ff41;box-shadow:0 0 6px #00ff41;}';
  document.head.appendChild(css);

  function chart(pts, ref, inv){
    if(!pts || pts.length < 2) return '<div class="pf-src">No chart yet: the line fills in as prices come in.</div>';
    var all = ref ? pts.concat([ref]) : pts;
    var lo = Math.min.apply(null, all), hi = Math.max.apply(null, all), r = hi - lo || 1, W = 300, H = 150, P = 14;
    function y(v){ return (P + (hi - v) / r * (H - 2 * P)).toFixed(1); }
    var d = pts.map(function(p, i){ return (i ? 'L' : 'M') + (i * W / (pts.length - 1)).toFixed(1) + ' ' + y(p); }).join(' ');
    var up = pts[pts.length - 1] >= (ref || pts[0]), col = (inv ? !up : up) ? '#00ff41' : '#ff3b30';
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
      (ref ? '<line x1="0" x2="' + W + '" y1="' + y(ref) + '" y2="' + y(ref) + '" stroke="#6f8aa3" stroke-dasharray="4 4" stroke-width="1" vector-effect="non-scaling-stroke"/>' : '') +
      '<path d="' + d + '" fill="none" stroke="' + col + '" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>' +
      '<div class="pf-src" style="display:flex;justify-content:space-between;margin-top:4px"><span>24 hours ago</span>' +
      (ref ? '<span>- - - 4 pm close</span>' : '') + '<span>now</span></div>';
  }

  function show(r, label){
    var inv = r.sym === '^VIX';
    var pts = (r.spark || []).filter(function(v){ return v != null; });
    var hi = pts.length ? Math.max.apply(null, pts) : null, lo = pts.length ? Math.min.apply(null, pts) : null;
    var diff = (r.last != null && r.close_ref != null) ? r.last - r.close_ref : null;
    var ov = document.createElement('div'); ov.className = 'pf-ov';
    ov.innerHTML = '<div class="pf-box"><div class="pf-h"><div><b>' + esc(label) + '</b><small>' + esc(r.name) + ' · ' + esc(r.sym) + '</small>' +
      '<small><span class="pf-dot' + (r.open ? ' on' : '') + '"></span>' + (r.open ? 'trading now' : 'closed right now') + '</small></div>' +
      '<button class="pf-x" aria-label="Close">×</button></div>' +
      '<div class="pf-big"><span class="p">' + num(r.last) + '</span><span class="c ' + cls(r.since_close, inv) + '">' + pct(r.since_close) +
      (diff != null ? ' (' + (diff > 0 ? '+' : '') + num(diff) + ')' : '') + '</span></div>' +
      '<div class="pf-src">vs the 4 pm New York close' + (inv ? ' · for the VIX, up (red) = more nervous' : '') + '</div>' +
      '<div class="pf-grid"><div><span>4 pm close</span><b>' + num(r.close_ref) + '</b></div>' +
      '<div><span>Since yesterday</span><b class="' + cls(r.day, inv) + '">' + pct(r.day) + '</b></div>' +
      '<div><span>24 h high</span><b>' + num(hi) + '</b></div><div><span>24 h low</span><b>' + num(lo) + '</b></div></div>' +
      '<div class="pf-chart">' + chart(pts, r.close_ref, inv) + '</div>' +
      (r.note ? '<div class="pf-note">' + esc(r.note) + '</div>' : '') +
      '<div class="pf-src">Price from ' + (r.last_ms ? et(r.last_ms) : '–') + ' · Yahoo Finance, refreshed every 5 minutes (same as the World tab).</div>' +
      (BOARD[r.sym] ? '<a class="pf-go" href="./#ibkr">Live price and full chart on the IBKR board ›</a>' : '') +
      '<a class="pf-go" href="./#world">World tab ›</a></div>';
    ov.querySelector('.pf-x').onclick = function(){ ov.remove(); };
    ov.onclick = function(e){ if(e.target === ov) ov.remove(); };
    document.body.appendChild(ov);
  }

  var box = document.getElementById('fut');
  if(!box) return;
  box.addEventListener('click', function(e){
    var tile = e.target.closest('.q'); if(!tile) return;
    var i = [].indexOf.call(box.querySelectorAll('.q'), tile);
    var label = (tile.querySelector('.n') || {}).textContent || '';
    fetch('/api/world', {credentials: 'same-origin', cache: 'no-store'}).then(function(r){ return r.json(); }).then(function(w){
      // the phone page lists these in WANT order, leaving out ones with no price yet
      var rows = WANT.map(function(s){ return (w.rows || []).filter(function(r){ return r.sym === s && r.last != null; })[0]; })
                     .filter(Boolean);
      if(rows[i]) show(rows[i], label);
    }).catch(function(){});
  });
})();
