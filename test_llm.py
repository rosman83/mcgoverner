"""Self-check for the LLM client + usage accounting. Run: .venv/bin/python test_llm.py

Covers the logic that would silently cost money or silently stop working:
JSON parsing, provider resolution, retry policy, usage rows, caption batching.
No network calls — the OpenAI client is stubbed.
"""
import os
import sys
import tempfile

os.environ["BLOCK1_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import app.db as db

db.DB_PATH = os.environ["BLOCK1_DB"]
db.init_db()

from app.llm import client as C
from app.llm import captions as CAP

_REAL_GET_CLIENT = C.get_client  # stubs below replace it; some tests need the real one


class _Usage:
    def __init__(self, p=100, c=50, cached=90):
        self.prompt_tokens, self.completion_tokens = p, c
        self.prompt_cache_hit_tokens = cached
        self.prompt_tokens_details = None


class _Resp:
    def __init__(self, content, usage=None):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]
        self.usage = usage or _Usage()


def stub_chat(replies):
    """Replace the network call with canned replies; records prompts seen."""
    seen = []
    it = iter(replies)

    class _Completions:
        def create(self, **kw):
            seen.append(kw)
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return _Resp(r)

    class _Client:
        chat = type("X", (), {"completions": _Completions()})()

    C.get_client = lambda: _Client()
    return seen


def test_parse_json():
    assert C._parse_json('{"a": 1}') == {"a": 1}
    assert C._parse_json('```json\n{"a": 2}\n```') == {"a": 2}          # fenced
    assert C._parse_json('here you go: {"a": 3} hope that helps') == {"a": 3}  # chatty
    try:
        C._parse_json("no json at all")
        assert False, "should have raised"
    except ValueError:
        pass


def test_json_mode_on_first_attempt():
    """The old client sent attempt 0 without JSON mode, buying a guaranteed retry."""
    seen = stub_chat(['{"ok": true}'])
    assert C.chat_json("p") == {"ok": True}
    assert len(seen) == 1, f"expected 1 call, got {len(seen)}"
    assert seen[0]["response_format"] == {"type": "json_object"}
    # json_object mode is rejected unless the messages contain the literal "json".
    body = " ".join(m["content"] for m in seen[0]["messages"])
    assert "json" in body, "lowercase 'json' missing — provider will reject json mode"


def test_api_errors_are_not_retried():
    seen = stub_chat([RuntimeError("401 unauthorized")])
    try:
        C.chat_json("p")
        assert False, "should have raised"
    except RuntimeError:
        pass
    assert len(seen) == 1, f"auth failure retried {len(seen)}x — should be 1"


def test_malformed_output_is_retried():
    seen = stub_chat(["not json", '{"ok": 1}'])
    assert C.chat_json("p") == {"ok": 1}
    assert len(seen) == 2


def test_usage_is_recorded():
    conn = db.get_conn()
    conn.execute("DELETE FROM api_usage")
    conn.commit()
    conn.close()

    stub_chat(['{"ok": 1}'])
    C.chat_json("p", kind="questions")

    conn = db.get_conn()
    row = conn.execute("SELECT * FROM api_usage").fetchone()
    conn.close()
    assert row is not None, "no usage row written"
    assert row["kind"] == "questions"
    assert row["prompt_tokens"] == 100 and row["completion_tokens"] == 50
    assert row["cached_tokens"] == 90


def test_provider_switch():
    for var in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL",
                "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
        os.environ.pop(var, None)

    os.environ["LLM_PROVIDER"] = "openrouter"
    assert C.active_model() == ("openrouter", "deepseek/deepseek-chat")
    assert "openrouter" in C._provider()["base_url"]

    os.environ["LLM_PROVIDER"] = "deepseek"
    assert C.active_model() == ("deepseek", "deepseek-chat")

    os.environ["LLM_MODEL"] = "some-other-model"
    assert C.active_model()[1] == "some-other-model"
    del os.environ["LLM_MODEL"]

    # Unknown provider falls back to whichever key is present.
    os.environ["LLM_PROVIDER"] = "nonsense"
    os.environ.pop("DEEPSEEK_API_KEY", None)
    os.environ["OPENROUTER_API_KEY"] = "x"
    assert C.active_model()[0] == "openrouter"
    del os.environ["OPENROUTER_API_KEY"]
    os.environ["LLM_PROVIDER"] = "deepseek"


def test_missing_key_names_the_right_var():
    C.get_client = _REAL_GET_CLIENT
    os.environ["LLM_PROVIDER"] = "openrouter"
    os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        C.get_client()
        assert False, "should have raised"
    except RuntimeError as e:
        assert "OPENROUTER_API_KEY" in str(e), str(e)
    os.environ["LLM_PROVIDER"] = "deepseek"


def test_captions_batch_into_one_call():
    """40 OCR'd slides used to cost 40 calls; batching makes it 4."""
    conn = db.get_conn()
    conn.execute("DELETE FROM slides")
    conn.execute("DELETE FROM lectures")
    conn.execute("INSERT INTO lectures(id, title) VALUES(1, 'Test Lecture')")
    for n in range(1, 13):
        conn.execute(
            "INSERT INTO slides(lecture_id, slide_num, text, ocr_text, caption) "
            "VALUES(1, ?, ?, ?, '')",
            (n, f"slide text {n}", f"ocr text {n}"),
        )
    conn.commit()
    conn.close()

    def fake_chat_json(prompt, **kw):
        # count is however many slides this batch carried
        n = prompt.count("SLIDE ") - prompt.count("SLIDE <")
        return {"captions": [{"slide_index": i, "caption": f"cap {i}"} for i in range(n)]}

    calls = []
    CAP.chat_json = lambda prompt, **kw: (calls.append(prompt), fake_chat_json(prompt, **kw))[1]

    n = CAP.generate_captions_for_lecture(1)
    assert n == 12, f"expected 12 captions, got {n}"
    assert len(calls) == 2, f"12 slides should batch into 2 calls, got {len(calls)}"

    # Static prefix must precede the variable slide text, or prompt caching is void.
    assert calls[0].index("Return ONLY JSON") < calls[0].index("SLIDES:")

    # Second run is free: existing captions are skipped unless force=True.
    calls.clear()
    assert CAP.generate_captions_for_lecture(1) == 0
    assert len(calls) == 0, "re-running captions should not call the API"


def test_question_prompt_keeps_slides_last():
    from app import sessiongen as S

    batch = [
        {"lecture_title": "L", "slide_num": i, "text": f"body {i}", "caption": "", "ocr": ""}
        for i in range(3)
    ]
    p = S._build_batch_prompt(batch)
    assert p.index("EXAM STYLE REFERENCE") < p.index("SLIDES:"), "examples must precede slides"
    assert p.rindex("body 2") > p.index("Return ONLY JSON"), "slide text must come last"


def _seed_lecture_with_professor_questions(n_prof):
    import json as _json

    conn = db.get_conn()
    for t in ("session_questions", "sessions", "answers", "questions", "slides", "lectures"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO lectures(id, title) VALUES(1, 'L1')")
    for i in range(1, 21):
        conn.execute(
            "INSERT INTO slides(lecture_id, slide_num, text) VALUES(1, ?, ?)",
            (i, f"biomedical slide content number {i} with enough text"),
        )
    for i in range(n_prof):
        conn.execute(
            "INSERT INTO questions(lecture_id, slide_id, question, options, correct_index, "
            "explanation, level, source) VALUES(1, NULL, ?, ?, 0, '', 'professor', 'professor')",
            (f"prof question {i}", _json.dumps(["a", "b", "c", "d", "e"])),
        )
    conn.commit()
    conn.close()


def test_professor_questions_are_mixed_in_and_cut_generation():
    from app import sessiongen as S

    _seed_lecture_with_professor_questions(20)

    generated = []

    def fake_gen(slides, **kw):
        generated.append(len(slides))
        return [
            {"lecture_id": s["lecture_id"], "slide_id": s["slide_id"], "slide_num": s["slide_num"],
             "question": f"gen {s['slide_id']}", "options": ["a", "b", "c", "d", "e"],
             "correct_index": 0, "explanation": ""}
            for s in slides
        ]

    S.generate_questions_for_slides = fake_gen
    sid, count, err = S.create_practice_session(lecture_ids=[1], target=10)
    assert err is None, err

    assert count == 10, f"expected 10 questions, got {count}"
    # 40% mix => 4 professor questions, so only 6 should have been generated.
    assert sum(generated) == 6, f"expected 6 slides generated, got {sum(generated)}"

    conn = db.get_conn()
    rows = conn.execute(
        "SELECT q.source, COUNT(*) c FROM session_questions sq "
        "JOIN questions q ON q.id=sq.question_id WHERE sq.session_id=? GROUP BY q.source",
        (sid,),
    ).fetchall()
    conn.close()
    by_source = {r["source"]: r["c"] for r in rows}
    assert by_source == {"professor": 4, "session": 6}, by_source


def test_session_works_with_no_professor_questions():
    from app import sessiongen as S

    _seed_lecture_with_professor_questions(0)
    S.generate_questions_for_slides = lambda slides, **kw: [
        {"lecture_id": s["lecture_id"], "slide_id": s["slide_id"], "slide_num": s["slide_num"],
         "question": f"gen {s['slide_id']}", "options": ["a", "b", "c", "d", "e"],
         "correct_index": 0, "explanation": ""}
        for s in slides
    ]
    sid, count, err = S.create_practice_session(lecture_ids=[1], target=5)
    assert err is None, err
    assert count == 5, count


def test_professor_selection_prefers_least_answered():
    from app import sessiongen as S

    _seed_lecture_with_professor_questions(5)
    conn = db.get_conn()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM questions WHERE source='professor' ORDER BY id"
    ).fetchall()]
    # Bury the first three under answer history; the last two are untouched.
    for qid in ids[:3]:
        conn.execute("INSERT INTO answers(question_id, correct) VALUES(?, 1)", (qid,))
    conn.commit()
    conn.close()

    picked = S.select_professor_questions([1], 2)
    assert set(picked) == set(ids[3:]), f"expected the unanswered pair, got {picked}"


def test_question_set_parsing_rejects_junk():
    from app.ingest import questionsets as QS

    QS.chat_json = lambda prompt, **kw: {"questions": [
        {"question": "Good one?", "options": ["a", "b", "c", "d"], "correct_index": 2,
         "explanation": "because"},
        {"question": "No answer known?", "options": ["a", "b"], "correct_index": -1},   # dropped
        {"question": "", "options": ["a", "b"], "correct_index": 0},                     # dropped
        {"question": "Too few options?", "options": ["a"], "correct_index": 0},          # dropped
        {"question": "Out of range?", "options": ["a", "b"], "correct_index": 9},        # dropped
    ]}
    parsed = QS._parse_chunk("some text", "")
    assert len(parsed) == 1, parsed
    assert parsed[0]["correct_index"] == 2 and len(parsed[0]["options"]) == 4


def test_question_set_import_is_idempotent():
    from app.ingest import questionsets as QS

    _seed_lecture_with_professor_questions(0)
    QS.extract_pdf = lambda p: [(1, "a question page with plenty of text on it")]
    QS.chat_json = lambda prompt, **kw: {"questions": [
        {"question": "Which artery?", "options": ["a", "b", "c", "d", "e"],
         "correct_index": 1, "explanation": "rationale"},
    ]}

    first = QS.import_question_set("handout.pdf", 1)
    assert first == {"imported": 1, "skipped_duplicates": 0}, first
    second = QS.import_question_set("handout.pdf", 1)
    assert second == {"imported": 0, "skipped_duplicates": 1}, second

    conn = db.get_conn()
    row = conn.execute("SELECT * FROM questions WHERE source='professor'").fetchone()
    n = conn.execute("SELECT COUNT(*) c FROM questions").fetchone()["c"]
    conn.close()
    assert n == 1, f"re-import duplicated the bank: {n} rows"
    assert row["slide_id"] is None, "professor questions must not claim a slide (breaks coverage)"


def test_professor_questions_stay_out_of_coverage():
    from app.ingest.concepts import coverage_stats

    _seed_lecture_with_professor_questions(5)
    cov = coverage_stats()
    assert cov["total"] == 20, f"professor questions inflated the slide count: {cov}"
    assert cov["covered"] == 0, cov


def test_lecture_with_no_slides_creates_no_session():
    """A lecture row with zero slides used to yield a resumable 'Practice (0 questions)'
    session that hid the real problem."""
    from app import sessiongen as S

    conn = db.get_conn()
    for t in ("session_questions", "sessions", "answers", "questions", "slides", "lectures"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO lectures(id, title, slide_count) VALUES(1, 'Empty', 40)")
    conn.commit()
    conn.close()

    called = []
    S.generate_questions_for_slides = lambda slides, **kw: called.append(1) or []

    sid, count, err = S.create_practice_session(lecture_ids=[1], target=10)
    assert sid is None and count == 0
    assert "no readable slides" in err, err
    assert not called, "must not call the API when there are no slides"

    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    conn.close()
    assert n == 0, f"{n} empty session(s) persisted"


def test_generation_failure_reports_the_cause():
    from app import sessiongen as S

    _seed_lecture_with_professor_questions(0)
    S.generate_questions_for_slides = lambda slides, **kw: []   # every batch errors

    sid, count, err = S.create_practice_session(lecture_ids=[1], target=10)
    assert sid is None and count == 0
    assert "generation failed" in err.lower() and "API key" in err, err

    conn = db.get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    conn.close()
    assert n == 0, "failed generation must not leave a session behind"


def test_lecture_rename_and_retag():
    from fastapi.testclient import TestClient
    import app.main as M

    _seed_lecture_with_professor_questions(0)
    c = TestClient(M.app)

    assert c.get("/api/lectures").json()[0]["tag"] == "foundations", "default tag wrong"

    # Pasted Canvas titles arrive with newlines and runs of spaces.
    r = c.patch("/api/lectures/1", json={"title": "  Week 3 —  Cardiac \n Physiology  "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Week 3 — Cardiac Physiology", r.json()

    r = c.patch("/api/lectures/1", json={"tag": "anatomy"})
    assert r.status_code == 200 and r.json()["tag"] == "anatomy", r.text
    # Retagging must not disturb the name.
    assert r.json()["title"] == "Week 3 — Cardiac Physiology"

    assert c.patch("/api/lectures/1", json={"tag": "pathology"}).status_code == 400
    assert c.patch("/api/lectures/1", json={"title": "   "}).status_code == 400
    assert c.patch("/api/lectures/1", json={}).status_code == 400
    assert c.patch("/api/lectures/999", json={"tag": "anatomy"}).status_code == 404

    # A rejected update leaves the row untouched.
    row = c.get("/api/lectures").json()[0]
    assert row["title"] == "Week 3 — Cardiac Physiology" and row["tag"] == "anatomy", row


def test_schema_and_migrations_agree():
    """SCHEMA and the migration path must produce the same columns. They drifted once
    (answers.selected_index existed only in a migration) and answering 500'd."""
    import sqlite3
    import tempfile as _tf

    # 1. A database built purely from SCHEMA has every expected column.
    fresh = os.path.join(_tf.mkdtemp(), "fresh.db")
    conn = sqlite3.connect(fresh)
    conn.executescript(db.SCHEMA)
    conn.commit()
    missing = []
    for table, columns in db.EXPECTED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        missing += [f"{table}.{n}" for n, _ in columns if n not in have]
    conn.close()
    assert not missing, f"SCHEMA is missing columns the app writes: {missing}"

    # 2. An old database missing a column self-heals on startup, whatever its version.
    old = os.path.join(_tf.mkdtemp(), "old.db")
    conn = sqlite3.connect(old)
    conn.executescript("""
        CREATE TABLE answers (id INTEGER PRIMARY KEY AUTOINCREMENT,
          question_id INTEGER NOT NULL, session_id INTEGER, correct INTEGER NOT NULL,
          answered_at TEXT);
        INSERT INTO answers(question_id, correct) VALUES(7, 1);
        PRAGMA user_version = 16;
    """)
    conn.commit()
    conn.close()

    real = db.DB_PATH
    try:
        db.DB_PATH = old
        db.init_db()
        conn = db.get_conn()
        have = {r[1] for r in conn.execute("PRAGMA table_info(answers)").fetchall()}
        rows = conn.execute("SELECT question_id FROM answers").fetchall()
        conn.close()
    finally:
        db.DB_PATH = real
    assert "selected_index" in have, "stamped-current database did not self-heal"
    assert len(rows) == 1 and rows[0]["question_id"] == 7, "migration lost existing data"


def test_answering_a_question_end_to_end():
    """The exact flow that broke: answer -> recorded -> next question advances."""
    from fastapi.testclient import TestClient
    import app.main as M
    import json as _json

    _seed_lecture_with_professor_questions(0)
    conn = db.get_conn()
    for qid in (1, 2):
        conn.execute(
            "INSERT INTO questions(id, lecture_id, question, options, correct_index, "
            "explanation, source) VALUES(?, 1, ?, ?, 2, 'because reasons', 'session')",
            (qid, f"stem {qid}", _json.dumps(["a", "b", "c", "d", "e"])),
        )
    conn.execute("INSERT INTO sessions(id, title, mode, target_count) VALUES(1,'P','practice',2)")
    conn.execute("INSERT INTO session_questions(session_id, question_id, position) VALUES(1,1,0)")
    conn.execute("INSERT INTO session_questions(session_id, question_id, position) VALUES(1,2,1)")
    conn.commit()
    conn.close()

    c = TestClient(M.app)
    first = c.get("/api/sessions/1/next").json()["question"]

    r = c.post(f"/api/sessions/1/answer/{first['id']}", json={"selected_index": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["correct"] is False and body["explanation"] == "because reasons", body

    # The bug: a failed answer left answered=0, so Next re-served the same stem.
    second = c.get("/api/sessions/1/next").json()["question"]
    assert second["id"] != first["id"], "Next re-served the same question"

    conn = db.get_conn()
    a = conn.execute("SELECT selected_index, correct FROM answers").fetchone()
    m = conn.execute("SELECT COUNT(*) c FROM missed WHERE resolved=0").fetchone()["c"]
    conn.close()
    assert a["selected_index"] == 0 and a["correct"] == 0, dict(a)
    assert m == 1, "a wrong answer should be tagged missed"


def _seed_two_question_session():
    import json as _json

    _seed_lecture_with_professor_questions(0)
    conn = db.get_conn()
    for qid in (1, 2):
        conn.execute(
            "INSERT INTO questions(id, lecture_id, question, options, correct_index, "
            "explanation, source) VALUES(?, 1, ?, ?, 2, 'because', 'session')",
            (qid, f"stem {qid}", _json.dumps(["a", "b", "c", "d", "e"])),
        )
    conn.execute("INSERT INTO sessions(id, title, mode, target_count) VALUES(1,'P','practice',2)")
    conn.execute("INSERT INTO session_questions(session_id, question_id, position) VALUES(1,1,0)")
    conn.execute("INSERT INTO session_questions(session_id, question_id, position) VALUES(1,2,1)")
    conn.commit()
    conn.close()


def test_changing_an_answer_recalculates():
    """Fixing a misclick must update the existing answer, not stack a second one."""
    from fastapi.testclient import TestClient
    import app.main as M

    _seed_two_question_session()
    c = TestClient(M.app)

    wrong = c.post("/api/sessions/1/answer/1", json={"selected_index": 0}).json()
    assert wrong["correct"] is False and wrong["revised"] is False

    def state():
        conn = db.get_conn()
        rows = conn.execute("SELECT correct, selected_index FROM answers WHERE question_id=1").fetchall()
        sess = conn.execute("SELECT completed_count FROM sessions WHERE id=1").fetchone()
        miss = conn.execute("SELECT COUNT(*) c FROM missed WHERE question_id=1 AND resolved=0").fetchone()
        conn.close()
        return [dict(r) for r in rows], sess["completed_count"], miss["c"]

    rows, completed, missed = state()
    assert len(rows) == 1 and completed == 1 and missed == 1, (rows, completed, missed)

    # Now fix it: same question, correct option.
    fixed = c.post("/api/sessions/1/answer/1", json={"selected_index": 2}).json()
    assert fixed["correct"] is True and fixed["revised"] is True, fixed

    rows, completed, missed = state()
    assert len(rows) == 1, f"re-answering stacked a duplicate row: {rows}"
    assert rows[0]["correct"] == 1 and rows[0]["selected_index"] == 2, rows
    assert completed == 1, f"progress double-counted the revision: {completed}"
    assert missed == 0, "correcting the answer should clear the missed tag"

    # And back to wrong again re-tags it.
    c.post("/api/sessions/1/answer/1", json={"selected_index": 4})
    rows, completed, missed = state()
    assert len(rows) == 1 and rows[0]["correct"] == 0 and completed == 1, (rows, completed)
    assert missed == 1, "re-missing should tag it again"


def test_nav_and_direct_question_load():
    from fastapi.testclient import TestClient
    import app.main as M

    _seed_two_question_session()
    c = TestClient(M.app)

    nav = c.get("/api/sessions/1/nav").json()
    assert [n["position"] for n in nav] == [0, 1]
    assert all(n["answered"] == 0 and n["selected_index"] is None for n in nav), nav

    c.post("/api/sessions/1/answer/2", json={"selected_index": 2})
    nav = c.get("/api/sessions/1/nav").json()
    by_q = {n["question_id"]: n for n in nav}
    assert by_q[2]["answered"] == 1 and by_q[2]["correct"] == 1, nav
    assert by_q[1]["answered"] == 0, nav

    # Jumping straight to an answered question returns the answer you gave.
    q = c.get("/api/sessions/1/question/2").json()["question"]
    assert q["prior_selected_index"] == 2 and q["prior_correct"] == 1, q
    assert q["explanation"] == "because" and len(q["options"]) == 5

    # An unanswered one has no prior answer, and /next still skips answered ones.
    q1 = c.get("/api/sessions/1/question/1").json()["question"]
    assert q1["prior_selected_index"] is None, q1
    assert c.get("/api/sessions/1/next").json()["question"]["id"] == 1
    assert c.get("/api/sessions/1/question/999").status_code == 404


def _seed_slides_with_answers(n_slides, per_slide_correct, per_slide_wrong,
                              answered_slides=None):
    """A lecture whose slides each have one question with a known answer history."""
    import json as _json

    conn = db.get_conn()
    for t in ("session_questions", "sessions", "answers", "missed", "questions",
              "slides", "lectures"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO lectures(id, title) VALUES(1, 'L')")
    for i in range(1, n_slides + 1):
        conn.execute(
            "INSERT INTO slides(id, lecture_id, slide_num, text) VALUES(?, 1, ?, ?)",
            (i, i, f"slide body number {i} with plenty of text"),
        )
        conn.execute(
            "INSERT INTO questions(id, lecture_id, slide_id, question, options, "
            "correct_index, source) VALUES(?, 1, ?, 'q', ?, 0, 'session')",
            (i, i, _json.dumps(list("abcde"))),
        )
        if answered_slides is not None and i > answered_slides:
            continue   # leave the rest untouched, so work always remains
        # Wrongs first, then corrects: mastery turns on the LAST answer, so this seeds
        # "missed it, then got it" — the normal path through the missed queue.
        for _ in range(per_slide_wrong):
            conn.execute("INSERT INTO answers(question_id, correct) VALUES(?, 0)", (i,))
        for _ in range(per_slide_correct):
            conn.execute("INSERT INTO answers(question_id, correct) VALUES(?, 1)", (i,))
    conn.commit()
    conn.close()


def test_picker_prioritises_what_you_do_not_know():
    """The picker used to weight only by how many questions had been GENERATED for a
    slide, so a session spent most of its questions re-testing slides you already knew
    while unseen ones waited. That is what made the projection say 180 questions when
    ~50 were actually needed."""
    from app import sessiongen as S
    import json as _json

    conn = db.get_conn()
    for t in ("answers", "questions", "slides", "lectures"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO lectures(id, title) VALUES(1, 'L')")
    # 10 slides: 1-4 answered right, 5-7 answered wrong, 8-10 never answered.
    for i in range(1, 11):
        conn.execute("INSERT INTO slides(id, lecture_id, slide_num, text) VALUES(?, 1, ?, ?)",
                     (i, i, f"slide {i} body text with enough characters"))
        conn.execute(
            "INSERT INTO questions(id, lecture_id, slide_id, question, options, "
            "correct_index, source) VALUES(?, 1, ?, 'q', ?, 0, 'session')",
            (i, i, _json.dumps(list("abcde"))))
    for i in range(1, 5):
        conn.execute("INSERT INTO answers(question_id, correct) VALUES(?, 1)", (i,))
    for i in range(5, 8):
        conn.execute("INSERT INTO answers(question_id, correct) VALUES(?, 0)", (i,))
    conn.commit()
    conn.close()

    pool = [{"slide_id": i} for i in range(1, 11)]
    _, weights = S._slide_weights(pool)
    known = sum(weights[i - 1] for i in range(1, 5))
    missed = sum(weights[i - 1] for i in range(5, 8))
    unseen = sum(weights[i - 1] for i in range(8, 11))

    assert unseen / 3 > missed / 3 > known / 4, \
        f"priority should be unseen > missed > known, got {unseen/3:.2f} {missed/3:.2f} {known/4:.2f}"
    # Known slides still appear — reinforcement matters, they just wait their turn.
    assert all(w > 0 for w in weights), "no slide may be excluded outright"


def test_projection_is_stable_between_loads():
    """An unseeded run moved the headline by tens of questions on every refresh, which
    makes the number look invented. It must only change when the data changes."""
    from app.projection import mastery_projection

    _seed_slides_with_answers(20, per_slide_correct=1, per_slide_wrong=0, answered_slides=8)
    a = mastery_projection(trials=40)
    b = mastery_projection(trials=40)
    assert a["questions_remaining"] == b["questions_remaining"], (a, b)
    assert a["milestones"] == b["milestones"]

    # Answering something must move it.
    conn = db.get_conn()
    conn.execute("INSERT INTO answers(question_id, correct) VALUES(12, 1)")
    conn.commit()
    conn.close()
    c = mastery_projection(trials=40)
    assert c["slides_mastered"] == a["slides_mastered"] + 1


def test_projection_matches_real_world_calibration():
    """The number this produces has to match what actually happens at the desk:
    one ~50-question block over a fresh 50-slide lecture gets you past 70% mastery.
    The first version claimed 800+ questions for two lectures, which was nonsense —
    it simulated draws WITH replacement (the real sampler draws distinct slides) and
    demanded two correct answers per slide."""
    from app.projection import mastery_projection

    _seed_slides_with_answers(50, 0, 0)
    p = mastery_projection(trials=60, seed=3)
    by_pct = {m["pct"]: m for m in p["milestones"]}

    assert by_pct[70]["questions"] <= 60, \
        f"70% of a 50-slide lecture should cost about one block, got {by_pct[70]['questions']}"
    assert by_pct[90]["questions"] <= 200, f"90% should stay reasonable: {by_pct[90]}"
    # Milestones must be monotonic — more mastery always costs more.
    assert by_pct[70]["questions"] <= by_pct[80]["questions"] <= by_pct[90]["questions"]
    # The headline targets 80%, not the hyperbolic 100%.
    assert p["target_pct"] == 80
    assert p["questions_remaining"] == by_pct[80]["questions"]

    # A brand-new import assumes a sensible accuracy instead of a coin flip.
    assert p["accuracy_is_assumed"] is True
    assert 0.7 <= p["accuracy"] <= 0.8, p["accuracy"]


def test_projection_reflects_progress_and_accuracy():
    from app.projection import mastery_projection

    # Everything already answered correctly -> nothing left to do.
    _seed_slides_with_answers(5, per_slide_correct=1, per_slide_wrong=0)
    done = mastery_projection(trials=30, seed=1)
    assert done["slides_mastered"] == 5 and done["questions_remaining"] == 0, done
    assert done["mastery_pct"] == 100.0

    # Mastery is the LAST answer, mirroring the missed queue: miss it then get it and
    # it's cleared, however many times you missed it before. A lifetime tally instead
    # made every early miss permanent baggage and the projection ran away.
    _seed_slides_with_answers(5, per_slide_correct=1, per_slide_wrong=3)
    assert mastery_projection(trials=20, seed=1)["slides_mastered"] == 5

    # And getting it right earlier doesn't protect you from missing it now.
    conn = db.get_conn()
    conn.execute("INSERT INTO answers(question_id, correct) VALUES(1, 0)")
    conn.commit()
    conn.close()
    assert mastery_projection(trials=20, seed=1)["slides_mastered"] == 4

    # A poor record must project more work than a strong one. Half the slides stay
    # untouched in both cases, so there is real work left either way.
    _seed_slides_with_answers(20, per_slide_correct=1, per_slide_wrong=3, answered_slides=10)
    weak = mastery_projection(trials=60, seed=1)
    _seed_slides_with_answers(20, per_slide_correct=3, per_slide_wrong=0, answered_slides=10)
    strong = mastery_projection(trials=60, seed=1)
    assert weak["questions_remaining"] > strong["questions_remaining"], (weak, strong)
    assert weak["accuracy"] < strong["accuracy"]

    # A terrible record still converges — drilling a slide is what makes you better at
    # it — but costs far more questions, and accuracy floors at guessing.
    _seed_slides_with_answers(20, per_slide_correct=0, per_slide_wrong=8)
    hopeless = mastery_projection(trials=20, seed=1)
    assert hopeless["accuracy"] >= 0.2, "accuracy floors at guessing on 5 options"
    assert hopeless["questions_remaining"] > strong["questions_remaining"], hopeless

    # No slides at all shouldn't blow up.
    conn = db.get_conn()
    conn.execute("DELETE FROM slides")
    conn.commit()
    conn.close()
    empty = mastery_projection(trials=5, seed=1)
    assert empty["slides_total"] == 0 and empty["questions_remaining"] == 0


def test_recommendations_generated_saved_and_reused():
    from app.llm import recommendations as R
    import json as _json

    _seed_two_question_session()
    conn = db.get_conn()
    conn.execute("INSERT INTO answers(question_id, session_id, correct, selected_index) "
                 "VALUES(1, 1, 0, 0)")
    conn.execute("UPDATE session_questions SET answered=1 WHERE question_id=1")
    conn.commit()
    conn.close()

    calls = []
    R.chat_json = lambda prompt, **kw: (calls.append(prompt), {
        "themes": [{"topic": "Receptor kinetics", "why": "Confused affinity with efficacy.",
                    "action": "Re-read the dose-response section."},
                   {"topic": "", "why": "dropped", "action": "dropped"}],
        "summary": "Focus on dose-response curves.",
    })[1]

    rec = R.generate_recommendations(1)
    assert len(calls) == 1
    assert len(rec["themes"]) == 1, "themes without a topic should be dropped"
    assert rec["themes"][0]["topic"] == "Receptor kinetics"
    assert rec["summary"] == "Focus on dose-response curves."
    assert rec["mistake_count"] == 1

    # Saved: a second call must not spend another request.
    again = R.generate_recommendations(1)
    assert len(calls) == 1, "cached recommendations should not re-call the API"
    assert again["themes"] == rec["themes"]

    # The prompt must actually contain the mistake, not just the question.
    assert "stem 1" in calls[0] and "They chose:" in calls[0]
    assert calls[0].index("Return ONLY json") < calls[0].index("MISTAKES:"), \
        "static prefix must precede the variable mistake list"

    # force=true regenerates.
    R.generate_recommendations(1, force=True)
    assert len(calls) == 2

    assert R.recent_recommendations()[0]["session_id"] == 1


def test_no_recommendations_without_mistakes():
    from app.llm import recommendations as R

    _seed_two_question_session()
    conn = db.get_conn()
    conn.execute("INSERT INTO answers(question_id, session_id, correct, selected_index) "
                 "VALUES(1, 1, 1, 2)")
    conn.commit()
    conn.close()

    called = []
    R.chat_json = lambda prompt, **kw: called.append(1) or {"themes": [], "summary": ""}
    assert R.generate_recommendations(1) is None
    assert not called, "a clean session must not spend an API call"


def test_lecture_week_tagging():
    from fastapi.testclient import TestClient
    import app.main as M

    _seed_lecture_with_professor_questions(0)
    c = TestClient(M.app)

    assert c.get("/api/lectures").json()[0]["week"] is None, "week should start unset"

    assert c.patch("/api/lectures/1", json={"week": 3}).json()["week"] == 3
    # Renaming must not silently wipe the week (omitted != null).
    r = c.patch("/api/lectures/1", json={"title": "Renal Physiology"}).json()
    assert r["week"] == 3 and r["title"] == "Renal Physiology", r
    # Explicit null clears it.
    assert c.patch("/api/lectures/1", json={"week": None}).json()["week"] is None

    assert c.patch("/api/lectures/1", json={"week": 0}).status_code == 400
    assert c.patch("/api/lectures/1", json={"week": 53}).status_code == 400
    assert c.patch("/api/lectures/1", json={"week": -1}).status_code == 400


def test_delete_session_keeps_answers_and_missed():
    from fastapi.testclient import TestClient
    import app.main as M
    import json as _json

    _seed_lecture_with_professor_questions(0)
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO questions(id, lecture_id, question, options, correct_index, source) "
        "VALUES(1, 1, 'q', ?, 0, 'session')", (_json.dumps(["a", "b", "c", "d", "e"]),)
    )
    conn.execute(
        "INSERT INTO sessions(id, title, mode, target_count) VALUES(1, 'Practice (1)', 'practice', 1)"
    )
    conn.execute("INSERT INTO session_questions(session_id, question_id, position) VALUES(1,1,0)")
    conn.execute("INSERT INTO answers(question_id, session_id, correct) VALUES(1, 1, 0)")
    conn.execute("INSERT INTO missed(question_id, lecture_id) VALUES(1, 1)")
    conn.commit()
    conn.close()

    c = TestClient(M.app)
    assert c.delete("/api/sessions/1").status_code == 200
    assert c.delete("/api/sessions/1").status_code == 404, "second delete should 404"

    conn = db.get_conn()
    counts = {
        t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        for t in ("sessions", "session_questions", "questions", "answers", "missed")
    }
    conn.close()
    assert counts["sessions"] == 0 and counts["session_questions"] == 0, counts
    # The point of the feature: clearing history must not erase learning history.
    assert counts["questions"] == 1 and counts["answers"] == 1 and counts["missed"] == 1, counts


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
    sys.exit(0)
