/* World and Macro additions. Since jb-desk the Fed meeting odds, Nasdaq-100 breadth, Bitcoin order book
 * and the economic calendar sit on the MACRO tab (moved there from World); World keeps the VIX curve
 * warning (amber line under its header when short-term VIX is above 3-month VIX).
 * Data: /api/plus/edge, /api/plus/feeds/btc and /api/world (calendar). Hooked in by one <script> line
 * on world.html and on macro.html. */
(function () {
  'use strict';
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function get(p) { return fetch('/api/plus/' + p, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }); }
  var onMacro = /macro\.html/i.test(location.pathname), cal = document.getElementById('cal');
  var warn = null, box = null;
  if (onMacro) {
    var st = document.createElement('style');
    st.textContent = '#wpBox .grid{display:grid;grid-template-columns:1fr 1fr;gap:0 18px;} #wpBox .up{color:var(--green,#00ff41);} #wpBox .dn{color:var(--red,#ff3b30);}' +
      '#wpCal .ev{display:flex;gap:10px;padding:8px 6px;border-bottom:1px solid var(--line,#0d2740);align-items:baseline;}' +
      '#wpCal .ev .tm{min-width:118px;color:var(--dim,#6f8aa3);font-size:12px;} #wpCal .ev .ti{flex:1;} #wpCal .ev .ti small{display:block;color:var(--dim,#6f8aa3);font-size:11px;margin-top:2px;}' +
      '#wpCal .ev.past{opacity:.5;} #wpCal .imp{display:inline-block;font-size:10px;font-weight:700;border-radius:3px;padding:1px 5px;margin-right:6px;vertical-align:1px;}' +
      '#wpCal .imp.High{background:#ff3b30;color:#fff;} #wpCal .imp.Medium{background:#ffb648;color:#04101f;} #wpCal .imp.Low,#wpCal .imp.Holiday{background:#1b3550;color:#6f8aa3;}' +
      '#wpCal .day{color:var(--cyan,#00e5ff);font-size:12px;margin:14px 6px 2px;}' +
      '@media (max-width:600px){ #wpBox .grid{grid-template-columns:1fr;} #wpCal .ev .tm{min-width:84px;} }';
    document.head.appendChild(st);
    var anchor = document.getElementById('inds');
    anchor = anchor ? anchor.previousElementSibling : null;          // the "The indicators" heading
    box = document.createElement('div'); box.id = 'wpBox';
    var c = document.createElement('div'); c.id = 'wpCalWrap';
    c.innerHTML = '<h2>Economic calendar · next 7 days (New York time)</h2><div class="card" id="wpCal"><p class="note">Loading…</p></div>' +
      '<p class="note"><span class="imp High" style="background:#ff3b30;color:#fff;font-size:10px;font-weight:700;border-radius:3px;padding:1px 5px">HIGH</span> often moves the whole market (jobs report, inflation (CPI), Fed decisions). ' +
      '<b>Forecast</b> = what economists expect; <b>Prev</b> = last time. A number far from the forecast is what moves prices. Treasury auctions (the government borrowing money) can move rates and futures. ' +
      'Sources: Forex Factory (free), TreasuryDirect, the Federal Reserve. Moved here from the World tab.</p>';
    if (anchor) { anchor.parentNode.insertBefore(box, anchor); anchor.parentNode.insertBefore(c, anchor); }
    else { document.querySelector('.wrap').appendChild(box); document.querySelector('.wrap').appendChild(c); }
  } else if (cal) {
    // World: the calendar, odds, breadth and Bitcoin book now live on MACRO (the "Next big event" line stays here)
    var h2 = cal.previousElementSibling, note = cal.nextElementSibling;
    [h2, cal, note].forEach(function (e) { if (e) e.style.display = 'none'; });
    var mv = document.createElement('p'); mv.className = 'note';
    mv.innerHTML = 'The economic calendar, Fed meeting odds, Nasdaq-100 breadth and the Bitcoin order book are on the <a href="./#macro" target="_top" style="color:#5fc97e">Macro tab</a>.';
    cal.parentNode.insertBefore(mv, cal);
    warn = document.createElement('div'); warn.id = 'wpVix';
    warn.style.cssText = 'display:none;margin:10px 4px 0;padding:8px 12px;border:1px solid #ffb648;border-radius:8px;background:#2e2400;color:#ffe9a6;font-size:12px';
    var hdr = document.querySelector('header'); if (hdr) hdr.parentNode.insertBefore(warn, hdr.nextSibling);
  } else return;
  function nyTime(ms, withDay) {
    return new Date(ms).toLocaleString('en-US', withDay ? { timeZone: 'America/New_York', weekday: 'short', hour: 'numeric', minute: '2-digit' }
                                                        : { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' });
  }
  function drawCal(data) {
    var el = document.getElementById('wpCal'); if (!el || !data) return;
    var now = Date.now(), last = '', html = '';
    (data.calendar || []).forEach(function (e) {
      var day = new Date(e.t).toLocaleDateString('en-US', { timeZone: 'America/New_York', weekday: 'long', month: 'short', day: 'numeric' });
      if (day !== last) { html += '<div class="day">' + esc(day) + '</div>'; last = day; }
      var bits = [];
      if (e.actual) bits.push('Actual <b>' + esc(e.actual) + '</b>');
      if (e.forecast) bits.push((e.kind === 'auction' ? 'Size ' : 'Forecast ') + esc(e.forecast));
      if (e.previous) bits.push('Prev ' + esc(e.previous));
      html += '<div class="ev' + (e.t < now - 3600000 ? ' past' : '') + '"><div class="tm">' + (e.impact === 'Holiday' ? 'all day' : esc(nyTime(e.t))) + '</div>' +
        '<div class="ti"><span class="imp ' + esc(e.impact) + '">' + esc((e.impact || 'low').slice(0, 4).toUpperCase().replace('MEDI', 'MED')) + '</span>' + esc(e.title) +
        (bits.length ? '<small>' + bits.join(' · ') + '</small>' : '') + '</div></div>';
    });
    if (!html) html = '<p class="note">Nothing yet. The calendar loads within a minute of the board starting.</p>';
    if (data.calendar_errors && data.calendar_errors.length) html += '<p class="note err">Could not load: ' + esc(data.calendar_errors.join('; ')) + '</p>';
    el.innerHTML = html;
  }
  function k(v) { return v >= 1000 ? Math.round(v).toLocaleString() : v; }
  function draw(e, b) {
    var h = '';
    if (warn && e && e.vix) {
      warn.style.display = e.vix.state === 'inverted' ? '' : 'none';
      warn.innerHTML = '<b>VIX curve warning:</b> short-term VIX ' + e.vix.vix + ' is above 3-month VIX ' + e.vix.vix3m + '. ' + esc(e.vix.text);
    }
    var o = e && e.odds && e.odds.meetings;
    if (o && o.length) h += '<h2>Fed meeting odds · next ' + o.length + ' meetings (Kalshi)</h2><div class="card">' + o.map(function (m) {
      var x = m.bucket, w = m.week;
      return '<div style="margin:4px 0 10px"><div style="font-size:12px">' + esc(m.title) + '</div><div style="display:flex;height:16px;border-radius:3px;overflow:hidden;margin:4px 0">' +
        '<i style="width:' + x.cut + '%;background:#2e9e5b"></i><i style="width:' + x.hold + '%;background:#5a6b7d"></i><i style="width:' + x.hike + '%;background:#c0453b"></i></div>' +
        '<div class="note" style="margin:0">cut ' + x.cut + '% · hold ' + x.hold + '% · hike ' + x.hike + '%' + (w ? ' · change this week: cut ' + (w.cut >= 0 ? '+' : '') + w.cut + ' pts, hike ' + (w.hike >= 0 ? '+' : '') + w.hike + ' pts' : '') + '</div></div>';
    }).join('') + '</div><p class="note">What betting markets (Kalshi) say the Fed will do at each meeting, like Bloomberg\'s WIRP page. Rate cut odds rising usually helps the Nasdaq (and 3x Nasdaq funds even more).</p>';
    var br = e && e.breadth;
    if (br) h += '<h2>Nasdaq-100 breadth</h2><div class="card"><div style="display:flex;height:16px;border-radius:3px;overflow:hidden"><i style="width:' + (br.up / br.n * 100) + '%;background:#2e9e5b"></i><i style="width:' + (br.down / br.n * 100) + '%;background:#c0453b"></i></div>' +
      '<div class="note" style="margin:6px 0 0"><b class="up">' + br.up + ' up</b> · <b class="dn">' + br.down + ' down</b>' + (br.n50 ? ' · ' + br.above50 + ' of ' + br.n50 + ' above their 50-day average' : '') + '</div></div><p class="note">' + esc(br.note) + '</p>';
    if (b && b.mid) {
      h += '<h2>Bitcoin order book · ' + esc(b.src) + '</h2><div class="card">' +
        '<div style="font-size:16px;font-weight:700">$' + k(b.mid) + (b.since_close != null ? ' <span class="' + (b.since_close >= 0 ? 'up' : 'dn') + '" style="font-size:13px">' + (b.since_close >= 0 ? '+' : '') + b.since_close.toFixed(2) + '% since 4 pm</span>' : '') + '</div>';
      if (b.bid1 != null) {
        h += '<div style="display:flex;height:16px;border-radius:3px;overflow:hidden;margin:8px 0 4px"><i style="width:' + b.tilt + '%;background:#2e9e5b"></i><i style="width:' + (100 - b.tilt) + '%;background:#c0453b"></i></div>' +
          '<div class="note" style="margin:0">Within 1% of the price: <b class="up">' + b.bid1 + ' BTC waiting to buy</b> vs <b class="dn">' + b.ask1 + ' BTC waiting to sell</b> (within 2%: ' + b.bid2 + ' vs ' + b.ask2 + ').</div>' +
          '<div class="grid" style="margin-top:6px"><div><small class="note">Biggest buy walls</small><br>' + (b.bid_walls || []).map(function (w) { return '$' + k(w.p) + ': ' + w.btc + ' BTC'; }).join('<br>') + '</div>' +
          '<div><small class="note">Biggest sell walls</small><br>' + (b.ask_walls || []).map(function (w) { return '$' + k(w.p) + ': ' + w.btc + ' BTC'; }).join('<br>') + '</div></div>';
      }
      h += '</div><p class="note">Crypto is the only big market open on weekends: heavy selling or a big sell wall near the price on a Saturday or Sunday often comes before a rough Sunday 6 pm futures open. ' +
        'From Gemini\'s free public order book (one exchange, not the whole market), refreshed every minute; Yahoo gives the price if Gemini does not answer.</p>';
    }
    if (box) box.innerHTML = h;
  }
  function load() {
    var w = [get('edge').catch(function () { return null; })];
    if (onMacro) {
      w.push(get('feeds/btc').catch(function () { return null; }));
      fetch('/api/world', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).then(drawCal).catch(function () {});
    }
    Promise.all(w).then(function (r) { draw(r[0], r[1]); });
  }
  load(); setInterval(function () { if (!document.hidden) load(); }, 60000);
})();
