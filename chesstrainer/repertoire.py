"""Repertoire drills: hand-picked opening positions for your own repertoire.

Each drill is a position you reach (or should aim for) in your openings, the move(s) to find there and,
where it applies, the move you tend to play instead. The positions come from recurring mistakes in your
analysed games; answers, variations and evaluations were checked with Stockfish (depth 20). Every move
within 4 percentage points of the best one counts as correct, the same rule as for the puzzles.

The drills do not touch the `puzzles` table, so reports, exports and statistics only ever show
mistakes from real games. Their spaced-repetition state lives in `drill_state`.

To add a drill: append to DRILLS. `moves`, `accept`, `line` and `typical` are SAN from the start
position (moves) or from the drill position (the others). Evaluations are centipawns from the
point of view of the side to move; `loss` is the lost win chance of `typical` in percentage points.
"""
from __future__ import annotations

import random
import sqlite3
import time
from functools import lru_cache

import chess

from . import puzzles

DRILLS: list[dict] = [
    # ---------------- White: 1.e4 e5 2.Nf3, Italian ----------------
    dict(key="w-bc5-nxe5", group="Italienisch: 2…Bc5", moves="e4 e5 Nf3 Bc5",
         accept=["Nxe5"], line="Nxe5 d6 Nf3 Nf6 d4 Bb6", eval_best=196,
         typical="Bc4", refutation="Bc4 Nc6 d3 Nf6 Nc3 O-O", eval_typical=22, loss=15,
         title="e5 ist ungedeckt",
         hint="Nach 2…Bc5 deckt niemand den Bauern e5. 3.Nxe5 gewinnt ihn einfach. Auf 3…Qh4 oder "
              "3…Qe7 kommt 4.d4 mit Tempo gegen den Läufer, auf 3…Bxf2+ nimmst du mit dem König. "
              "In deinen Partien hast du hier 3.Bc4 gespielt."),
    dict(key="w-bc5-bc4-nf6", group="Italienisch: 2…Bc5", moves="e4 e5 Nf3 Bc5 Bc4 Nf6",
         accept=["Nxe5"], line="Nxe5 O-O d4 Be7 Nd2 d6", eval_best=155,
         typical="c3", refutation="c3 Nxe4 d4 exd4 Qb3 O-O", eval_typical=-80, loss=21,
         title="Erst nehmen, dann bauen",
         hint="Auch nach 3.Bc4 Nf6 hängt e5 noch. 4.Nxe5 gewinnt den Bauern. Auf 4…Nxe4 schlägt "
              "5.Bxf7+ zurück und dein Springer steht stark. 4.c3 ist zu langsam: Schwarz nimmt "
              "seinerseits auf e4."),
    dict(key="w-bc5-bc4-c6", group="Italienisch: 2…Bc5", moves="e4 e5 Nf3 Bc5 Bc4 c6",
         accept=["Nxe5"], line="Nxe5 d5 exd5 cxd5 Bb5+ Kf8", eval_best=251,
         typical="O-O", refutation="O-O d6 c3 Ne7 d4 exd4", eval_typical=83, loss=14,
         title="Dasselbe Muster",
         hint="Schwarz hat schon wieder nichts für e5 getan. 4.Nxe5 gewinnt den Bauern, und nach "
              "…d5 exd5 cxd5 hast du mit Bb5+ ein Schach mit Tempo. Frage dich nach jedem gegnerischen "
              "Zug: Was ist jetzt ungedeckt?"),
    dict(key="w-bxf2-kxf2", group="Italienisch: 2…Bc5", moves="e4 e5 Nf3 Bc5 Bc4 Nf6 c3 Bxf2+",
         accept=["Kxf2"], line="Kxf2 Nxe4+ Kg1 Nc6 d4 d5", eval_best=313,
         typical="Kf1", refutation="Kf1 Bb6 d3 c6 Bg5 O-O", eval_typical=-321, loss=52,
         title="Nimm den Läufer",
         hint="…Bxf2+ ist ein Opfer, kein Angriff: Nimm mit dem König. Nach 5.Kxf2 Nxe4+ 6.Kg1 hast "
              "du eine Figur gegen einen Bauern mehr. Mit 5.Kf1 bist du einen Bauern weniger und "
              "darfst trotzdem nicht mehr rochieren. Kf1 statt Kxf2 hast du schon zweimal gespielt."),
    dict(key="w-petrov-bc5", group="Russisch: 2…Nf6", moves="e4 e5 Nf3 Nf6 Bc4 Bc5",
         accept=["Nxe5"], line="Nxe5 O-O d4 Be7 Qe2 c6", eval_best=158,
         typical="O-O", refutation="O-O d6 c3 Bb6 a4 a5", eval_typical=16, loss=13,
         title="Wieder e5",
         hint="Gleiches Bild wie nach 2…Bc5: e5 ist ungedeckt, 4.Nxe5 gewinnt einen Bauern. "
              "Auf 4…Nxe4 folgt 5.Bxf7+."),
    # ---------------- White: Two Knights 4.Ng5 ----------------
    dict(key="w-2n-be2", group="Zweispringerspiel 4.Ng5",
         moves="e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 d5 exd5 Na5 Bb5+ c6 dxc6 bxc6",
         accept=["Bd3", "Be2", "Qf3"], line="Be2 h6 Nf3 e4 Ne5 Bc5", eval_best=7,
         typical="Ba4", refutation="Ba4 h6 Nh3 Bg4 f3 Bxh3", eval_typical=-232, loss=21,
         title="Läufer zurück nach Hause",
         hint="Nach 7…bxc6 gehört der Läufer nach e2 (Hauptvariante), d3 oder Qf3 ist ebenfalls gut. "
              "Auf a4 steht er abseits, und nach …h6 muss dein Springer an den Rand nach h3. "
              "Du hast 8.Ba4 zweimal gespielt."),
    dict(key="w-traxler-bxf7", group="Zweispringerspiel 4.Ng5", moves="e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 Bc5",
         accept=["Bxf7+", "d4"], line="Bxf7+ Ke7 Bc4 Qe8 Nc3 Nd4", eval_best=96,
         title="Traxler: Läufer zuerst",
         hint="Gegen den Traxler (4…Bc5) nimmst du mit dem Läufer auf f7 (5.d4 geht auch). Nach 5.Nxf7 "
              "Bxf2+ wird es wild, und Schwarz gleicht mindestens aus."),
    dict(key="w-traxler-kf8", group="Zweispringerspiel 4.Ng5", moves="e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 Bc5 Bxf7+ Kf8",
         accept=["Bb3", "Bc4"], line="Bb3 Qe8 d3 d6 Nf3 a5", eval_best=149,
         typical="Nc3", refutation="Nc3 h6 Nf3 Kxf7 Nxe5+ Nxe5", eval_typical=-480, loss=49,
         title="Läufer retten",
         hint="Dein Läufer auf f7 hängt. Zieh ihn zurück (Bb3 oder Bc4), dann hast du einen sauberen "
              "Mehrbauern. Nach 6.Nc3? kommt …h6, und Schwarz gewinnt Material."),
    dict(key="w-traxler-ke7", group="Zweispringerspiel 4.Ng5", moves="e4 e5 Nf3 Nc6 Bc4 Nf6 Ng5 Bc5 Bxf7+ Ke7",
         accept=["Bc4", "Bd5", "Bb3"], line="Bc4 Qe8 Nc3 h6 Nf3 d6", eval_best=102,
         typical="Nc3", refutation="Nc3 h6 Bb3 hxg5 d3 Nd4", eval_typical=-343, loss=37,
         title="Gleiches Thema",
         hint="Auch nach 5…Ke7 steht der Läufer auf f7 im Angriff, und …h6 bedroht den Springer. "
              "Erst den Läufer zurückziehen, dann entwickeln."),
    dict(key="w-nd4-trap", group="Italienisch: 3…Nd4", moves="e4 e5 Nf3 Nc6 Bc4 Nd4",
         accept=["Nxd4", "O-O"], line="Nxd4 exd4 O-O c6 Re1 d6", eval_best=127,
         typical="Nxe5", refutation="Nxe5 Qg5 O-O Qxe5 c3 Ne6", eval_typical=-54, loss=16,
         title="Keine Gier auf e5",
         hint="3…Nd4 ist eine Falle (Blackburne-Kostić). 4.Nxe5? Qg5! greift e5 und g2 gleichzeitig "
              "an. Tausche einfach 4.Nxd4 exd4 und rochiere, oder rochiere sofort."),
    # ---------------- White: Scandinavian ----------------
    dict(key="w-scandi-nc3", group="Skandinavisch", moves="e4 d5 exd5 Qxd5",
         accept=["Nc3", "Nf3", "h3"], line="Nc3 Qa5 Nf3 Nf6 d4 Bf5", eval_best=68,
         title="Tempo gegen die Dame",
         hint="Die schwarze Dame steht früh im Zentrum. 3.Nc3 entwickelt mit Tempo, danach folgen d4, Nf3 "
              "und Bc4 oder Bd3. 3.Nf3 ist fast genauso gut, verschenkt aber das Tempo gegen die Dame."),
    dict(key="w-scandi-d4", group="Skandinavisch", moves="e4 d5 exd5 Qxd5 Nc3 Qe5+ Be2 Bg4",
         accept=["d4"], line="d4 Bxe2 Ngxe2 Qa5 b4 Qf5", eval_best=91,
         typical="Nf3", refutation="Nf3 Bxf3 gxf3 Nc6 d3 Nd4", eval_typical=-47, loss=13,
         title="d4 mit Tempo",
         hint="5.d4 greift die Dame an und öffnet deinem Läufer c1 den Weg. Nach 5.Nf3? tauscht Schwarz "
              "auf f3, und deine Bauern am Königsflügel sind zerrissen."),
    # ---------------- Black: Sicilian 1…c5 2…Nc6 ----------------
    dict(key="b-bowdler-qh5", group="Sizilianisch: 2.Bc4", moves="e4 c5 Bc4 Nc6 Qh5",
         accept=["e6"], line="e6 Nf3 Nf6 Qh4 d5 exd5", eval_best=141,
         typical="g6", refutation="g6 Qxc5 e6 Qe3 Bg7 Nf3", eval_typical=-42, loss=17,
         title="f7 decken, c5 halten",
         hint="Weiß droht Qxf7 matt. 3…e6 deckt f7 und macht den Läufer c4 stumpf. Nach 3…g6? schlägt "
              "die Dame den Bauern c5. Mit …Nf6 und …d5 vertreibst du die Dame dann mit Tempo."),
    dict(key="b-bowdler-qf3", group="Sizilianisch: 2.Bc4", moves="e4 c5 Bc4 Nc6 Qf3",
         accept=["e6", "Nf6", "Ne5"], line="e6 Ne2 Ne5 Qc3 Nxc4 Qxc4", eval_best=63,
         title="Gleiche Idee",
         hint="Wieder zielt Weiß auf f7. 3…e6 oder 3…Nf6 deckt in Ruhe, und mit …Ne5 oder …Nd4 "
              "gewinnst du ein Tempo gegen die Dame (3…Ne5 sofort geht auch)."),
    dict(key="b-bc4-ng5", group="Sizilianisch: 2.Nf3 Nc6 3.Bc4", moves="e4 c5 Nf3 Nc6 Bc4 g6 Ng5",
         accept=["e6"], line="e6 d3 Nge7 O-O Bg7 a4", eval_best=77,
         title="f7 schützen",
         hint="4.Ng5 und Bc4 zielen beide auf f7. 4…e6 schließt die Diagonale. Danach steht der Springer "
              "auf g5 ohne Ziel da."),
    dict(key="b-nxc6-d5", group="Sizilianisch: Nxc6 bxc6",
         moves="e4 c5 Nf3 Nc6 Bc4 g6 d4 cxd4 Nxd4 Bg7 Nxc6 bxc6 Qd3 e6 Nc3",
         accept=["d5", "Nf6", "Qc7"], line="d5 Bb3 a5 Ba4 Ne7 exd5", eval_best=24,
         typical="Ne7", refutation="Ne7 Be3 d5 O-O-O Qc7 Bb3", eval_typical=-69, loss=8,
         title="Nach Nxc6 bxc6 kommt …d5",
         hint="Wenn Weiß auf c6 tauscht, ist …d5 dein Plan. Der Bauer c6 deckt d5, du bekommst das "
              "Zentrum und schließt den Läufer c4 aus. In mehreren Partien hast du stattdessen erst "
              "…Ne7, …O-O oder …Rb8 gespielt."),
    dict(key="b-alapin-d5", group="Sizilianisch: 2.c3", moves="e4 c5 c3 Nc6 d4 cxd4 cxd4",
         accept=["d5", "e6", "e5"], line="d5 exd5 Qxd5 Nf3 e5 Nc3", eval_best=-46,
         typical="g6", refutation="g6 d5 Nb8 Qd4 Nf6 e5", eval_typical=-197, loss=13,
         title="Das Zentrum angreifen",
         hint="Gegen 2.c3 mit vollem Zentrum musst du sofort reagieren: 4…d5 (oder …e6 bzw. …e5). "
              "Nach 4…g6? 5.d5 wird dein Springer nach b8 zurückgejagt. Gegen 2.c3 hast du alle drei "
              "Partien verloren, und genau hier fing es an."),
    # ---------------- Black: 1.d4 ----------------
    dict(key="b-d4-nf6", group="Gegen 1.d4", moves="d4",
         accept=["Nf6", "d5", "e6", "c6", "d6", "g6", "f5", "a6"], line="Nf6 c4 c6 e3 d5 Nf3", eval_best=-23,
         typical="b6", refutation="b6 e4 e6 Nf3 d5 exd5", eval_typical=-86, loss=6,
         title="Kein 1…b6",
         hint="Mit 1…b6 überlässt du Weiß kampflos e4 und das ganze Zentrum. Das spielst du "
              "immer wieder. Spiel 1…Nf6 mit späterem …e6 und …d5: Damit nimmst du am Zentrum teil, und "
              "der Aufbau ist leicht zu lernen."),
]


@lru_cache(maxsize=None)
def _built() -> dict[str, dict]:
    """Resolve SAN to positions and UCI once; raises if a drill is broken (the tests check this)."""
    out = {}
    for d in DRILLS:
        board = chess.Board()
        for s in d["moves"].split():
            board.push_san(s)
        fen = board.fen()

        def uci_line(san_line: str | None) -> list[str]:
            if not san_line:
                return []
            b, res = board.copy(), []
            for s in san_line.split():
                m = b.parse_san(s)
                res.append(m.uci())
                b.push(m)
            return res

        accepted = [board.parse_san(s).uci() for s in d["accept"]]
        line = uci_line(d["line"])
        if line[0] not in accepted:
            raise ValueError(f"{d['key']}: line must start with an accepted move")
        out[d["key"]] = dict(
            d, fen=fen, accepted=accepted, solution=accepted[0], solution_san=d["accept"][0],
            line_uci=line, typical_uci=board.parse_san(d["typical"]).uci() if d.get("typical") else None,
            refutation_uci=uci_line(d.get("refutation")), turn="white" if board.turn else "black",
            move_no=board.fullmove_number, moves_text=_numbered(d["moves"]))
    return out


def _numbered(moves: str) -> str:
    parts = []
    for i, s in enumerate(moves.split()):
        parts.append(f"{i // 2 + 1}.{s}" if i % 2 == 0 else s)
    return " ".join(parts)


def get(key: str) -> dict | None:
    return _built().get(key)


def all_drills() -> list[dict]:
    return list(_built().values())


def _state(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["key"]: r for r in con.execute("SELECT * FROM drill_state")}


def pick(con: sqlite3.Connection, n: int = 5, now: int | None = None,
         rng: random.Random | None = None) -> list[dict]:
    """Due drills first (never seen, then failed, then lower boxes); at most two per opening group."""
    now = now or int(time.time())
    rng = rng or random.Random()
    st = _state(con)

    def key(d):
        s = st.get(d["key"])
        if s is None:
            return (0, 0, 0, rng.random())
        return (0 if s["due"] <= now else 1, s["box"], -s["fails"], rng.random())

    ordered = sorted(all_drills(), key=key)
    chosen, per_group = [], {}
    for d in ordered:
        if per_group.get(d["group"], 0) >= 2:
            continue
        chosen.append(d)
        per_group[d["group"]] = per_group.get(d["group"], 0) + 1
        if len(chosen) == n:
            break
    for d in ordered:                        # few groups left: fill up anyway
        if len(chosen) == n:
            break
        if d not in chosen:
            chosen.append(d)
    return chosen


def record(con: sqlite3.Connection, key: str, first_try: bool, now: int | None = None) -> dict:
    now = now or int(time.time())
    if get(key) is None:
        raise KeyError(key)
    s = con.execute("SELECT * FROM drill_state WHERE key=?", (key,)).fetchone()
    box, fails = (s["box"], s["fails"]) if s else (0, 0)
    box, due, fails = puzzles.next_state(box, fails, first_try, now)
    con.execute("INSERT INTO drill_state(key,box,due,seen,fails,last_seen) VALUES(?,?,?,1,?,?) "
                "ON CONFLICT(key) DO UPDATE SET box=excluded.box, due=excluded.due, seen=seen+1, "
                "fails=excluded.fails, last_seen=excluded.last_seen", (key, box, due, fails, now))
    con.commit()
    return {"box": box, "due": due}


def summary(con: sqlite3.Connection, now: int | None = None) -> dict:
    now = now or int(time.time())
    st = _state(con)
    total = len(DRILLS)
    seen = sum(1 for d in DRILLS if d["key"] in st)
    due = sum(1 for d in DRILLS if d["key"] not in st or st[d["key"]]["due"] <= now)
    learned = sum(1 for s in st.values() if s["box"] >= 2)
    return {"total": total, "seen": seen, "due": due, "learned": learned}
