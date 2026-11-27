"""Download games from the public Chess.com API (no login needed) or import a PGN file."""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Iterable

import chess.pgn

API = "https://api.chess.com/pub/player/{user}/games/archives"
UA = "REBLUNDER/1.0 (%s)" % (os.environ.get("CHESSTRAINER_CONTACT") or "personal training tool")
DEFAULT_CLASSES = ("rapid", "blitz")


def _get_json(url: str, retries: int = 4) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last}")


def parse_time_control(tc: str | None) -> tuple[int, int]:
    """'600' -> (600, 0); '180+2' -> (180, 2); daily '1/86400' -> (0, 0)."""
    if not tc or "/" in tc:
        return 0, 0
    m = re.fullmatch(r"(\d+)(?:\+(\d+))?", tc.strip())
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2) or 0)


def _result_for(user_color: str, white_res: str, black_res: str) -> str:
    mine = white_res if user_color == "white" else black_res
    if mine == "win":
        return "win"
    draws = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
    if mine in draws:
        return "draw"
    return "loss"


def game_row_from_api(g: dict, user: str) -> dict | None:
    """Convert one Chess.com API game dict to a DB row, or None if it should be skipped."""
    if g.get("rules", "chess") != "chess" or not g.get("pgn"):
        return None
    w, b = g["white"], g["black"]
    u = user.lower()
    if w["username"].lower() == u:
        color, me, opp = "white", w, b
    elif b["username"].lower() == u:
        color, me, opp = "black", b, w
    else:
        return None
    base, inc = parse_time_control(g.get("time_control"))
    return dict(
        id=g.get("uuid") or g.get("url"),
        end_time=g.get("end_time", 0),
        user_color=color,
        time_class=g.get("time_class", ""),
        base_secs=base,
        inc_secs=inc,
        result=_result_for(color, w["result"], b["result"]),
        opponent=opp["username"],
        user_rating=me.get("rating"),
        opp_rating=opp.get("rating"),
        pgn=g["pgn"],
    )


def store_games(con: sqlite3.Connection, rows: Iterable[dict]) -> int:
    n = 0
    for r in rows:
        cur = con.execute(
            "INSERT OR IGNORE INTO games(id,end_time,user_color,time_class,base_secs,inc_secs,"
            "result,opponent,user_rating,opp_rating,pgn) VALUES(:id,:end_time,:user_color,"
            ":time_class,:base_secs,:inc_secs,:result,:opponent,:user_rating,:opp_rating,:pgn)",
            r,
        )
        n += cur.rowcount
    con.commit()
    return n


def sync_user(con: sqlite3.Connection, user: str, months: int = 3,
              classes: tuple[str, ...] = DEFAULT_CLASSES, max_games: int = 400,
              log=print) -> int:
    """Download the newest `months` monthly archives. Returns number of new games stored."""
    archives = _get_json(API.format(user=user.lower()))["archives"]
    archives = archives[-months:]
    rows: list[dict] = []
    for url in reversed(archives):  # newest first
        log(f"  lade {url.split('/games/')[-1]} ...")
        data = _get_json(url)
        month_rows = []
        for g in data.get("games", []):
            if g.get("time_class") not in classes:
                continue
            row = game_row_from_api(g, user)
            if row:
                month_rows.append(row)
        month_rows.sort(key=lambda r: r["end_time"], reverse=True)
        rows.extend(month_rows)
        time.sleep(0.4)  # be polite to the API
        if len(rows) >= max_games:
            break
    return store_games(con, rows[:max_games])


def import_pgn(con: sqlite3.Connection, text: str, user: str,
               classes: tuple[str, ...] | None = None) -> int:
    """Import games from PGN text (e.g. a Chess.com/Lichess export)."""
    stream = io.StringIO(text)
    rows = []
    idx = 0
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        idx += 1
        h = game.headers
        white, black = h.get("White", ""), h.get("Black", "")
        if user.lower() == white.lower():
            color = "white"
        elif user.lower() == black.lower():
            color = "black"
        else:
            continue
        base, inc = parse_time_control(h.get("TimeControl"))
        res = h.get("Result", "*")
        result = "draw" if res == "1/2-1/2" else (
            "win" if (res == "1-0") == (color == "white") and res in ("1-0", "0-1") else "loss")
        site = h.get("Link") or h.get("Site", "") or f"import-{idx}"
        gid = f"{site}#{h.get('Date', '')}-{h.get('StartTime', '')}-{idx}"
        exporter = io.StringIO()
        game.accept(chess.pgn.FileExporter(exporter))
        elo = h.get("WhiteElo" if color == "white" else "BlackElo", "")
        oelo = h.get("BlackElo" if color == "white" else "WhiteElo", "")
        rows.append(dict(
            id=gid, end_time=_pgn_time(h), user_color=color, time_class="imported",
            base_secs=base, inc_secs=inc, result=result,
            opponent=black if color == "white" else white,
            user_rating=int(elo) if elo.isdigit() else None,
            opp_rating=int(oelo) if oelo.isdigit() else None,
            pgn=exporter.getvalue(),
        ))
    return store_games(con, rows)


def _pgn_time(h) -> int:
    import calendar
    d = h.get("UTCDate") or h.get("Date") or "1970.01.01"
    t = h.get("UTCTime") or h.get("EndTime") or "00:00:00"
    try:
        y, m, dd = (int(x) for x in d.split("."))
        hh, mm, ss = (int(x) for x in t.split(":"))
        return calendar.timegm((y, m, dd, hh, mm, ss))
    except Exception:
        return 0
