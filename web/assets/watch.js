import { api, esc, fmt, fmtDay, hoursText, PHASE, DOMAIN, hydrateInfo, info, flipText } from "/assets/site.js";
import { enableAlerts } from "/assets/alerts.js";

const $ = (id) => document.getElementById(id);
const id = new URLSearchParams(location.search).get("id");
let data = null, seen = new Set(), timer = null;

function ago(iso) {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  return m < 1 ? "less than a minute ago" : m === 1 ? "1 minute ago" : m < 120 ? `${m} minutes ago` : `${Math.round(m / 60)} hours ago`;
}

function stopWhen(s) {
  const day = new Date(s.start_utc).toLocaleDateString("en-US", { timeZone: s.timezone, weekday: "short", month: "short", day: "numeric" });
  if (!s.time_given) return `${day}, all day`;
  const t = (iso) => new Date(iso).toLocaleTimeString("en-US", { timeZone: s.timezone, hour: "numeric", minute: "2-digit" });
  return `${day}, ${t(s.start_utc)} to ${t(s.end_utc)}`;
}

function matchCard(m, i) {
  const w = m.window, d = m.deadlines, st = m.status, ph = PHASE[st.phase] || PHASE.watch;
  const now = new Date();
  const box = (label, key, iso) => {
    const h = (new Date(iso) - now) / 3600000;
    return `<div class="deadline ${h > 0 ? "open" : "closed"} ${h > 0 && h < 30 ? "urgent" : ""}"><div class="label">${label} ${info(key)}</div><div class="value num">${esc(h > 0 ? hoursText(h) : "Closed")}</div><div class="sub">${h > 0 ? "Until " : "Closed "}${esc(fmt(iso, w.timezone))}</div></div>`;
  };
  const how = m.already_on_board
    ? "This notice was already on the board when you saved your trip."
    : `Found by the matcher ${esc(ago(m.found_at))}${m.seconds_after_stored != null ? `, ${esc(m.seconds_after_stored)} seconds after the notice was stored` : ""}.`;
  return `<article class="result" data-ui>
    <div class="board result-row" data-ui><table><tbody><tr class="yours"><td class="place"><span class="flap">${esc(w.place)}</span></td><td class="state-col">${esc(w.state || "")}</td><td class="${ph.cls}">${esc(ph.word)}</td></tr></tbody></table></div>
    <p class="when">${esc(w.local_label || fmt(w.start_utc, w.timezone))}</p>
    <p class="small">You said: ${esc(m.stop)}. ${how}</p>
    <p class="lead">${esc(st.next_step)}</p>
    <div class="deadline-grid">
      ${DOMAIN.actions.map((a) => box(a.box, a.info, d[`${a.kind}_deadline`])).join("")}
      <div class="deadline open"><div class="label">Watch for symptoms ${info("watch")}</div><div class="value">To ${esc(new Date(d.watch_end).toLocaleDateString("en-US", { timeZone: w.timezone, month: "short", day: "numeric" }))}</div><div class="sub">Starts ${esc(fmtDay(d.watch_start, w.timezone))}</div></div>
    </div>
    <blockquote class="quote">${esc(w.quote)} ${info("quote")}</blockquote>
    <p class="small">From <a href="${esc(w.url)}" target="_blank" rel="noopener">${esc(w.issuer || "the health department")}</a>.</p>
    <div class="actions">
      <button class="btn start-clock" type="button" data-i="${i}">Start my clock</button>
      ${w.phone ? `<a class="btn secondary" href="tel:${esc(w.phone.replace(/[^0-9+]/g, ""))}">Call ${esc(w.phone)}</a>` : ""}
    </div>
  </article>`;
}

function render() {
  const t = data;
  $("drill-banner").hidden = !t.drill;
  const until = new Date(t.expires).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
  $("watch-lead").textContent = t.matches.length
    ? `${t.matches.length === 1 ? "A notice matches" : `${t.matches.length} notices match`} a place on your trip. Watching for more until ${until}.`
    : `No notice matches your trip yet. Watching every new notice until ${until}.`;
  const matchedStops = new Set(t.matches.map((m) => m.stop));
  $("stops").innerHTML = t.stops.map((s, i) => `<tr${matchedStops.has(s.place) ? ' class="yours"' : ""}><td class="place"><span class="flap" data-i="${i}">${esc(s.place)}</span></td><td>${esc(stopWhen(s))}</td><td class="${matchedStops.has(s.place) ? "status-watch" : "status-open"}"><span class="stop-status" data-i="${i}">${matchedStops.has(s.place) ? "MATCH" : "WATCHING"}</span></td></tr>`).join("");
  // a match that arrives while the page is open flips its row
  t.matches.forEach((m) => {
    if (seen.size && !seen.has(m.window.id)) {
      const i = t.stops.findIndex((s) => s.place === m.stop);
      const el = document.querySelector(`.stop-status[data-i="${i}"]`);
      if (el) flipText(el, "MATCH");
    }
  });
  t.matches.forEach((m) => seen.add(m.window.id));
  if (!seen.size) seen.add("_");
  const mt = t.matcher || {};
  $("matcher-line").innerHTML = mt.last_run
    ? `<span class="key-tile status-open">Live</span> The matcher last ran ${esc(ago(mt.last_run))}, on ${esc(mt.windows_in_last_run)} new exposure ${mt.windows_in_last_run === 1 ? "window" : "windows"}, reading only the saved places in ${mt.windows_in_last_run === 1 ? "its" : "their"} state and days. ${info("trip-index")}`
    : `The matcher runs the moment a new exposure window is stored. ${info("streams")}`;
  const feed = `${location.host}/api/watches/${encodeURIComponent(id)}/calendar`;
  $("subscribe").href = `webcal://${feed}`;
  $("copy-feed").dataset.url = `https://${feed}`;
  $("watch-actions").hidden = false;
  if (t.alerts && $("alerts-note").hidden) { $("alerts-note").textContent = `Alerts are on for ${t.alerts === 1 ? "one device" : `${t.alerts} devices`}.`; $("alerts-note").hidden = false; }
  $("feed-hint").hidden = false;
  const box = $("matches-inner");
  $("matches").hidden = !t.matches.length;
  box.innerHTML = t.matches.length ? `<h2 class="results-h">${t.matches.length === 1 ? "One match" : `${t.matches.length} matches`}</h2>${t.matches.map(matchCard).join("")}` : "";
  hydrateInfo(document);
}

async function load() {
  if (!id) { $("watch-lead").textContent = "This link is missing its trip. Check where you were, then press Watch this trip."; return; }
  try {
    data = await api(`/api/watches/${encodeURIComponent(id)}`);
    render();
  } catch (err) {
    clearInterval(timer);
    $("watch-lead").textContent = err.message;
    $("watch-actions").hidden = true;
  }
}

$("alerts").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  b.disabled = true; b.textContent = "Turning on";
  let msg;
  try { msg = await enableAlerts("watches", id); } catch (err) { msg = err.message; }
  $("alerts-note").textContent = msg; $("alerts-note").hidden = false;
  b.disabled = false; b.textContent = "Alert me on this device";
  load();
});
navigator.serviceWorker?.addEventListener("message", (e) => { if (e.data?.type === "push") load(); });

$("copy-feed").addEventListener("click", async (e) => {
  const b = e.currentTarget;
  try { await navigator.clipboard.writeText(b.dataset.url); b.textContent = "Copied"; } catch { prompt("Calendar address", b.dataset.url); }
  setTimeout(() => { b.textContent = "Copy the calendar address"; }, 2000);
});

$("stop-watch").addEventListener("click", async (e) => {
  if (!confirm("Stop watching this trip and delete it now? This cannot be undone.")) return;
  e.currentTarget.disabled = true;
  try {
    const r = await fetch(`/api/watches/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (!r.ok) throw new Error("We could not delete it. Please try again.");
    clearInterval(timer);
    $("watch-lead").textContent = "Deleted. Seventy-Two no longer has your trip.";
    $("watch-actions").hidden = true; $("feed-hint").hidden = true; $("matches").hidden = true;
    $("stops").innerHTML = ""; $("matcher-line").textContent = "";
  } catch (err) { $("watch-error").textContent = err.message; $("watch-error").hidden = false; e.currentTarget.disabled = false; }
});

$("matches").addEventListener("click", async (e) => {
  const b = e.target.closest(".start-clock");
  if (!b) return;
  const m = data.matches[b.dataset.i];
  b.disabled = true; b.textContent = "Starting";
  try {
    const c = await api("/api/clocks", { body: { window_id: m.window.id, first_exposure: m.first_exposure, last_exposure: m.last_exposure, scale: 1 } });
    location.href = `/clock/?id=${encodeURIComponent(c.id)}`;
  } catch (err) { b.disabled = false; b.textContent = "Start my clock"; $("watch-error").textContent = err.message; $("watch-error").hidden = false; }
});

load();
// a drill is watched closely, so the match shows the moment it is written
let ticks = 0;
timer = setInterval(() => { ticks++; if (!document.hidden && (data?.drill || ticks % 3 === 0)) load(); }, 1500);
