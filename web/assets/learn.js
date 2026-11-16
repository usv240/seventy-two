import { api, esc, GLOSSARY } from "/assets/site.js";

const order = ["exposure-window", "mmr", "mmr-window", "ig", "ig-window", "watch", "protected", "lag", "time-unclear", "quote", "reader", "match", "clock", "replay", "demo-clock", "ics", "lab", "sources", "discovery","serverless", "step-functions", "lambda", "bedrock", "dynamodb", "s3", "scheduler", "api-gateway", "cloudfront", "sam", "xray", "cloudwatch", "iam", "ses", "sdk"];
document.getElementById("glossary-list").innerHTML = order.filter((k) => GLOSSARY[k]).map((k) => {
  const g = GLOSSARY[k];
  return `<dt id="g-${k}">${esc(g.title)}</dt><dd>${esc(g.what)} ${esc(g.why)} <a href="${esc(g.src[1])}">${esc(g.src[0])}</a></dd>`;
}).join("");

(async () => {
  try {
    const s = await api("/api/status");
    if (s.lag_days_median == null) return;
    document.getElementById("lag-figures").innerHTML = `
      <p><span class="mid-stat num">${s.lag_days_median} days</span>median time from exposure to published notice, across ${s.with_publication_date} exposures</p>
      <p><span class="mid-stat num">${s.mmr_closed_before_publication} of ${s.with_publication_date}</span>notices went up after the 72-hour vaccine window had closed</p>
      <p><span class="mid-stat num">${s.ig_closed_before_publication} of ${s.with_publication_date}</span>went up after the 6-day immune globulin window had closed</p>`;
  } catch {}
})();
