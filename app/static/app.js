const $ = (id) => document.getElementById(id);

// ---------- Theme ----------
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("block1-theme", t); } catch (e) {}
}
function initTheme() {
  let t = "light";
  try { t = localStorage.getItem("block1-theme") || "light"; } catch (e) {}
  applyTheme(t);
  const sel = $("theme-select");
  if (sel) sel.value = t;
  sel.addEventListener("change", () => applyTheme(sel.value));
}

let state = {
  view: "dashboard",
  configured: true, // optimistic until initConfigGate() checks; avoids a flash on load
  lectureId: null,
  sessionId: null,
  optionSelected: false,
  currentImages: [],
  currentSlide: null,
  tutorMode: true,
  quizTotal: 0,
  quizActive: false,
  reviewSlides: [],
  segAttached: false,
};

// ---------- Navigation ----------
document.querySelectorAll("nav button").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

function switchView(view) {
  // No API key yet: every nav click lands back on Settings instead of a view
  // that can't do anything without one.
  if (!state.configured && view !== "settings") {
    view = "settings";
    toast("Add an API key in Settings first");
  }
  if (state.view === "drill" && view !== "drill") pauseActiveQuiz();
  state.view = view;
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $("view-" + view).classList.add("active");
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  if (view === "dashboard") loadDashboard();
  if (view === "learn") loadLearnList();
  if (view === "drill") loadDrill();
  if (view === "progress") loadProgress();
  if (view === "settings") loadSettings();
}

// ---------- Toast ----------
let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3000);
}

// ---------- Settings ----------
async function loadSettings() {
  const cfg = await fetch("/api/config").then((r) => r.json());
  state.configured = cfg.configured;
  $("settings-gate-banner").hidden = cfg.configured;
  renderSettingsFields(cfg.fields);
}

function renderSettingsFields(fields) {
  const el = $("settings-fields");
  el.innerHTML = fields.map((f) => {
    const statusLine = f.secret
      ? `<div class="${f.set ? "status-set" : "help"}">${f.set ? `Set (${f.display})` : "Not set"}</div>`
      : "";
    const inputValue = f.secret ? "" : escapeHtml(f.display);
    const placeholder = f.secret && f.set ? "Enter a new key to replace it" : "";
    return `
      <div class="settings-field">
        <label for="cfg-${f.name}">${escapeHtml(f.label)}</label>
        <input id="cfg-${f.name}" type="${f.secret ? "password" : "text"}"
               value="${inputValue}" placeholder="${placeholder}" autocomplete="off">
        ${statusLine}
        <div class="help">${escapeHtml(f.help)}</div>
      </div>`;
  }).join("");
}

async function saveSettings() {
  const statusEl = $("settings-status");
  const inputs = $("settings-fields").querySelectorAll("input");
  const updates = {};
  inputs.forEach((inp) => {
    const name = inp.id.replace(/^cfg-/, "");
    // Secret fields start blank (never pre-filled with the masked value) - only
    // send one if the user actually typed a replacement.
    if (inp.type !== "password" || inp.value) updates[name] = inp.value;
  });
  statusEl.textContent = "Saving...";
  try {
    const cfg = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    }).then((r) => r.json());
    state.configured = cfg.configured;
    $("settings-gate-banner").hidden = cfg.configured;
    renderSettingsFields(cfg.fields);
    statusEl.textContent = cfg.configured ? "Saved." : "Saved, but no API key is set yet.";
    if (cfg.configured) refreshUsage();
  } catch (e) {
    statusEl.textContent = "Save failed: " + e.message;
  }
}

$("settings-save").addEventListener("click", saveSettings);

async function initConfigGate() {
  const cfg = await fetch("/api/config").then((r) => r.json()).catch(() => ({ configured: true }));
  state.configured = cfg.configured;
  if (!cfg.configured) switchView("settings");
}

// ---------- Dashboard ----------
function pct(x) {
  return x === null || x === undefined ? "—" : Math.round(x * 100) + "%";
}

function accClass(x) {
  if (x === null || x === undefined) return "";
  if (x < 0.6) return "acc-bad";
  if (x < 0.8) return "acc-mid";
  return "acc-good";
}

async function loadDashboard() {
  $("dash-loading").style.display = "";
  $("dash-content").hidden = true;
  try {
    const d = await fetch("/api/dashboard").then((r) => r.json());
    $("stat-lectures").textContent = d.lecture_count;
    $("stat-coverage").textContent = d.coverage.question_coverage + "%";
    $("stat-accuracy").textContent = pct(d.overall_accuracy);
    $("stat-accuracy").className = "stat " + accClass(d.overall_accuracy);
    $("stat-questions").textContent = d.question_count;
    $("stat-due").textContent = d.missed_count;

    // Lecture breakdown table (slide coverage)
    const tbody = document.querySelector("#lecture-stats tbody");
    tbody.innerHTML = "";
    for (const l of d.lectures) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="td-title">${escapeHtml(l.lecture_title)}</td>
        <td>${l.slides_covered}/${l.slides_total} <span class="muted">covered</span></td>
        <td>${l.q_count}</td>
        <td><span class="acc-badge ${accClass(l.accuracy)}">${pct(l.accuracy)}</span></td>
        <td><span class="mini-bar"><span class="mini-fill" style="width:${l.coverage_pct}%"></span></span>
            <span class="muted">${l.coverage_pct}%</span></td>`;
      tbody.appendChild(tr);
    }

    // Weak slides
    const weak = $("weak-list");
    weak.innerHTML = "";
    if (d.weak_slides && d.weak_slides.length) {
      $("weak-empty").style.display = "none";
      d.weak_slides.forEach((s) => {
        const li = document.createElement("li");
        li.innerHTML = `
          <span class="w-left">
            <span class="acc-badge ${accClass(s.accuracy)}">${pct(s.accuracy)}</span>
            <span>
              <span class="w-title">Slide ${s.slide_num}: ${escapeHtml(shortLabel(s.text))}</span><br>
              <span class="muted">${escapeHtml(s.lecture_title)} · ${s.answers} answer${s.answers === 1 ? "" : "s"}</span>
            </span>
          </span>`;
        weak.appendChild(li);
      });
    } else {
      $("weak-empty").style.display = "";
    }

    // Coverage gaps (slides with no questions)
    const gapsEl = $("gap-list");
    gapsEl.innerHTML = "";
    const gaps = (d.gaps || []).slice(0, 12);
    if (gaps.length) {
      $("gap-empty").style.display = "none";
      for (const g of gaps) {
        const li = document.createElement("li");
        li.innerHTML = `
          <span class="w-title">Slide ${g.slide_num}: ${escapeHtml(shortLabel(g.text))}</span><br>
          <span class="muted">${escapeHtml(g.lecture_title)}</span>
          <span class="gap-tag">no questions</span>`;
        gapsEl.appendChild(li);
      }
      if (gaps.length >= 12) {
        const li = document.createElement("li");
        li.className = "muted";
        li.textContent = `…showing the first uncovered slides`;
        gapsEl.appendChild(li);
      }
    } else {
      $("gap-empty").style.display = "";
    }

    $("dash-loading").style.display = "none";
    $("dash-content").hidden = false;
    loadProjection();
  } catch (e) {
    $("dash-loading").textContent = "Failed to load dashboard: " + e.message;
  }
}

function shortLabel(s, n = 60) {
  s = s || "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

$("import-btn").addEventListener("click", async () => {
  const files = $("file-input").files;
  if (!files.length) return toast("Choose files first");
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  $("import-btn").disabled = true;
  $("import-result").textContent = "Importing...";
  try {
    const res = await fetch("/api/lectures/import", { method: "POST", body: fd }).then((r) => r.json());
    let msg = `Imported ${res.imported.length} lecture(s).`;
    if (res.duplicates && res.duplicates.length) {
      msg += ` Skipped ${res.duplicates.length} duplicate(s): ` + res.duplicates.map((d) => d.title).join("; ");
    }
    if (res.errors.length) msg += " Errors: " + res.errors.join("; ");
    $("import-result").textContent = msg;
    toast(msg);
    loadDashboard();
    loadQuestionSetLectures();
  } catch (e) {
    $("import-result").textContent = "Import failed: " + e.message;
  } finally {
    $("import-btn").disabled = false;
  }
});

// ---------- Professor question sets ----------
function professorBadge(source) {
  if (source !== "professor") return "";
  return `<div class="prof-badge" title="Written by your professor, not generated from a slide">
    ★ Professor-written question — high yield, expect this style on the exam
  </div>`;
}

async function loadQuestionSetLectures() {
  const sel = $("qs-lecture");
  if (!sel) return;
  const lectures = await fetch("/api/lectures").then((r) => r.json());
  sel.innerHTML = lectures.length
    ? lectures.map((l) => `<option value="${l.id}">${escapeHtml(l.title)}</option>`).join("")
    : `<option value="">Import a lecture first</option>`;
}

$("qs-import-btn").addEventListener("click", async () => {
  const files = $("qs-file-input").files;
  const lectureId = $("qs-lecture").value;
  if (!lectureId) return toast("Import a lecture first, then attach questions to it");
  if (!files.length) return toast("Choose files first");
  const fd = new FormData();
  fd.append("lecture_id", lectureId);
  for (const f of files) fd.append("files", f);
  $("qs-import-btn").disabled = true;
  $("qs-import-result").textContent = "Parsing questions (this makes API calls)...";
  try {
    const res = await fetch("/api/question_sets/import", { method: "POST", body: fd })
      .then((r) => r.json());
    let msg = `Imported ${res.imported} professor question(s).`;
    if (res.skipped_duplicates) msg += ` Skipped ${res.skipped_duplicates} already imported.`;
    if (res.errors.length) msg += " Errors: " + res.errors.join("; ");
    $("qs-import-result").textContent = msg;
    toast(msg);
    loadDashboard();
    refreshUsage();
  } catch (e) {
    $("qs-import-result").textContent = "Import failed: " + e.message;
  } finally {
    $("qs-import-btn").disabled = false;
  }
});

// ---------- Lecture rename / retag ----------
const LECTURE_TAGS = ["foundations", "doctoring", "anatomy"];

async function saveLecture(id, patch) {
  const res = await fetch(`/api/lectures/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Save failed");
  }
  return res.json();
}

function wireLectureEditing(row, l) {
  const pill = row.querySelector(".tag-pill");
  pill.addEventListener("change", async () => {
    const prev = pill.dataset.tag;
    try {
      await saveLecture(l.id, { tag: pill.value });
      pill.dataset.tag = pill.value;
      l.tag = pill.value;
      toast(`Tagged as ${pill.value}`);
    } catch (e) {
      pill.value = prev;   // keep the pill honest about what is stored
      toast(e.message);
    }
  });

  const week = row.querySelector(".week-input");
  const commitWeek = async () => {
    const raw = week.value.trim();
    const next = raw === "" ? null : parseInt(raw, 10);
    if (next === (l.week == null ? null : l.week)) return;
    if (next !== null && (!Number.isInteger(next) || next < 1 || next > 52)) {
      week.value = l.week == null ? "" : l.week;
      return toast("Week must be between 1 and 52");
    }
    try {
      const saved = await saveLecture(l.id, { week: next });
      l.week = saved.week;
      week.value = saved.week == null ? "" : saved.week;
      week.closest(".week-pill").classList.toggle("unset", saved.week == null);
      toast(saved.week == null ? "Week cleared" : `Tagged week ${saved.week}`);
    } catch (e) {
      week.value = l.week == null ? "" : l.week;
      toast(e.message);
    }
  };
  week.addEventListener("blur", commitWeek);
  week.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); week.blur(); }
    else if (e.key === "Escape") { week.value = l.week == null ? "" : l.week; week.blur(); }
  });

  const title = row.querySelector(".title");
  const commit = async () => {
    // textContent, so a name pasted from Canvas with markup stores as plain text.
    const next = title.textContent.trim().replace(/\s+/g, " ");
    if (!next || next === l.title) {
      title.textContent = l.title;   // reject empty, revert no-op edits
      return;
    }
    try {
      const saved = await saveLecture(l.id, { title: next });
      l.title = saved.title;
      title.textContent = saved.title;
      toast("Renamed");
      loadQuestionSetLectures();     // the attach-to-lecture dropdown shows titles
    } catch (e) {
      title.textContent = l.title;
      toast(e.message);
    }
  };
  title.addEventListener("blur", commit);
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      title.blur();                  // Enter commits; newlines never enter the name
    } else if (e.key === "Escape") {
      title.textContent = l.title;
      title.blur();
    }
  });
}

// ---------- Learn ----------
async function loadLearnList() {
  const list = $("learn-lecture-list");
  $("learn-summary").hidden = true;
  list.hidden = false;
  const lectures = await fetch("/api/lectures").then((r) => r.json());
  list.innerHTML = "";
  if (!lectures.length) {
    list.innerHTML = '<div class="muted">No lectures yet. Import them on the Dashboard.</div>';
    return;
  }
  for (const l of lectures) {
    const row = document.createElement("div");
    row.className = "lecture-row";
    const status = l.summary_status === "done" ? "summary ready" : l.summary_status;
    const tag = l.tag || "foundations";
    row.innerHTML = `
      <div class="row-main">
        <div class="title-line">
          <select class="tag-pill" data-tag="${tag}" title="Course strand">
            ${LECTURE_TAGS.map((t) =>
              `<option value="${t}"${t === tag ? " selected" : ""}>${t}</option>`).join("")}
          </select>
          <span class="week-pill${l.week ? "" : " unset"}" title="Week this lecture was given">
            <span class="week-label">Week</span>
            <input class="week-input" type="number" min="1" max="52" placeholder="–"
                   value="${l.week == null ? "" : l.week}">
          </span>
          <span class="title" contenteditable="plaintext-only" spellcheck="false"
                title="Click to rename — saves automatically">${escapeHtml(l.title)}</span>
        </div>
        <div class="meta">${l.slide_count} slides · ${l.word_count} words</div>
      </div>
      <div class="row-actions">
        <span class="meta">${status}</span>
        <button class="btn small ghost" id="cap-${l.id}">Captions</button>
        <button class="btn small ghost" id="ocr-${l.id}">OCR</button>
        <button class="btn small ghost danger-text" id="del-${l.id}">Delete</button>
      </div>`;
    row.addEventListener("click", (e) => {
      // Renaming or retagging must not also open the summary.
      if (e.target.closest("button, .tag-pill, .title, .week-pill")) return;
      openSummary(l.id);
    });
    wireLectureEditing(row, l);
    const ocr = row.querySelector(`#ocr-${l.id}`);
    ocr.addEventListener("click", async () => {
      ocr.disabled = true;
      ocr.textContent = "OCR running...";
      await fetch(`/api/lectures/${l.id}/ocr`, { method: "POST" });
      toast("OCR + captions running in background — this can take a few minutes if slides need the vision fallback");
      await pollOcrStatus(l.id);
      ocr.disabled = false;
      ocr.textContent = "OCR";
    });
    const cap = row.querySelector(`#cap-${l.id}`);
    cap.addEventListener("click", async () => {
      cap.disabled = true;
      cap.textContent = "Generating...";
      await fetch(`/api/lectures/${l.id}/captions`, { method: "POST" });
      toast("Generating image captions in background (~1 min)");
      setTimeout(() => { cap.disabled = false; cap.textContent = "Captions"; }, 3000);
    });
    const del = row.querySelector(`#del-${l.id}`);
    del.addEventListener("click", async () => {
      if (!confirm(`Delete "${l.title}" and all its questions? This cannot be undone.`)) return;
      await fetch(`/api/lectures/${l.id}`, { method: "DELETE" });
      toast("Lecture deleted");
      loadLearnList();
    });
    list.appendChild(row);
  }
}

// Poll until OCR (and its vision fallback) leaves the "running" state, instead of a
// fixed timeout — the vision fallback's network calls mean this can now take minutes,
// and re-enabling the button too early invited a second concurrent OCR run on the same
// lecture, which is what caused "database is locked".
async function pollOcrStatus(lectureId, { intervalMs = 2000, maxAttempts = 150 } = {}) {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((r) => setTimeout(r, intervalMs));
    const { status } = await fetch(`/api/lectures/${lectureId}/ocr/status`).then((r) => r.json());
    if (status !== "running") return status;
  }
  return "running";
}

async function openSummary(lectureId) {
  state.lectureId = lectureId;
  $("learn-lecture-list").hidden = true;
  $("learn-summary").hidden = false;
  const statusEl = $("summary-status");
  const bodyEl = $("summary-body");
  const kpEl = $("key-points");
  statusEl.textContent = "Loading summary...";
  bodyEl.innerHTML = "";
  kpEl.innerHTML = "";

  const regenBtn = $("regen-summary-btn");
  regenBtn.hidden = true;

  let res = await fetch(`/api/lectures/${lectureId}/summary`).then((r) => r.json());
  if (res.status === "done" && res.summary) {
    renderSummary(res.summary);
    regenBtn.hidden = false;
  } else if (res.status === "generating") {
    statusEl.textContent = "Summary is being generated. Refresh in a moment.";
    return;
  } else {
    statusEl.innerHTML = `No summary yet. <button class="btn small" id="gen-summary-btn">Generate now (takes ~30-60s)</button>`;
    $("gen-summary-btn").addEventListener("click", () => runSummaryGeneration(lectureId, statusEl));
  }
  renderSourceSlides(lectureId);
}

async function runSummaryGeneration(lectureId, statusEl, label = "Generating summary from lecture content...") {
  const regenBtn = $("regen-summary-btn");
  statusEl.textContent = label;
  regenBtn.disabled = true;
  try {
    await fetch(`/api/lectures/${lectureId}/summary/generate`, { method: "POST" });
    statusEl.textContent = "Done! Reloading...";
    const res = await fetch(`/api/lectures/${lectureId}/summary`).then((r) => r.json());
    renderSummary(res.summary);
    regenBtn.hidden = false;
  } catch (e) {
    statusEl.textContent = "Generation failed: " + e.message;
  } finally {
    regenBtn.disabled = false;
  }
}

function renderSourceSlides(lectureId) {
  const el = $("slide-deck");
  el.innerHTML = '<div class="muted">Loading slides…</div>';
  fetch(`/api/lectures/${lectureId}/deck`)
    .then((r) => r.json())
    .then((slides) => {
      const withContent = slides.filter((s) => s.text.trim() || (s.images && s.images.length));
      if (!withContent.length) {
        el.innerHTML = "";
        return;
      }
      const blocks = withContent.map((s) => {
        const text = s.text.trim()
          ? `<div class="deck-text">${escapeHtml(s.text)}</div>`
          : "";
        const caption = s.caption && s.caption.trim()
          ? `<div class="deck-caption">${escapeHtml(s.caption)}</div>`
          : "";
        const imgs = (s.images || [])
          .map((u) => `<img class="deck-img" src="${u}" loading="lazy" data-src="${u}">`)
          .join("");
        return `<div class="deck-slide" data-slide-num="${s.slide_num}">
          <div class="deck-num">Slide ${s.slide_num}</div>
          ${text}
          ${imgs ? `<div class="deck-imgs">${imgs}</div>` : ""}
          ${caption}
        </div>`;
      }).join("");
      el.innerHTML = `<h3 class="ss-title">Slides</h3>${blocks}`;
      el.querySelectorAll(".deck-img").forEach((img) => {
        img.addEventListener("click", () => openDeckLightbox(img.dataset.src, el));
      });
    })
    .catch(() => (el.innerHTML = ""));
}

function openDeckLightbox(src, container) {
  const imgs = [...container.querySelectorAll(".deck-img")];
  const idx = imgs.findIndex((i) => i.dataset.src === src);
  if (idx < 0) return;
  lbList = imgs.map((i) => i.dataset.src);
  lbIndex = idx;
  showLightbox();
}

function renderSummary(s) {
  $("summary-status").textContent = "";
  const bodyEl = $("summary-body");
  const kpEl = $("key-points");
  bodyEl.innerHTML = marked(s.body);
  const kps = JSON.parse(s.key_points || "[]");
  if (kps.length) {
    kpEl.innerHTML = "<h4>High-yield key points</h4><ul>" + kps.map((k) => `<li>${marked(k)}</li>`).join("") + "</ul>";
  }
}

$("learn-back").addEventListener("click", () => loadLearnList());

$("regen-summary-btn").addEventListener("click", () => {
  runSummaryGeneration(state.lectureId, $("summary-status"), "Regenerating summary from lecture content...");
});

// ---------- Lightbox ----------
let lbList = [];
let lbIndex = 0;

function showLightbox() {
  if (!lbList.length || !lbList[lbIndex]) {
    closeLightbox();
    return;
  }
  const lb = $("lightbox");
  $("lb-img").src = lbList[lbIndex];
  $("lb-caption").textContent = `Slide ${lbIndex + 1} of ${lbList.length}`;
  lb.hidden = false;
  document.body.style.overflow = "hidden";
}

function closeLightbox() {
  $("lightbox").hidden = true;
  document.body.style.overflow = "";
}

$("lb-close").addEventListener("click", closeLightbox);
$("lb-prev").addEventListener("click", () => {
  lbIndex = (lbIndex - 1 + lbList.length) % lbList.length;
  showLightbox();
});
$("lb-next").addEventListener("click", () => {
  lbIndex = (lbIndex + 1) % lbList.length;
  showLightbox();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("lightbox").hidden) return closeLightbox();
    if (!$("slide-modal").hidden) return closeSlideModal();
  }
  if ($("lightbox").hidden) return;
  if (e.key === "ArrowLeft") $("lb-prev").click();
  if (e.key === "ArrowRight") $("lb-next").click();
});
$("lightbox").addEventListener("click", (e) => {
  if (e.target === $("lightbox")) closeLightbox();
});

// ---------- Slide footnotes ----------
// A citation expands the referenced slide INLINE, right under the text you were
// reading — click again to collapse. Nothing covers the page, so you never lose your
// place, and several footnotes can stay open at once.
const slideCache = new Map();

async function fetchSlide(lectureId, num) {
  const key = `${lectureId}:${num}`;
  if (!slideCache.has(key)) {
    slideCache.set(key, fetch(`/api/lectures/${lectureId}/slide/${num}`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null));
  }
  return slideCache.get(key);
}

function slideCiteLectureId(cite) {
  return parseInt(cite.dataset.lectureId, 10) || state.lectureId;
}

function slidePanelHTML(slide) {
  if (!slide) return `<div class="muted">Slide not found.</div>`;
  const imgs = (slide.images || [])
    .map((u) => `<img class="cite-img" src="${u}" loading="lazy" data-src="${u}">`)
    .join("");
  const text = (slide.text || "").trim();
  const caption = (slide.caption || "").trim();
  return `
    <div class="cite-slide-head">${escapeHtml(slide.lecture_title || "")} · Slide ${slide.slide_num}</div>
    ${imgs ? `<div class="cite-imgs">${imgs}</div>` : ""}
    ${text ? `<div class="cite-text">${escapeHtml(text)}</div>` : ""}
    ${caption ? `<div class="cite-caption">${escapeHtml(caption)}</div>` : ""}`;
}

async function toggleSlideFootnote(cite) {
  const num = parseInt(cite.dataset.slide, 10);
  const lectureId = slideCiteLectureId(cite);
  if (!lectureId || !num) return;

  // The panel lives next to the citation, so it collapses back to exactly where it
  // came from rather than being a separate overlay to dismiss.
  let panel = cite.nextElementSibling;
  if (panel && panel.classList.contains("cite-slide")) {
    const open = !panel.hidden;
    panel.hidden = open;
    cite.classList.toggle("open", !open);
    return;
  }
  panel = document.createElement("div");
  panel.className = "cite-slide";
  panel.innerHTML = `<div class="muted">Loading slide ${num}…</div>`;
  cite.insertAdjacentElement("afterend", panel);
  cite.classList.add("open");

  const slide = await fetchSlide(lectureId, num);
  panel.innerHTML = slidePanelHTML(slide);
  // Images stay expandable to full size via the lightbox, but only on a second click.
  wireImageZoom(panel, panel.querySelectorAll(".cite-img"));
}

document.addEventListener("click", (e) => {
  const cite = e.target.closest(".slide-cite");
  if (!cite) return;
  e.preventDefault();
  toggleSlideFootnote(cite);
});

// Hover preview: a small card showing the slide before you commit to expanding it.
let hoverCard = null;
let hoverTimer = null;

function hideSlideHover() {
  clearTimeout(hoverTimer);
  if (hoverCard) { hoverCard.remove(); hoverCard = null; }
}

document.addEventListener("mouseover", (e) => {
  const cite = e.target.closest(".slide-cite");
  if (!cite) return;
  const num = parseInt(cite.dataset.slide, 10);
  const lectureId = slideCiteLectureId(cite);
  if (!lectureId || !num) return;
  clearTimeout(hoverTimer);
  hoverTimer = setTimeout(async () => {
    const slide = await fetchSlide(lectureId, num);
    if (!slide || !cite.isConnected) return;
    hideSlideHover();
    hoverCard = document.createElement("div");
    hoverCard.className = "cite-hover";
    const img = (slide.images || [])[0];
    hoverCard.innerHTML =
      `<div class="cite-hover-head">Slide ${slide.slide_num}</div>` +
      (img ? `<img src="${img}" loading="lazy">` : "") +
      `<div class="cite-hover-text">${escapeHtml(
        (slide.caption || slide.text || "").trim().slice(0, 220) || "No text on this slide."
      )}</div><div class="cite-hover-hint">Click to pin below</div>`;
    document.body.appendChild(hoverCard);
    const r = cite.getBoundingClientRect();
    const top = r.bottom + 8 + window.scrollY;
    hoverCard.style.top = `${top}px`;
    hoverCard.style.left =
      `${Math.max(8, Math.min(r.left + window.scrollX, window.innerWidth - hoverCard.offsetWidth - 8))}px`;
  }, 250);
});

document.addEventListener("mouseout", (e) => {
  if (e.target.closest && e.target.closest(".slide-cite")) hideSlideHover();
});
window.addEventListener("scroll", hideSlideHover, { passive: true });

// ---------- Source slide modal (drill explanations / review) ----------
function openSlideModal(slide, images) {
  slide = slide || {};
  const num = slide.slide_num;
  $("sm-num").textContent = num ? `Slide ${num}` : "Source slide";
  $("sm-text").textContent = slide.text || "";
  $("sm-text").hidden = !slide.text;
  $("sm-caption").textContent = slide.caption || "";
  $("sm-caption").hidden = !slide.caption;
  const imgsEl = $("sm-imgs");
  if (images && images.length) {
    imgsEl.innerHTML = images.map((u) => `<img class="sm-img" src="${u}" loading="lazy">`).join("");
    wireImageZoom(imgsEl);
  } else {
    imgsEl.innerHTML = "";
  }
  $("slide-modal").hidden = false;
  document.body.style.overflow = "hidden";
}

function closeSlideModal() {
  $("slide-modal").hidden = true;
  document.body.style.overflow = "";
}

$("sm-close").addEventListener("click", closeSlideModal);
$("slide-modal").addEventListener("click", (e) => {
  if (e.target === $("slide-modal")) closeSlideModal();
});

// ---------- Image zoom (drill explanation / review / slide modal) ----------
function wireImageZoom(container, imgNodes) {
  const imgs = imgNodes !== undefined ? [...imgNodes] : [...container.querySelectorAll(".src-img, .sm-img")];
  imgs.forEach((img) => {
    img.addEventListener("click", () => {
      const all = [...container.querySelectorAll(".src-img, .sm-img")]
        .map((i) => i.getAttribute("src") || i.src);
      lbList = all;
      lbIndex = Math.max(0, all.indexOf(img.getAttribute("src") || img.src));
      showLightbox();
    });
  });
}

// ---------- Markdown (minimal) ----------
function parseSlideList(s) {
  // handle "6, 7, 8" and "6-9" and mixed
  const out = [];
  for (const part of String(s).split(",")) {
    const m = part.trim().match(/^(\d+)(?:\s*[-–]\s*(\d+))?$/);
    if (!m) continue;
    const a = parseInt(m[1], 10);
    if (m[2]) {
      const b = parseInt(m[2], 10);
      for (let i = a; i <= b; i++) out.push(i);
    } else {
      out.push(a);
    }
  }
  return out;
}

function marked(md) {
  let out = escapeHtml(md);
  // code spans
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  // slide citations: (slide 6) / (slides 6, 7, 8) / (slides 6-9)
  out = out.replace(/\(slides?\s+([^)]+)\)/gi, (m, nums) => {
    const slides = parseSlideList(nums);
    if (!slides.length) return m;
    const links = slides.map((n) =>
      `<a href="#" class="slide-cite" data-slide="${n}">slide ${n}</a>`
    ).join(", ");
    return `<span class="slide-cites">(${links})</span>`;
  });
  // bold/italic
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // headings
  out = out.replace(/^### (.*)$/gm, "<h3>$1</h3>");
  out = out.replace(/^## (.*)$/gm, "<h2>$1</h2>");
  out = out.replace(/^# (.*)$/gm, "<h1>$1</h1>");
  // tables (simple pipe tables)
  out = out.replace(/\n?(\|[^\n]+\|)\n\|[^\n]+\|/g, (m) => m.replace(/\n/g, "\n").replace(/\|[-: ]+\|/g, "|"));
  const tableBlock = /((?:\|[^\n]+\|\n){2,})/g;
  out = out.replace(tableBlock, (m) => {
    const lines = m.trim().split("\n").filter((l) => !/^\|[\s|:|-]+\|$/.test(l));
    const header = lines[0];
    const rows = lines.slice(1);
    return "<table><thead><tr>" + header.split("|").filter((c) => c.trim()).map((c) => `<th>${c.trim()}</th>`).join("") + "</tr></thead><tbody>" +
      rows.map((r) => "<tr>" + r.split("|").filter((c) => c.trim()).map((c) => `<td>${c.trim()}</td>`).join("") + "</tr>").join("") + "</tbody></table>";
  });
  // lists
  out = out.replace(/^[-*] (.*)$/gm, "<li>$1</li>");
  out = out.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
  out = out.replace(/^(\d+)\. (.*)$/gm, "<li>$2</li>");
  // paragraphs
  out = out.replace(/\n\n+/g, "</p><p>");
  if (!out.startsWith("<")) out = "<p>" + out + "</p>";
  return out;
}

// ---------- Timer ----------
let timerInterval = null;
let timerSeconds = 0;
let timerCountingDown = false;

function startTimer() {
  stopTimer();
  timerCountingDown = false;
  timerSeconds = 0;
  $("session-timer").hidden = false;
  $("session-timer").classList.remove("timer-danger");
  renderTimer();
  timerInterval = setInterval(() => {
    timerSeconds += 1;
    renderTimer();
  }, 1000);
}

function startQuizCountdown(totalSeconds) {
  stopTimer();
  timerCountingDown = true;
  timerSeconds = Math.max(0, totalSeconds || 0);
  $("session-timer").hidden = false;
  $("session-timer").classList.remove("timer-danger");
  renderTimer();
  timerInterval = setInterval(() => {
    timerSeconds -= 1;
    renderTimer();
    if (timerSeconds <= 0) {
      stopTimer();
      onQuizTimeout();
    }
  }, 1000);
}

function stopTimer() {
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
  const el = $("session-timer");
  if (el) el.hidden = true;
}

function renderTimer() {
  const el = $("session-timer");
  if (!el) return;
  const s = Math.max(0, timerSeconds);
  const m = Math.floor(s / 60);
  const sec = s % 60;
  el.textContent = (timerCountingDown ? "− " : "") +
    `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  if (timerCountingDown) {
    el.classList.toggle("timer-danger", s < 60);
  }
}

// ---------- Drill ----------
let currentQid = null;

async function loadDrill() {
  stopTimer();
  $("drill-setup").hidden = false;
  $("drill-active").hidden = true;
  $("review-results").hidden = true;
  $("review-results").innerHTML = "";

  const modeSel = $("session-mode");
  const toggleCount = () => {
    $("count-wrap").style.display = modeSel.value === "practice" ? "" : "none";
  };
  modeSel.addEventListener("change", toggleCount);
  toggleCount();

  // time-mode segmented toggle
  const segBtns = [...$("time-mode-seg").querySelectorAll("button")];
  if (!state.segAttached) {
    state.segAttached = true;
    segBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        segBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const mode = btn.dataset.timeMode;
        $("time-mode-hint").querySelectorAll("span").forEach((s) => {
          s.hidden = s.dataset.hint !== mode;
        });
      });
    });
  }

  // populate lecture checkboxes
  const lectures = await fetch("/api/lectures").then((r) => r.json());
  const list = $("lecture-list");
  list.innerHTML = "";
  for (const l of lectures) {
    const lb = document.createElement("label");
    lb.className = "lp-item";
    lb.innerHTML = `<input type="checkbox" value="${l.id}" class="lp-check" checked> <span>${escapeHtml(l.title)}</span>`;
    list.appendChild(lb);
  }
  const allCheck = $("lecture-all");
  const toggleAll = () => {
    list.querySelectorAll(".lp-check").forEach((c) => {
      c.checked = allCheck.checked;
      c.disabled = allCheck.checked;
    });
  };
  allCheck.checked = true;
  allCheck.addEventListener("change", toggleAll);
  toggleAll();

  // missed summary panel
  await refreshMissedSummary();

  // past sessions panel
  await renderSessionHistory();
}

async function renderSessionHistory() {
  const el = $("session-history");
  let sessions = [];
  try {
    sessions = await fetch("/api/sessions").then((r) => r.json());
  } catch (e) {
    sessions = [];
  }
  if (!sessions.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = "<div class='due-total' style='margin-top:14px'>Past sessions</div>";
  const rows = sessions.map((s) => {
    const label = s.mode === "review" ? "Review" : "Practice";
    const timeMode = s.tutor_mode ? "tutor" : "quiz";
    const status = s.status === "completed" ? "done" : "in progress";
    const btn = s.status === "completed"
      ? `<button class="btn small ghost" data-act="review" data-id="${s.id}">Review</button>`
      : `<button class="btn small" data-act="resume" data-id="${s.id}">Resume</button>`;
    return `
      <div class="hist-row">
        <div class="hist-info">
          <span class="w-title">${escapeHtml(s.title)}</span>
          <span class="muted"> · ${label} · ${timeMode} · ${status}</span><br>
          <span class="muted">${s.completed_count}/${s.target_count} answered</span>
        </div>
        <div class="hist-actions">
          ${btn}
          <button class="hist-del" data-act="delete" data-id="${s.id}"
                  title="Remove from history">&times;</button>
        </div>
      </div>`;
  }).join("");
  el.innerHTML += `<div class="hist-list">${rows}</div>`;
  el.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.id, 10);
      if (btn.dataset.act === "resume") return startExistingSession(id);
      if (btn.dataset.act === "review") return startCompletedSessionReview(id);

      const row = btn.closest(".hist-row");
      const name = row.querySelector(".w-title").textContent;
      if (!confirm(
        `Remove "${name}" from your session history?\n\n` +
        `Your answers and missed questions are kept — only the history entry goes.`
      )) return;
      const res = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
      if (!res.ok) return toast("Could not remove that session");
      toast("Session removed");
      renderSessionHistory();
    });
  });
}

async function refreshMissedSummary() {
  const panel = $("missed-summary");
  if (!panel) return;
  const m = await fetch("/api/missed").then((r) => r.json());
  if (m.count === 0) {
    panel.innerHTML = '<div class="muted">No missed questions to review.</div>';
    return;
  }
  const rows = (m.by_lecture || [])
    .map((d) => `<li><span class="w-title">${escapeHtml(d.title)}</span> <span class="due-badge has-due">${d.c} missed</span></li>`)
    .join("");
  panel.innerHTML = `
    <div class="due-total"><span class="stat">${m.count}</span> questions to review</div>
    ${rows ? `<ul class="detail-list due-list">${rows}</ul>` : ""}`;
}

$("start-session").addEventListener("click", async () => {
  const mode = $("session-mode").value;
  const target = parseInt($("session-target").value, 10) || 20;
  const timeMode = $("time-mode-seg").querySelector("button.active").dataset.timeMode;
  const fd = new FormData();
  fd.append("mode", mode);
  fd.append("target", target);
  fd.append("time_mode", timeMode);
  const allChecked = $("lecture-all").checked;
  if (!allChecked) {
    const checked = [...document.querySelectorAll(".lp-check:checked")].map((c) => c.value);
    checked.forEach((id) => fd.append("lecture_ids", id));
  }
  const statusEl = $("gen-status");
  statusEl.textContent = mode === "practice" ? "Generating fresh questions... (this may take ~30-60s)" : "Loading missed questions...";
  const res = await fetch("/api/sessions", { method: "POST", body: fd }).then((r) => r.json());
  statusEl.textContent = "";
  if (!res.question_count) {
    toast(res.message || "No questions available for this mode/source.");
    return;
  }
  state.sessionId = res.session_id;
  startExistingSession(res.session_id);
});

async function startExistingSession(sid) {
  state.sessionId = sid;
  $("drill-setup").hidden = true;
  $("drill-active").hidden = false;
  $("review-results").hidden = true;
  $("review-results").innerHTML = "";
  stopTimer();
  state.quizActive = false;
  try {
    const sess = await fetch(`/api/sessions/${sid}`).then((r) => r.json());
    state.tutorMode = sess.session.tutor_mode !== 0;
    state.quizTotal = (sess.session.time_limit_min || 0) * 60;
    if (!state.tutorMode) {
      const elapsed = sess.session.elapsed_sec || 0;
      state.quizActive = true;
      startQuizCountdown(Math.max(0, state.quizTotal - elapsed));
    }
  } catch (e) {
    state.tutorMode = true;
    state.quizTotal = 0;
  }
  loadNextQuestion();
}

async function pauseActiveQuiz() {
  if (!state.quizActive || !state.sessionId || state.tutorMode) return;
  const elapsed = Math.max(0, state.quizTotal - timerSeconds);
  state.quizActive = false;
  stopTimer();
  try {
    await fetch(`/api/sessions/${state.sessionId}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ elapsed_sec: Math.round(elapsed) }),
    });
  } catch (e) {
    console.error("pause failed", e);
  }
}

async function startCompletedSessionReview(sid) {
  state.sessionId = sid;
  $("drill-setup").hidden = true;
  $("drill-active").hidden = false;
  stopTimer();
  $("question-card").innerHTML = "";
  $("review-results").hidden = true;
  await showReviewResults();
}

async function onQuizTimeout() {
  stopTimer();
  toast("Time's up — grading unanswered questions as incorrect");
  try {
    await fetch(`/api/sessions/${state.sessionId}/timeout`, { method: "POST" });
  } catch (e) {
    console.error("timeout grading failed", e);
  }
  await showReviewResults();
}

// The numbered strip under the question. Click a number to jump back and change an
// answer — a misclick shouldn't be permanent.
async function renderQuestionNav(activeQid) {
  const el = $("question-nav");
  if (!el) return;
  let nav = [];
  try {
    nav = await fetch(`/api/sessions/${state.sessionId}/nav`).then((r) => r.json());
  } catch (e) {
    el.innerHTML = "";
    return;
  }
  state.navCount = nav.length;
  el.innerHTML = nav.map((n) => {
    const classes = ["qnav-btn"];
    if (n.question_id === activeQid) classes.push("current");
    if (n.answered) {
      // Correctness is a spoiler in quiz mode, so it stays hidden until the end.
      classes.push(state.tutorMode ? (n.correct ? "ok" : "bad") : "done");
    }
    return `<button class="${classes.join(" ")}" data-qid="${n.question_id}"
              title="Question ${n.position + 1}">${n.position + 1}</button>`;
  }).join("");
  el.querySelectorAll(".qnav-btn").forEach((b) => {
    // Clicking the current number reloads it — a cheap "reset this question" that also
    // means the strip never has a dead button.
    b.addEventListener("click", () => loadQuestion(parseInt(b.dataset.qid, 10)));
  });
}

async function loadNextQuestion() {
  const res = await fetch(`/api/sessions/${state.sessionId}/next`).then((r) => r.json());
  if (res.done) {
    showReviewResults();
    return;
  }
  renderQuestion(res.question);
}

async function loadQuestion(qid) {
  const res = await fetch(`/api/sessions/${state.sessionId}/question/${qid}`)
    .then((r) => r.json());
  if (!res.question) return toast("Could not load that question");
  renderQuestion(res.question);
}

async function renderQuestion(q) {
  if (state.tutorMode) startTimer();
  const card = $("question-card");
  currentQid = q.id;
  state.currentImages = q.source_images || [];
  state.currentSlide = q.source_slide || null;
  state.currentSource = q.question_source || "";

  const answeredBefore = q.prior_selected_index !== null && q.prior_selected_index !== undefined;
  state.optionSelected = false;
  state.pendingIndex = answeredBefore ? q.prior_selected_index : null;
  state.revealed = false;

  const opts = q.options.map((o, i) => {
    const letter = String.fromCharCode(65 + i);
    const sel = i === q.prior_selected_index ? " selected" : "";
    return `<button class="option${sel}" data-idx="${i}" data-correct="${q.correct_index === i ? 1 : 0}">
      <strong>${letter}.</strong> ${escapeHtml(o)}
    </button>`;
  }).join("");

  const prog = $("session-progress");
  const sess = await fetch(`/api/sessions/${state.sessionId}`).then((r) => r.json());
  prog.textContent = `${sess.session.completed_count} of ${sess.session.target_count} answered` +
    ` · ${state.tutorMode ? "Tutor" : "Quiz"}`;

  card.innerHTML = `
    <div class="question-box">
      <div class="q-stem">${escapeHtml(q.question)}</div>
      ${opts}
      <div class="explanation" hidden>
        <h4>Explanation</h4>
        <p id="explain-text"></p>
        <div id="source-images"></div>
      </div>
      <div class="quiz-nav">
        <button class="btn" id="submit-q" ${answeredBefore ? "" : "disabled"}
        >${answeredBefore ? "Submit change" : "Submit"}</button>
        <button class="btn ghost" id="next-q">Next</button>
      </div>
      <div class="qnav-wrap"><div id="question-nav" class="qnav"></div></div>
    </div>`;

  const submitBtn = card.querySelector("#submit-q");
  const nextBtn = card.querySelector("#next-q");
  const options = card.querySelectorAll(".option");
  const expl = card.querySelector(".explanation");

  // Two display states. Whenever feedback is on screen the options are locked, and the
  // button becomes "Change answer" — that button is the ONLY way back to editing, so
  // the graded view can never become a dead end you have to navigate away from.
  function showFeedback(selectedIdx, wasCorrect, explanation) {
    state.revealed = true;
    stopTimer();
    options.forEach((b) => {
      b.disabled = true;
      b.classList.remove("wrong");
      if (b.dataset.correct === "1") b.classList.add("correct");
    });
    if (!wasCorrect) {
      const chosen = card.querySelector(`.option[data-idx="${selectedIdx}"]`);
      if (chosen) chosen.classList.add("wrong");
    }
    showExplanation(explanation);
    submitBtn.textContent = "Change answer";
    submitBtn.disabled = false;
  }

  function enableEditing() {
    state.revealed = false;
    options.forEach((b) => {
      b.disabled = false;
      b.classList.remove("correct", "wrong");
    });
    if (expl) expl.hidden = true;
    submitBtn.textContent = "Submit change";
    submitBtn.disabled = state.pendingIndex === null;
  }

  // Selecting only highlights. Nothing is graded or recorded until Submit, so a
  // misclick costs nothing.
  options.forEach((btn) => {
    btn.addEventListener("click", () => {
      if (state.revealed) return;
      options.forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      state.pendingIndex = parseInt(btn.dataset.idx, 10);
      state.optionSelected = true;
      submitBtn.disabled = false;
    });
  });

  submitBtn.addEventListener("click", async () => {
    // In the graded state this button unlocks the options instead of submitting.
    if (state.revealed) return enableEditing();
    if (state.pendingIndex === null) return;
    submitBtn.disabled = true;
    const chosen = card.querySelector(`.option[data-idx="${state.pendingIndex}"]`);
    const correct = chosen.dataset.correct === "1";
    const res = await submitAnswer(q.id, state.pendingIndex, correct, state.tutorMode);
    if (!res) {
      submitBtn.disabled = false;   // save failed; let them try again
      return;
    }
    if (state.tutorMode) showFeedback(state.pendingIndex, correct, res.explanation);
    await renderQuestionNav(q.id);
    if (!state.tutorMode) loadNextQuestion();
  });

  nextBtn.addEventListener("click", () => loadNextQuestion());

  // Revisiting something you already answered lands in the same graded state as if you
  // had just submitted it — including the "Change answer" way back out.
  if (answeredBefore && state.tutorMode) {
    showFeedback(q.prior_selected_index, q.prior_correct, q.explanation);
  }

  await renderQuestionNav(q.id);
}

// POSTs the answer and reports the outcome. Returns the server response, or null if
// the save failed — the caller only shows feedback for an answer that was recorded.
async function submitAnswer(qid, selectedIndex, correct, reveal) {
  let res;
  try {
    const r = await fetch(`/api/sessions/${state.sessionId}/answer/${qid}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_index: selectedIndex }),
    });
    if (!r.ok) throw new Error(`server returned ${r.status}`);
    res = await r.json();
  } catch (e) {
    // Silently swallowing this made a failed save look like a dead Next button:
    // the answer was never recorded, so Next re-served the same question forever.
    console.error("answer failed", e);
    toast(`Answer not saved (${e.message}). Check the server log.`);
    return null;
  }
  if (reveal) {
    if (res.revised) toast(correct ? "Updated — now correct" : "Updated — still incorrect");
    else if (!correct) toast("Missed — added to tomorrow's review");
    else toast("Correct");
  }
  return res;
}

// Renders the explanation + source block. Used both after submitting and when you
// revisit a question you already answered.
function showExplanation(explanation) {
  const exp = $("explain-text");
  if (!exp) return;
  exp.innerHTML = marked(explanation || "No explanation provided.");
  const srcImg = $("source-images");
  const prof = professorBadge(state.currentSource);
  if (state.currentImages && state.currentImages.length) {
    const cite = citationHTML(state.currentSlide);
    srcImg.innerHTML = `<h4 class="src-title">Source material <span class="muted">(click to zoom)</span></h4>` +
      state.currentImages.map((u) => `<img class="src-img" src="${u}" loading="lazy">`).join("") +
      cite + prof;
    wireImageZoom(srcImg);
  } else if (state.currentSlide || prof) {
    const cite = citationHTML(state.currentSlide);
    srcImg.innerHTML = cite || prof ? `<h4 class="src-title">Source</h4>${cite}${prof}` : "";
  } else {
    srcImg.innerHTML = "";
  }
  $("question-card").querySelector(".explanation").hidden = false;
}

function citationHTML(slide) {
  if (!slide) return "";
  const title = (slide.lecture_title || "").trim();
  const num = slide.slide_num;
  if (!title && !num) return "";
  const label = `Source: ${title || "Lecture"}${num ? ` · Slide ${num}` : ""}`;
  const cap = (slide.caption || "").trim();
  // When we know which lecture and slide, the citation becomes a footnote you can
  // hover to preview and click to expand in place.
  if (num && slide.lecture_id) {
    return `<div class="src-cite">` +
      `<a href="#" class="slide-cite" data-slide="${num}" data-lecture-id="${slide.lecture_id}">` +
      `${escapeHtml(label)}</a>` +
      `${cap ? `<br><span class="muted">${escapeHtml(cap)}</span>` : ""}</div>`;
  }
  return `<div class="src-cite">${escapeHtml(label)}${cap ? `<br><span class="muted">${escapeHtml(cap)}</span>` : ""}</div>`;
}

async function showReviewResults() {
  stopTimer();
  state.quizActive = false;
  const card = $("question-card");
  const review = $("review-results");
  review.hidden = false;
  const data = await fetch(`/api/sessions/${state.sessionId}/review`).then((r) => r.json());
  const qs = data.questions;
  const isCorrectQ = (q) => {
    if (q.selected_index === -1) return false;
    if (q.selected_index !== null && q.selected_index !== undefined) return q.selected_index === q.correct_index;
    return q.selected_correct === 1; // legacy answer (before selected_index tracking)
  };
  const correctCount = qs.filter(isCorrectQ).length;
  const timedOut = qs.filter((q) => q.selected_index === -1).length;
  state.reviewSlides = qs.map((q) => ({
    slide_num: q.slide_num,
    text: q.slide_text || "",
    caption: q.slide_caption || "",
    images: q.source_images || [],
  }));
  card.innerHTML = `
    <div class="session-done">
      <h2>Session complete</h2>
      <p class="muted">${correctCount}/${qs.length} correct${timedOut ? ` · ${timedOut} timed out` : ""}. Review below.</p>
    </div>`;
  review.innerHTML = `
    <h3>Review</h3>
    <div class="review-list">
      ${qs.map((q, i) => {
        const userPick = q.selected_index;
        const timedOut = userPick === -1;
        const isCorrect = isCorrectQ(q);
        const tag = timedOut
          ? '<span class="rev-tag rev-tag-time">Time expired</span>'
          : (isCorrect ? '<span class="rev-tag rev-tag-good">Correct</span>' : '<span class="rev-tag rev-tag-bad">Incorrect</span>');
        const imgs = (q.source_images || []).length
          ? `<div class="review-imgs">${q.source_images.map((u) => `<img class="src-img" src="${u}" loading="lazy">`).join("")}` +
            citationHTML({ lecture_id: q.lecture_id, lecture_title: q.lecture_title, slide_num: q.slide_num, caption: q.slide_caption }) + `</div>`
          : (citationHTML({ lecture_id: q.lecture_id, lecture_title: q.lecture_title, slide_num: q.slide_num, caption: q.slide_caption }) || "");
        return `
        <div class="review-item" data-qidx="${i}">
          <div class="review-q">
            <span class="review-num">${i + 1}.</span>
            <span class="q-stem">${escapeHtml(q.question)}</span>
            ${tag}
          </div>
          <div class="review-opts">
            ${q.options.map((o, oi) => {
              let cls = "rev-opt";
              if (oi === q.correct_index) cls = "rev-opt-correct";
              else if (oi === userPick) cls = "rev-opt-wrong";
              const mark = oi === q.correct_index ? " ✓" : (oi === userPick ? " ✗" : "");
              return `<div class="${cls}">${String.fromCharCode(65 + oi)}. ${escapeHtml(o)}${mark}</div>`;
            }).join("")}
          </div>
          <div class="review-explain"><strong>Explanation:</strong> ${marked(q.explanation || "None")}</div>
          ${imgs}
          ${professorBadge(q.question_source)}
        </div>`;
      }).join("")}
    </div>
    <div id="session-recommendations" class="rec-wrap"></div>
    <div style="margin-top:16px"><button class="btn" id="review-back">Back to setup</button></div>`;
  review.querySelectorAll(".review-item").forEach((item) => {
    wireImageZoom(item, item.querySelectorAll(".src-img"));
  });
  $("review-back").addEventListener("click", loadDrill);
  await refreshMissedSummary();
  // Only worth an API call once something was actually missed.
  const missedAny = qs.some((q) => !isCorrectQ(q));
  if (missedAny) {
    await renderSessionRecommendations(state.sessionId, $("session-recommendations"));
  }
}

$("quit-session").addEventListener("click", () => {
  pauseActiveQuiz();
  stopTimer();
  toast("Session paused. You can resume it from the Drill tab.");
  loadDrill();
});

// ---------- Progress ----------
async function loadProgress() {
  loadSavedRecommendations();
  const body = $("progress-body");
  const d = await fetch("/api/dashboard").then((r) => r.json());
  const lect = await fetch("/api/lectures").then((r) => r.json());
  body.innerHTML = "<div class='panel'><h3>Progress overview</h3></div>";
  body.innerHTML += `
    <div class="cards">
      <div class="card"><div class="stat">${d.question_count}</div><div class="label">Questions generated</div></div>
      <div class="card"><div class="stat">${d.missed_count}</div><div class="label">Missed to review</div></div>
      <div class="card"><div class="stat">${d.sessions_today}</div><div class="label">Sessions today</div></div>
      <div class="card"><div class="stat">${d.coverage.total}</div><div class="label">Slides total</div></div>
    </div>`;

  // Missed questions per lecture, for sorting
  const missedSum = await fetch("/api/missed").then((r) => r.json());
  const missedByLecture = {};
  (missedSum.by_lecture || []).forEach((x) => (missedByLecture[x.lecture_id] = x.c));

  // Slide coverage per lecture for sorting (from dashboard lecture_stats)
  const covByLecture = {};
  (d.lectures || []).forEach((x) => (covByLecture[x.lecture_id] = x.coverage_pct || 0));

  // Sort lectures: most missed first, then least covered
  lect.sort((a, b) => {
    const mb = (missedByLecture[b.id] || 0) - (missedByLecture[a.id] || 0);
    if (mb !== 0) return mb;
    return (covByLecture[a.id] || 0) - (covByLecture[b.id] || 0);
  });

  for (const l of lect) {
    const missedN = missedByLecture[l.id] || 0;
    const qs = await fetch(`/api/lectures/${l.id}/questions`).then((r) => r.json());
    const slides = await fetch(`/api/lectures/${l.id}/slides_progress`).then((r) => r.json());
    const slideRows = slides.map((s) => {
      const acc = s.accuracy === null || s.accuracy === undefined
        ? '<span class="muted">not reviewed</span>'
        : `<span class="acc-badge ${accClass(s.accuracy)}">${pct(s.accuracy)}</span> <span class="muted">(${s.correct_count}/${s.correct_count + s.wrong_count})</span>`;
      const qInfo = `${s.q_count} Q`;
      return `
        <div class="concept-row">
          <span class="concept-label" title="${escapeHtml(s.text)}">Slide ${s.slide_num}: ${escapeHtml(shortLabel(s.text, 70))}</span>
          <span class="concept-meta">${qInfo} · ${acc}</span>
        </div>`;
    }).join("");
    body.innerHTML += `
      <div class="lecture-row" style="cursor:default">
        <div>
          <div class="title">${escapeHtml(l.title)}</div>
          <div class="meta">${l.slide_count} slides · ${l.word_count} words</div>
        </div>
        <div class="meta">
          <span class="due-badge ${missedN ? "has-due" : ""}">${missedN} missed</span>
          · <span class="mini-bar"><span class="mini-fill" style="width:${covByLecture[l.id] || 0}%"></span></span>
          ${covByLecture[l.id] || 0}% covered · ${qs.length} questions generated
        </div>
      </div>
      <div class="concept-list">${slideRows}</div>`;
  }
}

// ---------- Helpers ----------
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------- Mastery projection ----------
async function loadProjection() {
  const el = $("projection-body");
  if (!el) return;
  let p;
  try {
    p = await fetch("/api/mastery_projection").then((r) => r.json());
  } catch (e) {
    el.textContent = "Could not calculate.";
    return;
  }
  if (!p.slides_total) {
    el.innerHTML = `<span class="muted">Import a lecture to see a projection.</span>`;
    return;
  }
  if (p.slides_mastered >= p.slides_total) {
    el.innerHTML = `<div class="proj-headline">All ${p.slides_total} slides mastered.</div>`;
    return;
  }
  const acc = p.accuracy_is_assumed
    ? `assuming ${Math.round(p.accuracy * 100)}% accuracy until you answer some`
    : `at your current ${Math.round(p.accuracy * 100)}% accuracy`;

  const rows = p.milestones.map((m) => {
    if (m.already_there) {
      return `<tr class="proj-done"><td>${m.pct}% mastery</td><td colspan="2">reached</td></tr>`;
    }
    if (!m.reachable) {
      return `<tr><td>${m.pct}% mastery</td><td class="muted" colspan="2">not reachable at this accuracy</td></tr>`;
    }
    return `<tr${m.pct === p.target_pct ? ' class="proj-target"' : ""}>
      <td>${m.pct}% mastery</td>
      <td><strong>${m.questions}</strong> questions</td>
      <td class="muted">${m.sessions} session${m.sessions === 1 ? "" : "s"}</td>
    </tr>`;
  }).join("");

  el.innerHTML = `
    <div class="proj-headline">~${p.questions_remaining} more questions to ${p.target_pct}%</div>
    <div class="proj-sub">
      ≈ ${p.sessions_remaining} session${p.sessions_remaining === 1 ? "" : "s"} ·
      likely range ${p.questions_p25}–${p.questions_p75} · ${escapeHtml(acc)}
    </div>
    <div class="proj-bar"><span style="width:${p.mastery_pct}%"></span></div>
    <div class="proj-sub">
      ${p.slides_mastered} of ${p.slides_total} slides mastered (${p.mastery_pct}%) —
      a slide counts once you've answered it right at least as often as you've missed it.
    </div>
    <table class="proj-table">${rows}</table>
    <div class="proj-note muted">
      Simulated ${p.trials}× through the app's real slide-picking, session by session.
      The last few percent cost far more than the first — random draws have to land on
      exactly the slides you still owe — which is why the headline targets ${p.target_pct}%.
    </div>`;
}

// ---------- Study recommendations ----------
function recommendationHTML(rec, opts = {}) {
  if (!rec) return "";
  const themes = (rec.themes || []).map((t) => `
    <li class="rec-theme">
      <div class="rec-topic">${escapeHtml(t.topic)}</div>
      ${t.why ? `<div class="rec-why">${escapeHtml(t.why)}</div>` : ""}
      ${t.action ? `<div class="rec-action">→ ${escapeHtml(t.action)}</div>` : ""}
    </li>`).join("");
  const head = opts.title
    ? `<div class="rec-head">${escapeHtml(opts.title)}
         <span class="muted">· ${rec.mistake_count} missed · ${escapeHtml((rec.created_at || "").slice(0, 10))}</span>
       </div>`
    : "";
  return `<div class="rec-card">
    ${head}
    ${rec.summary ? `<div class="rec-summary">${escapeHtml(rec.summary)}</div>` : ""}
    ${themes ? `<ul class="rec-list">${themes}</ul>` : ""}
  </div>`;
}

async function renderSessionRecommendations(sid, container) {
  container.innerHTML = `<div class="muted">Looking for patterns in your mistakes…</div>`;
  let res;
  try {
    // Cached in the database after the first call, so reopening a review is free.
    res = await fetch(`/api/sessions/${sid}/recommendations`, { method: "POST" })
      .then((r) => r.json());
  } catch (e) {
    container.innerHTML = `<div class="muted">Could not generate recommendations.</div>`;
    return;
  }
  if (res.error) {
    container.innerHTML = `<div class="muted">Recommendations unavailable: ${escapeHtml(res.error)}</div>`;
    return;
  }
  if (!res.recommendations) {
    container.innerHTML = `<div class="muted">Nothing missed in this session — no recommendations needed.</div>`;
    return;
  }
  container.innerHTML = `<h3 class="rec-title">What to work on</h3>` +
    recommendationHTML(res.recommendations);
}

async function loadSavedRecommendations() {
  const el = $("saved-recommendations");
  if (!el) return;
  let list = [];
  try {
    list = await fetch("/api/recommendations").then((r) => r.json());
  } catch (e) {
    list = [];
  }
  if (!list.length) {
    el.innerHTML = `<div class="muted">No recommendations yet — finish a session with a few misses.</div>`;
    return;
  }
  el.innerHTML = list
    .map((r) => recommendationHTML(r, { title: r.session_title || `Session ${r.session_id}` }))
    .join("");
}

// ---------- API usage indicator ----------
function fmtTokens(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

async function refreshUsage() {
  try {
    const u = await fetch("/api/usage").then((r) => r.json());
    $("usage-model").textContent = u.model;
    $("usage-tokens").textContent = fmtTokens(u.today.total_tokens) + " today";
    $("usage-cache").textContent = u.today.cache_hit_pct
      ? u.today.cache_hit_pct + "% cached"
      : "";
    const kinds = u.today_by_kind
      .map((k) => `${k.kind || "other"}: ${fmtTokens(k.tokens)} (${k.calls} calls)`)
      .join("\n");
    $("usage-chip").title =
      `${u.provider} / ${u.model}\n` +
      `Today: ${u.today.calls} calls, ${fmtTokens(u.today.total_tokens)} tokens ` +
      `(${fmtTokens(u.today.cached_tokens)} cached)\n` +
      `All time: ${u.all_time.calls} calls, ${fmtTokens(u.all_time.total_tokens)} tokens` +
      (kinds ? `\n\n${kinds}` : "");
  } catch (e) {
    $("usage-tokens").textContent = "-";
  }
}

// ---------- Init ----------
initTheme();
initConfigGate().then(() => {
  if (state.configured) {
    loadDashboard();
    loadQuestionSetLectures();
  }
  refreshUsage();
  setInterval(refreshUsage, 60000);
});
