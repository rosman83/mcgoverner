"""How many more questions until every slide is mastered?

Answered by simulating the real thing: sessions of questions drawn the way sessiongen
actually draws them, graded at the accuracy you actually have.

Two modelling choices matter, and getting either wrong makes the number nonsense:

1. A session draws DISTINCT slides. `select_slides` samples without replacement, so a
   50-question session on a 50-slide lecture touches all 50 slides once — it does not
   roll a 50-sided die 50 times. Simulating draw-by-draw with replacement turns this
   into a coupon-collector problem and inflates the estimate several-fold.
2. Mastery is "your last answer on this slide was right", and practice improves your
   chance on a slide. A single 50-question pass over a fresh 50-slide lecture leaves
   you around 70-75% mastered, which is what actually happens in practice.

An earlier version reported 800+ questions for two lectures. It drew slides one at a
time with replacement, demanded two correct answers per slide, counted lifetime
correct-vs-wrong (so every miss permanently raised the bar), and had no learning at
all — four compounding pessimisms.
"""
import random

from app.db import get_conn
from app.sessiongen import (
    MAX_QUESTIONS,
    MIN_SLIDE_WORDS,
    SLIDE_WEIGHT_ALPHA,
    _weighted_sample_without_replacement,
    mastery_multiplier,
)

MASTERY_CORRECT = 1     # correct answers needed before a slide counts as learned
TRIALS = 120            # Monte Carlo runs; the median is the headline number
MIN_SESSION = 10        # nobody starts a 2-question session to mop up stragglers
MAX_SESSIONS_PER_TRIAL = 200   # guard against a pathological slide never mastering
DEFAULT_ACCURACY = 0.75  # assumed until you have answers of your own
PRIOR_STRENGTH = 2       # how many "virtual" answers the global rate is worth per slide
ACCURACY_FLOOR = 0.2     # chance alone on a 5-option question; nobody is truly at 0

# Practising a slide makes you better at it — that is the entire point of the app, and a
# model without it is degenerate: if your chance on each slide never improves, mastery
# just oscillates around your current accuracy and any target above it is unreachable
# forever. Each exposure moves that slide's success chance a fraction of the way toward
# the ceiling, so a slide seen three or four times becomes reliable.
LEARN_RATE = 0.3
P_CEILING = 0.95

# Mastery is reported against targets, not just 100%. Chasing the final few slides is
# hyperbolic — random sampling has to happen to land on exactly the slides you still
# owe — so "questions to 90%" is an actionable number where "questions to 100%" is not.
MILESTONES = (0.7, 0.8, 0.9, 1.0)
HEADLINE_TARGET = 0.8


def _slide_state():
    """Per-slide: questions generated, lifetime correct/wrong, and whether the LAST
    answer was right (which is what mastery turns on)."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.id AS slide_id, "
        "COUNT(DISTINCT q.id) AS q_count, "
        "COALESCE(SUM(CASE WHEN a.correct=1 THEN 1 ELSE 0 END), 0) AS correct, "
        "COALESCE(SUM(CASE WHEN a.correct=0 THEN 1 ELSE 0 END), 0) AS wrong, "
        "(SELECT a2.correct FROM answers a2 "
        "   JOIN questions q2 ON q2.id = a2.question_id "
        "   WHERE q2.slide_id = s.id "
        "   ORDER BY a2.answered_at DESC, a2.id DESC LIMIT 1) AS last_correct "
        "FROM slides s "
        "LEFT JOIN questions q ON q.slide_id = s.id "
        "LEFT JOIN answers a ON a.question_id = q.id "
        "WHERE (length(trim(s.text)) >= ? OR length(trim(s.ocr_text)) >= ?) "
        "GROUP BY s.id ORDER BY s.id",   # fixed order: the seed alone must decide the run
        (MIN_SLIDE_WORDS, MIN_SLIDE_WORDS),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _is_mastered(slide):
    """Mastered = the most recent answer on this slide was correct.

    This mirrors what the app already does: a wrong answer puts a question in the missed
    queue, a right one clears it. Cumulative rules do not work here — with `correct >=
    wrong`, every miss permanently raises the bar, mastery becomes a random walk, and
    slides sitting at 1-wrong-0-right could take hundreds of sessions to settle. What
    matters for an exam is whether you'd get it right now, not your lifetime tally.
    """
    return slide["last_correct"] == 1


def _global_accuracy(slides):
    """Your observed accuracy, or the default when there is nothing to observe.

    A brand-new import has no answers, and assuming a coin flip there would double the
    projected work for no reason. The default gives way to real data as you answer, and
    mistakes pull it down — which is what makes the estimate move.
    """
    correct = sum(s["correct"] for s in slides)
    wrong = sum(s["wrong"] for s in slides)
    total = correct + wrong
    if total < 10:   # too little signal to trust; blend toward the default
        weight = total / 10
        observed = correct / total if total else DEFAULT_ACCURACY
        blended = weight * observed + (1 - weight) * DEFAULT_ACCURACY
    else:
        blended = correct / total
    return max(ACCURACY_FLOOR, blended)


def _simulate(slides, global_p, rng, targets):
    """One run: keep doing sessions until every slide is mastered.
    Returns {target: questions asked when mastery first reached that fraction}."""
    n = len(slides)
    q_counts = [s["q_count"] for s in slides]
    # Per-slide success chance, smoothed toward your global rate so one bad answer does
    # not model a slide as hopeless (and one lucky answer does not model it as solved).
    probs = [
        (s["correct"] + global_p * PRIOR_STRENGTH) / (s["correct"] + s["wrong"] + PRIOR_STRENGTH)
        for s in slides
    ]
    unmastered = {i for i in range(n) if not _is_mastered(slides[i])}

    reached = {}
    pending = sorted(targets)

    def record(asked, sessions):
        while pending and (n - len(unmastered)) >= pending[0] * n:
            reached[pending.pop(0)] = (asked, sessions)

    record(0, 0)   # some targets may already be met
    asked = 0
    sessions = 0
    # Mirror the picker exactly: never-answered slides come first, missed ones next,
    # and slides you already know are mostly left alone. Diverging from sessiongen here
    # would make the projection describe an app that does not exist.
    seen = {i for i in range(n) if slides[i]["last_correct"] is not None}

    while unmastered and sessions < MAX_SESSIONS_PER_TRIAL:
        sessions += 1
        # A realistic session: as big as you need, capped at the app's own limit.
        size = min(MAX_QUESTIONS, n, max(MIN_SESSION, len(unmastered)))
        weights = [
            (1.0 / ((1 + q_counts[i]) ** SLIDE_WEIGHT_ALPHA))
            * mastery_multiplier(None if i not in seen else (0 if i in unmastered else 1))
            for i in range(n)
        ]
        drawn = _weighted_sample_without_replacement(list(range(n)), weights, size, rng)
        asked += len(drawn)
        for i in drawn:
            q_counts[i] += 1
            # Answering right masters the slide; missing it puts it back in the queue,
            # exactly as the missed table behaves.
            if rng.random() < probs[i]:
                unmastered.discard(i)
            else:
                unmastered.add(i)
            seen.add(i)
            # Seeing the slide again (and its explanation) makes the next one easier.
            probs[i] += LEARN_RATE * (P_CEILING - probs[i])
        record(asked, sessions)
    # Targets still pending hit the session guard: at this accuracy the simulation does
    # not converge, and a precise-looking number would be a lie.
    for t in pending:
        reached[t] = (asked, sessions, False)
    return {t: (v[0], v[1], True) if len(v) == 2 else v for t, v in reached.items()}


def _state_seed(slides):
    """A seed derived from the data itself, so the projection is stable between page
    loads and only moves when your answers move. An unseeded RNG made the headline
    jump by tens of questions on every refresh, which reads as the number being
    made up."""
    # All ints — None is mapped to -1 rather than hashed, so the seed is identical
    # across process restarts, not just within one.
    key = tuple(
        (s["slide_id"], s["q_count"], s["correct"], s["wrong"],
         -1 if s["last_correct"] is None else s["last_correct"])
        for s in sorted(slides, key=lambda x: x["slide_id"])
    )
    return hash(key) & 0xFFFFFFFF


def mastery_projection(trials=TRIALS, seed=None):
    """Estimate the questions still needed to master every slide.

    Returns the median and interquartile range across simulations, so the UI can show a
    realistic spread rather than one falsely precise number.
    """
    slides = _slide_state()
    total = len(slides)
    mastered = sum(1 for s in slides if _is_mastered(s))
    answered = sum(s["correct"] + s["wrong"] for s in slides)
    result = {
        "slides_total": total,
        "slides_mastered": mastered,
        "mastery_pct": round(100 * mastered / total, 1) if total else 0,
        "mastery_correct_required": MASTERY_CORRECT,
        "accuracy": None,
        "accuracy_is_assumed": answered == 0,
        "target_pct": int(HEADLINE_TARGET * 100),
        "questions_remaining": 0,
        "questions_p25": 0,
        "questions_p75": 0,
        "sessions_remaining": 0,
        "milestones": [],
        "trials": 0,
    }
    if not total or mastered == total:
        return result

    global_p = _global_accuracy(slides)
    result["accuracy"] = round(global_p, 3)

    rng = random.Random(_state_seed(slides) if seed is None else seed)
    runs = [_simulate(slides, global_p, rng, MILESTONES) for _ in range(trials)]
    result["trials"] = trials

    def median(values):
        vs = sorted(values)
        return vs[len(vs) // 2]

    def quartiles(values):
        vs = sorted(values)
        return vs[len(vs) // 4], vs[(3 * len(vs)) // 4]

    result["milestones"] = [
        {
            "pct": int(t * 100),
            "questions": median(r[t][0] for r in runs),
            "sessions": median(r[t][1] for r in runs),
            "already_there": mastered >= t * total,
            # False when most runs never got there — at this accuracy the target is out
            # of reach, and the question count would be meaningless.
            "reachable": sum(1 for r in runs if r[t][2]) > len(runs) / 2,
        }
        for t in MILESTONES
    ]
    headline = [m for m in result["milestones"] if m["pct"] == int(HEADLINE_TARGET * 100)][0]
    result["questions_remaining"] = headline["questions"]
    result["sessions_remaining"] = headline["sessions"]
    result["questions_p25"], result["questions_p75"] = quartiles(
        r[HEADLINE_TARGET][0] for r in runs
    )
    return result
