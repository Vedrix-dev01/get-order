"use strict";

const STATUS_ORDER = ["researching", "awaiting_angle_approval", "scripting", "awaiting_footage", "editing",
  "writing_copy", "queuing_drafts", "awaiting_final_approval", "published"];
const STATUS_LABEL = {
  researching: "Researching", awaiting_angle_approval: "Waiting for you: pick an angle", scripting: "Writing script",
  awaiting_footage: "Waiting for you: film it", editing: "Editing video", writing_copy: "Writing copy",
  queuing_drafts: "Queuing drafts", awaiting_final_approval: "Waiting for you: final approval",
  published: "Published", failed: "Failed",
};
const RAIL = [
  { kind: "agent", idx: 0, n: "Agent 1", t: "Researcher", key: "researcher" },
  { kind: "gate", idx: 1, t: "You pick angle" },
  { kind: "agent", idx: 2, n: "Agent 2", t: "Scriptwriter", key: "scriptwriter" },
  { kind: "gate", idx: 3, t: "You film" },
  { kind: "agent", idx: 4, n: "Agent 3", t: "Video Editor", key: "video_editor" },
  { kind: "agent", idx: 5, n: "Agent 4", t: "Copywriter", key: "copywriter" },
  { kind: "agent", idx: 6, n: "Agent 5", t: "Publisher", key: "publisher" },
  { kind: "gate", idx: 7, t: "You approve" },
];
const PLATFORM = {
  instagram: "Instagram Reels", tiktok: "TikTok", youtube_shorts: "YouTube Shorts",
};

const ui = { runId: null, lastEvent: 0, slices: {}, timer: null, config: null };
const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function statusClass(status) {
  if (status === "published") return "done";
  if (status === "failed") return "fail";
  if (status.startsWith("awaiting")) return "wait";
  return "work";
}

// ---------- runs list ----------
async function loadRuns() {
  const runs = await api("/api/runs");
  $("#runList").innerHTML = runs.map((r) => `
    <li><button class="${r.id === ui.runId ? "active" : ""}" onclick="selectRun('${r.id}')">
      <span class="rt">${esc(r.title || r.topics[0])}</span>
      <span class="rs">${esc(STATUS_LABEL[r.status] || r.status)} · ${esc(r.id)}</span>
    </button></li>`).join("") || `<li class="muted" style="padding:8px">No runs yet</li>`;
  return runs;
}

function selectRun(id) {
  ui.runId = id;
  ui.lastEvent = 0;
  ui.slices = {};
  $("#feed").innerHTML = "";
  $("#stages").innerHTML = ["research", "script", "edit", "copy", "publish"].map((k) => `<div id="st-${k}"></div>`).join("");
  $("#empty").hidden = true;
  $("#run").hidden = false;
  history.replaceState(null, "", `#run=${id}`);
  loadRuns();
  poll();
}

// ---------- polling ----------
async function poll() {
  clearTimeout(ui.timer);
  if (!ui.runId) return;
  try {
    const run = await api(`/api/runs/${ui.runId}?after=${ui.lastEvent}`);
    renderEvents(run.events);
    renderRun(run);
  } catch (err) {
    console.error(err);
  }
  ui.timer = setTimeout(poll, 1000);
}

function renderEvents(events) {
  const feed = $("#feed");
  for (const ev of events) {
    ui.lastEvent = ev.id;
    const li = document.createElement("li");
    li.className = `ev ${ev.agent} ${ev.level}`;
    li.innerHTML = `<span class="ts">${esc(ev.ts.slice(11, 19))}</span><div class="who">${esc(ev.agent.replace("_", " "))}</div>${esc(ev.message)}`;
    feed.appendChild(li);
    if (ev.level === "error") delete ui.slices.script; // re-enable the filming buttons
  }
  if (events.length) loadRuns();
}

function changed(key, value) {
  const json = JSON.stringify(value);
  if (ui.slices[key] === json) return false;
  ui.slices[key] = json;
  return true;
}

function renderRun(run) {
  $("#runId").textContent = run.id;
  $("#runTitle").textContent = run.angles[run.selected_angle]?.title || run.topics.join(" · ");
  const st = $("#runStatus");
  st.textContent = STATUS_LABEL[run.status] || run.status;
  st.className = `status ${statusClass(run.status)}`;

  if (changed("rail", [run.status, run.busy])) renderRail(run);
  if (changed("banner", [run.status, run.error, run.busy])) renderBanner(run);
  if (changed("research", [run.status, run.signals, run.angles, run.selected_angle])) renderResearch(run);
  if (changed("script", [run.status, run.script, run.shoot_brief])) renderScript(run);
  if (changed("edit", [run.status, run.edit])) renderEdit(run);
  if (changed("copy", [run.copy_pack, run.copy_issues])) renderCopy(run);
  if (changed("publish", [run.status, run.drafts])) renderPublish(run);
}

function failedIndex(run) {
  if (!run.angles.length) return 0;
  if (run.selected_angle === null) return 1;
  if (!run.script) return 2;
  if (!run.edit) return run.footage_file ? 4 : 3;
  if (!run.copy_pack) return 5;
  return 6;
}

function renderRail(run) {
  const cur = run.status === "failed" ? failedIndex(run) : STATUS_ORDER.indexOf(run.status);
  $("#rail").innerHTML = RAIL.map((step) => {
    let cls = "", label = "Queued";
    if (cur > step.idx) { cls = "done"; label = "Done"; }
    else if (cur === step.idx) {
      if (run.status === "failed") { cls = "fail"; label = "Failed"; }
      else if (step.kind === "gate") { cls = "wait"; label = "Your turn"; }
      else { cls = "work"; label = "Working"; }
    }
    if (step.kind === "gate") {
      return `<li class="gate ${cls}"><span class="d"></span>${esc(step.t)}</li>`;
    }
    const tokens = run.usage[step.key];
    const meta = tokens && run.llm_mode === "live" ? ` · ${(tokens.input_tokens + tokens.output_tokens).toLocaleString()} tok` : "";
    return `<li class="node ${cls}"><div class="n">${step.n}</div><div class="t">${esc(step.t)}</div><div class="s">${label}${meta}</div></li>`;
  }).join("");
}

function renderBanner(run) {
  const b = $("#banner");
  if (run.status === "failed") {
    b.innerHTML = `<div class="callout bad"><div><strong>Run failed</strong><span class="mono">${esc(run.error)}</span></div>
      <button class="btn" onclick="act('retry')">Retry from this stage</button></div>`;
  } else if (run.status === "published") {
    b.innerHTML = `<div class="callout ok"><div><strong>Done.</strong>Approved drafts went out (dry run: API requests recorded, not sent).</div></div>`;
  } else if (run.status === "awaiting_angle_approval") {
    b.innerHTML = `<div class="callout"><div><strong>Checkpoint: pick the angle to produce</strong>The researcher ranked these by breakout potential. Choose one below.</div></div>`;
  } else if (run.status === "awaiting_footage") {
    b.innerHTML = `<div class="callout"><div><strong>Your turn: film the script</strong>Record it on your phone and drop it in the Drive folder. The editor picks it up automatically.</div></div>`;
  } else if (run.status === "awaiting_final_approval") {
    b.innerHTML = `<div class="callout"><div><strong>Final approval</strong>Drafts are queued on each platform. Review the video and copy, then approve.</div></div>`;
  } else {
    b.innerHTML = "";
  }
}

function stageHead(n, title, sub) {
  return `<div class="stage-head"><div><div class="agent-tag">Agent ${n}</div><h2>${esc(title)}</h2></div><span class="k">${sub || ""}</span></div>`;
}

function renderResearch(run) {
  const el = $("#st-research");
  if (!run.angles.length && !run.signals.length) {
    el.innerHTML = run.status === "researching" ? `<section class="stage">${stageHead(1, "Researcher", "")}<p class="muted">Pulling trend signals and generating angles…</p></section>` : "";
    return;
  }
  const choosing = run.status === "awaiting_angle_approval";
  const chips = run.signals.map((s) => `<span class="chip" title="${esc(s.title)}"><b>${esc(s.source)}</b> ${esc(s.title)}</span>`).join("");
  const card = (a, i) => {
    const sel = run.selected_angle === i;
    return `<article class="angle ${sel ? "selected" : ""}">
      <div class="title">${esc(a.title)}</div>
      <div class="hook">“${esc(a.hook)}”</div>
      <div class="score">Breakout potential <span class="bar"><i style="width:${a.virality_score * 10}%"></i></span> ${a.virality_score}/10</div>
      <ul>${a.key_points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
      <div class="why"><b>Why now:</b> ${esc(a.why_now)}</div>
      ${choosing ? `<button class="btn primary small" onclick="chooseAngle(${i})">Produce this angle</button>` : sel ? `<span class="agent-tag">✓ Selected</span>` : ""}
    </article>`;
  };
  let body;
  if (run.selected_angle === null) {
    body = `<div class="angles">${run.angles.map(card).join("")}</div>`;
  } else {
    const others = run.angles.map((a, i) => (i === run.selected_angle ? "" : card(a, i))).join("");
    body = `<div class="angles">${card(run.angles[run.selected_angle], run.selected_angle)}</div>
      <details class="others"><summary>${run.angles.length - 1} other angles (kept in the library)</summary><div class="angles">${others}</div></details>`;
  }
  el.innerHTML = `<section class="stage">${stageHead(1, "Researcher: trend signals → content angles", `${run.signals.length} signals · ${run.angles.length} angles saved to DB`)}
    <div class="chips">${chips}</div>${body}</section>`;
}

function renderScript(run) {
  const el = $("#st-script");
  if (!run.script) {
    el.innerHTML = run.status === "scripting" ? `<section class="stage">${stageHead(2, "Scriptwriter", "")}<p class="muted">Writing the 60-second script…</p></section>` : "";
    return;
  }
  const s = run.script;
  const total = Math.max(...s.beats.map((b) => b.end_s), 1);
  const timeline = s.beats.map((b) => `<div class="p-${b.purpose}" style="flex:${Math.max(b.end_s - b.start_s, 1)}" title="${esc(b.purpose)} ${b.start_s}-${b.end_s}s">${esc(b.purpose)}</div>`).join("");
  const rows = s.beats.map((b) => `<tr><td class="when">${b.start_s}–${b.end_s}s</td>
      <td><span class="pill p-${b.purpose}">${esc(b.purpose)}</span></td><td>${esc(b.spoken)}</td>
      <td>${esc(b.on_screen_text)}</td><td class="muted">${esc(b.b_roll)}</td></tr>`).join("");
  const filming = run.status === "awaiting_footage";
  const film = filming ? `<div class="film">
      <div class="grow"><strong>Drop the take in the Drive folder</strong><div class="muted mono">${esc(ui.config?.inbox_dir || "")}</div></div>
      <button class="btn primary" id="simBtn" onclick="simulateFootage()">Simulate phone upload</button>
      <label class="btn">Upload a real clip<input type="file" accept="video/*" hidden onchange="uploadFootage(this)"></label>
    </div>` : "";
  el.innerHTML = `<section class="stage">${stageHead(2, `Scriptwriter: “${s.title}”`, `${run.script_meta.word_count} words · ~${run.script_meta.est_duration_s}s spoken · ${total}s plan`)}
    <div class="timeline">${timeline}</div>
    <table class="script-table"><thead><tr><th>Time</th><th>Beat</th><th>Say</th><th>On screen</th><th>B-roll</th></tr></thead><tbody>${rows}</tbody></table>
    <ul class="notes">${s.filming_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>
    ${run.shoot_brief_file ? `<div class="links"><a href="/media/${run.id}/${esc(run.shoot_brief_file)}" target="_blank">Shoot brief (sent to the creator)</a></div>` : ""}
    ${film}</section>`;
}

function renderEdit(run) {
  const el = $("#st-edit");
  if (!run.edit) {
    el.innerHTML = run.status === "editing" ? `<section class="stage">${stageHead(3, "Video Editor", "")}
      <p class="muted">Detecting silences, cutting, cropping to 9:16 and burning captions…</p></section>` : "";
    return;
  }
  const e = run.edit;
  const pct = Math.round((e.silence_removed_s / e.input_duration_s) * 100);
  el.innerHTML = `<section class="stage">${stageHead(3, "Video Editor: raw take → vertical, cut, captioned", `${esc(e.resolution)} · captions: ${esc(e.caption_source)}`)}
    <div class="edit">
      <div class="phone"><video controls playsinline preload="metadata" poster="/media/${run.id}/${esc(e.thumbnail_file)}" src="/media/${run.id}/${esc(e.output_file)}"></video></div>
      <div>
        <div class="stats">
          <div class="stat"><div class="v">${e.input_duration_s.toFixed(1)}s → ${e.output_duration_s.toFixed(1)}s</div><div class="l">raw take → final</div></div>
          <div class="stat"><div class="v">${e.silence_removed_s.toFixed(1)}s</div><div class="l">dead air removed (${pct}%)</div></div>
          <div class="stat"><div class="v">${e.cuts}</div><div class="l">jump cuts</div></div>
          <div class="stat"><div class="v">9:16</div><div class="l">${esc(e.resolution)}, -14 LUFS audio</div></div>
        </div>
        <ol class="steps">
          <li>Watched the Drive inbox and claimed the new clip</li>
          <li>Found pauses with ffmpeg silencedetect and cut them out</li>
          <li>Center-cropped to vertical 1080×1920</li>
          <li>Captioned in short-form style, with the hook as a banner for the first 3 seconds</li>
          <li>Normalized loudness to -14 LUFS for Reels/TikTok/Shorts</li>
        </ol>
        <div class="links">
          <a href="/media/${run.id}/${esc(e.source_file)}" target="_blank">Raw take</a>
          <a href="/media/${run.id}/${esc(e.output_file)}" download>Download final video</a>
          <a href="/media/${run.id}/${esc(e.captions_file)}" target="_blank">Caption file</a>
        </div>
      </div>
    </div></section>`;
}

function renderCopy(run) {
  const el = $("#st-copy");
  if (!run.copy_pack) {
    el.innerHTML = run.status === "writing_copy" ? `<section class="stage">${stageHead(4, "Copywriter", "")}<p class="muted">Writing platform copy…</p></section>` : "";
    return;
  }
  const posts = run.copy_pack.posts.map((p) => {
    const fin = run.copy_final[p.platform];
    const over = fin.text.length > fin.limit;
    return `<article class="post">
      <div class="plat"><span>${esc(PLATFORM[p.platform])}</span><span class="count ${over ? "over" : ""}">${fin.text.length}/${fin.limit}</span></div>
      <div class="ptitle">${esc(p.title)} <span class="muted" style="font-weight:400">(${p.title.length}/${fin.title_limit})</span></div>
      <pre>${esc(fin.text)}</pre>
    </article>`;
  }).join("");
  const issues = run.copy_issues.length ? `<div class="issues">Auto-fixed: ${run.copy_issues.map(esc).join("; ")}</div>` : "";
  el.innerHTML = `<section class="stage">${stageHead(4, "Copywriter: platform captions, titles and hashtags", "checked against each platform's limits")}
    <div class="copy">${posts}</div>${issues}</section>`;
}

function renderPublish(run) {
  const el = $("#st-publish");
  if (!run.drafts.length) {
    el.innerHTML = run.status === "queuing_drafts" ? `<section class="stage">${stageHead(5, "Publisher", "")}<p class="muted">Queuing drafts…</p></section>` : "";
    return;
  }
  const approving = run.status === "awaiting_final_approval";
  const rows = run.drafts.map((d) => `<article class="draft">
      <div class="draft-top">
        <label>${approving ? `<input type="checkbox" checked data-platform="${d.platform}">` : ""}${esc(PLATFORM[d.platform])}</label>
        <span class="dstatus ${d.status}">${esc(d.status.replaceAll("_", " "))}${d.dry_run ? " · dry run" : ""}</span>
      </div>
      <div class="mech">${esc(d.draft_mechanism)}</div>
      <details><summary>API calls (${d.requests.length} to draft${d.publish_requests.length ? `, ${d.publish_requests.length} to publish` : ""})</summary>
        <pre>${esc([...d.requests, ...d.publish_requests].map((r) => `# ${r.step}\n${r.method} ${r.url}\n${JSON.stringify(r.body, null, 2)}`).join("\n\n"))}</pre>
      </details>
    </article>`).join("");
  el.innerHTML = `<section class="stage">${stageHead(5, "Publisher: drafts queued for your approval", "nothing goes live without you")}
    <div class="drafts">${rows}</div>
    ${approving ? `<div class="approve-bar"><button class="btn primary" onclick="approvePublish()">Approve &amp; publish selected</button></div>` : ""}
  </section>`;
}

// ---------- actions ----------
async function act(action, body) {
  try {
    await post(`/api/runs/${ui.runId}/${action}`, body);
    poll();
  } catch (err) {
    alert(err.message);
  }
}
const chooseAngle = (i) => act("angle", { index: i });
function simulateFootage() {
  const btn = $("#simBtn");
  if (btn) { btn.disabled = true; btn.textContent = "Filming…"; }
  act("simulate-footage");
}
async function uploadFootage(input) {
  const file = input.files[0];
  if (!file) return;
  input.parentElement.textContent = `Uploading ${file.name}…`;
  try {
    await api(`/api/runs/${ui.runId}/footage`, { method: "POST", headers: { "X-Filename": file.name }, body: file });
  } catch (err) {
    alert(err.message);
  }
  poll();
}
function approvePublish() {
  const platforms = [...document.querySelectorAll("[data-platform]")].filter((c) => c.checked).map((c) => c.dataset.platform);
  act("publish", { platforms });
}

function openNewRun() { $("#newRunDialog").showModal(); }
$("#newRunBtn").onclick = openNewRun;
$("#newRunForm").addEventListener("submit", async (ev) => {
  if (ev.submitter?.value !== "start") return;
  const topics = $("#topics").value.split("\n").map((t) => t.trim()).filter(Boolean);
  try {
    const { id } = await post("/api/runs", { topics, auto_approve: $("#auto").checked });
    selectRun(id);
  } catch (err) {
    alert(err.message);
  }
});

const libraryDialog = $("#libraryDialog");
$("#libraryBtn").onclick = async () => {
  const rows = await api("/api/library");
  $("#libraryBody").innerHTML = rows.length ? `<table><thead><tr><th></th><th>Angle</th><th>Hook</th><th>Score</th><th>Run</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${r.selected ? "★" : ""}</td><td>${esc(r.title)}</td><td class="muted">${esc(r.hook)}</td><td>${r.virality_score}/10</td><td class="mono">${esc(r.run_id)}</td></tr>`).join("")}
    </tbody></table>` : `<p class="muted">No angles yet.</p>`;
  libraryDialog.showModal();
};

(async function init() {
  ui.config = await api("/api/config");
  const mode = $("#mode");
  mode.textContent = ui.config.llm_mode === "live" ? `Live · ${ui.config.model}` : "Demo mode · offline agents";
  mode.className = `badge ${ui.config.llm_mode}`;
  const runs = await loadRuns();
  const fromHash = location.hash.match(/run=([0-9a-f]{6})/);
  if (fromHash) selectRun(fromHash[1]);
  else if (runs.length) selectRun(runs[0].id);
  setInterval(loadRuns, 5000);
})();
