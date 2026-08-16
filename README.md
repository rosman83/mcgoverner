# McGoverner

A UWorld-style, lecture-anchored practice engine for medical school block exams. Built from your actual lecture slides — not generic question banks, not Anki.

## How it works

```
Your lectures (PDF/PPTX)
   │  extract text per slide (+ OCR + image captions)
   ▼
Knowledge base (SQLite)
   ├─ Lectures → Slides   (every slide is the atomic unit, coordinate = lecture, slide #)
   ├─ Summaries           (LLM-condensed, high-yield, slide-cited)
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
3. **Review** — do "due reviews" (missed questions).

Sessions are dynamic: set your own target, pause/resume anytime, answers update the scheduler.

## Answering

Clicking an option only **highlights** it — nothing is graded or recorded until you
press **Submit**, so a misclick costs nothing. In tutor mode Submit reveals the
explanation; **Next** moves on.

Under every question is a numbered strip of the whole session. Click any number to jump
back to that question: your previous choice is shown (with its feedback, in tutor mode)
and the options stay live, so you can change the answer. A revision **updates** your
existing answer rather than adding a second one — accuracy, the missed queue, and the
progress count all reflect your latest answer.

### Tests

```bash
.venv/bin/python test_llm.py     # API client, generation, sessions, schema (no network)

# UI state machine, driving the real app.js in jsdom against a throwaway database:
npm i jsdom
BLOCK1_DB=/tmp/ui.db .venv/bin/python seed_ui_db.py
BLOCK1_DB=/tmp/ui.db ./run.sh &
node test_ui.js
```

`BLOCK1_DB` points the app at a different database — always set it when testing so your
real one is never touched.

## Session modes

- **Practice** — pick lectures (or all) and a count (max 59); fresh questions are generated on the spot, one per selected slide. Answer, see the explanation immediately.
- **Review missed** — replays every question you've gotten wrong. Answer it correctly and it's cleared; miss it again and it stays tagged.

Wrong answers are automatically tagged as "missed" and offered for review the next day. No rating buttons, no spaced-repetition setup — just answer, learn, and revisit what you missed.

## Renaming & tagging lectures

In **Learn**, click a lecture's name to edit it in place — Enter or clicking away saves
immediately, Escape cancels. Pasted names are flattened to a single line, so a title
copied out of Canvas lands clean.

Left of the name is a course-strand pill: **foundations** (blue, the default for every
import), **doctoring** (green), **anatomy** (amber). Changing it saves right away.
Add strands in `LECTURE_TAGS` (`main.py`, and the matching list in `app.js` + colors in
`style.css`).

Next to it is a **Week** pill — type the week the lecture was given (1–52) and it saves
on Enter or blur; clear the box to untag. Weeks are stored on the lecture, so they are
available for grouping or filtering later.

## Professor practice questions

Question handouts from your professor are imported as **questions, not slides**
(Dashboard → *Import professor practice questions*, attached to a lecture). They are
parsed once by the model, stored with `source='professor'` and `slide_id` NULL, and
then reused for free forever.

Why they bypass the slide pipeline: a question handout run through the normal import
would have MCQs generated *from* question pages — with the answer key sitting in the
source text — and would inflate slide-coverage stats with pages that aren't lecture
content. `slide_id` NULL keeps them out of coverage math entirely.

Each practice session fills up to **40%** (`PROFESSOR_MIX` in `sessiongen.py`) from
professor questions, preferring the ones you have answered least, and generates only
the shortfall. So they raise question quality and cut API spend at the same time.
They carry a "Professor-written" badge in the explanation footnote.

## Projected work to mastery

The dashboard estimates the questions between you and each mastery level — 70%, 80%
(the headline), 90%, 100%. A slide is **mastered when your most recent answer on it was
right**, mirroring the missed queue: miss it and it goes back, get it and it clears.

It's a Monte Carlo simulation (`projection.py`) that replays the app's own behaviour
session by session: `select_slides` draws *distinct* slides weighted by
`1/(1+q_count)^0.6`, you answer at your observed accuracy, and each exposure raises your
chance on that slide toward a ceiling — because re-drilling a slide is what makes you
better at it. 120 runs, median plus interquartile range.

Calibration: one 50-question block over a fresh 50-slide lecture lands at ~70% mastery,
which is what actually happens. A test asserts this so the model can't drift.

Four things make an estimate absurd, and the first version did all four — drawing one
slide at a time *with* replacement (a coupon-collector problem), requiring two correct
answers per slide, counting lifetime correct-vs-wrong so every miss permanently raised
the bar, and modelling no learning at all (which makes any target above your current
accuracy unreachable by construction). It claimed 800+ questions for two lectures.

The last few percent cost disproportionately more — random draws have to land on exactly
the slides you still owe — so the headline targets 80% and 100% is often reported as out
of reach. That's honest, not a bug.

With no answers yet, accuracy is assumed at `DEFAULT_ACCURACY` (75%) rather than a coin
flip, and gives way to your real numbers as you answer. It floors at 20% — chance alone
on a five-option question.

## Slide footnotes

Every slide citation — in a lecture summary and under a question's explanation — is a
footnote. Hover it for a preview card (thumbnail + caption); click to expand the slide
inline, right where you were reading. Click again to collapse. Nothing covers the page,
several can be open at once, and slide fetches are cached per session. Images inside an
expanded footnote still open full-size in the lightbox on a further click.

## Recommendations from your mistakes

Finish a session with at least one miss and it analyses what you got wrong — grouping
mistakes into themes with a concrete next step each, plus the single highest-value thing
to fix. One API call per session, stored in the `recommendations` table, so reopening
that review is free. They accumulate under **Progress → Saved recommendations** as a
running record of weak spots.

## Coverage & progress

## How slides are picked

`_slide_weights` in `sessiongen.py` ranks every slide by two things: how many questions
already exist for it (`1/(1+q_count)^0.6`) **and** whether you know it —

| state | multiplier |
|---|---|
| never answered | ×3.0 |
| last answer wrong | ×2.0 |
| last answer right | ×0.35 |

Nothing is ever excluded, so sessions stay unpredictable and slides you know still come
round for reinforcement — just far less often. Before this, weighting looked only at how
many questions had been *generated* for a slide, so a session spent most of its questions
re-testing material you already knew while unseen slides waited their turn. That made
real sessions inefficient and the mastery projection roughly 2.5× too pessimistic.

Coverage is measured in **slides**: a slide is covered once a question has been generated from it. The dashboard and Progress tab show per-lecture slide coverage, and per-slide accuracy from your answer history — so you can see exactly which slides you've practiced and which you've missed.

## Duplicate handling

When you import a lecture, the app fingerprints its slide text and compares against existing lectures. True duplicates (e.g. the same deck re-uploaded) are **skipped automatically** and reported in the import result.

## Question volume

Questions are generated fresh on demand (never pre-banked), capped at 59 per session so a session stays manageable.

## Setup

```bash
# 1. Create .env with your provider key
cp .env.example .env   # then add DEEPSEEK_API_KEY (and OPENROUTER_API_KEY for vision)

# 2. Run (creates venv + installs pinned deps from requirements.txt)
./run.sh
```

Open http://localhost:8000

## First use

1. **Dashboard → Import lectures** — upload your PDF/PPTX slides.
2. **Learn** — open a lecture, click *Generate now*. This creates the summary AND primes all its questions in the background (~1 min per lecture).
3. **Drill** — start a session. Answer MCQs → read the teaching explanation. Wrong answers are tagged for review the next day.

## Project layout

```
app/
  db.py            SQLite schema + helpers
  ingest/
    slides.py      PDF/PPTX → slide text
    images.py      slide image extraction
    ocr.py         Apple Vision OCR of slide images, OpenRouter vision fallback for thin OCR
    questionsets.py professor question handouts → questions (bypasses slides)
    concepts.py    slide coverage stats
  llm/
    client.py      provider client (DeepSeek direct / OpenRouter, OpenAI-compatible)
    summaries.py   condensed lecture summaries
    captions.py    batched image captions from OCR text
  sessiongen.py    slide-anchored MCQ generation (the question engine)
  scheduler.py     spaced repetition (SM-2 variant)
  main.py          FastAPI app + routes
  static/          frontend (vanilla JS, no build step)
lectures/          imported source files
data/block1.db     local database
```

## API keys & providers

DeepSeek is billed direct. OpenRouter is a single OpenAI-compatible gateway for
everything else (alternate text models, and the vision fallback below) — one key
instead of a separate subscription per provider:

| Provider | Key env var | Default model |
|---|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` | `deepseek-chat` |
| `openrouter` | `OPENROUTER_API_KEY` | `deepseek/deepseek-chat` |

Set `LLM_PROVIDER=deepseek|openrouter` to choose; if unset, whichever key is present wins.
Override the model with `LLM_MODEL`, or the endpoint with `LLM_BASE_URL`.
`run.sh` auto-loads `.env`; you can also `export` the vars manually.

**Vision fallback:** slides with images but little/no OCR text (research-paper figures,
anatomy diagrams) get described by an OpenRouter vision model (`OPENROUTER_VISION_MODEL`,
default `google/gemini-2.0-flash-001`), independent of your `LLM_PROVIDER` choice above.
Set `OPENROUTER_API_KEY` even if your text provider is DeepSeek, or image-only slides
stay thin. Without it, they're silently skipped (same as OCR always was).

## Token usage

The header chip shows the active model and today's token spend; hover it for a
per-task breakdown (questions / summaries / captions) and cache-hit rate. Raw rows
live in the `api_usage` table, and `/api/usage` returns the same data as JSON.

Prompts are ordered static-first so provider prompt caching (DeepSeek direct, or
whatever OpenRouter routes to) bills the repeated ~900-token instruction prefix at
the cache rate. Keep variable text (slide bodies) at the END of any prompt you add.
