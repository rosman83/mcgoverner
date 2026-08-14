import json

from app.db import get_conn
from app.llm.client import chat_json

QUESTION_SYSTEM = (
    "You are a medical exam writer creating single-best-answer (SBA) MCQ questions "
    "for a first-year medical school block exam. The exam is entirely multiple choice. "
    "You create questions that exactly match the style shown in examples. "
    "Every question MUST be anchored to the provided lecture text - never introduce "
    "facts from outside the lecture unless clearly labeled as board-style extension. "
    "Correct answers must be unambiguous; distractors must be plausible but clearly wrong "
    "given the lecture material."
)

EXAMPLE_QUESTIONS = """EXAMPLE STYLE - single best answer, 5 options A-E, numbered stem:

Q. A pregnant woman is exposed to cytomegalovirus during her first trimester. Based on the placental transfer categories discussed, is CMV expected to cross the placental membrane?
A. No - viruses cannot cross the placental membrane
B. Yes - viruses like CMV and rubella are listed as substances that cross the placenta
C. Only bacteria cross, not viruses
D. Only in the third trimester
E. Only if the mother has preeclampsia

Q. On day 11-12, the extraembryonic coelom (chorionic cavity) begins expanding within the extraembryonic mesoderm. By day 13, this cavity's expansion causes the primary yolk sac to be replaced by which structure?
A. Amniotic cavity
B. Secondary yolk sac
C. Exocoelomic membrane
D. Chorionic plate
E. Decidua basalis

Q. Endometrial connective tissue cells accumulate glycogen and lipids, become polyhedral, and eventually undergo apoptosis as part of a maternal response to blastocyst implantation. This process is called the:
A. Zonal reaction
B. Cortical reaction
C. Decidual reaction
D. Acrosome reaction
E. Menstrual reaction
"""

QUESTION_PROMPT = """You are writing practice questions for the following lecture material.

LECTURE TITLE: {title}

LECTURE TEXT (concept chunk):
{concept_text}

EXAM STYLE REFERENCE:
{examples}

Generate {num_questions} single-best-answer MCQ questions. Vary the difficulty and style:
- Mix direct-recall items (testing specific facts from the text), mechanism/why items, and clinical-vignette items.
- For later-position questions, prefer applying the material (e.g., "A patient... Which of the following..."), but only using knowledge consistent with the lecture.
- Follow the exact style above: a numbered stem "Q. <clinical or direct question>", then 5 options A-E (single best answer).

Return ONLY JSON:
{{
  "questions": [
    {{
      "question": "Full stem text (do NOT include answer letters)",
      "options": ["A option", "B option", "C option", "D option", "E option"],
      "correct_index": 0,
      "explanation": "A teaching explanation. 2-5 sentences: why the correct answer is right, why the others are wrong, and the high-yield takeaway. Written to help the student learn, UWorld-style."
    }}
  ]
}}

Every correct_index must be a valid option index (0-4). Explanations must be genuinely instructive."""


def generate_questions_for_concept(concept_id, num_questions=3, on_progress=None):
    conn = get_conn()
    concept = conn.execute(
        "SELECT * FROM concepts WHERE id=?", (concept_id,)
    ).fetchone()
    if not concept:
        conn.close()
        raise ValueError(f"No concept {concept_id}")

    # Prefer the granular extracted detail (protocol-based concepts) as anchor text
    detail_row = conn.execute(
        "SELECT detail FROM concept_details WHERE concept_id=?", (concept_id,)
    ).fetchone()
    detail = (detail_row["detail"] if detail_row else "") or ""

    slide_ids = json.loads(concept["slide_ids"])
    placeholders = ",".join("?" * len(slide_ids))
    slides = conn.execute(
        f"SELECT text, ocr_text, caption FROM slides WHERE id IN ({placeholders})",
        tuple(slide_ids),
    ).fetchall()
    lecture = conn.execute(
        "SELECT title FROM lectures WHERE id=?", (concept["lecture_id"],)
    ).fetchone()
    conn.close()

    chunks = []
    if detail:
        chunks.append(f"[CONCEPT DETAIL]: {detail}")
    for s in slides:
        if not detail:
            chunks.append(s["text"] or "")
        if (s["caption"] or "").strip() and not detail:
            chunks.append(f"[Image caption]: {s['caption']}")
        elif (s["ocr_text"] or "").strip() and not detail:
            chunks.append(f"[IMAGE TEXT]: {s['ocr_text']}")
    if detail:
        # still give slide context but mark concept focus
        for s in slides:
            if (s["text"] or "").strip():
                chunks.append(f"[Slide context]: {(s['text'] or '')[:400]}")
    concept_text = " ".join(chunks)
    result = chat_json(
        QUESTION_PROMPT.format(
            title=lecture["title"],
            concept_text=concept_text,
            examples=EXAMPLE_QUESTIONS,
            num_questions=num_questions,
        ),
        system=QUESTION_SYSTEM,
        temperature=0.8,
        max_tokens=4000,
    )

    conn = get_conn()
    created = []
    for q in result["questions"]:
        options = [_strip_letter_prefix(o) for o in q["options"]]
        if len(options) != 5:
            continue
        correct = int(q.get("correct_index", 0))
        if not (0 <= correct < len(options)):
            continue
        cur = conn.execute(
            "INSERT INTO questions(lecture_id, concept_ids, question, options, "
            "correct_index, explanation, level) VALUES(?,?,?,?,?,?,?)",
            (
                concept["lecture_id"],
                json.dumps([concept_id]),
                q["question"],
                json.dumps(options),
                correct,
                q.get("explanation", ""),
                "clinical" if "patient" in q["question"].lower() or "woman" in q["question"].lower() else "recall",
            ),
        )
        created.append(cur.lastrowid)
    conn.commit()
    conn.close()
    return created


def _strip_letter_prefix(text):
    """Remove a leading 'A. ' style letter prefix that models sometimes add to option text."""
    import re
    stripped = re.sub(r"^[A-E]\.\s*", "", text.strip())
    return stripped.strip()


def generate_for_lecture(lecture_id, per_concept=3, on_progress=None, force=False):
    """Generate questions for every concept in a lecture.
    If force=False, only generates for concepts that have no questions yet.
    If force=True, generates more regardless (top-up)."""
    from app.db import get_conn as _conn
    conn = _conn()
    if force:
        concepts = conn.execute(
            "SELECT id FROM concepts WHERE lecture_id=? ORDER BY id",
            (lecture_id,),
        ).fetchall()
    else:
        concepts = conn.execute(
            "SELECT id FROM concepts WHERE lecture_id=? "
            "AND NOT EXISTS (SELECT 1 FROM questions q WHERE q.concept_ids LIKE '%'||concepts.id||'%') "
            "ORDER BY id",
            (lecture_id,),
        ).fetchall()
    conn.close()
    created = []
    for i, c in enumerate(concepts):
        try:
            ids = generate_questions_for_concept(c["id"], num_questions=per_concept)
            created.extend(ids)
        except Exception as e:
            print(f"concept {c['id']} failed: {e}")
        if on_progress:
            on_progress(i + 1, len(concepts))
    return created
