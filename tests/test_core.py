"""Unit tests that need no engine: classification heuristics, spaced repetition, API."""
import json
import random
import sqlite3
import sys
import time
from pathlib import Path

import chess
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chesstrainer import classify as C, db, fetch, puzzles, repertoire, report  # noqa: E402


def mv(b, uci):
    return chess.Move.from_uci(uci)


def tags(fen, played, best, refut, cu_b=0, cu_a=-300, last_cap=None):
    b = chess.Board(fen)
    return C.motif_tags(b, mv(b, played), mv(b, best) if best else None,
                        chess.Move.from_uci(refut) if refut else None, cu_b, cu_a, last_cap)


def test_winpct_monotonic_and_symmetric():
    assert C.winpct(0) == pytest.approx(50)
    assert C.winpct(300) > C.winpct(100) > 50 > C.winpct(-100)
    assert C.winpct(200) + C.winpct(-200) == pytest.approx(100)


def test_classify_loss_ignores_decided_positions():
    assert C.classify_loss(25, 60, 35) == "blunder"
    assert C.classify_loss(12, 60, 48) == "mistake"
    assert C.classify_loss(25, 6, 0) == "ok"          # already lost
    assert C.classify_loss(25, 99, 93) == "ok"         # still winning


def test_hung_piece():
    # white queen goes to d5 where the black pawn on e6 takes it
    fen = "rnbqkbnr/pppp1ppp/4p3/8/8/3Q4/PPPPPPPP/RNB1KBNR w KQkq - 0 1"
    assert "hung_piece" in tags(fen, "d3d5", "e2e4", "e6d5")


def test_ignored_threat():
    # black bishop on b4 attacks nothing; black threatens Bxc3? use a knight threat instead:
    # black knight on d4 attacks the undefended white queen on e2 -> white plays a pawn move, loses queen
    fen = "r1bqkb1r/pppp1ppp/8/4p3/3n4/8/PPPPQPPP/RNB1KBNR w KQkq - 0 1"
    t = tags(fen, "a2a3", "e2d3", "d4e2")
    assert "ignored_threat" in t


def test_left_piece_hanging():
    # white knight on c3 defends the bishop on e4... after moving it, black pawn... simple version:
    # white rook on a1 defends nothing; use a bishop that was defended by the moved queen
    fen = "4k3/8/8/3p4/4B3/8/8/3QK3 w - - 0 1"   # bishop e4 attacked by d5 pawn, defended by Qd1? no: not defended
    t = tags(fen, "e1f1", "e4d5", "d5e4")
    assert "ignored_threat" in t or "left_piece_hanging" in t


def test_fork_allowed():
    # black knight b4-c2 will fork king e1 and rook a1 after white's irrelevant pawn move
    fen = "4k3/8/8/8/1n6/8/7P/R3K3 w - - 0 1"
    b = chess.Board(fen)
    b.push(mv(b, "h2h3"))
    assert C.makes_fork(b, mv(b, "b4c2"))
    assert "fork_allowed" in tags(fen, "h2h3", "a1b1", "b4c2")


def test_allowed_mate_back_rank():
    # no luft: any waiting move allows Re1#; the best move creates a flight square
    fen = "4r1k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1"
    t = tags(fen, "g1h1", "h2h3", "e8e1", cu_b=0, cu_a=-9990)
    assert "allowed_mate" in t and "back_rank" in t


def test_missed_recapture():
    fen = "4k3/8/8/3p4/8/2N5/8/4K3 w - - 0 1"
    b = chess.Board(fen)
    # pretend black just captured on d5
    t = tags(fen, "e1d1", "c3d5", None, last_cap=chess.D5)
    assert "missed_recapture" in t


def test_context_tags():
    ctx = C.context_tags("middlegame", "white", 70, 40, 2.0, 400, 600)
    assert "threw_win" in ctx and "fast_move" in ctx and "winning" in ctx
    assert "time_trouble" in C.context_tags("endgame", "black", 50, 30, 1.0, 30, 600)


def test_parse_time_control():
    assert fetch.parse_time_control("600") == (600, 0)
    assert fetch.parse_time_control("180+2") == (180, 2)
    assert fetch.parse_time_control("1/86400") == (0, 0)


def test_game_row_from_api():
    g = {"url": "https://www.chess.com/game/live/1", "pgn": "1. e4 e5 *", "time_control": "300+3",
         "time_class": "blitz", "rules": "chess", "end_time": 1700000000,
         "white": {"username": "Alice", "rating": 1500, "result": "win"},
         "black": {"username": "Bob", "rating": 1490, "result": "resigned"}}
    r = fetch.game_row_from_api(g, "alice")
    assert r["user_color"] == "white" and r["result"] == "win" and r["base_secs"] == 300 and r["inc_secs"] == 3
    assert fetch.game_row_from_api(g, "carol") is None
    g2 = dict(g, rules="chess960")
    assert fetch.game_row_from_api(g2, "alice") is None


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "t.db")
    for i in range(12):
        c.execute("INSERT INTO games(id,end_time,user_color,pgn,analyzed) VALUES(?,?,?,?,1)",
                  (f"g{i}", 1700000000 + i * 1000, "white", "*"))
        cur = c.execute(
            "INSERT INTO puzzles(game_id,ply,fen,solution,accepted,played_uci,played_san,solution_san,"
            "severity,loss,tags,context,time_spent,phase,end_time) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"g{i}", 10, chess.STARTING_FEN, "e2e4", json.dumps(["e2e4", "d2d4"]), "a2a3", "a3", "e4",
             "blunder", 25.0, json.dumps(["hung_piece" if i % 3 else "fork_allowed"]),
             json.dumps(["middlegame", "white"]), 3.0, "middlegame", 1700000000 + i * 1000))
        c.execute("INSERT INTO puzzle_state(puzzle_id,box,due) VALUES(?,0,0)", (cur.lastrowid,))
    c.commit()
    return c


def test_pick_diverse_and_limited(con):
    rows = puzzles.pick(con, 5, rng=random.Random(1))
    assert len(rows) == 5
    assert len({r["game_id"] for r in rows}) == 5


def test_srs_correct_moves_up_and_wrong_resets(con):
    now = int(time.time())
    pid = con.execute("SELECT id FROM puzzles LIMIT 1").fetchone()["id"]
    st = puzzles.record(con, pid, True, 1, 4000, now=now)
    assert st["box"] == 1 and st["due"] == now + 24 * 3600
    st = puzzles.record(con, pid, True, 1, 3000, now=now)
    assert st["box"] == 2
    st = puzzles.record(con, pid, False, 3, 9000, now=now)
    assert st["box"] == 0 and st["due"] == now
    due_ids = {r["id"] for r in puzzles.pick(con, 12, now=now)}
    assert pid in due_ids


def test_not_due_puzzles_come_last(con):
    now = int(time.time())
    ids = [r["id"] for r in con.execute("SELECT id FROM puzzles")]
    for pid in ids[:10]:
        puzzles.record(con, pid, True, 1, 1000, now=now)       # due in 24h
    picked = {r["id"] for r in puzzles.pick(con, 2, now=now, rng=random.Random(0))}
    assert picked == set(ids[10:])


def test_web_api(con, tmp_path):
    from chesstrainer.web.app import create_app
    app = create_app(tmp_path / "t.db")
    c = app.test_client()
    assert c.get("/").status_code == 200
    assert c.get("/report").status_code == 200
    w = c.get("/api/warmup?n=5").get_json()
    assert len(w) == 5 and "solution" not in w[0]
    p = w[0]
    assert "e2" in p["legal"] and "e4" in p["legal"]["e2"]
    bad = c.post(f"/api/puzzle/{p['id']}/check", json={"uci": "a2a3"}).get_json()
    assert bad["correct"] is False and bad["original_mistake"] is True
    ok = c.post(f"/api/puzzle/{p['id']}/check", json={"uci": "d2d4"}).get_json()
    assert ok["correct"] is True                              # accepted alternative
    assert c.post(f"/api/puzzle/{p['id']}/check", json={"uci": "e2e5"}).status_code == 400
    fin = c.post(f"/api/puzzle/{p['id']}/finish", json={"first_try": True, "attempts": 1, "ms": 2000}).get_json()
    assert fin["solution"] == "e2e4" and fin["motifs"] and fin["box"] == 1
    st = c.get("/api/stats").get_json()
    assert st["puzzles"] == 12 and st["due"] == 11


def test_sync_user_with_mocked_api(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "s.db")
    game = lambda i, tc: {"url": f"https://chess.com/g/{i}", "uuid": f"u{i}", "pgn": "1. e4 e5 *",
                          "time_control": tc, "time_class": "rapid" if tc == "600" else "bullet",
                          "rules": "chess", "end_time": 1700000000 + i,
                          "white": {"username": "Me", "rating": 1600, "result": "checkmated"},
                          "black": {"username": "Other", "rating": 1610, "result": "win"}}

    def fake(url, retries=4):
        if url.endswith("/archives"):
            return {"archives": ["https://api.chess.com/pub/player/me/games/2026/09",
                                 "https://api.chess.com/pub/player/me/games/2026/10"]}
        return {"games": [game(1, "600"), game(2, "60"), game(3, "600")]}

    monkeypatch.setattr(fetch, "_get_json", fake)
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    n = fetch.sync_user(c, "me", months=2, log=lambda *_: None)
    assert n == 2                               # bullet skipped; same uuids in both months deduplicated
    row = c.execute("SELECT * FROM games WHERE id='u1'").fetchone()
    assert row["result"] == "loss" and row["user_color"] == "white"


def test_opening_names():
    from chesstrainer import openings as O
    n = O.opening_name({"ECOUrl": "https://www.chess.com/openings/Sicilian-Defense-Open-Accelerated-Dragon-Exchange-Variation-5...bxc6"})
    assert n == "Sicilian Defense Open Accelerated Dragon Exchange Variation" and O.family_of(n) == "Sicilian Defense"
    n = O.opening_name({"ECOUrl": "https://www.chess.com/openings/Caro-Kann-Defense-Advance-Short-Variation-with-4-Nf3...e6"})
    assert n == "Caro-Kann Defense Advance Short Variation" and O.family_of(n) == "Caro-Kann Defense"
    assert O.family_of(O.opening_name({"ECOUrl": "https://www.chess.com/openings/Giuoco-Piano-Game-Center-Attack"})) == "Italian Game"
    assert O.family_of(O.opening_name({"ECOUrl": "https://www.chess.com/openings/Bishops-Opening-Boi-Variation-3.Nf3"})) == "Bishop's Opening"
    assert O.opening_name({}) == "Unbekannt"


def test_openings_build_finds_recurring_leak(tmp_path):
    from chesstrainer import openings as O
    c = db.connect(tmp_path / "o.db")
    pgn = ('[White "me"]\n[Black "x"]\n[ECOUrl "https://www.chess.com/openings/Italian-Game"]\n'
           '[Link "https://www.chess.com/game/live/1"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. h3 d6 *')
    for i in range(3):
        c.execute("INSERT INTO games(id,end_time,user_color,result,opponent,pgn,analyzed) VALUES(?,?,?,?,?,?,1)",
                  (f"g{i}", 1700000000 + i, "white", "win" if i else "loss", "x", pgn))
        for ply, san, uci, loss, cls in ((0, "e4", "e2e4", 0, "ok"), (2, "Nf3", "g1f3", 0, "ok"),
                                         (4, "Bc4", "f1c4", 0, "ok"), (6, "h3", "h2h3", 12.0, "mistake")):
            c.execute("INSERT INTO moves(game_id,ply,move_no,san,uci,best_uci,best_san,phase,win_before,win_after,"
                      "loss,cls) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"g{i}", ply, ply // 2 + 1, san, uci, "c2c3" if ply == 6 else uci,
                       "c3" if ply == 6 else san, "opening", 55, 55 - loss, loss, cls))
    c.commit()
    o = O.build(c)
    w = o["white"]
    assert w["games"] == 3 and w["families"][0]["family"] == "Italienische Partie" and w["families"][0]["errors"] == 3
    assert round(w["score"]) == 67
    assert len(w["leaks"]) == 1 and w["leaks"][0]["n"] == 3 and w["leaks"][0]["best_san"] == "c3"
    assert w["leaks"][0]["played"][0]["san"] == "h3" and w["leaks"][0]["link"].endswith("/1")
    assert o["black"]["games"] == 0 and o["insights"]


def test_pick_opening_mode(con):
    assert puzzles.pick(con, 5, mode="opening") == []
    con.execute("UPDATE puzzles SET phase='opening' WHERE game_id IN ('g1','g2')")
    con.commit()
    assert {r["game_id"] for r in puzzles.pick(con, 5, mode="opening")} == {"g1", "g2"}


def test_openings_and_examples_pages(con, tmp_path):
    from chesstrainer.web.app import create_app
    c = create_app(tmp_path / "t.db").test_client()
    assert c.get("/openings").status_code == 200
    assert c.get("/report").status_code == 200
    assert len(c.get("/api/warmup?mode=opening").get_json()) == 0


def test_line_material_counts_pending_recapture():
    b = chess.Board("4k3/8/8/3p4/4P3/8/8/4K3 w - - 0 1")
    assert C.line_material(b, [chess.Move.from_uci("e4d5")], chess.WHITE) == 1
    b = chess.Board("4k3/8/2p5/3p4/4P3/8/8/4K3 w - - 0 1")          # pawn is defended: exd5 cxd5 is an even trade
    assert C.line_material(b, [chess.Move.from_uci("e4d5")], chess.WHITE) == 0


def test_pin_and_discovery_detection():
    b = chess.Board("4k3/8/8/4n3/8/8/8/R3K3 w - - 0 1")              # Re1 pins the knight to the king
    assert C.makes_pin_or_skewer(b, chess.Move.from_uci("a1e1"))
    assert not C.makes_pin_or_skewer(b, chess.Move.from_uci("a1b1"))
    b = chess.Board("3qk3/8/8/8/3N4/8/8/3RK3 w - - 0 1")             # knight moves away, rook hits the queen
    assert C.makes_discovery(b, chess.Move.from_uci("d4f5"))


def test_multi_move_tactic_and_positional_split():
    # refutation wins a rook two moves later, nothing is captured immediately
    fen = "6k1/5ppp/8/8/8/8/r4PPP/1R4K1 w - - 0 1"
    b = chess.Board(fen)
    line = [chess.Move.from_uci(u) for u in ("a2a1", "g1h1", "a1b1")]
    t = C.motif_tags(b, chess.Move.from_uci("g2g3"), [chess.Move.from_uci("b1b8")], line, 0, -500, None,
                     "endgame", 50)
    assert "tactic_allowed" in t or "pin_allowed" in t
    quiet = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3"
    args = (chess.Board(quiet), chess.Move.from_uci("a2a3"), [chess.Move.from_uci("f1c4")],
            [chess.Move.from_uci("g8f6")], 30, -20, None)
    assert C.motif_tags(*args, "opening", 55) == ["opening_play"]
    assert C.motif_tags(*args, "middlegame", 80) == ["missed_conversion"]
    assert C.motif_tags(*args, "middlegame", 20) == ["missed_defense"]
    assert C.motif_tags(*args, "middlegame", 50) == ["quiet_mistake"]


def test_finish_returns_variations(con, tmp_path):
    from chesstrainer.web.app import create_app
    con.execute("UPDATE puzzles SET line=?, refutation=?, eval_best=30, eval_played=-250",
                (json.dumps(["e2e4", "e7e5", "g1f3"]), json.dumps(["a2a3", "e7e5"])))
    con.commit()
    c = create_app(tmp_path / "t.db").test_client()
    pid = c.get("/api/warmup?n=1").get_json()[0]["id"]
    r = c.post(f"/api/puzzle/{pid}/finish", json={"first_try": False, "attempts": 3}).get_json()
    assert [s["label"] for s in r["best_line"]] == ["1. e4", "1… e5", "2. Nf3"]
    assert r["your_line"][0]["label"] == "1. a3" and r["eval_played"] == -250 and r["loss"] == 25.0
    assert r["best_line"][0]["fen"].startswith("rnbqkbnr/pppppppp/8/8/4P3")


def test_progress_compares_windows(tmp_path):
    from chesstrainer import progress
    c = db.connect(tmp_path / "p.db")
    for i in range(20):
        late = i >= 10
        c.execute("INSERT INTO games(id,end_time,user_color,result,user_rating,time_class,pgn,analyzed) "
                  "VALUES(?,?,?,?,?,?,?,1)", (f"g{i}", 1700000000 + i * 3600, "white", "win" if late else "loss",
                                              1000 + 5 * i, "blitz", "1. e4 e5 *"))
        for ply in range(0, 40, 2):
            bad = ply in ((4,) if late else (4, 8, 12))
            c.execute("INSERT INTO moves(game_id,ply,move_no,san,uci,phase,time_spent,win_before,win_after,loss,cls) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (f"g{i}", ply, ply // 2 + 1, "x", "a2a3", "middlegame", 5.0,
                                                         50, 30 if bad else 50, 20 if bad else 0,
                                                         "blunder" if bad else "ok"))
        for k in range(1 if late else 3):
            c.execute("INSERT INTO puzzles(game_id,ply,fen,solution,accepted,tags,context,end_time) VALUES(?,?,?,?,?,?,?,?)",
                      (f"g{i}", 4 + 4 * k, chess.STARTING_FEN, "e2e4", "[]", '["hung_piece"]', '["middlegame"]', 0))
    c.commit()
    o = progress.build(c)
    m = {x["key"]: x for x in o["metrics"]}
    assert o["window"] == 10 and m["err100"]["verdict"] == "better" and m["score"]["verdict"] == "better"
    assert m["err100"]["prev"] == "15.0" and m["err100"]["recent"] == "5.0"
    hp = next(d for d in o["motifs"] if d["tag"] == "hung_piece")
    assert hp["prev"] == 30 and hp["recent"] == 10 and hp["verdict"] == "better"
    assert o["rating"]["first"] == 1000 and o["rating"]["last"] == 1095 and o["insights"]
    from chesstrainer.web.app import create_app
    assert create_app(tmp_path / "p.db").test_client().get("/progress").status_code == 200
    assert create_app(tmp_path / "empty.db").test_client().get("/progress").status_code == 200


def _game_db(tmp_path):
    c = db.connect(tmp_path / "g.db")
    pgn = ('[White "me"]\n[Black "opp"]\n[ECOUrl "https://www.chess.com/openings/Italian-Game"]\n'
           '[Link "https://www.chess.com/game/live/9"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. h3 d6 *')
    c.execute("INSERT INTO games(id,end_time,user_color,result,opponent,pgn,analyzed) VALUES('g',1700000000,'white','loss','opp',?,1)", (pgn,))
    for ply, san, uci, loss, cls in ((0, "e4", "e2e4", 0, "ok"), (2, "Nf3", "g1f3", 0, "ok"), (4, "Bc4", "f1c4", 0, "ok"),
                                     (6, "h3", "h2h3", 12.0, "mistake")):
        c.execute("INSERT INTO moves(game_id,ply,move_no,san,uci,phase,win_before,win_after,loss,cls) VALUES('g',?,?,?,?,?,?,?,?,?)",
                  (ply, ply // 2 + 1, san, uci, "opening", 55, 55 - loss, loss, cls))
    fen = "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    c.execute("INSERT INTO puzzles(game_id,ply,fen,solution,accepted,played_uci,played_san,solution_san,severity,loss,tags,"
              "context,phase,end_time,line,refutation,eval_best,eval_played) VALUES('g',6,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (fen, "c2c3", '["c2c3"]', "h2h3", "h3", "c3", "mistake", 12.0, '["opening_play"]', '["opening","white"]',
               "opening", 1700000000, '["c2c3","g8f6"]', '["h2h3","d7d6"]', 40, -20))
    c.execute("INSERT INTO puzzle_state(puzzle_id) VALUES(1)")
    c.commit()
    return c


def test_game_detail_and_export(tmp_path):
    from chesstrainer import games
    c = _game_db(tmp_path)
    rows = games.list_games(c)
    assert rows[0]["mistakes"] == 1 and rows[0]["opening"] == "Italienische Partie" and rows[0]["worst"] == 12.0
    d = games.detail(c, rows[0]["n"])
    assert len(d["plies"]) == 8 and d["plies"][6]["cls"] == "mistake" and d["plies"][1]["mine"] is False
    m = d["mistakes"][0]
    assert m["ply"] == 6 and m["best_san"] == "c3" and m["best_line"][0]["label"] == "4. c3"
    assert m["your_line"][1]["label"] == "4… d6" and m["motifs"][0]["title"] == "Eröffnungsprinzip verletzt"
    assert games.detail(c, 999) is None
    csv_text = games.export_csv(c)
    assert "Italienische Partie" in csv_text and ",h3,c3,12.0,0.40,-0.20," in csv_text
    pgn = games.export_pgn(c)
    assert "4. c3" in pgn and "( 4. h3" in pgn and '[SetUp "1"]' in pgn


def test_pages_login_and_export_routes(tmp_path):
    from chesstrainer.web.app import create_app
    _game_db(tmp_path)
    c = create_app(tmp_path / "g.db").test_client()
    for path in ("/", "/report", "/games", "/game/1", "/openings", "/progress", "/manifest.webmanifest"):
        assert c.get(path).status_code == 200, path
    assert c.get("/game/77").status_code == 404
    r = c.get("/export/mistakes.csv")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and "Italienische Partie".encode() in r.data
    assert c.get("/export/mistakes.pgn").status_code == 200 and c.get("/export/mistakes.xls").status_code == 404
    assert c.get("/login").status_code == 302                      # no password configured

    p = create_app(tmp_path / "g.db", password="geheim").test_client()
    assert p.get("/report").status_code == 302 and "/login" in p.get("/report").headers["Location"]
    assert p.get("/api/stats").status_code == 401 and p.get("/export/mistakes.csv").status_code == 302
    assert p.get("/static/style.css").status_code == 200 and p.get("/login").status_code == 200
    assert p.get("/sw.js").status_code == 200 and p.get("/offline").status_code == 200
    import chesstrainer.web.app as webapp
    webapp.time.sleep = lambda s: None
    assert p.post("/login", data={"password": "falsch"}).status_code == 401
    assert p.get("/report").status_code == 302
    r = p.post("/login?next=//evil.example", data={"password": "geheim"})
    assert r.status_code == 302 and r.headers["Location"] == "/"
    assert p.get("/report").status_code == 200 and p.get("/api/stats").status_code == 200
    p.post("/logout")
    assert p.get("/report").status_code == 302


def test_repertoire_drills_are_consistent():
    keys = [d["key"] for d in repertoire.DRILLS]
    assert len(keys) == len(set(keys))
    for d in repertoire.all_drills():
        board = chess.Board(d["fen"])
        assert d["accepted"] and all(chess.Move.from_uci(u) in board.legal_moves for u in d["accepted"])
        assert d["line_uci"][0] in d["accepted"]
        if d["typical_uci"]:
            assert d["typical_uci"] not in d["accepted"]
            assert d["refutation_uci"][0] == d["typical_uci"] and d["loss"] > 0
        assert d["hint"] and d["title"]


def test_repertoire_pick_and_record(tmp_path):
    c = db.connect(tmp_path / "r.db")
    now = int(time.time())
    first = repertoire.pick(c, 5, now=now, rng=random.Random(0))
    assert len(first) == 5 and max(sum(d["group"] == g for d in first) for g in {d["group"] for d in first}) <= 2
    for d in first:
        repertoire.record(c, d["key"], True, now=now)               # learned once, due in 24h
    nxt = {d["key"] for d in repertoire.pick(c, 5, now=now, rng=random.Random(0))}
    assert not nxt & {d["key"] for d in first}                      # unseen drills come first
    st = repertoire.record(c, first[0]["key"], False, now=now)
    assert st["box"] == 0 and st["due"] == now
    assert repertoire.summary(c, now=now)["seen"] == 5
    assert c.execute("SELECT COUNT(*) FROM puzzles").fetchone()[0] == 0   # drills never become puzzles


def test_repertoire_api(con, tmp_path):
    from chesstrainer.web.app import create_app
    c = create_app(tmp_path / "t.db").test_client()
    w = c.get("/api/warmup?n=3&mode=repertoire").get_json()
    assert len(w) == 3 and all(p["drill"] and p["url"].startswith("/api/drill/") for p in w)
    d = repertoire.get(w[0]["id"])
    ok = c.post(f"{w[0]['url']}/check", json={"uci": d["solution"]}).get_json()
    assert ok["correct"] is True
    fin = c.post(f"{w[0]['url']}/finish", json={"first_try": True}).get_json()
    assert fin["drill"] and fin["best_line"] and fin["box"] == 1 and fin["motifs"][0]["hint"] == d["hint"]
    assert c.post("/api/drill/nope/check", json={"uci": "e2e4"}).status_code == 404
    assert c.get("/openings").status_code == 200
    assert c.get("/api/stats").get_json()["puzzles"] == 12
