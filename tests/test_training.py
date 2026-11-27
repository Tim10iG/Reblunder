"""Multi-move puzzles, engine-judged alternatives and the plain-language reasons (no engine needed)."""
import json

import chess
import pytest

from chesstrainer import db, explain, i18n
from chesstrainer import judge as J

FORK = "r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1"          # 1.Nc7+ Kd7 2.Nxa8 wins the rook


def _moves(*u):
    return [chess.Move.from_uci(x) for x in u]


def test_realized_moves():
    assert explain.realized_moves(FORK, ["b5c7", "e8d7", "c7a8"]) == 2
    assert explain.realized_moves(FORK, ["b5c7", "e8d7"]) == 1               # gain not on the board: one move
    assert explain.realized_moves(chess.STARTING_FEN, ["e2e4", "e7e5", "g1f3"]) == 1
    mate = "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"
    assert explain.realized_moves(mate, ["a1a8"]) == 1


def test_reasons_are_concrete():
    tok = i18n.set_lang("de")
    try:
        b = chess.Board("4k3/8/2n5/8/8/8/8/3QK3 w - - 0 1")
        why = explain.punishment(b, chess.Move.from_uci("d1d4"), _moves("c6d4"))
        assert why[0] == "Der Gegner antwortet Nxd4 und schlägt deine Dame."
        assert "d4" in why[1] and any("9 Bauern" in w for w in why)
        best = explain.merit(chess.Board(FORK), _moves("b5c7", "e8d7", "c7a8"))
        assert best and "5 Bauern" in best[0]
        fork = explain.punishment(chess.Board("4k3/8/8/8/1n6/8/7P/R3K3 w - - 0 1"), chess.Move.from_uci("h2h3"),
                                  _moves("b4c2"))
        assert "gleichzeitig" in fork[0]
    finally:
        i18n._lang.reset(tok)


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    from chesstrainer.web.app import create_app
    c = db.connect(tmp_path / "t.db")
    c.execute("INSERT INTO games(id,end_time,user_color,pgn,analyzed) VALUES('g',1700000000,'white','*',1)")
    cur = c.execute(
        "INSERT INTO puzzles(game_id,ply,fen,solution,accepted,played_uci,played_san,solution_san,severity,loss,tags,"
        "context,time_spent,phase,end_time,line,refutation,eval_best,eval_played) "
        "VALUES('g',20,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (FORK, "b5c7", '["b5c7"]', "e1d1", "Kd1", "Nc7+", "blunder", 40.0, '["missed_fork"]', '["middlegame","white"]',
         5.0, "middlegame", 1700000000, json.dumps(["b5c7", "e8d7", "c7a8"]), json.dumps(["e1d1", "a8a1"]), 500, 0))
    c.execute("INSERT INTO puzzle_state(puzzle_id,box,due) VALUES(?,0,0)", (cur.lastrowid,))
    c.commit()
    calls = []
    monkeypatch.setattr(J.JUDGE, "evaluate", lambda path, board, move, depth=14: calls.append(move.uci()) or None)
    return create_app(tmp_path / "t.db").test_client(), cur.lastrowid, calls


def test_multi_move_check_flow(app_client):
    c, pid, calls = app_client
    w = c.get("/api/warmup?n=1").get_json()
    assert w[0]["goal"] == 2
    r = c.post(f"/api/puzzle/{pid}/check", json={"uci": "b5c7", "step": 0}).get_json()
    assert r["correct"] and r["done"] is False and r["reply"] == "e8d7" and r["next_step"] == 2
    assert "c7" in r["legal"]
    bad = c.post(f"/api/puzzle/{pid}/check", json={"uci": "c7e6", "step": 2}).get_json()
    assert bad["correct"] is False and calls == ["c7e6"]
    ok = c.post(f"/api/puzzle/{pid}/check", json={"uci": "c7a8", "step": 2}).get_json()
    assert ok["correct"] and ok["done"] and not ok["alt"]
    assert c.post(f"/api/puzzle/{pid}/check", json={"uci": "c7a8", "step": 3}).status_code == 400
    game_move = c.post(f"/api/puzzle/{pid}/check", json={"uci": "e1d1", "step": 0}).get_json()
    assert game_move["original_mistake"] and game_move["refute"] == "a8a1" and game_move["why"]
    fin = c.post(f"/api/puzzle/{pid}/finish", json={"first_try": True}).get_json()
    assert fin["why_played"] and fin["why_best"]


def test_engine_accepts_equal_alternative(app_client, monkeypatch):
    c, pid, _ = app_client
    monkeypatch.setattr(J.JUDGE, "evaluate", lambda path, board, move, depth=14: dict(cp=480, pv=[]))
    r = c.post(f"/api/puzzle/{pid}/check", json={"uci": "b5d6", "step": 0}).get_json()
    assert r["correct"] and r["done"] and r["alt"] and r["best_san"] == "Nc7+"
    monkeypatch.setattr(J.JUDGE, "evaluate", lambda path, board, move, depth=14: dict(cp=-50, pv=_moves("e8e7")))
    r = c.post(f"/api/puzzle/{pid}/check", json={"uci": "b5d6", "step": 0}).get_json()
    assert r["correct"] is False and r["refute"] == "e8e7"


def test_accept_threshold():
    assert J.accept_threshold(None) is None
    assert J.accept_threshold(5000) == J.CLEARLY_WON
    assert J.accept_threshold(0) == pytest.approx(46.0)
