import { api, esc, fmt, hoursText, icsFor, download, hydrateInfo, info, DOMAIN } from "/assets/site.js";
import { enableAlerts } from "/assets/alerts.js";

const $ = (id) => document.getElementById(id);
const id = new URLSearchParams(location.search).get("id");

// Friendly names for the workflow's states. Inside the Map the same state runs once per deadline,
// so its row is named for that deadline (the step's "kind", read from the execution history).
let SHOT = Object.fromEntries(DOMAIN.actions.map((a) => [a.kind, a.label]));
let WINDOW = Object.fromEntries(DOMAIN.actions.map((a) => [a.kind, `${a.box} window`]));
const MILESTONE_WAIT = { watch_start: "Wait until the watch starts", all_clear: "Wait until the watch ends" };
const MILESTONE = { watch_start: "Start watching for symptoms", all_clear: "Watch ended" };
const STEP_NAMES = {
  PlanDeadlines: () => "Work out your deadlines from CDC rules",
  EveryDeadline: () => "Run every deadline side by side",
  IsWindowOpen: (k) => `Is the window for ${SHOT[k] || k} still open?`,
  AlreadyClosed: (k) => `${WINDOW[k] || k} had already closed`,
  WaitUntilDayBeforeDeadline: (k) => `Wait until a day before the deadline for ${SHOT[k] || k}`,
  Remind: (k) => `Remind you about ${SHOT[k] || k}`,
  AlreadyCovered: (k) => `No reminder needed for ${SHOT[k] || k}: already done or covered`,
  WaitForYou: (k) => `Wait for you to say you got ${SHOT[k] || k}`,
  YouGotIt: (k) => `You got ${SHOT[k] || k}`,
  WindowClosed: (k) => `${WINDOW[k] || k} closed`,
  WaitUntilMilestone: (k) => MILESTONE_WAIT[k] || "Wait",
  Milestone: (k) => MILESTONE[k] || k,
  ClockFinished: () => "Clock finished",
};
const stepName = (s) => (STEP_NAMES[s.state] ? STEP_NAMES[s.state](s.kind) : null);

let clock = null, timer = null;

function timelineNow(c) {
  // A demo clock runs fast from the moment it was created.
  if (!c.scale || c.scale === 1) return new Date();
  const elapsed = Date.now() - new Date(c.created).getTime();
  return new Date(new Date(c.as_of).getTime() + elapsed * c.scale);
}

// "I got it" is offered for every action whose window is still open and not yet done, from the start.
function openActions(c, now) {
  const ev = c.events || [];
  return (c.actions || []).filter((a) => new Date(c.deadlines[`${a.kind}_deadline`]) > now
    && !ev.some((e) => e.kind === `${a.kind}_done` || e.kind === `${a.kind}_closed`));
}

function render() {
  const c = clock, now = timelineNow(c), d = c.deadlines;
  if (c.actions) {   // a clock carries its own disease's actions (one workflow runs every domain)
    SHOT = Object.fromEntries(c.actions.map((a) => [a.kind, a.label]));
    WINDOW = Object.fromEntries(c.actions.map((a) => [a.kind, `${a.box} window`]));
  }
  $("demo-banner").hidden = !(c.scale > 1);
  $("clock-place").textContent = c.place;
  $("clock-when").textContent = c.local_label || "";
  const box = (label, key, iso, done) => {
    const h = (new Date(iso) - now) / 3600000;
    const state = done ? "Done" : h > 0 ? hoursText(h) : "Closed";
    return `<div class="deadline ${h > 0 && !done ? "open" : "closed"} ${h > 0 && h < 30 && !done ? "urgent" : ""}"><div class="label">${label} ${info(key)}</div><div class="value num" data-iso="${esc(iso)}" data-done="${done ? 1 : 0}">${esc(state)}</div><div class="sub">${esc(fmt(iso))}</div></div>`;
  };
  const ev = c.events || [];
  const done = (k) => ev.some((e) => e.kind === `${k}_done`);
  // an action that makes symptoms start later (immune globulin, for measles) runs the watch longer
  if (c.disease && c.disease !== DOMAIN.disease) $("clock-when").textContent = `${c.local_label || ""}${c.local_label ? ". " : ""}A ${c.disease} clock, on the same workflow as every measles clock.`;
  const longer = (c.actions || []).filter((a) => a.longer_watch_days && done(a.kind));
  const extra = Math.max(0, ...longer.map((a) => a.longer_watch_days));
  const watchEnd = extra ? new Date(new Date(d.watch_end).getTime() + extra * 86400000).toISOString() : d.watch_end;
  const watchH = (new Date(watchEnd) - now) / 3600000;
  $("deadlines").innerHTML =
    (c.actions || []).map((a) => box(a.box, a.info, d[`${a.kind}_deadline`], done(a.kind))).join("") +
    `<div class="deadline ${watchH > 0 ? "open" : "closed"}"><div class="label">Watch for symptoms ${info("watch")}</div><div class="value num" data-iso="${esc(watchEnd)}" data-done="0" data-ended="Ended">${watchH > 0 ? esc(hoursText(watchH)) : "Ended"}</div><div class="sub">Until ${esc(fmt(watchEnd))}${longer.length ? `, longer after ${esc(longer[0].label)}` : ""}</div></div>`;
  const latest = ev.length ? ev[ev.length - 1].text : "Your clock is starting.";
  $("next-step").textContent = latest;
  $("clock-actions").innerHTML =
    openActions(c, now).map((a) => `<button class="btn done" data-kind="${esc(a.kind)}" type="button">I got ${esc(a.label)}</button>`).join("") +
    (c.scale > 1 ? "" : `<button class="btn secondary" id="alerts" type="button">${c.alerts ? "Alerts are on" : "Alert me on this device"}</button>`) +
    `<button class="btn secondary" id="cal" type="button">Add to calendar</button>` +
    (c.phone ? `<a class="btn secondary" href="tel:${esc(c.phone.replace(/[^0-9+]/g, ""))}">Call ${esc(c.phone)}</a>` : "");
  const steps = (c.steps || []).filter((s) => stepName(s) && s.state !== "ActionOrMilestone");
  const late = (s) => {   // a closed window: how long after its planned deadline the workflow closed it
    if (s.state !== "WindowClosed" || !s.deadline_at) return "";
    const ms = Date.parse(s.at) - Date.parse(s.deadline_at);
    return Number.isFinite(ms) ? `<br><span class="small">Closed by the workflow ${(Math.abs(ms) / 1000).toFixed(1)} s ${ms < 0 ? "before" : "after"} its deadline, with nobody pressing anything</span>` : "";
  };
  $("steps").innerHTML = steps.length ? steps.map((s) => `<tr${s.done ? "" : ' class="yours"'}><td class="place"><span class="flap">${esc(stepName(s))}</span>${late(s)}</td><td class="${s.done ? "" : "status-watch"}">${s.done ? "DONE" : "NOW"}</td></tr>`).join("") : `<tr><td colspan="2">Starting</td></tr>`;
  $("exec-status").textContent = `Workflow status: ${c.execution_status.toLowerCase()}.`;
  $("events").innerHTML = ev.map((e) => `<li><span class="small">${esc(new Date(e.at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: c.scale > 1 ? "2-digit" : undefined }))}</span><br>${esc(e.text)}</li>`).join("");
  hydrateInfo(document);
}

async function load() {
  try {
    clock = await api(`/api/clocks/${encodeURIComponent(id)}`);
    render();
    const done = ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"].includes(clock.execution_status);
    clearTimeout(timer);
    if (!done) timer = setTimeout(load, clock.scale > 1 ? 2000 : 60000);
  } catch (err) {
    $("clock-place").textContent = err.message;
  }
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest(".done");
  if (b) {
    b.disabled = true;
    try { await api(`/api/clocks/${encodeURIComponent(id)}/done`, { body: { kind: b.dataset.kind } }); setTimeout(load, 800); }
    catch (err) { const el = $("clock-error"); el.textContent = err.message; el.hidden = false; b.disabled = false; }
  }
  if (e.target.id === "cal" && clock) {
    download(`exposure-clock-${id}.ics`, icsFor({ deadlines: clock.deadlines }, clock.place, clock.phone, clock.actions || DOMAIN.actions, clock.disease));
  }
});

// keep the countdowns moving between polls without rebuilding the buttons
setInterval(() => {
  if (!clock) return;
  const now = timelineNow(clock);
  document.querySelectorAll("#deadlines [data-iso]").forEach((el) => {
    if (el.dataset.done === "1") return;
    const h = (new Date(el.dataset.iso) - now) / 3600000;
    el.textContent = h > 0 ? hoursText(h) : (el.dataset.ended || "Closed");
  });
}, 1000);
if (id) load(); else $("clock-place").textContent = "No clock id in the address. Start a clock from the check page.";

document.addEventListener("click", async (e) => {
  const b = e.target.closest("#alerts");
  if (!b) return;
  b.disabled = true; b.textContent = "Turning on";
  let msg;
  try { msg = await enableAlerts("clocks", id); } catch (err) { msg = err.message; }
  const note = document.getElementById("alerts-note");
  note.textContent = msg; note.hidden = false;
  b.disabled = false; b.textContent = "Alert me on this device";
});
