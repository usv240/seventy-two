// "Alert me on this device": Web Push for a saved trip or a clock. No account, no email, no app.
import { api } from "/assets/site.js";

const supported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const ios = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
const installed = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

function keyBytes(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

// kind: "watches" or "clocks". Returns a short sentence for the page to show.
export async function enableAlerts(kind, id) {
  if (!supported()) {
    if (ios() && !installed()) return "On iPhone and iPad, first add this site to your Home Screen (Share, then Add to Home Screen), open it from there, and press this again.";
    return "This browser cannot show notifications from websites. Use the calendar feed instead.";
  }
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "Notifications are blocked for this site in your browser settings. The calendar feed still works.";
  const reg = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const { key } = await api("/api/push/key");
  let sub = await reg.pushManager.getSubscription();
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
  const res = await api(`/api/${kind}/${encodeURIComponent(id)}/push`, { body: { subscription: sub.toJSON() } });
  return res.ok ? "Alerts are on for this device. A first notification was just sent, so you know it works."
    : "Your browser's push service did not accept the first notification. Try again, or use the calendar feed.";
}
