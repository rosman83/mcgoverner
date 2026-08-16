"""Import a professor-provided practice-question PDF/PPTX as real questions.

These are NOT slides. A question handout fed through the slide pipeline would be
turned into generated questions *about* question pages — with the answer key
visible in the source text — and would inflate slide-coverage stats with pages
that are not lecture content. So they bypass slides entirely: parsed once by the
model, stored in `questions` with source='professor' and slide_id NULL (which
keeps them out of coverage math), then reused free forever.
"""
import json
import os

from app.db import get_conn
from app.ingest.slides import extract_pdf, extract_pptx
from app.llm.client import chat_json

CHUNK_CHARS = 6000   # source characters per API call
ANSWER_KEY_TAIL = 3000  # trailing chars appended to each chunk as possible answer key

PARSE_SYSTEM = (
    "You extract existing multiple-choice questions from a document written by a "
    "medical school professor. You transcribe what is there - you never invent "
    "questions, options, or facts. If the document states the correct answer (inline, "
    "bolded, or in an answer key), use it. If a question's correct answer cannot be "
    "determined from the document, omit that question entirely rather than guessing."
)

# Static instructions first, variable document text last, to keep the cacheable prefix
# intact (same rule as sessiongen.QUESTION_BATCH_PROMPT).
PARSE_PROMPT = """Extract every multiple-choice question from the document text below.

RULES:
1. Transcribe questions as written. Do not rewrite, improve, or invent them.
2. Options: if a question has fewer than 5 options, keep the real ones - do not pad
   with invented distractors. If it has more than 5, keep them all.
3. correct_index is the 0-based index of the correct option. The answer may appear
   next to the question, marked in the text, or in an answer key section (which may be
   included at the end of this excerpt under POSSIBLE ANSWER KEY).
4. If you cannot determine the correct answer for a question, OMIT that question.
5. explanation: copy the rationale if the document provides one; otherwise "".
6. Ignore page headers, footers, page numbers, and instructions to students.
7. If the text contains no complete multiple-choice questions, return an empty list.

Return ONLY json:
{{
  "questions": [
    {{
      "question": "The full question stem as written.",
      "options": ["option A text", "option B text", "..."],
      "correct_index": 0,
      "explanation": "Rationale as given in the document, or empty string."
    }}
  ]
}}

DOCUMENT TEXT:
{text}
{answer_key}"""


def _chunks(pages):
    """Group page texts into chunks under CHUNK_CHARS so each fits one call."""
    out, cur, size = [], [], 0
    for _, text in pages:
        text = (text or "").strip()
        if not text:
            continue
        if cur and size + len(text) > CHUNK_CHARS:
            out.append("\n".join(cur))
            cur, size = [], 0
        cur.append(text)
        size += len(text)
    if cur:
        out.append("\n".join(cur))
    return out


def _parse_chunk(text, answer_key):
    result = chat_json(
        PARSE_PROMPT.format(
            text=text,
            answer_key=f"\n\nPOSSIBLE ANSWER KEY (may be unrelated):\n{answer_key}" if answer_key else "",
        ),
        system=PARSE_SYSTEM,
        temperature=0.0,   # transcription, not authoring
        max_tokens=8000,
        kind="qset_parse",
    )
    out = []
    for q in result.get("questions") or []:
        stem = (q.get("question") or "").strip()
        options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        try:
            correct = int(q.get("correct_index", -1))
        except (ValueError, TypeError):
            continue
        if not stem or len(options) < 2 or not (0 <= correct < len(options)):
            continue
        out.append({
            "question": stem,
            "options": options,
            "correct_index": correct,
            "explanation": (q.get("explanation") or "").strip(),
        })
    return out


def import_question_set(path, lecture_id):
    """Parse `path` into professor-authored questions attached to `lecture_id`.
    Returns {"imported": n, "skipped_duplicates": n}.

    ponytail: every question lands on the one lecture you pick. A handout spanning a
    whole block gets attributed to a single lecture — fine for drilling, wrong for
    per-lecture stats. Split by lecture if that starts to matter.
    """
    ext = os.path.splitext(path)[1].lower()
    pages = extract_pdf(path) if ext == ".pdf" else extract_pptx(path)
    chunks = _chunks(pages)
    if not chunks:
        return {"imported": 0, "skipped_duplicates": 0}

    # Answer keys usually sit at the end of the handout, so the tail is offered to every
    # chunk. Pointless when the whole document already fits in one chunk.
    full = "\n".join(chunks)
    tail = full[-ANSWER_KEY_TAIL:] if len(chunks) > 1 else ""

    parsed = []
    for c in chunks:
        try:
            parsed.extend(_parse_chunk(c, tail))
        except Exception as e:
            print(f"question-set chunk failed for {os.path.basename(path)}: {e}")

    if not parsed:
        return {"imported": 0, "skipped_duplicates": 0}

    conn = get_conn()
    existing = {
        r["question"].strip().lower()
        for r in conn.execute(
            "SELECT question FROM questions WHERE lecture_id=? AND source='professor'",
            (lecture_id,),
        ).fetchall()
    }
    imported = dupes = 0
    for q in parsed:
        key = q["question"].strip().lower()
        if key in existing:   # re-uploading the same handout must not double the bank
            dupes += 1
            continue
        existing.add(key)
        conn.execute(
            "INSERT INTO questions(lecture_id, slide_id, question, options, correct_index, "
            "explanation, level, source) VALUES(?, NULL, ?, ?, ?, ?, 'professor', 'professor')",
            (lecture_id, q["question"], json.dumps(q["options"]), q["correct_index"],
             q["explanation"]),
        )
        imported += 1
    conn.commit()
    conn.close()
    return {"imported": imported, "skipped_duplicates": dupes}


def professor_question_counts():
    """{lecture_id: count} of professor questions, for the dashboard/drill UI."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT lecture_id, COUNT(*) c FROM questions WHERE source='professor' "
        "GROUP BY lecture_id"
    ).fetchall()
    conn.close()
    return {r["lecture_id"]: r["c"] for r in rows}
