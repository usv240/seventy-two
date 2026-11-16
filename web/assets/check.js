import { api, esc, fmt, fmtDay, hoursText, PHASE, DOMAIN, icsFor, download, hydrateInfo, info } from "/assets/site.js";

// the sample is already understood, so it is sent as a stop: instant, and it never waits on the model
const REAL = { text: "Philadelphia airport, terminal B, Sunday Sept 13 2026, 8 to 11:30 pm", date: "2026-09-19", stops: [{ place: "Philadelphia airport, terminal B", city: "Philadelphia", state: "PA", date: "2026-09-13", start_time: "20:00", end_time: "23:30", timezone: "America/New_York" }] };
const $ = (id) => document.getElementById(id);
let last = null;

$("replay-on").addEventListener("change", (e) => { $("replay-fields").hidden = !e.target.checked; });
$("use-real").addEventListener("click", () => {
  $("q").value = REAL.text; $("replay-on").checked = true; $("replay-fields").hidden = false; $("replay-date").value = REAL.date;
  $("check-form").requestSubmit();
});

function asOf() {
  if (!$("replay-on").checked || !$("replay-date").value) return null;
  return `${$("replay-date").value}T16:00:00Z`;  // noon US Eastern on the chosen day
}

function deadlineBox(label, key, iso, tz, now, extra = "") {
  const h = (new Date(iso) - now) / 3600000;
  const open = h > 0;
  return `<div class="deadline ${open ? "open" : "closed"} ${open && h < 30 ? "urgent" : ""}">
    <div class="label">${label} ${info(key)}</div>
    <div class="value num" data-deadline="${esc(iso)}">${open ? esc(hoursText(h)) : "Closed"}</div>
    <div class="sub">${open ? "Until " : "Closed "}${esc(fmt(iso, tz))}${extra}</div></div>`;
}

function card(m, i, res) {
  const w = m.window, st = m.status, ph = PHASE[st.phase] || PHASE.watch;
  const now = new Date(res.as_of);
  const d = m.deadlines;
  const watchEnd = fmtDay(d.watch_end, w.timezone);
  const sources = (w.sources || [{ issuer: w.issuer, url: w.url }]).filter((s) => s.url);
  const timeNote = !w.time_clear ? `<p class="small">This notice does not give clock times, so the whole day counts. ${info("time-unclear")}</p>` : (!m.time_given ? `<p class="small">You did not give a time, so we matched the whole day. Add a time to be more precise.</p>` : "");
  const steps = {
    mmr_open: ["Check your vaccine record. Two MMR doses, or being born before 1957, usually means you are protected.", `If you are not protected, call your doctor, a pharmacy or the health department today about the MMR vaccine. The window closes ${fmt(d.mmr_deadline, w.timezone)}.`, "If you are pregnant, have a baby with you or have a weak immune system, ask about immune globulin instead.", `Watch for symptoms until ${watchEnd}. If any start, call before you go in.`],
    ig_open: ["The 72-hour vaccine window has closed.", `If you are pregnant, have a baby with you or have a weak immune system and are not protected, call your doctor today about immune globulin. That window closes ${fmt(d.ig_deadline, w.timezone)}.`, `Everyone: watch for fever, cough, runny nose, red eyes and a rash until ${watchEnd}.`, "If symptoms start, call before you go in, so the clinic can protect others."],
    watch: ["The vaccine and immune globulin windows have closed for this exposure.", `Watch for fever, cough, runny nose, red eyes and a rash until ${watchEnd}.`, "If symptoms start, call your doctor before you go in.", "Getting vaccinated now still protects you from future exposures."],
    clear: ["The watch period for this exposure has ended.", "If you have had no symptoms, no action is needed for this exposure.", "Getting vaccinated still protects you from future exposures."],
  }[st.phase] || [st.next_step, `Watch for symptoms until ${watchEnd}. If any start, call your doctor before you go in.`];
  return `<article class="result" data-ui data-i="${i}">
    <div class="board result-row" data-ui><table><tbody><tr class="yours"><td class="place"><span class="flap">${esc(w.place)}</span></td><td class="state-col">${esc(w.state || "")}</td><td class="${ph.cls}">${esc(ph.word)}</td></tr></tbody></table></div>
    <p class="when">${esc(w.local_label || fmt(w.start_utc, w.timezone))}</p>
    <p class="small">You said: ${esc(m.stop)}. Matched ${info("match")}</p>
    ${timeNote}
    <div class="deadline-grid">
      ${DOMAIN.actions.map((a) => deadlineBox(a.box, a.info, d[`${a.kind}_deadline`], w.timezone, now)).join("")}
      <div class="deadline open"><div class="label">Watch for symptoms ${info("watch")}</div><div class="value">To ${esc(new Date(d.watch_end).toLocaleDateString("en-US", { timeZone: w.timezone, month: "short", day: "numeric" }))}</div><div class="sub">Starts ${esc(fmtDay(d.watch_start, w.timezone))}</div></div>
    </div>
    <h3 class="mini-h">What to do now</h3>
    <ol class="steps">${steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
    <blockquote class="quote">${esc(w.quote)} ${info("quote")}</blockquote>
    <p class="small">From ${sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.issuer || "the health department")}</a>`).join(", ")}${w.published_date ? `, published ${esc(new Date(w.published_date + "T12:00:00Z").toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" }))}` : ""}${w.lag_days != null && w.lag_measured ? `, ${w.lag_days} days after the exposure ${info("lag")}` : ""}.</p>
    <div class="actions">
      <button class="btn start-clock" type="button" data-i="${i}">Start my clock</button>
      <button class="btn secondary add-cal" type="button" data-i="${i}">Add to calendar</button>
      ${w.phone ? `<a class="btn secondary" href="tel:${esc(w.phone.replace(/[^0-9+]/g, ""))}">Call ${esc(w.phone)}</a>` : ""}
    </div>
    <label class="switch demo-switch"><input type="checkbox" class="demo" data-i="${i}" ${res.replay ? "checked" : ""}> <span>Run the clock at demo speed: 1 hour passes in 1 second ${info("demo-clock")}</span></label>
  </article>`;
}

function render(res) {
  last = res;
  $("understood").hidden = false;
  $("stops").innerHTML = res.stops.length ? res.stops.map((s) => `<li><strong>${esc(s.place)}</strong>${s.city ? `, ${esc(s.city)}` : ""}<br>${esc(fmt(s.start_utc, s.timezone, { weekday: "long" }))}${s.time_given ? ` to ${esc(new Date(s.end_utc).toLocaleTimeString("en-US", { timeZone: s.timezone, hour: "numeric", minute: "2-digit" }))}` : " (no time given, whole day)"}</li>`).join("") : "<li>No places found.</li>";
  const box = $("results-inner");
  $("results").hidden = false;
  const replay = res.replay ? `<p class="replay-note">Replay: this is what you would have seen on ${esc(new Date(res.as_of).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" }))}, using only notices published by then.</p>` : "";
  const lastStop = Math.max(0, ...res.stops.map((s) => new Date(s.end_utc).getTime()));
  const watchable = !res.replay && res.stops.length && lastStop + 21 * 86400e3 > Date.now();
  const watchBox = watchable ? `<div class="watch-cta" data-ui>
      <h3 class="mini-h">${res.matches.length ? "Keep watching the rest of your trip" : "Let the next notice find you"} ${info("trip-watch")}</h3>
      <p>Notices usually go up days after an exposure. Save this trip and Seventy-Two checks it against every new notice the moment it is read, until 21 days after your last stop. If one matches, your trip page and your calendar show it, with what to do.</p>
      <div class="actions"><button class="btn" type="button" id="save-trip">Watch this trip</button><span class="small">No account. Deleted on its own after 21 days.</span></div>
    </div>` : "";
  const samePlace = res.nearby.filter((n) => n.same_place), others = res.nearby.filter((n) => !n.same_place);
  if (!res.matches.length) {
    box.innerHTML = `${replay}<h2 class="results-h">No match, as of ${esc(fmt(res.as_of))}</h2>
      <p class="lead">None of the ${res.windows_checked} exposure windows we know about matches where you were.</p>
      ${watchBox}
      <p>If your notice is from a health department we do not read yet, paste it into <a href="/lab/">the Lab</a>.</p>
      ${samePlace.length ? `<div class="notice-box"><h3 class="mini-h">Same place, different time</h3><p>You were at a place with a published exposure, but outside its times. If you are not sure of your times, check them. If they could have overlapped, call the health department.</p><ul class="stop-list">${samePlace.slice(0, 4).map((n) => `<li><strong>${esc(n.window.place)}</strong>: exposure ${esc(n.window.local_label)}. Your visit was ${esc(n.hours_apart)} hours away from it.${n.window.phone ? ` <a href="tel:${esc(n.window.phone.replace(/[^0-9+]/g, ""))}">Call ${esc(n.window.phone)}</a>` : ""}</li>`).join("")}</ul></div>` : ""}
      ${others.length ? `<h3 class="mini-h">Other windows near your places</h3><ul class="stop-list">${others.slice(0, 6).map((n) => `<li>${esc(n.window.place)}: ${esc(n.window.local_label)}</li>`).join("")}</ul>` : ""}`;
  } else {
    box.innerHTML = `${replay}<h2 class="results-h">${res.matches.length === 1 ? "One match" : `${res.matches.length} matches`}</h2>
      <p class="lead">${esc(res.matches[0].status.next_step)}</p>${res.matches.map((m, i) => card(m, i, res)).join("")}${watchBox}`;
  }
  hydrateInfo(box);
  box.closest("section").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

$("results").addEventListener("click", async (e) => {
  const save = e.target.closest("#save-trip");
  if (save) {
    save.disabled = true; save.textContent = "Saving";
    try {
      const w = await api("/api/watches", { body: { stops: last.stops } });
      location.href = `/watch/?id=${encodeURIComponent(w.id)}`;
    } catch (err) { save.disabled = false; save.textContent = "Watch this trip"; alertBox(err.message); }
    return;
  }
  const cal = e.target.closest(".add-cal");
  if (cal) {
    const m = last.matches[cal.dataset.i];
    download(`measles-${m.window.id}.ics`, icsFor(m, m.window.place, m.window.phone));
    return;
  }
  const start = e.target.closest(".start-clock");
  if (!start) return;
  const m = last.matches[start.dataset.i];
  const demo = document.querySelector(`.demo[data-i="${start.dataset.i}"]`).checked;
  start.disabled = true; start.textContent = "Starting";
  try {
    const c = await api("/api/clocks", { body: { window_id: m.window.id, first_exposure: m.first_exposure, last_exposure: m.last_exposure, as_of: last.replay ? last.as_of : undefined, scale: demo ? 3600 : 1 } });
    location.href = `/clock/?id=${encodeURIComponent(c.id)}`;
  } catch (err) {
    start.disabled = false; start.textContent = "Start my clock";
    alertBox(err.message);
  }
});

function alertBox(msg) { const el = $("check-error"); el.textContent = msg; el.hidden = false; }

$("check-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("q").value.trim();
  $("check-error").hidden = true;
  if (text.length < 4) { alertBox("Tell us at least one place and a date."); $("q").focus(); return; }
  const btn = $("check-btn");
  btn.disabled = true; btn.textContent = "Checking";
  try {
    const a = asOf();
    const sample = text === REAL.text && a === `${REAL.date}T16:00:00Z`;
    render(await api("/api/check", { body: { text, ...(a ? { as_of: a } : {}), ...(sample ? { stops: REAL.stops } : {}) } }));
  } catch (err) { alertBox(err.message); }
  finally { btn.disabled = false; btn.textContent = "Check"; }
});

// live countdowns (real time only; replays stay frozen at the replay date)
setInterval(() => {
  if (!last || last.replay) return;
  document.querySelectorAll("[data-deadline]").forEach((el) => {
    const h = (new Date(el.dataset.deadline) - Date.now()) / 3600000;
    el.textContent = h > 0 ? hoursText(h) : "Closed";
  });
}, 30000);

// arrivals from the landing page
const params = new URLSearchParams(location.search);
if (params.get("from") === "home") {
  try { const r = JSON.parse(sessionStorage.getItem("seventytwo-last")); if (r) { $("q").value = r.query || ""; if (r.replay) { $("replay-on").checked = true; $("replay-fields").hidden = false; $("replay-date").value = r.as_of.slice(0, 10); } render(r); } } catch {}
} else if (params.get("replay") === "phl") {
  $("use-real").click();
} else if (params.get("q")) {
  $("q").value = params.get("q"); $("check-form").requestSubmit();
}
