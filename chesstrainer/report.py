"""Aggregate the analysed moves into a mistake-pattern report."""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter, defaultdict

from . import classify as C, openings
from .i18n import tr

MIN_BUCKET = 30      # don't draw conclusions from fewer moves than this


def _rate(bad: int, total: int) -> float:
    return 100.0 * bad / total if total else 0.0


def build(con: sqlite3.Connection) -> dict:
    games = con.execute("SELECT * FROM games WHERE analyzed=1 ORDER BY end_time").fetchall()
    gmap = {g["id"]: g for g in games}
    mv = con.execute("SELECT m.*, g.base_secs, g.user_color, g.end_time FROM moves m "
                     "JOIN games g ON g.id=m.game_id ORDER BY g.end_time, m.ply").fetchall()
    out: dict = {"games": len(games), "moves": len(mv)}
    if not mv:
        return out
    bad = [m for m in mv if m["cls"] in ("mistake", "blunder")]
    out["mistakes"] = sum(1 for m in bad if m["cls"] == "mistake")
    out["blunders"] = sum(1 for m in bad if m["cls"] == "blunder")
    out["rate"] = _rate(len(bad), len(mv))
    out["per_game"] = len(bad) / len(games)

    # --- motif tags --------------------------------------------------------------------
    tags: Counter = Counter()
    for r in con.execute("SELECT tags FROM puzzles"):
        for t in json.loads(r["tags"]):
            tags[t] += 1
    total_tags = sum(tags.values()) or 1
    out["motifs"] = [dict(tag=t, title=C.MOTIFS[t][0], hint=C.MOTIFS[t][1], count=c,
                          share=100 * c / total_tags) for t, c in tags.most_common()]

    # --- rate per context ----------------------------------------------------------------
    def bucket_stats(keyfn) -> list[dict]:
        tot, b = Counter(), Counter()
        for m in mv:
            k = keyfn(m)
            if k is None:
                continue
            tot[k] += 1
            if m["cls"] in ("mistake", "blunder"):
                b[k] += 1
        return [dict(key=k, n=tot[k], bad=b[k], rate=_rate(b[k], tot[k])) for k in tot]

    phase_order = {"opening": 0, "middlegame": 1, "endgame": 2}
    out["phase"] = sorted(bucket_stats(lambda m: m["phase"]), key=lambda d: phase_order.get(d["key"], 9))
    for d in out["phase"]:
        d["label"] = C.CONTEXTS.get(d["key"], d["key"])
    out["color"] = [dict(d, label=C.CONTEXTS[d["key"]]) for d in
                    sorted(bucket_stats(lambda m: m["user_color"]), key=lambda d: d["key"] != "white")]

    def timekey(m):
        if m["clock"] is not None and m["base_secs"] and m["clock"] <= max(20.0, 0.1 * m["base_secs"]):
            return "time_trouble"
        if m["time_spent"] is not None and m["time_spent"] <= C.FAST_MOVE_SECS:
            return "fast_move"
        if m["time_spent"] is None:
            return None
        return "normal"
    labels = {"time_trouble": tr("Zeitnot (<10 % der Zeit)"), "fast_move": tr("Schneller Zug (≤{s} s)", s=f"{C.FAST_MOVE_SECS:.0f}"),
              "normal": tr("Normales Tempo")}
    order = {"normal": 0, "fast_move": 1, "time_trouble": 2}
    out["tempo"] = sorted((dict(d, label=labels[d["key"]]) for d in bucket_stats(timekey)),
                          key=lambda d: order[d["key"]])

    def posture(m):
        w = m["win_before"]
        return "winning" if w >= 65 else ("worse" if w <= 35 else "equal")
    pos_order = {"winning": 0, "equal": 1, "worse": 2}
    out["position"] = sorted((dict(d, label=C.CONTEXTS[d["key"]]) for d in bucket_stats(posture)),
                             key=lambda d: pos_order[d["key"]])

    # --- time spent --------------------------------------------------------------------
    t_bad = [m["time_spent"] for m in bad if m["time_spent"] is not None]
    t_all = [m["time_spent"] for m in mv if m["time_spent"] is not None]
    if len(t_bad) >= 10 and len(t_all) >= 50:
        out["time"] = dict(median_bad=statistics.median(t_bad), median_all=statistics.median(t_all))

    # --- trend: blocks of 10 games, oldest -> newest -----------------------------------
    per_game: dict[str, list] = defaultdict(lambda: [0, 0])
    for m in mv:
        per_game[m["game_id"]][0] += 1
        per_game[m["game_id"]][1] += m["cls"] in ("mistake", "blunder")
    order_ids = [g["id"] for g in games if g["id"] in per_game]
    blocks = []
    for i in range(0, len(order_ids), 10):
        ids = order_ids[i:i + 10]
        if len(ids) < 5 and blocks:
            break
        n = sum(per_game[x][0] for x in ids)
        b = sum(per_game[x][1] for x in ids)
        blocks.append(dict(label=f"Partien {i + 1}–{i + len(ids)}", rate=_rate(b, n), games=len(ids)))
    out["trend"] = blocks[-12:]

    out["insights"] = _insights(out)
    out["training"] = _training(con)
    out["examples"] = _examples(con, [m["tag"] for m in out["motifs"]])
    return out


EXAMPLES_PER_GROUP = 12
_SITUATIONS = [("fast_move", "Zu schnell gezogen"), ("time_trouble", "In Zeitnot"),
               ("threw_win", "Vorteil verspielt")]


def example_card(p: sqlite3.Row, meta: dict) -> dict:
    """One example position for the UI (mini board with the played and the best move)."""
    return dict(
        fen=p["fen"], played_uci=p["played_uci"], played_san=p["played_san"],
        best_uci=p["solution"], best_san=p["solution_san"], loss=p["loss"], severity=p["severity"],
        eval_best=p["eval_best"], eval_played=p["eval_played"],
        time_spent=p["time_spent"], end_time=p["end_time"], move_no=int(p["fen"].split()[-1]),
        opponent=meta["opponent"], link=meta["link"], opening=meta["family"], game=meta["game"], ply=p["ply"],
        motifs=[C.MOTIFS[t][0] for t in json.loads(p["tags"]) if t in C.MOTIFS],
        context=[C.CONTEXTS[c] for c in json.loads(p["context"]) if c in C.CONTEXTS])


def _examples(con: sqlite3.Connection, motif_order: list[str]) -> dict:
    """Newest example positions per mistake type and per situation."""
    rows = con.execute("SELECT p.*, g.pgn, g.opponent, g.rowid AS n FROM puzzles p JOIN games g ON g.id=p.game_id "
                       "ORDER BY p.end_time DESC, p.ply").fetchall()
    by_tag: dict[str, list] = defaultdict(list)
    by_ctx: dict[str, list] = defaultdict(list)
    counts: Counter = Counter()
    for r in rows:
        card = None
        for kind, keys, store in (("m", json.loads(r["tags"]), by_tag), ("c", json.loads(r["context"]), by_ctx)):
            for k in keys:
                counts[(kind, k)] += 1
                if len(store[k]) < EXAMPLES_PER_GROUP:
                    card = card or example_card(r, openings.game_meta(r))
                    store[k].append(card)
    motifs = [dict(key=t, title=C.MOTIFS[t][0], hint=C.MOTIFS[t][1], total=counts[("m", t)], cards=by_tag[t])
              for t in motif_order if by_tag.get(t)]
    situations = [dict(key=k, title=title, hint="", total=counts[("c", k)], cards=by_ctx[k])
                  for k, title in ((k, tr(t)) for k, t in _SITUATIONS) if by_ctx.get(k)]
    return {"motifs": motifs, "situations": situations}


def _insights(r: dict) -> list[str]:
    ins: list[str] = []
    for m in r["motifs"][:3]:
        ins.append(tr("{title}: {n}× ({share} % deiner Fehler). {hint}", title=m["title"], n=m["count"],
                      share=f"{m['share']:.0f}", hint=m["hint"]))
    base = r["rate"]
    for d in r["tempo"]:
        if d["key"] != "normal" and d["n"] >= MIN_BUCKET and base > 0:
            normal = next((x for x in r["tempo"] if x["key"] == "normal"), None)
            if normal and normal["n"] >= MIN_BUCKET and normal["rate"] > 0 and d["rate"] >= 1.4 * normal["rate"]:
                ins.append(tr("{label}: {rate} Fehler pro 100 Züge gegenüber {base} bei normalem Tempo ({x}×).",
                              label=d["label"], rate=f"{d['rate']:.1f}", base=f"{normal['rate']:.1f}",
                              x=f"{d['rate'] / normal['rate']:.1f}"))
    phases = [d for d in r["phase"] if d["n"] >= MIN_BUCKET]
    if len(phases) >= 2:
        worst = max(phases, key=lambda d: d["rate"])
        ins.append(tr("Schwächste Phase: {label} mit {rate} Fehlern pro 100 Zügen.", label=worst["label"], rate=f"{worst['rate']:.1f}"))
    pos = {d["key"]: d for d in r["position"] if d["n"] >= MIN_BUCKET}
    if "winning" in pos and pos["winning"]["rate"] >= 1.2 * r["rate"]:
        ins.append(tr("Du machst in besseren Stellungen überdurchschnittlich viele Fehler: Vorteil verwalten, nicht beschleunigen."))
    return ins


def _training(con: sqlite3.Connection) -> dict:
    row = con.execute(
        "SELECT COUNT(*) n, SUM(first_try) ok, COUNT(DISTINCT puzzle_id) p FROM attempts").fetchone()
    solved = con.execute("SELECT COUNT(*) c FROM puzzle_state WHERE box>=2").fetchone()["c"]
    total = con.execute("SELECT COUNT(*) c FROM puzzle_state").fetchone()["c"]
    return dict(attempts=row["n"] or 0, first_try=row["ok"] or 0, learned=solved, total=total)
