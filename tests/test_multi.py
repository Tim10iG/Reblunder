"""Public multi-user mode: sign-in by Chess.com name, per-player databases, queue, limits, deletion."""
import json
import os
import sys
import time
from pathlib import Path

import chess
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chesstrainer import analyze, db, fetch, users  # noqa: E402
from chesstrainer.jobs import JobQueue  # noqa: E402
from chesstrainer.users import MultiConfig, UnknownPlayer, UserStore  # noqa: E402
from chesstrainer.web.app import create_app  # noqa: E402


@pytest.fixture()
def known(monkeypatch):
    """Pretend these players exist on Chess.com (no network in tests)."""
    names = {"alice_99": "Alice_99", "bob": "Bob", "carol": "Carol", "dave": "Dave"}

    def fake(name):
        if name not in names:
            raise UnknownPlayer(f"Auf Chess.com gibt es keinen Spieler „{name}“.")
        return {"username": names[name]}
    monkeypatch.setattr("chesstrainer.web.app.lookup_player", fake)
    return names


def make(tmp_path, **kw):
    app = create_app(multi=MultiConfig(data_dir=tmp_path, autostart=False, **kw))
    orig = app.test_client

    def client(*a, **k):                      # these tests assert the German texts
        c = orig(*a, **k)
        c.environ_base["HTTP_ACCEPT_LANGUAGE"] = "de"
        return c
    app.test_client = client
    return app


def add_puzzle(store, name):
    con, lock = store.open(name)
    with lock:
        con.execute("INSERT INTO games(id,end_time,user_color,pgn,analyzed) VALUES('g1',1700000000,'white','*',1)")
        cur = con.execute(
            "INSERT INTO puzzles(game_id,ply,fen,solution,accepted,played_uci,played_san,solution_san,severity,loss,tags,"
            "context,time_spent,phase,end_time) VALUES('g1',10,?,'e2e4',?,'a2a3','a3','e4','blunder',25.0,?,?,3.0,"
            "'middlegame',1700000000)",
            (chess.STARTING_FEN, json.dumps(["e2e4"]), json.dumps(["hung_piece"]), json.dumps(["middlegame", "white"])))
        con.execute("INSERT INTO puzzle_state(puzzle_id,box,due) VALUES(?,0,0)", (cur.lastrowid,))
        con.commit()


def test_guest_sees_landing_and_nothing_else(tmp_path, known):
    c = make(tmp_path).test_client()
    r = c.get("/")
    assert r.status_code == 200 and b"Chess.com-Benutzername" in r.data
    assert c.get("/privacy").status_code == 200
    for path in ("/report", "/games", "/openings", "/progress", "/game/1"):
        resp = c.get(path)
        assert resp.status_code == 302 and resp.headers["Location"].endswith("/"), path
    assert c.get("/api/stats").status_code == 401
    assert c.get("/api/warmup").status_code == 401
    assert c.post("/api/sync").status_code == 401


@pytest.mark.parametrize("bad", ["", "ab", "a/../b", "x" * 26, "Ali ce", "../etc/passwd", "alice;drop", "ä_ö_ü"])
def test_invalid_names_are_rejected_without_touching_disk(tmp_path, known, bad):
    app = make(tmp_path)
    r = app.test_client().post("/enter", data={"username": bad})
    assert r.status_code == 400 and "gültiger" in r.get_data(as_text=True)
    assert app.store.count() == 0


def test_unknown_player(tmp_path, known):
    app = make(tmp_path)
    r = app.test_client().post("/enter", data={"username": "nobody123"})
    assert r.status_code == 404 and "keinen Spieler" in r.get_data(as_text=True)
    assert app.store.count() == 0


def test_enter_creates_private_db_and_queues_a_job(tmp_path, known):
    app = make(tmp_path)
    c = app.test_client()
    r = c.post("/enter", data={"username": "  ALICE_99 "})
    assert r.status_code == 302
    assert (tmp_path / "users" / "alice_99.db").is_file()
    s = c.get("/api/stats").get_json()
    assert s["user"] == "Alice_99" and s["multi"] is True
    assert s["job"]["running"] and s["job"]["queued"] and "Gleich dran" in s["job"]["msg"]
    page = c.get("/").get_data(as_text=True)
    assert "Alice_99" in page and 'id="board"' in page          # the warm-up page, with the player's name in the footer


def test_players_are_isolated(tmp_path, known):
    app = make(tmp_path)
    a, b = app.test_client(), app.test_client()
    a.post("/enter", data={"username": "alice_99"})
    b.post("/enter", data={"username": "bob"})
    add_puzzle(app.store, "alice_99")
    assert len(a.get("/api/warmup?n=5").get_json()) == 1
    assert b.get("/api/warmup?n=5").get_json() == []
    assert a.get("/api/stats").get_json()["user"] == "Alice_99"
    assert b.get("/api/stats").get_json()["user"] == "Bob"
    assert b.get("/api/puzzle/1/check", method="POST").status_code in (404, 405)


def test_returning_player_keeps_data(tmp_path, known):
    app = make(tmp_path)
    c = app.test_client()
    c.post("/enter", data={"username": "alice_99"})
    add_puzzle(app.store, "alice_99")
    c.post("/leave")
    assert c.get("/report").status_code == 302
    c.post("/enter", data={"username": "Alice_99"})
    assert len(c.get("/api/warmup?n=5").get_json()) == 1
    assert app.store.count() == 1


def test_delete_me_removes_everything_and_cancels_the_job(tmp_path, known):
    app = make(tmp_path)
    c = app.test_client()
    c.post("/enter", data={"username": "alice_99"})
    r = c.post("/delete-me")
    assert r.status_code == 302 and "gone=1" in r.headers["Location"]
    assert list((tmp_path / "users").glob("alice_99.db*")) == []
    assert app.queue.load()["waiting"] == 0
    assert c.get("/report").status_code == 302
    assert "gelöscht" in c.get("/?gone=1").get_data(as_text=True)


def test_privacy_page_offers_delete_only_when_signed_in(tmp_path, known):
    app = make(tmp_path, retention_days=60, contact="hi@example.org")
    c = app.test_client()
    page = c.get("/privacy").get_data(as_text=True)
    assert "hi@example.org" in page and "60 Tage" in page and "/delete-me" not in page
    c.post("/enter", data={"username": "bob"})
    assert "/delete-me" in c.get("/privacy").get_data(as_text=True)


def test_rate_limit_on_new_players_and_user_cap(tmp_path, known):
    app = make(tmp_path, new_per_hour=2)
    c = app.test_client()
    assert c.post("/enter", data={"username": "alice_99"}).status_code == 302
    assert c.post("/enter", data={"username": "bob"}).status_code == 302
    r = c.post("/enter", data={"username": "carol"})
    assert r.status_code == 429
    assert c.post("/enter", data={"username": "bob"}).status_code == 302     # existing players are not limited
    app2 = make(tmp_path / "cap", max_users=1)
    c2 = app2.test_client()
    assert c2.post("/enter", data={"username": "alice_99"}).status_code == 302
    r = c2.post("/enter", data={"username": "bob"})
    assert r.status_code == 503 and "voll" in r.get_data(as_text=True)


def test_session_cookie_is_signed_and_not_forgeable(tmp_path, known):
    app = make(tmp_path)
    c = app.test_client()
    c.post("/enter", data={"username": "alice_99"})
    with c.session_transaction() as s:
        assert s["u"] == "alice_99"
    other = app.test_client()
    other.set_cookie("session", "eyJ1IjoiYWxpY2VfOTkifQ.AAAAAA.forged")
    assert other.get("/api/stats").status_code == 401


# ------------------------------------------------------------------------------------------- queue
def test_queue_order_positions_and_cooldown(tmp_path, monkeypatch):
    store = UserStore(tmp_path)
    for n in ("aaa", "bbb", "ccc"):
        store.create(n, n)
    q = JobQueue(store, MultiConfig(data_dir=tmp_path, autostart=False, cooldown_min=15, max_queue=3))
    ran = []
    monkeypatch.setattr(q, "run_user", lambda name: ran.append(name))
    for n in ("aaa", "bbb", "ccc"):
        assert q.submit(n)["started"]
    assert q.submit("aaa")["reason"] == "queued"
    assert "2 Analysen vor dir" in q.status("ccc")["msg"] and "1 Analyse vor dir" in q.status("bbb")["msg"]
    assert q.submit("ddd")["reason"] == "full"
    assert q.run_pending() == 3 and ran == ["aaa", "bbb", "ccc"]
    st = q.status("aaa")
    assert not st["running"] and st["error"] is None and st["last"]
    r = q.submit("aaa")
    assert r["started"] is False and r["reason"] == "cooldown" and r["minutes"] >= 14
    assert q.submit("aaa", force=True)["started"]


def test_failed_job_reports_a_friendly_error_and_can_retry_soon(tmp_path, monkeypatch):
    store = UserStore(tmp_path)
    store.create("aaa", "aaa")
    q = JobQueue(store, MultiConfig(data_dir=tmp_path, autostart=False))

    def boom(name):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(q, "run_user", boom)
    q.submit("aaa")
    q.run_pending()
    st = q.status("aaa")
    assert st["error"] and "secret" not in st["error"] and not st["running"]
    q.state["aaa"]["next_ok"] = time.time() - 1               # the short retry delay is over
    assert q.submit("aaa")["started"]


def test_pipeline_fetches_analyses_and_reports_progress(tmp_path, monkeypatch):
    store = UserStore(tmp_path)
    store.create("aaa", "Aaa")
    cfg = MultiConfig(data_dir=tmp_path, autostart=False, max_games=7)
    q = JobQueue(store, cfg)
    seen = {}

    def fake_sync(con, user, months, classes, max_games, log):
        seen.update(user=user, months=months, classes=classes, max_games=max_games)
        con.execute("INSERT INTO games(id,end_time,user_color,pgn,analyzed) VALUES('x',1,'white','*',0)")
        con.commit()
        return 1

    def fake_run(con, eng, depth, workers, limit, log, progress):
        seen.update(depth=depth, workers=workers, limit=limit)
        progress(1, 1)
        con.execute("UPDATE games SET analyzed=1")
        con.commit()
        return 1
    monkeypatch.setattr(fetch, "sync_user", fake_sync)
    monkeypatch.setattr(analyze, "find_engine", lambda *a, **k: "/bin/true")
    monkeypatch.setattr(analyze, "run_analysis", fake_run)
    monkeypatch.setattr(analyze, "missing_lines", lambda con: 0)
    q.submit("aaa")
    q.run_pending()
    assert seen == dict(user="Aaa", months=2, classes=("rapid", "blitz"), max_games=7, depth=12, workers=1, limit=7)
    st = q.status("aaa")
    assert st["error"] is None and st["new"] == 1 and st["done"] == 1


def test_player_without_games_gets_a_clear_message(tmp_path, monkeypatch):
    store = UserStore(tmp_path)
    store.create("aaa", "Aaa")
    q = JobQueue(store, MultiConfig(data_dir=tmp_path, autostart=False))
    monkeypatch.setattr(fetch, "sync_user", lambda *a, **k: 0)
    q.submit("aaa")
    q.run_pending()
    assert "keine Rapid- oder Blitz-Partien" in q.status("aaa")["error"]


def test_cancel_while_waiting_and_deleted_db_is_not_recreated(tmp_path, monkeypatch):
    store = UserStore(tmp_path)
    store.create("aaa", "Aaa")
    q = JobQueue(store, MultiConfig(data_dir=tmp_path, autostart=False))
    q.submit("aaa")
    q.cancel("aaa")
    assert q.run_pending() == 0 and "aaa" not in q.state
    q.submit("aaa")
    store.delete("aaa")
    q.run_pending()                                             # run_user sees the missing file and stops quietly
    assert not (tmp_path / "users" / "aaa.db").exists()
    store.touch("aaa")
    assert not (tmp_path / "users" / "aaa.db").exists()


# ------------------------------------------------------------------------------------------- store helpers
def test_purge_old_and_normalize(tmp_path):
    store = UserStore(tmp_path)
    store.create("old_one", "Old_One")
    store.create("fresh", "Fresh")
    long_ago = time.time() - 100 * 86400
    os.utime(store.path("old_one"), (long_ago, long_ago))
    assert store.purge_old(90) == ["old_one"]
    assert store.names() == ["fresh"]
    assert store.purge_old(0) == []
    assert users.normalize(" Mixed-Case_9 ") == "mixed-case_9"
    with pytest.raises(users.InvalidName):
        users.normalize("../x")


def test_rate_limiter_window():
    rl = users.RateLimiter(2, window=10)
    assert rl.allow("ip", now=100) and rl.allow("ip", now=101)
    assert not rl.allow("ip", now=102)
    assert rl.allow("other", now=102)
    assert rl.allow("ip", now=111)                              # the first hit has expired


def test_secret_key_is_stable(tmp_path):
    a = UserStore(tmp_path).secret_key()
    assert a == UserStore(tmp_path).secret_key() and len(a) == 64


def test_single_user_mode_is_unchanged(tmp_path):
    c = create_app(tmp_path / "s.db").test_client()
    assert c.get("/").status_code == 200 and c.get("/privacy").status_code == 200
    assert c.post("/enter", data={"username": "x"}).status_code == 404
    assert c.get("/api/stats").get_json()["multi"] is False
