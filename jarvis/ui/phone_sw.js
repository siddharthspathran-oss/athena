self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));
// Share to Athena: the share arrives as a POST with no token, so it is parked
// in a cache and the page - which has the token - picks it up and sends it on.
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'POST' || u.pathname !== '/share-in') return;
  e.respondWith((async () => {
    const f = await e.request.formData();
    const c = await caches.open('athena-share');
    const meta = {title: f.get('title') || '', text: f.get('text') || '', url: f.get('url') || '', files: []};
    let i = 0;
    for (const file of f.getAll('files')) {
      if (!file || !file.name) continue;
      const key = '/share-file/' + (i++);
      await c.put(key, new Response(file, {headers: {'Content-Type': file.type || 'application/octet-stream',
                                                     'X-Name': encodeURIComponent(file.name)}}));
      meta.files.push(key);
    }
    await c.put('/share-meta', new Response(JSON.stringify(meta), {headers: {'Content-Type': 'application/json'}}));
    return Response.redirect('/?shared=1', 303);
  })());
});
// Athena's own notifications (jarvis/webpush.py); a tap opens the app at d.url.
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data.json(); } catch (_) { d = {title: 'Athena', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(d.title || 'Athena', {
    body: d.body || '', icon: '/icons/icon-192.png', badge: '/icons/maskable-192.png',
    tag: d.tag || undefined, renotify: !!d.tag, data: {url: d.url || '/'},
    requireInteraction: !!d.urgent, vibrate: d.urgent ? [250, 120, 250, 120, 500] : [120]}));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || '/', self.location.origin).href;
  e.waitUntil(clients.matchAll({type: 'window', includeUncontrolled: true}).then(cs => {
    for (const c of cs) {
      if (c.url.startsWith(self.location.origin) && 'focus' in c) {
        return c.focus().then(w => (w && w.navigate ? w.navigate(url) : w)).catch(() => clients.openWindow(url));
      }
    }
    return clients.openWindow(url);
  }));
});
