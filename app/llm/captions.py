import json

from app.db import get_conn
from app.llm.client import chat_json

CAPTION_SYSTEM = (
    "You are a medical educator writing concise image captions for lecture slides. "
    "Given a slide's text and OCR text extracted from its images, produce a SHORT caption "
    "(1-3 sentences, under 60 words) that tells a medical student what the image(s) on this "
    "slide show and the high-yield takeaway. Reason over the OCR text rather than dumping it. "
    "If the OCR is just noise (page numbers, logos, fragments), summarize it into the meaningful "
    "content or say 'No meaningful image content.' Never invent facts not supported by the input."
)

CAPTION_PROMPT = """Slide {slide_num} of a lecture titled "{title}".

SLIDE TEXT:
{slide_text}

IMAGE TEXT (OCR from the slide's images):
{ocr_text}

Write a caption for this slide's image(s). JSON: {{"caption": "..."}}"""


def _slides_needing_captions(lecture_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, slide_num, text, ocr_text, caption FROM slides "
        "WHERE lecture_id=? AND (ocr_text IS NOT NULL AND length(trim(ocr_text)) > 0) "
        "ORDER BY slide_num",
        (lecture_id,),
    ).fetchall()
    conn.close()
    return rows


def generate_captions_for_lecture(lecture_id, force=False):
    """Generate a reasoned caption for each slide that has OCR text.
    Returns number of captions generated."""
    conn = get_conn()
    row = conn.execute(
        "SELECT title FROM lectures WHERE id=?", (lecture_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No lecture {lecture_id}")
    title = row["title"]

    slides = _slides_needing_captions(lecture_id)
    if force:
        slides = _slides_needing_captions(lecture_id)  # force just regenerates all
    generated = 0
    for s in slides:
        if not force and s["caption"] and s["caption"].strip():
            continue
        try:
            result = chat_json(
                CAPTION_PROMPT.format(
                    slide_num=s["slide_num"],
                    title=title,
                    slide_text=(s["text"] or "").strip() or "(no extractable text)",
                    ocr_text=(s["ocr_text"] or "").strip(),
                ),
                system=CAPTION_SYSTEM,
                temperature=0.3,
                max_tokens=300,
            )
            caption = (result.get("caption") or "").strip()
        except Exception as e:
            print(f"caption failed for slide {s['slide_num']}: {e}")
            continue
        if caption:
            conn = get_conn()
            conn.execute(
                "UPDATE slides SET caption=? WHERE id=?", (caption, s["id"])
            )
            conn.commit()
            conn.close()
            generated += 1
    return generated
