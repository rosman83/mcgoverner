import os
import json
from datetime import datetime, timedelta

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.db import init_db, get_conn, DB_PATH
from app.ingest.slides import import_lecture, list_lectures, slugify
from app.ingest.concepts import chunk_lecture, coverage_stats
from app.llm.summaries import generate_summary, get_summary
import app.scheduler as sched

init_db()

app = FastAPI(title="Block1 Exam Prep")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

from app.ingest.images import IMAGES_ROOT as _IMAGES_ROOT
os.makedirs(_IMAGES_ROOT, exist_ok=True)
app.mount("/images", StaticFiles(directory=_IMAGES_ROOT), name="images")


# ---------- Static ----------
def _asset_version():
    """Cache-buster derived from asset file mtimes, so CSS and JS always share
    one version and change together on every origin."""
    js = os.path.getmtime(os.path.join(STATIC_DIR, "app.js"))
    css = os.path.getmtime(os.path.join(STATIC_DIR, "style.css"))
    return str(int(max(js, css)))


@app.get("/")
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__CACHE_VERSION__", _asset_version())
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# ---------- Lectures ----------
@app.get("/api/lectures")
def api_lectures():
    lectures = list_lectures()
    out = []
    for l in lectures:
        out.append(dict(l))
    return out


@app.post("/api/lectures/import")
async def api_import_lectures(files: list[UploadFile] = File(...)):
    imported = []
    errors = []
    duplicates = []
    upload_dir = os.path.join(os.path.dirname(DB_PATH), "..", "lectures")
    os.makedirs(upload_dir, exist_ok=True)
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".pdf", ".pptx"):
            errors.append(f"{f.filename}: unsupported (use .pdf or .pptx)")
            continue
        dest = os.path.join(upload_dir, f.filename)
        with open(dest, "wb") as out:
            content = await f.read()
            out.write(content)
        try:
            # Detect duplicates before importing
            from app.ingest.duplicates import detect_duplicate_file
            dup = detect_duplicate_file(dest)
            if dup:
                # remove the temp file we just saved
                try:
                    os.remove(dest)
                except OSError:
                    pass
                duplicates.append(
                    {"filename": f.filename, "dup_of": dup["lecture_id"], "title": dup["title"], "score": dup["score"]}
                )
                continue
            lid = import_lecture(dest)
            imported.append(lid)
        except Exception as e:
            errors.append(f"{f.filename}: {e}")
    return {"imported": imported, "errors": errors, "duplicates": duplicates}


@app.post("/api/lectures/{lid}/dedupe")
def api_dedupe_lecture(lid: int):
    from app.ingest.duplicates import detect_duplicate, _lecture_text
    text = _lecture_text(lid, include_ocr=False)
    conn = get_conn()
    others = [r["id"] for r in conn.execute("SELECT id FROM lectures WHERE id != ?", (lid,)).fetchall()]
    conn.close()
    dup = detect_duplicate(text, lecture_ids=others, existing_include_ocr=False) if others else None
    return {"duplicate_of": dup}


@app.get("/api/lectures/{lid}/slides")
def api_lecture_slides(lid: int):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM slides WHERE lecture_id=? ORDER BY slide_num", (lid,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/lectures/{lid}/deck")
def api_lecture_deck(lid: int):
    """Slides with their text AND images, for the inline slide-by-slide view."""
    from app.ingest.images import images_for_slides
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM slides WHERE lecture_id=? ORDER BY slide_num", (lid,)
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["images"] = images_for_slides([d["id"]])
        out.append(d)
    return out


@app.get("/api/lectures/{lid}/images")
def api_lecture_images(lid: int):
    from app.ingest.images import images_for_lecture
    return images_for_lecture(lid)


@app.post("/api/lectures/{lid}/extract_images")
def api_extract_images(lid: int):
    from app.ingest.images import extract_lecture_images
    count, kind = extract_lecture_images(lid)
    return {"count": count, "kind": kind}


@app.post("/api/lectures/{lid}/ocr")
def api_ocr_lecture(lid: int):
    """Run OCR on all slide images for a lecture (background)."""
    from app.ingest.ocr import run_ocr_for_lecture
    import threading

    def _run():
        run_ocr_for_lecture(lid)
        try:
            from app.llm.captions import generate_captions_for_lecture
            generate_captions_for_lecture(lid, force=True)
        except Exception as e:
            print(f"caption generation failed for lecture {lid}: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started"}


@app.post("/api/lectures/{lid}/captions")
def api_captions_lecture(lid: int):
    """Generate reasoned image captions from OCR text (background)."""
    from app.llm.captions import generate_captions_for_lecture
    import threading
    threading.Thread(
        target=generate_captions_for_lecture, args=(lid,), kwargs={"force": True},
        daemon=True,
    ).start()
    return {"status": "started"}


@app.get("/api/lectures/{lid}/ocr/status")
def api_ocr_status(lid: int):
    from app.ingest.ocr import lecture_ocr_status
    return {"status": lecture_ocr_status(lid)}


@app.delete("/api/lectures/{lid}")
def api_delete_lecture(lid: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM lectures WHERE id=?", (lid,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Lecture not found")
    # Remove everything belonging to this lecture (FK cascades handle the rest)
    conn.execute("DELETE FROM lectures WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    return {"deleted": lid}


# ---------- Summaries ----------
@app.get("/api/lectures/{lid}/summary")
def api_summary(lid: int):
    s = get_summary(lid)
    if not s:
        conn = get_conn()
        st = conn.execute(
            "SELECT summary_status FROM lectures WHERE id=?", (lid,)
        ).fetchone()
        conn.close()
        return {"status": st["summary_status"] if st else "not_started", "summary": None}
    return {"status": "done", "summary": dict(s)}


@app.post("/api/lectures/{lid}/summary/generate")
def api_generate_summary(lid: int):
    try:
        result = generate_summary(lid)
        return {"status": "done", "key_points": len(result.get("key_points", []))}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------- Questions ----------
@app.get("/api/lectures/{lid}/questions")
def api_lecture_questions(lid: int, level: str = None):
    conn = get_conn()
    if level:
        rows = conn.execute(
            "SELECT * FROM questions WHERE lecture_id=? AND level=?",
            (lid, level),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM questions WHERE lecture_id=?", (lid,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Coverage / Dashboard ----------
@app.get("/api/coverage")
def api_coverage():
    return coverage_stats()


@app.get("/api/missed_summary")
def api_missed_summary():
    """Unresolved missed questions per lecture, for drill/progress views."""
    from app.sessiongen import missed_count, missed_by_lecture
    return {"total": missed_count(), "per_lecture": missed_by_lecture()}


@app.get("/api/lectures/{lid}/slides_progress")
def api_lecture_slides_progress(lid: int):
    """Per-slide progress for a lecture: slide coordinate + questions + accuracy."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.id AS slide_id, s.slide_num, s.text, "
        "(SELECT COUNT(*) FROM questions q WHERE q.slide_id=s.id) AS q_count, "
        "(SELECT COUNT(*) FROM questions q JOIN answers a ON a.question_id=q.id "
        "  WHERE q.slide_id=s.id AND a.correct=1) AS correct_count, "
        "(SELECT COUNT(*) FROM questions q JOIN answers a ON a.question_id=q.id "
        "  WHERE q.slide_id=s.id AND a.correct=0) AS wrong_count "
        "FROM slides s WHERE s.lecture_id=? "
        "AND (length(trim(s.text)) >= 15 OR length(trim(s.ocr_text)) >= 15) "
        "ORDER BY s.slide_num",
        (lid,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        total = d["correct_count"] + d["wrong_count"]
        d["accuracy"] = round(d["correct_count"] / total, 2) if total else None
        out.append(d)
    return out


@app.get("/api/dashboard")
def api_dashboard():
    from app.stats import lecture_stats, weak_slides, gaps
    from app.sessiongen import missed_count
    conn = get_conn()
    lect = conn.execute("SELECT COUNT(*) c FROM lectures").fetchone()["c"]
    qs = conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
    summaries = conn.execute(
        "SELECT COUNT(*) c FROM lectures WHERE summary_status='done'"
    ).fetchone()["c"]
    sessions_today = conn.execute(
        "SELECT COUNT(*) c FROM sessions WHERE created_at >= datetime('now', 'start of day')"
    ).fetchone()["c"]
    conn.close()

    lstats = lecture_stats()
    overall_accuracy = None
    ans_total = sum(l["answers"] for l in lstats)
    ans_correct = sum(l["correct"] for l in lstats)
    if ans_total:
        overall_accuracy = round(ans_correct / ans_total, 2)

    return {
        "lecture_count": lect,
        "question_count": qs,
        "summary_done": summaries,
        "coverage": coverage_stats(),
        "missed_count": missed_count(),
        "sessions_today": sessions_today,
        "overall_accuracy": overall_accuracy,
        "lectures": lstats,
        "weak_slides": weak_slides(),
        "gaps": gaps(),
    }



# ---------- Sessions ----------
@app.post("/api/sessions")
def api_create_session(
    mode: str = Form("practice"),
    target: int = Form(20),
    lecture_id: int = Form(None),
    lecture_ids: list[int] = Form(None),
    time_mode: str = Form("tutor"),
):
    """Create a session. mode: 'practice' (fresh on-the-spot questions from selected
    lectures) or 'review' (previously missed questions). target capped at 59.
    time_mode: 'tutor' (infinite time per question, explanations immediately) or
    'quiz' (total time = 1.5 min/question, explanations only at the end).
    lecture_ids: one or more lectures to draw from (empty/None = all)."""
    from app.sessiongen import create_practice_session, create_review_session
    lids = lecture_ids if lecture_ids else ([lecture_id] if lecture_id else None)
    if mode == "review":
        sid, count = create_review_session(lecture_ids=lids, time_mode=time_mode)
    else:
        sid, count = create_practice_session(lecture_ids=lids, target=target, time_mode=time_mode)
    if count == 0:
        return {"session_id": None, "question_count": 0, "message": "No questions generated"}
    return {"session_id": sid, "question_count": count}


@app.get("/api/missed")
def api_missed():
    """Unresolved missed questions count + breakdown by lecture."""
    from app.sessiongen import missed_count, missed_by_lecture
    return {"count": missed_count(), "by_lecture": missed_by_lecture()}


@app.get("/api/sessions")
def api_list_sessions():
    """All past sessions (newest first), for the Drill 'past sessions' panel."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/sessions/{sid}")
def api_session(sid: int):
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    sq = conn.execute(
        "SELECT * FROM session_questions WHERE session_id=? ORDER BY position",
        (sid,),
    ).fetchall()
    conn.close()
    return {"session": dict(s), "questions": [dict(r) for r in sq]}


@app.get("/api/sessions/{sid}/review")
def api_session_review(sid: int):
    """Full question set + correct/wrong status, for end-of-session review."""
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    rows = conn.execute(
        "SELECT q.id, q.question, q.options, q.correct_index, q.explanation, sq.answered, "
        "a.selected_index, a.correct AS selected_correct, q.slide_id, "
        "l.title AS lecture_title, s.slide_num, s.text AS slide_text, s.caption AS slide_caption "
        "FROM session_questions sq JOIN questions q ON q.id=sq.question_id "
        "LEFT JOIN lectures l ON l.id=q.lecture_id "
        "LEFT JOIN answers a ON a.question_id=q.id AND a.session_id=? "
        "LEFT JOIN slides s ON s.id=q.slide_id "
        "WHERE sq.session_id=? ORDER BY sq.position",
        (sid, sid),
    ).fetchall()
    conn.close()
    from app.ingest.images import images_for_slides
    out = []
    for r in rows:
        d = dict(r)
        d["options"] = json.loads(d["options"])
        d["source_images"] = images_for_slides([d["slide_id"]]) if d.get("slide_id") else []
        out.append(d)
    return {"session": dict(s), "questions": out}


@app.get("/api/sessions/{sid}/next")
def api_next_question(sid: int):
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    row = conn.execute(
        "SELECT sq.*, q.question, q.options, q.correct_index, q.explanation, q.level, q.slide_id, "
        "l.title AS source_lecture_title, "
        "s.slide_num AS source_slide_num, s.text AS source_slide_text, "
        "s.caption AS source_slide_caption "
        "FROM session_questions sq JOIN questions q ON q.id = sq.question_id "
        "LEFT JOIN lectures l ON l.id = q.lecture_id "
        "LEFT JOIN slides s ON s.id = q.slide_id "
        "WHERE sq.session_id=? AND sq.answered=0 "
        "ORDER BY sq.position LIMIT 1",
        (sid,),
    ).fetchone()
    conn.close()
    if not row:
        return {"done": True}
    q = dict(row)
    q["options"] = json.loads(q["options"])
    q["id"] = q["question_id"]

    # Source material images: question -> slide directly (no concept indirection)
    from app.ingest.images import images_for_slides
    slide_ids = [q["slide_id"]] if q.get("slide_id") else []
    q["source_images"] = images_for_slides(slide_ids)
    q["source_slide"] = {
        "lecture_title": q.get("source_lecture_title") or "",
        "slide_num": q.get("source_slide_num"),
        "text": q.get("source_slide_text") or "",
        "caption": q.get("source_slide_caption") or "",
    }
    return {"done": False, "question": q}


class Answer(BaseModel):
    selected_index: int = None  # option index the user chose


class PauseBody(BaseModel):
    elapsed_sec: int = 0


@app.post("/api/sessions/{sid}/pause")
def api_pause(sid: int, body: PauseBody):
    """Persist elapsed quiz time so a paused quiz resumes with correct remaining time."""
    conn = get_conn()
    s = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    conn.execute(
        "UPDATE sessions SET elapsed_sec=?, updated_at=datetime('now') WHERE id=?",
        (max(0, body.elapsed_sec), sid),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/sessions/{sid}/answer/{question_id}")
def api_answer(sid: int, question_id: int, body: Answer):
    """Mark a question answered. If wrong, tag it as missed for next-day review.
    Returns whether correct + the explanation."""
    conn = get_conn()
    row = conn.execute(
        "SELECT q.question_id, q.answered, qs.correct_index, qs.explanation, qs.lecture_id "
        "FROM session_questions q JOIN questions qs ON qs.id = q.question_id "
        "WHERE q.session_id=? AND q.question_id=? AND q.answered=0",
        (sid, question_id),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Not in session")
    qid = row["question_id"]
    correct = body.selected_index == row["correct_index"]

    conn.execute(
        "INSERT INTO answers(question_id, session_id, correct, selected_index) VALUES(?,?,?,?)",
        (qid, sid, 1 if correct else 0, body.selected_index),
    )
    conn.execute(
        "UPDATE session_questions SET answered=1 WHERE session_id=? AND question_id=?",
        (sid, qid),
    )
    conn.execute(
        "UPDATE sessions SET completed_count = completed_count + 1, updated_at=datetime('now') "
        "WHERE id=?",
        (sid,),
    )

    # Tag wrong answers as missed (resolved on next-day review)
    if not correct:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO missed(question_id, lecture_id, missed_at, resolved, last_wrong_at) "
            "VALUES(?,?,?,0,?) "
            "ON CONFLICT DO NOTHING",
            (qid, row["lecture_id"], now, now),
        )
        conn.execute(
            "UPDATE missed SET resolved=0, last_wrong_at=? WHERE question_id=?",
            (now, qid),
        )
    else:
        # correct in a practice/review session -> mark resolved if it was missed
        conn.execute(
            "UPDATE missed SET resolved=1 WHERE question_id=? AND lecture_id=?",
            (qid, row["lecture_id"]),
        )

    remaining = conn.execute(
        "SELECT COUNT(*) c FROM session_questions WHERE session_id=? AND answered=0",
        (sid,),
    ).fetchone()["c"]
    if remaining == 0:
        conn.execute("UPDATE sessions SET status='completed' WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return {
        "correct": correct,
        "correct_index": row["correct_index"],
        "explanation": row["explanation"],
        "remaining": remaining,
    }


@app.post("/api/sessions/{sid}/timeout")
def api_timeout(sid: int):
    """Quiz-mode timer expired: grade every unanswered question as incorrect and
    complete the session."""
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    rows = conn.execute(
        "SELECT sq.question_id, qs.lecture_id FROM session_questions sq "
        "JOIN questions qs ON qs.id = sq.question_id "
        "WHERE sq.session_id=? AND sq.answered=0",
        (sid,),
    ).fetchall()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        qid = r["question_id"]
        conn.execute(
            "INSERT INTO answers(question_id, session_id, correct, selected_index) VALUES(?,?,0,-1)",
            (qid, sid),
        )
        conn.execute(
            "UPDATE session_questions SET answered=1 WHERE session_id=? AND question_id=?",
            (sid, qid),
        )
        conn.execute(
            "INSERT INTO missed(question_id, lecture_id, missed_at, resolved, last_wrong_at) "
            "VALUES(?,?,?,0,?) ON CONFLICT DO NOTHING",
            (qid, r["lecture_id"], now, now),
        )
        conn.execute(
            "UPDATE missed SET resolved=0, last_wrong_at=? WHERE question_id=?",
            (now, qid),
        )
    conn.execute(
        "UPDATE sessions SET completed_count = completed_count + ?, "
        "status='completed', updated_at=datetime('now') WHERE id=?",
        (len(rows), sid),
    )
    conn.commit()
    conn.close()
    return {"timeout": True, "graded_incorrect": len(rows)}


@app.get("/api/sessions_active")
def api_active_sessions():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions WHERE status='active' ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
