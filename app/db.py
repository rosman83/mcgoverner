import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "block1.db")

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
    ocr_status TEXT DEFAULT 'not_run'
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
    answered_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_questions (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    answered INTEGER DEFAULT 0,
    PRIMARY KEY (session_id, question_id)
);
"""


def get_conn(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn):
    """Additive migrations. Each step only runs once, so existing data is never dropped.
    Bump SCHEMA_VERSION when you add a new column/table to SCHEMA."""
    SCHEMA_VERSION = 13
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 2:
        # Add mastery tracking to concepts (safe additive column)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(concepts)").fetchall()]
        if "last_reviewed_at" not in cols:
            conn.execute("ALTER TABLE concepts ADD COLUMN last_reviewed_at TEXT")
        if "accuracy" not in cols:
            conn.execute("ALTER TABLE concepts ADD COLUMN accuracy REAL DEFAULT NULL")
    if version < 3:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS slide_images ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  slide_id INTEGER NOT NULL REFERENCES slides(id) ON DELETE CASCADE,"
            "  path TEXT NOT NULL,"
            "  kind TEXT DEFAULT 'embedded',"
            "  seq INTEGER DEFAULT 0,"
            "  created_at TEXT DEFAULT (datetime('now'))"
            ")"
        )
    if version < 4:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(slides)").fetchall()]
        if "ocr_text" not in cols:
            conn.execute("ALTER TABLE slides ADD COLUMN ocr_text TEXT DEFAULT ''")
        conn.execute(
            "ALTER TABLE lectures ADD COLUMN ocr_status TEXT DEFAULT 'not_run'"
        )
    if version < 5:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(slides)").fetchall()]
        if "caption" not in cols:
            conn.execute("ALTER TABLE slides ADD COLUMN caption TEXT DEFAULT ''")
    if version < 6:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(scheduler_state)").fetchall()]
        if "learning_step" not in cols:
            conn.execute(
                "ALTER TABLE scheduler_state ADD COLUMN learning_step INTEGER DEFAULT 0"
            )
    if version < 7:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS concept_details ("
            "  concept_id INTEGER PRIMARY KEY REFERENCES concepts(id) ON DELETE CASCADE,"
            "  detail TEXT,"
            "  level TEXT DEFAULT 'recall'"
            ")"
        )
    if version < 8:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "tutor_mode" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN tutor_mode INTEGER DEFAULT 1")
        if "time_limit_min" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN time_limit_min INTEGER DEFAULT 0")
    if version < 9:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS missed ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,"
            "  lecture_id INTEGER,"
            "  missed_at TEXT DEFAULT (datetime('now')),"
            "  resolved INTEGER DEFAULT 0,"
            "  last_wrong_at TEXT"
            ")"
        )
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "gen_status" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN gen_status TEXT DEFAULT 'ready'")
    if version < 10:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(questions)").fetchall()]
        if "slide_id" not in cols:
            conn.execute("ALTER TABLE questions ADD COLUMN slide_id INTEGER")
    if version < 11:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS answers ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,"
            "  session_id INTEGER,"
            "  correct INTEGER NOT NULL,"
            "  answered_at TEXT DEFAULT (datetime('now'))"
            ")"
        )
    if version < 12:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(answers)").fetchall()]
        if "selected_index" not in cols:
            conn.execute("ALTER TABLE answers ADD COLUMN selected_index INTEGER")
    if version < 13:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
        if "elapsed_sec" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN elapsed_sec INTEGER DEFAULT 0")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def to_json(obj):
    return json.dumps(obj)


def concept_has_questions_sql():
    """SQL predicate: a question's concept_ids JSON array contains c.id (exact match)."""
    return "EXISTS (SELECT 1 FROM questions q, json_each(q.concept_ids) je WHERE je.value = c.id)"
