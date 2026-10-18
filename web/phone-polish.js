/* Alex Hale's Board -- phone polish (jb-phone, October 10 list). Phones and small touch screens only;
 * the computer is unchanged. Loaded on every page by looks.js. Adds, never removes:
 *   1 sideways drift: the page slides back to the middle by itself when you let go (and the full board's
 *     tabs no longer get wider than the screen, which also pushed the bottom bar up off the bottom)
 *   2 full board: the Futures mood and Picks mood meters scroll away with the page
 *   3 full board: a cleaner tab list (big boxes, a Close button, the tab you are on marked)
 *   4 Phone page: Channel, Board and look buttons the same size and colour (Board = the computer view);
 *     the Moves / Ticket / My trading... chips fit the screen; Your holdings sits under the Order ticket
 *   5 Night Desk section tabs and Portfolio's Add a trade boxes in even rows
 *   6 the bottom bar's buttons look the same on every page (System check showed only Back / Home / Chart)
 */
(function () {
  'use strict';
  if (window.__jbPhonePolish) return;
  window.__jbPhonePolish = true;
  var mq = window.matchMedia && window.matchMedia('(max-width: 640px), (pointer: coarse) and (max-width: 1024px)');
  if (!mq || !mq.matches) return;
  var top = false; try { top = window.top === window; } catch (e) {}
  var file = (location.pathname.split('/').pop() || 'index.html').toLowerCase();
  var onBoard = file === 'index.html' || file === '';

  var css = document.createElement('style');
  css.textContent =
    'html,body{overscroll-behavior-x:none;}' +
    /* 6: the bottom bar keeps its own look whatever the page's buttons look like */
    '#jbNav button{flex:1 1 0 !important;min-width:0 !important;width:auto !important;margin:0 !important;border:0 !important;border-radius:0 !important;' +
    'padding:6px 0 4px !important;font-size:11px !important;font-weight:400 !important;color:#8fd8ff !important;box-shadow:none !important;min-height:56px !important;}' +
    '#jbNav button:not(.on){background:transparent !important;} #jbNav button.on{background:#0d2a40 !important;color:#39e07a !important;}' +
    /* ticket: the "Good for" menu stays inside the card */
    '.tk-row label{display:flex;flex-wrap:wrap;align-items:center;gap:6px;max-width:100%;} .tk-tif{max-width:100%;min-width:0;}' +
    /* 5: Night Desk section tabs in even rows */
    '#ndtabs{display:grid !important;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px !important;border-bottom:0 !important;}' +
    '#ndtabs button{margin:0 !important;border:1px solid var(--line-bright,#164060) !important;border-radius:8px !important;text-align:center;padding:9px 4px !important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}' +
    '#ndtabs button.on{background:var(--line-bright,#164060) !important;color:#fff !important;}' +
    /* 5: Portfolio's Add a trade boxes in even rows */
    '.jb-tradeform{display:grid !important;grid-template-columns:1fr 1fr;gap:8px !important;}' +
    '.jb-tradeform > *{width:100% !important;min-width:0;margin:0 !important;box-sizing:border-box;}' +
    '.jb-tradeform > button{grid-column:1 / -1;min-height:42px;}' +
    /* 4: Phone page chips fit the screen */
    '#jbJump{overflow:visible !important;flex-wrap:nowrap;gap:5px !important;padding:2px 0 8px !important;}' +
    '#jbJump button{flex:1 1 auto !important;padding:0 4px !important;font-size:11.5px !important;white-space:nowrap;min-width:0;}' +
    /* 4: Phone page header buttons */
    '.jb-hbtns{display:flex;flex-direction:column;gap:6px;align-items:stretch;margin-bottom:4px;}' +
    '.jb-hbtns > a,.jb-hbtns > button{display:flex !important;align-items:center;justify-content:center;box-sizing:border-box;width:118px !important;height:34px !important;' +
    'margin:0 !important;padding:0 8px !important;border:1px solid #5cc8db !important;border-radius:8px !important;background:rgba(92,200,219,.08) !important;' +
    'color:#5cc8db !important;font-family:inherit !important;font-size:12.5px !important;font-weight:700 !important;text-decoration:none;white-space:nowrap;overflow:hidden;cursor:pointer;}' +
    'html.jb-look-clean{--jbh-b:hsl(193,64%,32%);--jbh-t:hsl(193,69%,24%);--jbh-bg:hsla(193,64%,32%,.10);}' +
    'html.jb-look-clean .jb-hbtns > a,html.jb-look-clean .jb-hbtns > button{border-color:var(--jbh-b) !important;color:var(--jbh-t) !important;background:var(--jbh-bg) !important;}' +
    /* 3: the tab list */
    '.mb-menu.jb-tabsheet{background:rgba(0,0,0,.6);}' +
    '.mb-menu.jb-tabsheet > div{left:8px;right:8px;top:max(10px,env(safe-area-inset-top));padding:12px;border-radius:14px;max-height:calc(100vh - 90px - env(safe-area-inset-top));}' +
    '.jb-tabsheet .ts-h,.jb-tabsheet .ts-g{position:static !important;left:auto;right:auto;top:auto;max-height:none !important;overflow:visible !important;background:none !important;border:0 !important;padding:0 !important;border-radius:0 !important;}' +
    '.jb-tabsheet button{width:auto;border-bottom:0;font-size:13px;}' +
    '.jb-tabsheet .ts-h{display:flex;justify-content:space-between;align-items:center;margin:0 2px 10px;}' +
    '.jb-tabsheet .ts-h b{font-size:12px;letter-spacing:.14em;color:#8fd8ff;}' +
    '.jb-tabsheet .ts-h button.ts-x{width:auto !important;display:inline-flex !important;align-items:center;min-height:36px;padding:0 14px;border:1px solid #2bb6e6;border-radius:8px;font-size:13px;color:#bfeeff;}' +
    '.jb-tabsheet .ts-g{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;}' +
    '.jb-tabsheet .ts-g button{width:100% !important;display:flex !important;align-items:center;justify-content:center;text-align:center;min-height:46px;padding:6px 4px;border:1px solid #2bb6e6;' +
    'border-radius:8px;background:#071a30;color:#bfeeff;font-size:13px;line-height:1.2;}' +
    '.jb-tabsheet .ts-g button.on{border-color:#39e07a;color:#39e07a;font-weight:700;}' +
    '.jb-tabsheet .ts-back{display:block !important;width:100% !important;text-align:center !important;margin-top:10px;min-height:44px;border:1px solid #39e07a;border-radius:8px;color:#39e07a;font-size:14px;text-align:center;}' +
    (onBoard ? '#panes iframe{width:1px !important;min-width:100% !important;} html,body{max-width:100%;overflow-x:hidden;}' +
               '#bar{position:relative;z-index:20;} #jbm-strip{position:relative;z-index:1;transition:margin-top .08s linear;will-change:margin-top;}' : '');
  (document.head || document.documentElement).appendChild(css);

  // ---------------------------------------------------------------- 1 slide back to the middle
  var touching = false, tmr = null;
  function home() { if (!touching && (window.scrollX || window.pageXOffset) > 0) window.scrollTo({ left: 0, top: window.scrollY, behavior: 'smooth' }); }
  window.addEventListener('touchstart', function () { touching = true; clearTimeout(tmr); }, { passive: true });
  window.addEventListener('touchend', function () { touching = false; clearTimeout(tmr); tmr = setTimeout(home, 140); }, { passive: true });
  window.addEventListener('touchcancel', function () { touching = false; clearTimeout(tmr); tmr = setTimeout(home, 140); }, { passive: true });
  window.addEventListener('scroll', function () { if (!touching && (window.scrollX || window.pageXOffset) > 0) { clearTimeout(tmr); tmr = setTimeout(home, 180); } }, { passive: true });

  function ready(fn) { if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn); else fn(); }

  // ---------------------------------------------------------------- 2 + 3 the full board
  if (onBoard && top) ready(function () {
    var strip = null, h = 0, hooked = [];
    function active() { return document.querySelector('#panes iframe.active'); }
    function place(y) {
      strip = strip || document.getElementById('jbm-strip'); if (!strip) return;
      if (!h || y <= 0) h = strip.offsetHeight + 8;
      strip.style.marginTop = -Math.min(Math.max(y, 0), h) + 'px';
    }
    setInterval(function () {
      var f = active(); if (!f) return;
      var w; try { w = f.contentWindow; } catch (e) { return; }
      if (!w || hooked.indexOf(w) >= 0) { if (w) try { place(w.scrollY); } catch (e) {} return; }
      try { w.addEventListener('scroll', function () { if (active() && active().contentWindow === w) place(w.scrollY); }, { passive: true }); hooked.push(w); place(w.scrollY); } catch (e) {}
    }, 400);
    // a cleaner list of tabs
    document.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('#mbTabs'); if (!b) return;
      e.preventDefault(); e.stopImmediatePropagation();
      var bar = document.getElementById('bar'), curT = bar.querySelector('.tab.active');
      var m = document.createElement('div'); m.className = 'mb-menu jb-tabsheet';
      var box = document.createElement('div'); m.appendChild(box);
      box.innerHTML = '<div class="ts-h"><b>TABS</b><button type="button" class="ts-x">✕ Close</button></div><div class="ts-g"></div>';
      var g = box.querySelector('.ts-g');
      [].slice.call(bar.querySelectorAll('.tab')).forEach(function (t) {
        if (t.hidden || getComputedStyle(t).display === 'none') return;
        var x = document.createElement('button'); x.type = 'button'; x.textContent = t.textContent.trim();
        if (t === curT) x.className = 'on';
        x.onclick = function () { m.remove(); t.click(); try { t.scrollIntoView({ inline: 'center', block: 'nearest' }); } catch (er) {} };
        g.appendChild(x);
      });
      var back = document.createElement('button'); back.type = 'button'; back.className = 'ts-back';
      back.textContent = '‹ Back to ' + (curT ? curT.textContent.trim() : 'the board');
      box.appendChild(back);
      function close() { m.remove(); }
      back.onclick = close; box.querySelector('.ts-x').onclick = close;
      m.onclick = function (ev) { if (ev.target === m) close(); };
      document.body.appendChild(m);
    }, true);
  });

  // ---------------------------------------------------------------- 4 Phone page
  if (file === 'phone.html') ready(function () {
    function buttons() {
      var look = document.getElementById('jb-theme'), ch = document.getElementById('jbChanBtn');
      if (!look || !ch) return false;
      var wrap = document.querySelector('.jb-hbtns');
      if (!wrap) {
        wrap = document.createElement('div'); wrap.className = 'jb-hbtns';
        look.parentNode.insertBefore(wrap, look);
        var bd = document.createElement('a'); bd.id = 'jbBoardBtn'; bd.href = './?desk=1'; bd.textContent = '▦ Board';
        bd.title = 'The full board as on the computer (zoom with two fingers; Phone at the top comes back here)';
        wrap.appendChild(ch); wrap.appendChild(bd); wrap.appendChild(look);
      }
      return true;
    }
    function holdings() {
      var held = document.getElementById('heldc'), tk = document.getElementById('tkBox'), tc = tk && tk.closest('.card');
      if (!held || !tc) return false;
      if (tc.nextElementSibling !== held) tc.parentNode.insertBefore(held, tc.nextSibling);
      return true;
    }
    var n = 0, t = setInterval(function () { var a = buttons(), b = holdings(); if ((a && b) || ++n > 40) clearInterval(t); }, 250);
  });

  // ---------------------------------------------------------------- 5 Portfolio: Add a trade in even rows
  ready(function () {
    var d = document.getElementById('fDate'), f = d && d.closest('.form');
    if (f) f.classList.add('jb-tradeform');
  });
  // ---------------------------------------------------------------- 10 Home-screen app: bar on the real bottom edge
  // In the iPhone home-screen app, pages that don't scroll as a whole (the board's tabs) can get a
  // window that stops short of the screen bottom, leaving a grey strip under the bar. Measure the
  // strip, push the bar into it and stretch the board to match. Nothing changes in Safari or when
  // there is no strip.
  var standalone = navigator.standalone || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches);
  if (top && standalone) ready(function () {
    var last = '';
    function fit() {
      var nav = document.getElementById('jbNav');
      var portrait = window.innerHeight >= window.innerWidth;
      var full = portrait ? Math.max(screen.height, screen.width) : Math.min(screen.height, screen.width);
      var gap = Math.round(full - window.innerHeight);
      if (!(gap > 0 && gap < 160)) gap = 0;
      var key = gap + (nav ? 'n' : '');
      if (key === last) return;
      last = key;
      var EXTRA = 14;   // Alex 16:32Z: a little lower still (eats into the bar's own bottom padding, never off screen)
      var tr = gap ? 'translateY(' + (gap + EXTRA) + 'px)' : '';
      if (nav) nav.style.transform = tr;
      var sheet = document.getElementById('jbNavSheet');
      if (sheet) sheet.style.transform = tr;
      if (onBoard) {
        var h = gap ? (window.innerHeight + gap + EXTRA) + 'px' : '';
        document.documentElement.style.height = h;
        document.body.style.height = h;
      }
    }
    fit();
    window.addEventListener('resize', fit);
    window.addEventListener('orientationchange', function () { setTimeout(fit, 400); });
    document.addEventListener('visibilitychange', function () { setTimeout(fit, 200); });
    setInterval(fit, 1000);
  });

})();
