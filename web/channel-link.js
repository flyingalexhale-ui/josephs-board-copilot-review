/* Alex's channel (jb-channel): ways to open the channel (channel.html).
 *  - a small "▶ Channel" button next to the look button, on the full board and the Phone page
 *  - a wide "Watch Alex's Channel" button above the Phone page's link boxes
 * Hooked in by one <script src="channel-link.js"> line on index.html and phone.html (add-channel.py). */
(function () {
  'use strict';
  var small = window.matchMedia && window.matchMedia('(max-width: 640px), (pointer: coarse) and (max-width: 1024px)').matches;
  var css = document.createElement('style');
  css.textContent =
    '#jbChanBtn{display:inline-block;background:transparent;border:1px solid #5cc8db;color:#5cc8db;font:inherit;font-size:12px;' +
    'font-weight:700;padding:6px 10px;border-radius:6px;text-decoration:none;white-space:nowrap;align-self:center;margin:0 6px 6px 4px;flex-shrink:0;}' +
    '#jbChanBtn:hover{background:#5cc8db;color:#04101f;}' +
    'header #jbChanBtn{margin:0 6px 4px 0;vertical-align:middle;}' +
    /* full board on a wide screen: one more narrow column at the right end of the two tab rows */
    '@media (min-width:1100px){#bar{grid-template-columns:max-content repeat(9,minmax(0,1fr)) max-content max-content max-content !important;}' +
    '#bar #jbChanBtn{grid-column:13;grid-row:1 / span 2;margin:0 8px 6px 2px;}}';
  document.head.appendChild(css);
  function small_button() {
    if (document.getElementById('jbChanBtn')) return true;
    var look = document.getElementById('jb-theme');
    if (!look) return false;
    var a = document.createElement('a');
    a.id = 'jbChanBtn'; a.href = 'channel.html'; a.textContent = '▶ Channel';
    a.title = 'Alex\'s channel: your board as a news channel, with the anchor\'s voice';
    if (!small && document.getElementById('bar')) a.target = '_blank';     // the board stays open on a computer
    if (look.parentNode.tagName === 'HEADER' || look.closest('header')) look.parentNode.insertBefore(a, look);
    else look.parentNode.insertBefore(a, look.nextSibling);
    return true;
  }
  function wide_button() {
    if (document.getElementById('jbChanLink')) return true;
    var grid = document.querySelector('.jb-qlinks'), links = document.querySelector('.links');
    var spot = grid || links;
    if (!spot) return false;
    var a = document.createElement('a');
    a.id = 'jbChanLink'; a.href = 'channel.html';
    a.textContent = '▶  Watch Alex\'s Channel (with the anchor\'s voice)';
    a.style.cssText = 'display:flex;align-items:center;justify-content:center;min-height:48px;margin:0 0 8px;border-radius:8px;' +
      'background:#5cc8db;color:#04101f;font-weight:800;font-size:15px;text-decoration:none;text-align:center;padding:10px';
    spot.parentNode.insertBefore(a, spot);
    return true;
  }
  function add() {
    small_button();
    if (/phone\.html$/i.test(location.pathname)) wide_button();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
