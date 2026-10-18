/* Night desk section: overnight and session features (jb-night). Data: /api/plus/night */
(function () {
  'use strict';
  var ND = window.ND;
  var sw = ND.section('night-alerts', 'Session alerts (phone)');
  var vn = ND.section('night-venue', 'Overnight venue');
  var bz = ND.section('night-busy', 'Busy overnight hours');
  var ex = ND.section('night-exch', 'New exchange overnight session (Dec 6, 2026)');
  var lg = ND.section('night-log', 'Recent session alerts');
  var SW = [
    ['on_break', 'Overnight high / low broken after the open', 'After 9:30 am, a push when a stock crosses above its overnight (8 pm - 4 am) high or below its overnight low. Once per stock per side per day.'],
    ['pm_break', 'Pre-market high / low broken after the open', 'After 9:30 am, a push when a stock crosses its pre-market (4:00 - 9:30 am) high or low. The classic "gap and go" trigger.'],
    ['venue', 'Overnight venue looks down', 'Overnight, a push if every stock trading on IBKR\'s overnight venue stops updating for 15+ minutes at the same time while futures keep moving (like the Blue Ocean outage on Aug 5, 2024), and another when prices come back.']
  ];
  var drawn = false;
  function load() {
    ND.api('night').then(function (d) {
      if (!drawn) { drawn = true; ND.switches(sw, SW, d.settings, function (v) { return ND.post('night/settings', { settings: v }); }); }
      var v = d.venue || {};
      vn.innerHTML = '<div class="card" style="padding:10px 12px">' + (d.session !== 'overnight' ? '<span class="chip c-d">NOT OVERNIGHT NOW</span> The check runs 8 pm - 4 am on trading nights.' :
        v.down ? '<span class="chip c-r">LOOKS DOWN</span> since ' + ND.esc(ND.time(v.since)) + '. All ' + v.stocks + ' stocks that were trading overnight stopped while futures kept moving.' :
        '<span class="chip c-g">OK</span> ' + (v.stocks ? v.stocks + ' stocks trading overnight right now.' : 'No stock has traded overnight in the last 40 minutes yet.')) +
        '<div class="note" style="margin:6px 0 0">Spreads: ' + (d.quotes_connected ? 'bid/ask prices are coming in from IBKR (SPREAD chip on each IBKR tile).' : '<span class="warn">no bid/ask from the IBKR bridge yet (restart it after the install: <code>./jb restart ibkr</code>).</span>') + '</div></div>';
      var fb = d.fills_by_hour || {}, hours = [16, 17, 18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9], mx = 0;
      hours.forEach(function (h) { mx = Math.max(mx, fb[h] || 0); });
      bz.innerHTML = '<p class="note">NYSE research (June 2025): overnight volume peaks around <b>9 pm</b> (Asian markets open) and again around <b>3 am</b> (Asian markets close). ' +
        'More traders at those times usually means tighter spreads and better fills. On the price chart, the <b>Busy night hours</b> button shades those hours.</p>' +
        '<div class="card tbl"><table><tr><th>Hour (ET)</th><th>Busy?</th><th>Your fills</th><th style="width:40%"></th></tr>' + hours.map(function (h) {
          var busy = h === 21 || h === 3, n = fb[h] || 0, lab = (h % 12 || 12) + (h < 12 ? ' am' : ' pm');
          return '<tr><td>' + lab + '</td><td>' + (busy ? '<span class="chip c-p">PEAK</span>' : h >= 4 && h < 9 ? '<span class="chip c-d">pre-market</span>' : h >= 16 && h < 20 ? '<span class="chip c-d">after-hours</span>' : '') +
            '</td><td>' + n + '</td><td><div class="bar"><i style="width:' + (mx ? n / mx * 100 : 0) + '%"></i></div></td></tr>';
        }).join('') + '</table></div><p class="note">Your fills = trades in your My trading journal outside regular hours, by the hour they filled.</p>';
      lg.innerHTML = ND.log(d.log);
    }).catch(function () {});
  }
  ex.innerHTML = '<div class="card" style="padding:10px 12px;line-height:1.55">From <b>Sunday Dec 6, 2026</b>, Nasdaq and NYSE run their own overnight session: <b>9 pm to 4 am ET</b>, Sunday night to Friday morning, with a ' +
    '<b>pause from 8 to 9 pm</b> every night. Overnight trades there are <b>limit orders only</b>, have a <b>20% price band</b> (orders far from the last price are rejected), and trades from 9 pm to midnight carry the <b>next day\'s date</b>.' +
    '<br><br>What the board does: the session line under the badge on the IBKR and Webull boards shows the pause and the exchange session; the OVERNIGHT box keeps measuring 8 pm - 4 am (IBKR\'s overnight route still starts at 8 pm); your journal counts late-night fills toward the next trading day.</div>';
  load(); setInterval(load, 30000);
})();
