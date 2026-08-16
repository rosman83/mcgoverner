import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { SessionRow } from "../components/SessionRow";

const PAGE = 6;

function MissedSummary({ missed, onReviewLecture }) {
  const [shown, setShown] = useState(PAGE);
  const [starting, setStarting] = useState(null); // lecture_id currently spinning up a session
  if (!missed) return null;
  if (!missed.count) return <div className="muted">No missed questions to review.</div>;
  const rows = missed.by_lecture || [];
  const max = Math.max(1, ...rows.map((d) => d.c));

  async function start(lectureId) {
    setStarting(lectureId);
    try {
      await onReviewLecture(lectureId);
    } finally {
      setStarting(null);
    }
  }

  return (
    <div>
      <div className="proj-headline"><span className="proj-num">{missed.count}</span> question{missed.count === 1 ? "" : "s"} to review</div>
      {rows.length > 0 && (
        <>
          <ul className="detail-list">
            {rows.slice(0, shown).map((d) => (
              <li key={d.lecture_id} className="weight-row weight-row-click" onClick={() => start(d.lecture_id)}>
                <span className="weight-label">{starting === d.lecture_id ? "Starting…" : d.title}</span>
                <span className="mini-bar"><span className="mini-fill mini-fill-bad" style={{ width: `${(100 * d.c) / max}%` }} /></span>
                <span className="acc-badge acc-bad">{d.c}</span>
              </li>
            ))}
          </ul>
          {shown < rows.length && (
            <button className="btn ghost sm" style={{ marginTop: 10 }} onClick={() => setShown((n) => n + PAGE)}>
              Show more
            </button>
          )}
        </>
      )}
    </div>
  );
}

export function Review({ onOpenSession }) {
  const [missed, setMissed] = useState(null);
  const [history, setHistory] = useState([]);
  const [shown, setShown] = useState(PAGE);

  async function refresh() {
    const [m, h] = await Promise.all([api.get("/api/missed"), api.get("/api/sessions")]);
    setMissed(m);
    setHistory(h);
  }
  useEffect(() => { refresh(); }, []);

  async function handleDelete(id) {
    await api.del(`/api/sessions/${id}`);
    refresh();
  }

  // Missed questions aren't tied to one originating session (a lecture can
  // accumulate misses across many practice runs), so "review these" has to
  // dynamically spin up a fresh review-mode session scoped to that lecture
  // rather than reopening a specific past one.
  async function reviewLecture(lectureId) {
    const fd = new FormData();
    fd.append("mode", "review");
    fd.append("time_mode", "tutor");
    fd.append("lecture_ids", lectureId);
    const res = await fetch("/api/sessions", { method: "POST", body: fd }).then((r) => r.json());
    if (res.session_id) onOpenSession(res.session_id);
  }

  return (
    <div className="review-tab">
      <div className="card">
        <h3>To review</h3>
        <MissedSummary missed={missed} onReviewLecture={reviewLecture} />
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <h3>Past sessions</h3>
        {history.length ? (
          <>
            <ul className="detail-list">
              {history.slice(0, shown).map((s) => (
                <SessionRow key={s.id} s={s} onResume={onOpenSession} onReview={onOpenSession} onDelete={handleDelete} />
              ))}
            </ul>
            {shown < history.length && (
              <button className="btn ghost sm" style={{ marginTop: 10 }} onClick={() => setShown((n) => n + PAGE)}>
                Show more
              </button>
            )}
          </>
        ) : <div className="muted">No past sessions yet.</div>}
      </div>
    </div>
  );
}
