"use strict";

// Shared state and formatting helpers.
const $ = (id) => document.getElementById(id);
let analysis = null,
  experiment = null,
  prefix = "/data/sample/",
  baseline = false,
  file = null,
  busy = false,
  toastTimer = null;
const video = $("video"),
  canvas = $("overlay"),
  ctx = canvas.getContext("2d");

const fmt = (x, d = 1) =>
  x === null || x === undefined || !Number.isFinite(Number(x))
    ? "—"
    : Number(x).toFixed(d);

const clock = (x, detail = false) => {
  const cs = Math.round(Math.max(0, Number(x) || 0) * 100);
  return `${String(Math.floor(cs / 6000)).padStart(2, "0")}:${String(Math.floor(cs / 100) % 60).padStart(2, "0")}${detail ? "." + String(cs % 100).padStart(2, "0") : ""}`;
};

const metric = (id, n, unit, d = 1) => {
  const el = $(id);
  el.replaceChildren(document.createTextNode(fmt(n, d)));
  if (n !== null && n !== undefined) {
    const small = document.createElement("small");
    small.textContent = unit;
    el.append(small);
  }
};

const artifact = (key, fallback) => {
  const name = analysis?.artifacts?.[key] || fallback;
  return name.startsWith("/") ? name : prefix + name;
};

// Navigation and analysis loading.
function notify(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 4000);
}

function page(name) {
  document
    .querySelectorAll(".page")
    .forEach((el) => (el.hidden = el.id !== name + "Page"));
  document
    .querySelectorAll(".nav-item")
    .forEach((el) => el.classList.toggle("active", el.dataset.page === name));
  const titles = {
    review: [
      "Video Review",
      "Understand every aiming decision",
      "From gameplay video to target tracking, make aiming observable and reviewable.",
    ],
    experiments: [
      "Experiments",
      "Make every iteration measurable",
      "Start with a simple baseline and record behavior, limitations, and next experiments.",
    ],
    method: [
      "Method",
      "Understand the vision behind the metrics",
      "Define each measurement and make the limitations explicit.",
    ],
  };
  $("crumb").textContent = titles[name][0];
  $("pageTitle").replaceChildren(document.createTextNode(titles[name][1]));
  const dot = document.createElement("span");
  dot.textContent = ".";
  $("pageTitle").append(dot);
  $("pageSubtitle").textContent = titles[name][2];
  if (name !== "review") video.pause();
}

document
  .querySelectorAll(".nav-item")
  .forEach((el) => el.addEventListener("click", () => page(el.dataset.page)));

async function loadAnalysis(data, base, name) {
  analysis = data;
  prefix = base;
  baseline = false;
  const s = data.summary,
    v = data.video;
  canvas.width = v.width;
  canvas.height = v.height;
  $("sessionName").textContent = name || "Twistzz · CS2 gameplay";
  $("sessionMeta").textContent =
    `${v.width} × ${v.height} / ${fmt(v.duration, 1)}s / ${fmt(v.analysis_fps, 0)} FPS analysis`;
  $("analysisState").textContent = "Analysis complete";
  metric("meanError", s.mean_error_px, "px");
  metric("alignment", s.alignment_pct, "%");
  metric("latency", s.median_acquisition_ms, "ms", 0);
  metric("trackCount", s.confirmed_tracks, "tracks", 0);
  $("latencyNote").textContent =
    s.median_acquisition_ms === null
      ? "No eligible acquisition events"
      : `${s.acquired_encounters} eligible event${s.acquired_encounters === 1 ? "" : "s"} · ${fmt(1000 / v.analysis_fps, 0)} ms sampling interval`;
  $("coverage").textContent =
    `Confirmed targets in ${s.confirmed_frames} / ${s.total_frames} sampled frames`;
  $("seek").max = v.duration;
  $("duration").textContent = clock(v.duration);
  $("videoLoading").hidden = false;
  video.src = data.video_url || artifact("recording", "recording.mp4");
  video.poster = artifact("preview", "preview.jpg");
  video.load();
  video.addEventListener(
    "loadeddata",
    () => {
      video.currentTime = Math.min(0.3, v.duration * 0.1);
      $("videoLoading").hidden = true;
      draw();
    },
    {
      once: true,
    },
  );
  $("csvDownload").href = artifact("frame_metrics", "frame_metrics.csv");
  $("jsonDownload").href = data.analysis_url || prefix + "analysis.json";
  $("videoDownload").href = artifact("annotated", "annotated.mp4");
  $("experimentDownload").href = artifact("experiment", "experiment.json");
  buildEvents();
  buildFeedback();
  buildChart();
  setArm(false);
  try {
    experiment = await fetch(artifact("experiment", "experiment.json")).then(
      (r) => {
        if (!r.ok) throw Error(r.status);
        return r.json();
      },
    );
    buildExperiment();
  } catch {
    experiment = null;
    $("experimentDetails").textContent =
      "Experiment results are unavailable for this recording.";
  }
}

// Event list and review feedback.
function buildEvents() {
  const list = $("encounterList");
  list.replaceChildren();
  $("eventCount").textContent = `${analysis.encounters.length} EVENTS`;
  if (!analysis.encounters.length) {
    const el = document.createElement("p");
    el.className = "empty";
    el.textContent =
      "No continuously confirmed targets. Try a lower confidence threshold or clearer first-person recording.";
    list.append(el);
    return;
  }
  analysis.encounters.forEach((e, index) => {
    const b = document.createElement("button");
    b.className = "encounter";
    b.dataset.index = index;
    b.setAttribute(
      "aria-label",
      `Seek to target ${e.id}, ${clock(e.start, true)}`,
    );
    const icon = document.createElement("span");
    icon.className = "event-icon";
    icon.innerHTML = '<svg><use href="#cross"/></svg>';
    const title = document.createElement("span");
    const strong = document.createElement("b");
    strong.textContent = `TRACK ${String(e.id).padStart(2, "0")} / ${e.segment}`;
    const small = document.createElement("small");
    small.textContent = `${clock(e.start, true)} → ${clock(e.end, true)}`;
    title.append(strong, small);
    const delay = document.createElement("span");
    delay.className = "event-delay";
    delay.textContent = e.already_aligned
      ? "Initially aligned"
      : e.acquisition_ms === null
        ? "No alignment observed"
        : `${fmt(e.acquisition_ms, 0)} ms`;
    b.append(icon, title, delay);
    b.addEventListener("click", () => {
      video.currentTime = Math.min(analysis.video.duration, e.start + 0.2);
      video.pause();
      draw();
    });
    list.append(b);
  });
}

function buildFeedback() {
  const parent = $("feedback");
  parent.replaceChildren();
  const messages = analysis.feedback?.length
    ? analysis.feedback
    : [
        "Insufficient confirmed targets. Check the model, recording, and confidence threshold.",
      ];
  messages.slice(0, 3).forEach((text, i) => {
    const el = document.createElement("div");
    el.className = "observation";
    const n = document.createElement("span");
    n.textContent = String(i + 1).padStart(2, "0");
    const p = document.createElement("p");
    p.textContent = text;
    el.append(n, p);
    parent.append(el);
  });
}

function frameAt(t) {
  if (!analysis?.frames?.length) return null;
  let lo = 0,
    hi = analysis.frames.length - 1;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (analysis.frames[mid].time <= t + 1e-6) lo = mid;
    else hi = mid - 1;
  }
  return analysis.frames[lo];
}

function setArm(value) {
  baseline = value;
  $("improvedButton").classList.toggle("active", !value);
  $("baselineButton").classList.toggle("active", value);
  $("frameBadge").textContent = value
    ? "YOLO11n / FULL-FRAME BASELINE"
    : "YOLO11n + ROI + TRACKING";
  draw();
}

$("improvedButton").addEventListener("click", () => setArm(false));

$("baselineButton").addEventListener("click", () => setArm(true));

// Synchronized replay overlays and frame details.
function draw() {
  if (!analysis) return;
  const f = frameAt(video.currentTime);
  if (!f) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const cp = baseline
    ? {
        x: canvas.width / 2,
        y: canvas.height / 2,
        source: "center_config",
      }
    : f.crosshair;
  if ($("showRoi").checked && !baseline) {
    const w = canvas.width,
      h = canvas.height;
    ctx.fillStyle = "rgba(5,9,15,.40)";
    ctx.fillRect(0, 0, w, h * 0.12);
    ctx.fillRect(0, h * 0.12, w * 0.2, h * 0.72);
    ctx.fillRect(0, h * 0.84, w, h * 0.16);
    ctx.strokeStyle = "#d3ba80";
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 5]);
    ctx.strokeRect(w * 0.2, h * 0.12, w * 0.8, h * 0.72);
    ctx.setLineDash([]);
  }
  const dets = baseline ? f.baseline_detections || [] : f.detections;
  const selected = baseline
    ? dets.reduce(
        (a, b) =>
          !a ||
          Math.hypot(
            (b.box[0] + b.box[2]) / 2 - cp.x,
            (b.box[1] + b.box[3]) / 2 - cp.y,
          ) <
            Math.hypot(
              (a.box[0] + a.box[2]) / 2 - cp.x,
              (a.box[1] + a.box[3]) / 2 - cp.y,
            )
            ? b
            : a,
        null,
      )
    : dets.find((d) => d.id === f.selected_id);
  if ($("showBoxes").checked) {
    for (const [i, d] of dets.entries()) {
      const [x1, y1, x2, y2] = d.box;
      const color = !baseline && d.confirmed ? "#a8ebbe" : "#f7ac63";
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash(d.confirmed && !baseline ? [] : [5, 3]);
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      ctx.setLineDash([]);
      ctx.font = "bold 12px monospace";
      const label = `${baseline ? "PERSON" : d.confirmed ? "TRACK" : "CANDIDATE"} ${d.id ?? i + 1}  ${fmt(d.confidence * 100, 0)}%`;
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = "rgba(10,15,13,.88)";
      ctx.fillRect(x1, Math.max(32, y1 - 24), tw + 13, 20);
      ctx.fillStyle = color;
      ctx.fillText(label, x1 + 6, Math.max(46, y1 - 10));
    }
  }
  if ($("showAim").checked) {
    ctx.strokeStyle = "#f7a34c";
    ctx.lineWidth = 1.5;
    const { x, y } = cp;
    ctx.beginPath();
    ctx.moveTo(x - 13, y);
    ctx.lineTo(x - 5, y);
    ctx.moveTo(x + 5, y);
    ctx.lineTo(x + 13, y);
    ctx.moveTo(x, y - 13);
    ctx.lineTo(x, y - 5);
    ctx.moveTo(x, y + 5);
    ctx.lineTo(x, y + 13);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.stroke();
    if (selected) {
      const aim = baseline
        ? [
            (selected.box[0] + selected.box[2]) / 2,
            (selected.box[1] + selected.box[3]) / 2,
          ]
        : selected.aim;
      ctx.setLineDash([4, 5]);
      ctx.strokeStyle = "#f7b677";
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(aim[0], aim[1]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#f7a34c";
      ctx.beginPath();
      ctx.arc(aim[0], aim[1], 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  $("frameTime").textContent = clock(video.currentTime, true);
  $("currentTime").textContent = clock(video.currentTime);
  if (document.activeElement !== $("seek")) $("seek").value = video.currentTime;
  $("liveTarget").textContent = selected
    ? `${baseline ? "P" : "T"}${String(selected.id ?? dets.indexOf(selected) + 1).padStart(2, "0")}`
    : "—";
  $("targetStatus").textContent = selected
    ? baseline
      ? "Baseline candidate"
      : selected.on_target
        ? "Aligned"
        : selected.confirmed
          ? "Tracking"
          : "Pending confirmation"
    : "No confirmed target";
  $("liveConfidence").textContent = selected
    ? `${fmt(selected.confidence * 100, 1)}%`
    : "—";
  $("liveError").textContent =
    `${fmt(baseline ? f.baseline_error_px : f.aim_error_px)}${(baseline ? f.baseline_error_px : f.aim_error_px) !== null ? " px" : ""}`;
  $("liveCrosshair").textContent =
    cp.source === "hsv"
      ? "HSV localization"
      : cp.source === "center_config"
        ? "Fixed screen center"
        : "Center fallback";
  $("liveCount").textContent =
    `${dets.length} / ${baseline ? "Candidate" : f.confirmed_count + " confirmed"}`;
  const line = $("playhead");
  if (line) {
    const x = 45 + (video.currentTime / analysis.video.duration) * 930;
    line.setAttribute("x1", x);
    line.setAttribute("x2", x);
  }
  document.querySelectorAll(".encounter").forEach((el) => {
    const e = analysis.encounters[Number(el.dataset.index)];
    el.classList.toggle(
      "current",
      Boolean(e) &&
        video.currentTime >= e.start &&
        video.currentTime <= e.end + 0.1,
    );
  });
}

function animate() {
  if (!video.paused) draw();
  requestAnimationFrame(animate);
}
animate();

video.addEventListener("seeked", draw);

video.addEventListener("timeupdate", draw);

video.addEventListener("play", () => {
  $("playButton").textContent = "Ⅱ";
  $("playButton").setAttribute("aria-label", "Pause video");
});

video.addEventListener("pause", () => {
  $("playButton").innerHTML = '<svg><use href="#play"/></svg>';
  $("playButton").setAttribute("aria-label", "Play video");
});

video.addEventListener("error", () => {
  $("videoLoading").hidden = false;
  $("videoLoading").textContent =
    "Video loading failed. Check the local service and H.264 file.";
});

async function togglePlay() {
  if (!analysis) return;
  if (video.paused) {
    try {
      await video.play();
    } catch {
      notify("Playback is unavailable. Check the video format.");
    }
  } else video.pause();
}

$("playButton").addEventListener("click", togglePlay);

$("videoStage").addEventListener("click", togglePlay);

$("seek").addEventListener("input", (e) => {
  video.currentTime = Number(e.target.value);
  draw();
});

$("speed").addEventListener(
  "change",
  (e) => (video.playbackRate = Number(e.target.value)),
);
["showBoxes", "showAim", "showRoi"].forEach((id) =>
  $(id).addEventListener("change", draw),
);
document.addEventListener("keydown", (e) => {
  if (
    $("uploadDialog").open ||
    ["INPUT", "SELECT", "BUTTON"].includes(document.activeElement.tagName) ||
    !analysis
  )
    return;
  if (e.code === "Space") {
    e.preventDefault();
    togglePlay();
  }
  if (e.code === "ArrowRight" || e.code === "ArrowLeft") {
    e.preventDefault();
    video.currentTime = Math.max(
      0,
      Math.min(
        analysis.video.duration,
        video.currentTime + (e.code === "ArrowRight" ? 1 : -1),
      ),
    );
    draw();
  }
});

function buildChart() {
  const frames = analysis.frames,
    duration = analysis.video.duration,
    max = Math.max(
      100,
      ...frames.map((f) =>
        Math.max(f.aim_error_px || 0, f.baseline_error_px || 0),
      ),
    );
  const top = Math.ceil(max / 100) * 100;
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 1000 145");
  svg.setAttribute("preserveAspectRatio", "none");
  const add = (tag, attrs, text) => {
    const el = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    if (text) el.textContent = text;
    svg.append(el);
    return el;
  };
  for (let i = 0; i < 4; i++) {
    const y = 12 + i * 32;
    add("line", {
      x1: 45,
      y1: y,
      x2: 975,
      y2: y,
      stroke: "#2b3037",
      "stroke-width": 0.7,
    });
    add(
      "text",
      {
        x: 35,
        y: y + 3,
        fill: "#6d7480",
        "font-size": 8,
        "font-family": "monospace",
        "text-anchor": "end",
      },
      String(Math.round(top - (i * top) / 3)),
    );
  }
  for (let i = 0; i <= 6; i++) {
    const x = 45 + (i * 930) / 6;
    add(
      "text",
      {
        x,
        y: 136,
        fill: "#6d7480",
        "font-size": 8,
        "font-family": "monospace",
        "text-anchor": "middle",
      },
      clock((duration * i) / 6),
    );
  }
  for (const [key, color, width] of [
    ["baseline_error_px", "#657080", 1.1],
    ["aim_error_px", "#f7a34c", 1.7],
  ]) {
    let path = "",
      open = false;
    for (const f of frames) {
      if (f[key] === null || f[key] === undefined) {
        open = false;
        continue;
      }
      const x = 45 + (f.time / duration) * 930,
        y = 108 - (f[key] / top) * 96;
      path += `${open ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)} `;
      open = true;
    }
    add("path", {
      d: path,
      fill: "none",
      stroke: color,
      "stroke-width": width,
      "stroke-linejoin": "round",
      "vector-effect": "non-scaling-stroke",
    });
  }
  add("line", {
    id: "playhead",
    x1: 45,
    y1: 4,
    x2: 45,
    y2: 113,
    stroke: "#e6e6e2",
    "stroke-width": 0.7,
    "stroke-dasharray": "3 3",
  });
  $("chart").replaceChildren(svg);
}

$("chart").addEventListener("click", (e) => {
  if (!analysis) return;
  const rect = $("chart").getBoundingClientRect();
  video.currentTime = Math.max(
    0,
    Math.min(
      analysis.video.duration,
      ((((e.clientX - rect.left) / rect.width) * 1000 - 45) / 930) *
        analysis.video.duration,
    ),
  );
  draw();
});

$("chart").addEventListener("keydown", (e) => {
  if (!analysis) return;
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    video.currentTime = Math.max(
      0,
      Math.min(
        analysis.video.duration,
        video.currentTime + (e.key === "ArrowRight" ? 1 : -1),
      ),
    );
    draw();
  }
});

// Baseline and iteration comparison.
function buildExperiment() {
  if (!experiment) return;
  const labels = {
    total_detections: "Total candidate detections",
    raw_roi_detections: "Raw ROI detections",
    geometry_rejections: "Geometry rejections",
    coverage_pct: "Candidate frame coverage (%)",
    naive_latency_events: "Baseline delay events",
    naive_zero_latency_events: "Baseline zero-delay events",
    acquisition_events: "Eligible acquisition events",
    already_aligned_encounters: "Initially aligned events",
    detected_frames: "Frames with candidates",
    confirmed_frames: "Frames with confirmed targets",
    confirmed_tracks: "Confirmed tracks",
    mean_error_px: "Mean aim distance (px)",
    alignment_pct: "Alignment (%)",
    median_acquisition_ms: "Median acquisition proxy (ms)",
    naive_latency_median_ms: "Baseline global median delay (ms)",
  };
  for (const arm of ["baseline", "improved"]) {
    const el = $(arm + "Stats");
    el.replaceChildren();
    const stats = experiment[arm] || experiment.arms?.[arm] || {};
    let rows = 0;
    for (const [key, label] of Object.entries(labels)) {
      if (!(key in stats)) continue;
      const row = document.createElement("div");
      row.className = "stats-row";
      const l = document.createElement("span");
      l.textContent = label;
      const n = document.createElement("strong");
      n.textContent = fmt(
        stats[key],
        key.includes("frames") ||
          key.includes("tracks") ||
          key.includes("detections") ||
          key.includes("rejections") ||
          key.includes("events") ||
          key.includes("encounters")
          ? 0
          : 1,
      );
      row.append(l, n);
      el.append(row);
      rows++;
    }
    if (!rows)
      for (const [key, value] of Object.entries(stats)) {
        if (typeof value !== "number") continue;
        const row = document.createElement("div");
        row.className = "stats-row";
        const l = document.createElement("span");
        l.textContent = labels[key] || key;
        const n = document.createElement("strong");
        n.textContent = fmt(value, Number.isInteger(value) ? 0 : 1);
        row.append(l, n);
        el.append(row);
      }
  }
  const el = $("experimentDetails");
  el.replaceChildren();
  const facts = document.createElement("div");
  facts.className = "experiment-facts";
  facts.textContent = `YOLO11n · CONF ${analysis.options.conf} · IMG ${analysis.options.imgsz} · ${analysis.video.analysis_fps} FPS · ${analysis.summary.total_frames} SAMPLES`;
  el.append(facts);
  const p = document.createElement("p");
  p.textContent =
    "This iteration changes the detection region, filtering, crosshair localization, and tracking together. Mean distance depends on target selection, anchors, and missed detections; it does not establish a change in player skill.";
  el.append(p);
}

// Local uploads and job progress.
function openUpload() {
  if (!busy) $("uploadError").textContent = "";
  video.pause();
  $("uploadDialog").showModal();
}

$("uploadButton").addEventListener("click", openUpload);

$("settingsButton").addEventListener("click", openUpload);

$("closeDialog").addEventListener("click", () => {
  if (busy) notify("Analysis will continue in the background.");
  $("uploadDialog").close();
});

function setFile(value) {
  file = value;
  $("fileLabel").textContent = value
    ? value.name
    : "Choose or drop a recording";
  $("uploadError").textContent = "";
}

$("fileInput").addEventListener("change", (e) =>
  setFile(e.target.files[0] || null),
);
const drop = $("dropZone");
drop.addEventListener("dragover", (e) => {
  e.preventDefault();
  drop.classList.add("dragover");
});
drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  drop.classList.remove("dragover");
  if (!busy) setFile(e.dataTransfer.files[0] || null);
});

$("uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (busy) return;
  if (!file) {
    $("uploadError").textContent = "Choose a recording first.";
    return;
  }
  if (!/\.(mp4|mov|mkv|avi)$/i.test(file.name)) {
    $("uploadError").textContent = "Supported formats: MP4, MOV, MKV, and AVI.";
    return;
  }
  if (file.size > 500 * 1024 * 1024) {
    $("uploadError").textContent =
      "File exceeds 500 MB. Trim the recording first.";
    return;
  }
  busy = true;
  $("analyzeButton").disabled = true;
  $("jobProgress").hidden = false;
  $("uploadError").textContent = "";
  $("progressText").textContent = "Uploading to the local service...";
  $("progressFill").style.width = "2%";
  try {
    const q = new URLSearchParams({
      filename: file.name,
      conf: $("confidenceInput").value,
      sample_fps: $("fpsInput").value,
      max_seconds: $("secondsInput").value,
      crosshair: $("crosshairInput").value,
      tracking: "true",
      hud: "true",
      imgsz: "1280",
    });
    const r = await fetch("/api/analyze?" + q, {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
      },
      body: file,
    });
    const job = await r.json();
    if (!r.ok) throw Error(job.error || "Could not create analysis job");
    let status;
    while (true) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      const rr = await fetch("/api/jobs/" + job.id);
      status = await rr.json();
      if (!rr.ok) throw Error(status.error || "Could not read job status");
      $("progressFill").style.width = `${Math.max(2, status.progress * 100)}%`;
      $("progressText").textContent =
        status.message || "Running local detection and tracking...";
      if (status.status === "failed")
        throw Error(status.error || status.message || "Analysis failed");
      if (status.status === "complete") break;
    }
    $("progressFill").style.width = "100%";
    await loadAnalysis(status.result, "/runs/" + job.id + "/", file.name);
    history.replaceState(null, "", "/?run=" + job.id);
    page("review");
    $("uploadDialog").close();
    notify(
      "Analysis complete. Your recording and measured results are loaded.",
    );
  } catch (err) {
    $("uploadError").textContent = err.message;
    $("progressText").textContent =
      "Analysis did not complete. Adjust settings and retry.";
  } finally {
    busy = false;
    $("analyzeButton").disabled = false;
  }
});

$("sampleButton").addEventListener("click", async () => {
  if (busy) {
    notify(
      "A recording is being analyzed. Switch to the sample after completion.",
    );
    return;
  }
  try {
    const data = await fetch("/api/sample").then((r) => {
      if (!r.ok) throw Error("Could not load sample");
      return r.json();
    });
    await loadAnalysis(data, "/data/sample/");
    history.replaceState(null, "", "/");
    page("review");
    notify("Loaded the bundled gameplay sample.");
  } catch (err) {
    notify(err.message);
  }
});

(async () => {
  try {
    const saved = new URLSearchParams(location.search).get("run");
    if (saved && /^[0-9a-f]{32}$/.test(saved)) {
      const r = await fetch("/api/jobs/" + saved);
      const job = await r.json();
      if (r.ok && job.status === "complete") {
        await loadAnalysis(
          job.result,
          "/runs/" + saved + "/",
          job.result.video.source,
        );
        return;
      }
      notify("Saved analysis is unavailable. Loaded the bundled sample.");
    }
    const r = await fetch("/api/sample");
    if (!r.ok) throw Error("Sample not ready");
    await loadAnalysis(await r.json(), "/data/sample/");
  } catch (err) {
    $("analysisState").textContent = "Sample not ready";
    $("videoLoading").textContent =
      "Bundled analysis is unavailable. Import a recording.";
    $("uploadError").textContent = err.message;
  }
})();
