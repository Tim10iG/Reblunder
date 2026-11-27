"""Judging a move while a puzzle is being solved: one shared Stockfish process, started on first use.

Used for moves that are not in the stored list of accepted moves: is the move about as good after all
(then it counts as solved), and if not, how does the opponent punish it (shown to the player)?
"""
from __future__ import annotations

import threading

import chess
import chess.engine

from . import classify as C

TOLERANCE = 4.0          # win% behind the best move that still counts as correct (same as the analysis)
CLEARLY_WON = C.ALREADY_WON   # a move that keeps this win% is never a mistake by the app's own rules
DEPTH = 14
MAX_SECS = 2.0


def accept_threshold(eval_best_cp: int | None) -> float | None:
    """Minimum win% (mover's view) a move must keep to count as correct."""
    if eval_best_cp is None:
        return None
    return min(C.winpct(eval_best_cp) - TOLERANCE, CLEARLY_WON)


class Judge:
    def __init__(self):
        self._lock = threading.Lock()
        self._eng: chess.engine.SimpleEngine | None = None
        self._path: str | None = None

    def _engine(self, path: str) -> chess.engine.SimpleEngine:
        if self._eng is None or self._path != path:
            self.close()
            self._eng = chess.engine.SimpleEngine.popen_uci(path)
            try:
                self._eng.configure({"Threads": 1, "Hash": 16})
            except Exception:
                pass
            self._path = path
        return self._eng

    def close(self):
        if self._eng is not None:
            try:
                self._eng.quit()
            except Exception:
                pass
        self._eng = None

    def evaluate(self, path: str | None, board: chess.Board, move: chess.Move, depth: int = DEPTH) -> dict | None:
        """Score of `move` (cp, mover's view) and the opponent's best line after it; None without engine."""
        if not path:
            return None
        after = board.copy(stack=False)
        after.push(move)
        if after.is_game_over():
            return dict(cp=10000 if after.is_checkmate() else 0, pv=[])
        with self._lock:
            for attempt in (0, 1):                 # one restart if the engine died
                try:
                    info = self._engine(path).analyse(after, chess.engine.Limit(depth=depth, time=MAX_SECS))
                    break
                except (chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError):
                    self.close()
                    if attempt:
                        return None
        score = info.get("score")
        if score is None:
            return None
        return dict(cp=score.pov(board.turn).score(mate_score=10000), pv=list(info.get("pv") or [])[:6])


JUDGE = Judge()
# python-chess runs the engine in a non-daemon thread; quit it before the interpreter waits for that thread
(getattr(threading, "_register_atexit", None) or __import__("atexit").register)(JUDGE.close)
