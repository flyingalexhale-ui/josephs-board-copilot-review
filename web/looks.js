/* Alex Hale's Board -- the Polished, Pure black and Clean light looks (jb-looks).
   Loaded first on every page. It never changes what a page does, only its colours and a few
   finishing touches: it reads the look picked on the theme button (saved per phone/computer as
   jb_theme) and swaps each colour the page uses for that look's version of it, in the page's
   styles, in colours set by the page's code and in the charts. Since jb-looktrim it also draws
   Night (every colour turned dim red, brightness kept), and Classic / Old light are no longer
   offered (a saved Classic acts as Polished, Old light as Clean light). If anything here fails,
   the page simply keeps the Classic colours. */
(function () {
  'use strict';
  if (window.jbLooks) return;

  // ---------------------------------------------------------------- which look
  var OLD = { classic: 'dark', light: 'clean' };     // jb-looktrim: looks no longer offered -> their closest look
  function stored() { try { var t = localStorage.getItem('jb_theme') || 'dark'; return OLD[t] || t; } catch (e) { return 'dark'; } }
  function effective(t) {
    t = t || stored();
    if (t !== 'auto') return t;
    var h = new Date().getHours();
    return (h >= 21 || h < 6) ? 'night' : 'dark';
  }
  // dark = Polished (the normal look), black = Pure black (polished), clean = Clean light (polished)
  // night = Night (polished, every colour turned dim red; jb-looktrim)
  function lookOf(t) { var e = effective(t); return e === 'dark' ? 'pol' : e === 'black' ? 'black' : e === 'clean' ? 'clean' : e === 'night' ? 'night' : ''; }
  var look = '';

  // ---------------------------------------------------------------- colour maths
  function hsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, h = 0, s = 0, d = mx - mn;
    if (d) {
      s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
      h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
      h *= 60;
    }
    return [h, s, l];
  }
  function rgb(h, s, l) {
    function f(n) { var k = (n + h / 30) % 12, a = s * Math.min(l, 1 - l); return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))); }
    return [f(0), f(8), f(4)];
  }
  function hex(c) { return '#' + c.map(function (v) { return ('0' + Math.max(0, Math.min(255, v)).toString(16)).slice(-2); }).join(''); }
  function parseHex(s) { var x = parseInt(s, 16); return [(x >> 16) & 255, (x >> 8) & 255, x & 255]; }

  // Polished: the neon green, red and cyan become calmer versions (used by all three looks)
  var POL = { '00ff41': '3ee07a', 'ff3b30': 'ff5c52', '00e5ff': '5cc8db' };
  // Clean light: the board's main background colours, picked by hand
  var CLEAN = {
    '04101f': 'dfe4eb', '020a14': 'f7f9fb', '071a30': 'ffffff', '0b2238': 'f3f5f8', '0d2740': 'e1e6ed',
    '0f2c47': 'e6eaf0', '164060': 'c4cedb', '0b2a44': 'edf1f5', '0e2a3a': 'e5edf2', '1b3550': 'd2dae4',
    '2a3d52': 'bfc8d3', '12385a': 'cbd4df', '061529': 'dfe4eb', '000000': 'ffffff', '1e5631': '2e7d4a',
    '173f27': 'cfe5d6', '1d5230': 'd9ecdf', '2f7a44': '1f6b3a', '3ea85c': '2e8a4c'
  };
  function blueish(h) { return h >= 190 && h <= 235; }
  function mapRGB(c) {
    var k = hex(c).slice(1);
    if (POL[k]) { k = POL[k]; c = parseHex(k); }
    if (look === 'black') {
      var x = hsl(c[0], c[1], c[2]);
      if (blueish(x[0]) && x[2] < 0.42 && (x[1] < 0.9 || x[2] < 0.3)) return rgb(0, 0, Math.max(0, x[2] - 0.07) * 0.95);
      if (blueish(x[0]) && x[1] < 0.5) return rgb(0, 0, x[2]);
      return c;
    }
    if (look === 'night') {                              // brightness kept, colour turned dim red
      var lum = (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;
      return rgb(4, 0.85, 0.015 + 0.58 * Math.pow(lum, 0.65));
    }
    if (look === 'clean') {
      if (CLEAN[k]) return parseHex(CLEAN[k]);
      if (k === 'ffffff') return c;                      // white text sits on coloured buttons
      var y = hsl(c[0], c[1], c[2]), h = y[0], s = y[1], l = y[2];
      if (l < 0.3) {
        if (s < 0.35 || blueish(h)) return rgb(h, Math.min(s, 0.25), 0.95 - l * 0.3);   // dark backgrounds -> light
        if (l >= 0.18 && s >= 0.6) return rgb(h, Math.min(s, 0.75), 0.42);              // strong dark colours (e.g. SELL ALL) stay strong
        return rgb(h, Math.min(s, 0.6), 0.92);                                           // dark tints (red, gold...) -> pale tint
      }
      if (s < 0.35 && l < 0.7) return rgb(h, s, l * 0.75);                              // dim grey text -> a bit darker
      if (s < 0.5 && l >= 0.7) return rgb(h, s * 0.6, Math.max(0.12, 1.02 - l));        // light text -> dark text
      return rgb(h, Math.min(s, 0.85), (h >= 45 && h <= 200) ? 0.34 : 0.44);             // bright colours -> deeper, readable on white
    }
    return c;
  }
  var RE = /#([0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\(\s*(\d+)\s*[\s,]\s*(\d+)\s*[\s,]\s*(\d+)\s*(?:[,\/]\s*([\d.]+%?)\s*)?\)/g;
  function mapText(s) {
    if (!look || typeof s !== 'string' || (s.indexOf('#') < 0 && s.indexOf('rgb') < 0)) return s;
    return s.replace(RE, function (m, hx, r, g, b, a) {
      var c, alpha = null;
      if (hx) {
        if (hx.length === 3) hx = hx[0] + hx[0] + hx[1] + hx[1] + hx[2] + hx[2];
        c = parseHex(hx.slice(0, 6)); if (hx.length === 8) alpha = hx.slice(6);
        var o = mapRGB(c);
        return hex(o) + (alpha || '');
      }
      c = [+r, +g, +b];
      var black = c[0] + c[1] + c[2] === 0, white = c[0] === 255 && c[1] === 255 && c[2] === 255;
      if (a != null && look === 'clean' && white) return 'rgba(0,0,0,' + a + ')';     // white glows/highlights -> soft grey on light
      if (a != null && black) return m;                                                 // shadows stay shadows
      var n = mapRGB(c);
      return a != null ? 'rgba(' + n[0] + ',' + n[1] + ',' + n[2] + ',' + a + ')' : 'rgb(' + n[0] + ',' + n[1] + ',' + n[2] + ')';
    });
  }

  // ---------------------------------------------------------------- finishing touches (scoped to the 3 looks)
  function board(list) {
    return list.map(function (r) {
      var pre = r[2] ? 'html.jb-look-clean ' : 'html.jb-pol ';
      var sel = r[0].split(',').map(function (x) {
        x = x.trim();
        return (x === 'body' ? [pre + 'body.layout-tiles', pre + 'body.layout-rows']
                             : [pre + 'body.layout-tiles ' + x, pre + 'body.layout-rows ' + x]).join(',');
      }).join(',');
      return sel + r[1];
    }).join('');
  }
  var CSS =
    // board pages (IBKR / Webull) only: board.js gives their body layout-tiles or layout-rows
    board([
      ['body', '{background:linear-gradient(180deg,#061529 0%,#04101f 320px) fixed;}'],
      ['.cmdbar', '{background:linear-gradient(90deg,#173f27,#1d5230);font-family:var(--jb-sans);font-weight:600;letter-spacing:.8px;}'],
      ['.cmdbar .go', '{border-radius:3px;}'],
      ['header', '{background:transparent;border-bottom:1px solid #164060;}'],
      ['.brand .mark', '{font-family:var(--jb-sans);font-weight:700;letter-spacing:.2px;display:inline-flex;align-items:center;gap:10px;}'],
      ['.brand .mark::before', '{content:"AH";font-size:12px;font-weight:800;color:#ffffff;background:linear-gradient(135deg,#2f7a44,#1e5631);border-radius:6px;width:30px;height:30px;display:inline-flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,.35);}'],
      ['.clock .time', '{text-shadow:none;font-weight:600;}'],
      ['.status, .clock .date, .foot, footer.note', '{font-family:var(--jb-sans);}'],
      ['.dot.live', '{box-shadow:0 0 4px #00ff41;}'],
      ['.controls', '{border-radius:8px;border-color:#0d2740;box-shadow:0 1px 2px rgba(0,0,0,.3);}'],
      ['.controls label', '{font-family:var(--jb-sans);font-size:11px;color:#9fb3c8;font-weight:500;}'],
      ['.controls input', '{border-radius:5px;}'],
      ['.btn', '{border-radius:5px;font-family:var(--jb-sans);font-weight:600;letter-spacing:.2px;font-size:12px;}'],
      ['.btn.ghost', '{color:#cfd8e3;}'],
      ['.btn:not(.ghost)', '{color:#ffffff;}', 1],
      ['.group-title', '{font-family:var(--jb-sans);background:linear-gradient(90deg,rgba(47,122,68,.45),rgba(47,122,68,.10));border-left:3px solid #3ea85c;border-radius:5px;letter-spacing:1.2px;font-size:11.5px;padding:7px 12px;}'],
      ['.group-title .count', '{color:#cfe0ee;background:rgba(0,0,0,.25);border-radius:10px;padding:0 8px;font-size:11px;}'],
      ['.grid', '{grid-template-columns:repeat(auto-fill,minmax(235px,1fr));gap:6px;padding:6px;background:#12385a;border:0;border-radius:8px;}'],
      ['.tile', '{border-radius:6px;border:1px solid #164060;box-shadow:0 1px 3px rgba(0,0,0,.35);}'],
      ['.tile.zone-up', '{background:linear-gradient(rgba(0,255,65,.07),rgba(0,255,65,.07)),#071a30;}'],
      ['.tile.zone-down', '{background:linear-gradient(rgba(255,59,48,.07),rgba(255,59,48,.07)),#071a30;}'],
      ['.sym', '{color:#f2f2f2;}'],
      ['.changes', '{gap:2px;background:transparent;}'],
      ['.chg', '{border-radius:3px;min-width:0;overflow:hidden;}'],
      ['.changes', '{grid-template-columns:repeat(5,minmax(0,1fr));}'],
      ['.sess-boxes', '{grid-template-columns:repeat(3,minmax(0,1fr));}'],
      ['.chg .lbl', '{font-size:9px;color:#9fb3c8;}'],
      ['.chg.sess .lbl', '{font-size:8px;color:#ffc400;}'],
      ['.foot', '{font-size:10px;}'],
      ['.warn', '{border-radius:6px;}']
    ]) +
    // dashboard top bar and the phone page
    'html.jb-pol #bar .brand,html.jb-pol .tab,html.jb-pol .xlink{font-family:var(--jb-sans);}' +
    'html.jb-pol #bar .brand{color:#2f7a44;font-weight:700;letter-spacing:.06em;}' +
    'html.jb-pol .tab{font-weight:600;}' +
    'html.jb-pol #jb-theme{font-family:var(--jb-sans);}' +
    // every page: softer corners on buttons and boxes you type in
    'html.jb-pol button,html.jb-pol input,html.jb-pol select{border-radius:5px;}' +
    'html.jb-pol .tab{border-radius:8px 8px 0 0;}';

  // ---------------------------------------------------------------- applying it
  var ruleOrig = typeof WeakMap === 'function' ? new WeakMap() : null;
  var COLOR_ATTRS = ['fill', 'stroke', 'stop-color', 'color', 'bgcolor'];

  function doRule(rule) {
    if (rule.cssRules) { for (var i = 0; i < rule.cssRules.length; i++) doRule(rule.cssRules[i]); }
    var st = rule.style; if (!st) return;
    var o = ruleOrig.get(rule);
    if (!o) {
      o = {};
      for (var j = 0; j < st.length; j++) {
        var p = st[j], v = st.getPropertyValue(p);
        if (/#|rgb/.test(v)) o[p] = [v, st.getPropertyPriority(p)];
      }
      ruleOrig.set(rule, o);
    }
    for (var q in o) {
      if (q === '-jb-color') continue;
      var want = look ? mapText(o[q][0]) : o[q][0];
      if (st.getPropertyValue(q) !== want) st.setProperty(q, want, o[q][1]);
    }
    // Clean light: the board's green buttons stay solid green, so their text stays white
    var bg = (o['background-color'] || o['background'] || [''])[0];
    if (/#1e5631|rgb\(30, 86, 49\)/i.test(bg)) {
      if (!o['-jb-color']) o['-jb-color'] = [st.getPropertyValue('color'), st.getPropertyPriority('color')];
      var c0 = o['-jb-color'];
      if (look === 'clean') st.setProperty('color', '#ffffff', c0[1]);
      else if (c0[0]) st.setProperty('color', o.color ? (look ? mapText(o.color[0]) : o.color[0]) : c0[0], c0[1]);
      else st.removeProperty('color');
    }
  }
  function doSheet(sh) {
    var rules; try { rules = sh.cssRules; } catch (e) { return; }   // not readable (another site) or not loaded yet
    if (!rules) return;
    for (var i = 0; i < rules.length; i++) { try { doRule(rules[i]); } catch (e) {} }
  }
  function doEl(el) {
    if (el.nodeType !== 1) return;
    if (el.tagName === 'STYLE' && el.sheet) doSheet(el.sheet);
    var o = el.__jbo || (el.__jbo = {}), m = el.__jbm || (el.__jbm = {});
    var names = ['style'].concat(COLOR_ATTRS);
    for (var i = 0; i < names.length; i++) {
      var n = names[i];
      if (!el.hasAttribute(n)) continue;
      var v = el.getAttribute(n);
      if (m[n] !== v) o[n] = v;                     // the page set it: remember the page's own value
      var want = look ? mapText(o[n]) : o[n];
      if (v !== want) { m[n] = want; el.setAttribute(n, want); } else m[n] = v;
    }
  }
  function doTree(root) {
    if (!root || root.nodeType !== 1) return;
    doEl(root);
    var all = root.querySelectorAll('[style],[fill],[stroke],[stop-color],[color],[bgcolor],style');
    for (var i = 0; i < all.length; i++) doEl(all[i]);
  }
  function doAll() {
    var sheets = document.styleSheets;
    for (var i = 0; i < sheets.length; i++) doSheet(sheets[i]);
    if (document.documentElement) doTree(document.documentElement);
  }

  var styleEl = null, obs = null;
  function refresh() {
    if (!ruleOrig) return;
    var was = look;
    look = lookOf();
    var r = document.documentElement;
    r.classList.toggle('jb-pol', !!look);
    r.classList.toggle('jb-look-black', look === 'black');
    r.classList.toggle('jb-look-clean', look === 'clean');
    r.style.setProperty('--jb-sans', "-apple-system,BlinkMacSystemFont,'SF Pro Text','Segoe UI',Inter,Roboto,'Helvetica Neue',Arial,sans-serif");
    if (!styleEl && document.head) {
      styleEl = document.createElement('style'); styleEl.id = 'jb-looks-css'; styleEl.textContent = CSS;
      document.head.appendChild(styleEl);
    }
    if (look || was) { try { doAll(); } catch (e) {} }
    if (window.top === window) {
      var m = document.querySelector('meta[name="theme-color"]');
      if (m && look) m.setAttribute('content', look === 'clean' ? '#dfe4eb' : look === 'black' ? '#000000' : look === 'night' ? '#0a0101' : '#04101f');
    }
    if (look && !obs && window.MutationObserver) {
      obs = new MutationObserver(function (list) {
        if (!look) return;
        for (var i = 0; i < list.length; i++) {
          var mu = list[i];
          if (mu.type === 'attributes') { try { doEl(mu.target); } catch (e) {} continue; }
          var t = mu.target;
          if (t.nodeType === 1 && t.tagName === 'STYLE') { try { doEl(t); } catch (e) {} }
          for (var j = 0; j < mu.addedNodes.length; j++) {
            var n = mu.addedNodes[j];
            if (n.nodeType === 1) { try { doTree(n); } catch (e) {} if (n.tagName === 'LINK') n.addEventListener('load', function () { try { doAll(); } catch (e) {} }); }
          }
        }
      });
      obs.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['style'].concat(COLOR_ATTRS) });
    }
  }

  // charts drawn by code: their colours pass through the same swap
  try {
    var P = window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype;
    if (P) ['fillStyle', 'strokeStyle', 'shadowColor'].forEach(function (k) {
      var d = Object.getOwnPropertyDescriptor(P, k);
      if (!d || !d.set) return;
      Object.defineProperty(P, k, { configurable: true, enumerable: d.enumerable, get: d.get,
        set: function (v) { d.set.call(this, typeof v === 'string' ? mapText(v) : v); } });
    });
    var G = window.CanvasGradient && CanvasGradient.prototype;
    if (G && G.addColorStop) { var add = G.addColorStop; G.addColorStop = function (o, c) { return add.call(this, o, mapText(c)); }; }
  } catch (e) {}

  window.jbLooks = { refresh: refresh, effective: effective, map: mapText, look: function () { return look; } };
  look = lookOf();
  if (look) document.documentElement.classList.add('jb-pol');
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh); else refresh();
  window.addEventListener('load', function () { if (look) { try { doAll(); } catch (e) {} } });
  window.addEventListener('storage', function (e) { if (e.key === 'jb_theme') refresh(); });
  setInterval(function () { if (stored() === 'auto' && lookOf() !== look) refresh(); }, 60000);
})();

/* jb-desk: page titles in the formal engraved font with a gradient (titles.js, titles.css) */
(function(){ if(document.getElementById('jbTitlesJs')) return; var s = document.createElement('script'); s.id = 'jbTitlesJs'; s.src = 'titles.js'; (document.head || document.documentElement).appendChild(s); })();

/* jb-phone: phone polish on every page (phone-polish.js; phones only) */
(function(){ if(document.getElementById('jbPhonePolishJs')) return; var s = document.createElement('script'); s.id = 'jbPhonePolishJs'; s.src = 'phone-polish.js'; (document.head || document.documentElement).appendChild(s); })();
