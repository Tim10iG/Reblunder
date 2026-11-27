"""Create synthetic demo games (with %clk comments) so the pipeline can be tried without Chess.com.

The 'user' plays mostly engine moves but sometimes a random legal move, preferably played fast.
Usage: python tools/make_demo_games.py --engine /path/to/stockfish --games 12 --out data/demo.pgn
"""
import argparse
import random
import sys
from pathlib import Path

import chess
import chess.engine
import chess.pgn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def play_game(eng, rng, user_white: bool, idx: int, base=600, inc=0, max_plies=90):
    board = chess.Board()
    game = chess.pgn.Game()
    me, opp = "demo_user", f"opponent{idx}"
    game.headers.update({"Event": "Demo", "Site": f"demo/{idx}", "Date": f"2026.10.{1 + idx % 28:02d}",
                         "UTCDate": f"2026.10.{1 + idx % 28:02d}", "UTCTime": f"{10 + idx % 10:02d}:00:00",
                         "White": me if user_white else opp, "Black": opp if user_white else me,
                         "TimeControl": str(base), "WhiteElo": "1700", "BlackElo": "1700"})
    clocks = {chess.WHITE: float(base), chess.BLACK: float(base)}
    node = game
    while not board.is_game_over() and board.ply() < max_plies:
        mine = (board.turn == chess.WHITE) == user_white
        infos = eng.analyse(board, chess.engine.Limit(depth=4), multipv=3)
        cands = [i["pv"][0] for i in infos if i.get("pv")]
        blunder = mine and board.ply() > 8 and rng.random() < 0.17
        if blunder:
            move = rng.choice(list(board.legal_moves))
            spent = rng.choice([1, 1, 2, 2, 3, 12])
        else:
            move = cands[0] if rng.random() < 0.75 or len(cands) == 1 else rng.choice(cands[1:])
            spent = max(1, int(rng.gauss(14 if mine else 10, 8)))
        clocks[board.turn] = max(3.0, clocks[board.turn] - spent + inc)
        board.push(move)
        node = node.add_variation(move)
        node.set_clock(clocks[not board.turn])
    res = board.result(claim_draw=True) if board.is_game_over() else "1/2-1/2"
    game.headers["Result"] = res
    return game


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--games", type=int, default=12)
    ap.add_argument("--out", default="data/demo.pgn")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    eng = chess.engine.SimpleEngine.popen_uci(a.engine)
    with open(a.out, "w") as f:
        for i in range(a.games):
            g = play_game(eng, rng, user_white=(i % 2 == 0), idx=i)
            print(g, file=f, end="\n\n")
            print(f"game {i + 1}/{a.games}: {len(list(g.mainline()))} plies", flush=True)
    eng.quit()


if __name__ == "__main__":
    main()
