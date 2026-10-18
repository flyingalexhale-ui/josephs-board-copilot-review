/* 1W and 52W range bars (jb-ranges): two small bars under each tile's O/N overnight range bar,
 * on the IBKR and Webull boards (tiles and list rows).
 *   1W  = lowest and highest price of the last 7 days (all sessions: the board's own price record,
 *         after-hours and overnight included, plus Yahoo's daily highs and lows)
 *   52W = lowest and highest price of the last year (Yahoo daily highs and lows + the board's record)
 * The dot is the price now; green = at a new high for that range, red = at a new low.
 * Data: /api/ranges (gateway/ranges.py), refreshed every 10 minutes. The O/N bar is untouched.
 */
(function () {
  'use strict';
  var Y = {}, asked = '', nextAsk = 0;

  function jb() { return window.__jb && window.__jb.state; }
  function fmt(p) {
    if (p == null) return '';
    if (p >= 1000) return p.toLocaleString(undefined, { maximumFractionDigits: 2 });
    var dec = (String(+p.toFixed(6)).split('.')[1] || '').length;
    return p.toFixed(Math.max(2, Math.min(6, dec)));
  }
  function nice(d) {   // '2026-07-14' -> 'Jul 14, 2026'
    var p = (d || '').split('-'); if (p.length !== 3) return d || '';
    return ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+p[1] - 1] + ' ' + (+p[2]) + ', ' + p[0];
  }
  function etDay(ms) {   // New York date as YYYY-MM-DD
    try { return new Date(ms).toLocaleDateString('en-CA', { timeZone: 'America/New_York' }); } catch (e) { return new Date(ms).toISOString().slice(0, 10); }
  }

  function load(force) {
    var S = jb(); if (!S || !S.symbols || !S.symbols.length) return;
    var list = S.symbols.slice().sort().join(',');
    if (!force && list === asked && Date.now() < nextAsk) return;
    asked = list; nextAsk = Date.now() + 10 * 60e3;
    fetch('/api/ranges?syms=' + encodeURIComponent(list), { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.r) return;
        Y = d.r;
        var missing = S.symbols.some(function (s) { return !Y[s]; });
        if (missing) nextAsk = Date.now() + 60e3;   // a symbol still waiting for its first Yahoo year: ask again in a minute
        paint();
      }).catch(function () {});
  }

  // low / high / price now for one range from the board's record (+ Yahoo days since `fromDay`)
  function calc(sym, ms, useYear) {
    var S = jb(), hist = S && S.history[sym];
    if (!hist || !hist.length) return null;
    var now = hist[hist.length - 1].p, from = Date.now() - ms, lo = Infinity, hi = -Infinity, loD = null, hiD = null;
    for (var i = hist.length - 1; i >= 0 && hist[i].t >= from; i--) {
      var p = hist[i].p;
      if (p < lo) { lo = p; loD = etDay(hist[i].t); }
      if (p > hi) { hi = p; hiD = etDay(hist[i].t); }
    }
    var y = Y[sym];
    if (y) {
      if (useYear) {
        if (y.lo < lo) { lo = y.lo; loD = y.loD; }
        if (y.hi > hi) { hi = y.hi; hiD = y.hiD; }
      } else {
        var first = etDay(from);
        (y.days || []).forEach(function (d) {
          if (d[0] < first) return;
          if (d[2] < lo) { lo = d[2]; loD = d[0]; }
          if (d[1] > hi) { hi = d[1]; hiD = d[0]; }
        });
      }
    } else if (useYear) return null;          // no year of data yet: hide the 52W bar
    if (!(hi > lo)) return null;
    return { lo: lo, hi: hi, loD: loD, hiD: hiD, now: now };
  }

  function bar(cls, label) {
    var r = document.createElement('div'); r.className = 'x-range ' + cls;
    r.innerHTML = '<span class="x-lbl">' + label + '</span><span class="x-lo"></span><span class="x-bar"><span class="x-dot"></span></span><span class="x-hi"></span>';
    return r;
  }

  function fill(el, v, name) {
    if (!v) { el.style.display = 'none'; return; }
    el.style.display = '';
    var f = Math.max(0, Math.min(1, (v.now - v.lo) / (v.hi - v.lo)));
    el.querySelector('.x-lo').textContent = 'L ' + fmt(v.lo);
    el.querySelector('.x-hi').textContent = 'H ' + fmt(v.hi);
    var dot = el.querySelector('.x-dot');
    dot.style.left = (f * 100) + '%';
    dot.className = 'x-dot' + (v.now >= v.hi ? ' at-hi' : v.now <= v.lo ? ' at-lo' : '');
    var offHi = (v.now - v.hi) / v.hi * 100, offLo = (v.now - v.lo) / v.lo * 100;
    el.title = name + ' range: low ' + fmt(v.lo) + (v.loD ? ' (' + nice(v.loD) + ')' : '') + ', high ' + fmt(v.hi) + (v.hiD ? ' (' + nice(v.hiD) + ')' : '') +
      '. The dot is the price now: ' + offHi.toFixed(1) + '% from the high, +' + offLo.toFixed(1) + '% above the low.';
  }

  function paint() {
    var S = jb(); if (!S || document.hidden) return;
    load(false);
    Array.prototype.forEach.call(document.querySelectorAll('.on-range'), function (on) {
      var root = on.closest('[data-sym]'); if (!root) return;
      var sym = root.dataset.sym, w = on.nextElementSibling, y;
      if (!w || !w.classList.contains('x-w1')) {
        w = bar('x-w1', '1W'); y = bar('x-y1', '52W');
        on.parentNode.insertBefore(w, on.nextSibling); on.parentNode.insertBefore(y, w.nextSibling);
      } else y = w.nextElementSibling;
      fill(w, calc(sym, 7 * 864e5, false), '1-week');
      fill(y, calc(sym, 366 * 864e5, true), '52-week');
    });
  }

  var css = document.createElement('style');
  css.textContent =
    '.x-range{display:flex;align-items:center;gap:5px;font-size:8px;line-height:1;color:var(--text-dim,#6f8aa3);margin:0 0 4px;font-variant-numeric:tabular-nums;opacity:.9}' +
    '.x-range .x-lbl{color:#9fb3c8;font-weight:700;min-width:20px}.x-range .x-lo{min-width:48px}.x-range .x-hi{min-width:48px;text-align:right}' +
    '.x-range .x-bar{flex:1;height:3px;background:linear-gradient(90deg,#4a1c1c,#1b3550,#12401f);border-radius:2px;position:relative}' +
    '.x-range .x-dot{position:absolute;top:-2px;width:7px;height:7px;margin-left:-3.5px;border-radius:50%;background:#dfe6ee}' +
    '.x-range .x-dot.at-hi{background:var(--green,#3ee07a)}.x-range .x-dot.at-lo{background:var(--red,#ff5c52)}' +
    '.x-range.x-y1{margin-bottom:6px}' +
    // phone polish: the stock count next to US EQUITIES stayed a tall pill beside the taller menu
    '.group-title .count{align-self:center}';
  document.head.appendChild(css);

  setInterval(paint, 2000);
  setTimeout(function () { load(true); paint(); }, 1500);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) paint(); });
})();
