"""Checks for the origin/host guard, projection cache and image batching.
Run: .venv/bin/python test_platform.py   (no network, throwaway DB)"""
import os
import sys
import tempfile

os.environ["BLOCK1_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

import app.db as db

db.DB_PATH = os.environ["BLOCK1_DB"]
db.init_db()

from fastapi.testclient import TestClient

import app.main as M
from app import projection as P
from app.ingest.images import images_by_slide, images_for_slides

c = TestClient(M.app)

_conn = db.get_conn()
_conn.execute("INSERT INTO lectures(id, title) VALUES(1, 'L')")
_conn.execute("INSERT INTO slides(id, lecture_id, slide_num, text) VALUES(1, 1, 1, ?)", ("x" * 40,))
_conn.commit()
_conn.close()


def test_foreign_origin_blocked_local_allowed():
    assert c.delete("/api/sessions/999", headers={"Origin": "https://evil.example"}).status_code == 403
    # no Origin (curl, same-origin GET) and local origins pass the guard
    assert c.get("/api/lectures").status_code == 200
    r = c.delete("/api/sessions/999", headers={"Origin": "http://localhost:8000"})
    assert r.status_code == 404
    assert c.get("/api/lectures", headers={"Origin": "https://evil.example"}).status_code == 200  # reads aren't state-changing


def test_unknown_host_rejected():
    assert c.get("/api/lectures", headers={"Host": "evil.example"}).status_code == 400


def test_projection_cache_invalidates():
    calls = []
    real = P._mastery_projection
    P._mastery_projection = lambda *a, **k: (calls.append(1), {"n": len(calls)})[1]
    try:
        P._CACHE.clear()
        assert P.mastery_projection()["n"] == 1
        assert P.mastery_projection()["n"] == 1, "unchanged DB must hit the cache"
        conn = db.get_conn()
        conn.execute("INSERT INTO questions(id, lecture_id, slide_id, question, options, correct_index, explanation) "
                     "VALUES(1, 1, 1, 'q', '[]', 0, 'e')")
        conn.execute("INSERT INTO answers(question_id, correct) VALUES(1, 1)")
        conn.commit()
        conn.close()
        assert P.mastery_projection()["n"] == 2, "a new answer must invalidate the cache"
        assert P.mastery_projection(seed=1)["n"] == 3, "explicit seed bypasses the cache"
    finally:
        P._mastery_projection = real


def test_images_batched():
    conn = db.get_conn()
    conn.execute("INSERT INTO slides(id, lecture_id, slide_num, text) VALUES(2, 1, 2, 'y')")
    conn.execute("INSERT INTO slide_images(slide_id, seq, path) VALUES(1, 0, 'a.png')")
    conn.execute("INSERT INTO slide_images(slide_id, seq, path) VALUES(1, 1, 'b.png')")
    conn.execute("INSERT INTO slide_images(slide_id, seq, path) VALUES(2, 0, 'c.png')")
    conn.commit()
    conn.close()
    assert images_by_slide([1, 2, None]) == {1: ["/images/a.png", "/images/b.png"], 2: ["/images/c.png"]}
    assert images_for_slides([2]) == ["/images/c.png"]
    assert images_for_slides([]) == [] and images_by_slide([None]) == {}


def test_indexes_created():
    conn = db.get_conn()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert {"idx_questions_slide", "idx_answers_session", "idx_missed_question"} <= names


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")
    sys.exit(0)
