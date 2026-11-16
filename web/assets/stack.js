import { api, esc } from "/assets/site.js";

// one live line: when the scheduled read last ran, and how it went
(async () => {
  const el = document.getElementById("live-run");
  try {
    const s = await api("/api/status");
    const tries = (s.sources || []).map((x) => Date.parse(x.last_try)).filter(Boolean);
    if (!tries.length) return;
    const mins = Math.max(0, Math.round((Date.now() - Math.max(...tries)) / 60000));
    const ago = mins < 1 ? "less than a minute ago" : mins === 1 ? "1 minute ago" : mins < 120 ? `${mins} minutes ago` : `${Math.round(mins / 60)} hours ago`;
    el.innerHTML = `<span class="key-tile status-open">Live</span> The last read ran ${esc(ago)}. ${esc(s.sources_ok)} of ${esc(s.sources_watched)} pages were read without errors, ${esc(s.notices_found)} new notices have been found on their news pages, and ${esc(s.windows_active)} exposure windows are still being watched. <a href="/board/#status">See every source</a>.`;
  } catch {}
})();
