"""Self-check: generated/imported questions must not always land the correct
answer at index 0 ("A"). Run: .venv/bin/python test_answer_shuffle.py
"""
import random

from app.sessiongen import _question_from_result
from app.ingest.questionsets import _parse_chunk


def test_sessiongen_shuffles_and_preserves_answer():
    random.seed(0)
    s = {"lecture_id": 1, "slide_id": 2, "slide_num": 3}
    seen_zero = False
    for _ in range(50):
        result = {
            "question": "What is the mechanism? Because it happens due to reasons.",
            "options": ["right answer", "b", "c", "d", "e"],
            "correct_index": 0,
            "explanation": "",
        }
        q = _question_from_result(s, result)
        assert q is not None
        assert q["options"][q["correct_index"]] == "right answer"
        if q["correct_index"] == 0:
            seen_zero = True
    assert seen_zero, "expected some shuffles to land back on index 0 by chance"


def test_parse_chunk_shuffles_and_preserves_answer():
    import app.ingest.questionsets as qs

    def fake_chat_json(*a, **k):
        return {
            "questions": [
                {
                    "question": "stem",
                    "options": ["right answer", "b", "c"],
                    "correct_index": 0,
                    "explanation": "",
                }
            ]
        }

    real_chat_json = qs.chat_json
    qs.chat_json = fake_chat_json
    try:
        random.seed(1)
        indices = set()
        for _ in range(50):
            out = _parse_chunk("text", "")
            assert out[0]["options"][out[0]["correct_index"]] == "right answer"
            indices.add(out[0]["correct_index"])
        assert indices != {0}, "correct_index never moved off 0 across 50 runs"
    finally:
        qs.chat_json = real_chat_json


if __name__ == "__main__":
    test_sessiongen_shuffles_and_preserves_answer()
    test_parse_chunk_shuffles_and_preserves_answer()
    print("ok")
