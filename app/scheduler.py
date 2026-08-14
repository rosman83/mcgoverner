import json
import math
import random
from datetime import datetime, timedelta

from app.db import get_conn

# --- Anki-style settings ---
MAX_INTERVAL_DAYS = 180.0
LEARNING_STEPS_MINUTES = [1, 10]  # 1m, 10m
DESIRED_RETENTION = 0.84
MIN_EASE = 1.3
MAX_EASE = 3.0

# FSRS-5 default constants (retention-curve shape)
FSRS_DECAY = -0.5
FSRS_FACTOR = 19 / 81
INITIAL_STABILITY = 3.0
RATING_WEIGHTS = {1: 0.5, 2: 0.9, 3: 1.0, 4: 1.3}  # again/hard/good/easy stability multipliers


def _now():
    return datetime.now()


def _minutes_to_days(mins):
    return mins / (24 * 60)


def _retrievability(stability_days, elapsed_days):
    """FSRS forgetting curve: R = (1 + F*t/S)^(1/D)."""
    if stability_days <= 0:
        return 0.0
    return (1 + FSRS_FACTOR * elapsed_days / stability_days) ** (1 / FSRS_DECAY)


def _interval_for_retention(stability_days):
    """Solve for the interval that gives R == DESIRED_RETENTION.
    R = (1 + F*t/S)^(1/D) => t = S * ((R^D - 1) / F)
    """
    if stability_days <= 0:
        return 1.0
    r_pow = DESIRED_RETENTION ** FSRS_DECAY
    days = stability_days * ((r_pow - 1) / FSRS_FACTOR)
    return max(days, 1.0)


def format_interval(days):
    """Human-friendly interval: <1 day in minutes/hours, else days/weeks."""
    if days is None or days < 0:
        return ""
    if days < 1:
        mins = days * 24 * 60
        if mins < 60:
            return f"{int(round(mins))}m"
        return f"{int(round(mins / 60))}h"
    if days < 30:
        return f"{int(round(days))}d"
    if days < 365:
        weeks = days / 7
        return f"{int(round(weeks))}w"
    months = days / 30
    return f"{int(round(months))}mo"


def _stability_multiplier(rating):
    """FSRS-5-ish: how much stability changes per rating."""
    base = RATING_WEIGHTS[rating]
    # increase difficulty via ease drift over time; keep it simple
    return base


def _new_stability(rating, current_stability, ease, correct):
    """FSRS-5 simplified stability update. rating: 1-4 (again..easy)."""
    if not correct:  # again
        return max(current_stability * 0.5, 0.5)
    if rating == 2:  # hard
        return max(current_stability * 1.0 + 0.3, 0.5)
    if rating == 3:  # good
        return max(current_stability * 1.6 + 0.7, 0.5)
    # easy
    return max(current_stability * 2.4 + 1.5, 0.5)


def add_question_to_scheduler(question_id):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO scheduler_state(question_id, due_at) VALUES(?, ?)",
        (question_id, _now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    conn.close()


def get_state(question_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM scheduler_state WHERE question_id=?", (question_id,)
    ).fetchone()
    conn.close()
    return row


def predict(question_id):
    """Return the next interval (days) for each rating, given current state.
    Used to display predicted timing above the rating buttons."""
    state = get_state(question_id)
    now = _now()
    if state is None:
        base = {
            "ease": 2.5,
            "interval_days": 0.0,
            "reps": 0,
            "lapses": 0,
            "learning_step": 0,
        }
    else:
        base = {
            "ease": state["ease"],
            "interval_days": state["interval_days"],
            "reps": state["reps"],
            "lapses": state["lapses"],
            "learning_step": state["learning_step"],
        }
    out = {}
    for rating in (1, 2, 3, 4):
        interval = _compute_interval(base, rating)
        out[rating] = {
            "days": round(interval, 2),
            "str": format_interval(interval),
        }
    return out


def _compute_interval(state, rating):
    """Core scheduling math: return next interval in days given state + rating.
    rating: 1=again, 2=hard, 3=good, 4=easy.
    Handles learning steps (1m/10m) for new/again cards, then FSRS review intervals.
    """
    reps = state.get("reps", 0) or 0
    step = state.get("learning_step", 0) or 0
    ease = state.get("ease", 2.5) or 2.5
    current_interval = state.get("interval_days", 0.0) or 0.0

    # LEARNING PHASE: card still in learning steps (reps below graduation)
    in_learning = reps < 2 or (current_interval < 1 and reps >= 1 and step < len(LEARNING_STEPS_MINUTES))
    if in_learning:
        if rating == 1:  # again -> back to first learning step (1m)
            return _minutes_to_days(LEARNING_STEPS_MINUTES[0])
        if rating == 2:  # hard -> stay on current step
            step_idx = min(step, len(LEARNING_STEPS_MINUTES) - 1)
            return _minutes_to_days(LEARNING_STEPS_MINUTES[step_idx])
        if rating == 4:  # easy -> graduate immediately to a review interval
            stability = max(INITIAL_STABILITY * 2.0, 2.0)
            return min(_interval_for_retention(stability), MAX_INTERVAL_DAYS)
        # good -> advance to next step; if last step, graduate
        next_step = step + 1
        if next_step < len(LEARNING_STEPS_MINUTES):
            return _minutes_to_days(LEARNING_STEPS_MINUTES[next_step])
        stability = INITIAL_STABILITY
        return min(_interval_for_retention(stability), MAX_INTERVAL_DAYS)

    # REVIEW PHASE: FSRS-style stability update
    # estimate current stability from the interval that produced retention at last review
    elapsed = current_interval if current_interval >= 1 else 1.0
    stability = _infer_stability(current_interval, elapsed)

    if rating == 1:  # again -> relearn, back to first step
        return _minutes_to_days(LEARNING_STEPS_MINUTES[0])
    if rating == 2:  # hard
        new_s = _new_stability(2, stability, ease, True)
        return min(_interval_for_retention(new_s), MAX_INTERVAL_DAYS)
    if rating == 3:  # good
        new_s = _new_stability(3, stability, ease, True)
        return min(_interval_for_retention(new_s), MAX_INTERVAL_DAYS)
    # easy
    new_s = _new_stability(4, stability, ease, True)
    return min(_interval_for_retention(new_s), MAX_INTERVAL_DAYS)


def _infer_stability(interval_days, elapsed_days):
    """Back out stability from an observed interval, assuming retention was ~0.9 at scheduling.
    Since we schedule intervals at DESIRED_RETENTION, stability = interval / factor."""
    if interval_days and interval_days > 0:
        # interval was set = S * ((R^D - 1)/F) => S = interval * F / (R^D - 1)
        r_pow = DESIRED_RETENTION ** FSRS_DECAY
        return max(interval_days * FSRS_FACTOR / (r_pow - 1), 0.5)
    return INITIAL_STABILITY


def rate(question_id, rating_str):
    """Apply a rating to a question. rating_str in {again, hard, good, easy}.
    Returns updated state info including the next interval."""
    rating_map = {"again": 1, "hard": 2, "good": 3, "easy": 4}
    rating = rating_map[rating_str]
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM scheduler_state WHERE question_id=?", (question_id,)
    ).fetchone()
    now = _now()

    if row is None:
        state = {
            "ease": 2.5,
            "interval_days": 0.0,
            "reps": 0,
            "lapses": 0,
            "learning_step": 0,
        }
    else:
        state = {
            "ease": row["ease"],
            "interval_days": row["interval_days"],
            "reps": row["reps"],
            "lapses": row["lapses"],
            "learning_step": row["learning_step"],
        }

    reps = state["reps"]
    step = state["learning_step"]
    ease = state["ease"]
    correct = rating != 1
    next_interval = _compute_interval(state, rating)

    # ---- update state bookkeeping ----
    if rating == 1:
        state["lapses"] += 1
        state["learning_step"] = 0
        ease = max(MIN_EASE, ease - 0.2)
    elif rating == 2:
        state["learning_step"] = min(step + 1 if step + 1 < len(LEARNING_STEPS_MINUTES) else step, len(LEARNING_STEPS_MINUTES) - 1)
        ease = max(MIN_EASE, ease - 0.15)
    elif rating == 3:
        next_step = step + 1
        if next_step < len(LEARNING_STEPS_MINUTES):
            state["learning_step"] = next_step
        else:
            state["learning_step"] = len(LEARNING_STEPS_MINUTES)  # graduated
        ease = ease  # FSRS keeps ease mostly stable; we nudge up slightly on success
        ease = min(MAX_EASE, ease + 0.03)
    elif rating == 4:
        state["learning_step"] = len(LEARNING_STEPS_MINUTES)
        ease = min(MAX_EASE, ease + 0.15)

    state["reps"] = reps + 1
    state["interval_days"] = next_interval
    state["ease"] = ease
    due_at = (now + timedelta(days=next_interval)).strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        "INSERT INTO scheduler_state(question_id, ease, interval_days, reps, lapses, learning_step, due_at, last_review) "
        "VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(question_id) DO UPDATE SET ease=excluded.ease, "
        "interval_days=excluded.interval_days, reps=excluded.reps, lapses=excluded.lapses, "
        "learning_step=excluded.learning_step, due_at=excluded.due_at, last_review=excluded.last_review",
        (question_id, ease, next_interval, state["reps"], state["lapses"],
         state["learning_step"], due_at, now.strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.execute(
        "INSERT INTO reviews(question_id, correct, rating, ease, interval_days, due_at) "
        "VALUES(?,?,?,?,?,?)",
        (question_id, 1 if correct else 0, rating_str, ease, next_interval, due_at),
    )
    _update_concept_progress(conn, question_id)
    conn.commit()
    conn.close()
    return {
        "correct": correct,
        "due_at": due_at,
        "interval_days": next_interval,
        "interval_str": format_interval(next_interval),
        "ease": round(ease, 2),
        "reps": state["reps"],
    }


def _update_concept_progress(conn, question_id):
    """Refresh status/accuracy for concepts tagged to a question after a review."""
    import json

    row = conn.execute(
        "SELECT concept_ids FROM questions WHERE id=?", (question_id,)
    ).fetchone()
    if not row:
        return
    try:
        cids = json.loads(row["concept_ids"] or "[]")
    except (ValueError, TypeError):
        return

    for cid in cids:
        qids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM questions WHERE concept_ids LIKE ?", (f"%{cid}%",)
            ).fetchall()
        ]
        qids = [
            qid
            for qid in qids
            if cid in json.loads(
                conn.execute(
                    "SELECT concept_ids FROM questions WHERE id=?", (qid,)
                ).fetchone()["concept_ids"]
                or "[]"
            )
        ]
        if not qids:
            continue
        placeholders = ",".join("?" * len(qids))
        agg = conn.execute(
            f"SELECT COUNT(*) total, "
            f"SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) correct "
            f"FROM reviews WHERE question_id IN ({placeholders})",
            qids,
        ).fetchone()
        total = agg["total"] or 0
        correct = agg["correct"] or 0
        accuracy = round(correct / total, 2) if total else None
        status = "mastered" if (accuracy is not None and accuracy >= 0.8 and total >= 3) else ("seen" if total else "unseen")
        conn.execute(
            "UPDATE concepts SET status=?, accuracy=?, last_reviewed_at=datetime('now') WHERE id=?",
            (status, accuracy, cid),
        )


def due_questions(limit=50, exclude_lecture_id=None, lecture_id=None):
    conn = get_conn()
    now = _now().strftime("%Y-%m-%d %H:%M:%S")
    q = (
        "SELECT q.*, s.ease, s.reps, s.lapses, s.due_at, s.interval_days "
        "FROM scheduler_state s JOIN questions q ON q.id = s.question_id "
        "WHERE s.due_at <= ? "
    )
    params = [now]
    if exclude_lecture_id:
        q += "AND q.lecture_id != ? "
        params.append(exclude_lecture_id)
    if lecture_id:
        q += "AND q.lecture_id = ? "
        params.append(lecture_id)
    q += "ORDER BY s.due_at ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


def due_count():
    conn = get_conn()
    now = _now().strftime("%Y-%m-%d %H:%M:%S")
    c = conn.execute(
        "SELECT COUNT(*) c FROM scheduler_state WHERE due_at <= ?", (now,)
    ).fetchone()["c"]
    conn.close()
    return c


def new_questions_for_lecture(lecture_id, limit=15):
    """Questions for a lecture that have never been reviewed (reps == 0)."""
    conn = get_conn()
    if lecture_id:
        rows = conn.execute(
            "SELECT q.* FROM questions q "
            "LEFT JOIN scheduler_state s ON s.question_id = q.id "
            "WHERE q.lecture_id=? AND (s.reps IS NULL OR s.reps = 0) "
            "ORDER BY q.id LIMIT ?",
            (lecture_id, limit),
        ).fetchall()
        conn.close()
        return rows
    # all lectures: round-robin split so every lecture is represented
    lectures = conn.execute("SELECT id FROM lectures ORDER BY id").fetchall()
    rows = _even_split(
        conn,
        "SELECT q.* FROM questions q "
        "LEFT JOIN scheduler_state s ON s.question_id = q.id "
        "WHERE q.lecture_id=? AND (s.reps IS NULL OR s.reps = 0) ORDER BY q.id",
        lectures,
        limit,
    )
    conn.close()
    return rows


def due_questions_even(limit=50, lecture_id=None):
    """Due questions, round-robin split across lectures (or one lecture)."""
    conn = get_conn()
    now = _now().strftime("%Y-%m-%d %H:%M:%S")
    if lecture_id:
        rows = conn.execute(
            "SELECT q.*, s.ease, s.reps, s.lapses, s.due_at, s.interval_days "
            "FROM scheduler_state s JOIN questions q ON q.id = s.question_id "
            "WHERE s.due_at <= ? AND q.lecture_id=? ORDER BY s.due_at ASC LIMIT ?",
            (now, lecture_id, limit),
        ).fetchall()
        conn.close()
        return rows
    lectures = conn.execute("SELECT id FROM lectures ORDER BY id").fetchall()
    rows = _even_split(
        conn,
        "SELECT q.*, s.ease, s.reps, s.lapses, s.due_at, s.interval_days "
        "FROM scheduler_state s JOIN questions q ON q.id = s.question_id "
        "WHERE s.due_at <= ? AND q.lecture_id=? ORDER BY s.due_at ASC",
        lectures,
        limit,
        extra=(now,),
    )
    conn.close()
    return rows


def _even_split(conn, sql_template, lectures, limit, extra=()):
    """Round-robin select across lectures so results are evenly distributed.
    sql_template must have a ? for lecture_id as its LAST param placeholder."""
    n = len(lectures)
    if n == 0:
        return []
    per_lecture = max(limit // n, 1)
    combined = []
    for l in lectures:
        rows = conn.execute(sql_template, extra + (l["id"],)).fetchall()
        combined.extend(rows[:per_lecture])
    # fill up to limit if some lectures had fewer
    if len(combined) < limit:
        for l in lectures:
            if len(combined) >= limit:
                break
            rows = conn.execute(sql_template, extra + (l["id"],)).fetchall()
            for r in rows:
                if r["id"] not in {x["id"] for x in combined}:
                    combined.append(r)
                if len(combined) >= limit:
                    break
    return combined[:limit]
