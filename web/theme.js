/* Looks for the board (jb-extras, more looks added by jb-looks).
   Polished is the normal look. Pure black and Clean light are polished too. Classic is the board
   exactly as it looked before jb-looks. Old light flips Classic's colours. Night turns everything
   a dim red, easy on the eyes in a dark room. Auto = night from 9 pm to 6 am (this phone's or
   computer's own clock), Polished otherwise. Since jb-looktrim, Classic and Old light are no longer
   offered (they copied Polished and Clean light): a phone or computer that had one now gets Polished
   or Clean light. Night is now drawn by looks.js on every page (it used to be a filter on the whole
   page, which hid the phone's bottom bar and missed pages opened on their own). The choice is remembered per phone/computer
   (jb_theme). Only the top page runs this; the tabs inside it follow along (looks.js).
   The button with id="jb-theme" opens a short list of the looks. */
(function(){
  'use strict';
  if(window.top !== window) return;               // inside a tab: the outer page does it
  var ORDER = ['dark', 'black', 'clean', 'night', 'auto'];
  var OLD = {classic: 'dark', light: 'clean'};      // looks no longer offered -> their closest look
  var LABEL = {dark: '◐ Polished', black: '● Pure black', clean: '○ Clean light', classic: '◐ Classic',
               light: '○ Old light', night: '● Night', auto: '◑ Auto'};
  var TIP = {dark: 'Polished: the normal look.', black: 'Pure black, polished.', clean: 'Clean light: grey and white, polished.',
             classic: 'Classic: the board as it looked before.', light: 'Old light: Classic with the colours flipped.',
             night: 'Night: dim red, easy on the eyes in the dark.',
             auto: 'Auto: night mode from 9 pm to 6 am, Polished the rest of the day.'};
  var css = document.createElement('style');
  css.textContent =
    '#jb-theme{font:inherit;font-size:11px;color:#9fb3c8;background:transparent;border:1px solid #164060;' +
    'border-radius:6px;padding:5px 8px;cursor:pointer;white-space:nowrap;}' +
    '#jb-theme-menu{position:fixed;z-index:9999;background:#071a30;border:1px solid #164060;border-radius:8px;' +
    'padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.45);min-width:150px;}' +
    '#jb-theme-menu button{display:block;width:100%;text-align:left;font:inherit;font-size:13px;color:#f2f2f2;' +
    'background:transparent;border:0;border-radius:5px;padding:9px 12px;cursor:pointer;white-space:nowrap;}' +
    '#jb-theme-menu button:hover{background:#0d2740;}' +
    '#jb-theme-menu button.on{background:#0d2740;color:#00ff41;}';
  (document.head || document.documentElement).appendChild(css);
  function get(){
    try{
      var t = localStorage.getItem('jb_theme') || 'dark';
      if(OLD[t]){ t = OLD[t]; localStorage.setItem('jb_theme', t); }
      return ORDER.indexOf(t) >= 0 ? t : 'dark';
    }catch(e){ return 'dark'; }
  }
  function effective(t){
    if(t !== 'auto') return t;
    var h = new Date().getHours();
    return (h >= 21 || h < 6) ? 'night' : 'dark';
  }
  function apply(){
    var t = get(), e = effective(t), r = document.documentElement;
    r.classList.remove('jb-light');
    r.classList.toggle('jb-night', e === 'night');
    var b = document.getElementById('jb-theme');
    if(b){ b.textContent = LABEL[t] || LABEL.dark; b.title = TIP[t] || ''; }
    var m = document.querySelector('meta[name="theme-color"]');
    if(m) m.setAttribute('content', e === 'night' ? '#0a0101' : e === 'clean' ? '#dfe4eb' : e === 'black' ? '#000000' : '#04101f');
    if(window.jbLooks) window.jbLooks.refresh();
  }
  function set(t){ try{ localStorage.setItem('jb_theme', t); }catch(e){} apply(); }
  function cycle(){ var i = ORDER.indexOf(get()); set(ORDER[(i + 1) % ORDER.length]); }
  function closeMenu(){ var m = document.getElementById('jb-theme-menu'); if(m) m.parentNode.removeChild(m); }
  function openMenu(b){
    if(document.getElementById('jb-theme-menu')){ closeMenu(); return; }
    var m = document.createElement('div'); m.id = 'jb-theme-menu';
    ORDER.forEach(function(t){
      var o = document.createElement('button'); o.type = 'button'; o.textContent = LABEL[t]; o.title = TIP[t];
      if(t === get()) o.className = 'on';
      o.addEventListener('click', function(ev){ ev.stopPropagation(); set(t); closeMenu(); });
      m.appendChild(o);
    });
    document.body.appendChild(m);
    var r = b.getBoundingClientRect(), w = m.offsetWidth;
    m.style.top = Math.round(r.bottom + 6) + 'px';
    m.style.left = Math.round(Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8))) + 'px';
  }
  document.addEventListener('click', function(ev){
    var m = document.getElementById('jb-theme-menu');
    if(m && !m.contains(ev.target) && ev.target.id !== 'jb-theme') closeMenu();
  });
  window.jbTheme = {get: get, set: set, cycle: cycle};
  apply();
  function hook(){
    var b = document.getElementById('jb-theme');
    if(b && !b._jb){ b._jb = true; b.addEventListener('click', function(ev){ ev.stopPropagation(); openMenu(b); }); }
    apply();
  }
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', hook); else hook();
  setInterval(function(){ if(get() === 'auto') apply(); }, 60000);
  window.addEventListener('storage', function(e){ if(e.key === 'jb_theme') apply(); });
})();
