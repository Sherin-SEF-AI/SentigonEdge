// End-to-end Web Push proof: a real browser registers the service worker,
// subscribes via PushManager (real push service endpoint), posts the subscription
// to notify, then we trigger a real push and capture the SW notification.
import puppeteer from "puppeteer-core";

const APP = "http://localhost:3001";
const NOTIFY = "http://localhost:8070";

const browser = await puppeteer.launch({
  executablePath: "/usr/bin/google-chrome-stable",
  headless: "new",
  args: ["--no-sandbox", "--disable-setuid-sandbox"],
});
try {
  const context = browser.defaultBrowserContext();
  await context.overridePermissions(APP, ["notifications"]);
  const page = await browser.newPage();
  const notifications = [];
  await page.exposeFunction("__pushSeen", (d) => notifications.push(d));
  await page.goto(APP, { waitUntil: "networkidle2", timeout: 30000 });

  const sub = await page.evaluate(async (NOTIFY) => {
    function toU8(b64) {
      const pad = "=".repeat((4 - (b64.length % 4)) % 4);
      const s = (b64 + pad).replace(/-/g, "+").replace(/_/g, "/");
      const raw = atob(s);
      const a = new Uint8Array(raw.length);
      for (let i = 0; i < raw.length; i++) a[i] = raw.charCodeAt(i);
      return a;
    }
    const vapid = await fetch(`${NOTIFY}/push/vapid`).then((r) => r.json());
    const reg = await navigator.serviceWorker.register("/sw.js");
    await navigator.serviceWorker.ready;
    const s = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: toU8(vapid.publicKey),
    });
    const j = s.toJSON();
    // relay incoming push payloads from the SW to node
    navigator.serviceWorker.addEventListener("message", (e) => window.__pushSeen(e.data));
    const res = await fetch(`${NOTIFY}/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(j),
    }).then((r) => r.json());
    return { endpoint: j.endpoint, stored: res.subscriptions };
  }, NOTIFY);

  console.log(`SUBSCRIBED: endpoint host = ${new URL(sub.endpoint).host}`);
  console.log(`stored subscriptions on server = ${sub.stored}`);

  // trigger a real push from the server
  const send = await fetch(`${NOTIFY}/push/test`, { method: "POST" }).then((r) => r.json());
  console.log(`SERVER SEND: ok=${send.ok} detail="${send.detail}"`);

  await new Promise((r) => setTimeout(r, 4000));
  const shown = await page.evaluate(async () => {
    const reg = await navigator.serviceWorker.getRegistration();
    const ns = await reg.getNotifications();
    return ns.map((n) => ({ title: n.title, body: n.body }));
  });
  console.log(`NOTIFICATIONS SHOWN BY SW: ${JSON.stringify(shown)}`);
} finally {
  await browser.close();
}
