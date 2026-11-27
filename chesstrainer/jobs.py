"""Multi-user mode: a queue that fetches and analyses one player at a time on a single background thread.

Stockfish is the expensive part, so players wait in line instead of running side by side.  Every player's
status uses the same shape as the single-user job dict (running / msg / done / total / error / new), so the
front end does not need to know which mode it talks to.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from .i18n import Msg
from . import analyze, db, fetch
from .users import MultiConfig, UserError, UserStore


class Cancelled(Exception):
    """The player deleted their data while the job was running."""


def _noop(*_a, **_k):
    return None


class JobQueue:
    def __init__(self, store: UserStore, cfg: MultiConfig):
        self.store, self.cfg = store, cfg
        self.cv = threading.Condition()
        self.waiting: deque[str] = deque()
        self.current: str | None = None
        self.state: dict[str, dict] = {}
        self._stop = False
        self.thread: threading.Thread | None = None
        if cfg.autostart:
            self.thread = threading.Thread(target=self._loop, name="reblunder-worker", daemon=True)
            self.thread.start()

    # ---------------------------------------------------------------- public API (called from request threads)
    def submit(self, name: str, force: bool = False) -> dict:
        """Put a player in line.  Returns {"started": bool, "reason": ...}."""
        with self.cv:
            if name == self.current:
                return dict(started=False, reason="running")
            if name in self.waiting:
                return dict(started=False, reason="queued")
            st = self.state.setdefault(name, dict(msg="", done=0, total=0, error=None, last=None, new=None, next_ok=0))
            now = time.time()
            if not force and now < st.get("next_ok", 0):
                return dict(started=False, reason="cooldown", minutes=max(1, int((st["next_ok"] - now) / 60 + 0.99)))
            if len(self.waiting) >= self.cfg.max_queue:
                return dict(started=False, reason="full")
            st.update(error=None, msg="", done=0, total=0, cancel=False)
            self.waiting.append(name)
            self.cv.notify()
            return dict(started=True, reason="queued")

    def status(self, name: str) -> dict:
        with self.cv:
            st = dict(self.state.get(name) or {})
            running = name == self.current
            queued = name in self.waiting
            msg = st.get("msg", "")
            if queued:
                ahead = list(self.waiting).index(name) + (1 if self.current else 0)
                msg = (Msg("Gleich dran …") if ahead == 0 else
                       Msg("Warteschlange: 1 Analyse vor dir") if ahead == 1 else Msg("Warteschlange: {n} Analysen vor dir", n=ahead))
            err = st.get("error")
            return dict(running=running or queued, queued=queued, msg=str(msg) if msg else "", done=st.get("done", 0),
                        total=st.get("total", 0), error=str(err) if err else err, last=st.get("last"), new=st.get("new"))

    def cancel(self, name: str) -> None:
        """Stop and forget a player's job (used when they delete their data)."""
        with self.cv:
            try:
                self.waiting.remove(name)
            except ValueError:
                pass
            if name == self.current and name in self.state:
                self.state[name]["cancel"] = True
            elif name in self.state:
                del self.state[name]

    def load(self) -> dict:
        with self.cv:
            return dict(waiting=len(self.waiting), running=self.current)

    def stop(self) -> None:
        with self.cv:
            self._stop = True
            self.cv.notify_all()

    # ---------------------------------------------------------------- worker
    def run_pending(self) -> int:
        """Process everything that is waiting, on the calling thread (used by tests)."""
        n = 0
        while True:
            with self.cv:
                if not self.waiting:
                    return n
                name = self.waiting.popleft()
                self.current = name
            self._run_one(name)
            n += 1

    def _loop(self) -> None:
        while True:
            with self.cv:
                while not self.waiting and not self._stop:
                    self.cv.wait()
                if self._stop:
                    return
                name = self.waiting.popleft()
                self.current = name
            self._run_one(name)

    def _run_one(self, name: str) -> None:
        st = self.state.setdefault(name, {})
        error = None
        try:
            self.run_user(name)
        except Cancelled:
            error = "cancelled"
        except UserError as e:
            error = e.args[0] if e.args else str(e)
        except Exception as e:                       # shown to the player, details stay in the server log
            print(f"[reblunder] Job für {name} fehlgeschlagen: {e!r}", flush=True)
            error = Msg("Die Analyse ist fehlgeschlagen. Bitte versuche es später noch einmal.")
        finally:
            with self.cv:
                self.current = None
                if error == "cancelled" or st.get("cancel"):
                    self.state.pop(name, None)
                else:
                    now = time.time()
                    st.update(msg="", error=error, last=int(now),
                              next_ok=now + (60 if error else self.cfg.cooldown_min * 60))
                    if error:
                        st["new"] = None

    def _set(self, name: str, **kw) -> None:
        with self.cv:
            st = self.state.setdefault(name, {})
            st.update(kw)
            if st.get("cancel"):
                raise Cancelled()

    def run_user(self, name: str) -> None:
        """Fetch the newest games of one player and analyse the ones that are new."""
        cfg = self.cfg
        if not self.store.exists(name):
            raise Cancelled()
        con = db.connect(self.store.path(name))
        try:
            user = db.get_setting(con, "user") or name
            classes = tuple((db.get_setting(con, "classes") or cfg.classes).split(","))
            self._set(name, msg=Msg("Lade deine Partien von Chess.com …"), done=0, total=0, new=None)
            n = fetch.sync_user(con, user, months=cfg.months, classes=classes, max_games=cfg.max_games, log=_noop)
            self._set(name)
            if con.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0:
                raise UserError(Msg("Wir haben keine Rapid- oder Blitz-Partien aus den letzten zwei Monaten gefunden. "
                                    "Spiele ein paar Partien und versuche es danach noch einmal."))
            eng = analyze.find_engine(None, con)
            if not eng:
                raise RuntimeError("Stockfish nicht gefunden")
            self._set(name, msg=Msg("{n} neue Partien, analysiere …", n=n))

            def tick(d, t):
                self._set(name, done=d, total=t, msg=Msg("Analysiere Partie {d} von {t} …", d=d, t=t))

            analyze.run_analysis(con, eng, depth=cfg.depth, workers=cfg.workers, limit=cfg.max_games,
                                 log=_noop, progress=tick)
            if analyze.missing_lines(con):
                self._set(name, msg=Msg("Ergänze Varianten …"), done=0, total=0)
                analyze.refresh(con, eng, depth=cfg.depth, workers=cfg.workers, log=_noop,
                                progress=lambda d, t: self._set(name, done=d, total=t, msg=Msg("Varianten {d} von {t} …", d=d, t=t)))
            self._set(name, new=n)
            self.store.touch(name)
        finally:
            con.close()
