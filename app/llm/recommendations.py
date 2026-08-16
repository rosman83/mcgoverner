"""Study recommendations generated from the mistakes in one session.

One API call per session, only when there were misses, and the result is stored — so
reopening an old session's review costs nothing and you keep a running record of what
to work on.
"""
import json

from app.db import get_conn
from app.llm.client import chat_json

RECOMMENDATION_SYSTEM = (
    "You are a medical school tutor reviewing a first-year student's practice session. "
    "You are given the questions they got WRONG, with the answer they chose and the "
    "correct answer. Diagnose the underlying knowledge gaps - not the individual "
    "questions - and give focused, actionable study guidance. Be specific and concise; "
    "never pad. Ground every claim in the mistakes provided. If the mistakes do not "
    "support a general pattern, say the misses look scattered rather than inventing one."
)

# Static instructions first, the variable mistake list last, so the cacheable prefix
# stays intact (same rule as sessiongen.QUESTION_BATCH_PROMPT).
RECOMMENDATION_PROMPT = """Analyse this student's incorrect answers and produce study guidance.

Return ONLY json:
{{
  "themes": [
    {{
      "topic": "Short name for the knowledge gap (e.g. 'Second messenger cascades')",
      "why": "One or two sentences on what the wrong answers reveal about the misunderstanding.",
      "action": "One concrete next step: what to re-read or drill, phrased as an instruction."
    }}
  ],
  "summary": "2-3 sentences: the single highest-value thing to fix before the exam."
}}

RULES:
1. Between 1 and 4 themes. Group related mistakes; do not produce one theme per question.
2. Reference the actual content of the mistakes, not the question numbers.
3. If a wrong answer suggests a specific confusion (picking a plausible-but-wrong
   mechanism), name that confusion explicitly.
4. Do not invent material the questions do not cover.

MISTAKES:
{mistakes}"""


def _session_mistakes(session_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT q.question, q.options, q.correct_index, q.explanation, "
        "a.selected_index, l.title AS lecture_title, s.slide_num "
        "FROM session_questions sq "
        "JOIN questions q ON q.id = sq.question_id "
        "JOIN answers a ON a.question_id = q.id AND a.session_id = sq.session_id "
        "LEFT JOIN lectures l ON l.id = q.lecture_id "
        "LEFT JOIN slides s ON s.id = q.slide_id "
        "WHERE sq.session_id = ? AND a.correct = 0 "
        "ORDER BY sq.position",
        (session_id,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        options = json.loads(r["options"])
        picked = r["selected_index"]
        out.append({
            "lecture": r["lecture_title"] or "",
            "slide_num": r["slide_num"],
            "question": r["question"],
            "chose": options[picked] if picked is not None and 0 <= picked < len(options)
                     else "(ran out of time)",
            "correct": options[r["correct_index"]] if 0 <= r["correct_index"] < len(options) else "",
            "explanation": r["explanation"] or "",
        })
    return out


def _format_mistakes(mistakes):
    blocks = []
    for i, m in enumerate(mistakes, 1):
        loc = f"{m['lecture']}" + (f", slide {m['slide_num']}" if m["slide_num"] else "")
        blocks.append(
            f"MISTAKE {i} ({loc})\n"
            f"Question: {m['question']}\n"
            f"They chose: {m['chose']}\n"
            f"Correct answer: {m['correct']}\n"
            f"Explanation: {m['explanation']}"
        )
    return "\n\n".join(blocks)


def get_recommendations(session_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM recommendations WHERE session_id=?", (session_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["themes"] = json.loads(d["themes"] or "[]")
    return d


def generate_recommendations(session_id, force=False):
    """Analyse a session's mistakes into saved study guidance.
    Returns the stored record, or None if there were no mistakes to learn from."""
    if not force:
        existing = get_recommendations(session_id)
        if existing:
            return existing

    mistakes = _session_mistakes(session_id)
    if not mistakes:
        return None

    result = chat_json(
        RECOMMENDATION_PROMPT.format(mistakes=_format_mistakes(mistakes)),
        system=RECOMMENDATION_SYSTEM,
        temperature=0.3,
        max_tokens=1500,
        kind="recommendations",
    )
    themes = [
        {
            "topic": (t.get("topic") or "").strip(),
            "why": (t.get("why") or "").strip(),
            "action": (t.get("action") or "").strip(),
        }
        for t in (result.get("themes") or [])
        if (t.get("topic") or "").strip()
    ]
    summary = (result.get("summary") or "").strip()
    if not themes and not summary:
        return None

    conn = get_conn()
    conn.execute(
        "INSERT INTO recommendations(session_id, themes, summary, mistake_count) "
        "VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
        "themes=excluded.themes, summary=excluded.summary, "
        "mistake_count=excluded.mistake_count, created_at=datetime('now')",
        (session_id, json.dumps(themes), summary, len(mistakes)),
    )
    conn.commit()
    conn.close()
    return get_recommendations(session_id)


def recent_recommendations(limit=10):
    """Saved guidance across sessions, newest first — the running record of weak spots."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT r.*, s.title AS session_title FROM recommendations r "
        "LEFT JOIN sessions s ON s.id = r.session_id "
        "ORDER BY r.created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["themes"] = json.loads(d["themes"] or "[]")
        out.append(d)
    return out
