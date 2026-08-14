# Block 1 Exam Prep

A UWorld-style, lecture-anchored practice engine for medical school block exams. Built from your actual lecture slides — not generic question banks, not Anki.

## How it works

```
Your lectures (PDF/PPTX)
   │  extract text per slide (+ OCR + image captions)
   ▼
Knowledge base (SQLite)
   ├─ Lectures → Slides   (every slide is the atomic unit, coordinate = lecture, slide #)
   ├─ Summaries           (DeepSeek condensed, high-yield, slide-cited)
   └─ Questions           (MCQ + explanation, anchored directly to a slide)
        │
        └─ Missed tracking (wrong answers tagged for next-day review)
```

Every question is anchored to a specific slide by coordinate `(lecture, slide #)` —
there is no intermediate "concept" layer. Question stems are built from the slide's
own text + OCR + caption, so redundancy in questions simply mirrors the source slides.

## Daily protocol

1. **Learn** — pick today's lecture, read the condensed summary.
2. **Drill** — start a session (tutor or quiz mode, UWorld-style MCQs with teaching explanations).
3. **Review** — do "due reviews" (spaced repetition pulls old material back automatically).

Sessions are dynamic: set your own target, pause/resume anytime, answers update the scheduler.

## Session modes

- **Practice** — pick lectures (or all) and a count (max 59); fresh questions are generated on the spot, one per selected slide. Answer, see the explanation immediately.
- **Review missed** — replays every question you've gotten wrong. Answer it correctly and it's cleared; miss it again and it stays tagged.

Wrong answers are automatically tagged as "missed" and offered for review the next day. No rating buttons, no spaced-repetition setup — just answer, learn, and revisit what you missed.

## Coverage & progress

Coverage is measured in **slides**: a slide is covered once a question has been generated from it. The dashboard and Progress tab show per-lecture slide coverage, and per-slide accuracy from your answer history — so you can see exactly which slides you've practiced and which you've missed.

## Duplicate handling

When you import a lecture, the app fingerprints its slide text and compares against existing lectures. True duplicates (e.g. the same deck re-uploaded) are **skipped automatically** and reported in the import result.

## Question volume

Questions are generated fresh on demand (never pre-banked), capped at 59 per session so a session stays manageable.

## Setup

```bash
# 1. Create .env with your DeepSeek key
cp .env.example .env   # then add your DEEPSEEK_API_KEY

# 2. Run (creates venv + installs deps on first run)
./run.sh
```

Open http://localhost:8000

## First use

1. **Dashboard → Import lectures** — upload your PDF/PPTX slides.
2. **Learn** — open a lecture, click *Generate now*. This creates the summary AND primes all its questions in the background (~1 min per lecture).
3. **Drill** — start a session. Answer MCQs → read the teaching explanation → rate yourself (Again/Hard/Good/Easy) → the scheduler adjusts your next review date.

## Project layout

```
app/
  db.py            SQLite schema + helpers
  ingest/
    slides.py      PDF/PPTX → slide text
    concepts.py    slide text → concept chunks (coverage map)
  llm/
    client.py      DeepSeek API (OpenAI-compatible)
    summaries.py   condensed lecture summaries
    questions.py   anchored MCQ + explanation generation
  scheduler.py     spaced repetition (SM-2 variant)
  main.py          FastAPI app + routes
  static/          frontend (vanilla JS, no build step)
lectures/          imported source files
data/block1.db     local database
```

## API keys

The app reads `DEEPSEEK_API_KEY` from `.env` (auto-loaded by `run.sh`). You can also `export DEEPSEEK_API_KEY=sk-...` manually.
