// Web Push subscription against the notify service (self-generated VAPID).
const NOTIFY_URL = process.env.NEXT_PUBLIC_NOTIFY_URL ?? "http://localhost:8070";

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const b64 = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const arr = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
  return arr;
}

export function pushSupported(): boolean {
  return typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window;
}

export async function subscribeToPush(): Promise<{ ok: boolean; detail: string }> {
  if (!pushSupported()) return { ok: false, detail: "push not supported in this browser" };
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return { ok: false, detail: "permission denied" };

  const vapid = await fetch(`${NOTIFY_URL}/push/vapid`).then((r) => r.json());
  if (!vapid.configured || !vapid.publicKey) return { ok: false, detail: "server VAPID not configured" };

  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(vapid.publicKey) as BufferSource,
  });
  const res = await fetch(`${NOTIFY_URL}/push/subscribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub.toJSON()),
  }).then((r) => r.json());
  return { ok: true, detail: `subscribed (${res.subscriptions} total)` };
}

export async function testPush(): Promise<{ ok: boolean; detail: string }> {
  try {
    const res = await fetch(`${NOTIFY_URL}/push/test`, { method: "POST" }).then((r) => r.json());
    return { ok: !!res.ok, detail: res.detail ?? (res.ok ? "sent" : "no subscriptions") };
  } catch (e) {
    return { ok: false, detail: e instanceof Error ? e.message : "request failed" };
  }
}
