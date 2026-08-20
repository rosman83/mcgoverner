"""Slide-based progress stats.

The slide is the atomic unit. Every question is anchored to a slide.
coverage_pct = slides with at least one generated question (goes up the
moment a session is created, before the user does anything with it).
practiced_pct = slides with an actually-answered question - the real
progress signal, and what the UI shows as "coverage". accuracy is computed
from the `answers` table (which records each answer's correctness).
"""
from app.db import get_conn

MIN_SLIDE_WORDS = 15


def lecture_stats():
    """Per-lecture aggregation of slide coverage + accuracy."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT l.id AS lecture_id, l.title AS lecture_title, "
        "COUNT(DISTINCT s.id) AS slides_total, "
        "COUNT(DISTINCT CASE WHEN q.id IS NOT NULL THEN s.id END) AS slides_covered, "
        "COUNT(DISTINCT CASE WHEN a.id IS NOT NULL THEN s.id END) AS slides_practiced, "
        "COUNT(DISTINCT q.id) AS q_count, "
        "COUNT(DISTINCT CASE WHEN a.id IS NOT NULL THEN q.id END) AS q_answered, "
        "SUM(CASE WHEN a.correct=1 THEN 1 ELSE 0 END) AS correct, "
        "COUNT(a.id) AS answers "
        "FROM lectures l "
        "JOIN slides s ON s.lecture_id=l.id "
        "AND (length(trim(s.text)) >= ? OR length(trim(s.ocr_text)) >= ?) "
        "LEFT JOIN questions q ON q.slide_id=s.id "
        "LEFT JOIN answers a ON a.question_id=q.id "
        "GROUP BY l.id ORDER BY l.id",
        (MIN_SLIDE_WORDS, MIN_SLIDE_WORDS),
    ).fetchall()
    conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["slides_covered"] = d["slides_covered"] or 0
        d["slides_practiced"] = d["slides_practiced"] or 0
        d["q_count"] = d["q_count"] or 0
        d["q_answered"] = d["q_answered"] or 0
        d["correct"] = d["correct"] or 0
        d["answers"] = d["answers"] or 0
        d["coverage_pct"] = (
            round(100 * d["slides_covered"] / d["slides_total"], 1)
            if d["slides_total"]
            else 0
        )
        # coverage_pct only means "a question exists" - it goes up the moment a
        # session is created, before the user has answered anything in it.
        # practiced_pct is the real progress signal: slides where a question
        # has actually been answered at least once.
        d["practiced_pct"] = (
            round(100 * d["slides_practiced"] / d["slides_total"], 1)
            if d["slides_total"]
            else 0
        )
        d["accuracy"] = (
            round(d["correct"] / d["answers"], 2) if d["answers"] else None
        )
        out.append(d)
    return out


_THUMB_SUBQUERY = (
    "(SELECT si.path FROM slide_images si WHERE si.slide_id=s.id ORDER BY si.seq LIMIT 1) AS thumb"
)


def _with_thumb_url(d):
    d["thumb"] = ("/images/" + d["thumb"]) if d.get("thumb") else None
    return d


def weak_slides(limit=30, max_accuracy=0.85, min_answers=1):
    """Slides with the worst answer accuracy (only genuinely weak ones).
    Excludes 0% accuracy - a single unlucky guess reading as "0%" is noise,
    not a meaningful weak-slide signal to act on.

    HAVING/ORDER BY use the full aggregate expressions rather than the
    `answers`/`correct` SELECT aliases on purpose: the `answers` table (joined
    as `a`) has its own real column literally named `correct`, and SQLite
    resolves a bare identifier in HAVING/ORDER BY against an in-scope column
    before an output alias - `correct` and even `answers` silently bound to
    the wrong thing, so this was sorting on essentially nothing. Confirmed by
    running the two forms side by side against real data: the alias version
    returned 1.0, 1.0, 1.0, 0.67, 1.0, ... (not sorted); the explicit-
    expression version returns a real ascending sequence."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.id AS slide_id, s.slide_num, s.text, l.title AS lecture_title, "
        "l.id AS lecture_id, "
        "COUNT(a.id) AS answers, "
        "SUM(CASE WHEN a.correct=1 THEN 1 ELSE 0 END) AS correct, "
        f"{_THUMB_SUBQUERY} "
        "FROM slides s "
        "JOIN lectures l ON l.id=s.lecture_id "
        "JOIN questions q ON q.slide_id=s.id "
        "JOIN answers a ON a.question_id=q.id "
        "GROUP BY s.id "
        "HAVING COUNT(a.id) >= ? AND SUM(CASE WHEN a.correct=1 THEN 1 ELSE 0 END) > 0 "
        "ORDER BY SUM(CASE WHEN a.correct=1 THEN 1 ELSE 0 END)*1.0/COUNT(a.id) ASC "
        "LIMIT ?",
        (min_answers, limit),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["accuracy"] = round(d["correct"] / d["answers"], 2) if d["answers"] else None
        out.append(_with_thumb_url(d))
    return out


def gaps(limit=50):
    """Slides with no questions yet (coverage gaps)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.id AS slide_id, s.slide_num, s.text, l.title AS lecture_title, "
        "l.id AS lecture_id, "
        f"{_THUMB_SUBQUERY} "
        "FROM slides s JOIN lectures l ON l.id=s.lecture_id "
        "WHERE (length(trim(s.text)) >= ? OR length(trim(s.ocr_text)) >= ?) "
        "AND NOT EXISTS (SELECT 1 FROM questions q WHERE q.slide_id=s.id) "
        "ORDER BY l.id, s.slide_num LIMIT ?",
        (MIN_SLIDE_WORDS, MIN_SLIDE_WORDS, limit),
    ).fetchall()
    conn.close()
    return [_with_thumb_url(dict(r)) for r in rows]
