import { api, esc, flipText, fmt, hoursText, PHASE, shortPlace, hydrateInfo, info, windowStatus, DOMAIN } from "/assets/site.js";

// the sample is already understood, so it is sent as a stop: instant, and it never waits on the model
const REAL = { text: "Philadelphia airport, terminal B, Sunday Sept 13 2026, 8 to 11:30 pm", as_of: "2026-09-19T16:00:00Z", stops: [{ place: "Philadelphia airport, terminal B", city: "Philadelphia", state: "PA", date: "2026-09-13", start_time: "20:00", end_time: "23:30", timezone: "America/New_York" }] };
let replayAsOf = null;

function row(w) {
  const day = new Date(w.start_utc).toLocaleDateString("en-US", { timeZone: w.timezone, month: "short", day: "numeric" });
  const t = w.time_clear ? new Date(w.start_utc).toLocaleTimeString("en-US", { timeZone: w.timezone, hour: "numeric", minute: "2-digit" }) : "time not given";
  const [word, cls] = windowStatus(w);
  return `<tr><td class="place"><span class="flap">${esc(shortPlace(w.place))}</span></td><td class="state-col">${esc(w.state || "")}</td>
    <td class="when">${esc(day)} ${esc(t)}</td><td class="${cls}">${word}</td></tr>`;
}

async function loadBoard() {
  let data;
  try { data = await api("/api/exposures"); } catch { data = await (await fetch("/data/exposures.json")).json(); }
  const rows = (data.windows || []).slice(0, 4);
  const body = document.getElementById("hero-rows");
  const yours = document.getElementById("your-row");
  body.innerHTML = "";
  body.appendChild(yours);
  body.insertAdjacentHTML("beforeend", rows.length ? rows.map(row).join("") : `<tr><td class="place" colspan="4">No active exposure windows right now.</td></tr>`);
}

function setStat(name, value) { document.querySelectorAll(`[data-stat="${name}"]`).forEach((el) => (el.textContent = value)); }

async function loadStats() {
  try {
    const s = await api("/api/status");
    const issuers = new Set((s.sources || []).map((x) => x.issuer)).size;
    const states = new Set((s.sources || []).map((x) => x.state).filter(Boolean)).size;
    setStat("issuers", issuers); setStat("sources", (s.sources || []).filter((x) => !x.found_on).length); setStat("states", states);
    if (s.lag_days_median != null) {
      setStat("lag", (Math.round(s.lag_days_median * 10) / 10).toString());
      setStat("samples", s.with_publication_date);
      setStat("mmrClosed", `${s.mmr_closed_before_publication} of ${s.with_publication_date}`);
      setStat("igClosed", `${s.ig_closed_before_publication} of ${s.with_publication_date}`);
    }
  } catch { /* the page already shows the last measured numbers */ }
}

async function lagChart() {
  const el = document.getElementById("lag-chart");
  let data;
  try { data = await api("/api/exposures?all=1"); } catch { return; }
  const rows = (data.windows || []).filter((w) => w.lag_measured && w.lag_days != null).sort((a, b) => a.lag_days - b.lag_days);
  if (!rows.length) return;
  const max = Math.max(14, Math.ceil(Math.max(...rows.map((r) => r.lag_days)) / 2) * 2);
  const pct = (d) => `${(d / max) * 100}%`;
  const ticks = [];
  for (let d = 0; d <= max; d += max > 16 ? 4 : 2) ticks.push(`<span style="left:${pct(d)}">${d === 0 ? "Exposure" : `${d} days`}</span>`);
  el.innerHTML = `
    <div class="lc-marks">
      <span class="lc-mark" style="left:${pct(3)}"><span>72 hours: vaccine window closes</span></span>
      <span class="lc-mark second" style="left:${pct(6)}"><span>6 days: immune globulin window closes</span></span>
    </div>
    <div class="lc-rows">${rows.map((r) => `<span class="lc-row${r.lag_days > 3 ? " late" : ""}" style="width:${pct(Math.max(0.15, r.lag_days))}" title="${esc(r.place)}: published ${r.lag_days} days after the exposure"></span>`).join("")}
      <span class="lc-line" style="left:${pct(3)}"></span><span class="lc-line" style="left:${pct(6)}"></span></div>
    <div class="lc-axis">${ticks.join("")}</div>`;
}

function renderResult(res) {
  const box = document.getElementById("hero-result");
  const yours = document.getElementById("your-row");
  yours.hidden = false;
  if (!res.matches.length) {
    flipText(document.getElementById("yr-place"), "No match");
    document.getElementById("yr-when").textContent = res.stops.map((s) => s.place).join(", ");
    document.getElementById("yr-status").textContent = "CLEAR SO FAR";
    box.hidden = false;
    box.innerHTML = `<p class="lead">No notice matches where you were, as of ${esc(fmt(res.as_of))}.</p>
      <p>We checked ${res.windows_checked} exposure windows from ${res.sources_watched} health department pages. Notices often arrive days late, so check again in a few days, or <a href="/check/">open the full check</a> to see what we understood.</p>`;
    return;
  }
  const m = res.matches[0];
  const w = m.window, st = m.status, ph = PHASE[st.phase] || PHASE.watch;
  flipText(document.getElementById("yr-place"), shortPlace(w.place));
  document.getElementById("yr-state").textContent = w.state || "";
  document.getElementById("yr-when").textContent = new Date(w.start_utc).toLocaleString("en-US", { timeZone: w.timezone, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const s = document.getElementById("yr-status");
  s.className = ph.cls;
  const left = st.phase.endsWith("_open") ? st[`${st.phase.slice(0, -5)}_hours_left`] : null;
  flipText(s, left != null ? `${ph.word} ${Math.floor(left)} H` : ph.word);
  const watchEnd = new Date(m.deadlines.watch_end).toLocaleDateString("en-US", { timeZone: w.timezone, month: "long", day: "numeric" });
  try { sessionStorage.setItem("seventytwo-last", JSON.stringify(res)); } catch {}
  const now = new Date(res.as_of);
  const box3 = (label, key, iso) => {
    const h = (new Date(iso) - now) / 3600000;
    return `<div class="deadline ${h > 0 ? "open" : "closed"} ${h > 0 && h < 30 ? "urgent" : ""}"><div class="label">${label} ${info(key)}</div><div class="value">${esc(h > 0 ? hoursText(h) : "Closed")}</div><div class="sub">${esc(fmt(iso, w.timezone))}</div></div>`;
  };
  box.hidden = false;
  box.innerHTML = `<div class="hero-result-grid"><div>
    <p class="lead">${esc(st.next_step)}</p>
    <p>You were at ${esc(w.place)} during the exposure window in the ${esc(w.issuer || "health department")} notice. Watch for symptoms until ${esc(watchEnd)}.${res.replay ? ` <em>Replay: as it would have read on ${esc(new Date(res.as_of).toLocaleDateString("en-US", { month: "long", day: "numeric" }))}.</em>` : ""}</p>
    <p class="actions"><a class="btn" href="/check/?from=home">See your full result and start your clock</a>${w.phone ? `<a class="btn secondary" href="tel:${esc(w.phone.replace(/[^0-9+]/g, ""))}">Call ${esc(w.phone)}</a>` : ""}</p>
    </div><div class="deadline-grid stack" data-ui>${DOMAIN.actions.map((a) => box3(a.box, a.info, m.deadlines[`${a.kind}_deadline`])).join("")}<div class="deadline open"><div class="label">Watch for symptoms ${info("watch")}</div><div class="value">To ${esc(new Date(m.deadlines.watch_end).toLocaleDateString("en-US", { timeZone: w.timezone, month: "short", day: "numeric" }))}</div><div class="sub">Starts ${esc(new Date(m.deadlines.watch_start).toLocaleDateString("en-US", { timeZone: w.timezone, weekday: "short", month: "short", day: "numeric" }))}</div></div></div></div>`;
}

document.getElementById("try-real").addEventListener("click", () => {
  document.getElementById("trip").value = REAL.text;
  replayAsOf = REAL.as_of;
  document.getElementById("hero-check").requestSubmit();
});
document.getElementById("trip").addEventListener("input", () => { replayAsOf = null; });

document.getElementById("hero-check").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = e.target.querySelector("button[type=submit]");
  const text = document.getElementById("trip").value.trim();
  if (!text) return;
  btn.disabled = true; btn.textContent = "Checking";
  const box = document.getElementById("hero-result");
  try {
    const sample = text === REAL.text && replayAsOf === REAL.as_of;
    const res = await api("/api/check", { body: { text, ...(replayAsOf ? { as_of: replayAsOf } : {}), ...(sample ? { stops: REAL.stops } : {}) } });
    res.query = text;
    renderResult(res);
    // the answer sits under the board: bring it into view so a first-time visitor sees it
    const r = box.getBoundingClientRect();
    if (r.bottom > innerHeight) box.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  } catch (err) {
    box.hidden = false;
    box.innerHTML = `<p class="error">${esc(err.message)}</p>`;
  } finally {
    btn.disabled = false; btn.textContent = "Check";
    hydrateInfo(box);
  }
});

loadBoard(); loadStats(); lagChart();
