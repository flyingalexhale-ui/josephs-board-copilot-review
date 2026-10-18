/* Builds the ? Legend panel (web/legend.js) for every tab with a stand-in page, the way a click on ? Legend does,
 * and fails if one throws or shows "undefined" (for example a row that lost its comma: valid JavaScript, broken legend).
 * usage (from the josephs-board folder):   node tests/legend-test.js        (exit code 1 if a check fails) */
'use strict';
var fs = require('fs'), path = require('path'), vm = require('vm');
var src = fs.readFileSync(path.join(__dirname, '..', 'web', 'legend.js'), 'utf8');
var tabsBlock = src.slice(src.indexOf('var TABS = {'), src.indexOf('};', src.indexOf('var TABS = {')));
var keys = (tabsBlock.match(/'([a-z0-9-]+)': \{ title:/g) || []).map(function (m) { return m.split("'")[1]; });
var els = {}, listeners = {}, active = 'ibkr', fetched = [];
function el(id) {
  return els[id] = els[id] || { id: id, style: {}, classList: { add: function () {}, remove: function () {}, contains: function () { return false; } },
    appendChild: function () {}, addEventListener: function (ev, fn) { (listeners[id + ':' + ev] = listeners[id + ':' + ev] || []).push(fn); },
    setAttribute: function () {}, innerHTML: '', textContent: '', scrollTop: 0 };
}
var document = {
  readyState: 'complete', head: el('head'), body: el('body'),
  getElementById: function (id) { return id === 'lgBody' || id === 'lgTitle' || id === 'lgClose' || id === 'lgPanel' || id === 'bar' || els[id] ? el(id) : null; },
  createElement: function (tag) { var e = el('new-' + Object.keys(els).length); e.tag = tag; return e; },
  querySelector: function (q) { return q.indexOf('.tab.active') >= 0 ? { getAttribute: function () { return active; } } : null; },
  addEventListener: function () {}
};
var sandbox = { window: {}, document: document, console: console, setTimeout: setTimeout,
  fetch: function (u) { fetched.push(u); return new Promise(function () {}); } };
vm.createContext(sandbox);
try {
  vm.runInContext(src, sandbox);
} catch (e) {
  console.log('FAIL legend.js runs when the page loads  -- ' + e.message);
  process.exit(1);
}
var handlers = Object.keys(listeners).filter(function (k) { return /:click$/.test(k); }).map(function (k) { return listeners[k][0]; });
var bad = 0, n = 0;
keys.forEach(function (k) {
  active = k; n++;
  el('lgBody').innerHTML = '';
  var opened = false, err = null;
  handlers.forEach(function (h) {
    if (opened) return;
    try { h({ target: null }); } catch (e) { err = e; }
    if (el('lgBody').innerHTML) opened = true;
  });
  var html = el('lgBody').innerHTML;
  var ok = !err && opened && html.indexOf('undefined') < 0 && html.length > 200;
  if (!ok) bad++;
  console.log((ok ? 'PASS ' : 'FAIL ') + 'legend for the ' + k + ' tab builds' + (ok ? '' : '  -- ' + (err ? err.message : (opened ? 'shows "undefined"' : 'did not open'))));
});
console.log('\nlegend tests: ' + (n - bad) + ' of ' + n + ' passed');
process.exit(bad || !n ? 1 : 0);
