/* Phone fixes (jb-kill). Only adds; nothing on the page is removed or moved for big screens.
   Everything below applies on screens up to 640 px wide (phones):
   1 boxes you type in use 16 px text, so the iPhone no longer zooms in when you tap them
   2 a "☰ Tabs" button at the left of the top bar opens a list of every tab (the bar still scrolls)
   3 small buttons, menus and chips get at least 36 px of height, checkboxes 22 px
   4 the board's small top-corner text (session, connection, footer) is a little bigger
   5 wide tables keep their first column (the symbol) in place while you swipe, with a hint
   6 a table wider than the screen scrolls inside its own box instead of sliding the whole page */
(function(){
  'use strict';
  var PHONE = '(max-width:640px)';
  var css = document.createElement('style');
  css.textContent = '@media ' + PHONE + '{' +
    'input:not([type=checkbox]):not([type=radio]):not([type=range]),select,textarea{font-size:16px !important;}' +
    'input[type=checkbox],input[type=radio]{width:22px !important;height:22px !important;flex-shrink:0;}' +
    '.sess-badge,body.layout-rows .sess-badge{font-size:11px !important;}' +
    'header .status,body.layout-rows header .status{font-size:11px !important;}' +
    '.add-row label{font-size:12px !important;}' +
    'footer.note,body.layout-rows footer.note{font-size:12px !important;}' +
    '.mb-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;}' +
    '.mb-sticky tr>*:first-child{position:sticky;left:0;z-index:1;background:var(--mb-bg,#04101f);}' +
    '.mb-hint{font-size:11px;opacity:.6;text-align:right;margin:2px 4px;}' +
    '#mbTabs{display:inline-flex !important;}' +
    '}' +
    '#mbTabs{display:none;align-self:center;flex-shrink:0;background:transparent;border:1px solid #164060;color:inherit;font:inherit;' +
      'font-size:13px;font-weight:600;border-radius:8px;padding:0 10px;margin:0 6px 6px 0;min-height:36px;align-items:center;cursor:pointer;}' +
    '.mb-menu{position:fixed;inset:0;z-index:9998;background:rgba(0,0,0,.6);}' +
    '.mb-menu div{position:absolute;left:8px;right:8px;top:max(8px,env(safe-area-inset-top));max-height:85vh;overflow-y:auto;' +
      'background:#071a30;border:1px solid #164060;border-radius:12px;padding:6px;}' +
    '.mb-menu button{display:block;width:100%;text-align:left;background:none;border:0;border-bottom:1px solid #0d2740;color:#f2f2f2;' +
      'font:inherit;font-size:16px;padding:12px 10px;min-height:44px;cursor:pointer;}' +
    '.mb-menu button.on{color:#00ff41;font-weight:700;}';
  document.head.appendChild(css);
  var mq = window.matchMedia ? window.matchMedia(PHONE) : {matches: false};

  // 2: tab list (only on the main page, which has the tab bar)
  var bar = document.getElementById('bar');
  if(bar && bar.querySelector('.tab') && !document.getElementById('mbTabs')){
    var b = document.createElement('button'); b.id = 'mbTabs'; b.type = 'button'; b.textContent = '☰ Tabs';
    b.title = 'List of all tabs';
    bar.insertBefore(b, bar.firstChild);
    b.onclick = function(){
      var m = document.createElement('div'); m.className = 'mb-menu';
      var box = document.createElement('div'); m.appendChild(box);
      [].slice.call(bar.querySelectorAll('.tab')).forEach(function(t){
        if(t.hidden) return;
        var x = document.createElement('button'); x.textContent = t.textContent.trim();
        if(t.classList.contains('active')) x.className = 'on';
        x.onclick = function(){ m.remove(); t.click(); t.scrollIntoView({inline: 'center', block: 'nearest'}); };
        box.appendChild(x);
      });
      m.onclick = function(e){ if(e.target === m) m.remove(); };
      document.body.appendChild(m);
    };
  }

  function bgOf(el){
    for(; el && el !== document.documentElement; el = el.parentElement){
      var c = getComputedStyle(el).backgroundColor;
      if(c && c !== 'transparent' && !/rgba\(.*,\s*0\)$/.test(c)) return c;
    }
    return getComputedStyle(document.body).backgroundColor;
  }
  function scrolls(el){ var o = getComputedStyle(el).overflowX; return o === 'auto' || o === 'scroll'; }

  function pass(){
    if(!mq.matches) return;
    // 3: bigger tap targets for short buttons, menus and chip-like links
    // measure everything first: growing one button can stretch its neighbours in a row
    var grow = [];
    [].forEach.call(document.querySelectorAll('button,select,a'), function(el){
      if(el.classList.contains('mb-tap') || el.closest('.mb-menu,.kill-ov')) return;
      var h = el.offsetHeight; if(!h || h >= 34) return;
      if(el.tagName === 'A' && getComputedStyle(el).display === 'inline' && !el.closest('h2,h3,nav,.links')) return;
      if(el.tagName === 'BUTTON' && el.textContent.trim().length < 2) return;     // × and other tiny icons stay as they are
      var c = getComputedStyle(el);
      grow.push([el, h, c, parseFloat(c.paddingTop), parseFloat(c.paddingBottom)]);
    });
    grow.forEach(function(g){
      var el = g[0], h = g[1], cs = g[2];
      el.classList.add('mb-tap');
      if(el.tagName === 'SELECT'){ el.style.minHeight = '36px'; return; }
      var add = (36 - h) / 2, disp = cs.display;              // grow evenly above and below the text
      if(disp === 'inline') el.style.display = 'inline-block';
      el.style.paddingTop = (g[3] + add) + 'px';
      el.style.paddingBottom = (g[4] + add) + 'px';
    });
    // 5 + 6: wide tables
    [].forEach.call(document.querySelectorAll('table'), function(t){
      if(!t.offsetWidth || t.closest('.kill-ov,.lg-wrap')) return;
      var p = t.parentElement;
      if(t.offsetWidth <= p.clientWidth + 2 && t.offsetWidth <= document.documentElement.clientWidth) return;
      if(!scrolls(p)){
        var w = document.createElement('div'); w.className = 'mb-wrap';
        p.insertBefore(w, t); w.appendChild(t); p = w;
      }
      if(!t.classList.contains('mb-sticky')){
        t.classList.add('mb-sticky');
        t.style.setProperty('--mb-bg', bgOf(p));
      }
      if(!(p.previousElementSibling && p.previousElementSibling.classList.contains('mb-hint')) &&
         !(t.previousElementSibling && t.previousElementSibling.classList.contains('mb-hint'))){
        var h = document.createElement('div'); h.className = 'mb-hint'; h.textContent = 'swipe sideways for more →';
        if(p.classList.contains('mb-wrap') || p.children.length > 1) p.parentNode.insertBefore(h, p); else p.insertBefore(h, t);
      }
    });
  }
  var timer = null;
  function soon(){ if(!timer) timer = setTimeout(function(){ timer = null; pass(); }, 400); }
  if(window.MutationObserver) new MutationObserver(soon).observe(document.documentElement, {childList: true, subtree: true});
  window.addEventListener('resize', soon);
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', pass); else pass();
})();

/* jb-safe-top (jb-calendar): on the iPhone home-screen app the page is drawn under the clock and
   notch; give every top-level page at least that much space at the top (pages that already
   leave it, like the Phone page and the board, are left alone). */
(function(){
  if(window.top !== window) return;
  var orig = null;
  function fix(){
    var p = document.createElement('div');
    p.style.cssText = 'position:fixed;top:0;left:0;width:0;height:env(safe-area-inset-top,0px);visibility:hidden';
    document.body.appendChild(p);
    var inset = p.offsetHeight; p.parentNode.removeChild(p);
    if(orig === null) orig = parseFloat(getComputedStyle(document.body).paddingTop) || 0;
    document.body.style.paddingTop = orig < inset ? (orig + inset) + 'px' : '';
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fix); else fix();
  window.addEventListener('orientationchange', function(){ setTimeout(fix, 300); });
})();
