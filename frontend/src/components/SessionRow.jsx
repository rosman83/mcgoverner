import { fmtDate } from "../lib/format";

// One row of session history - shared between the Review tab (full history)
// and Learn's "Resume a session" card (in-progress subset only).
export function SessionRow({ s, onResume, onReview, onDelete }) {
  return (
    <li className="session-row-v">
      <div className="session-row-main">
        <div className="session-row-title">{s.title}</div>
        <div className="session-row-meta muted">
          {s.mode === "review" ? "Review" : "Practice"} · {s.tutor_mode ? "tutor" : "quiz"} · {fmtDate(s.created_at)}
        </div>
        {s.lecture_titles?.length > 0 && (
          <div className="session-row-lectures muted">{s.lecture_titles.join(", ")}</div>
        )}
      </div>
      <div className="session-row-side">
        {s.status !== "completed" && s.target_count > 0 && (
          <div className="session-progress-row">
            <span className="mini-bar">
              <span className="mini-fill" style={{ width: `${(100 * s.completed_count) / s.target_count}%` }} />
            </span>
            <span className="muted" style={{ fontSize: 12 }}>{s.completed_count}/{s.target_count}</span>
          </div>
        )}
        <div className="session-row-actions">
          {s.status === "completed"
            ? onReview && <button className="btn ghost sm" onClick={() => onReview(s.id)}>Review</button>
            : onResume && <button className="btn sm" onClick={() => onResume(s.id)}>Resume</button>}
          {onDelete && <button className="btn ghost sm" onClick={() => onDelete(s.id, s.title)}>Remove</button>}
        </div>
      </div>
    </li>
  );
}
