"""Progress: are you getting better, and do the same mistakes come back?

Compares your most recent games with the block of games before them."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

from . import classify as C, openings
from .i18n import tr

MAX_WINDOW = 25         # compare the last N games with the N before (fewer if you have fewer games)
MIN_GAMES = 10
SAME = 0.10             # relative change below this counts as "unchanged"


def _verdict(prev: float | None, cur: float | None, lower_is_better: bool, min_abs: float = 0.0) -> str:
    if prev is None or cur is None:
        return "na"
    diff = cur - prev
    if abs(diff) < min_abs or abs(diff) <= SAME * max(abs(prev), 1e-9):
        return "same"
    return "better" if (diff < 0) == lower_is_better else "worse"


def _window_stats(games: list[dict]) -> dict:
    n = len(games)
    moves = sum(g["moves"] for g in games)
    bad = sum(g["bad"] for g in games)
    exits = [g["exit_win"] for g in games if g["exit_win"] is not None]
    ratings = [g["rating"] for g in games if g["rating"]]
    timed = sum(g["bad_timed"] for g in games)
    return dict(
        games=n,
        err100=100 * bad / moves if moves else None,
        blunders=sum(g["blunders"] for g in games) / n if n else None,
        avg_loss=sum(g["loss"] for g in games) / moves if moves else None,
        score=100 * sum(1 if g["result"] == "win" else .5 if g["result"] == "draw" else 0 for g in games) / n if n else None,
        rating=sum(ratings) / len(ratings) if ratings else None,
        exit_win=sum(exits) / len(exits) if exits else None,
        fast_share=100 * sum(g["bad_fast"] for g in games) / timed if timed else None)


METRICS = [  # key, label, lower_is_better, format, explanation
    ("err100", "Fehler pro 100 Züge", True, "{:.1f}", "Fehler und Patzer je 100 eigene Züge"),
    ("blunders", "Patzer pro Partie", True, "{:.1f}", "Züge mit ≥ 20 Prozentpunkten Verlust"),
    ("avg_loss", "Ø Verlust pro Zug", True, "{:.1f} %", "Durchschnittlich verlorene Gewinnchance pro Zug"),
    ("exit_win", "Stellung nach Zug 10", False, "{:.0f} %", "Ø Gewinnchance nach der Eröffnung"),
    ("fast_share", "Fehler nach Schnellzug", True, "{:.0f} %", "Anteil deiner Fehler, die nach ≤ 3 s Bedenkzeit passieren"),
    ("score", "Punkteausbeute", False, "{:.0f} %", "Sieg 1, Remis ½"),
    ("rating", "Ø Wertung", False, "{:.0f}", "Durchschnittliche Chess.com-Wertung im Zeitraum"),
]


def build(con: sqlite3.Connection) -> dict:
    rows = con.execute("SELECT id,end_time,result,user_rating,time_class FROM games WHERE analyzed=1 "
                       "ORDER BY end_time").fetchall()
    out: dict = {"games": len(rows), "enough": len(rows) >= MIN_GAMES, "min_games": MIN_GAMES}
    if not rows:
        return out
    games = {r["id"]: dict(id=r["id"], end_time=r["end_time"] or 0, result=r["result"], rating=r["user_rating"],
                           time_class=r["time_class"], moves=0, bad=0, blunders=0, loss=0.0, exit_win=None,
                           bad_fast=0, bad_timed=0, tags=Counter(), ctx=Counter()) for r in rows}
    for m in con.execute("SELECT game_id,move_no,cls,loss,win_after,time_spent FROM moves ORDER BY game_id, ply"):
        g = games.get(m["game_id"])
        if g is None:
            continue
        g["moves"] += 1
        g["loss"] += m["loss"] or 0
        if m["cls"] in ("mistake", "blunder"):
            g["bad"] += 1
            g["blunders"] += m["cls"] == "blunder"
            if m["time_spent"] is not None:
                g["bad_timed"] += 1
                g["bad_fast"] += m["time_spent"] <= C.FAST_MOVE_SECS
        if openings.OPENING_MOVES - 2 <= m["move_no"] <= openings.OPENING_MOVES:
            g["exit_win"] = m["win_after"]
    for p in con.execute("SELECT game_id,tags,context FROM puzzles"):
        g = games.get(p["game_id"])
        if g is not None:
            g["tags"].update(json.loads(p["tags"]))
            g["ctx"].update(json.loads(p["context"]))
    order = [g for g in games.values() if g["moves"]]
    w = min(MAX_WINDOW, len(order) // 2)
    out["window"] = w
    if w < 3:
        out["enough"] = False
        return out
    prev, recent = order[-2 * w:-w], order[-w:]
    sp, sr = _window_stats(prev), _window_stats(recent)
    out["span"] = dict(prev_from=prev[0]["end_time"], prev_to=prev[-1]["end_time"],
                       recent_from=recent[0]["end_time"], recent_to=recent[-1]["end_time"])

    out["metrics"] = []
    for key, label, lower, fmt, tip in METRICS:
        a, b = sp[key], sr[key]
        if a is None or b is None:
            continue
        rel = (b - a) / abs(a) * 100 if a else 0.0
        out["metrics"].append(dict(key=key, label=tr(label), tip=tr(tip), prev=fmt.format(a), recent=fmt.format(b),
                                   rel=rel, verdict=_verdict(a, b, lower)))

    # ---- mistake types: how often per 10 games, before vs. now ----------------------------
    def per10(window, counter_key, tag):
        return 10 * sum(g[counter_key][tag] for g in window) / len(window)

    def trend_rows(counter_key, titles):
        res = []
        for tag, title in titles:
            total = sum(g[counter_key][tag] for g in prev + recent)
            if total < 4:
                continue
            a, b = per10(prev, counter_key, tag), per10(recent, counter_key, tag)
            res.append(dict(tag=tag, title=title, prev=a, recent=b, total=total,
                            verdict=_verdict(a, b, True, min_abs=0.8)))
        res.sort(key=lambda d: -(d["recent"] + d["prev"]))
        return res

    out["motifs"] = trend_rows("tags", [(t, v[0]) for t, v in C.MOTIFS.items()])
    out["situations"] = trend_rows("ctx", [("fast_move", "Zu schnell gezogen"), ("time_trouble", "In Zeitnot"),
                                           ("threw_win", "Vorteil verspielt")])
    out["trend_max"] = max([d["prev"] for d in out["motifs"] + out["situations"]]
                           + [d["recent"] for d in out["motifs"] + out["situations"]] + [1])

    # ---- series -----------------------------------------------------------------------------
    blocks = []
    for i in range(0, len(order), 10):
        chunk = order[i:i + 10]
        if len(chunk) < 5 and blocks:
            break
        st = _window_stats(chunk)
        blocks.append(dict(label=tr("Partien {a}–{b}", a=i + 1, b=i + len(chunk)), rate=st["err100"] or 0, games=len(chunk)))
    out["blocks"] = blocks[-14:]
    out["rating"] = _rating_series(order, w)

    # ---- same mistake again? recurring opening positions ------------------------------------
    cutoff = recent[0]["end_time"]
    op = openings.build(con)
    repeats = []
    for color, label in (("white", tr("Weiß")), ("black", tr("Schwarz"))):
        for lk in op[color]["all_leaks"]:
            bad = [e for e in lk["events"] if e["loss"] >= 5]
            now = [e for e in bad if e["end_time"] >= cutoff]
            reached_now = [e for e in lk["events"] if e["end_time"] >= cutoff]
            if len(bad) < 2:
                continue
            status = "again" if now else ("fixed" if reached_now else "open")
            repeats.append(dict(color=label, family=lk["family"], move_no=lk["move_no"], fen=lk["fen"],
                                played=lk["played"], best_san=lk["best_san"], best_uci=lk["best_uci"],
                                n=lk["n"], last_seen=lk["last_seen"], link=lk["link"], game=lk["game"], ply=lk["ply"],
                                bad_total=len(bad), bad_recent=len(now), reached_recent=len(reached_now),
                                status=status))
    repeats.sort(key=lambda d: ({"again": 0, "open": 1, "fixed": 2}[d["status"]], -d["bad_total"]))
    out["repeats"] = repeats[:9]

    out["training"] = _training(con)
    out["insights"] = _insights(out)
    return out


def _rating_series(order: list[dict], w: int) -> dict | None:
    """Rating after each game for the most played time class, scaled to a 1000 x 220 SVG."""
    classes = Counter(g["time_class"] for g in order if g["rating"])
    if not classes:
        return None
    tc = classes.most_common(1)[0][0]
    pts = [g for g in order if g["rating"] and g["time_class"] == tc]
    if len(pts) < 5:
        return None
    lo, hi = min(g["rating"] for g in pts), max(g["rating"] for g in pts)
    pad = max(10, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad
    W, H = 1000, 220
    xs = [W * i / (len(pts) - 1) for i in range(len(pts))]
    ys = [H - H * (g["rating"] - lo) / (hi - lo) for g in pts]
    step = max(10, round((hi - lo) / 4 / 10) * 10)
    ticks = [dict(v=v, y=H - H * (v - lo) / (hi - lo)) for v in range(int(lo // step + 1) * step, int(hi) + 1, step)]
    recent_ids = {g["id"] for g in order[-w:]}
    first_recent = next((i for i, g in enumerate(pts) if g["id"] in recent_ids), len(pts) - 1)
    return dict(time_class=tc, path=" ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)),
                points=[dict(x=x, y=y, rating=g["rating"], end_time=g["end_time"], n=i + 1)
                        for i, (x, y, g) in enumerate(zip(xs, ys, pts))],
                ticks=ticks, first=pts[0]["rating"], last=pts[-1]["rating"], best=max(g["rating"] for g in pts),
                recent_x=xs[first_recent], width=W, height=H)


def _training(con: sqlite3.Connection) -> dict:
    att = con.execute("SELECT puzzle_id, first_try, ts FROM attempts ORDER BY ts").fetchall()
    seen: dict[int, int] = defaultdict(int)
    first = [0, 0]
    again = [0, 0]
    for a in att:
        bucket = again if seen[a["puzzle_id"]] else first
        bucket[0] += 1
        bucket[1] += a["first_try"]
        seen[a["puzzle_id"]] += 1
    boxes = Counter(r["box"] for r in con.execute("SELECT box FROM puzzle_state"))
    total = sum(boxes.values())
    stubborn = con.execute("SELECT COUNT(*) c FROM puzzle_state WHERE fails>=2").fetchone()["c"]
    return dict(attempts=len(att), first_n=first[0], first_ok=first[1], again_n=again[0], again_ok=again[1],
                trained=len(seen), total=total, learned=sum(v for b, v in boxes.items() if b >= 2),
                stubborn=stubborn)


def _insights(o: dict) -> list[str]:
    ins = []
    m = {x["key"]: x for x in o["metrics"]}
    w = o["window"]
    words = {"better": tr("besser"), "worse": tr("schlechter"), "same": tr("praktisch unverändert")}
    if "err100" in m:
        e = m["err100"]
        ins.append(tr("Fehlerquote: {prev} → {recent} pro 100 Züge in den letzten {w} Partien gegenüber den {w} davor ({word}).",
                      prev=e["prev"], recent=e["recent"], w=w, word=words[e["verdict"]]))
    if "rating" in m and o.get("rating"):
        r = o["rating"]
        ins.append(tr("Wertung ({tc}): {first} → {last} über {n} Partien, Höchststand {best}.",
                      tc=r["time_class"], first=r["first"], last=r["last"], n=len(r["points"]), best=r["best"]))
    down = [d for d in o["motifs"] if d["verdict"] == "better"]
    up = [d for d in o["motifs"] if d["verdict"] == "worse"]
    if down:
        d = max(down, key=lambda d: d["prev"] - d["recent"])
        ins.append(tr("Seltener geworden: {title} ({prev} → {recent} pro 10 Partien).",
                      title=d["title"], prev=f"{d['prev']:.1f}", recent=f"{d['recent']:.1f}"))
    if up:
        d = max(up, key=lambda d: d["recent"] - d["prev"])
        ins.append(tr("Häufiger geworden: {title} ({prev} → {recent} pro 10 Partien).",
                      title=d["title"], prev=f"{d['prev']:.1f}", recent=f"{d['recent']:.1f}"))
    again = [r for r in o["repeats"] if r["status"] == "again"]
    if again:
        ins.append(tr("1 Eröffnungsstellung hast du in den letzten {w} Partien erneut falsch behandelt.", w=w) if len(again) == 1
                   else tr("{n} Eröffnungsstellungen hast du in den letzten {w} Partien erneut falsch behandelt.", n=len(again), w=w))
    t = o["training"]
    if t["again_n"] >= 5:
        ins.append(tr("Wiederholte Puzzles löst du zu {a} % im ersten Versuch (beim ersten Mal: {b} %).",
                      a=f"{100 * t['again_ok'] / t['again_n']:.0f}", b=f"{100 * t['first_ok'] / max(1, t['first_n']):.0f}"))
    return ins
