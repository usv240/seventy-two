import { api, esc, fmt, hydrateInfo, info, hoursText, PHASE } from "/assets/site.js";

const $ = (id) => document.getElementById(id);
// a real notice for each disease; the second is read by the same stack with hepatitis A's rule file
const SAMPLES = {
  measles: ["https://www.dhs.wisconsin.gov/news/releases/110626.htm", "Use a real notice from Wisconsin"],
  hepatitis_a: ["https://www.nyc.gov/site/doh/about/press/pr2025/health-department-warns-patrons-of-possible-exposure-to-hepatitis-a.page", "Use a real hepatitis A notice from New York City"],
};
const disease = () => document.querySelector("input[name=disease]:checked")?.value || "measles";
const fromUrl = new URLSearchParams(location.search).get("disease");
if (SAMPLES[fromUrl]) document.querySelector(`input[name=disease][value=${fromUrl}]`).checked = true;
const RULES = { measles: "Code applies the CDC's 72-hour, 6-day and 21-day rules", hepatitis_a: "Code applies the CDC's 2-week rule for the vaccine or immune globulin, and the 50-day watch" };
function syncSample() {
  $("lab-sample").textContent = SAMPLES[disease()][1];
  const li = document.querySelector('#progress [data-step="rules"]');
  if (li) li.textContent = RULES[disease()];
}
document.querySelectorAll("input[name=disease]").forEach((el) => el.addEventListener("change", syncSample));
syncSample();
let last = null;
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

function setStep(name, state) {
  const li = document.querySelector(`#progress [data-step="${name}"]`);
  if (li) li.dataset.state = state;
}

$("lab-sample").addEventListener("click", () => { $("lab-url").value = SAMPLES[disease()][0]; $("lab-text").value = ""; $("lab-form").requestSubmit(); });

$("lab-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("lab-url").value.trim(), text = $("lab-text").value.trim();
  $("lab-error").hidden = true;
  if (!url && text.length < 40) { $("lab-error").textContent = "Paste a notice (at least a sentence) or a link to one."; $("lab-error").hidden = false; return; }
  const btn = $("lab-btn");
  btn.disabled = true; btn.textContent = "Reading";
  document.querySelectorAll("#progress li").forEach((li) => (li.dataset.state = "now"));
  const t0 = performance.now();
  const ticks = [];
  try {
    const r = await api("/api/lab/read", { body: { ...(url ? { url } : { text }), domain: disease() } });
    ticks.forEach(clearTimeout);
    ["fetch", "read", "check", "rules"].forEach((s) => setStep(s, "done"));
    show(r, Math.round(performance.now() - t0));
  } catch (err) {
    ticks.forEach(clearTimeout);
    document.querySelectorAll("#progress li[data-state=now]").forEach((li) => (li.dataset.state = "failed"));
    $("lab-error").textContent = err.message; $("lab-error").hidden = false;
  } finally { btn.disabled = false; btn.textContent = "Read it"; }
});

function show(r, ms) {
  const out = $("lab-out");
  last = r;
  $("lab-results").hidden = false;
  const dom = r.domain || { disease: "measles", actions: [] };
  // the phase words come from the disease the notice was read for
  const phase = { ...PHASE, ...Object.fromEntries(dom.actions.map((a, i) => [`${a.kind}_open`, { word: a.phase_word, cls: i === 0 ? "status-open" : "status-watch" }])) };
  if (!r.is_exposure_notice || !r.windows.length) {
    out.innerHTML = `<h2 class="results-h">Not an exposure notice</h2>
      <p class="lead">This page does not list specific places and times where people may have been exposed to ${esc(dom.disease)}, so there is nothing to check against.</p>
      <p class="small">Read ${r.chars.toLocaleString()} characters in ${(ms / 1000).toFixed(1)} seconds with ${esc(shortModel(r.usage.model))}, for $${r.usage.cost_usd.toFixed(4)}.</p>`;
    return;
  }
  const lags = r.windows.filter((w) => w.lag_days != null);
  const first = dom.actions[0];
  // closed even for someone exposed on the window's last day (for a one-evening window, the same thing)
  const closedAtPub = lags.filter((w) => (w.left_at_publication_last || w.left_at_publication)[`${first.kind}_hours_left`] === 0).length;
  const lagLine = !lags.length ? "" : closedAtPub
    ? ` For ${closedAtPub} of ${lags.length}, the window for ${esc(first.label)} had already closed when it was published, even for people there at the end. ${info("lag")}`
    : ` When it was published, the window for ${esc(first.label)} was still open for at least some of the people exposed. ${info("lag")}`;
  out.innerHTML = `<h2 class="results-h">${r.windows.length} exposure window${r.windows.length === 1 ? "" : "s"} found</h2>
    <p class="lead">${esc(r.issuer || "The notice")}${r.published_date ? `, published ${esc(new Date(r.published_date + "T12:00:00Z").toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" }))}` : ""}.${lagLine}</p>
    <p class="small">Read ${r.chars.toLocaleString()} characters in ${(ms / 1000).toFixed(1)} seconds with ${esc(shortModel(r.usage.model))}, for $${r.usage.cost_usd.toFixed(4)}. ${r.rejected.length ? `${r.rejected.length} proposed window${r.rejected.length === 1 ? " was" : "s were"} dropped because ${esc(r.rejected[0].reason)}.` : "Every proposed window passed the checks."}</p>
    <div class="lab-list">${r.windows.map((w, i) => `<article class="result" data-ui>
      <div class="board result-row" data-ui><table><tbody><tr><td class="place"><span class="flap">${esc(w.place)}</span></td><td class="state-col">${esc(w.state || "")}</td><td class="${(phase[w.status_now.phase] || phase.clear).cls}">${esc((phase[w.status_now.phase] || phase.clear).word)}</td></tr></tbody></table></div>
      <p class="when">${esc(w.local_label)}${dom.name && dom.name !== "measles" ? ` <span class="tag">${esc(dom.disease)}</span>` : ""}</p>
      <blockquote class="quote">${esc(w.quote)} ${info("quote")}</blockquote>
      <p>${w.flags.includes("time_unclear") ? `No clock times given, so the whole day counts. ${info("time-unclear")} ` : ""}${w.flags.includes("crosses_midnight") ? "This window crosses midnight. " : ""}${esc(first.box)} window${w.deadlines_last ? " (it depends on the day you were there; for someone there on the last day, it is" : ""} ${(() => { const dl = (w.deadlines_last || w.deadlines)[`${first.kind}_deadline`]; return new Date(dl) > Date.now() ? `open, ${esc(hoursText((new Date(dl) - Date.now()) / 3600e3))}` : `closed ${esc(fmt(dl, w.timezone))}`; })()}${w.deadlines_last ? ")" : ""}. Watch for symptoms until ${esc(new Date(w.deadlines.watch_end).toLocaleDateString("en-US", { timeZone: w.timezone, month: "long", day: "numeric" }))}.${w.lag_days != null ? ` Published ${w.lag_days} days after the exposure.` : ""}</p>
      <div class="actions">${dayPicker(w, i)}<button class="btn secondary lab-clock" type="button" data-i="${i}">Run a demo clock for this window</button> <span class="small">1 hour runs as 1 second, on the same Step Functions workflow as every clock.</span></div>
    </article>`).join("")}</div>
    <p><a class="btn" href="/check/">Check your own trip</a></p>`;
  hydrateInfo(out);
  if (!reduce) out.querySelectorAll(".result").forEach((el, i) => el.animate([{ opacity: 0, transform: "translateY(14px)" }, { opacity: 1, transform: "none" }], { duration: 380, delay: i * 60, easing: "cubic-bezier(0.23, 1, 0.32, 1)", fill: "both" }));
}

$("lab-out").addEventListener("click", async (e) => {
  const b = e.target.closest(".lab-clock");
  if (!b || !last) return;
  b.disabled = true; b.textContent = "Starting the workflow";
  try {
    // the clock starts at the person's own exposure, so even an old notice can be replayed; for a window of
    // several days, the deadlines run from the day they choose (the last day unless they say otherwise)
    const pick = document.getElementById(`lab-day-${b.dataset.i}`);
    const c = await api("/api/clocks", { body: { lab_id: last.id, index: Number(b.dataset.i), scale: 3600, from_exposure: true, ...(pick ? { day: pick.value } : {}) } });
    location.href = `/clock/?id=${encodeURIComponent(c.id)}`;
  } catch (err) {
    b.disabled = false; b.textContent = "Run a demo clock for this window";
    $("lab-error").textContent = err.message; $("lab-error").hidden = false;
  }
});

// the local days a window covers, in its own time zone
function localDays(w) {
  const day = (t) => new Date(t).toLocaleDateString("en-CA", { timeZone: w.timezone || "UTC" });
  const out = [];
  for (let t = Date.parse(w.start_utc); t <= Date.parse(w.end_utc); t += 6 * 3600e3) { const d = day(t); if (!out.includes(d)) out.push(d); }
  const end = day(Date.parse(w.end_utc));
  if (!out.includes(end)) out.push(end);
  return out;
}
function dayPicker(w, i) {
  const days = localDays(w);
  if (days.length < 2) return "";
  const label = (d) => new Date(d + "T12:00:00Z").toLocaleDateString("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });
  return `<label class="day-pick" for="lab-day-${i}">The day you were there <select id="lab-day-${i}">${days.map((d, k) => `<option value="${d}"${k === days.length - 1 ? " selected" : ""}>${esc(label(d))}</option>`).join("")}</select></label>`;
}

function shortModel(m) { return /haiku/i.test(m) ? "Claude Haiku 4.5 on Amazon Bedrock" : m; }
