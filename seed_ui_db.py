"""Seed a throwaway database for the jsdom UI tests (test_ui.js).

Creates one lecture, three questions with known correct answers, and an unanswered
session — no API calls, and your real database is untouched.

    BLOCK1_DB=/tmp/ui.db python seed_ui_db.py
    BLOCK1_DB=/tmp/ui.db ./run.sh &
    node test_ui.js
"""
import json
import os
import sys

if not os.environ.get("BLOCK1_DB"):
    sys.exit("Set BLOCK1_DB to a throwaway path first (never your real database).")

from app.db import get_conn, init_db

init_db()
conn = get_conn()
for t in ("session_questions", "sessions", "answers", "missed", "questions",
          "slides", "lectures"):
    conn.execute(f"DELETE FROM {t}")
conn.execute("INSERT INTO lectures(id, title, slide_count) VALUES(1, 'UI Test Lecture', 3)")
conn.execute(
    "INSERT INTO sessions(id, title, status, mode, target_count, tutor_mode) "
    "VALUES(1, 'UI Test', 'active', 'practice', 3, 1)"
)
for n in (1, 2, 3):
    conn.execute(
        "INSERT INTO slides(id, lecture_id, slide_num, text, caption) VALUES(?, 1, ?, ?, ?)",
        (n, n, f"Slide {n} body text about receptor structure and signalling.",
         f"Figure for slide {n}."),
    )
for qid in (1, 2, 3):
    conn.execute(
        "INSERT INTO questions(id, lecture_id, question, options, correct_index, "
        "explanation, source) VALUES(?, 1, ?, ?, 2, 'Because of the mechanism.', 'session')",
        (qid, f"Test question {qid}: which option is correct?",
         json.dumps([f"option {c}" for c in "ABCDE"])),
    )
    conn.execute(
        "INSERT INTO session_questions(session_id, question_id, position) VALUES(1, ?, ?)",
        (qid, qid - 1),
    )
conn.commit()
conn.close()
print(f"seeded {os.environ['BLOCK1_DB']}: session 1, 3 questions, correct_index=2")
