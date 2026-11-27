"""Flask web UI: warm-up puzzles, game analysis, openings, progress."""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import chess
from flask import (Flask, Response, abort, jsonify, redirect, render_template as _render, request, session, url_for)
from jinja2 import BaseLoader

from .. import analyze, classify as C, db, explain, fetch, games, openings, progress, puzzles, repertoire, report
from ..judge import JUDGE, accept_threshold
from ..i18n import LANGS, NAMES, Msg, fmt_date, get_lang, js_catalog, pick, set_lang, translate_source, tr
from ..jobs import JobQueue
from ..users import (MultiConfig, RateLimiter, UserError, UserStore, TooManyRequests, UnknownPlayer, Unavailable,
                     lookup_player, normalize)


def _legal_map(board: chess.Board) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in board.legal_moves:
        out.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
    return out


def _drill_rows(con) -> list[dict]:
    """One row per repertoire drill for the openings page: opening, position, topic and learning state."""
    now = int(time.time())
    st = {r["key"]: r for r in con.execute("SELECT * FROM drill_state")}
    rows = []
    for d in repertoire.all_drills():
        s = st.get(d["key"])
        state = ("new" if s is None else "learned" if s["box"] >= 2 else "due" if s["due"] <= now else "work")
        rows.append(dict(group=tr(d["group"]), moves=d["moves_text"], title=tr(d["title"]), state=state))
    return rows


def _puzzle_spec(p) -> dict:
    """What checking a move needs to know about a puzzle from a game."""
    return dict(fen=p["fen"], line=db.jloads(p["line"]) or [p["solution"]], accepted=json.loads(p["accepted"]),
                mistake=p["played_uci"], mistake_line=db.jloads(p["refutation"]) or [p["played_uci"]],
                eval_best=p["eval_best"])


def _drill_spec(d) -> dict:
    typical = d["typical_uci"]
    return dict(fen=d["fen"], line=d["line_uci"], accepted=d["accepted"], mistake=typical,
                mistake_line=d["refutation_uci"] or ([typical] if typical else []), eval_best=d.get("eval_best"))


def _ucis(board: chess.Board, ucis: list[str]) -> list[chess.Move]:
    b, out = board.copy(stack=False), []
    for u in ucis:
        try:
            m = chess.Move.from_uci(u)
        except ValueError:
            break
        if m not in b.legal_moves:
            break
        out.append(m)
        b.push(m)
    return out


def _reasons(spec: dict) -> dict:
    """Why the game move was bad and what the best line does (for the explanation after a puzzle)."""
    board = chess.Board(spec["fen"])
    out = dict(why_best=explain.merit(board, _ucis(board, spec["line"])), why_played=[])
    if spec["mistake"]:
        played = chess.Move.from_uci(spec["mistake"])
        if played in board.legal_moves:
            after = board.copy(stack=False)
            after.push(played)
            out["why_played"] = explain.punishment(board, played, _ucis(after, spec["mistake_line"][1:]))
    return out


class _LangLoader(BaseLoader):
    """Serves "en/index.html" etc.: the German template with every known fragment translated."""
    def __init__(self, inner):
        self.inner = inner

    def get_source(self, env, template):
        lang, _, name = template.partition("/")
        if name and lang in LANGS and lang != "de":
            src, fn, uptodate = self.inner.get_source(env, name)
            src = translate_source(src, lang)
            src = re.sub(r'(\{%-?\s*(?:extends|from|import|include)\s+)"([^"]+\.html)"', rf'\1"{lang}/\2"', src)
            return src, fn, uptodate
        return self.inner.get_source(env, template)

    def list_templates(self):
        return self.inner.list_templates()


def _js_keys() -> list[str]:
    """German keys that the static scripts translate with t('...')."""
    keys: list[str] = []
    for f in sorted((Path(__file__).parent / "static").glob("*.js")):
        keys += [k.replace("\\'", "'") for k in re.findall(r"\bt\('((?:[^'\\]|\\.)*)'", f.read_text(encoding="utf-8"))]
    return keys


def create_app(db_path=None, depth: int = 14, workers: int = 0, password: str | None = None,
               auto_sync: int = 0, multi: MultiConfig | None = None) -> Flask:
    """password: if set, every page requires a login.  auto_sync: fetch + analyse every N minutes.

    multi: public mode.  Every visitor enters a Chess.com username and gets a private database of their own;
    analyses run one after another in a queue.  (password / auto_sync / db_path are not used in this mode.)"""
    app = Flask(__name__)
    app.jinja_env.loader = _LangLoader(app.jinja_env.loader)

    def render_template(tpl, /, **kw):
        lang = get_lang()
        return _render(tpl if lang == "de" else f"{lang}/{tpl}", **kw)
    if os.environ.get("CHESSTRAINER_PROXY"):              # behind Caddy / nginx: trust X-Forwarded-*
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    job = {"running": False, "msg": "", "done": 0, "total": 0, "error": None, "last": None}

    if multi:
        password = None
        depth = multi.depth
        workers = multi.workers
        store = UserStore(multi.data_dir)
        queue = JobQueue(store, multi)
        enter_limit, new_limit = RateLimiter(multi.enters_per_hour), RateLimiter(multi.new_per_hour)
        con = lock = None
        app.secret_key = store.secret_key()
        store.purge_old(multi.retention_days)
        if multi.autostart and multi.retention_days > 0:
            def _purge_loop():
                while True:
                    time.sleep(24 * 3600)
                    store.purge_old(multi.retention_days)
            threading.Thread(target=_purge_loop, daemon=True).start()
        app.config.update(SESSION_COOKIE_SECURE=bool(os.environ.get("CHESSTRAINER_HTTPS")))
    else:
        store = queue = None
        con = db.connect(db_path)
        lock = threading.RLock()                      # sqlite connection is shared across threads
        secret = db.get_setting(con, "secret_key")
        if not secret:
            secret = secrets.token_hex(32)
            db.set_setting(con, "secret_key", secret)
        app.secret_key = secret
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=timedelta(days=60))

    def ctx():
        """(connection, lock) of whoever is asking: the one database, or the signed-in player's own."""
        if not multi:
            return con, lock
        return store.open(session["u"])

    def q(sql, *a):
        c, l = ctx()
        with l:
            return c.execute(sql, a).fetchall()

    @app.template_filter("day")
    def day(ts):
        return fmt_date(ts) if ts else "?"

    js_keys = _js_keys()

    @app.before_request
    def pick_language():
        lang = request.cookies.get("lang")
        set_lang(lang if lang in LANGS else pick(request.headers.get("Accept-Language")))

    @app.context_processor
    def inject():
        lang = get_lang()
        cat = js_catalog(lang)
        return {"login_enabled": bool(password), "multi": bool(multi), "me": session.get("d") if multi else None,
                "contact": multi.contact if multi else "", "retention": multi.retention_days if multi else 0,
                "lang": lang, "langs": [(c, NAMES[c]) for c in LANGS],
                "i18n": {k: cat[k] for k in js_keys if k in cat}}

    @app.get("/lang/<code>")
    def choose_language(code):
        back = request.referrer or "/"
        host = request.host_url
        resp = redirect(back if back.startswith(host) or back.startswith("/") else "/")
        if code in LANGS:
            resp.set_cookie("lang", code, max_age=365 * 86400, samesite="Lax", secure=bool(os.environ.get("CHESSTRAINER_HTTPS")))
        return resp

    # ---------------- access control ----------------
    @app.before_request
    def guard():
        if multi:
            if request.endpoint in ("index", "enter", "privacy", "health", "static", "manifest", "service_worker", "offline", "choose_language"):
                return None
            if session.get("u") and store.exists(session["u"]):
                return None
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify(error="login required"), 401
            return redirect("/")
        if not password or session.get("ok"):
            return None
        if request.endpoint in ("login", "static", "manifest", "service_worker", "offline", "choose_language"):
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="login required"), 401
        return redirect(url_for("login", next=request.path))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not password:
            return redirect("/")
        error = False
        if request.method == "POST":
            if hmac.compare_digest(request.form.get("password", "").encode(), password.encode()):
                session.permanent = True
                session["ok"] = True
                nxt = request.args.get("next", "/")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
            time.sleep(1.5)                       # slow down guessing
            error = True
        return render_template("login.html", error=error), (401 if error else 200)

    # ---------------- multi-user: sign in by Chess.com name, sign out, delete my data ----------------
    def landing(error=None, status=200, notice=None, name=""):
        return render_template("landing.html", error=error, notice=notice, name=name), status

    @app.post("/enter")
    def enter():
        if not multi:
            abort(404)
        ip = request.remote_addr or "?"
        raw = request.form.get("username", "")
        try:
            if not enter_limit.allow(ip):
                raise TooManyRequests(Msg("Zu viele Versuche von dieser Adresse. Bitte warte etwas und versuche es später noch einmal."))
            name = normalize(raw)
            if store.exists(name):
                c, l = store.open(name)
                with l:
                    display = db.get_setting(c, "user") or name
            else:
                if store.count() >= multi.max_users:
                    raise Unavailable(Msg("REBLUNDER ist gerade voll. Bitte versuche es später noch einmal."))
                if not new_limit.allow(ip):
                    raise TooManyRequests(Msg("Von dieser Adresse wurden zu viele neue Spieler angelegt. Bitte versuche es später noch einmal."))
                display = lookup_player(name).get("username", name)
                store.create(name, display, multi.classes)
        except UserError as e:
            return landing(error=str(e), status=e.status, name=raw.strip()[:40])
        store.touch(name)
        session.clear()
        session.permanent = True
        session["u"], session["d"] = name, display
        queue.submit(name)                          # refreshes right away unless this player synced a moment ago
        return redirect("/")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect("/" if multi else "/login" if password else "/")

    @app.post("/leave")
    def leave():
        session.clear()
        return redirect("/")

    @app.post("/delete-me")
    def delete_me():
        if not multi:
            abort(404)
        name = session.get("u")
        if name:
            queue.cancel(name)
            store.delete(name)
        session.clear()
        return redirect("/?gone=1")

    @app.get("/privacy")
    def privacy():
        return render_template("privacy.html")

    @app.get("/health")
    def health():
        if not multi:
            abort(404)
        return jsonify(ok=True, users=store.count(), **queue.load())


    @app.get("/manifest.webmanifest")
    def manifest():
        return jsonify(name="REBLUNDER", short_name="REBLUNDER", id="/", start_url="/", scope="/", display="standalone",
                       description=tr("Puzzles aus deinen eigenen Schachfehlern"),
                       background_color="#0b0a0b", theme_color="#0b0a0b",
                       icons=[dict(src=url_for("static", filename="icon-192.png"), sizes="192x192", type="image/png", purpose="any"),
                              dict(src=url_for("static", filename="icon-512.png"), sizes="512x512", type="image/png", purpose="any")])

    @app.get("/sw.js")
    def service_worker():
        resp = app.send_static_file("sw.js")       # must be served from the root to control every page
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    @app.get("/offline")
    def offline():
        return render_template("offline.html")

    # ---------------- pages ----------------
    @app.get("/")
    def index():
        if multi and not (session.get("u") and store.exists(session["u"])):
            session.clear()
            return landing(notice=tr("Deine Daten wurden gelöscht.") if request.args.get("gone") else None)
        return render_template("index.html")

    @app.get("/report")
    def report_page():
        con, lock = ctx()
        with lock:
            r = report.build(con)
            recent = games.list_games(con, 6)
        return render_template("report.html", r=r, recent=recent, ctx=C.CONTEXTS)

    @app.get("/games")
    def games_page():
        con, lock = ctx()
        with lock:
            rows = games.list_games(con)
        return render_template("games.html", games=rows)

    @app.get("/game/<int:n>")
    def game_page(n: int):
        con, lock = ctx()
        with lock:
            g = games.detail(con, n)
        if g is None:
            abort(404)
        return render_template("game.html", g=g)

    @app.get("/openings")
    def openings_page():
        con, lock = ctx()
        with lock:
            o = openings.build(con)
            rep = repertoire.summary(con)
            drills = _drill_rows(con)
        return render_template("openings.html", o=o, rep=rep, drills=drills)

    @app.get("/progress")
    def progress_page():
        con, lock = ctx()
        with lock:
            o = progress.build(con)
        return render_template("progress.html", o=o)

    @app.get("/export/mistakes.<fmt>")
    def export(fmt: str):
        if fmt not in ("csv", "pgn"):
            abort(404)
        con, lock = ctx()
        with lock:
            body = games.export_csv(con) if fmt == "csv" else games.export_pgn(con)
        if fmt == "csv":
            body = "﻿" + body               # so Excel reads umlauts correctly
        name = f"fehler-{datetime.now().strftime('%Y-%m-%d')}.{fmt}"
        return Response(body, mimetype="text/csv" if fmt == "csv" else "application/x-chess-pgn",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ---------------- API ----------------
    @app.get("/api/stats")
    def stats():
        con, lock = ctx()
        with lock:
            c = puzzles.counts(con)
            c["user"] = db.get_setting(con, "user")
        c["job"] = queue.status(session["u"]) if multi else {k: (str(v) if isinstance(v, Msg) else v) for k, v in job.items()}
        c["multi"] = bool(multi)
        return jsonify(c)

    @app.get("/api/warmup")
    def warmup():
        con, lock = ctx()
        n = max(1, min(20, request.args.get("n", 5, type=int)))
        mode = request.args.get("mode")
        mode = mode if mode in ("opening", "repertoire") else "mixed"
        if mode == "repertoire":
            with lock:
                chosen = repertoire.pick(con, n)
                st = {r["key"]: r for r in con.execute("SELECT * FROM drill_state")}
            return jsonify([dict(
                id=d["key"], fen=d["fen"], turn=d["turn"], legal=_legal_map(chess.Board(d["fen"])), move_no=d["move_no"],
                date="", game=None, opponent="", drill=True, url=f"/api/drill/{d['key']}", group=tr(d["group"]),
                moves=d["moves_text"], box=st[d["key"]]["box"] if d["key"] in st else 0,
                goal=explain.realized_moves(d["fen"], d["line_uci"]),
                fails=st[d["key"]]["fails"] if d["key"] in st else 0) for d in chosen])
        with lock:
            rows = puzzles.pick(con, n, mode=mode)
            link = {r["id"]: con.execute("SELECT rowid AS n, opponent FROM games WHERE id=?", (r["game_id"],)).fetchone()
                    for r in rows}
        out = []
        for r in rows:
            board = chess.Board(r["fen"])
            out.append(dict(
                id=r["id"], fen=r["fen"], turn="white" if board.turn else "black",
                legal=_legal_map(board), move_no=board.fullmove_number,
                date=fmt_date(r["end_time"]) if r["end_time"] else "",
                game=link[r["id"]]["n"] if link[r["id"]] else None,
                opponent=link[r["id"]]["opponent"] if link[r["id"]] else "", box=r["box"], fails=r["fails"],
                goal=explain.realized_moves(r["fen"], db.jloads(r["line"]) or [r["solution"]])))
        return jsonify(out)

    def _puzzle(pid: int):
        rows = q("SELECT * FROM puzzles WHERE id=?", pid)
        return rows[0] if rows else None

    def _parse_move(board: chess.Board, uci: str) -> chess.Move | None:
        try:
            m = chess.Move.from_uci(uci)
        except ValueError:
            return None
        if m not in board.legal_moves:
            m2 = chess.Move.from_uci(uci + "q") if len(uci) == 4 else None
            if m2 is not None and m2 in board.legal_moves:
                return m2
            return None
        return m

    def _check(spec: dict):
        """Check one move of a puzzle.  body: {uci, step}; step = plies of the solution line already on the board.

        Multi-move puzzles continue along the stored best line until the advantage is on the board
        (explain.realized_moves).  A move that is not in the stored list is judged by the engine: about as good
        counts as solved, otherwise the answer says how the opponent punishes it."""
        body = request.get_json(silent=True) or {}
        goal = explain.realized_moves(spec["fen"], spec["line"])
        try:
            step = int(body.get("step", 0))
        except (TypeError, ValueError):
            step = -1
        line = spec["line"]
        if step < 0 or step % 2 or step >= 2 * goal or step > len(line) - 1:
            return jsonify(error="bad step"), 400
        board = chess.Board(spec["fen"])
        for u in line[:step]:
            board.push(chess.Move.from_uci(u))
        move = _parse_move(board, body.get("uci", ""))
        if move is None:
            return jsonify(error="illegal move"), 400
        after = board.copy(stack=False)
        after.push(move)
        expected = line[step]
        mistake = step == 0 and move.uci() == spec["mistake"]
        correct = not mistake and ((move.uci() in spec["accepted"]) if step == 0 else move.uci() == expected)
        correct = correct or after.is_checkmate()
        pv: list[chess.Move] = []
        if mistake:
            pv = _ucis(after, spec["mistake_line"][1:])
        elif not correct:
            c, l = ctx()
            with l:
                eng = analyze.find_engine(None, c)
            res = JUDGE.evaluate(eng, board, move, depth)
            thr = accept_threshold(spec["eval_best"])
            if res is not None and thr is not None and C.winpct(res["cp"]) >= thr:
                correct = True
            elif res is not None:
                pv = res["pv"]
        out = dict(correct=correct, uci=move.uci(), san=board.san(move), fen_after=after.fen(),
                   original_mistake=mistake, step=step, goal=goal)
        if correct:
            out["alt"] = move.uci() != expected
            if out["alt"]:
                out["best_san"] = board.san(chess.Move.from_uci(expected))
            if not out["alt"] and step + 2 < 2 * goal and step + 1 < len(line):
                reply = chess.Move.from_uci(line[step + 1])
                out.update(done=False, reply=reply.uci(), reply_san=after.san(reply), next_step=step + 2)
                after.push(reply)
                out.update(fen_reply=after.fen(), legal=_legal_map(after))
            else:
                out["done"] = True
        else:
            out["why"] = explain.punishment(board, move, pv)
            if pv and pv[0] in after.legal_moves:
                out.update(refute=pv[0].uci(), refute_san=after.san(pv[0]))
        return jsonify(out)

    @app.post("/api/puzzle/<int:pid>/check")
    def check(pid: int):
        p = _puzzle(pid)
        if p is None:
            return jsonify(error="unknown puzzle"), 404
        return _check(_puzzle_spec(p))

    @app.post("/api/drill/<key>/check")
    def drill_check(key: str):
        d = repertoire.get(key)
        if d is None:
            return jsonify(error="unknown drill"), 404
        return _check(_drill_spec(d))

    @app.post("/api/drill/<key>/finish")
    def drill_finish(key: str):
        d = repertoire.get(key)
        if d is None:
            return jsonify(error="unknown drill"), 404
        body = request.get_json(silent=True) or {}
        con, lock = ctx()
        with lock:
            st = repertoire.record(con, key, bool(body.get("first_try")))
        board = chess.Board(d["fen"])
        after = board.copy()
        after.push(chess.Move.from_uci(d["solution"]))
        return jsonify(
            drill=True, link="", opening=tr(d["group"]), opponent="", game=None, fen=d["fen"],
            loss=d.get("loss"), eval_best=d.get("eval_best"), eval_played=d.get("eval_typical"),
            best_line=games._steps(d["fen"], d["line_uci"]),
            your_line=games._steps(d["fen"], d["refutation_uci"]) if d["refutation_uci"] else [],
            solution=d["solution"], solution_san=d["solution_san"], fen_after=after.fen(),
            played_san=d.get("typical"), time_spent=None, severity=None,
            motifs=[dict(tag="drill", title=tr(d["title"]), hint=tr(d["hint"]))], context=[],
            next_due=st["due"], box=st["box"], **_reasons(_drill_spec(d)))

    @app.post("/api/puzzle/<int:pid>/finish")
    def finish(pid: int):
        p = _puzzle(pid)
        if p is None:
            return jsonify(error="unknown puzzle"), 404
        body = request.get_json(silent=True) or {}
        con, lock = ctx()
        with lock:
            st = puzzles.record(con, pid, bool(body.get("first_try")), int(body.get("attempts", 1)),
                                body.get("ms"))
        if p["line"] is None:                     # older puzzle: compute its variations now
            try:
                with lock:
                    eng = analyze.find_engine(None, con)
                    if eng:
                        analyze.lines_for_puzzle(con, pid, eng, depth)
                p = _puzzle(pid)
            except Exception:
                pass
        board = chess.Board(p["fen"])
        sol = chess.Move.from_uci(p["solution"])
        after = board.copy()
        after.push(sol)
        tags = json.loads(p["tags"])
        g = q("SELECT rowid AS n, pgn, opponent FROM games WHERE id=?", p["game_id"])
        meta = openings.game_meta(g[0]) if g else {"link": "", "family": "", "opponent": ""}
        return jsonify(
            link=meta["link"], opening=meta["family"], opponent=meta["opponent"], game=g[0]["n"] if g else None,
            fen=p["fen"], loss=p["loss"], eval_best=p["eval_best"], eval_played=p["eval_played"],
            best_line=games._steps(p["fen"], db.jloads(p["line"]) or [p["solution"]]),
            your_line=games._steps(p["fen"], db.jloads(p["refutation"]) or [p["played_uci"]]),
            solution=p["solution"], solution_san=p["solution_san"], fen_after=after.fen(),
            played_san=p["played_san"], time_spent=p["time_spent"], severity=p["severity"],
            motifs=[dict(tag=t, title=C.MOTIFS[t][0], hint=C.MOTIFS[t][1]) for t in tags if t in C.MOTIFS],
            context=[C.CONTEXTS[c] for c in json.loads(p["context"]) if c in C.CONTEXTS],
            next_due=st["due"], box=st["box"], **_reasons(_puzzle_spec(p)))

    # ---------------- background sync + analysis ----------------
    def _job():
        try:
            local = db.connect(db_path)           # own connection for this thread
            user = db.get_setting(local, "user")
            classes = tuple((db.get_setting(local, "classes") or "rapid,blitz").split(","))
            if not user:
                raise RuntimeError(Msg("Kein Benutzername gesetzt. Starte `python -m chesstrainer sync --user NAME`."))
            job.update(msg=Msg("Lade deine Partien von Chess.com …"), done=0, total=0)
            n = fetch.sync_user(local, user, months=2, classes=classes, max_games=200, log=lambda *_: None)
            eng = analyze.find_engine(None, local)
            if not eng:
                raise RuntimeError(Msg("Stockfish nicht gefunden (siehe README)."))
            job.update(msg=Msg("{n} neue Partien, analysiere …", n=n))
            nw = workers or max(1, (os.cpu_count() or 2) - 1)
            analyze.run_analysis(local, eng, depth=depth, workers=nw, log=lambda *_: None,
                                 progress=lambda d, t: job.update(done=d, total=t, msg=Msg("Analysiere Partie {d} von {t} …", d=d, t=t)))
            if analyze.missing_lines(local):
                job.update(msg=Msg("Ergänze Varianten …"), done=0, total=0)
                analyze.refresh(local, eng, depth=depth, workers=nw, log=lambda *_: None,
                                progress=lambda d, t: job.update(done=d, total=t, msg=Msg("Varianten {d} von {t} …", d=d, t=t)))
            job.update(msg="", error=None, last=int(time.time()), new=n)
        except Exception as e:                      # surfaced in the UI
            job.update(error=e.args[0] if e.args and isinstance(e.args[0], Msg) else str(e), msg="")
        finally:
            job["running"] = False

    def start_job() -> bool:
        if job["running"]:
            return False
        job.update(running=True, error=None, msg=Msg("Starte …"), done=0, total=0)
        threading.Thread(target=_job, daemon=True).start()
        return True

    @app.post("/api/sync")
    def sync():
        if multi:
            return jsonify(**queue.submit(session["u"]))
        return jsonify(started=start_job())

    if auto_sync and auto_sync > 0 and not multi:
        def loop():
            while True:
                start_job()
                time.sleep(max(2, auto_sync) * 60)
        threading.Thread(target=loop, daemon=True).start()

    app.store, app.queue = store, queue               # (multi-user mode only; handy for tests and tooling)
    return app
