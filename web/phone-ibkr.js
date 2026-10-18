/* IBKR groups on the phone home page (jb-phonegroups).
   A live copy of the IBKR board's US EQUITIES and FUTURES groups, placed between the
   "Mood since the 4 pm close" card and "Futures and fear". It is the IBKR board itself
   (ibkr.html) in a frame with everything except those two groups hidden, so tiles, the
   group menu, WHY? chips and alerts behave exactly as on the board. */
(function () {
  var mood = document.getElementById('mood');
  var card = mood && mood.closest('.card');
  if (!card || document.getElementById('ibGroups')) return;

  var box = document.createElement('div');
  box.id = 'ibGroups';
  box.style.cssText = 'margin:10px 0;';
  var fr = document.createElement('iframe');
  fr.src = 'ibkr.html?groups=1';
  fr.title = 'IBKR board: US equities and futures';
  fr.setAttribute('scrolling', 'no');
  fr.style.cssText = 'display:block;width:100%;height:420px;border:0;background:transparent;overflow:hidden;';
  box.appendChild(fr);
  card.parentNode.insertBefore(box, card.nextSibling);

  var CSS =
    'html,body{background:transparent!important;min-height:0!important;padding:0!important;margin:0!important;overflow:hidden!important;}' +
    'body>*:not(.wrap){display:none!important;}' +
    '.wrap{padding:0!important;margin:0!important;max-width:none!important;}' +
    '.wrap>*:not(section.group){display:none!important;}' +
    'section.group{margin-top:0!important;margin-left:0!important;margin-right:0!important;}' +
    'section.group+section.group{margin-top:10px!important;}';

  function fit() {
    try {
      var d = fr.contentDocument, w = d && d.querySelector('.wrap');
      if (!w) return;
      var hgt = Math.ceil(w.getBoundingClientRect().height);
      if (hgt > 0 && Math.abs(hgt - fr.offsetHeight) > 1) fr.style.height = hgt + 'px';
    } catch (e) {}
  }

  fr.addEventListener('load', function () {
    // Mac not reachable (the Phone page's saved copy): no board in the frame, so hide it
    // instead of showing an error box; it comes back on the next load
    var ok = false;
    try { ok = !!fr.contentDocument.querySelector('.wrap'); } catch (e) {}
    box.style.display = ok ? '' : 'none';
    if (!ok) return;
    try {
      var d = fr.contentDocument;
      var st = d.createElement('style');
      st.id = 'jbGroupsOnly';
      st.textContent = CSS;
      d.head.appendChild(st);
      var w = d.querySelector('.wrap');
      if (w && window.ResizeObserver) new ResizeObserver(fit).observe(w);
    } catch (e) {}
    fit();
    setTimeout(fit, 500); setTimeout(fit, 2000);
  });
  setInterval(fit, 3000);

  // stop drawing while the phone page is in the background (it keeps recording prices)
  document.addEventListener('visibilitychange', function () {
    try { fr.contentWindow.postMessage({ type: 'jb-visibility', visible: !document.hidden }, location.origin); } catch (e) {}
  });
})();
