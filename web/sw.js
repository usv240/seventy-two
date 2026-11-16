// Seventy-Two's service worker: shows the notifications the site sends, and opens the right page when one is pressed.
// It caches nothing and runs nothing else.
self.addEventListener("push", (event) => {
  let msg = { title: "Seventy-Two", body: "", url: "/" };
  try { msg = { ...msg, ...event.data.json() }; } catch {}
  event.waitUntil((async () => {
    await self.registration.showNotification(msg.title, { body: msg.body, data: { url: msg.url }, icon: "/assets/icon-192.png", badge: "/assets/icon-192.png", tag: msg.url });
    // tell any open page, so it can refresh what it shows
    for (const c of await self.clients.matchAll({ type: "window" })) c.postMessage({ type: "push", ...msg });
  })());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil((async () => {
    for (const c of await self.clients.matchAll({ type: "window" })) {
      if (c.url === url && "focus" in c) return c.focus();
    }
    return self.clients.openWindow(url);
  })());
});
