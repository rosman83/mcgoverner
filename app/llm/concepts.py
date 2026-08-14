import json

from app.db import get_conn
from app.llm.client import chat_json

CONCEPT_SYSTEM = (
    "You are a medical curriculum expert extracting testable concepts from lecture slides. "
    "Your job is to break a slide down into the distinct facts that could be tested on a "
    "single-best-answer MCQ exam. Aim for 2-4 well-scoped concepts per slide — enough to "
    "cover the material without fragmenting it. Group closely related facts (e.g. several "
    "morphogens on one figure, a clinical correlate plus its mechanism) into one concept. "
    "You only use information present in the provided text — you never invent content."
)

CONCEPT_PROMPT = """Extract the distinct testable concepts from the following medical lecture slide.

LECTURE TITLE: {title}
SLIDE NUMBER: {slide_num}

SLIDE TEXT:
{slide_text}

IMAGE CAPTION (if any):
{caption}

Return ONLY JSON with this exact structure:
{{
  "concepts": [
    {{
      "label": "Short concept name (2-8 words). The thing being tested, e.g. 'Morphogen gradients (Shh, FGF, BMP)' or 'Gastrulation cell layers' or 'Clinical correlate: neural tube defects'.",
      "detail": "The specific testable fact. 1-3 sentences pulled from the slide text. Include exact names, numbers, classifications, mechanisms.",
      "level": "recall" | "application" | "clinical"
    }}
  ]
}}

Rules:
- Extract 2-4 concepts per slide. Prefer fewer, well-rounded concepts over many tiny fragments.
- Group related facts together (a family of morphogens, a structure plus its function, a clinical correlate plus its mechanism).
- Do NOT include slide headers, professor names, objectives statements, or 'image resources' as concepts.
- 'detail' must be a faithful extraction of the slide text — do not add external facts.
- If the slide has no meaningful testable content, return "concepts": [].
"""


def _slide_text(conn, slide_id):
    row = conn.execute(
        "SELECT text, caption FROM slides WHERE id=?", (slide_id,)
    ).fetchone()
    if not row:
        return "", ""
    return (row["text"] or ""), (row["caption"] or "")


def extract_concepts_for_slide(lecture_id, slide_id, slide_num, title, force=False):
    """Extract granular concepts for a single slide via DeepSeek.
    Returns list of new concept ids. Skips slides already extracted unless force=True."""
    conn = get_conn()
    text, caption = _slide_text(conn, slide_id)
    existing = conn.execute(
        "SELECT COUNT(*) c FROM concepts WHERE lecture_id=? AND slide_ids=?",
        (lecture_id, json.dumps([slide_id])),
    ).fetchone()["c"]
    conn.close()

    if existing and not force:
        return []

    if not text.strip():
        return []

    try:
        result = chat_json(
            CONCEPT_PROMPT.format(
                title=title,
                slide_num=slide_num,
                slide_text=text,
                caption=caption or "(none)",
            ),
            system=CONCEPT_SYSTEM,
            temperature=0.2,
            max_tokens=3000,
        )
    except Exception as e:
        print(f"concept extraction failed slide {slide_num}: {e}")
        return []

    concepts = result.get("concepts") or []
    created = []
    conn = get_conn()
    for c in concepts:
        label = (c.get("label") or "").strip()
        detail = (c.get("detail") or "").strip()
        if not label or not detail:
            continue
        level = c.get("level") or "recall"
        if level not in ("recall", "application", "clinical"):
            level = "recall"
        cur = conn.execute(
            "INSERT INTO concepts(lecture_id, label, slide_ids, slide_nums, status) "
            "VALUES(?,?,?,?, 'unseen')",
            (
                lecture_id,
                label,
                json.dumps([slide_id]),
                json.dumps([slide_num]),
            ),
        )
        # store the detail as a small hint we can feed to question generation later
        conn.execute(
            "INSERT INTO concept_details(concept_id, detail, level) VALUES(?,?,?)",
            (cur.lastrowid, detail, level),
        )
        created.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return created


def extract_concepts_for_lecture(lecture_id, force=False, on_progress=None):
    """Extract granular concepts for every slide in a lecture (background-friendly).
    Returns total new concepts created."""
    conn = get_conn()
    row = conn.execute(
        "SELECT title FROM lectures WHERE id=?", (lecture_id,)
    ).fetchone()
    slides = conn.execute(
        "SELECT id, slide_num FROM slides WHERE lecture_id=? ORDER BY slide_num",
        (lecture_id,),
    ).fetchall()
    conn.close()
    if not row:
        raise ValueError(f"No lecture {lecture_id}")
    title = row["title"]

    total = 0
    for i, s in enumerate(slides):
        created = extract_concepts_for_slide(
            lecture_id, s["id"], s["slide_num"], title, force=force
        )
        total += len(created)
        if on_progress:
            on_progress(i + 1, len(slides), total)
    return total


def dedupe_concepts(lecture_id=None):
    """Remove duplicate concepts (same lecture+slide_ids+label), keeping ones that
    already have questions. Returns number removed."""
    conn = get_conn()
    if lecture_id:
        concepts = conn.execute(
            "SELECT id, lecture_id, label, slide_ids FROM concepts "
            "WHERE lecture_id=? ORDER BY id", (lecture_id,)
        ).fetchall()
    else:
        concepts = conn.execute(
            "SELECT id, lecture_id, label, slide_ids FROM concepts ORDER BY id"
        ).fetchall()
    seen = {}
    removed = 0
    for c in concepts:
        key = (c["lecture_id"], c["slide_ids"], c["label"].strip().lower())
        if key in seen:
            # keep the one with questions, remove the other
            keep_id = seen[key]
            has_q_keep = conn.execute(
                "SELECT COUNT(*) c FROM questions q, json_each(q.concept_ids) je "
                "WHERE je.value = ?", (keep_id,)
            ).fetchone()["c"]
            has_q_this = conn.execute(
                "SELECT COUNT(*) c FROM questions q, json_each(q.concept_ids) je "
                "WHERE je.value = ?", (c["id"],)
            ).fetchone()["c"]
            if has_q_this and not has_q_keep:
                # this one has questions, keep it; remove the older one
                conn.execute("DELETE FROM concepts WHERE id=?", (keep_id,))
                seen[key] = c["id"]
                removed += 1
            else:
                conn.execute("DELETE FROM concepts WHERE id=?", (c["id"],))
                removed += 1
        else:
            seen[key] = c["id"]
    conn.commit()
    conn.close()
    return removed
