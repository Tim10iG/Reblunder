"""Pick the warm-up puzzles and keep the spaced-repetition state (Leitner boxes)."""
from __future__ import annotations

import json
import random
import sqlite3
import time
from collections import Counter

# hours until a puzzle is due again after being solved FIRST TRY from box k-1 into box k
BOX_HOURS = [0, 24, 72, 168, 504]
MAX_BOX = len(BOX_HOURS) - 1
RECENT_WINDOW = 150          # pattern weights look at the newest N mistakes


def _load(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT p.*, s.box, s.due, s.seen, s.fails, s.last_seen "
        "FROM puzzles p JOIN puzzle_state s ON s.puzzle_id=p.id "
        "ORDER BY p.end_time DESC, p.ply DESC").fetchall()


def pattern_shares(rows) -> dict[str, float]:
    """Share of each motif tag among the most recent mistakes (0..1)."""
    cnt: Counter = Counter()
    for r in rows[:RECENT_WINDOW]:
        for t in json.loads(r["tags"]):
            cnt[t] += 1
    total = sum(cnt.values()) or 1
    return {t: c / total for t, c in cnt.items()}


def pick(con: sqlite3.Connection, n: int = 5, now: int | None = None,
         rng: random.Random | None = None, mode: str = "mixed") -> list[sqlite3.Row]:
    """Choose n puzzles: due ones first, weighted towards your most frequent recent patterns,
    newer mistakes, and puzzles you failed before; spread over different games and patterns.

    mode='opening' only uses mistakes from your first moves and prefers positions you reached
    more than once."""
    now = now or int(time.time())
    rng = rng or random.Random()
    rows = _load(con)
    if mode == "opening":
        rows = [r for r in rows if r["phase"] == "opening"]
    if not rows:
        return []
    position = lambda r: " ".join(r["fen"].split()[:4])
    repeats = Counter(position(r) for r in rows)
    shares = pattern_shares(rows)
    recency = {r["id"]: 1 - i / max(1, len(rows)) for i, r in enumerate(rows)}  # newest = 1

    def score(r) -> float:
        tags = json.loads(r["tags"])
        pat = max((shares.get(t, 0) for t in tags), default=0)
        s = 1.0 + 4.0 * pat + 1.0 * recency[r["id"]] + 0.5 * min(r["fails"], 3)
        s += 0.4 if r["severity"] == "blunder" else 0
        if mode == "opening":
            s += 1.0 * min(repeats[position(r)] - 1, 3)
        return s + rng.random() * 0.4

    due = [r for r in rows if r["due"] <= now]
    pool = due if len(due) >= n else due + sorted(
        [r for r in rows if r["due"] > now], key=lambda r: (r["box"], r["last_seen"] or 0))[: n * 3]
    scored = {r["id"]: score(r) for r in pool}
    chosen: list[sqlite3.Row] = []
    for _ in range(min(n, len(pool))):
        best = max((r for r in pool if r not in chosen), key=lambda r: scored[r["id"]])
        chosen.append(best)
        btags = set(json.loads(best["tags"]))
        for r in pool:      # diversity penalty
            if r in chosen:
                continue
            if r["game_id"] == best["game_id"]:
                scored[r["id"]] *= 0.25
            if btags & set(json.loads(r["tags"])):
                scored[r["id"]] *= 0.7
    return chosen


def next_state(box: int, fails: int, first_try: bool, now: int) -> tuple[int, int, int]:
    """Leitner step: (box, due, fails) after an attempt. Shared by puzzles and repertoire drills."""
    if first_try:
        box = min(box + 1, MAX_BOX)
        return box, now + BOX_HOURS[box] * 3600, fails
    return max(0, box - 2), now, fails + 1     # wrong: comes back at the next warm-up


def record(con: sqlite3.Connection, puzzle_id: int, first_try: bool, attempts: int,
           ms: int | None, now: int | None = None) -> dict:
    now = now or int(time.time())
    st = con.execute("SELECT * FROM puzzle_state WHERE puzzle_id=?", (puzzle_id,)).fetchone()
    if st is None:
        raise KeyError(puzzle_id)
    box, due, fails = next_state(st["box"], st["fails"], first_try, now)
    con.execute("UPDATE puzzle_state SET box=?, due=?, seen=seen+1, fails=?, last_seen=? WHERE puzzle_id=?",
                (box, due, fails, now, puzzle_id))
    con.execute("INSERT INTO attempts(puzzle_id,ts,first_try,attempts,ms) VALUES(?,?,?,?,?)",
                (puzzle_id, now, int(first_try), attempts, ms))
    con.commit()
    return {"box": box, "due": due}


def counts(con: sqlite3.Connection, now: int | None = None) -> dict:
    now = now or int(time.time())
    g = con.execute("SELECT COUNT(*) c, SUM(analyzed) a FROM games").fetchone()
    p = con.execute("SELECT COUNT(*) c, SUM(CASE WHEN due<=? THEN 1 ELSE 0 END) d FROM puzzle_state",
                    (now,)).fetchone()
    return {"games": g["c"] or 0, "analyzed": g["a"] or 0, "puzzles": p["c"] or 0, "due": p["d"] or 0}
