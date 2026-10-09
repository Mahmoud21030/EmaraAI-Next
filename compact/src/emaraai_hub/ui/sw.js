/* EmaraAI service worker: shows the hub's push notifications while the Control Center is closed, and opens the right page on a click. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('push', e => {
  let n = {};
  try { n = e.data ? e.data.json() : {}; } catch (err) { n = {title: 'EmaraAI', body: e.data ? e.data.text() : ''}; }
  e.waitUntil(self.registration.showNotification(n.title || 'EmaraAI', {
    body: n.body || '', tag: n.tag || 'emara', renotify: true, data: {link: n.link || '#/'},
    requireInteraction: n.cat === 'decision' || n.cat === 'approval',      // what waits for you stays until you see it
    icon: '/ui/icon.svg', badge: '/ui/icon.svg'}));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = '/company' + ((e.notification.data && e.notification.data.link) || '#/');
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(list => {
    const open = list.find(c => new URL(c.url).pathname === '/company');
    if (open) return open.focus().then(c => (c || open).navigate ? (c || open).navigate(url) : null).catch(() => self.clients.openWindow(url));
    return self.clients.openWindow(url);
  }));
});
