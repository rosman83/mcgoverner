import logging
import os
import json
import shutil
import subprocess
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.db import init_db, get_conn, DB_PATH

# A crash used to reach the user as a bare "Internal Server Error" with
# nothing else - no way to tell what broke or report it. Every unhandled
# exception now gets a short id (shown in the UI) and its full traceback
# written to a log file that survives the launcher's /tmp log being cleared
# (data/ is the same persistent dir the database lives in).
ERROR_LOG_PATH = os.path.join(os.path.dirname(DB_PATH), "error.log")
from logging.handlers import RotatingFileHandler

_handler = RotatingFileHandler(ERROR_LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logging.basicConfig(level=logging.ERROR, handlers=[_handler])
error_logger = logging.getLogger("mcgoverner.errors")
from app.ingest.slides import import_lecture, list_lectures
from app.ingest.concepts import coverage_stats
from app.llm.summaries import generate_summary, get_summary

init_db()

app = FastAPI(title="McGoverner")

# Local-app hardening: any website you visit can fire cross-site POSTs at
# localhost:8000 (spend API credit, quit the app), and DNS rebinding can make
# the browser treat a hostile name as same-origin. Reject foreign Origins on
# state-changing requests and unknown Host headers.
from starlette.middleware.trustedhost import TrustedHostMiddleware

_LOCAL_ORIGINS = {"http://localhost:8000", "http://127.0.0.1:8000"}


@app.middleware("http")
async def reject_foreign_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin not in _LOCAL_ORIGINS:
        return JSONResponse(status_code=403, content={"message": "Blocked: request came from another website."})
    return await call_next(request)


app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])


# Short commit hash of the running code, so the navbar can show it and someone
# can confirm a relaunch actually picked up the latest update. Computed once at
# startup (not per-request) since it can't change without a restart anyway.
#
# Distributed installs (the fetch_code/Fetch-Code zip-download path in
# launch.sh/launch.ps1) have no .git, so `git rev-parse` always fails there -
# every real end-user install showed "dev" regardless of what commit was
# actually running, making this useless for confirming someone picked up a
# fix. The launcher now writes the real commit hash it downloaded into a
# .version file at the repo root; prefer that, and only fall back to git (for
# local dev checkouts, which have no .version file) then "dev".
from app.config import REPO_ROOT
from app.llm.client import LLMError

VERSION_FILE = os.path.join(REPO_ROOT, ".version")
try:
    with open(VERSION_FILE) as f:
        APP_VERSION = f.read().strip() or "dev"
except OSError:
    try:
        APP_VERSION = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(__file__), capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "dev"
    except Exception:
        APP_VERSION = "dev"

_UPDATE_CACHE = {"behind_by": 0, "checked_at": 0.0}
UPDATE_CHECK_TTL = 600  # seconds


def _commits_behind_main():
    """How many commits main has that the running build doesn't, so the UI can
    flag "a fix has shipped, relaunch to get it". Cached for 10 minutes - the
    navbar polls /api/usage every 60s, and GitHub allows 60 unauthenticated
    calls/hr. A failed check keeps the last known value.

    Uses GitHub's compare API rather than "latest sha != ours": a 404 means
    APP_VERSION isn't a commit on GitHub at all (an unpushed dev commit, or the
    file-blob hash older Mac launchers wrote into .version by mistake), and
    that used to show "Update available" forever."""
    import time
    import urllib.error
    import urllib.request

    if APP_VERSION == "dev":
        return 0
    now = time.time()
    if now - _UPDATE_CACHE["checked_at"] < UPDATE_CHECK_TTL:
        return _UPDATE_CACHE["behind_by"]
    _UPDATE_CACHE["checked_at"] = now
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/rosman83/mcgoverner/compare/{APP_VERSION}...main?per_page=1",
            headers={"User-Agent": "McGoverner", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            _UPDATE_CACHE["behind_by"] = json.loads(resp.read()).get("ahead_by", 0)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _UPDATE_CACHE["behind_by"] = 0
    except Exception:
        pass
    return _UPDATE_CACHE["behind_by"]


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Catches anything a route didn't turn into an HTTPException. AI failures
    (LLMError) already carry a message the user can act on - show it as-is.
    Anything else is a bug: give the user something they can screenshot and
    report instead of a bare 500, and put the full traceback where it survives
    past the launcher's /tmp log."""
    error_id = uuid.uuid4().hex[:8]
    error_logger.error(
        "[%s] %s %s\n%s", error_id, request.method, request.url.path,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )
    if isinstance(exc, LLMError):
        status, message = 502, str(exc)
    else:
        status = 500
        message = f"Something went wrong on the server's end ({type(exc).__name__}: {str(exc)[:200]})."
    return JSONResponse(
        status_code=status,
        content={
            "error": "server_error",
            "message": message,
            "error_id": error_id,
            "log_hint": f"Details saved to {ERROR_LOG_PATH}",
        },
    )


# React (Vite) build, committed to git - end-user machines never run npm, the
# launcher's auto-update just syncs whatever's already built into dist/.
DIST_DIR = os.path.join(os.path.dirname(__file__), "static", "dist")
app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")

from app.ingest.images import IMAGES_ROOT as _IMAGES_ROOT
os.makedirs(_IMAGES_ROOT, exist_ok=True)
app.mount("/images", StaticFiles(directory=_IMAGES_ROOT), name="images")


# ---------- Static ----------
@app.get("/")
def index():
    # Vite content-hashes asset filenames itself, so no manual cache-busting
    # needed here (the old __CACHE_VERSION__ template was for the previous
    # hand-written static/app.js + style.css).
    with open(os.path.join(DIST_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


# ---------- Lifecycle ----------
@app.post("/api/quit")
def api_quit():
    """The Mac launcher is an AppleScript app with no Dock/menu-bar quit most
    users will find, so the UI gets its own kill switch. Exit this process
    after the response has gone out - there is no --reload supervisor to
    respawn it, and os._exit works the same on Mac and Windows."""
    threading.Timer(0.3, os._exit, args=(0,)).start()
    return {"ok": True}


# ---------- Lectures ----------
@app.get("/api/lectures")
def api_lectures():
    from app.stats import lecture_stats
    cov_by_id = {s["lecture_id"]: s for s in lecture_stats()}
    lectures = list_lectures()
    out = []
    for l in lectures:
        d = dict(l)
        cov = cov_by_id.get(d["id"])
        d["coverage_pct"] = cov["coverage_pct"] if cov else 0
        d["practiced_pct"] = cov["practiced_pct"] if cov else 0
        d["slides_covered"] = cov["slides_covered"] if cov else 0
        d["slides_practiced"] = cov["slides_practiced"] if cov else 0
        d["slides_total"] = cov["slides_total"] if cov else 0
        out.append(d)
    return out


UPLOAD_DIR = os.path.join(os.path.dirname(DB_PATH), "..", "lectures")


def _save_upload(f):
    """Stream an upload into lectures/ under a name nothing else is using.
    Writing straight to lectures/<filename> overwrote an existing lecture's
    source file on re-upload (image extraction reads it later), and the
    duplicate check then deleted it outright."""
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(f.filename or "upload"))
    dest = os.path.join(UPLOAD_DIR, stem + ext)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(UPLOAD_DIR, f"{stem} ({n}){ext}")
        n += 1
    with open(dest, "wb") as out:
        shutil.copyfileobj(f.file, out)
    return dest


# Plain `def`, not `async def`: parsing a big deck is slow, blocking work, and
# in an async route it froze the whole server (every other request stalled).
@app.post("/api/lectures/import")
def api_import_lectures(files: list[UploadFile] = File(...), force: bool = Form(False)):
    """force=true imports even if a file looks like a duplicate ("Import anyway")."""
    imported = []
    errors = []
    duplicates = []
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".pdf", ".pptx"):
            errors.append(f"{f.filename}: unsupported (use .pdf or .pptx)")
            continue
        dest = None
        try:
            dest = _save_upload(f)
            # Parse once; the duplicate check and the import share the result.
            from app.ingest.duplicates import detect_duplicate_file
            from app.ingest.slides import extract_slides
            slides = extract_slides(dest)
            dup = None if force else detect_duplicate_file(dest, slides=slides)
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
            lid = import_lecture(dest, slides=slides)
            imported.append(lid)
            # New users have no idea OCR/captions/summaries are separate manual
            # steps, and shouldn't need to - kick off the full pipeline right
            # away so a freshly imported lecture is already usable by the time
            # they click into it.
            _queue_analysis(lid)
        except Exception as e:
            errors.append(f"{f.filename}: {e}")
            if dest and os.path.exists(dest):
                os.remove(dest)  # unreadable upload - don't leave it in lectures/
    return {"imported": imported, "errors": errors, "duplicates": duplicates}


@app.post("/api/question_sets/import")
def api_import_question_set(
    lecture_id: int = Form(...), files: list[UploadFile] = File(...)
):
    """Import a professor's practice-question handout as real questions attached to a
    lecture. These bypass the slide pipeline entirely — see ingest/questionsets.py."""
    from app.ingest.questionsets import import_question_set

    conn = get_conn()
    lec = conn.execute("SELECT id FROM lectures WHERE id=?", (lecture_id,)).fetchone()
    conn.close()
    if not lec:
        raise HTTPException(404, "Lecture not found")

    imported = dupes = 0
    errors = []
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".pdf", ".pptx"):
            errors.append(f"{f.filename}: unsupported (use .pdf or .pptx)")
            continue
        try:
            res = import_question_set(_save_upload(f), lecture_id)
            imported += res["imported"]
            dupes += res["skipped_duplicates"]
        except Exception as e:
            errors.append(f"{f.filename}: {e}")
    return {"imported": imported, "skipped_duplicates": dupes, "errors": errors}


DEFAULT_TAG = "foundations"
MAX_TAG_LEN = 40


MAX_WEEK_LEN = 40


class LectureUpdate(BaseModel):
    # `x: int = None` is NOT optional in pydantic v2 — the default is None but the type
    # stays strict, so sending null 422s. `| None` is what makes "clear the week" work.
    title: str | None = None
    tag: str | None = None
    # Free-text label ("3", "A", "Midterm review"); ints still accepted.
    week: str | int | None = None


@app.patch("/api/lectures/{lid}")
def api_update_lecture(lid: int, body: LectureUpdate):
    """Rename, retag, and/or set the course week. All fields optional; only what's
    sent changes. Sending week=null clears it."""
    fields, params = [], []
    if body.title is not None:
        title = " ".join(body.title.split())[:200]   # collapse pasted whitespace/newlines
        if not title:
            raise HTTPException(400, "Title cannot be empty")
        fields.append("title=?")
        params.append(title)
    if body.tag is not None:
        # Free text; blank resets to the default strand.
        tag = " ".join(body.tag.split())[:MAX_TAG_LEN] or DEFAULT_TAG
        fields.append("tag=?")
        params.append(tag)
    # model_fields_set distinguishes "week omitted" from "week: null" (clear it).
    if "week" in body.model_fields_set:
        week = " ".join(str(body.week).split())[:MAX_WEEK_LEN] if body.week is not None else ""
        fields.append("week=?")
        params.append(week or None)   # blank clears it
    if not fields:
        raise HTTPException(400, "Nothing to update")

    conn = get_conn()
    cur = conn.execute(
        f"UPDATE lectures SET {', '.join(fields)} WHERE id=?", (*params, lid)
    )
    conn.commit()
    if not cur.rowcount:
        conn.close()
        raise HTTPException(404, "Lecture not found")
    row = conn.execute(
        "SELECT id, title, tag, week FROM lectures WHERE id=?", (lid,)
    ).fetchone()
    conn.close()
    return dict(row)


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
    from app.ingest.images import images_by_slide
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM slides WHERE lecture_id=? ORDER BY slide_num", (lid,)
    ).fetchall()
    conn.close()
    imgs = images_by_slide([r["id"] for r in rows])
    return [{**dict(r), "images": imgs.get(r["id"], [])} for r in rows]


@app.get("/api/lectures/{lid}/slide/{num}")
def api_lecture_slide(lid: int, num: int):
    """One slide by its number — backs the expandable footnotes in summaries and
    question explanations."""
    from app.ingest.images import images_for_slides, extract_lecture_images

    conn = get_conn()
    row = conn.execute(
        "SELECT s.id, s.slide_num, s.text, s.caption, s.ocr_text, l.title AS lecture_title, l.source "
        "FROM slides s JOIN lectures l ON l.id=s.lecture_id "
        "WHERE s.lecture_id=? AND s.slide_num=?",
        (lid, num),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Slide not found")
    d = dict(row)
    d["images"] = images_for_slides([d["id"]])
    if not d["images"] and d.pop("source") == "pdf":
        # Page renders missing (import-time extraction failed, or a pre-images upload):
        # redo it once so the slide shows instead of only its text.
        try:
            extract_lecture_images(lid)
            d["images"] = images_for_slides([d["id"]])
        except Exception as e:
            print(f"lazy image extraction for lecture {lid} failed: {e}")
    d.pop("source", None)
    return d


@app.get("/api/lectures/{lid}/images")
def api_lecture_images(lid: int):
    from app.ingest.images import images_for_lecture
    return images_for_lecture(lid)


@app.post("/api/lectures/{lid}/extract_images")
def api_extract_images(lid: int):
    from app.ingest.images import extract_lecture_images
    count, kind = extract_lecture_images(lid)
    return {"count": count, "kind": kind}


def _analyze_lecture(lid, force_captions=False):
    """The full pipeline: OCR -> captions -> summary. Runs automatically right
    after import (new users never have to know these steps exist), and is also
    what the "Reanalyze" button re-runs on demand. Each step is isolated so one
    failure doesn't skip the rest; the first failure's reason is stored on the
    lecture (analysis_error) so the UI can show it instead of failing silently."""
    errors = []

    def step(name, fn):
        try:
            fn()
        except Exception as e:
            print(f"{name} failed for lecture {lid}: {e}")
            errors.append(f"{name}: {e}")

    def ocr():
        from app.ingest.ocr import run_ocr_for_lecture
        run_ocr_for_lecture(lid)

    def captions():
        from app.llm.captions import generate_captions_for_lecture
        generate_captions_for_lecture(lid, force=force_captions)

    step("OCR", ocr)
    step("Captions", captions)
    step("Summary", lambda: generate_summary(lid))
    conn = get_conn()
    conn.execute("UPDATE lectures SET analysis_error=? WHERE id=?", ("; ".join(errors)[:500], lid))
    conn.commit()
    conn.close()


# One worker: importing 10 lectures used to start 10 threads x (4 caption
# workers + summary) = 40+ simultaneous AI calls -> 429s and SQLite lock fights.
# Lectures are analysed one at a time, in upload order.
_analysis_pool = ThreadPoolExecutor(max_workers=1)


def _queue_analysis(lid, force_captions=False):
    # Mark queued lectures as busy right away so the UI shows "Analyzing…"
    # (and polls) while they wait their turn.
    conn = get_conn()
    conn.execute("UPDATE lectures SET summary_status='generating', analysis_error='' WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    _analysis_pool.submit(_analyze_lecture, lid, force_captions)


@app.post("/api/lectures/{lid}/ocr")
def api_ocr_lecture(lid: int):
    """Run OCR on all slide images for a lecture (background)."""
    # force_captions=False: OCR is deterministic, so re-running it usually yields
    # the same text and existing captions stay valid. /reanalyze is the explicit
    # "redo everything" path.
    _queue_analysis(lid)
    return {"status": "started"}


@app.post("/api/lectures/{lid}/reanalyze")
def api_reanalyze_lecture(lid: int):
    """Full redo: OCR + captions + summary, forcing regeneration of all three."""
    _queue_analysis(lid, force_captions=True)
    return {"status": "started"}


@app.post("/api/lectures/{lid}/captions")
def api_captions_lecture(lid: int):
    """Generate reasoned image captions from OCR text (background)."""
    from app.llm.captions import generate_captions_for_lecture
    _analysis_pool.submit(generate_captions_for_lecture, lid, True)
    return {"status": "started"}


@app.get("/api/lectures/{lid}/ocr/status")
def api_ocr_status(lid: int):
    from app.ingest.ocr import lecture_ocr_status
    return {"status": lecture_ocr_status(lid)}


@app.delete("/api/lectures/{lid}")
def api_delete_lecture(lid: int):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, filename FROM lectures WHERE id=?", (lid,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Lecture not found")
    # Remove everything belonging to this lecture (FK cascades handle the rest)
    conn.execute("DELETE FROM lectures WHERE id=?", (lid,))
    conn.commit()
    # ...and its files, which otherwise stay on disk forever. Another lecture
    # may share the source filename only via a bug; _save_upload makes names unique.
    from app.ingest.images import IMAGES_ROOT
    shutil.rmtree(os.path.join(IMAGES_ROOT, str(lid)), ignore_errors=True)
    if row["filename"]:
        try:
            os.remove(os.path.join(UPLOAD_DIR, os.path.basename(row["filename"])))
        except OSError:
            pass
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
    result = generate_summary(lid)
    return {"status": "done", "key_points": len(result.get("key_points", []))}


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


# ---------- API usage ----------
@app.get("/api/config")
def api_get_config():
    from app.config import current_config
    return current_config()


@app.post("/api/config")
def api_save_config(body: dict):
    from app.config import save_config
    return save_config(body)


@app.get("/api/usage")
def api_usage():
    """Token spend, so the cost of a session is visible instead of invisible."""
    from app.llm.client import active_model

    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) prompt, "
        "COALESCE(SUM(cached_tokens),0) cached, COALESCE(SUM(completion_tokens),0) completion "
        "FROM api_usage WHERE date(created_at)=date('now')"
    ).fetchone()
    total = conn.execute(
        "SELECT COUNT(*) calls, COALESCE(SUM(prompt_tokens),0) prompt, "
        "COALESCE(SUM(cached_tokens),0) cached, COALESCE(SUM(completion_tokens),0) completion "
        "FROM api_usage"
    ).fetchone()
    by_kind = conn.execute(
        "SELECT kind, COUNT(*) calls, COALESCE(SUM(prompt_tokens+completion_tokens),0) tokens "
        "FROM api_usage WHERE date(created_at)=date('now') GROUP BY kind ORDER BY tokens DESC"
    ).fetchall()
    conn.close()

    provider, model = active_model()
    behind = _commits_behind_main()

    def shape(r):
        prompt = r["prompt"]
        return {
            "calls": r["calls"],
            "prompt_tokens": prompt,
            "cached_tokens": r["cached"],
            "completion_tokens": r["completion"],
            "total_tokens": prompt + r["completion"],
            "cache_hit_pct": round(100.0 * r["cached"] / prompt, 1) if prompt else 0.0,
        }

    return {
        "provider": provider,
        "model": model,
        "version": APP_VERSION,
        "updates_behind": behind,
        "update_available": behind > 0,
        "today": shape(row),
        "all_time": shape(total),
        "today_by_kind": [dict(r) for r in by_kind],
    }


# ---------- Mastery projection ----------
@app.get("/api/mastery_projection")
def api_mastery_projection():
    """Simulated questions still needed to master every slide. Recomputed each call,
    so it tracks your accuracy as it changes."""
    from app.projection import mastery_projection
    return mastery_projection()


# ---------- Study recommendations ----------
@app.post("/api/sessions/{sid}/recommendations")
def api_generate_recommendations(sid: int, force: bool = False):
    """Analyse this session's mistakes into saved study guidance (one API call, cached
    in the database — regenerating only happens with force=true)."""
    from app.llm.recommendations import generate_recommendations
    rec = generate_recommendations(sid, force=force)
    if not rec:
        return {"recommendations": None, "message": "No mistakes in this session."}
    return {"recommendations": rec}


@app.get("/api/sessions/{sid}/recommendations")
def api_get_recommendations(sid: int):
    from app.llm.recommendations import get_recommendations
    return {"recommendations": get_recommendations(sid)}


@app.get("/api/recommendations")
def api_recent_recommendations(limit: int = 10):
    """Saved guidance across sessions — the running record of what to work on."""
    from app.llm.recommendations import recent_recommendations
    return recent_recommendations(limit)


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
        sid, count = create_review_session(lecture_ids=lids, time_mode=time_mode, target=target)
        reason = "Nothing to review yet — miss a question first and it lands here."
    else:
        sid, count, reason = create_practice_session(
            lecture_ids=lids, target=target, time_mode=time_mode
        )
    if count == 0:
        return {"session_id": None, "question_count": 0, "message": reason}
    return {"session_id": sid, "question_count": count}


@app.get("/api/missed")
def api_missed():
    """Unresolved missed questions count + breakdown by lecture."""
    from app.sessiongen import missed_count, missed_by_lecture
    return {"count": missed_count(), "by_lecture": missed_by_lecture()}


@app.get("/api/sessions")
def api_list_sessions():
    """All past sessions (newest first), for the Drill 'past sessions' panel.

    sessions.lecture_id only ever records the FIRST lecture a session drew
    from (or nothing, for an all-lectures session) - not which lectures are
    actually in it. Derive the real set from the questions each session
    actually contains, so the list can show it for clarity.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY updated_at DESC, id DESC"
    ).fetchall()
    lecture_rows = conn.execute(
        "SELECT DISTINCT sq.session_id, l.title "
        "FROM session_questions sq "
        "JOIN questions q ON q.id = sq.question_id "
        "JOIN lectures l ON l.id = q.lecture_id"
    ).fetchall()
    conn.close()
    titles_by_session = {}
    for r in lecture_rows:
        titles_by_session.setdefault(r["session_id"], []).append(r["title"])
    out = []
    for r in rows:
        d = dict(r)
        d["lecture_titles"] = titles_by_session.get(d["id"], [])
        out.append(d)
    return out


@app.delete("/api/sessions/{sid}")
def api_delete_session(sid: int):
    """Remove a session from the history.

    Only the session and its question links go — the questions themselves, your answer
    history, and anything tagged missed all survive, so clearing history never erases
    what you have learned or what you still owe a review.
    """
    conn = get_conn()
    cur = conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
    conn.execute("DELETE FROM session_questions WHERE session_id=?", (sid,))
    conn.commit()
    conn.close()
    if not cur.rowcount:
        raise HTTPException(404, "Session not found")
    return {"deleted": sid}


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
        "a.selected_index, a.correct AS selected_correct, q.slide_id, q.lecture_id, "
        "q.source AS question_source, "
        "l.title AS lecture_title, l.week AS lecture_week, s.slide_num, s.text AS slide_text, s.caption AS slide_caption "
        "FROM session_questions sq JOIN questions q ON q.id=sq.question_id "
        "LEFT JOIN lectures l ON l.id=q.lecture_id "
        "LEFT JOIN answers a ON a.question_id=q.id AND a.session_id=? "
        "LEFT JOIN slides s ON s.id=q.slide_id "
        "WHERE sq.session_id=? ORDER BY sq.position",
        (sid, sid),
    ).fetchall()
    conn.close()
    from app.ingest.images import images_by_slide
    imgs = images_by_slide([r["slide_id"] for r in rows])
    out = []
    for r in rows:
        d = dict(r)
        d["options"] = json.loads(d["options"])
        d["source_images"] = imgs.get(d["slide_id"], [])
        out.append(d)
    return {"session": dict(s), "questions": out}


@app.get("/api/sessions/{sid}/nav")
def api_session_nav(sid: int):
    """One row per question in the session: position, whether it's answered, and how.
    Drives the numbered strip that lets you jump back and fix a misclick."""
    conn = get_conn()
    s = conn.execute("SELECT id FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    rows = conn.execute(
        "SELECT sq.position, sq.question_id, sq.answered, "
        "a.selected_index, a.correct "
        "FROM session_questions sq "
        "LEFT JOIN answers a ON a.question_id=sq.question_id AND a.session_id=? "
        "WHERE sq.session_id=? ORDER BY sq.position",
        (sid, sid),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


QUESTION_SELECT = (
    "SELECT sq.*, q.question, q.options, q.correct_index, q.explanation, q.level, q.slide_id, "
    "q.lecture_id, q.source AS question_source, "
    "l.title AS source_lecture_title, l.week AS source_lecture_week, "
    "s.slide_num AS source_slide_num, s.text AS source_slide_text, "
    "s.caption AS source_slide_caption, "
    "a.selected_index AS prior_selected_index, a.correct AS prior_correct "
    "FROM session_questions sq JOIN questions q ON q.id = sq.question_id "
    "LEFT JOIN lectures l ON l.id = q.lecture_id "
    "LEFT JOIN slides s ON s.id = q.slide_id "
    "LEFT JOIN answers a ON a.question_id = q.id AND a.session_id = sq.session_id "
)


def _question_payload(row):
    """Shape one question row for the drill UI (shared by /next and /question/{qid})."""
    from app.ingest.images import images_for_slides

    q = dict(row)
    q["options"] = json.loads(q["options"])
    q["id"] = q["question_id"]
    q["source_images"] = images_for_slides([q["slide_id"]] if q.get("slide_id") else [])
    q["source_slide"] = {
        "lecture_id": q.get("lecture_id"),
        "lecture_title": q.get("source_lecture_title") or "",
        "lecture_week": q.get("source_lecture_week"),
        "slide_num": q.get("source_slide_num"),
        "text": q.get("source_slide_text") or "",
        "caption": q.get("source_slide_caption") or "",
    }
    return q


@app.get("/api/sessions/{sid}/question/{question_id}")
def api_session_question(sid: int, question_id: int):
    """Load one specific question in a session, with the answer you gave (if any),
    so you can revisit and change it mid-session."""
    conn = get_conn()
    row = conn.execute(
        QUESTION_SELECT + "WHERE sq.session_id=? AND sq.question_id=?",
        (sid, question_id),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Not in session")
    return {"done": False, "question": _question_payload(row)}


@app.get("/api/sessions/{sid}/next")
def api_next_question(sid: int, after: int = None):
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close()
        raise HTTPException(404, "Session not found")
    # `after`: the position being left, so "Next" on an unanswered question moves
    # on to the following unanswered one (wrapping to the first) instead of
    # reloading the same question.
    row = None
    if after is not None:
        row = conn.execute(
            QUESTION_SELECT + "WHERE sq.session_id=? AND sq.answered=0 AND sq.position>? "
            "ORDER BY sq.position LIMIT 1",
            (sid, after),
        ).fetchone()
    if row is None:
        row = conn.execute(
            QUESTION_SELECT + "WHERE sq.session_id=? AND sq.answered=0 "
            "ORDER BY sq.position LIMIT 1",
            (sid,),
        ).fetchone()
    conn.close()
    if not row:
        return {"done": True}
    return {"done": False, "question": _question_payload(row)}


def _tag_missed(conn, qid, lecture_id):
    """Mark a question as missed, keeping exactly one row per question.

    The old `INSERT ... ON CONFLICT DO NOTHING` was a no-op guard — `missed` has no
    unique constraint on question_id, so nothing ever conflicted and every miss after
    the first added a duplicate row, inflating the "to review" count.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "UPDATE missed SET resolved=0, last_wrong_at=? WHERE question_id=?", (now, qid)
    )
    if not cur.rowcount:
        conn.execute(
            "INSERT INTO missed(question_id, lecture_id, missed_at, resolved, last_wrong_at) "
            "VALUES(?,?,?,0,?)",
            (qid, lecture_id, now, now),
        )


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
        "WHERE q.session_id=? AND q.question_id=?",
        (sid, question_id),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Not in session")
    qid = row["question_id"]
    correct = body.selected_index == row["correct_index"]
    # Tutor mode already showed the answer, so a change there may update the
    # score but never clears a real miss from the review queue (below).
    tutor = conn.execute("SELECT tutor_mode FROM sessions WHERE id=?", (sid,)).fetchone()["tutor_mode"]

    # Re-answering a question you already did (fixing a misclick) updates the existing
    # row rather than stacking a second one, so accuracy reflects your latest answer
    # and the progress count doesn't drift upward on every revision.
    prior = conn.execute(
        "SELECT id FROM answers WHERE session_id=? AND question_id=? ORDER BY id DESC LIMIT 1",
        (sid, qid),
    ).fetchone()
    if prior:
        conn.execute(
            "UPDATE answers SET correct=?, selected_index=?, answered_at=datetime('now') "
            "WHERE id=?",
            (1 if correct else 0, body.selected_index, prior["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO answers(question_id, session_id, correct, selected_index) VALUES(?,?,?,?)",
            (qid, sid, 1 if correct else 0, body.selected_index),
        )
        conn.execute(
            "UPDATE sessions SET completed_count = completed_count + 1 WHERE id=?", (sid,)
        )
    conn.execute(
        "UPDATE session_questions SET answered=1 WHERE session_id=? AND question_id=?",
        (sid, qid),
    )
    conn.execute("UPDATE sessions SET updated_at=datetime('now') WHERE id=?", (sid,))

    # Tag wrong answers as missed (resolved on next-day review)
    if not correct:
        _tag_missed(conn, qid, row["lecture_id"])
    elif not (prior and tutor):
        # correct in a practice/review session -> mark resolved if it was missed
        conn.execute("UPDATE missed SET resolved=1 WHERE question_id=?", (qid,))

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
        "revised": bool(prior),
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
        _tag_missed(conn, qid, r["lecture_id"])
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
