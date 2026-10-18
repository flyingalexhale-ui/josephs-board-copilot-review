/* Phone page offline copy (jb-safety, idea 60). Network first, always: when the Mac answers, nothing
 * changes. Only when it can't be reached does the phone show the last copy it saved, and the page shows a
 * "Mac not reachable" banner (it sees the X-JB-Saved time). Only the phone page and what it needs are kept.
 * Phones allow this only on the https link (the ts.net address), not on the plain 100.x address. */
var C = 'jb-phone-v1';
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
function keep(u) {
  return u.pathname === '/phone.html' || u.pathname === '/api/phone' || u.pathname === '/api/plus/safety' ||
    u.pathname === '/api/plus/meters' || /^\/[\w.-]+\.(js|css|png|json)$/.test(u.pathname);
}
self.addEventListener('fetch', function (e) {
  var r = e.request;
  if (r.method !== 'GET') return;
  var u = new URL(r.url);
  if (u.origin !== self.location.origin || !keep(u) || u.pathname === '/sw.js') return;
  var key = u.origin + u.pathname;
  e.respondWith(fetch(r).then(function (resp) {
    if (resp.ok && !resp.redirected && resp.type === 'basic') {
      var cp = resp.clone();
      cp.blob().then(function (body) {
        var h = new Headers(cp.headers); h.set('X-JB-Saved', String(Date.now()));
        return caches.open(C).then(function (c) { return c.put(key, new Response(body, { status: 200, headers: h })); });
      }).catch(function () {});
    }
    return resp;
  }).catch(function () {
    return caches.open(C).then(function (c) { return c.match(key); }).then(function (m) {
      if (m) return m;
      throw new Error('offline');
    });
  }));
});
