import { api, esc, hydrateInfo, info } from "/assets/site.js";

const $ = (id) => document.getElementById(id);
const nf = new Intl.NumberFormat("en-US");

// ---------- formatting ----------
function ms(v) {
  if (v == null) return "-";
  if (v >= 60000) return `${(v / 60000).toFixed(1)} min`;
  if (v >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 0 : 1)} s`;
  return `${Math.round(v)} ms`;
}
const usd = (v) => (v == null ? "-" : v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
const count = (v) => (v == null ? "-" : nf.format(Math.round(v)));
const pct = (v) => (v == null ? "-" : `${v.toFixed(2)}%`);

function spark(values, kind) {
  const max = Math.max(0, ...values.filter((v) => v != null));
  const w = 240, h = 26, bw = w / values.length;
  const bars = values.map((v, i) => {
    if (v == null || max === 0) return `<rect class="base" x="${(i * bw + 1).toFixed(1)}" y="${h - 1}" width="${(bw - 2).toFixed(1)}" height="1"></rect>`;
    const bh = Math.max(2, (v / max) * (h - 2));
    return `<rect class="${kind === "bad" && v > 0 ? "bad" : ""}" x="${(i * bw + 1).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${bh.toFixed(1)}"></rect>`;
  }).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">${bars}</svg>`;
}

// ---------- what each group shows ----------
const GROUPS = [
  { title: "Reading notices", note: "Every 30 minutes, every source in parallel.", rows: [
    ["sfn_ingest_started", "Scheduled reads started", count],
    ["sfn_ingest_failed", "Scheduled reads that failed", count, "bad"],
    ["sfn_ingest_ms", "Time for one whole read, average", ms],
    ["src_read", "Changed pages read by Claude", count],
    ["src_failed", "Pages that failed after retries", count, "bad"],
    ["win_found", "New exposure windows", count],
    ["win_rejected", "Claude's proposals the checks dropped", count, null, "evidence"],
    ["model_calls", "Claude calls (notices and trips)", count],
    ["model_p50", "Claude latency, median", ms],
    ["model_p95", "Claude latency, p95", ms, null, "p95"],
    ["model_usd", "Claude spend", usd],
  ] },
  { title: "Visitors", note: "CloudFront serves the pages and caches the board; API Gateway answers the rest.", rows: [
    ["cf_requests", "Requests to the site", count],
    ["cf_errors", "Share of requests that errored", pct, "bad"],
    ["api_requests", "API requests", count],
    ["api_4xx", "API answers 4xx (not found, too many)", count],
    ["api_5xx", "API answers 5xx", count, "bad", "drills"],
    ["api_p50", "API latency, median", ms],
    ["api_p99", "API latency, p99", ms, null, "p95"],
    ["checks", "Trip checks", count],
    ["check_p95", "Check time, p95 (includes Claude)", ms],
  ] },
  { title: "Watching trips", note: "A new window reaches the saved trips it matches through DynamoDB Streams.", rows: [
    ["match_windows", "New windows matched against saved trips", count],
    ["match_p95", "Matching time per window, p95", ms],
    ["match_stops", "Saved stops read for one window, most", count, null, "trip-index"],
    ["match_trips", "Trips read for one window, most", count],
    ["stored_p50", "Stored to matched, median", ms],
    ["stored_max", "Stored to matched, slowest", ms],
    ["stream_age", "Stream lag, most", ms, null, "iterator-age"],
    ["trip_matches", "Matches recorded on saved trips", count],
    ["push_sent", "Notifications accepted by push services", count],
    ["push_failed", "Notifications refused", count, "bad"],
    ["dlq", "Stream batches left unmatched (SQS)", count, "bad"],
  ], foot: "Errors and unmatched batches on November 15 came from a bug I shipped and fixed that morning: the alarm went off, the failed batches waited in SQS, and scripts/replay.py matched them again. They leave this 24-hour view on November 16." },
  { title: "Clocks", note: "One Step Functions run per person; each deadline is a task token's timeout.", rows: [
    ["sfn_clock_started", "Clocks started", count],
    ["sfn_clock_ok", "Clocks finished", count],
    ["sfn_clock_failed", "Clocks that failed", count, "bad"],
    ["slip_n", "Windows closed by the clock itself", count],
    ["slip_max", "Latest close after its deadline", ms, null, "deadline-slip"],
  ] },
  { title: "Data", note: "One DynamoDB table, on demand.", rows: [
    ["ddb_rcu", "Read units used", count],
    ["ddb_wcu", "Write units used", count],
    ["ddb_rthr", "Reads throttled", count, "bad"],
    ["ddb_wthr", "Writes throttled", count, "bad"],
  ], foot: "Throttled writes come from the scale tests, which write tens of thousands of items in a minute: DynamoDB slows them while it adds capacity, and the script retries them. Visitors' requests have not been throttled." },
];
const ROLE = { api: "API", ingest: "Read one source", snapshot: "Publish the board", matcher: "Match new windows", clock: "Clock steps" };

function groupHtml(g, series) {
  const rows = g.rows.filter(([id]) => series[id]).map(([id, label, f, kind, key]) => {
    const s = series[id];
    return `<tr><td>${esc(label)}${key ? " " + info(key) : ""}</td><td class="sp">${spark(s.values, kind)}</td><td class="v num${kind === "bad" && s.total > 0 ? " status-closed" : ""}">${esc(f(s.total))}</td></tr>`;
  }).join("");
  return `<div class="ops-group"><h3 class="mini-head">${esc(g.title)}</h3><p class="small">${esc(g.note)}</p>
    <div class="board"><table class="ops-table"><thead><tr><th scope="col">Measure</th><th scope="col">Each hour</th><th scope="col" class="v">24 hours</th></tr></thead><tbody>${rows}</tbody></table></div>${g.foot ? `<p class="small ops-foot">${esc(g.foot)}</p>` : ""}</div>`;
}

function functionsHtml(series, roles) {
  const rows = Object.keys(roles).map((r) => {
    const g = (k) => (series[`fn_${r}_${k}`] || {}).total;
    return `<tr><td>${esc(ROLE[r] || r)}</td><td class="v num">${count(g("inv"))}</td><td class="v num${g("err") > 0 ? " status-closed" : ""}">${count(g("err"))}</td><td class="v num">${count(g("thr"))}</td><td class="v num">${ms(g("p95"))}</td><td class="v num">${count(g("conc"))}</td></tr>`;
  }).join("");
  return `<div class="ops-group"><h3 class="mini-head">Functions</h3><p class="small">Five Lambda functions. Errors include pages that could not be fetched, which Step Functions retries. ${info("lambda")}</p>
    <div class="board ops-scroll"><table class="ops-table fn"><thead><tr><th scope="col">Function</th><th scope="col" class="v">Runs</th><th scope="col" class="v">Errors</th><th scope="col" class="v">Throttled</th><th scope="col" class="v">p95</th><th scope="col" class="v">Most at once</th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

// ---------- the service map ----------
const ENTRY = { api: "Visitors, through CloudFront", "Reading workflow": "EventBridge Scheduler", matcher: "DynamoDB Streams",
  ingest: "Distributed Map children", "Clock workflow": "Drill and scale scripts" };
const COLS = [["entry", "Starts it"], ["workflow", "Workflows"], ["function", "Functions"], ["aws", "AWS services"], ["outside", "Outside AWS"]];

function mapModel(g) {
  const nodes = new Map();
  const edges = [];
  for (const n of g.nodes || []) if (n.group !== "client") nodes.set(n.id, { ...n, label: ROLE[n.id] || n.id });
  for (const e of g.edges || []) {
    if (e.from === "Callers") {
      const name = ENTRY[e.to] || "Other callers";
      const prev = nodes.get(name) || { id: name, label: name, group: "entry", requests: 0, avg_ms: null };
      prev.requests += e.requests;
      nodes.set(name, prev);
      edges.push({ ...e, from: name });
    } else edges.push(e);
  }
  return { nodes: [...nodes.values()], edges: edges.filter((e) => nodes.has(e.from) && nodes.has(e.to)) };
}

function drawMap(g) {
  const el = $("ops-map");
  const m = mapModel(g);
  if (!m.nodes.length) { el.innerHTML = `<p class="small">${g.error ? "The service map could not be read just now." : "No traces in the last 24 hours yet."}</p>`; return; }
  el.innerHTML = COLS.map(([grp, title]) => {
    const ns = m.nodes.filter((n) => n.group === grp).sort((a, b) => b.requests - a.requests);
    if (!ns.length) return "";
    return `<div class="ops-col"><h3>${esc(title)}</h3>${ns.map((n) => `<div class="ops-node${n.faults ? " fault" : ""}" data-node="${esc(n.id)}"><strong>${esc(n.label)}</strong><span class="num">${count(n.requests)} ${n.group === "entry" ? "starts" : "calls"}${n.avg_ms != null && n.group !== "entry" ? `, ${ms(n.avg_ms)}` : ""}${n.hosts > 1 && n.group === "outside" ? `, ${n.hosts} hosts` : ""}</span></div>`).join("")}</div>`;
  }).join("") + `<svg class="ops-lines" aria-hidden="true"></svg>`;
  el.dataset.edges = JSON.stringify(m.edges);
  lines();
}

function lines() {
  const el = $("ops-map");
  const svg = el.querySelector(".ops-lines");
  if (!svg || getComputedStyle(svg).display === "none") return;
  const box = el.getBoundingClientRect();
  const at = (id) => el.querySelector(`[data-node="${CSS.escape(id)}"]`)?.getBoundingClientRect();
  const edges = JSON.parse(el.dataset.edges || "[]");
  svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
  svg.innerHTML = edges.map((e) => {
    const a = at(e.from), b = at(e.to);
    if (!a || !b) return "";
    const fwd = b.left >= a.right;
    const x1 = (fwd ? a.right : a.left) - box.left, y1 = a.top + a.height / 2 - box.top;
    const x2 = (fwd ? b.left : b.right) - box.left, y2 = b.top + b.height / 2 - box.top;
    const dx = Math.max(24, Math.abs(x2 - x1) / 2) * (fwd ? 1 : -1);
    const wgt = Math.min(4, 1 + Math.log10(1 + e.requests));
    return `<path d="M${x1.toFixed(1)},${y1.toFixed(1)} C${(x1 + dx).toFixed(1)},${y1.toFixed(1)} ${(x2 - dx).toFixed(1)},${y2.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}" stroke-width="${wgt.toFixed(2)}"${e.faults ? ' class="fault"' : ""}></path>`;
  }).join("");
}
let t;
addEventListener("resize", () => { clearTimeout(t); t = setTimeout(lines, 120); });

// ---------- alarms ----------
const WATCHES = { "reads-stopped": "Scheduled reads starting every 30 minutes", "read-failed": "Scheduled reads finishing",
  "sources-failing": "No page failing three runs in a row", "matcher-failed": "New windows reaching saved trips",
  "matcher-lagging": "New windows matched within a minute", "clock-failed": "Every person's clock running",
  "api-errors": "The API answering without errors", "model-spend": "Claude spending under $1 an hour" };
const WORD = { OK: "OK", ALARM: "ALARM", INSUFFICIENT_DATA: "NO DATA YET" };

function alarmsHtml(alarms) {
  const order = Object.keys(WATCHES);
  const key = (a) => order.findIndex((k) => a.name.endsWith(k));
  return alarms.slice().sort((a, b) => key(a) - key(b)).map((a) => {
    const label = Object.entries(WATCHES).find(([k]) => a.name.endsWith(k))?.[1] || a.name;
    return `<li><span class="key-tile ${a.state === "ALARM" ? "status-closed" : "status-open"}">${esc(WORD[a.state] || a.state)}</span> ${esc(label)}<br><span class="small">${esc(a.about)}</span></li>`;
  }).join("");
}

// ---------- scale tests ----------
function scaleHtml(runs) {
  const last = (k) => runs.filter((r) => r.test === k).slice(-1)[0];
  const parts = [];
  const m = last("matcher");
  if (m) {
    const rows = m.windows.map((w) => `<tr><td>${esc(w.state)}, ${esc(w.day)}</td><td class="v num">${count(w.stops_read)}</td><td class="v num">${count(w.trips_read)}</td><td class="v num">${count(w.matches)}</td><td class="v num">${ms(w.ms)}</td></tr>`).join("");
    parts.push(`<div class="ops-group"><h3 class="mini-head">${count(m.saved_trips)} saved trips, one new window at a time</h3>
      <p>${count(m.saved_trips)} trips with ${count(m.saved_stops)} stops in all 50 states, DC and Puerto Rico were saved. Each new window, carried by the real stream to the real matcher, read only the stops saved in its own state on its own days, and read a trip only when a stop matched. Time is the matcher's own work; the window reached it a few seconds after it was stored. <span class="small">Run ${esc(new Date(m.at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }))}.</span></p>
      <div class="board ops-scroll"><table class="ops-table fn"><thead><tr><th scope="col">Window in</th><th scope="col" class="v">Stops read</th><th scope="col" class="v">Trips read</th><th scope="col" class="v">Matches</th><th scope="col" class="v">Time</th></tr></thead><tbody>${rows}</tbody></table></div></div>`);
  }
  const base = last("baseline");
  if (base) {
    const rows = base.windows.map((w) => `<tr><td>${esc(w.state)}, ${esc(w.day)}</td><td class="v num">${count(w.index.items_read)}</td><td class="v num">${count(w.index.read_units)}</td><td class="v num">${ms(w.index.ms)}</td><td class="v num">${count(w.every_trip.items_read)}</td><td class="v num">${count(w.every_trip.read_units)}</td><td class="v num">${ms(w.every_trip.ms)}</td><td class="v">${w.same_matches ? "yes" : "NO"}</td></tr>`).join("");
    parts.push(`<div class="ops-group"><h3 class="mini-head">The index against reading every trip</h3>
      <p>The same windows and the same ${count(base.saved_trips)} trips, two ways: the index, and the obvious design that reads every saved trip, which was Seventy-Two's own first version. Both found the same matches every time. The index read 3.5 to 4.5 units of DynamoDB capacity for an ordinary window; reading every trip took 675. Per million new windows, that is about $${esc(base.usd_per_million_windows.index)} of reads against $${esc(base.usd_per_million_windows.every_trip)}, and the gap grows with every trip saved. Timed from one laptop, so the times include the trip to AWS.</p>
      <div class="board ops-scroll"><table class="ops-table fn"><thead><tr><th scope="col">Window in</th><th scope="col" class="v">Index: items</th><th scope="col" class="v">Read units</th><th scope="col" class="v">Time</th><th scope="col" class="v">Every trip: items</th><th scope="col" class="v">Read units</th><th scope="col" class="v">Time</th><th scope="col" class="v">Same matches</th></tr></thead><tbody>${rows}</tbody></table></div></div>`);
  }
  const b = last("burst");
  if (b) parts.push(`<div class="ops-group"><h3 class="mini-head">${count(b.windows)} new windows at once</h3><p>All ${count(b.windows)} were stored together and matched against the same ${count(b.saved_trips)} trips in ${ms(b.drained_ms)} from the first write to the last match, in ${count(b.matcher_runs)} matcher runs. The slowest window waited ${ms(b.slowest_stored_to_matched_ms)} from being stored to being matched. Nothing was left unmatched.</p></div>`);
  const e = last("edge");
  if (e) parts.push(`<div class="ops-group"><h3 class="mini-head">${count(e.requests)} requests to the board</h3><p>${count(e.requests)} requests, ${count(e.concurrency)} at a time, from one laptop to <code>/api/exposures</code> through CloudFront: median ${ms(e.p50_ms)}, p95 ${ms(e.p95_ms)}, p99 ${ms(e.p99_ms)}, ${count(e.errors)} errors. Of the ${count(e.api_requests)} requests to the API, CloudFront answered ${count(e.api_cache_hits)} from its cache, so the API ran ${count(e.api_requests - e.api_cache_hits)} times.</p></div>`);
  const c = last("clocks");
  const race = last("race");
  const raced = race ? `<p>The race the clock is built for: ${count(race.clocks)} clocks, each told "I acted" at a random moment within a second either side of its deadline. ${count(race.ended_exactly_once)} of ${count(race.clocks)} ended exactly once (${count(race.acted)} acted, ${count(race.missed)} missed, none both or neither); the answer each press got, in time or too late, agreed with how its workflow ended ${count(race.answer_agrees_with_outcome)} of ${count(race.clocks)} times; and ${count(race.exactly_one_final_message)} of ${count(race.clocks)} sent exactly one final message.</p>` : "";
  const sc = last("scheduler");
  const alt = sc && sc.late_s ? `<p>The obvious alternative, measured the same way: ${count(sc.schedules)} one-time EventBridge Scheduler schedules, one per deadline, due at whole seconds over two minutes. All fired, none early, but a median of ${ms(sc.late_s.median * 1000)} and at most ${ms(sc.late_s.max * 1000)} late. Cancelling works: ${count(sc.cancelled - sc.cancelled_that_still_fired)} of ${count(sc.cancelled)} schedules deleted before they were due never fired.</p>` : "";
  if (c) parts.push(`<div class="ops-group"><h3 class="mini-head">${count(c.clocks)} clocks at once</h3><p>${count(c.clocks)} real-speed clocks were started together, each with an immune globulin window closing ${esc(c.minutes)} minutes later and nobody pressing anything. ${count(c.closed)} of ${count(c.clocks)} closed on their own: the earliest ${ms(c.slip_ms.min)} after its deadline, the median ${ms(c.slip_ms.p50)}, the latest ${ms(c.slip_ms.max)}. None closed early.</p>${raced}${alt}</div>`);
  return parts.join("") || `<p class="small">No scale run is recorded yet.</p>`;
}

// ---------- load ----------
(async () => {
  try {
    const d = await api("/api/ops");
    const s = d.series || {};
    const tiles = [
      count((s.sfn_ingest_started || {}).total),
      `${(d.alarms || []).filter((a) => a.state === "OK").length} of ${(d.alarms || []).length}`,
      ms((s.api_p50 || {}).total),
      usd((s.model_usd || {}).total),
    ];
    document.querySelectorAll("#ops-tally .tally-n").forEach((el, i) => { el.textContent = tiles[i]; });
    const asOf = new Date(d.as_of);
    $("ops-asof").innerHTML = `<span class="key-tile status-open">Live</span> Read from CloudWatch at ${esc(asOf.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }))}. CloudWatch is asked again at most every 10 minutes.`;
    $("ops-alarms").innerHTML = alarmsHtml(d.alarms || []);
    $("ops-groups").innerHTML = `<div class="ops-grid">${GROUPS.map((g) => groupHtml(g, s)).join("")}${functionsHtml(s, d.functions || {})}</div>`;
    drawMap(d.graph || {});
    hydrateInfo(document);
  } catch (e) {
    $("ops-asof").textContent = "The live numbers could not be read just now. Try again in a minute.";
  }
  try {
    const runs = await (await fetch("/data/scale.json", { cache: "no-cache" })).json();
    $("ops-scale").innerHTML = scaleHtml(runs);
  } catch {
    $("ops-scale").innerHTML = `<p class="small">No scale run is recorded yet.</p>`;
  }
  hydrateInfo(document);
})();
