from concurrent.futures import ThreadPoolExecutor, as_completed

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

CAPTION_BATCH_SIZE = 10   # slides per API call
CAPTION_WORKERS = 4       # concurrent calls

# Static instructions first, variable slide text last, so the prefix stays cacheable
# across every batch (see the same treatment in sessiongen.QUESTION_BATCH_PROMPT).
CAPTION_BATCH_PROMPT = """Write one image caption for each of the {count} slides below.

Each slide is presented as:
SLIDE <index> | Slide number: <slide_num>
SLIDE TEXT: ...
IMAGE TEXT (OCR): ...

Return ONLY JSON:
{{
  "captions": [
    {{"slide_index": 0, "caption": "1-3 sentences, under 60 words."}}
  ]
}}

- Return exactly one entry per slide, with slide_index from 0 to {count_minus_1}.
- Caption only what the slide's own text and OCR support; invent nothing.

LECTURE: "{title}"

SLIDES:
{slides}"""


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


def _build_batch_prompt(title, batch):
    blocks = []
    for i, s in enumerate(batch):
        blocks.append(
            f"SLIDE {i} | Slide number: {s['slide_num']}\n"
            f"SLIDE TEXT: {(s['text'] or '').strip() or '(no extractable text)'}\n"
            f"IMAGE TEXT (OCR): {(s['ocr_text'] or '').strip()}"
        )
    return CAPTION_BATCH_PROMPT.format(
        count=len(batch),
        count_minus_1=len(batch) - 1,
        title=title,
        slides="\n\n".join(blocks),
    )


def _caption_batch(title, batch):
    """Caption one batch of slides in a single API call.
    Returns [(slide_id, caption), ...] for whatever came back valid."""
    result = chat_json(
        _build_batch_prompt(title, batch),
        system=CAPTION_SYSTEM,
        temperature=0.3,
        max_tokens=min(4000, 300 + 150 * len(batch)),
        kind="caption",
    )
    out = []
    for c in result.get("captions") or []:
        try:
            idx = int(c.get("slide_index", -1))
        except (ValueError, TypeError):
            continue
        caption = (c.get("caption") or "").strip()
        if 0 <= idx < len(batch) and caption:
            out.append((batch[idx]["id"], caption))
    return out


def generate_captions_for_lecture(lecture_id, force=False):
    """Generate a reasoned caption for each slide that has OCR text.

    Slides are batched (10 per call) and batches run concurrently — a 60-slide deck
    with 40 OCR'd slides costs 4 calls instead of 40.
    Returns number of captions generated.
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT title FROM lectures WHERE id=?", (lecture_id,)
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError(f"No lecture {lecture_id}")
    title = row["title"]

    slides = [
        s for s in _slides_needing_captions(lecture_id)
        if force or not (s["caption"] or "").strip()
    ]
    if not slides:
        return 0

    batches = [
        slides[i:i + CAPTION_BATCH_SIZE]
        for i in range(0, len(slides), CAPTION_BATCH_SIZE)
    ]
    results = []
    with ThreadPoolExecutor(max_workers=CAPTION_WORKERS) as ex:
        futures = [ex.submit(_caption_batch, title, b) for b in batches]
        for fut in as_completed(futures):
            try:
                results.extend(fut.result())
            except Exception as e:
                print(f"caption batch failed for lecture {lecture_id}: {e}")

    if not results:
        return 0
    conn = get_conn()
    conn.executemany(
        "UPDATE slides SET caption=? WHERE id=?",
        [(caption, sid) for sid, caption in results],
    )
    conn.commit()
    conn.close()
    return len(results)
