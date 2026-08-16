import json

from app.db import get_conn
from app.llm.client import chat_json

SUMMARY_SYSTEM = (
    "You are a senior medical school lecturer writing a condensed study summary "
    "for a first-year medical student about to take a block exam. "
    "You are precise, organized, and prioritize memorizable high-yield facts. "
    "You NEVER invent facts not present in the provided lecture text. "
    "Format tables for anything tabular (transfer categories, timing, lists of structures)."
)

SUMMARY_PROMPT = """Here is the raw text extracted from a lecture titled "{title}" (slide-by-slide).

LECTURE TEXT:
{text}

Write a condensed study summary with EXACTLY this JSON structure:
{{
  "summary": "Markdown. 400-800 words. Logical narrative grouped into sections with headers (##). Include every distinct concept from the lecture - do not drop facts. Use tables where the source material is tabular.",
  "key_points": ["list of 8-15 short, high-yield, memorizable bullets - the facts most likely to appear on a single-best-answer MCQ exam"]
}}

Rules:
- If a slide is a title/header slide or has no meaningful content, skip it.
- Preserve exact numbers, dates, names, and classifications from the source.
- For anything clearly missing or empty, do not fabricate.
- CITATIONS: At the end of each paragraph (or each bullet in key_points) that drew facts from specific slides, add a citation in this exact format: (slides N, M) or (slide N) — using the slide numbers shown in the "--- SLIDE N ---" markers in the source. Only cite slides you actually used for that paragraph. If a paragraph synthesizes many slides, list the range, e.g. (slides 4-9). Do NOT add citations to header-only paragraphs.
"""


def build_lecture_text(lecture_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT slide_num, text, ocr_text, caption FROM slides WHERE lecture_id=? ORDER BY slide_num",
        (lecture_id,),
    ).fetchall()
    conn.close()
    parts = []
    for r in rows:
        slide_text = r["text"] or ""
        ocr = r["ocr_text"] or ""
        caption = r["caption"] or ""
        combined = slide_text
        if caption.strip():
            combined += "\n[Image caption]: " + caption
        elif ocr.strip():
            combined += "\n[IMAGE TEXT via OCR]:\n" + ocr
        parts.append(f"--- SLIDE {r['slide_num']} ---\n{combined}")
    return "\n".join(parts)


def generate_summary(lecture_id, on_progress=None):
    conn = get_conn()
    row = conn.execute(
        "SELECT title FROM lectures WHERE id=?", (lecture_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No lecture {lecture_id}")

    conn = get_conn()
    conn.execute(
        "UPDATE lectures SET summary_status='generating' WHERE id=?", (lecture_id,)
    )
    conn.commit()
    conn.close()

    text = build_lecture_text(lecture_id)
    try:
        result = chat_json(
            SUMMARY_PROMPT.format(title=row["title"], text=text),
            system=SUMMARY_SYSTEM,
            temperature=0.3,
            max_tokens=4000,
            kind="summary",
        )
    except Exception as e:
        conn = get_conn()
        conn.execute(
            "UPDATE lectures SET summary_status='error' WHERE id=?", (lecture_id,)
        )
        conn.commit()
        conn.close()
        raise e

    conn = get_conn()
    conn.execute(
        "INSERT INTO summaries(lecture_id, body, key_points) VALUES(?,?,?) "
        "ON CONFLICT(lecture_id) DO UPDATE SET body=excluded.body, "
        "key_points=excluded.key_points, created_at=datetime('now')",
        (lecture_id, result["summary"], json.dumps(result["key_points"])),
    )
    conn.execute(
        "UPDATE lectures SET summary_status='done' WHERE id=?", (lecture_id,)
    )
    conn.commit()
    conn.close()
    return result


def get_summary(lecture_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM summaries WHERE lecture_id=?", (lecture_id,)
    ).fetchone()
    conn.close()
    return row
