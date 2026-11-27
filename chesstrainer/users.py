"""Multi-user mode: one SQLite database per Chess.com player, plus the small helpers around it.

Players are identified by their (public) Chess.com username only.  There is no password: everything shown is
derived from public games.  A player can delete all of their data at any time.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
import time
import urllib.error
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from . import db, fetch
from .i18n import Msg

NAME_RE = re.compile(r"[a-z0-9_-]{3,25}")        # Chess.com: letters, digits, "_" and "-", 3-25 characters
PLAYER_API = "https://api.chess.com/pub/player/{user}"


class UserError(Exception):
    """Something the player should be told (message is German, shown in the UI)."""
    status = 400


class InvalidName(UserError):
    status = 400


class UnknownPlayer(UserError):
    status = 404


class Unavailable(UserError):
    status = 503


class TooManyRequests(UserError):
    status = 429


@dataclass
class MultiConfig:
    data_dir: Path | str = "data"
    depth: int = 12                 # lower than single-user mode: many players share one machine
    workers: int = 1                # engine processes used by the (single) analysis job
    months: int = 2                 # monthly archives to read per sync
    max_games: int = 40             # newest games analysed per sync
    classes: str = "rapid,blitz"
    max_users: int = 300
    cooldown_min: int = 15          # minimum time between two manual syncs of the same player
    max_queue: int = 40
    retention_days: int = 90        # delete players who have not visited for this long (0 = never)
    new_per_hour: int = 6           # new players one IP address may create per hour
    enters_per_hour: int = 40       # sign-in attempts per IP address per hour
    autostart: bool = True          # start the background worker (tests switch this off)
    contact: str = ""               # shown on the privacy page


def normalize(raw: str) -> str:
    """'  Alice_99 ' -> 'alice_99'; raises InvalidName for anything that is not a Chess.com username."""
    name = (raw or "").strip().lower()
    if not NAME_RE.fullmatch(name):
        raise InvalidName(Msg("Das ist kein gültiger Chess.com-Benutzername (3 bis 25 Zeichen: Buchstaben, Zahlen, _ und -)."))
    return name


def lookup_player(name: str) -> dict:
    """Ask Chess.com whether the player exists.  Returns the profile (with the canonical 'username')."""
    try:
        prof = fetch._get_json(PLAYER_API.format(user=name), retries=2)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UnknownPlayer(Msg("Auf Chess.com gibt es keinen Spieler „{name}“.", name=name)) from None
        raise Unavailable(Msg("Chess.com antwortet gerade nicht. Bitte versuche es in ein paar Minuten noch einmal.")) from None
    except Exception:
        raise Unavailable(Msg("Chess.com ist gerade nicht erreichbar. Bitte versuche es in ein paar Minuten noch einmal.")) from None
    if not isinstance(prof, dict) or "username" not in prof:
        raise Unavailable(Msg("Chess.com hat unerwartet geantwortet. Bitte versuche es später noch einmal."))
    return prof


class RateLimiter:
    """At most `limit` hits per key within `window` seconds (in memory, per process)."""

    def __init__(self, limit: int, window: float = 3600.0):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self.lock:
            q = self.hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > 5000:                 # forget idle keys
                for k in [k for k, v in self.hits.items() if not v][:1000]:
                    del self.hits[k]
            return True


class UserStore:
    """Opens, creates and deletes the per-player databases (data_dir/users/<name>.db)."""

    def __init__(self, root: Path | str, max_open: int = 40):
        self.root = Path(root)
        self.dir = self.root / "users"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_open = max_open
        self._open: OrderedDict[str, tuple] = OrderedDict()
        self._lock = threading.Lock()

    # -- paths ---------------------------------------------------------------------------------------------------
    def path(self, name: str) -> Path:
        return self.dir / f"{normalize(name)}.db"

    def exists(self, name: str) -> bool:
        try:
            return self.path(name).is_file()
        except InvalidName:
            return False

    def count(self) -> int:
        return sum(1 for _ in self.dir.glob("*.db"))

    def names(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.db"))

    # -- connections ---------------------------------------------------------------------------------------------
    def open(self, name: str):
        """(connection, lock) shared by all request threads of this process."""
        name = normalize(name)
        with self._lock:
            if name in self._open:
                self._open.move_to_end(name)
                return self._open[name]
            con = db.connect(self.path(name))
            entry = (con, threading.RLock())
            self._open[name] = entry
            while len(self._open) > self.max_open:
                old = next(iter(self._open))
                oc, ol = self._open[old]
                if old == name or not ol.acquire(blocking=False):   # never close a connection that is in use
                    break
                try:
                    del self._open[old]
                    oc.close()
                finally:
                    ol.release()
            return entry

    def _close(self, name: str) -> None:
        with self._lock:
            entry = self._open.pop(name, None)
        if entry:
            with entry[1]:
                entry[0].close()

    # -- lifecycle -----------------------------------------------------------------------------------------------
    def create(self, name: str, display: str, classes: str = "rapid,blitz") -> None:
        con, lock = self.open(name)
        with lock:
            db.set_setting(con, "user", display)
            db.set_setting(con, "classes", classes)
            db.set_setting(con, "created", str(int(time.time())))

    def touch(self, name: str) -> None:
        """Mark the player as active (the retention clock looks at the file's modification time)."""
        try:
            p = self.path(name)
            if p.is_file():                           # never re-create a database that was just deleted
                os.utime(p)
        except (InvalidName, OSError):
            pass

    def delete(self, name: str) -> bool:
        name = normalize(name)
        self._close(name)
        found = False
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = self.dir / f"{name}.db{suffix}"
            if p.exists():
                p.unlink()
                found = found or suffix == ""
        return found

    def purge_old(self, days: int, now: float | None = None) -> list[str]:
        """Delete players whose database has not been touched for `days` days."""
        if days <= 0:
            return []
        limit = (time.time() if now is None else now) - days * 86400
        gone = []
        for p in self.dir.glob("*.db"):
            if p.stat().st_mtime < limit:
                self.delete(p.stem)
                gone.append(p.stem)
        return gone

    # -- signing key for the session cookie ----------------------------------------------------------------------
    def secret_key(self) -> str:
        f = self.root / "secret_key"
        if f.is_file() and f.read_text().strip():
            return f.read_text().strip()
        key = secrets.token_hex(32)
        f.write_text(key)
        try:
            f.chmod(0o600)
        except OSError:
            pass
        return key
