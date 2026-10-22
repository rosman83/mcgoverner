import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { weekText, weekLabel, weekCompare } from "../lib/format";
import { Pills } from "../components/Pills";
import { onLecturesChanged, notifySessionsChanged } from "../lib/events";

const MODE_OPTIONS = [
  { value: "practice", label: "Practice" },
  { value: "review", label: "Review missed" },
];
const TIME_OPTIONS = [
  { value: "tutor", label: "Tutor mode" },
  { value: "quiz", label: "Quiz mode" },
];
const TAG_OPTIONS = [
  { value: "", label: "All" },
  { value: "foundations", label: "Foundations" },
  { value: "doctoring", label: "Doctoring" },
  { value: "anatomy", label: "Anatomy" },
];

function fmtClock(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

// Small markdown-lite: bold, code spans, and "(slide N)" citations become
// clickable links that open the slide in a modal - explanations are LLM text,
// not raw HTML, so this renders to React nodes directly (no dangerouslySetInnerHTML).
function renderExplanation(text, lectureId, onCite) {
  if (!text) return <p className="muted">No explanation provided.</p>;
  return text.trim().split(/\n\n+/).map((para, pi) => (
    <p key={pi}>{renderInline(para, lectureId, onCite, pi)}</p>
  ));
}
function renderInline(text, lectureId, onCite, keyPrefix) {
  // Double-marker bold checked before single-marker italic, same reasoning
  // as Learn.jsx's renderBold - otherwise "**x**" matches as italic "*x*"
  // plus two stray asterisks instead of bold.
  const re = /(\*\*[^*]+\*\*)|(\*[^*]+\*)|(`[^`]+`)|(\(slides?\s+([\d,\s-]+)\))/g;
  const out = [];
  let last = 0, m, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<strong key={`${keyPrefix}-${i++}`}>{m[1].slice(2, -2)}</strong>);
    else if (m[2]) out.push(<em key={`${keyPrefix}-${i++}`}>{m[2].slice(1, -1)}</em>);
    else if (m[3]) out.push(<code key={`${keyPrefix}-${i++}`}>{m[3].slice(1, -1)}</code>);
    else if (m[4]) {
      const nums = (m[5].match(/\d+/g) || []).map(Number);
      out.push(
        <span key={`${keyPrefix}-${i++}`}>
          (
          {nums.map((n, ni) => (
            <span key={n}>
              {ni > 0 ? ", " : ""}
              <a href="#" className="slide-cite" onClick={(e) => { e.preventDefault(); onCite(lectureId, n); }}>
                slide {n}
              </a>
            </span>
          ))}
          )
        </span>
      );
    }
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function ProfessorBadge({ source }) {
  if (source !== "professor") return null;
  return <div className="prof-badge">★ Professor-written question — high yield</div>;
}

function SourceBlock({ images, slide, source, onCite }) {
  const cite = slide && (slide.lecture_title || slide.slide_num) ? (
    <div className="src-cite">
      <a href="#" className="slide-cite" onClick={(e) => { e.preventDefault(); onCite(slide.lecture_id, slide.slide_num); }}>
        Source: {slide.lecture_title || "Lecture"}{slide.slide_num ? ` · Slide ${slide.slide_num}` : ""}
      </a>
      {slide.caption && <div className="muted">{slide.caption}</div>}
    </div>
  ) : null;
  if (!images?.length && !cite && source !== "professor") return null;
  return (
    <div style={{ marginTop: 10 }}>
      {images?.length > 0 && (
        <div className="src-imgs">
          {images.map((u) => <img key={u} className="slide-thumb-lg" src={u} alt={slide?.caption || "Lecture slide"} />)}
        </div>
      )}
      {cite}
      <ProfessorBadge source={source} />
    </div>
  );
}

function SlideModal({ lectureId, slideNum, onClose }) {
  const [slide, setSlide] = useState(null);
  useEffect(() => {
    setSlide(null);
    if (lectureId && slideNum) api.get(`/api/lectures/${lectureId}/slide/${slideNum}`).then(setSlide).catch(() => {});
  }, [lectureId, slideNum]);
  if (!lectureId || !slideNum) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn modal-close" aria-label="Close" onClick={onClose}>×</button>
        {!slide ? <div className="muted">Loading…</div> : (
          <>
            <h3>{slide.lecture_title} · Slide {slide.slide_num}</h3>
            {slide.images?.map((u) => <img key={u} className="slide-modal-img" src={u} alt={slide.caption || "Lecture slide"} />)}
            <p>{slide.text}</p>
          </>
        )}
      </div>
    </div>
  );
}

function Setup({ onStarted }) {
  const [mode, setMode] = useState("practice");
  const [timeMode, setTimeMode] = useState("tutor");
  const [target, setTarget] = useState(20);
  const [lectures, setLectures] = useState([]);
  const [allLectures, setAllLectures] = useState(false);
  const [checked, setChecked] = useState(new Set());
  const [weekFilter, setWeekFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [status, setStatus] = useState("");
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    api.get("/api/lectures").then(setLectures);
    return onLecturesChanged(() => api.get("/api/lectures").then(setLectures));
  }, []);

  const weeks = useMemo(
    () => [...new Set(lectures.map((l) => weekText(l.week)).filter(Boolean))].sort(weekCompare),
    [lectures]
  );

  function toggleLecture(id) {
    setChecked((c) => {
      const next = new Set(c);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  // Picking a week/category is a quick-select, not a toggle: it sets the
  // checked set to exactly what matches (both filters combine), so a student
  // can jump straight to "just week 3" or "just Doctoring" instead of
  // hand-checking a dozen lectures one at a time.
  function applyFilters(week, tag) {
    if (!week && !tag) return;
    const matches = lectures.filter(
      (l) => (!week || weekText(l.week) === String(week)) && (!tag || l.tag === tag)
    );
    setChecked(new Set(matches.map((l) => l.id)));
    setAllLectures(false);
  }
  function onWeekFilter(v) { setWeekFilter(v); applyFilters(v, tagFilter); }
  function onTagFilter(v) { setTagFilter(v); applyFilters(weekFilter, v); }

  async function start() {
    setStarting(true);
    setStatus(mode === "practice" ? "Generating fresh questions… (this may take ~30-60s)" : "Loading missed questions…");
    try {
      const fd = new FormData();
      fd.append("mode", mode);
      fd.append("target", target);
      fd.append("time_mode", timeMode);
      if (!allLectures) for (const id of checked) fd.append("lecture_ids", id);
      const res = await api.post("/api/sessions", fd);
      setStatus("");
      if (!res.question_count) {
        setStatus(res.message || "No questions available for this mode/source.");
        return;
      }
      notifySessionsChanged();
      onStarted(res.session_id);
    } catch (e) {
      setStatus("Could not start session: " + e.message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="card">
      {/* Card spans the same width as every other tab's cards (page-frame
          consistency); the form itself only needs to be as wide as its
          fields, so it's centered in a narrower inner column. */}
      <div className="narrow-col" style={{ maxWidth: 480 }}>
        <h3>Start a session</h3>
        <div className="settings-field">
          <label>Mode</label>
          <Pills options={MODE_OPTIONS} value={mode} onChange={setMode} />
        </div>
        <div className="settings-field">
          <label>Source</label>
          <label className="lp-all">
            <input type="checkbox" checked={allLectures} onChange={(e) => setAllLectures(e.target.checked)} /> All lectures
          </label>
          {weeks.length > 0 && (
            <div className="lp-filters">
              <select value={weekFilter} onChange={(e) => onWeekFilter(e.target.value)} style={{ maxWidth: 140 }}>
                <option value="">Filter by week…</option>
                {weeks.map((w) => <option key={w} value={w}>{weekLabel(w)}</option>)}
              </select>
              <Pills options={TAG_OPTIONS} value={tagFilter} onChange={onTagFilter} />
            </div>
          )}
          {!allLectures && (
            <div className="lp-list">
              {lectures.map((l) => (
                <label key={l.id} className="lp-item">
                  <input type="checkbox" checked={checked.has(l.id)} onChange={() => toggleLecture(l.id)} />
                  <span>{l.title}</span>
                </label>
              ))}
            </div>
          )}
        </div>
        <div className="settings-field">
          <label>Time mode</label>
          <Pills options={TIME_OPTIONS} value={timeMode} onChange={setTimeMode} />
          <div className="muted" style={{ fontSize: 12.5, marginTop: 6 }}>
            {timeMode === "tutor"
              ? "Infinite time per question, explanation shown right after you answer."
              : "Total time = 1.5 min/question; no explanation until the end."}
          </div>
        </div>
        <div className="settings-field">
          <label>Number of questions (max 59)</label>
          <input type="number" min={1} max={59} value={target} onChange={(e) => setTarget(e.target.value)} style={{ maxWidth: 100 }} />
        </div>
        <div className="settings-actions">
          <button className="btn" disabled={starting} onClick={start}>Start session</button>
          <span className="muted">{status}</span>
        </div>
      </div>
    </div>
  );
}

function QuestionCard({ sid, question, tutorMode, onAnswered, onNext, stopTimer, cite, modalOpen }) {
  const [pending, setPending] = useState(question.prior_selected_index ?? null);
  const [revealed, setRevealed] = useState(false);
  const [result, setResult] = useState(null); // {correct, explanation}
  const [nav, setNav] = useState([]);
  const answeredBefore = question.prior_selected_index !== null && question.prior_selected_index !== undefined;

  useEffect(() => {
    setPending(question.prior_selected_index ?? null);
    const alreadyRevealed = answeredBefore && tutorMode;
    setRevealed(alreadyRevealed);
    setResult(answeredBefore ? { correct: question.prior_correct === 1, explanation: question.explanation } : null);
    if (alreadyRevealed) stopTimer();
    loadNav();
  }, [question.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function loadNav() {
    try { setNav(await api.get(`/api/sessions/${sid}/nav`)); } catch { setNav([]); }
  }

  const submitting = useRef(false);
  async function submit() {
    // Tutor-mode answers are final once revealed (the server enforces it too).
    if (revealed || pending === null || submitting.current) return;
    submitting.current = true;
    try {
      const res = await api.post(`/api/sessions/${sid}/answer/${question.id}`, { selected_index: pending });
      setResult({ correct: res.correct, explanation: res.explanation });
      await onAnswered(res);
      await loadNav();
      if (tutorMode) { stopTimer(); setRevealed(true); }
      else onNext(undefined, question.position);
    } finally {
      submitting.current = false;
    }
  }

  // Keyboard: A-E / 1-5 pick, Enter submits (or moves on once revealed), -> next.
  // The handler lives in a ref so the single listener always sees current state.
  const keyRef = useRef(null);
  keyRef.current = (e) => {
    if (modalOpen || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
    const k = e.key.toLowerCase();
    const idx = "abcde".indexOf(k) >= 0 ? "abcde".indexOf(k) : "12345".indexOf(k);
    if (k.length === 1 && idx >= 0) {
      if (!revealed && idx < question.options.length) setPending(idx);
    } else if (k === "enter") {
      e.preventDefault(); // otherwise a focused option button also "clicks"
      if (revealed) onNext(undefined, question.position); else submit();
    } else if (k === "arrowright") {
      e.preventDefault();
      onNext(undefined, question.position);
    }
  };
  useEffect(() => {
    const h = (e) => keyRef.current?.(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const letters = "ABCDE";
  return (
    <div className="question-box">
      <div className="q-stem">{question.question}</div>
      <div className="options">
        {question.options.map((o, i) => {
          let cls = "option";
          if (revealed) {
            if (i === question.correct_index) cls += " correct";
            else if (i === pending && !result?.correct) cls += " wrong";
          } else if (i === pending) cls += " selected";
          return (
            <button key={i} className={cls} disabled={revealed} onClick={() => setPending(i)}>
              <strong>{letters[i]}.</strong> {o}
              {revealed && i === question.correct_index && <span className="mark" aria-label="correct"> ✓</span>}
              {revealed && i === pending && i !== question.correct_index && <span className="mark" aria-label="your answer, incorrect"> ✗</span>}
            </button>
          );
        })}
      </div>
      {revealed && result && (
        <div className="explanation">
          <div className="explain-head-row">
            <h4>Explanation</h4>
            {question.source_slide?.lecture_title && (
              <span className="source-pill">
                {question.source_slide.lecture_title}
                {weekText(question.source_slide.lecture_week) ? ` · ${weekLabel(question.source_slide.lecture_week)}` : ""}
              </span>
            )}
          </div>
          {renderExplanation(result.explanation, question.lecture_id, cite)}
          <SourceBlock images={question.source_images} slide={question.source_slide} source={question.question_source} onCite={cite} />
        </div>
      )}
      <div className="quiz-nav">
        {!revealed && (
          <button className="btn" disabled={pending === null} onClick={submit}>
            {answeredBefore ? "Submit change" : "Submit"}
          </button>
        )}
        <button className="btn ghost" onClick={() => onNext(undefined, question.position)}>Next</button>
      </div>
      {nav.length > 0 && (
        <div className="qnav-wrap">
          <div className="qnav">
            {nav.map((n) => {
              const cls = ["qnav-btn"];
              if (n.question_id === question.id) cls.push("current");
              if (n.answered) cls.push(tutorMode ? (n.correct ? "ok" : "bad") : "done");
              const state = !n.answered ? "unanswered" : tutorMode ? (n.correct ? "correct" : "incorrect") : "answered";
              return (
                <button key={n.question_id} className={cls.join(" ")} aria-label={`Question ${n.position + 1}, ${state}`} onClick={() => onNext(n.question_id)}>
                  {n.position + 1}
                  {n.answered && tutorMode && <span className="qmark" aria-hidden="true">{n.correct ? "✓" : "✗"}</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function SessionReview({ sid, onBack, cite }) {
  const [data, setData] = useState(null);
  const [rec, setRec] = useState(undefined); // undefined = not loaded, null = none

  useEffect(() => {
    api.get(`/api/sessions/${sid}/review`).then(setData);
  }, [sid]);

  useEffect(() => {
    if (!data) return;
    const isCorrect = (q) => q.selected_index === -1 ? false
      : (q.selected_index != null ? q.selected_index === q.correct_index : q.selected_correct === 1);
    const anyMissed = data.questions.some((q) => !isCorrect(q));
    if (!anyMissed) { setRec(null); return; }
    api.post(`/api/sessions/${sid}/recommendations`).then((r) => setRec(r.recommendations || null)).catch(() => setRec(null));
  }, [data, sid]);

  if (!data) return <div className="muted">Loading…</div>;
  const qs = data.questions;
  const tutorMode = !!data.session.tutor_mode;
  const isCorrect = (q) => q.selected_index === -1 ? false
    : (q.selected_index != null ? q.selected_index === q.correct_index : q.selected_correct === 1);
  const correctCount = qs.filter(isCorrect).length;
  const timedOut = qs.filter((q) => q.selected_index === -1).length;
  const letters = "ABCDE";

  return (
    <div>
      <button className="btn ghost sm" onClick={onBack}>← Back to setup</button>

      <div className="card" style={{ marginTop: 12 }}>
        <h2 style={{ margin: "0 0 6px" }}>Session complete</h2>
        <div className="proj-headline">
          <span className="proj-num">{correctCount}</span>/{qs.length} correct
          {timedOut > 0 && <span className="muted"> · {timedOut} timed out</span>}
        </div>
        <div className="proj-bar"><span style={{ width: `${qs.length ? (100 * correctCount / qs.length) : 0}%` }} /></div>
      </div>

      {rec && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>What to work on</h3>
          <div className="muted" style={{ marginBottom: 8 }}>{rec.mistake_count} missed</div>
          {rec.summary && <p>{rec.summary}</p>}
          {rec.themes?.length > 0 && (
            <ul className="detail-list">
              {rec.themes.map((t, i) => (
                <li key={i}>
                  <div style={{ fontWeight: 600 }}>{t.topic}</div>
                  {t.why && <div className="muted" style={{ fontSize: 13 }}>{t.why}</div>}
                  {t.action && <div style={{ fontSize: 13 }}>→ {t.action}</div>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {rec === undefined && qs.some((q) => !isCorrect(q)) && (
        <div className="card muted" style={{ marginTop: 16 }}>Looking for patterns in your mistakes…</div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Review</h3>
        <div className="review-list">
          {qs.map((q, i) => {
            const timedOutQ = q.selected_index === -1;
            const correct = isCorrect(q);
            return (
              <div key={q.id} className="review-item">
                <div className="review-q">
                  <span className="review-num muted">{i + 1}.</span> {q.question}{" "}
                  <span className={`rev-tag ${timedOutQ ? "rev-tag-time" : correct ? "rev-tag-good" : "rev-tag-bad"}`}>
                    {timedOutQ ? "Time expired" : correct ? "Correct" : "Incorrect"}
                  </span>
                </div>
                <div className="review-opts">
                  {q.options.map((o, oi) => {
                    let cls = "rev-opt";
                    if (oi === q.correct_index) cls = "rev-opt-correct";
                    else if (oi === q.selected_index) cls = "rev-opt-wrong";
                    const mark = oi === q.correct_index ? " ✓" : (oi === q.selected_index ? " ✗" : "");
                    return <div key={oi} className={cls}>{letters[oi]}. {o}{mark}</div>;
                  })}
                </div>
                <div className="review-explain-row">
                  <div className="review-explain">
                    <strong>Explanation:</strong> {renderExplanation(q.explanation, q.lecture_id, cite)}
                  </div>
                  {tutorMode && q.lecture_title && (
                    <span className="source-pill">
                      {q.lecture_title}{weekText(q.lecture_week) ? ` · ${weekLabel(q.lecture_week)}` : ""}
                    </span>
                  )}
                </div>
                <SourceBlock
                  images={q.source_images}
                  slide={{ lecture_id: q.lecture_id, lecture_title: q.lecture_title, slide_num: q.slide_num, caption: q.slide_caption }}
                  source={q.question_source}
                  onCite={cite}
                />
              </div>
            );
          })}
        </div>
      </div>
      <div style={{ marginTop: 16 }}>
        <button className="btn" onClick={onBack}>Back to setup</button>
      </div>
    </div>
  );
}

export function Drill({ openSessionId, onOpenSessionHandled }) {
  const [screen, setScreen] = useState("setup"); // setup | active | review
  const [sid, setSid] = useState(null);
  const [question, setQuestion] = useState(null);
  const [tutorMode, setTutorMode] = useState(true);
  const [progress, setProgress] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [quizActive, setQuizActive] = useState(false);
  const [citeTarget, setCiteTarget] = useState(null); // {lectureId, slideNum}
  const quizTotalRef = useRef(0);
  const timerRef = useRef(null);
  // The countdown interval and the pagehide handler outlive the render that
  // created them, so they must read the CURRENT session/clock from refs - a
  // closed-over `sid` was null (or the previous session) when the timer fired.
  const sidRef = useRef(null);
  const secondsRef = useRef(0);
  secondsRef.current = seconds;

  function stopTimer() {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
  }
  function startCountUp() {
    stopTimer();
    setSeconds(0);
    timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
  }
  function startCountDown(startAt, id) {
    stopTimer();
    setSeconds(Math.max(0, startAt));
    let s = Math.max(0, startAt);
    timerRef.current = setInterval(() => {
      s -= 1;
      setSeconds(s);
      if (s <= 0) {
        stopTimer();
        onTimeout(id);
      }
    }, 1000);
  }
  useEffect(() => () => stopTimer(), []);

  // Resume/Review triggered from the Review tab or Learn's in-progress list -
  // both live outside Drill's own mounted tree, so App hands us a session id
  // to open on demand instead of Drill owning that navigation itself.
  useEffect(() => {
    if (openSessionId) {
      openSession(openSessionId);
      onOpenSessionHandled?.();
    }
  }, [openSessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  function quizElapsed() {
    return Math.max(0, Math.round(quizTotalRef.current - secondsRef.current));
  }

  async function pauseIfQuizActive() {
    if (!quizActive || !sid || tutorMode) return;
    setQuizActive(false);
    stopTimer();
    try { await api.post(`/api/sessions/${sid}/pause`, { elapsed_sec: quizElapsed() }); } catch { /* best effort */ }
  }

  // Save quiz time every 15s and when the tab closes/reloads - only Pause used
  // to preserve it, so anything else restarted the full clock on resume.
  useEffect(() => {
    if (!quizActive || tutorMode || !sid) return;
    const save = () => fetch(`/api/sessions/${sid}/pause`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ elapsed_sec: quizElapsed() }),
      keepalive: true,
    }).catch(() => {});
    const iv = setInterval(save, 15000);
    window.addEventListener("pagehide", save);
    return () => { clearInterval(iv); window.removeEventListener("pagehide", save); };
  }, [quizActive, tutorMode, sid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function onTimeout(id = sidRef.current) {
    setQuizActive(false);
    stopTimer();
    try { await api.post(`/api/sessions/${id}/timeout`); } catch { /* best effort */ }
    setScreen("review");
  }

  async function endTest() {
    if (!window.confirm("End the test now? Every unanswered question will be marked incorrect.")) return;
    await onTimeout(sid);
  }

  async function openSession(id) {
    sidRef.current = id;
    setSid(id);
    stopTimer();
    setQuizActive(false);
    setScreen("active");
    setQuestion(null);
    try {
      const sess = await api.get(`/api/sessions/${id}`);
      const isTutor = sess.session.tutor_mode !== 0;
      setTutorMode(isTutor);
      quizTotalRef.current = (sess.session.time_limit_min || 0) * 60;
      if (sess.session.status === "completed") { setScreen("review"); return; }
      if (!isTutor) {
        const elapsed = sess.session.elapsed_sec || 0;
        setQuizActive(true);
        startCountDown(quizTotalRef.current - elapsed, id);
      }
      await loadNext(id);
      if (isTutor) startCountUp();
      return;
    } catch {
      setTutorMode(true);
      quizTotalRef.current = 0;
    }
    await loadNext(id);
    startCountUp();
  }

  // `after`: position of the question being left, so Next skips ahead.
  // Returns the loaded question id (null when the session is done).
  async function loadNext(id, qid, after) {
    const sessionId = id || sid;
    const res = qid
      ? await api.get(`/api/sessions/${sessionId}/question/${qid}`)
      : await api.get(`/api/sessions/${sessionId}/next${after != null ? `?after=${after}` : ""}`);
    if (res.done) { setScreen("review"); return null; }
    setQuestion(res.question);
    const sess = await api.get(`/api/sessions/${sessionId}`);
    setProgress(`${sess.session.completed_count} of ${sess.session.target_count} answered · ${sess.session.tutor_mode ? "Tutor" : "Quiz"}`);
    return res.question.id;
  }

  async function handleAnswered() {
    // stats refresh happens on next question load; nothing else needed here.
  }

  function backToSetup() {
    stopTimer();
    sidRef.current = null;
    setSid(null);
    setQuestion(null);
    setScreen("setup");
  }

  return (
    <div>
      {screen === "setup" && <Setup onStarted={openSession} />}

      {screen === "active" && question && (
        <div>
          <div className="session-top">
            <div className="muted">{progress}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {(tutorMode || quizActive) && (
                <div className={`timer ${!tutorMode && seconds < 60 ? "timer-danger" : ""}`}>
                  {!tutorMode ? "− " : ""}{fmtClock(seconds)}
                </div>
              )}
              {!tutorMode && quizActive && (
                <button className="btn ghost sm" onClick={endTest}>End test</button>
              )}
              <button className="btn danger sm" onClick={async () => { await pauseIfQuizActive(); backToSetup(); }}>
                Pause session
              </button>
            </div>
          </div>
          <QuestionCard
            sid={sid}
            question={question}
            tutorMode={tutorMode}
            onAnswered={handleAnswered}
            onNext={async (qid, after) => {
              // Only restart the tutor clock when we actually moved to a different
              // question - Next on the last unanswered one reloads the same.
              const shownId = question.id;
              const loaded = await loadNext(sid, qid, after);
              if (tutorMode && loaded != null && loaded !== shownId) startCountUp();
            }}
            modalOpen={!!citeTarget}
            stopTimer={stopTimer}
            cite={(lectureId, slideNum) => setCiteTarget({ lectureId, slideNum })}
          />
        </div>
      )}
      {screen === "active" && !question && <div className="muted">Loading…</div>}

      {screen === "review" && sid && (
        <SessionReview sid={sid} onBack={backToSetup} cite={(lectureId, slideNum) => setCiteTarget({ lectureId, slideNum })} />
      )}

      {citeTarget && (
        <SlideModal lectureId={citeTarget.lectureId} slideNum={citeTarget.slideNum} onClose={() => setCiteTarget(null)} />
      )}
    </div>
  );
}
