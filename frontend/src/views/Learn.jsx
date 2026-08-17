import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { SessionRow } from "../components/SessionRow";
import { notifyLecturesChanged, onLecturesChanged } from "../lib/events";

// ---------- Minimal markdown + slide-citation renderer ----------
// Backend summaries use: ## / ### headings, **bold**, plain paragraphs, and
// inline citations like "(slide 6)" / "(slides 6, 7)" / "(slides 6-9)".
// Citations render as clickable chips that expand the referenced slide inline
// (image + text), fetched lazily and cached by slide number.
function parseSlideList(s) {
  const out = [];
  for (const part of String(s).split(",")) {
    const m = part.trim().match(/^(\d+)(?:\s*[-–]\s*(\d+))?$/);
    if (!m) continue;
    const a = parseInt(m[1], 10);
    const b = m[2] ? parseInt(m[2], 10) : a;
    for (let i = a; i <= b; i++) out.push(i);
  }
  return out;
}

function renderInline(text, key, lectureId, openLightbox) {
  const parts = [];
  let i = 0;
  const citeRe = /\(slides?\s+([^)]+)\)/gi;
  let lastIndex = 0;
  let m;
  while ((m = citeRe.exec(text))) {
    if (m.index > lastIndex) parts.push(renderBold(text.slice(lastIndex, m.index), `${key}-t${i++}`));
    const nums = parseSlideList(m[1]);
    if (nums.length) {
      parts.push(
        <span key={`${key}-c${i++}`} className="slide-cites">
          ({nums.map((n, idx) => (
            <span key={n}>
              {idx > 0 && ", "}
              <SlideCite num={n} lectureId={lectureId} openLightbox={openLightbox} />
            </span>
          ))})
        </span>
      );
    } else {
      parts.push(m[0]);
    }
    lastIndex = citeRe.lastIndex;
  }
  if (lastIndex < text.length) parts.push(renderBold(text.slice(lastIndex), `${key}-t${i++}`));
  return parts;
}

function renderBold(text, key) {
  const segs = text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  if (segs.length === 1) return text;
  return (
    <span key={key}>
      {segs.map((s, idx) =>
        s.startsWith("**") && s.endsWith("**")
          ? <strong key={idx}>{s.slice(2, -2)}</strong>
          : <span key={idx}>{s}</span>
      )}
    </span>
  );
}

// Line-oriented block parser - some summaries put a blank line after a "##"
// header, some don't (the LLM isn't perfectly consistent about it). Splitting
// on blank-line runs alone glues an inconsistently-formatted header straight
// onto the paragraph that follows it, rendering the whole thing as one giant
// bolded heading. Classifying line-by-line instead makes headers, bullet
// lists, and tables render correctly no matter which convention was used.
function parseMarkdownBlocks(text) {
  const lines = (text || "").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const h = line.match(/^(#{1,3})\s+(.*)$/);
    if (h) {
      blocks.push({ type: "heading", level: h[1].length, text: h[2].trim() });
      i++;
      continue;
    }

    if (/^\|.*\|\s*$/.test(line)) {
      const rows = [];
      while (i < lines.length && /^\|.*\|\s*$/.test(lines[i])) { rows.push(lines[i]); i++; }
      blocks.push({ type: "table", rows });
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s+/, "").trim());
        i++;
      }
      blocks.push({ type: "list", items });
      continue;
    }

    const para = [];
    while (
      i < lines.length && lines[i].trim() &&
      !/^(#{1,3})\s+/.test(lines[i]) && !/^\|.*\|\s*$/.test(lines[i]) && !/^[-*]\s+/.test(lines[i])
    ) {
      para.push(lines[i].trim());
      i++;
    }
    blocks.push({ type: "para", text: para.join(" ") });
  }
  return blocks;
}

function parseTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}
function isTableSeparatorRow(cells) {
  return cells.every((c) => /^:?-{2,}:?$/.test(c));
}

function MarkdownTable({ rows, lectureId, openLightbox }) {
  const parsed = rows.map(parseTableRow);
  const header = parsed[0] || [];
  const body = parsed.slice(1).filter((r) => !isTableSeparatorRow(r));
  return (
    <div className="md-table-wrap">
      <table className="md-table">
        <thead>
          <tr>{header.map((c, i) => <th key={i}>{renderInline(c, `th${i}`, lectureId, openLightbox)}</th>)}</tr>
        </thead>
        <tbody>
          {body.map((r, ri) => (
            <tr key={ri}>{r.map((c, ci) => <td key={ci}>{renderInline(c, `td${ri}-${ci}`, lectureId, openLightbox)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Markdown({ text, lectureId, openLightbox }) {
  const blocks = useMemo(() => parseMarkdownBlocks(text), [text]);
  return (
    <>
      {blocks.map((b, i) => {
        if (b.type === "heading") {
          const Tag = b.level >= 3 ? "h4" : "h3";
          return <Tag key={i}>{b.text}</Tag>;
        }
        if (b.type === "list") {
          return (
            <ul key={i}>
              {b.items.map((it, j) => <li key={j}>{renderInline(it, `b${i}-${j}`, lectureId, openLightbox)}</li>)}
            </ul>
          );
        }
        if (b.type === "table") {
          return <MarkdownTable key={i} rows={b.rows} lectureId={lectureId} openLightbox={openLightbox} />;
        }
        return <p key={i}>{renderInline(b.text, `b${i}`, lectureId, openLightbox)}</p>;
      })}
    </>
  );
}

// ---------- Slide citation chip + inline expand panel ----------
const slideCache = new Map();
function fetchSlide(lectureId, num) {
  const key = `${lectureId}:${num}`;
  if (!slideCache.has(key)) {
    slideCache.set(key, api.get(`/api/lectures/${lectureId}/slide/${num}`).catch(() => null));
  }
  return slideCache.get(key);
}

function SlideCite({ num, lectureId, openLightbox }) {
  const [open, setOpen] = useState(false);
  const [slide, setSlide] = useState(undefined);

  async function toggle() {
    if (!open && slide === undefined) {
      setSlide(await fetchSlide(lectureId, num));
    }
    setOpen((o) => !o);
  }

  return (
    <span className="slide-cite-wrap">
      <button type="button" className={`slide-cite${open ? " open" : ""}`} onClick={toggle}>
        slide {num}
      </button>
      {open && (
        <span className="cite-slide">
          {slide === undefined ? (
            <span className="muted">Loading slide {num}…</span>
          ) : slide === null ? (
            <span className="muted">Slide not found.</span>
          ) : (
            <>
              <span className="cite-slide-head muted">Slide {slide.slide_num}</span>
              {slide.images?.length > 0 && (
                <span className="cite-imgs">
                  {slide.images.map((u) => (
                    <img
                      key={u}
                      className="cite-img"
                      src={u}
                      alt=""
                      onClick={() => openLightbox(slide.images, slide.images.indexOf(u))}
                    />
                  ))}
                </span>
              )}
              {slide.text && <span className="cite-text">{slide.text}</span>}
              {slide.caption && <span className="cite-caption muted">{slide.caption}</span>}
            </>
          )}
        </span>
      )}
    </span>
  );
}

// ---------- Lightbox ----------
function Lightbox({ list, index, onClose, onNav }) {
  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") onNav(-1);
      if (e.key === "ArrowRight") onNav(1);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose, onNav]);

  if (!list?.length) return null;
  return (
    <div className="lightbox" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <button className="lb-close" onClick={onClose}>×</button>
      {list.length > 1 && <button className="lb-nav lb-prev" onClick={() => onNav(-1)}>‹</button>}
      <img className="lb-img" src={list[index]} alt="" />
      {list.length > 1 && <button className="lb-nav lb-next" onClick={() => onNav(1)}>›</button>}
      <div className="lb-caption">Slide {index + 1} of {list.length}</div>
    </div>
  );
}

function shortLabel(s, n = 60) {
  s = s || "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// ---------- Lecture list (grouped by week, tagged by course strand) ----------
const TAG_LABELS = { foundations: "Foundations", doctoring: "Doctoring", anatomy: "Anatomy" };
const TAG_OPTIONS = Object.entries(TAG_LABELS).map(([value, label]) => ({ value, label }));

// Click a tag to edit it in place (no navigation into the lecture) - a native
// select/number input replaces the pill until it blurs or commits.
function EditableTagPill({ value, onSave }) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <select
        autoFocus
        className="tag-pill tag-edit"
        value={value || "foundations"}
        onClick={(e) => e.stopPropagation()}
        onBlur={() => setEditing(false)}
        onChange={async (e) => { await onSave(e.target.value); setEditing(false); }}
      >
        {TAG_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    );
  }
  return (
    <button
      type="button"
      className={`tag-pill tag-${value || "foundations"}`}
      onClick={(e) => { e.stopPropagation(); setEditing(true); }}
    >
      {TAG_LABELS[value] || TAG_LABELS.foundations}
    </button>
  );
}

function EditableWeekPill({ week, onSave }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(week || "");

  async function commit() {
    setEditing(false);
    const n = val === "" ? null : parseInt(val, 10);
    if (n !== (week || null)) await onSave(n);
  }

  if (editing) {
    return (
      <input
        autoFocus
        type="number"
        min={1}
        max={52}
        className="tag-pill tag-week tag-edit"
        value={val}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => setVal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") setEditing(false); }}
      />
    );
  }
  return (
    <button
      type="button"
      className="tag-pill tag-week"
      onClick={(e) => { e.stopPropagation(); setVal(week || ""); setEditing(true); }}
    >
      {week ? `Week ${week}` : "Set week"}
    </button>
  );
}

// Click the title to rename in place - same click-to-edit pattern as the
// week/tag pills, just a text input instead of a select. stopPropagation
// keeps a click here from also triggering the row's "open this lecture" nav.
function EditableLectureTitle({ title, onSave }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(title);

  async function commit() {
    setEditing(false);
    const trimmed = val.trim();
    if (trimmed && trimmed !== title) await onSave(trimmed);
    else setVal(title);
  }

  if (editing) {
    return (
      <input
        autoFocus
        type="text"
        className="td-title td-title-edit"
        value={val}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => setVal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
          if (e.key === "Escape") { setVal(title); setEditing(false); }
        }}
      />
    );
  }
  return (
    <div
      className="td-title td-title-edit-trigger"
      onClick={(e) => { e.stopPropagation(); setVal(title); setEditing(true); }}
    >
      {title}
    </div>
  );
}

function LectureRow({ l, onPick, onUpdate }) {
  return (
    <li className="lecture-row lecture-row-tagged">
      <div className="lecture-tags">
        <EditableWeekPill week={l.week} onSave={(week) => onUpdate(l.id, { week })} />
        <EditableTagPill value={l.tag} onSave={(tag) => onUpdate(l.id, { tag })} />
      </div>
      <div className="lecture-main" onClick={() => onPick(l.id)}>
        <EditableLectureTitle title={l.title} onSave={(title) => onUpdate(l.id, { title })} />
        <div className="muted" style={{ fontSize: 12.5 }}>
          {l.slide_count} slides · {l.summary_status === "done" ? "summary ready" : l.summary_status}
        </div>
      </div>
    </li>
  );
}

function LectureList({ lectures, onPick, onUpdate }) {
  const groups = useMemo(() => {
    const byWeek = new Map();
    for (const l of lectures) {
      const key = l.week || null;
      if (!byWeek.has(key)) byWeek.set(key, []);
      byWeek.get(key).push(l);
    }
    const weeks = [...byWeek.keys()].filter((k) => k !== null).sort((a, b) => a - b);
    if (byWeek.has(null)) weeks.push(null);
    return weeks.map((w) => ({ week: w, items: byWeek.get(w) }));
  }, [lectures]);

  if (!lectures.length) {
    return <div className="muted">No lectures yet — add one from the Dashboard.</div>;
  }
  return (
    <div>
      {groups.map((g) => (
        <div key={g.week ?? "none"} className="lecture-group">
          <div className="lecture-group-head muted">{g.week ? `Week ${g.week}` : "No week set"}</div>
          <ul className="detail-list">
            {g.items.map((l) => <LectureRow key={l.id} l={l} onPick={onPick} onUpdate={onUpdate} />)}
          </ul>
        </div>
      ))}
    </div>
  );
}

// ---------- Lecture detail (summary + deck) ----------
function LectureView({ lecture, onBack }) {
  const [summary, setSummary] = useState(undefined);
  const [generating, setGenerating] = useState(false);
  const [deck, setDeck] = useState(null);
  const [lb, setLb] = useState(null); // { list, index }
  const openLightbox = (list, index) => setLb({ list, index });

  async function loadSummary() {
    setSummary(await api.get(`/api/lectures/${lecture.id}/summary`));
  }

  useEffect(() => {
    loadSummary();
    api.get(`/api/lectures/${lecture.id}/deck`).then(setDeck);
  }, [lecture.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function generate() {
    setGenerating(true);
    try {
      await api.post(`/api/lectures/${lecture.id}/summary/generate`);
      await loadSummary();
    } finally {
      setGenerating(false);
    }
  }

  const deckWithContent = (deck || []).filter((s) => (s.text || "").trim() || s.images?.length);

  return (
    <div>
      <button className="btn ghost sm" onClick={onBack}>← Lectures</button>

      <div className="card" style={{ marginTop: 12 }}>
        <h3>{lecture.title}</h3>
        {summary === undefined ? (
          <div className="muted">Loading summary…</div>
        ) : summary.status === "done" && summary.summary ? (
          <>
            <Markdown text={summary.summary.body} lectureId={lecture.id} openLightbox={openLightbox} />
            {(() => {
              let kps = [];
              try { kps = JSON.parse(summary.summary.key_points || "[]"); } catch { /* empty */ }
              return kps.length ? (
                <>
                  <h4>High-yield key points</h4>
                  <ul>
                    {kps.map((k, i) => <li key={i}>{renderInline(k, `kp${i}`, lecture.id, openLightbox)}</li>)}
                  </ul>
                </>
              ) : null;
            })()}
            <button className="btn ghost sm" disabled={generating} onClick={generate} style={{ marginTop: 8 }}>
              {generating ? "Regenerating…" : "Regenerate summary"}
            </button>
          </>
        ) : summary.status === "generating" ? (
          <div className="muted">Generating summary…</div>
        ) : (
          <button className="btn" disabled={generating} onClick={generate}>
            {generating ? "Generating…" : "Generate summary"}
          </button>
        )}
      </div>

      {deckWithContent.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>Slides</h3>
          <div className="deck">
            {deckWithContent.map((s) => (
              <div key={s.id} className="deck-slide">
                <div className="deck-num muted">Slide {s.slide_num}</div>
                {s.images?.length > 0 && (
                  <div className="deck-imgs">
                    {s.images.map((u, idx) => (
                      <img
                        key={u}
                        className="deck-img"
                        src={u}
                        alt=""
                        loading="lazy"
                        onClick={() => setLb({ list: s.images, index: idx })}
                      />
                    ))}
                  </div>
                )}
                {s.text && <div className="deck-text">{shortLabel(s.text, 400)}</div>}
                {s.caption && <div className="deck-caption muted">{s.caption}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {lb && (
        <Lightbox
          list={lb.list}
          index={lb.index}
          onClose={() => setLb(null)}
          onNav={(d) => setLb((cur) => ({ ...cur, index: (cur.index + d + cur.list.length) % cur.list.length }))}
        />
      )}
    </div>
  );
}

// ---------- Resume a session (in-progress subset, easy access from Learn) ----------
function InProgressSessions({ onOpenSession }) {
  const [sessions, setSessions] = useState(null);

  async function refresh() {
    const all = await api.get("/api/sessions");
    setSessions(all.filter((s) => s.status !== "completed"));
  }
  useEffect(() => { refresh(); }, []);

  async function handleDelete(id) {
    await api.del(`/api/sessions/${id}`);
    refresh();
  }

  if (!sessions || !sessions.length) return null;
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h3>Resume a session</h3>
      <ul className="detail-list">
        {sessions.map((s) => (
          <SessionRow key={s.id} s={s} onResume={onOpenSession} onDelete={handleDelete} />
        ))}
      </ul>
    </div>
  );
}

export function Learn({ onOpenSession }) {
  const [lectures, setLectures] = useState([]);
  const [lectureId, setLectureId] = useState(null);

  function refresh() { api.get("/api/lectures").then(setLectures); }
  useEffect(() => {
    refresh();
    // Dashboard's importer and this view's own rename/retag both change the
    // lecture list - refetch whenever either happens elsewhere too.
    return onLecturesChanged(refresh);
  }, []);

  async function updateLecture(id, patch) {
    const updated = await api.patch(`/api/lectures/${id}`, patch);
    setLectures((prev) => prev.map((l) => (l.id === id ? { ...l, ...updated } : l)));
    notifyLecturesChanged();
  }

  const lecture = lectures.find((l) => l.id === lectureId);

  return lecture ? (
    <LectureView lecture={lecture} onBack={() => setLectureId(null)} />
  ) : (
    <div>
      <div className="card">
        <h3>Lectures</h3>
        <LectureList lectures={lectures} onPick={setLectureId} onUpdate={updateLecture} />
      </div>
      <InProgressSessions onOpenSession={onOpenSession} />
    </div>
  );
}
