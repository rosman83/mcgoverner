"""Self-check for session logic: question reuse, next-after, tutor lock, weak
slides, count clamps, stem filter. Run: .venv/bin/python test_sessions.py
No network - generation is stubbed."""
import json
import os
import sys
import tempfile

os.environ["BLOCK1_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import app.db as db

db.DB_PATH = os.environ["BLOCK1_DB"]
db.init_db()

from fastapi.testclient import TestClient
import app.main as M
from app import sessiongen as S
from app import stats

OPTS = json.dumps(["a", "b", "c", "d", "e"])


def seed(n_slides=10):
    conn = db.get_conn()
    for t in ("session_questions", "sessions", "answers", "missed", "questions", "slides", "lectures"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO lectures(id, title) VALUES(1, 'L1')")
    conn.execute("INSERT INTO lectures(id, title) VALUES(2, 'L2')")
    for i in range(1, n_slides + 1):
        for lid in (1, 2):
            conn.execute(
                "INSERT INTO slides(id, lecture_id, slide_num, text) VALUES(?,?,?,?)",
                (lid * 100 + i, lid, i, f"biomedical slide content number {i} with enough text"),
            )
    conn.commit()
    conn.close()


def add_q(lid, slide_id, source="session"):
    conn = db.get_conn()
    cur = conn.execute(
        "INSERT INTO questions(lecture_id, slide_id, question, options, correct_index, explanation, level, source) "
        "VALUES(?,?,?,?,0,'x','recall',?)",
        (lid, slide_id, f"q{slide_id}", OPTS, source),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def fake_gen(slides, **kw):
    fake_gen.n += len(slides)
    return [
        {"lecture_id": s["lecture_id"], "slide_id": s["slide_id"], "slide_num": s["slide_num"],
         "question": f"gen {s['slide_id']}", "options": ["a", "b", "c", "d", "e"],
         "correct_index": 0, "explanation": ""}
        for s in slides
    ]


fake_gen.n = 0
S.generate_questions_for_slides = fake_gen


def test_unanswered_questions_are_reused_and_respect_lectures():
    seed()
    mine = [add_q(1, 101 + i) for i in range(4)]
    other = add_q(2, 201)
    answered = add_q(1, 110)
    conn = db.get_conn()
    conn.execute("INSERT INTO answers(question_id, correct) VALUES(?,1)", (answered,))
    conn.commit()
    conn.close()

    fake_gen.n = 0
    sid, count, err = S.create_practice_session(lecture_ids=[1], target=4)
    assert err is None and count == 4
    assert fake_gen.n == 0, "paid for questions again instead of reusing"
    conn = db.get_conn()
    used = {r[0] for r in conn.execute("SELECT question_id FROM session_questions WHERE session_id=?", (sid,))}
    conn.close()
    assert used == set(mine) and other not in used and answered not in used

    # Those four now sit in an ACTIVE session - must not be handed out again.
    fake_gen.n = 0
    sid2, count2, _ = S.create_practice_session(lecture_ids=[1], target=3)
    assert fake_gen.n == 3 and count2 == 3


def test_next_after_skips_instead_of_repeating():
    seed()
    for i in range(3):
        add_q(1, 101 + i)
    sid, _, _ = S.create_practice_session(lecture_ids=[1], target=3)
    c = TestClient(M.app)
    first = c.get(f"/api/sessions/{sid}/next").json()["question"]
    assert c.get(f"/api/sessions/{sid}/next").json()["question"]["id"] == first["id"]
    nxt = c.get(f"/api/sessions/{sid}/next?after={first['position']}").json()["question"]
    assert nxt["id"] != first["id"] and nxt["position"] > first["position"]
    last_pos = max(q["position"] for q in c.get(f"/api/sessions/{sid}/nav").json())
    wrap = c.get(f"/api/sessions/{sid}/next?after={last_pos}").json()["question"]
    assert wrap["position"] == first["position"], "should wrap to the first unanswered"


def test_tutor_answers_are_final_quiz_answers_can_change():
    c = TestClient(M.app)
    for mode, final in (("tutor", True), ("quiz", False)):
        seed()
        add_q(1, 101)
        sid, _, _ = S.create_practice_session(lecture_ids=[1], target=1, time_mode=mode)
        qid = c.get(f"/api/sessions/{sid}/next").json()["question"]["id"]
        assert c.post(f"/api/sessions/{sid}/answer/{qid}", json={"selected_index": 1}).status_code == 200
        r = c.post(f"/api/sessions/{sid}/answer/{qid}", json={"selected_index": 0})
        assert (r.status_code == 409) == final, (mode, r.status_code)


def test_weak_slides_include_zero_percent_but_need_two_answers():
    seed()
    q_zero, q_one = add_q(1, 101), add_q(1, 102)
    conn = db.get_conn()
    for _ in range(3):
        conn.execute("INSERT INTO answers(question_id, correct) VALUES(?,0)", (q_zero,))
    conn.execute("INSERT INTO answers(question_id, correct) VALUES(?,0)", (q_one,))
    conn.commit()
    conn.close()
    ids = [w["slide_id"] for w in stats.weak_slides()]
    assert 101 in ids, "a slide missed every time must show"
    assert 102 not in ids, "one answer is noise"


def test_counts_are_clamped():
    seed(n_slides=3)
    c = TestClient(M.app)
    r = c.post("/api/sessions", data={"mode": "practice", "target": -5, "lecture_ids": 1}).json()
    assert r["question_count"] == 1, r
    qids = [add_q(1, 101 + (i % 3)) for i in range(5)]
    conn = db.get_conn()
    for qid in qids:
        conn.execute("INSERT INTO missed(question_id, lecture_id, resolved) VALUES(?,1,0)", (qid,))
    conn.commit()
    conn.close()
    r = c.post("/api/sessions", data={"mode": "review", "target": 2}).json()
    assert r["question_count"] == 2, r


def test_stem_filter_allows_nbme_phrasing_blocks_slide_references():
    assert S._valid_stem("Which of the following best summarizes the mechanism of action?")
    assert S._valid_stem("A drug, as shown by its half-life, accumulates. Which is true?")
    assert not S._valid_stem("As shown in the slide, which enzyme is used?")
    assert not S._valid_stem("According to this lecture, what is X?")
    assert not S._valid_stem("What does the figure above depict?")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
    sys.exit(0)
