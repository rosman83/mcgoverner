import os
import re

from app.db import get_conn, DB_PATH
from app.llm.client import describe_image

IMAGES_ROOT = os.path.join(os.path.dirname(DB_PATH), "images")

# Below this many OCR chars, treat the slide as "not really OCR'd" - either no text
# was found (pure figure) or Vision only caught scattered axis/legend labels with no
# real content (e.g. a research graph). Fall back to an OpenRouter vision model on
# the first image.
# ponytail: char-count heuristic, not word-density. Lower it if label-heavy diagrams
# still slip through with a caption too shallow to question on.
VISION_FALLBACK_MAX_CHARS = 60

VISION_PROMPT = (
    "This is an image from a medical school lecture slide. Describe factually what it "
    "shows: the type of figure (graph, diagram, micrograph, anatomical illustration, "
    "flowchart, etc.), the specific structures, curves, phases, or labels present, and "
    "the key relationship or finding it illustrates. Be specific - name the axes, phases, "
    "or structures shown. Do not invent anything not visible in the image. Plain text, "
    "under 150 words, no markdown."
)


def _ocr_image_apple(path):
    """OCR an image using Apple's built-in Vision framework (free, on-device)."""
    try:
        import Vision
        from Foundation import NSURL
        from Quartz import CIImage
    except ImportError:
        return None

    url = NSURL.fileURLWithPath_(path)
    ci = CIImage.imageWithContentsOfURL_(url)
    if not ci:
        return ""
    handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(ci, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    handler.performRequests_error_([req], None)
    results = req.results() or []
    lines = []
    for r in results:
        c = r.topCandidates_(1)
        if c and len(c):
            lines.append(c[0].string())
    return "\n".join(lines)


def _ocr_image(path):
    """OCR an image; returns recognized text string."""
    try:
        return _ocr_image_apple(path) or ""
    except Exception:
        return ""


def run_ocr_for_lecture(lecture_id):
    """OCR every slide image for a lecture; store per-slide OCR text.
    Returns count of slides OCR'd and total chars extracted.

    Vision-fallback calls are network round trips, so a lecture with several
    image-only slides can now take minutes instead of milliseconds. A re-click of
    the OCR button (or a second concurrent trigger) used to spawn a duplicate run
    against the same lecture, and two long-lived connections fighting over SQLite's
    single writer lock is exactly what produced "database is locked". Guard against
    that at the source: skip if a run is already in flight.
    """
    conn = get_conn()
    row = conn.execute("SELECT ocr_status FROM lectures WHERE id=?", (lecture_id,)).fetchone()
    if row and row["ocr_status"] == "running":
        conn.close()
        return {"slides_ocr": 0, "chars": 0, "skipped": "already_running"}
    conn.execute("UPDATE lectures SET ocr_status='running' WHERE id=?", (lecture_id,))
    conn.commit()

    slides = conn.execute(
        "SELECT id, slide_num FROM slides WHERE lecture_id=? ORDER BY slide_num",
        (lecture_id,),
    ).fetchall()
    slide_ids = [r["id"] for r in slides]
    images = {}
    if slide_ids:
        ph = ",".join("?" * len(slide_ids))
        rows = conn.execute(
            f"SELECT slide_id, path FROM slide_images WHERE slide_id IN ({ph}) "
            f"ORDER BY slide_id, seq",
            tuple(slide_ids),
        ).fetchall()
        for r in rows:
            images.setdefault(r["slide_id"], []).append(r["path"])
    conn.close()

    ocr_counts = 0
    total_chars = 0
    conn = get_conn()
    try:
        for slide_id, paths in images.items():
            texts = []
            for rel in paths:
                abs_path = os.path.join(IMAGES_ROOT, rel)
                if os.path.exists(abs_path):
                    t = _ocr_image(abs_path)
                    if t.strip():
                        texts.append(t)
            joined = "\n".join(texts).strip()
            if len(joined) < VISION_FALLBACK_MAX_CHARS and paths:
                first_abs = os.path.join(IMAGES_ROOT, paths[0])
                if os.path.exists(first_abs):
                    try:
                        desc = describe_image(first_abs, VISION_PROMPT, kind="slide_vision")
                    except Exception as e:
                        print(f"vision describe failed for {first_abs}: {e}")
                        desc = ""
                    if desc:
                        joined = f"{joined}\n{desc}".strip() if joined else desc
            if joined:
                conn.execute(
                    "UPDATE slides SET ocr_text=? WHERE id=?",
                    (joined, slide_id),
                )
                # Commit per slide so the write lock is never held across the vision
                # fallback's network calls — those can now stretch this loop from
                # milliseconds (local OCR only) to minutes.
                conn.commit()
                ocr_counts += 1
                total_chars += len(joined)
    except Exception:
        conn.execute("UPDATE lectures SET ocr_status='error' WHERE id=?", (lecture_id,))
        conn.commit()
        conn.close()
        raise  # otherwise ocr_status stays 'running' forever and blocks every retry

    conn.execute(
        "UPDATE lectures SET ocr_status=? WHERE id=?",
        ("done" if ocr_counts else "no_images", lecture_id),
    )
    conn.commit()
    conn.close()
    return {"slides_ocr": ocr_counts, "chars": total_chars}


def lecture_ocr_status(lecture_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT ocr_status FROM lectures WHERE id=?", (lecture_id,)
    ).fetchone()
    conn.close()
    return row["ocr_status"] if row else None
