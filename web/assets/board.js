import { api, esc, fmt, hydrateInfo, info, shortPlace, windowStatus as status } from "/assets/site.js";

const $ = (id) => document.getElementById(id);
let all = [];

function rowHtml(w, i) {
  const [word, cls] = status(w);
  const srcs = (w.sources || []).filter((s) => s.url);
  return `<tr class="row" tabindex="0" data-i="${i}" aria-expanded="false"><td class="place"><span class="flap">${esc(shortPlace(w.place))}</span></td><td class="state-col">${esc(w.state || "")}</td><td class="when">${esc(w.local_label || fmt(w.start_utc, w.timezone))}</td><td class="${cls}">${word}</td></tr>
  <tr class="detail" data-for="${i}" hidden><td colspan="4"><div class="detail-in">
    <p class="quote-plain">"${esc(w.quote)}" ${info("quote")}</p>
    <p>${w.address ? esc(w.address) + ". " : ""}${!w.time_clear ? "No clock times given, so the whole day counts. " : ""}${w.lag_measured ? `Notice published ${esc(w.published_date)}, ${w.lag_days} days after the exposure. ` : ""}${w.phone ? `Health department: <a href="tel:${esc(w.phone.replace(/[^0-9+]/g, ""))}">${esc(w.phone)}</a>.` : ""}</p>
    <p>Reported by ${srcs.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.issuer || "a health department")}</a>`).join(", ")}. <a href="/check/?q=${encodeURIComponent(`${shortPlace(w.place)}, ${new Date(w.start_utc).toLocaleDateString("en-US", { timeZone: w.timezone, month: "long", day: "numeric", year: "numeric" })}`)}">Check if this was you</a></p>
  </div></td></tr>`;
}

function draw() {
  const q = $("f-q").value.trim().toLowerCase(), st = $("f-state").value;
  const rows = all.filter((w) => (!st || w.state === st) && (!q || `${w.place} ${w.address || ""} ${w.city || ""}`.toLowerCase().includes(q)));
  $("rows").innerHTML = rows.map(rowHtml).join("") || `<tr><td colspan="4">No windows match.</td></tr>`;
  $("board-caption").textContent = `${rows.length} exposure window${rows.length === 1 ? "" : "s"}${q || st ? " match" : ""}. Several health departments often report the same exposure; each appears once.`;
  hydrateInfo($("rows"));
}

async function load() {
  let data;
  try { data = await api(`/api/exposures${$("f-all").checked ? "?all=1" : ""}`); }
  catch { data = await (await fetch("/data/exposures.json")).json(); }
  all = data.windows || [];
  const states = [...new Set(all.map((w) => w.state).filter(Boolean))].sort();
  const sel = $("f-state"), keep = sel.value;
  sel.innerHTML = `<option value="">All states</option>` + states.map((s) => `<option${s === keep ? " selected" : ""}>${s}</option>`).join("");
  $("as-of").textContent = `As of ${fmt(data.as_of)}.`;
  draw();
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    const watched = s.sources.filter((x) => !x.found_on);
    const found = s.sources.filter((x) => x.found_on);
    $("n-sources").textContent = watched.length;
    const ok = watched.filter((x) => !x.failures).length;
    $("figures").innerHTML = `
      <dt>Pages read without errors</dt><dd class="num">${ok} of ${watched.length}</dd>
      <dt>New notices found on their news pages ${info("discovery")}</dt><dd class="num">${found.length}</dd>
      <dt>Exposure windows, all time</dt><dd class="num">${s.windows_total}</dd>
      <dt>Median notice lag ${info("lag")}</dt><dd class="num">${s.lag_days_median} days</dd>
      ${(s.alarms || []).length ? `<dt>Alarms in alarm ${info("alarms")}</dt><dd class="num">${s.alarms.filter((a) => a.state === "ALARM").length} of ${s.alarms.length}</dd>` : ""}`;
    const alarmWord = { OK: "OK", ALARM: "ALARM", INSUFFICIENT_DATA: "NO DATA YET" };
    const watches = { "reads-stopped": "Scheduled reads starting every 30 minutes", "read-failed": "Scheduled reads finishing",
      "sources-failing": "No page failing three runs in a row", "matcher-failed": "New windows reaching saved trips",
      "matcher-lagging": "New windows matched within a minute", "clock-failed": "Every person's clock running",
      "api-errors": "The API answering without errors", "model-spend": "Claude spending under $1 an hour" };
    const order = Object.keys(watches);
    s.alarms.sort((a, b) => order.findIndex((k) => a.name.endsWith(k)) - order.findIndex((k) => b.name.endsWith(k)));
    const label = (a) => Object.entries(watches).find(([k]) => a.name.endsWith(k))?.[1] || a.name;
    $("alarms").innerHTML = (s.alarms || []).map((a) => `<li title="${esc(a.about)}"><span class="key-tile ${a.state === "ALARM" ? "status-closed" : "status-open"}">${esc(alarmWord[a.state] || a.state)}</span> ${esc(label(a))}</li>`).join("");
    $("alarms").hidden = !(s.alarms || []).length;
    const day = (iso) => new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
    const time = (iso) => new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    const last = (x) => x.failures ? "FAILED: " + esc((x.last_error || "").slice(0, 40))
      : x.found_on ? "Found " + esc(day(x.last_change || x.last_ok)) : esc(x.last_ok ? time(x.last_ok) : "");
    const byState = (a, b) => (a.state || "").localeCompare(b.state || "");
    const row = (x) => `<tr><td class="place"><a href="${esc(x.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(x.issuer || x.id)}</a> <span class="small">${esc(x.state || "")}${x.kind === "index" ? " · news page" : ""}</span></td><td class="num n">${x.kind === "index" ? "" : x.windows ?? 0}</td><td class="${x.failures ? "status-closed" : ""}">${last(x)}</td></tr>`;
    $("sources").innerHTML = watched.sort(byState).map(row).join("")
      + (found.length ? `<tr class="group"><th scope="rowgroup" colspan="3">New notices found on their news pages ${info("discovery")}</th></tr>` + found.sort(byState).map(row).join("") : "");
    hydrateInfo($("figures"));
  } catch { $("figures").innerHTML = "<dt>Status is unavailable right now.</dt>"; }
}

document.addEventListener("click", (e) => {
  const r = e.target.closest("tr.row");
  if (!r || e.target.closest("a, button")) return;
  toggle(r);
});
document.addEventListener("keydown", (e) => { const r = e.target.closest && e.target.closest("tr.row"); if (r && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggle(r); } });
function toggle(r) {
  const d = document.querySelector(`tr.detail[data-for="${r.dataset.i}"]`);
  d.hidden = !d.hidden;
  r.setAttribute("aria-expanded", String(!d.hidden));
}
$("f-q").addEventListener("input", draw);
$("f-state").addEventListener("change", draw);
$("f-all").addEventListener("change", load);
load(); loadStatus();
