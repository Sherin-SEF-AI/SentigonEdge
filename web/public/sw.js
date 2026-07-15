// Sentigon push service worker. Receives Web Push messages and shows a
// notification. Clicking it focuses the console.
self.addEventListener("push", (event) => {
  let data = { title: "Sentigon", body: "Security incident" };
  try {
    if (event.data) data = event.data.json();
  } catch {
    /* keep default */
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Sentigon", {
      body: data.body || "",
      icon: "/icon.png",
      badge: "/icon.png",
      tag: "sentigon-incident",
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow("/"));
});
