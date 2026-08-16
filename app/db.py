import sqlite3
import json
import os
from datetime import datetime

# BLOCK1_DB points the app at a different database — used to run the UI tests against a
# throwaway copy instead of your real one.
DB_PATH = os.environ.get("BLOCK1_DB") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "block1.db"
)

# sqlite cannot create the db file if its parent dir is missing (data/ is gitignored,
# so a fresh clone has no data/ and get_conn fails with "unable to open database file").
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS lectures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    filename TEXT,
    source TEXT,
    slide_count INTEGER DEFAULT 0,
    word_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    summary_status TEXT DEFAULT 'not_started',
    ocr_status TEXT DEFAULT 'not_run',
    tag TEXT DEFAULT 'foundations',     -- course strand: foundations | doctoring | anatomy
    week INTEGER                        -- course week the lecture was given (NULL = untagged)
);

CREATE TABLE IF NOT EXISTS slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    slide_num INTEGER NOT NULL,
    text TEXT NOT NULL,
    ocr_text TEXT DEFAULT '',
    caption TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS slide_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slide_id INTEGER NOT NULL REFERENCES slides(id) ON DELETE CASCADE,
    path TEXT NOT NULL,                 -- relative path under data/images
    kind TEXT DEFAULT 'embedded',       -- 'page' (pdf render) | 'embedded' (pptx)
    seq INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    slide_ids TEXT,
    slide_nums TEXT,
    status TEXT DEFAULT 'unseen',
    last_reviewed_at TEXT,
    accuracy REAL DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS concept_details (
    concept_id INTEGER PRIMARY KEY REFERENCES concepts(id) ON DELETE CASCADE,
    detail TEXT,
    level TEXT DEFAULT 'recall'
);

CREATE TABLE IF NOT EXISTS summaries (
    lecture_id INTEGER PRIMARY KEY REFERENCES lectures(id) ON DELETE CASCADE,
    body TEXT,
    key_points TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lecture_id INTEGER NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    slide_id INTEGER REFERENCES slides(id) ON DELETE SET NULL,
    concept_ids TEXT,
    question TEXT NOT NULL,
    options TEXT NOT NULL,
    correct_index INTEGER NOT NULL,
    explanation TEXT,
    level TEXT DEFAULT 'recall',
    source TEXT DEFAULT 'generated',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    answered_at TEXT DEFAULT (datetime('now')),
    correct INTEGER NOT NULL,
    rating TEXT NOT NULL,
    ease REAL DEFAULT 2.5,
    interval_days REAL DEFAULT 0,
    due_at TEXT
);

CREATE TABLE IF NOT EXISTS scheduler_state (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    ease REAL DEFAULT 2.5,
    interval_days REAL DEFAULT 0,
    reps INTEGER DEFAULT 0,
    lapses INTEGER DEFAULT 0,
    learning_step INTEGER DEFAULT 0,
    due_at TEXT NOT NULL,
    first_seen TEXT DEFAULT (datetime('now')),
    last_review TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    status TEXT DEFAULT 'active',
    mode TEXT,
    target_count INTEGER,
    completed_count INTEGER DEFAULT 0,
    lecture_id INTEGER,
    current_question_id INTEGER,
    tutor_mode INTEGER DEFAULT 1,
    time_limit_min INTEGER DEFAULT 0,
    gen_status TEXT DEFAULT 'ready',
    elapsed_sec INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS missed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    lecture_id INTEGER,
    missed_at TEXT DEFAULT (datetime('now')),
    resolved INTEGER DEFAULT 0,
    last_wrong_at TEXT
);

CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    session_id INTEGER,
    correct INTEGER NOT NULL,
    selected_index INTEGER,
    answered_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_questions (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    answered INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, question_id)
);

CREATE TABLE IF NOT EXISTS recommendations (
    session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    themes TEXT,                        -- json: [{topic, why, action}]
    summary TEXT,
    mistake_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT,
    model TEXT,
    kind TEXT,                          -- 'questions' | 'summary' | 'caption'
    prompt_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,    -- subset of prompt_tokens billed at cache rate
    completion_tokens INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def get_conn(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Bump when you add a column/table to SCHEMA, and add the matching step in _migrate.
SCHEMA_VERSION = 17


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    conn.close()


# Columns that must exist on each table, whatever route the database took to get here.
# SCHEMA and the old version-gated migrations had drifted apart (SCHEMA never gained
# answers.selected_index or sessions.elapsed_sec), so a database built one way was
# missing columns the other way had. Reconciling against this list every startup means
# drift self-heals instead of surfacing as a 500 mid-session.
EXPECTED_COLUMNS = {
    "concepts": [
        ("last_reviewed_at", "TEXT"),
        ("accuracy", "REAL DEFAULT NULL"),
    ],
    "slides": [
        ("ocr_text", "TEXT DEFAULT ''"),
        ("caption", "TEXT DEFAULT ''"),
    ],
    "lectures": [
        ("ocr_status", "TEXT DEFAULT 'not_run'"),
        ("tag", "TEXT DEFAULT 'foundations'"),
        ("week", "INTEGER"),
    ],
    "scheduler_state": [
        ("learning_step", "INTEGER DEFAULT 0"),
    ],
    "sessions": [
        ("tutor_mode", "INTEGER DEFAULT 1"),
        ("time_limit_min", "INTEGER DEFAULT 0"),
        ("gen_status", "TEXT DEFAULT 'ready'"),
        ("elapsed_sec", "INTEGER DEFAULT 0"),
    ],
    "questions": [
        ("slide_id", "INTEGER"),
        ("source", "TEXT DEFAULT 'generated'"),
    ],
    "answers": [
        ("selected_index", "INTEGER"),
    ],
}


def _migrate(conn):
    """Reconcile the database with EXPECTED_COLUMNS. Purely additive: every step is
    guarded, so this is safe to run on every startup and never drops data."""
    existing_tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    added = []
    for table, columns in EXPECTED_COLUMNS.items():
        if table not in existing_tables:
            continue
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                added.append(f"{table}.{name}")
    if added:
        print(f"db: added missing column(s): {', '.join(added)}")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return added


def to_json(obj):
    return json.dumps(obj)


def concept_has_questions_sql():
    """SQL predicate: a question's concept_ids JSON array contains c.id (exact match)."""
    return "EXISTS (SELECT 1 FROM questions q, json_each(q.concept_ids) je WHERE je.value = c.id)"
