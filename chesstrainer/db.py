"""SQLite storage."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "trainer.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    end_time INTEGER,
    user_color TEXT,           -- 'white' | 'black'
    time_class TEXT,
    base_secs INTEGER,
    inc_secs INTEGER,
    result TEXT,               -- 'win' | 'loss' | 'draw'
    opponent TEXT,
    user_rating INTEGER,
    opp_rating INTEGER,
    pgn TEXT NOT NULL,
    analyzed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS moves (
    game_id TEXT NOT NULL,
    ply INTEGER NOT NULL,      -- index of the user's move (0-based)
    move_no INTEGER,
    san TEXT,
    uci TEXT,
    best_uci TEXT,
    best_san TEXT,
    phase TEXT,
    clock REAL,                -- seconds left after the move
    time_spent REAL,
    win_before REAL,
    win_after REAL,
    loss REAL,
    cls TEXT,                  -- ok | inaccuracy | mistake | blunder
    PRIMARY KEY (game_id, ply)
);

CREATE TABLE IF NOT EXISTS puzzles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL,
    ply INTEGER NOT NULL,
    fen TEXT NOT NULL,
    solution TEXT NOT NULL,        -- uci
    accepted TEXT NOT NULL,        -- json list of uci (includes solution)
    played_uci TEXT,
    played_san TEXT,
    solution_san TEXT,
    severity TEXT,                 -- mistake | blunder
    loss REAL,
    tags TEXT NOT NULL,            -- json list of motif tags
    context TEXT NOT NULL,         -- json list of context tags
    time_spent REAL,
    phase TEXT,
    end_time INTEGER,
    UNIQUE (game_id, ply)
);

CREATE TABLE IF NOT EXISTS puzzle_state (
    puzzle_id INTEGER PRIMARY KEY,
    box INTEGER NOT NULL DEFAULT 0,
    due INTEGER NOT NULL DEFAULT 0,   -- unix seconds
    seen INTEGER NOT NULL DEFAULT 0,
    fails INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER
);

-- spaced repetition for the hand-picked repertoire drills (chesstrainer/repertoire.py)
CREATE TABLE IF NOT EXISTS drill_state (
    key TEXT PRIMARY KEY,
    box INTEGER NOT NULL DEFAULT 0,
    due INTEGER NOT NULL DEFAULT 0,
    seen INTEGER NOT NULL DEFAULT 0,
    fails INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    puzzle_id INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    first_try INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    ms INTEGER
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


# columns added after the first version: (table, column, type)
_NEW_COLUMNS = [
    ("puzzles", "line", "TEXT"),          # json list of uci: best move + continuation
    ("puzzles", "refutation", "TEXT"),    # json list of uci: your move + the punishment
    ("puzzles", "eval_best", "REAL"),     # centipawns from your point of view after the best move
    ("puzzles", "eval_played", "REAL"),   # ... after the move you played
]


def _migrate(con: sqlite3.Connection) -> None:
    for table, col, typ in _NEW_COLUMNS:
        have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    con.commit()


def get_setting(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


def jloads(s: str | None) -> list:
    return json.loads(s) if s else []
