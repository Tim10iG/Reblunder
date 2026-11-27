"""Opening analysis: which openings you play, how you come out of them, and recurring leaks."""
from __future__ import annotations

import io
import re
import sqlite3
from collections import Counter, defaultdict

import chess
import chess.pgn

from .i18n import tr

OPENING_MOVES = 10          # the report looks at your first N moves (same as phase 'opening')
_FAMILY_END = {"Defense", "Game", "Opening", "Gambit", "Attack", "System"}
_APOS = {"Kings": "King's", "Queens": "Queen's", "Bishops": "Bishop's", "Petrovs": "Petrov's",
         "Alekhines": "Alekhine's", "Birds": "Bird's", "Larsens": "Larsen's", "Owens": "Owen's"}
_HDR = re.compile(r'\[(\w+) "([^"]*)"\]')
_cache: dict[str, list] = {}


def headers(pgn: str) -> dict[str, str]:
    return dict(_HDR.findall(pgn.split("\n\n", 1)[0]))


def opening_name(h: dict[str, str]) -> str:
    """Readable opening name from Chess.com's ECOUrl header (falls back to Opening / ECO)."""
    url = h.get("ECOUrl", "")
    if "/openings/" not in url:
        return h.get("Opening") or h.get("ECO") or tr("Unbekannt")
    words = []
    for tok in url.rsplit("/", 1)[-1].split("-"):
        if "..." in tok:
            tok = tok.split("...")[0]
            if tok.isalpha():
                words.append(tok)
            break
        if not tok or tok[0].isdigit():
            break
        words.append(tok)
    while words and words[-1].lower() in ("with", "and"):
        words.pop()
    name = " ".join(_APOS.get(w, w) for w in words)
    return name.replace("Caro Kann", "Caro-Kann").replace("Nimzo Indian", "Nimzo-Indian") or tr("Unbekannt")


def family_of(name: str) -> str:
    """Coarse family, e.g. 'Sicilian Defense Open Accelerated Dragon' -> 'Sicilian Defense'."""
    if "Sicilian" in name:
        return "Sicilian Defense"
    if name.startswith("Giuoco Piano"):
        return "Italian Game"
    words = name.split()
    for i, w in enumerate(words):
        if w in _FAMILY_END:
            return " ".join(words[: i + 1])
    return name


def _family_keys() -> dict[str, str]:
    from .strings.opening_names import ROWS
    return {en.replace("’", "'"): de for de, en, *_ in ROWS}


_FAMILY_DE = _family_keys()


def local_name(name: str) -> str:
    """Opening name in the UI language: the family is translated, the variation (mostly proper names) is kept,
    e.g. 'Sicilian Defense Alapin Variation' -> 'Sizilianische Verteidigung: Alapin Variation'."""
    fam = family_of(name)
    de = _FAMILY_DE.get(fam)
    if de is None:
        return name
    rest = name[len(fam):].strip() if name.startswith(fam) else ("" if name == fam else name)
    return tr(de) + (f": {rest}" if rest else "")


def game_meta(row: sqlite3.Row) -> dict:
    h = headers(row["pgn"])
    name = opening_name(h)
    link = h.get("Link", "")
    return dict(name=local_name(name), family=local_name(family_of(name)), eco=h.get("ECO", ""), game=row["n"] if "n" in row.keys() else None,
                link=link if link.startswith("https://") else "", opponent=row["opponent"] or "")


def opening_positions(game_id: str, pgn: str, user_white: bool) -> list[tuple[int, str, str]]:
    """[(ply, epd, fen)] for the positions before each of the user's first OPENING_MOVES moves."""
    if game_id in _cache:
        return _cache[game_id]
    out = []
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is not None and "SetUp" not in game.headers:
        board = game.board()
        for ply, move in enumerate(game.mainline_moves()):
            if board.fullmove_number > OPENING_MOVES:
                break
            if (board.turn == chess.WHITE) == user_white:
                out.append((ply, board.epd(), board.fen()))
            board.push(move)
    _cache[game_id] = out
    return out


def _score(results: Counter) -> float:
    n = sum(results.values())
    return 100.0 * (results["win"] + 0.5 * results["draw"]) / n if n else 0.0


def build(con: sqlite3.Connection) -> dict:
    games = con.execute("SELECT rowid AS n, * FROM games WHERE analyzed=1 ORDER BY end_time DESC").fetchall()
    if not games:
        return {"games": 0}
    moves_by_game: dict[str, dict[int, sqlite3.Row]] = defaultdict(dict)
    for m in con.execute("SELECT * FROM moves WHERE move_no<=?", (OPENING_MOVES,)):
        moves_by_game[m["game_id"]][m["ply"]] = m

    sides = {}
    for color in ("white", "black"):
        fam: dict[str, dict] = {}
        pos: dict[str, list] = defaultdict(list)
        singles = []
        total = Counter()
        n_games = err_total = 0
        exits = []
        for g in games:
            if g["user_color"] != color:
                continue
            meta = game_meta(g)
            mv = moves_by_game.get(g["id"], {})
            if not mv:
                continue
            n_games += 1
            f = fam.setdefault(meta["family"], dict(family=meta["family"], results=Counter(), errors=0,
                                                    inacc=0, exits=[], variations=Counter()))
            f["results"][g["result"]] += 1
            f["variations"][meta["name"]] += 1
            total[g["result"]] += 1
            last = mv[max(mv)]
            if last["move_no"] >= OPENING_MOVES - 2:        # game actually left the opening
                f["exits"].append(last["win_after"])
                exits.append(last["win_after"])
            for ply, epd, fen in opening_positions(g["id"], g["pgn"], color == "white"):
                m = mv.get(ply)
                if m is None:
                    continue
                bad = m["cls"] in ("mistake", "blunder")
                f["errors"] += bad
                err_total += bad
                f["inacc"] += m["cls"] == "inaccuracy"
                entry = dict(fen=fen, san=m["san"], uci=m["uci"], best_san=m["best_san"], best_uci=m["best_uci"],
                             loss=m["loss"], cls=m["cls"], move_no=m["move_no"], end_time=g["end_time"], ply=ply,
                             time_spent=m["time_spent"], **meta)
                pos[epd].append(entry)
                if bad:
                    singles.append(entry)

        fams = []
        for f in fam.values():
            n = sum(f["results"].values())
            fams.append(dict(
                family=f["family"], games=n, wins=f["results"]["win"], draws=f["results"]["draw"],
                losses=f["results"]["loss"], score=_score(f["results"]), errors=f["errors"],
                err_per_game=f["errors"] / n, inacc_per_game=f["inacc"] / n,
                exit_win=sum(f["exits"]) / len(f["exits"]) if f["exits"] else None,
                variations=[dict(name=k, n=v) for k, v in f["variations"].most_common(4)]))
        fams.sort(key=lambda d: (-d["games"], d["family"]))

        leaks = []
        for epd, entries in pos.items():
            total_loss = sum(e["loss"] for e in entries)
            if len(entries) < 2 or total_loss < 10 or max(e["loss"] for e in entries) < 5:
                continue
            by_move: dict[str, list] = defaultdict(list)
            for e in entries:
                by_move[e["san"]].append(e)
            played = sorted((dict(san=s, uci=es[0]["uci"], n=len(es), loss=sum(x["loss"] for x in es) / len(es))
                             for s, es in by_move.items()), key=lambda d: -d["n"] * d["loss"])
            best = Counter((e["best_san"], e["best_uci"]) for e in entries if e["best_uci"]).most_common(1)
            worst = played[0]
            if worst["loss"] < 5 or not best or best[0][0][1] == worst["uci"]:
                continue
            leaks.append(dict(fen=entries[0]["fen"], n=len(entries), total_loss=total_loss, played=played,
                              best_san=best[0][0][0], best_uci=best[0][0][1], family=entries[0]["family"],
                              events=[dict(end_time=e["end_time"] or 0, san=e["san"], loss=e["loss"]) for e in entries],
                              move_no=entries[0]["move_no"], last_seen=max(e["end_time"] or 0 for e in entries),
                              link=entries[0]["link"], game=entries[0]["game"], ply=entries[0]["ply"]))
        leaks.sort(key=lambda d: -d["total_loss"])
        singles.sort(key=lambda e: (-(e["end_time"] or 0), e["move_no"]))
        sides[color] = dict(
            games=n_games, score=_score(total), errors=err_total,
            err_per_game=err_total / n_games if n_games else 0.0,
            exit_win=sum(exits) / len(exits) if exits else None,
            families=fams, leaks=leaks[:8], all_leaks=leaks, singles=singles[:18], n_singles=len(singles))
    return {"games": len(games), "white": sides["white"], "black": sides["black"],
            "opening_moves": OPENING_MOVES, "insights": _insights(sides)}


def _insights(sides: dict) -> list[str]:
    out = []
    for color, label in (("white", tr("Mit Weiß")), ("black", tr("Mit Schwarz"))):
        s = sides[color]
        if not s["games"]:
            continue
        main = s["families"][0]
        kw = dict(label=label, family=main["family"], g=main["games"], tot=s["games"], score=f"{main['score']:.0f}",
                  err=f"{main['err_per_game']:.1f}", m=OPENING_MOVES)
        if main["exit_win"] is not None:
            out.append(tr("{label} spielst du am häufigsten {family} ({g} von {tot} Partien, {score} % der Punkte, "
                          "{err} Eröffnungsfehler pro Partie, Ø Gewinnchance nach Zug {m}: {exit} %).",
                          exit=f"{main['exit_win']:.0f}", **kw))
        else:
            out.append(tr("{label} spielst du am häufigsten {family} ({g} von {tot} Partien, {score} % der Punkte, "
                          "{err} Eröffnungsfehler pro Partie).", **kw))
        cand = [f for f in s["families"] if f["games"] >= 3 and f["exit_win"] is not None]
        if len(cand) >= 2:
            worst = min(cand, key=lambda f: f["exit_win"])
            if worst["exit_win"] <= 47 and worst is not main:
                out.append(tr("{label} kommst du in der Eröffnung {family} am schlechtesten aus den ersten Zügen "
                              "(Ø {exit} % nach Zug {m}, {g} Partien).", label=label, family=worst["family"],
                              exit=f"{worst['exit_win']:.0f}", m=OPENING_MOVES, g=worst["games"]))
        if s["leaks"]:
            lk = s["leaks"][0]
            p = lk["played"][0]
            out.append(tr("{label} wiederholt sich ein Fehler: in derselben Stellung (Zug {n}, {k}× erreicht) spielst du "
                          "{san} statt {best} (Ø −{loss} Prozentpunkte).", label=label, n=lk["move_no"], k=lk["n"],
                          san=p["san"], best=lk["best_san"], loss=f"{p['loss']:.0f}"))
    return out
