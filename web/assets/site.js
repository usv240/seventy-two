// Seventy-Two shared script: theme, info buttons, API, board rows, calendar files.
document.documentElement.classList.remove("no-js");

// ---------- theme ----------
const THEME_KEY = "seventytwo-theme";
function storedTheme() { try { return localStorage.getItem(THEME_KEY); } catch { return null; } }
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  const btn = document.querySelector(".theme-toggle");
  if (btn) {
    const dark = t === "dark";   // light unless the reader chose dark
    btn.textContent = dark ? "Light" : "Dark";
    btn.setAttribute("aria-label", dark ? "Switch to the light theme" : "Switch to the dark theme");
  }
}
applyTheme(storedTheme());
document.addEventListener("click", (e) => {
  const t = e.target.closest(".theme-toggle");
  if (!t) return;
  const now = document.documentElement.dataset.theme || "light";
  const next = now === "dark" ? "light" : "dark";
  try { localStorage.setItem(THEME_KEY, next); } catch {}
  applyTheme(next);
});
document.addEventListener("click", (e) => {
  const m = e.target.closest(".menu-btn");
  if (!m) return;
  const nav = document.querySelector(".site-nav");
  const open = nav.classList.toggle("open");
  m.setAttribute("aria-expanded", String(open));
  m.textContent = open ? "Close" : "Menu";
});

// ---------- glossary for info buttons: what, why, source ----------
export const GLOSSARY = {
  "exposure-window": { title: "Exposure window", what: "The place and the hours when someone with measles was there, as published by a health department. It includes up to two hours after they left, because the virus stays in the air that long.", why: "If you were in that place during those hours and you are not protected, you could catch measles.", src: ["CDC: measles exposures", "https://www.cdc.gov/infection-control/media/pdfs/Measles-Exposure-Guide-HC-Settings-508.pdf"] },
  "mmr": { title: "MMR vaccine", what: "The measles, mumps and rubella vaccine. Two doses protect about 97 out of 100 people from measles.", why: "Given within 72 hours of an exposure, it can prevent measles or make it milder.", src: ["CDC: measles vaccine", "https://www.cdc.gov/measles/hcp/vaccine-considerations/index.html"] },
  "mmr-window": { title: "MMR window: 72 hours", what: "The 72 hours after your first exposure. The clock starts when you were exposed, not when the notice was posted.", why: "After 72 hours, the vaccine may no longer stop illness from this exposure. It still protects you from future ones.", src: ["CDC: post-exposure prophylaxis", "https://www.cdc.gov/measles/hcp/vaccine-considerations/index.html"] },
  "ig": { title: "Immune globulin", what: "A shot of antibodies taken from donated blood. It gives short-term protection.", why: "It is for people who cannot get MMR or who missed the 72 hours: babies under 12 months (babies 6 to 11 months can get MMR instead if it is within 72 hours), pregnant people who are not immune, and people with weak immune systems.", src: ["CDC: immune globulin for measles", "https://www.cdc.gov/measles/downloads/measles-immune-globulin-post-exposure-prophylaxis.pdf"] },
  "ig-window": { title: "Immune globulin window: 6 days", what: "The 6 days after your first exposure.", why: "After 6 days, immune globulin is no longer expected to prevent illness from this exposure.", src: ["CDC: immune globulin for measles", "https://www.cdc.gov/measles/downloads/measles-immune-globulin-post-exposure-prophylaxis.pdf"] },
  "watch": { title: "Symptom watch", what: "From day 5 after the exposure through day 21 (day 28 if you got immune globulin). This is when measles symptoms can start.", why: "If you get a fever, cough, runny nose, red eyes or a rash, call your doctor before you go in, so they can keep others from catching it.", src: ["CDC: measles exposures", "https://www.cdc.gov/infection-control/media/pdfs/Measles-Exposure-Guide-HC-Settings-508.pdf"] },
  "protected": { title: "Am I protected?", what: "You are usually protected if you have written proof of two MMR doses, a blood test showing immunity, a past lab-confirmed case, or you were born before 1957.", why: "If you are protected, you do not need the vaccine after an exposure. Still watch for symptoms.", src: ["CDC: evidence of immunity", "https://www.cdc.gov/measles/hcp/vaccine-considerations/index.html"] },
  "lag": { title: "Notice lag", what: "Days between the exposure and the day the health department published the notice. We measure it from dated press releases only, because list pages only show when they were last updated.", why: "Every day of lag is a day of the 72-hour window already gone before anyone can read the notice.", src: ["How we measure it", "/learn/#lag"] },
  "replay": { title: "Replay a past date", what: "Checks your trip as if today were the date you choose, using only the notices that had been published by then.", why: "It lets you try a real 2026 exposure from start to finish and see what someone would have seen that day.", src: ["How the check works", "/learn/#how"] },
  "demo-clock": { title: "Demo clock", what: "A clock that runs fast: one hour of real time passes in one second.", why: "So you can watch a whole 21-day clock run in a few minutes. Real clocks run in real time.", src: ["How the clock works", "/learn/#clock"] },
  "time-unclear": { title: "Time not given", what: "The notice names the place and the day but not the hours.", why: "We never guess a time. We treat the whole day as the window and show the health department's phone number.", src: ["How we read notices", "/learn/#reading"] },
  "match": { title: "How a match is decided", what: "Code compares the hours you gave with the notice's hours, and the place you named with the place in the notice. Airport codes like PHL count as the airport's name.", why: "The AI only reads text. Whether you overlapped, and your deadlines, are decided by code using CDC's rules.", src: ["How the check works", "/learn/#how"] },
  "sources": { title: "Health departments we read", what: "Pages from state and county health departments that list measles exposure sites, read every 30 minutes.", why: "Notices are spread across many sites. We bring them into one place so you can check them all at once.", src: ["Live source status", "/board/#status"] },
  "discovery": { title: "New notices found on news pages", what: "Some of the pages we read are a health department's list of press releases. When a new measles release appears there, we read it on the next run, up to three a run, newest first.", why: "A new notice usually goes up at a brand-new web address. Watching the list is how it reaches the board within about 30 minutes, without anyone adding it by hand.", src: ["Live source status", "/board/#status"] },
  "quote": { title: "The quote", what: "The exact words from the notice that this exposure window was taken from.", why: "Every window must quote the notice word for word, with its date and times written there, or we drop it. You can always check us against the original.", src: ["How we read notices", "/learn/#reading"] },
  "reader": { title: "The reader", what: "Claude Haiku on Amazon Bedrock reads each notice and proposes exposure windows, each with a quote.", why: "Code then checks every proposal: the quote must appear in the notice word for word, its date must be written in or just above it, its place must be named nearby, and each time must be inside the quote. Anything that fails is dropped and counted.", src: ["How we read notices", "/learn/#reading"] },
  "clock": { title: "Your clock", what: "One AWS Step Functions workflow just for you. Each deadline is a step that waits until its moment.", why: "It reminds you before each deadline, waits for you to say you acted, and ends on day 21. While it waits, it costs nothing.", src: ["How the clock works", "/learn/#clock"] },
  "ics": { title: "Calendar file", what: "A small .ics file your phone or computer calendar can open. It adds each deadline as an event with a reminder.", why: "Your calendar will remind you even if you never come back to this site.", src: ["What a calendar file is", "https://en.wikipedia.org/wiki/ICalendar"] },
  "lab": { title: "The Lab", what: "Paste any measles notice, or a link to one, and watch it being read live.", why: "So you can test Seventy-Two on notices we do not watch yet. Nothing you paste is shown to anyone else, and it is deleted after one day.", src: ["How we read notices", "/learn/#reading"] },
  "serverless": { title: "Serverless", what: "Software that runs only when something happens, on services AWS manages. There are no servers to patch or keep running.", why: "Seventy-Two costs close to nothing when nobody is using it, and keeps running with nobody looking after it.", src: ["How it is built", "/learn/#built"] },
  "step-functions": { title: "AWS Step Functions", what: "A service that runs workflows: a list of steps with waits, choices and retries.", why: "Each person's clock is its own workflow. Each reading run is a workflow that reads every source in parallel.", src: ["AWS Step Functions", "https://aws.amazon.com/step-functions/"] },
  "lambda": { title: "AWS Lambda", what: "Runs small pieces of code only when needed: fetching a page, reading it, answering a check.", why: "No server sits idle. Each call is billed by the millisecond.", src: ["AWS Lambda", "https://aws.amazon.com/lambda/"] },
  "dynamodb": { title: "Amazon DynamoDB", what: "The database that holds exposure windows, saved trips and clocks.", why: "Each saved trip and clock has a delete date. DynamoDB removes it on its own when it is no longer needed. That is our privacy policy, written as data.", src: ["DynamoDB time to live", "https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/TTL.html"] },
  "s3": { title: "Amazon S3", what: "Stores this website, a copy of every notice we read, and a backup of the board.", why: "If the live service ever fails, the site serves the last saved board from S3 instead.", src: ["Amazon S3", "https://aws.amazon.com/s3/"] },
  "bedrock": { title: "Amazon Bedrock", what: "Runs the Claude models that read notices and your trip description.", why: "We use Claude Haiku 4.5, a small, fast model, so each notice costs well under a cent.", src: ["Amazon Bedrock", "https://aws.amazon.com/bedrock/"] },
  "scheduler": { title: "Amazon EventBridge Scheduler", what: "Starts the reading workflow every 30 minutes.", why: "Nobody has to press a button. New notices appear on the board within about half an hour.", src: ["EventBridge Scheduler", "https://aws.amazon.com/eventbridge/scheduler/"] },
  "api-gateway": { title: "Amazon API Gateway", what: "The front door for the site's live requests: checks, clocks and the Lab.", why: "It limits how many requests anyone can send, so the service stays up for everyone.", src: ["Amazon API Gateway", "https://aws.amazon.com/api-gateway/"] },
  "sam": { title: "AWS SAM and CloudFormation", what: "One template file describes every AWS resource Seventy-Two uses. CloudFormation creates or updates them all together.", why: "Deploying is one command from a laptop, and anyone can deploy the same stack to their own account.", src: ["AWS SAM", "https://aws.amazon.com/serverless/sam/"] },
  "xray": { title: "AWS X-Ray", what: "Records each function run as a trace, with a part for every DynamoDB, S3 and SSM call, every page fetched, every Claude call and every push. Its service map, kept to this stack, is drawn on the Ops page.", why: "When a source is slow or a read fails, the trace shows exactly which call took the time. Traces carry the source, state and window as searchable annotations, never what a visitor typed.", src: ["AWS X-Ray service map", "https://docs.aws.amazon.com/xray/latest/devguide/xray-console-servicemap.html"] },
  "hepa-window": { title: "Hepatitis A: 2 weeks", what: "After an exposure to hepatitis A, the vaccine (or immune globulin, for babies under 12 months and people who cannot have the vaccine) can prevent the illness if given within 2 weeks.", why: "Symptoms start 15 to 50 days later, so the watch is longer than for measles.", src: ["CDC: hepatitis A clinical overview", "https://www.cdc.gov/hepatitis-a/hcp/clinical-overview/index.html"] },
  "two-diseases": { title: "Two diseases, one stack", what: "Everything that is about measles lives in one domain file: the reader's instructions, the actions and their windows, the watch period and the words. Hepatitis A is a second file.", why: "Choosing hepatitis A here sends the same reader, the same checks and the same clock workflow its rules instead. Nothing else on the server changes, which is what makes the pattern reusable for another disease, or any notice that starts a deadline.", src: ["src/domains/", "/learn/#reuse"] },
  "emf": { title: "Metrics from log lines", what: "Each function prints one JSON line in CloudWatch's Embedded Metric Format, and CloudWatch turns it into metrics: pages read, Claude's cost and latency, how many saved stops one window read, how late a clock closed.", why: "No metrics API call, no agent, and no extra time on the request. The same line stays in the log, so a metric can always be traced back to the run that made it.", src: ["CloudWatch Embedded Metric Format", "https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html"] },
  "p95": { title: "Percentiles", what: "The p95 is the time that 95 out of 100 requests beat; the median is the one half of them beat. p99 is 99 out of 100.", why: "An average hides the slow ones. The slow ones are what a person waiting on a check notices.", src: ["CloudWatch statistics", "https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Statistics-definitions.html"] },
  "iterator-age": { title: "Stream lag", what: "How old the newest record was when the matcher received it from DynamoDB Streams. Lambda reports it as IteratorAge.", why: "If the matcher fell behind, new windows would wait to reach saved trips. An alarm fires if the lag passes a minute twice in a row.", src: ["Lambda stream metrics", "https://docs.aws.amazon.com/lambda/latest/dg/monitoring-metrics-types.html"] },
  "deadline-slip": { title: "How late a window closed", what: "When nobody presses I got it, the clock closes the window itself when the task token times out. This is how long after the deadline that happened.", why: "The waits are rounded up, so a window never closes early. Late by a second is the price of that.", src: ["Task tokens and timeouts", "https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html"] },
  "trip-index": { title: "Saved stops, indexed by state and day", what: "Each saved stop is filed under its state and each day it covers. A new window reads only the stops filed under its own state and days, and reads a trip only when one of them matches.", why: "The work for one new window grows with the stops saved in that state on those days, not with every trip anyone saved.", src: ["How trip watching works", "/learn/#watching"] },
  "scale-test": { title: "Scale tests", what: "scripts/scale.py saves thousands of test trips, sends new windows through the real stream, sends thousands of requests to the board through CloudFront, and starts many clocks at once, then deletes everything it made.", why: "A claim about scale should be a number from a run, on this stack, not a guess.", src: ["The scale results", "/ops/#scale"] },
  "drills": { title: "Drills", what: "Some 5xx answers are on purpose: the failover drill makes the API answer 503 so CloudFront must serve the saved copy of the board.", why: "A failover that has never been tried is a hope. The drill log is in docs/drills.json.", src: ["How it is built", "/learn/#built"] },
  "cloudwatch": { title: "Amazon CloudWatch", what: "Keeps the log of every function run, turns log lines into metrics (pages read and failed, Claude's cost and latency, matching work, clock accuracy), runs eight alarms on them, and holds one dashboard.", why: "Each failed source is logged with its error, counted, and shown on the board's status panel and the Ops page with the alarms' current state.", src: ["CloudWatch embedded metric format", "https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format.html"] },
  "iam": { title: "AWS IAM", what: "Decides what each part of Seventy-Two is allowed to do.", why: "Each function gets only the permissions it needs, so a mistake in one part cannot reach the rest.", src: ["AWS IAM", "https://aws.amazon.com/iam/"] },
  "ses": { title: "Amazon SES", what: "Sends email. Seventy-Two can email your clock's reminders.", why: "It stays off until a sender address is verified. Your clock page and calendar file remind you without it.", src: ["Amazon SES", "https://aws.amazon.com/ses/"] },
  "sdk": { title: "Anthropic Python SDK", what: "The library that calls Claude. Its Bedrock client sends the calls through Amazon Bedrock in this AWS account.", why: "Forcing a tool call makes Claude answer as structured data, which code can then check field by field.", src: ["Anthropic SDK for Python", "https://github.com/anthropics/anthropic-sdk-python"] },
  "trip-watch": { title: "Watch my trip", what: "Seventy-Two keeps your places and times, and checks them against every new exposure window the moment it is stored, until 21 days after your last stop.", why: "Notices usually arrive days after an exposure, when the vaccine window may already be closing. You should not have to remember to come back and check.", src: ["How trip watching works", "/learn/#watching"] },
  "streams": { title: "DynamoDB Streams", what: "A change feed from the database. Each new exposure window starts a small function that compares it with the saved trips that have a stop in the same state on the same days.", why: "No code of ours polls and nothing runs on a timer: the match happens the moment the window is written. Re-reading a notice never re-sends it, because its id stays the same.", src: ["DynamoDB Streams and Lambda", "https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Streams.Lambda.html"] },
  "web-push": { title: "Alerts on this device", what: "A notification from this site on your phone or computer, sent through your browser's own push service. No account, no email, no app.", why: "The message is encrypted so that only your browser can read it, and only this site can send to it. It works on Android, Windows and Mac browsers; on iPhone, add the site to your Home Screen first.", src: ["Web Push (RFC 8291)", "https://www.rfc-editor.org/rfc/rfc8291"] },
  "calendar-feed": { title: "Calendar feed", what: "An address your calendar app subscribes to. It fetches it again on its own, so new events appear without you doing anything.", why: "When a notice matches your trip, an event with what to do appears in your calendar with an alert, plus the deadlines that are still open.", src: ["What a calendar feed is", "https://en.wikipedia.org/wiki/ICalendar"] },
  "alarms": { title: "Alarms", what: "Eight Amazon CloudWatch alarms watch for a page failing three runs in a row, a read failing, reads stopping, a clock failing, new windows that could not be matched to saved trips or waited more than a minute, the API answering with errors, and Claude spending more than $1 in an hour.", why: "If something breaks, it shows here, in the same words CloudWatch uses, and can email whoever runs the stack.", src: ["CloudWatch alarms", "https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html"] },
  "cloudfront": { title: "Amazon CloudFront", what: "Delivers the site quickly worldwide over HTTPS.", why: "If the live board request fails, CloudFront switches to the saved copy in S3 automatically.", src: ["CloudFront origin failover", "https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/high_availability_origin_failover.html"] },
};

// ---------- popovers ----------
let openPop = null, lastBtn = null, recentlyOpen = 0;
function closePop(returnFocus = true) {
  if (!openPop) return;
  const p = openPop; openPop = null;
  p.classList.remove("on");
  setTimeout(() => p.remove(), 200);
  if (returnFocus && lastBtn) lastBtn.focus();
  if (lastBtn) lastBtn.setAttribute("aria-expanded", "false");
}
function showPop(btn) {
  const g = GLOSSARY[btn.dataset.info];
  if (!g) return;
  const instant = Date.now() - recentlyOpen < 600;
  closePop(false);
  const p = document.createElement("div");
  p.className = "pop" + (instant ? " instant" : "");
  p.setAttribute("role", "dialog");
  p.setAttribute("aria-label", g.title);
  const srcIsLocal = g.src[1].startsWith("/");
  p.innerHTML = `<button class="close" type="button" aria-label="Close">Close</button><h4></h4><p class="what"></p><p class="why"></p><p class="src"><a></a></p>`;
  p.querySelector("h4").textContent = g.title;
  p.querySelector(".what").textContent = g.what;
  p.querySelector(".why").textContent = g.why;
  const a = p.querySelector(".src a");
  a.textContent = (srcIsLocal ? "" : "Source: ") + g.src[0];
  a.href = g.src[1];
  if (!srcIsLocal) { a.target = "_blank"; a.rel = "noopener"; }
  document.body.appendChild(p);
  const r = btn.getBoundingClientRect();
  const w = p.offsetWidth, h = p.offsetHeight;
  let left = Math.min(Math.max(16, r.left), innerWidth - w - 16);
  let top = r.bottom + 10;
  let origin = "top left";
  if (top + h > innerHeight - 12) { top = Math.max(12, r.top - h - 10); origin = "bottom left"; }
  p.style.left = left + "px"; p.style.top = top + "px"; p.style.setProperty("--origin", origin);
  requestAnimationFrame(() => p.classList.add("on"));
  openPop = p; lastBtn = btn; recentlyOpen = Date.now();
  btn.setAttribute("aria-expanded", "true");
  p.querySelector(".close").addEventListener("click", () => closePop());
  p.querySelector(".close").focus({ preventScroll: true });
}
document.addEventListener("click", (e) => {
  const b = e.target.closest(".info");
  if (b) { e.preventDefault(); openPop && lastBtn === b ? closePop() : showPop(b); return; }
  if (openPop && !e.target.closest(".pop")) closePop(false);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
addEventListener("scroll", () => { if (openPop && innerWidth > 640) closePop(false); }, { passive: true });

export function info(key, label) {
  const g = GLOSSARY[key];
  return `<button type="button" class="info" data-info="${key}" aria-label="About ${(label || (g && g.title) || key).replace(/"/g, "")}" aria-expanded="false">i</button>`;
}
export function hydrateInfo(root = document) {
  root.querySelectorAll("[data-info-for]").forEach((el) => {
    if (el.nextElementSibling && el.nextElementSibling.classList.contains("info")) return;
    el.insertAdjacentHTML("afterend", info(el.dataset.infoFor, el.textContent.trim()));
  });
}

// ---------- API ----------
export async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.body ? "POST" : "GET",
    headers: opts.body ? { "content-type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch {}
  if (!res.ok) throw new Error(data.error || `The request failed (${res.status}). Please try again.`);
  return data;
}

// ---------- formatting ----------
export function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
export function fmt(iso, tz, opts = {}) {
  const d = new Date(iso);
  return d.toLocaleString("en-US", { timeZone: tz || undefined, weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short", ...opts });
}
export function fmtDay(iso, tz) { return new Date(iso).toLocaleDateString("en-US", { timeZone: tz || undefined, weekday: "long", month: "long", day: "numeric" }); }
export function hoursText(h) {
  if (h <= 0) return "Closed";
  if (h < 48) {
    const m = Math.round(h * 60), hh = Math.floor(m / 60), mm = m % 60;
    if (!hh) return `${mm} min left`;
    return mm ? `${hh} h ${mm} min left` : `${hh} h left`;
  }
  const t = Math.round(h), d = Math.floor(t / 24), r = t % 24;
  return r ? `${d} days ${r} h left` : `${d} days left`;
}
// The disease's actions (deadlines) come from the domain file on the server, written into the page at build time.
export const DOMAIN = window.SEVENTY_TWO || { actions: [
  { kind: "mmr", label: "the MMR vaccine", box: "MMR vaccine", info: "mmr-window", within_hours: 72, phase_word: "MMR WINDOW OPEN" },
  { kind: "ig", label: "immune globulin", box: "Immune globulin", info: "ig-window", within_hours: 144, phase_word: "IMMUNE GLOBULIN OPEN" }],
  watch: { start_days: 5, end_days: 21 } };
export const PHASE = {
  ...Object.fromEntries(DOMAIN.actions.map((a, i) => [`${a.kind}_open`, { word: a.phase_word, cls: i === 0 ? "status-open" : "status-watch" }])),
  watch: { word: "WATCH FOR SYMPTOMS", cls: "status-watch" },
  clear: { word: "WATCH ENDED", cls: "" },
};

// ---------- board rows and the flip ----------
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
export function flipText(el, text) {
  text = String(text).toUpperCase();
  if (reduce) { el.textContent = text; return; }
  el.classList.add("flipping");
  el.innerHTML = "";
  // letters are grouped by word so a line only ever breaks at a space, never inside a word
  const spans = [];
  text.split(" ").forEach((word, wi) => {
    if (wi) el.appendChild(document.createTextNode(" "));
    const w = document.createElement("span");
    w.style.whiteSpace = "nowrap";
    for (const ch of word) {
      const s = document.createElement("span");
      s.className = "flap-char";
      s.textContent = GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
      w.appendChild(s);
      spans.push([s, ch]);
    }
    el.appendChild(w);
  });
  // when every letter has landed, go back to plain text
  const token = String(Math.random());
  el.dataset.flip = token;
  setTimeout(() => { if (el.dataset.flip === token) { el.textContent = text; el.classList.remove("flipping"); } }, spans.length * 18 + 5 * 60 + 250);
  spans.forEach(([s, ch], i) => {
    let n = 0;
    const steps = 2 + Math.floor(Math.random() * 3);
    const tick = () => {
      if (n++ >= steps) { s.textContent = ch; s.style.animation = "none"; void s.offsetWidth; s.style.animation = ""; return; }
      s.textContent = GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
      setTimeout(tick, 60);
    };
    setTimeout(tick, i * 18);
  });
}
export function windowStatus(w, now = Date.now()) {
  const start = new Date(w.start_utc).getTime();
  for (const [i, a] of DOMAIN.actions.entries()) {
    if (start + a.within_hours * 3600e3 > now) return [i === 0 ? "VACCINE WINDOW OPEN" : a.phase_word, i === 0 ? "status-open" : "status-watch"];
  }
  if (new Date(w.end_utc).getTime() + DOMAIN.watch.end_days * 86400e3 > now) return ["WATCH FOR SYMPTOMS", "status-watch"];
  return ["ENDED", ""];
}
export function shortPlace(p) {
  return p.replace(/\s*\(.*?\)/g, "").replace(/International Airport/i, "Airport").replace(/\s+/g, " ").trim();
}

// ---------- calendar file (.ics), made in the browser ----------
export function icsFor(match, place, phone, actions = DOMAIN.actions, disease = DOMAIN.disease) {
  const d = match.deadlines;
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const t = (iso) => new Date(iso).toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const call = phone ? ` Health department: ${phone}.` : "";
  const ev = (iso, title, desc) => [
    "BEGIN:VEVENT", `UID:${t(iso)}-${Math.random().toString(36).slice(2)}@seventytwo`, `DTSTAMP:${stamp}`,
    `DTSTART:${t(iso)}`, `DTEND:${t(new Date(new Date(iso).getTime() + 30 * 60000).toISOString())}`,
    `SUMMARY:${title}`, `DESCRIPTION:${desc}${call} Not medical advice.`,
    "BEGIN:VALARM", "TRIGGER:-PT12H", "ACTION:DISPLAY", `DESCRIPTION:${title}`, "END:VALARM", "END:VEVENT"].join("\r\n");
  const events = [];
  const now = Date.now();
  for (const a of actions) {
    const iso = d[`${a.kind}_deadline`];
    if (iso && new Date(iso) > now) events.push(ev(iso, `${a.window_title || a.box} (${place})`, a.calendar_note || ""));
  }
  if (new Date(d.watch_start) > now) events.push(ev(d.watch_start, `Start watching for symptoms (${place})`, "If symptoms start, call your doctor before you go in."));
  events.push(ev(d.watch_end, `Watch for symptoms ends (${place})`, "If you have had no symptoms, no action is needed."));
  return ["BEGIN:VCALENDAR", "VERSION:2.0", `PRODID:-//Seventy-Two//${disease || "exposure"} clock//EN`, "CALSCALE:GREGORIAN", ...events, "END:VCALENDAR"].join("\r\n");
}
export function download(name, text, type = "text/calendar") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------- entrances ----------
const io = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  entries.forEach((en) => { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
}, { rootMargin: "0px 0px -8% 0px" }) : null;
export function reveal(root = document) {
  root.querySelectorAll(".reveal").forEach((el, i) => {
    el.style.transitionDelay = `${(Number(el.dataset.d) || 0) * 70}ms`;
    io ? io.observe(el) : el.classList.add("in");
  });
}
reveal();
hydrateInfo();
