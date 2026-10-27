// VisionVoice dashboard. All decisions are made by the backend; this page only shows them.
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const time = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const state = { view: "annotated", selected: null, captures: [], last: null };

const STATE_TEXT = {
  MATCH: (c) => `Page ${c.expected} verified`,
  MISMATCH: (c) => `Wrong page: found ${c.detected}, expected ${c.expected}`,
  UNCERTAIN: () => "Page number not verified",
};
const PAGE_STATUS = {
  DETECTED: "found", MISSING: "no page number found", AMBIGUOUS: "more than one page number",
  UNREADABLE: "page number unclear",
};
const SOURCE = { voice: "Spoken", tap: "Button tap", typed: "Typed", dashboard: "Dashboard", upload: "Uploaded photo" };

// ---------- devices ----------
function renderDevices(d) {
  const set = (key, cls, val) => {
    const li = document.querySelector(`[data-dev="${key}"]`);
    li.querySelector(".dot").className = "dot " + cls;
    li.querySelector(".val").textContent = val;
  };
  const cam = d.cam || {};
  if (cam.state === "online") set("cam", "ok", cam.rssi ? `online, ${cam.rssi} dBm` : "online");
  else if (cam.state === "file") set("cam", "warn", "using a file");
  else if (cam.state === "camera error") set("cam", "bad", "sensor error");
  else set("cam", cam.state === "offline" ? "bad" : "warn", cam.state || "not found");
  const aim = $("aim");
  if (cam.url) { aim.href = cam.url + "/"; aim.hidden = false; } else aim.hidden = true;

  const mic = d.mic || {};
  if (mic.state === "online") set("mic", "ok", mic.streaming ? "listening" : (mic.rssi ? `online, ${mic.rssi} dBm` : "online"));
  else set("mic", mic.state === "reconnecting" ? "bad" : "warn", mic.state || "not found");
  set("stt", d.stt === "ready" ? "ok" : d.stt === "loading" ? "warn" : "bad", d.stt);
  set("ocr", d.ocr && d.ocr.startsWith("Tesseract") ? "ok" : "bad", d.ocr === "unknown" ? "…" : d.ocr);
}

// ---------- session strip ----------
function renderSession(s) {
  const strip = $("strip");
  if (!s) {
    strip.innerHTML = "";
    $("session-line").textContent = "No pages requested yet. Say or type “read page 12” or “read pages 12 to 14”.";
    return;
  }
  const done = new Set(s.verified);
  strip.innerHTML = s.pages.map((p) => {
    const cls = done.has(p) ? "done" : p === s.expected ? "next" : "";
    const st = done.has(p) ? "read" : p === s.expected ? "next" : "";
    return `<li class="${cls}" aria-label="Page ${p}${st ? ", " + st : ""}"><span class="num">${p}</span><span class="st">${st}</span></li>`;
  }).join("");
  $("session-line").textContent = s.status === "ACTIVE"
    ? `${s.describe[0].toUpperCase() + s.describe.slice(1)}: waiting for page ${s.expected}. ${s.verified.length} of ${s.pages.length} read.`
    : s.status === "COMPLETED" ? `Finished ${s.describe}.` : "Reading cancelled.";
}

// ---------- latest capture ----------
function showImage(id) {
  const img = $("photo");
  if (!id) { img.hidden = true; $("photo-empty").hidden = false; return; }
  img.src = `/captures/${encodeURIComponent(id)}/${state.view}.jpg`;
  img.alt = state.view === "annotated"
    ? "Photo with the header and footer search areas shaded and the page numbers found boxed"
    : "Photo of the page as taken";
  img.hidden = false;
  $("photo-empty").hidden = true;
  document.querySelectorAll("#gallery button").forEach((b) => b.classList.toggle("sel", b.dataset.id === id));
}

function renderCapture(c, fresh) {
  if (!c) return;
  state.last = c;
  state.selected = c.id;
  showImage(c.id);
  const v = $("verdict");
  v.className = "verdict " + c.decision + (fresh ? " fresh" : "");
  $("v-state").textContent = STATE_TEXT[c.decision](c);
  $("v-message").textContent = c.message;
  const facts = [
    ["Expected page", c.expected ?? "—"],
    ["Page number", c.detected != null ? `${c.detected} (${PAGE_STATUS[c.page_status]})` : PAGE_STATUS[c.page_status]],
    ["Where on the page", c.region ? c.region.replace("_", " ") : "—"],
    ["Confidence", c.confidence ? Math.round(c.confidence * 100) + "%" : "—"],
    ["Words read", `${c.words} (OCR confidence ${c.ocr_conf}%)`],
    ["Time", `photo ${c.photo_s}s, reading ${(c.ocr_ms / 1000).toFixed(1)}s`],
    ["Taken by", `${SOURCE[c.source] || c.source}, ${c.resolution}`],
  ];
  $("v-facts").innerHTML = facts.map(([k, val]) => `<dt>${esc(k)}</dt><dd>${esc(val)}</dd>`).join("");
  $("v-text-wrap").hidden = !c.text;
  $("v-text").textContent = c.text || "";
  $("v-cands").innerHTML = c.candidates.length
    ? `<div class="cands"><p>Numbers found in the header and footer</p><ul>${c.candidates.map((x) =>
        `<li class="${x.chosen ? "chosen" : ""}">${x.value} <span class="muted">${esc(x.region.replace("_", " "))}, ${Math.round(x.conf * 100)}%</span></li>`).join("")}</ul></div>`
    : "";
  $("photo-meta").textContent = `${time(c.ts)}${c.warnings.length ? ". " + c.warnings.join("; ") : ""}`;
  if (fresh) setTimeout(() => v.classList.remove("fresh"), 600);
}

function renderGallery(list) {
  state.captures = list || [];
  $("gallery").innerHTML = state.captures.map((c) =>
    `<button type="button" class="${c.decision}" data-id="${esc(c.id)}" aria-label="Photo: expected ${c.expected}, ${c.decision.toLowerCase()}">
       <img src="/captures/${encodeURIComponent(c.id)}/photo.jpg" alt=""></button>`).join("");
  document.querySelectorAll("#gallery button").forEach((b) => b.classList.toggle("sel", b.dataset.id === state.selected));
}

// ---------- conversation ----------
function feedItem(ev) {
  if (ev.kind === "heard") {
    const who = SOURCE[ev.source] || "You";
    return `<li class="heard"><span class="who">${esc(who)} at ${time(ev.ts)}</span>${esc(ev.text)}</li>`;
  }
  if (ev.kind === "speech") return `<li class="speech"><span class="who">VisionVoice said at ${time(ev.ts)}</span>${esc(ev.text)}</li>`;
  return `<li class="info ${esc(ev.level)}">${esc(ev.text)}</li>`;
}
function renderFeed(items) {
  const feed = $("feed");
  feed.innerHTML = items.length ? items.map(feedItem).join("") : `<li class="empty-feed">Spoken and typed commands, and everything VisionVoice says, appear here.</li>`;
  feed.scrollTop = feed.scrollHeight;
}
function appendFeed(ev) {
  const feed = $("feed");
  feed.querySelector(".empty-feed")?.remove();
  feed.insertAdjacentHTML("beforeend", feedItem(ev));
  while (feed.children.length > 80) feed.firstElementChild.remove();
  feed.scrollTop = feed.scrollHeight;
}

// ---------- summary ----------
const SUM_STATUS = { ANSWERED: "Found", NOT_FOUND: "Not found on these pages", INSUFFICIENT_EVIDENCE: "Not enough detail yet" };
function renderSummary(s) {
  if (!s) return;
  const ev = s.evidence.length
    ? `<ul class="evidence">${s.evidence.map((e) =>
        `<li><span class="pg">p. ${esc(e.page)}</span><span>${esc(e.sentence)}${e.kind !== "direct" ? ` <span class="kind">(${e.kind === "pronoun" ? "follows a mention" : "partial match"})</span>` : ""}</span></li>`).join("")}</ul>`
    : "";
  $("sum-body").innerHTML = `
    <p class="sum-status ${s.status}">${esc(s.target)}: ${esc(SUM_STATUS[s.status] || s.status)}</p>
    <p class="sum-answer">${esc(s.answer)}</p>${ev}
    <p class="sum-meta">Used pages ${esc(s.pages.join(", "))}, ${s.provider === "extractive" ? "sentences quoted from the pages" : "AI summary checked against the pages"}${s.warnings.length ? ". " + esc(s.warnings.join("; ")) : ""}</p>`;
}

// ---------- history ----------
const KIND = { capture: "Page check", summary: "Summary", command: "Command" };
async function loadHistory() {
  const rows = await (await fetch("/api/history?limit=40")).json();
  $("hist-body").innerHTML = rows.length ? rows.map((r) => `<tr>
      <td>${time(r.ts)}</td><td>${KIND[r.kind] || r.kind}</td><td>${esc(SOURCE[r.source] || r.source || "")}</td>
      <td class="detail">${esc(r.detail || (r.page_status && r.page_status !== "DETECTED" ? PAGE_STATUS[r.page_status] : ""))}</td>
      <td>${r.expected_page ?? ""}</td><td>${r.detected_page ?? ""}</td>
      <td>${r.decision ? `<span class="pill ${r.kind === "command" ? "cmd" : esc(r.decision)}">${esc(r.decision.replaceAll("_", " ").toLowerCase())}</span>` : ""}</td>
      <td>${r.ocr_ms ? (r.ocr_ms / 1000).toFixed(1) + "s" : ""}</td></tr>`).join("")
    : `<tr><td colspan="8" class="muted">Nothing yet.</td></tr>`;
}
function renderStats(s) {
  if (!s) return;
  $("tally").innerHTML = `<span><b>${s.captures}</b> page ${s.captures === 1 ? "check" : "checks"}</span>
    <span class="m"><b>${s.match}</b> verified</span><span class="x"><b>${s.mismatch}</b> wrong page</span>
    <span class="u"><b>${s.uncertain}</b> not verified</span><span><b>${s.summaries}</b> ${s.summaries === 1 ? "summary" : "summaries"}</span>
    ${s.avg_ocr_ms ? `<span>average reading time <b>${(s.avg_ocr_ms / 1000).toFixed(1)}s</b></span>` : ""}`;
}

// ---------- actions ----------
async function post(url, body, isForm) {
  const opts = { method: "POST" };
  if (isForm) opts.body = body; else { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body || {}); }
  const r = await fetch(url, opts);
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    appendFeed({ kind: "info", level: "error", text: e.error?.message || `Request failed (${r.status})` });
  }
}
$("cmd-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("cmd").value.trim();
  if (v) { post("/api/command", { text: v }); $("cmd").value = ""; }
});
$("sum-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("sum-target").value.trim();
  if (v) post("/api/command", { text: "summarize " + v });
});
$("btn-capture").addEventListener("click", () => post("/api/capture", {}));
$("btn-stop").addEventListener("click", () => post("/api/stop", {}));
document.querySelectorAll("[data-cmd]").forEach((b) => b.addEventListener("click", () => post("/api/command", { text: b.dataset.cmd })));
$("upload").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("photo", f);
  post("/api/capture", fd, true);
  e.target.value = "";
});
$("samples").addEventListener("change", (e) => {
  if (e.target.value) post("/api/capture", { sample: e.target.value });
  e.target.value = "";
});
document.querySelectorAll(".toggle button").forEach((b) => b.addEventListener("click", () => {
  state.view = b.dataset.view;
  document.querySelectorAll(".toggle button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  showImage(state.selected);
}));
$("gallery").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) { state.selected = b.dataset.id; showImage(b.dataset.id); }
});

// ---------- live updates ----------
let busyTimer;
function setBusy(text) {
  $("busy").textContent = text || "";
  clearTimeout(busyTimer);
  if (text) busyTimer = setTimeout(() => ($("busy").textContent = ""), 20000);
}
function handle(ev) {
  switch (ev.kind) {
    case "devices": renderDevices(ev); break;
    case "session": renderSession(ev.session); break;
    case "capture":
      setBusy("");
      renderCapture(ev, true);
      state.captures.unshift({ id: ev.id, decision: ev.decision, expected: ev.expected, detected: ev.detected });
      renderGallery(state.captures.slice(0, 30));
      loadHistory();
      break;
    case "summary": setBusy(""); renderSummary(ev); loadHistory(); break;
    case "stats": renderStats(ev); break;
    case "heard": appendFeed(ev); loadHistory(); break;
    case "speech": setBusy(""); appendFeed(ev); break;
    case "info": appendFeed(ev); break;
    case "busy": setBusy(ev.what); break;
    case "button": setBusy(ev.state === "down" ? "Listening…" : ""); break;
  }
}

async function init() {
  const s = await (await fetch("/api/state")).json();
  renderDevices(s.devices);
  renderSession(s.session);
  renderGallery(s.captures);
  if (s.last_capture) renderCapture(s.last_capture, false);
  renderSummary(s.last_summary);
  renderFeed(s.feed);
  renderStats(s.stats);
  loadHistory();
  const samples = await (await fetch("/api/samples")).json();
  if (samples.length) {
    $("samples").insertAdjacentHTML("beforeend", samples.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join(""));
    $("samples").hidden = false;
  }
  const es = new EventSource("/api/events");
  es.onmessage = (m) => handle(JSON.parse(m.data));
  es.onerror = () => setBusy("Reconnecting to VisionVoice…");
  es.onopen = () => setBusy("");
}
init();
