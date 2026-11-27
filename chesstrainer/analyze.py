"""Stockfish analysis of the user's moves; writes moves, puzzles and puzzle state to the DB."""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from . import classify as C
from .db import get_setting

MATE = 10000
MULTIPV_TOLERANCE = 4.0     # other moves within this many win% of the best are accepted as solutions
LINE_PLIES = 7              # stored variation: the move itself + 3 full moves

def find_engine(explicit: str | None = None, con: sqlite3.Connection | None = None) -> str | None:
    cands = [explicit, os.environ.get("STOCKFISH_PATH"), get_setting(con, "engine") if con else None,
             shutil.which("stockfish")]
    here = Path(__file__).resolve().parent.parent
    cands += [str(p) for p in sorted(here.glob("stockfish*")) + sorted((here / "engine").glob("stockfish*"))
              if p.is_file()]
    cands += ["/usr/games/stockfish", "/opt/homebrew/bin/stockfish", "/usr/local/bin/stockfish"]
    for c in cands:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _open_engine(path: str) -> chess.engine.SimpleEngine:
    eng = chess.engine.SimpleEngine.popen_uci(path)
    try:
        eng.configure({"Threads": 1, "Hash": 32})
    except Exception:
        pass
    return eng


def _white_cp(info: dict) -> int:
    return info["score"].white().score(mate_score=MATE)


def _terminal_cp(board: chess.Board) -> int:
    if board.is_checkmate():
        return -MATE if board.turn == chess.WHITE else MATE
    return 0


def puzzle_lines(eng: chess.engine.SimpleEngine, board: chess.Board, solution: chess.Move,
                 played: chess.Move, depth: int) -> dict:
    """Engine lines for one mistake: the best move with its continuation, and how the played move
    gets punished. Evaluations are centipawns from the point of view of the side to move."""
    me = board.turn
    info = eng.analyse(board, chess.engine.Limit(depth=depth), root_moves=[solution])
    line = list(info.get("pv") or [solution])[:LINE_PLIES]
    if not line or line[0] != solution:
        line = [solution]
    eval_best = info["score"].pov(me).score(mate_score=MATE)
    after = board.copy(stack=False)
    after.push(played)
    if after.is_game_over():
        reply, eval_played = [], (MATE if after.is_checkmate() else 0)
    else:
        info2 = eng.analyse(after, chess.engine.Limit(depth=depth))
        reply = list(info2.get("pv") or [])[:LINE_PLIES - 1]
        eval_played = info2["score"].pov(me).score(mate_score=MATE)
    return dict(line=line, reply=reply, eval_best=eval_best, eval_played=eval_played)


def analyze_game(job: dict) -> dict:
    """Analyse one game (runs in a worker process). Returns plain dicts for the DB.

    The engine is started and shut down per game: this keeps worker processes from hanging
    on exit, and native Stockfish starts in milliseconds.
    """
    eng = _open_engine(job["engine"])
    try:
        return _analyze_game(job, eng)
    finally:
        eng.quit()


def _analyze_game(job: dict, eng: chess.engine.SimpleEngine) -> dict:
    depth = job["depth"]
    game = chess.pgn.read_game(io.StringIO(job["pgn"]))
    user = chess.WHITE if job["color"] == "white" else chess.BLACK
    sign = 1 if user == chess.WHITE else -1
    base, inc = job["base"], job["inc"]

    nodes = list(game.mainline())
    boards = [game.board()]
    for n in nodes:
        b = boards[-1].copy(stack=False)
        b.push(n.move)
        boards.append(b)

    # evaluate every position once: white-POV cp and engine's best move
    evals: list[int] = []
    bests: list[chess.Move | None] = []
    for b in boards:
        if b.is_game_over():
            evals.append(_terminal_cp(b))
            bests.append(None)
            continue
        info = eng.analyse(b, chess.engine.Limit(depth=depth))
        evals.append(_white_cp(info))
        pv = info.get("pv") or []
        bests.append(pv[0] if pv else None)

    moves, puzzles = [], []
    prev_clock = {chess.WHITE: float(base) if base else None, chess.BLACK: float(base) if base else None}
    for i, node in enumerate(nodes):
        mover = boards[i].turn
        clk = node.clock()
        spent = None
        if clk is not None and prev_clock[mover] is not None:
            spent = max(0.0, prev_clock[mover] + inc - clk)
        if clk is not None:
            prev_clock[mover] = clk
        if mover != user:
            continue

        before, played = boards[i], node.move
        best = bests[i]
        cu_b, cu_a = sign * evals[i], sign * evals[i + 1]
        wb, wa = C.winpct(cu_b), C.winpct(cu_a)
        loss = 0.0 if (best is not None and played == best) else max(0.0, wb - wa)
        cls = C.classify_loss(loss, wb, wa)
        phase = C.phase_of(before)
        san = before.san(played)
        moves.append(dict(
            ply=i, move_no=before.fullmove_number, san=san, uci=played.uci(),
            best_uci=best.uci() if best else None, best_san=before.san(best) if best else None,
            phase=phase, clock=clk, time_spent=spent, win_before=round(wb, 1),
            win_after=round(wa, 1), loss=round(loss, 1), cls=cls))

        if cls not in ("mistake", "blunder"):
            continue

        # second pass: several candidate moves, to accept equally good alternatives
        infos = eng.analyse(before, chess.engine.Limit(depth=depth + 2), multipv=4)
        scored = []
        for inf in infos:
            if not inf.get("pv"):
                continue
            scored.append((C.winpct(sign * _white_cp(inf)), inf["pv"][0]))
        if not scored:
            continue
        top_win, solution = scored[0]
        accepted = [m for w, m in scored if top_win - w <= MULTIPV_TOLERANCE]
        if played in accepted:      # depth-noise: the played move is fine after all
            moves[-1]["cls"] = "inaccuracy"
            continue
        last_cap = None
        if i > 0 and boards[i - 1].is_capture(nodes[i - 1].move):
            last_cap = nodes[i - 1].move.to_square
        pl = puzzle_lines(eng, before, solution, played, depth + 2)
        tags = C.motif_tags(before, played, pl["line"], pl["reply"], pl["eval_best"], pl["eval_played"],
                            last_cap, phase, wb)
        ctx = C.context_tags(phase, job["color"], wb, wa, spent, clk, base)
        puzzles.append(dict(
            line=json.dumps([m.uci() for m in pl["line"]]),
            refutation=json.dumps([played.uci()] + [m.uci() for m in pl["reply"]]),
            eval_best=pl["eval_best"], eval_played=pl["eval_played"],
            ply=i, fen=before.fen(), solution=solution.uci(),
            accepted=json.dumps([m.uci() for m in accepted]),
            played_uci=played.uci(), played_san=san, solution_san=before.san(solution),
            severity=cls, loss=round(loss, 1), tags=json.dumps(tags), context=json.dumps(ctx),
            time_spent=spent, phase=phase))
    return {"game_id": job["id"], "moves": moves, "puzzles": puzzles}


def _store(con: sqlite3.Connection, res: dict, end_time: int) -> None:
    gid = res["game_id"]
    con.execute("DELETE FROM moves WHERE game_id=?", (gid,))
    for m in res["moves"]:
        con.execute(
            "INSERT INTO moves(game_id,ply,move_no,san,uci,best_uci,best_san,phase,clock,time_spent,"
            "win_before,win_after,loss,cls) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (gid, m["ply"], m["move_no"], m["san"], m["uci"], m["best_uci"], m["best_san"], m["phase"],
             m["clock"], m["time_spent"], m["win_before"], m["win_after"], m["loss"], m["cls"]))
    for p in res["puzzles"]:
        cur = con.execute(
            "INSERT OR IGNORE INTO puzzles(game_id,ply,fen,solution,accepted,played_uci,played_san,"
            "solution_san,severity,loss,tags,context,time_spent,phase,end_time,line,refutation,"
            "eval_best,eval_played) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (gid, p["ply"], p["fen"], p["solution"], p["accepted"], p["played_uci"], p["played_san"],
             p["solution_san"], p["severity"], p["loss"], p["tags"], p["context"], p["time_spent"],
             p["phase"], end_time, p["line"], p["refutation"], p["eval_best"], p["eval_played"]))
        if cur.rowcount:
            con.execute("INSERT OR IGNORE INTO puzzle_state(puzzle_id,box,due) VALUES(?,0,0)",
                        (cur.lastrowid,))
    con.execute("UPDATE games SET analyzed=1 WHERE id=?", (gid,))
    con.commit()


def run_analysis(con: sqlite3.Connection, engine_path: str, depth: int = 14, workers: int = 1,
                 limit: int | None = None, log=print, progress=None) -> int:
    """Analyse all not-yet-analysed games (newest first). Returns number of games done."""
    rows = con.execute(
        "SELECT * FROM games WHERE analyzed=0 ORDER BY end_time DESC" + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()
    if not rows:
        return 0
    jobs = [dict(id=r["id"], pgn=r["pgn"], color=r["user_color"], base=r["base_secs"] or 0,
                 inc=r["inc_secs"] or 0, engine=engine_path, depth=depth) for r in rows]
    end_times = {r["id"]: r["end_time"] for r in rows}
    done, t0 = 0, time.time()

    def finish(res):
        nonlocal done
        _store(con, res, end_times[res["game_id"]])
        done += 1
        if progress:
            progress(done, len(jobs))
        log(f"  [{done}/{len(jobs)}] {len(res['puzzles'])} Fehler-Puzzles ({time.time() - t0:.0f}s)")

    if workers <= 1:
        for j in jobs:
            finish(analyze_game(j))
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(analyze_game, j) for j in jobs]
            try:
                for f in as_completed(futs):
                    finish(f.result())
            except BaseException:                   # e.g. cancelled from the progress callback: drop queued games
                for f in futs:
                    f.cancel()
                raise
    return done


# ---------------------------------------------------------------------------------------------
# refresh: add variations/evaluations to older puzzles and re-run the mistake-type detection
# ---------------------------------------------------------------------------------------------
def _lines_chunk(job: dict) -> list[dict]:
    eng = _open_engine(job["engine"])
    out = []
    try:
        for p in job["puzzles"]:
            board = chess.Board(p["fen"])
            pl = puzzle_lines(eng, board, chess.Move.from_uci(p["solution"]),
                              chess.Move.from_uci(p["played"]), job["depth"])
            out.append(dict(id=p["id"], line=[m.uci() for m in pl["line"]],
                            refutation=[p["played"]] + [m.uci() for m in pl["reply"]],
                            eval_best=pl["eval_best"], eval_played=pl["eval_played"]))
    finally:
        eng.quit()
    return out


def lines_for_puzzle(con: sqlite3.Connection, puzzle_id: int, engine_path: str, depth: int = 14) -> None:
    """Compute and store the variations for a single puzzle (used on demand by the web UI)."""
    p = con.execute("SELECT * FROM puzzles WHERE id=?", (puzzle_id,)).fetchone()
    res = _lines_chunk(dict(engine=engine_path, depth=depth, puzzles=[
        dict(id=p["id"], fen=p["fen"], solution=p["solution"], played=p["played_uci"])]))[0]
    con.execute("UPDATE puzzles SET line=?, refutation=?, eval_best=?, eval_played=? WHERE id=?",
                (json.dumps(res["line"]), json.dumps(res["refutation"]), res["eval_best"],
                 res["eval_played"], puzzle_id))
    con.commit()


def missing_lines(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) c FROM puzzles WHERE line IS NULL").fetchone()["c"]


def reclassify(con: sqlite3.Connection) -> int:
    """Re-run the (engine-free) mistake-type detection for all puzzles that have variations."""
    rows = con.execute(
        "SELECT p.*, g.pgn, m.win_before FROM puzzles p JOIN games g ON g.id=p.game_id "
        "LEFT JOIN moves m ON m.game_id=p.game_id AND m.ply=p.ply WHERE p.line IS NOT NULL "
        "ORDER BY p.game_id, p.ply").fetchall()
    last_caps: dict[str, dict[int, int | None]] = {}
    n = 0
    for r in rows:
        gid = r["game_id"]
        if gid not in last_caps:                      # which square was captured on just before each ply
            caps, game = {}, chess.pgn.read_game(io.StringIO(r["pgn"]))
            board, prev = game.board(), None
            for ply, mv in enumerate(game.mainline_moves()):
                caps[ply] = prev
                prev = mv.to_square if board.is_capture(mv) else None
                board.push(mv)
            last_caps = {gid: caps}
        board = chess.Board(r["fen"])
        line = [chess.Move.from_uci(u) for u in json.loads(r["line"])]
        ref = [chess.Move.from_uci(u) for u in json.loads(r["refutation"])]
        tags = C.motif_tags(board, ref[0], line, ref[1:], r["eval_best"], r["eval_played"],
                            last_caps[gid].get(r["ply"]), r["phase"], r["win_before"])
        if json.dumps(tags) != r["tags"]:
            con.execute("UPDATE puzzles SET tags=? WHERE id=?", (json.dumps(tags), r["id"]))
            n += 1
    con.commit()
    return n


def refresh(con: sqlite3.Connection, engine_path: str, depth: int = 14, workers: int = 1,
            log=print, progress=None, force: bool = False) -> int:
    """Fill in variations for puzzles that lack them (all with force=True), then reclassify."""
    rows = con.execute("SELECT id, fen, solution, played_uci FROM puzzles"
                       + ("" if force else " WHERE line IS NULL")).fetchall()
    todo = [dict(id=r["id"], fen=r["fen"], solution=r["solution"], played=r["played_uci"]) for r in rows]
    done = 0
    if todo:
        size = 8
        jobs = [dict(engine=engine_path, depth=depth, puzzles=todo[i:i + size]) for i in range(0, len(todo), size)]

        def store(results):
            nonlocal done
            for res in results:
                con.execute("UPDATE puzzles SET line=?, refutation=?, eval_best=?, eval_played=? WHERE id=?",
                            (json.dumps(res["line"]), json.dumps(res["refutation"]), res["eval_best"],
                             res["eval_played"], res["id"]))
            con.commit()
            done += len(results)
            if progress:
                progress(done, len(todo))
            log(f"  Varianten {done}/{len(todo)}")

        if workers <= 1:
            for j in jobs:
                store(_lines_chunk(j))
        else:
            with ProcessPoolExecutor(max_workers=workers) as ex:
                for f in as_completed([ex.submit(_lines_chunk, j) for j in jobs]):
                    store(f.result())
    changed = reclassify(con)
    log(f"  Fehlertypen neu bestimmt: {changed} geändert")
    return done
