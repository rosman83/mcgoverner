"""Cap question volume per lecture to a manageable number.

Strategy: for each lecture, keep the concepts' questions but limit total
questions per lecture to MAX_QUESTIONS_PER_LECTURE. We prefer to keep
questions that have review history, and spread across concepts so coverage
stays broad rather than deep on a few concepts.
"""
from app.db import get_conn

MAX_QUESTIONS_PER_LECTURE = 250


def cap_lecture(lecture_id, max_questions=MAX_QUESTIONS_PER_LECTURE):
    """Reduce a lecture's question bank to ~max_questions, spreading across
    concepts. Questions with review history are kept first.
    Returns dict of stats."""
    conn = get_conn()
    # All concepts for this lecture that have questions
    concepts = conn.execute(
        "SELECT c.id FROM concepts c WHERE c.lecture_id=? "
        "AND EXISTS (SELECT 1 FROM questions q, json_each(q.concept_ids) je WHERE je.value=c.id) "
        "ORDER BY c.id",
        (lecture_id,),
    ).fetchall()
    if not concepts:
        conn.close()
        return {"concepts": 0, "kept": 0, "removed": 0, "review_kept": 0}

    # Build a map concept_id -> ordered list of question ids (reviewed first, then by id)
    concept_questions = {}
    for c in concepts:
        qs = conn.execute(
            "SELECT q.id FROM questions q, json_each(q.concept_ids) je "
            "WHERE je.value=? "
            "ORDER BY (SELECT COUNT(*) FROM reviews r WHERE r.question_id=q.id) DESC, q.id",
            (c["id"],),
        ).fetchall()
        concept_questions[c["id"]] = [r["id"] for r in qs]

    # Round-robin across concepts, keeping the first question per concept until budget
    kept_ids = []
    kept_set = set()
    removed_ids = set()
    review_kept = 0
    total_concepts = len(concepts)

    # First pass: keep at most 1 question per concept (broad coverage)
    for cid, qids in concept_questions.items():
        if len(kept_ids) >= max_questions:
            break
        for qid in qids:
            if qid not in kept_set:
                kept_ids.append(qid)
                kept_set.add(qid)
                if _has_reviews(conn, qid):
                    review_kept += 1
                break

    # Second pass: if budget remains, add 2nd question per concept (depth),
    # but never exceed max_questions total.
    if len(kept_ids) < max_questions:
        for cid, qids in concept_questions.items():
            if len(kept_ids) >= max_questions:
                break
            for qid in qids:
                if len(kept_ids) >= max_questions:
                    break
                if qid not in kept_set:
                    kept_ids.append(qid)
                    kept_set.add(qid)
                    if _has_reviews(conn, qid):
                        review_kept += 1

    # Any question in this lecture not kept gets removed (with reviews/scheduler cascade)
    all_qs = conn.execute(
        "SELECT id FROM questions WHERE lecture_id=?", (lecture_id,)
    ).fetchall()
    for r in all_qs:
        if r["id"] not in kept_set:
            removed_ids.add(r["id"])
            conn.execute("DELETE FROM questions WHERE id=?", (r["id"],))

    # Remove concepts that now have no questions
    conn.execute(
        "DELETE FROM concepts WHERE lecture_id=? AND NOT EXISTS "
        "(SELECT 1 FROM questions q, json_each(q.concept_ids) je WHERE je.value=concepts.id)",
        (lecture_id,),
    )
    conn.commit()
    conn.close()
    return {
        "concepts": total_concepts,
        "kept": len(kept_ids),
        "removed": len(removed_ids),
        "review_kept": review_kept,
    }


def _has_reviews(conn, qid):
    c = conn.execute(
        "SELECT COUNT(*) c FROM reviews WHERE question_id=?", (qid,)
    ).fetchone()["c"]
    return c > 0


def cap_all(max_questions=MAX_QUESTIONS_PER_LECTURE):
    conn = get_conn()
    lids = [r[0] for r in conn.execute("SELECT id FROM lectures").fetchall()]
    conn.close()
    results = {}
    for lid in lids:
        try:
            results[lid] = cap_lecture(lid, max_questions)
        except Exception as e:
            results[lid] = {"error": str(e)}
    return results
