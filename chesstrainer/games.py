"""Single games: list, move-by-move review data, and export of the mistake library."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone

import chess
import chess.pgn

from . import classify as C, explain, openings
from .i18n import tr


def _day(ts) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts else ""


def list_games(con: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    rows = con.execute(
        "SELECT g.rowid AS n, g.*, "
        "(SELECT COUNT(*) FROM moves m WHERE m.game_id=g.id AND m.cls='mistake') AS mistakes, "
        "(SELECT COUNT(*) FROM moves m WHERE m.game_id=g.id AND m.cls='blunder') AS blunders, "
        "(SELECT MAX(loss) FROM moves m WHERE m.game_id=g.id AND m.cls IN ('mistake','blunder')) AS worst, "
        "(SELECT COUNT(*) FROM moves m WHERE m.game_id=g.id) AS n_moves "
        "FROM games g WHERE g.analyzed=1 ORDER BY g.end_time DESC" + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()
    out = []
    for r in rows:
        meta = openings.game_meta(r)
        out.append(dict(n=r["n"], end_time=r["end_time"], color=r["user_color"], result=r["result"],
                        opponent=r["opponent"], opp_rating=r["opp_rating"], rating=r["user_rating"],
                        time_class=r["time_class"], opening=meta["family"], mistakes=r["mistakes"],
                        blunders=r["blunders"], worst=r["worst"], moves=r["n_moves"]))
    return out


def _steps(fen: str, ucis: list[str]) -> list[dict]:
    board, out = chess.Board(fen), []
    for u in ucis:
        try:
            m = chess.Move.from_uci(u)
        except ValueError:
            break
        if m not in board.legal_moves:
            break
        label = f"{board.fullmove_number}{'.' if board.turn else '…'} {board.san(m)}"
        board.push(m)
        out.append(dict(uci=u, label=label, fen=board.fen()))
    return out


def _reasons(fen: str, played: chess.Move, p) -> dict:
    board = chess.Board(fen)
    mv = lambda ucis: [chess.Move.from_uci(u) for u in ucis]
    best = mv(json.loads(p["line"]) if p["line"] else [p["solution"]])
    reply = mv(json.loads(p["refutation"])[1:]) if p["refutation"] else []
    return dict(why_played=explain.punishment(board, played, reply), why_best=explain.merit(board, best))


def detail(con: sqlite3.Connection, n: int) -> dict | None:
    """Everything the review page needs for one game."""
    g = con.execute("SELECT rowid AS n, * FROM games WHERE rowid=?", (n,)).fetchone()
    if g is None:
        return None
    game = chess.pgn.read_game(io.StringIO(g["pgn"]))
    if game is None:
        return None
    mv = {m["ply"]: m for m in con.execute("SELECT * FROM moves WHERE game_id=?", (g["id"],))}
    pz = {p["ply"]: p for p in con.execute("SELECT * FROM puzzles WHERE game_id=?", (g["id"],))}
    board = game.board()
    start = board.fen()
    plies, mistakes = [], []
    for ply, move in enumerate(game.mainline_moves()):
        fen_before = board.fen()
        san = board.san(move)
        m = mv.get(ply)
        entry = dict(ply=ply, no=board.fullmove_number, white=board.turn, san=san, uci=move.uci(),
                     mine=m is not None, cls=m["cls"] if m else None, loss=m["loss"] if m else None,
                     win=m["win_after"] if m else None, spent=m["time_spent"] if m else None)
        board.push(move)
        entry["fen"] = board.fen()
        plies.append(entry)
        p = pz.get(ply)
        if p is not None:
            tags = [t for t in json.loads(p["tags"]) if t in C.MOTIFS]
            mistakes.append(dict(
                ply=ply, no=entry["no"], san=san, uci=move.uci(), fen=fen_before, severity=p["severity"],
                loss=p["loss"], best_san=p["solution_san"], best_uci=p["solution"],
                eval_best=p["eval_best"], eval_played=p["eval_played"], spent=p["time_spent"],
                motifs=[dict(title=C.MOTIFS[t][0], hint=C.MOTIFS[t][1]) for t in tags],
                context=[C.CONTEXTS[c] for c in json.loads(p["context"]) if c in C.CONTEXTS],
                best_line=_steps(fen_before, json.loads(p["line"]) if p["line"] else [p["solution"]]),
                your_line=_steps(fen_before, json.loads(p["refutation"]) if p["refutation"] else [move.uci()]),
                **_reasons(fen_before, move, p)))
    meta = openings.game_meta(g)
    older = con.execute("SELECT rowid AS n FROM games WHERE analyzed=1 AND end_time<? ORDER BY end_time DESC LIMIT 1",
                        (g["end_time"],)).fetchone()
    newer = con.execute("SELECT rowid AS n FROM games WHERE analyzed=1 AND end_time>? ORDER BY end_time LIMIT 1",
                        (g["end_time"],)).fetchone()
    return dict(n=g["n"], start=start, color=g["user_color"], result=g["result"], opponent=g["opponent"],
                opp_rating=g["opp_rating"], rating=g["user_rating"], end_time=g["end_time"],
                time_class=g["time_class"], opening=meta["name"], link=meta["link"], analyzed=bool(g["analyzed"]),
                plies=plies, mistakes=mistakes, older=older["n"] if older else None,
                newer=newer["n"] if newer else None)


# ---------------------------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------------------------
def _library(con: sqlite3.Connection):
    return con.execute("SELECT p.*, g.pgn, g.opponent, g.user_color FROM puzzles p JOIN games g ON g.id=p.game_id "
                       "ORDER BY p.end_time DESC, p.ply").fetchall()


def export_csv(con: sqlite3.Connection) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["datum", "gegner", "farbe", "eroeffnung", "zug", "gespielt", "besser", "verlust_gewinnchance",
                "bewertung_besser", "bewertung_gespielt", "fehlertypen", "situation", "bedenkzeit_s", "fen", "partie"])
    for p in _library(con):
        meta = openings.game_meta(p)
        ev = lambda v: "" if v is None else f"{v / 100:.2f}"
        w.writerow([_day(p["end_time"]), p["opponent"], p["user_color"], meta["family"], p["fen"].split()[-1],
                    p["played_san"], p["solution_san"], p["loss"], ev(p["eval_best"]), ev(p["eval_played"]),
                    "; ".join(C.MOTIFS[t][0] for t in json.loads(p["tags"]) if t in C.MOTIFS),
                    "; ".join(C.CONTEXTS[c] for c in json.loads(p["context"]) if c in C.CONTEXTS),
                    "" if p["time_spent"] is None else round(p["time_spent"], 1), p["fen"], meta["link"]])
    return buf.getvalue()


def export_pgn(con: sqlite3.Connection) -> str:
    """Every mistake as a small PGN: the position, the best line, and your move as a variation."""
    out = []
    for p in _library(con):
        meta = openings.game_meta(p)
        board = chess.Board(p["fen"])
        game = chess.pgn.Game()
        game.setup(board)
        game.headers.update({
            "Event": "; ".join(C.MOTIFS[t][0] for t in json.loads(p["tags"]) if t in C.MOTIFS) or tr("Fehler"),
            "Site": meta["link"] or "?", "Date": _day(p["end_time"]).replace("-", ".") or "????.??.??",
            "White": "Du" if p["user_color"] == "white" else (p["opponent"] or "?"),
            "Black": "Du" if p["user_color"] == "black" else (p["opponent"] or "?"), "Result": "*",
            "Opening": meta["name"]})
        node = game
        for u in (json.loads(p["line"]) if p["line"] else [p["solution"]]):
            m = chess.Move.from_uci(u)
            if m not in node.board().legal_moves:
                break
            node = node.add_variation(m)
        if game.variations:
            game.variations[0].comment = f"besser; kostet sonst {p['loss']:.0f} Prozentpunkte Gewinnchance"
        var = game
        for i, u in enumerate(json.loads(p["refutation"]) if p["refutation"] else [p["played_uci"]]):
            m = chess.Move.from_uci(u)
            if m not in var.board().legal_moves:
                break
            var = var.add_variation(m)
            if i == 0:
                var.comment = tr("in der Partie gespielt")
        out.append(str(game))
    return "\n\n".join(out) + "\n"
