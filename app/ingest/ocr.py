import os
import re

from app.db import get_conn, DB_PATH

IMAGES_ROOT = os.path.join(os.path.dirname(DB_PATH), "images")


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
    Returns count of slides OCR'd and total chars extracted."""
    conn = get_conn()
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
    for slide_id, paths in images.items():
        texts = []
        for rel in paths:
            abs_path = os.path.join(IMAGES_ROOT, rel)
            if os.path.exists(abs_path):
                t = _ocr_image(abs_path)
                if t.strip():
                    texts.append(t)
        if texts:
            joined = "\n".join(texts)
            conn.execute(
                "UPDATE slides SET ocr_text=? WHERE id=?",
                (joined, slide_id),
            )
            ocr_counts += 1
            total_chars += len(joined)
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
