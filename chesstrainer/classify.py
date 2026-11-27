"""Rule-based labelling of *why* a move was a mistake (motif + context tags).

Stockfish tells us THAT a move was bad. These heuristics (python-chess only, no engine)
guess WHY, using the engine's best move and its refutation of the played move.
"""
from __future__ import annotations

import math

from .i18n import tr

import chess

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
MATE_CP = 9000          # |cp| above this means a forced mate (engine mate score is 10000 - plies)

# --- thresholds (win-probability points, 0-100) ---------------------------------------
BLUNDER, MISTAKE, INACCURACY = 20.0, 10.0, 5.0
ALREADY_LOST, ALREADY_WON = 8.0, 92.0   # ignore "mistakes" in dead-lost / still-won positions
FAST_MOVE_SECS = 3.0

# --- labels (German UI) --------------------------------------------------------------
# tag -> (title, what to do about it)
_MOTIFS: dict[str, tuple[str, str]] = {
    "hung_piece": ("Figur eingestellt",
                   "Bevor du ziehst: Ist das Zielfeld angegriffen? Wie oft ist es gedeckt?"),
    "left_piece_hanging": ("Figur ungedeckt zurückgelassen",
                           "Welche Figur hat der gezogene Stein gedeckt oder geschützt? Prüfe nach dem Zug alle deine ungedeckten Figuren."),
    "ignored_threat": ("Drohung des Gegners übersehen",
                       "Frage zuerst: Was droht der Gegner mit seinem letzten Zug? Würde er nach einem Passzug etwas gewinnen?"),
    "fork_allowed": ("Gabel zugelassen",
                     "Suche Felder, von denen eine gegnerische Figur zwei deiner Figuren gleichzeitig angreift (v.a. Springer)."),
    "allowed_mate": ("Matt zugelassen",
                     "Prüfe vor jedem Zug alle Schachgebote des Gegners, besonders gegen deinen König."),
    "back_rank": ("Grundreihenmatt",
                  "Hat dein König ein Fluchtfeld (Luft)? Prüfe die Grundreihe, bevor du Türme oder Damen abziehst."),
    "missed_mate": ("Matt übersehen",
                    "Gehe bei Vorteil alle Schachgebote und Damenzüge zuerst durch."),
    "missed_recapture": ("Rückschlagen vergessen",
                         "Wurde gerade etwas geschlagen? Schlage zuerst zurück, bevor du einen anderen Plan verfolgst."),
    "missed_capture": ("Gewinnende Schlagfolge verpasst",
                       "Gehe alle Schlagzüge durch: Gibt es eine ungedeckte oder höherwertige Figur?"),
    "missed_fork": ("Eigene Gabel übersehen",
                    "Suche Felder, von denen eine deiner Figuren zwei gegnerische Figuren angreift."),
    "missed_check_tactic": ("Taktik mit Schach übersehen",
                            "Schachgebote zuerst prüfen: Wohin muss der König, was wird frei?"),
    "pin_allowed": ("Fesselung oder Spieß zugelassen",
                    "Stehen zwei deiner Figuren (oder Figur und König) auf einer Linie oder Diagonale? Dann kann ein Läufer, Turm oder die Dame sie aufspießen."),
    "missed_pin": ("Fesselung oder Spieß übersehen",
                   "Suche gegnerische Figuren, die auf einer Linie oder Diagonale hintereinander stehen."),
    "discovery_allowed": ("Abzugsangriff zugelassen",
                          "Welche gegnerische Figur steht vor einem Läufer, Turm oder einer Dame? Zieht sie weg, greift die hintere Figur an."),
    "missed_discovery": ("Abzugsangriff übersehen",
                         "Steht eine deiner Figuren vor deinem eigenen Läufer, Turm oder deiner Dame? Ein Wegzug mit Drohung greift doppelt an."),
    "tactic_allowed": ("Mehrzügige Taktik zugelassen",
                       "Der Materialverlust kommt erst nach zwei, drei Zügen. Rechne erzwungene Folgen (Schach, Schlagen, Drohung) des Gegners einen Zug weiter."),
    "missed_tactic": ("Mehrzügige Taktik übersehen",
                      "Es gab eine erzwungene Folge mit Materialgewinn. Rechne Schachs, Schlagzüge und Drohungen zwei bis drei Züge tief."),
    "lost_pawn": ("Bauer verloren",
                  "Zähle vor dem Zug Angreifer und Verteidiger deiner Bauern, vor allem im Zentrum und am Damenflügel."),
    "missed_pawn": ("Bauerngewinn übersehen",
                    "Prüfe, ob ein gegnerischer Bauer öfter angegriffen als gedeckt ist."),
    "opening_play": ("Eröffnungsprinzip verletzt",
                     "Ohne Taktik: Entwicklung, Zentrum, Rochade. Vergleiche die Stellung im Reiter „Eröffnungen“ mit dem Engine-Zug."),
    "king_safety": ("Königsstellung geschwächt",
                    "Bauernzüge vor dem eigenen König schaffen Schwächen. Ziehe sie nur mit konkretem Grund."),
    "missed_conversion": ("Vorteil nicht verwertet",
                          "In besserer Stellung: vereinfachen, Gegenspiel nehmen, keine neuen Baustellen aufmachen."),
    "missed_defense": ("Beste Verteidigung verpasst",
                       "In schlechterer Stellung zählt der zäheste Zug: Was ist die konkrete Drohung, und welcher Zug pariert sie?"),
    "endgame_technique": ("Endspieltechnik",
                          "König aktivieren, Freibauern schaffen und stoppen, Türme hinter die Bauern."),
    "quiet_mistake": ("Positioneller Fehler",
                      "Kein klares Taktikmotiv: Vergleiche mehrere Kandidatenzüge, statt den ersten plausiblen zu spielen."),
}

_CONTEXTS: dict[str, str] = {
    "opening": "Eröffnung", "middlegame": "Mittelspiel", "endgame": "Endspiel",
    "time_trouble": "Zeitnot", "fast_move": "Zu schnell gezogen",
    "winning": "In besserer Stellung", "equal": "In ausgeglichener Stellung",
    "worse": "In schlechterer Stellung", "threw_win": "Vorteil verspielt",
    "white": "Mit Weiß", "black": "Mit Schwarz",
}


class _Tr(dict):
    """Label table that hands out the text in the current UI language (German source strings are the keys)."""
    def __getitem__(self, k):
        v = dict.__getitem__(self, k)
        return tuple(tr(x) for x in v) if isinstance(v, tuple) else tr(v)

    def get(self, k, default=None):
        return self[k] if k in self else default

    def items(self):
        return [(k, self[k]) for k in self]

    def values(self):
        return [self[k] for k in self]


MOTIFS: dict[str, tuple[str, str]] = _Tr(_MOTIFS)
CONTEXTS: dict[str, str] = _Tr(_CONTEXTS)


def winpct(cp: float) -> float:
    """Lichess win-probability model: centipawns (from the mover's view) -> 0..100."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * max(-1500, min(1500, cp)))) - 1)


def classify_loss(loss: float, win_before: float, win_after: float) -> str:
    """Return 'ok'|'inaccuracy'|'mistake'|'blunder'. Positions that are already decided count as ok."""
    if win_before <= ALREADY_LOST or win_after >= ALREADY_WON:
        return "ok"
    if loss >= BLUNDER:
        return "blunder"
    if loss >= MISTAKE:
        return "mistake"
    if loss >= INACCURACY:
        return "inaccuracy"
    return "ok"


def phase_of(board: chess.Board) -> str:
    if board.fullmove_number <= 10:
        return "opening"
    mat = 0
    for pt in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        mat += VALUES[pt] * (len(board.pieces(pt, chess.WHITE)) + len(board.pieces(pt, chess.BLACK)))
    return "endgame" if mat <= 26 else "middlegame"


def _victim(board: chess.Board, move: chess.Move) -> chess.Piece | None:
    if board.is_en_passant(move):
        return chess.Piece(chess.PAWN, not board.turn)
    return board.piece_at(move.to_square)


def see_gain(board: chess.Board, move: chess.Move) -> int:
    """Cheap one-step material estimate for a capture (not a full SEE)."""
    victim = _victim(board, move)
    attacker = board.piece_at(move.from_square)
    if victim is None or attacker is None:
        return 0
    defended = bool(board.attackers(not board.turn, move.to_square)) and not board.is_en_passant(move)
    gain = VALUES[victim.piece_type]
    if defended:
        gain -= VALUES[attacker.piece_type] if attacker.piece_type != chess.KING else 99
    return gain


def _threat_squares(board: chess.Board) -> set[int]:
    """Squares where the side NOT to move would win material if it were their turn."""
    if board.is_check():
        return set()
    b = board.copy(stack=False)
    b.push(chess.Move.null())
    out = set()
    for m in b.legal_moves:
        if b.is_capture(m) and see_gain(b, m) > 0:
            out.add(m.to_square)
    return out


def makes_fork(board: chess.Board, move: chess.Move) -> bool:
    """Does `move` (by board.turn) leave the moved piece attacking 2+ valuable enemy targets?"""
    mover = board.turn
    b = board.copy(stack=False)
    b.push(move)
    sq = move.to_square
    piece = b.piece_at(sq)
    if piece is None:
        return False
    # the forking piece itself must not just hang for free
    if b.is_attacked_by(not mover, sq) and not b.is_attacked_by(mover, sq):
        return False
    targets = 0
    for t in b.attacks(sq):
        p = b.piece_at(t)
        if p is None or p.color == mover:
            continue
        if p.piece_type == chess.KING:
            targets += 1
        elif VALUES[p.piece_type] >= 3 and (
                VALUES[p.piece_type] > VALUES[piece.piece_type] or not b.is_attacked_by(not mover, t)):
            targets += 1
    return targets >= 2


def _balance(board: chess.Board, pov: chess.Color) -> int:
    total = 0
    for pt in (chess.PAWN, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
        total += VALUES[pt] * (len(board.pieces(pt, pov)) - len(board.pieces(pt, not pov)))
    return total


def line_material(board: chess.Board, line: list[chess.Move], pov: chess.Color) -> int:
    """Material change (in pawns, from pov's view) after playing `line`, counting an obvious
    recapture that the line was cut off before."""
    b = board.copy(stack=False)
    start = _balance(b, pov)
    last_capture = None
    for m in line:
        if m not in b.legal_moves:
            break
        last_capture = m.to_square if b.is_capture(m) else None
        b.push(m)
    delta = _balance(b, pov) - start
    if last_capture is not None and not b.is_game_over():
        piece = b.piece_at(last_capture)
        attackers = [b.piece_at(a) for a in b.attackers(b.turn, last_capture)]
        if piece is not None and attackers:
            defended = b.is_attacked_by(not b.turn, last_capture)
            cheapest = min(99 if a.piece_type == chess.KING else VALUES[a.piece_type] for a in attackers)
            if not (defended and cheapest == 99):
                gain = VALUES[piece.piece_type] - (min(cheapest, VALUES[piece.piece_type]) if defended else 0)
                if gain > 0:
                    delta += gain if b.turn == pov else -gain
    return delta


def _val(piece: chess.Piece) -> int:
    return 100 if piece.piece_type == chess.KING else VALUES[piece.piece_type]


def makes_pin_or_skewer(board: chess.Board, move: chess.Move) -> bool:
    """Does the moved bishop/rook/queen attack an enemy piece with a more (pin) or less (skewer)
    valuable enemy piece standing right behind it on the same line?"""
    mover = board.turn
    b = board.copy(stack=False)
    b.push(move)
    sq = move.to_square
    piece = b.piece_at(sq)
    if piece is None or piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        return False
    if b.is_attacked_by(not mover, sq) and not b.is_attacked_by(mover, sq):
        return False                                  # the attacker simply hangs
    for x in b.attacks(sq):
        front = b.piece_at(x)
        if front is None or front.color == mover:
            continue
        df = (chess.square_file(x) > chess.square_file(sq)) - (chess.square_file(x) < chess.square_file(sq))
        dr = (chess.square_rank(x) > chess.square_rank(sq)) - (chess.square_rank(x) < chess.square_rank(sq))
        f, r = chess.square_file(x) + df, chess.square_rank(x) + dr
        while 0 <= f <= 7 and 0 <= r <= 7:
            behind = b.piece_at(chess.square(f, r))
            if behind is not None:
                if behind.color != mover:
                    vf, vb = _val(front), _val(behind)
                    if vf >= 3 and vb > vf:                       # pin (absolute or to a bigger piece)
                        return True
                    if vf > vb >= 3 and vf > VALUES[piece.piece_type]:   # skewer
                        return True
                break
            f, r = f + df, r + dr
    return False


def makes_discovery(board: chess.Board, move: chess.Move) -> bool:
    """Does moving this piece open a line for another own bishop/rook/queen onto the enemy king
    or onto a piece that is more valuable or undefended?"""
    mover = board.turn
    b = board.copy(stack=False)
    b.push(move)
    for s in b.pieces(chess.BISHOP, mover) | b.pieces(chess.ROOK, mover) | b.pieces(chess.QUEEN, mover):
        if s == move.to_square:
            continue
        slider = b.piece_at(s)
        for t in b.attacks(s):
            target = b.piece_at(t)
            if target is None or target.color == mover or not (chess.BB_SQUARES[move.from_square] & chess.between(s, t)):
                continue
            if target.piece_type == chess.KING:
                return True
            if VALUES[target.piece_type] >= 3 and (VALUES[target.piece_type] > VALUES[slider.piece_type]
                                                   or not b.is_attacked_by(not mover, t)):
                return True
    return False


def weakens_king(board: chess.Board, move: chess.Move) -> bool:
    """A pawn move right in front of the own (castled) king."""
    piece = board.piece_at(move.from_square)
    king = board.king(board.turn)
    if piece is None or piece.piece_type != chess.PAWN or king is None:
        return False
    home = 0 if board.turn == chess.WHITE else 7
    if chess.square_rank(king) != home or chess.square_file(king) in (3, 4):
        return False
    return (abs(chess.square_file(move.from_square) - chess.square_file(king)) <= 1
            and abs(chess.square_rank(move.from_square) - home) <= 2)


def _as_line(x) -> list[chess.Move]:
    if x is None:
        return []
    return [x] if isinstance(x, chess.Move) else list(x)


def motif_tags(board: chess.Board, played: chess.Move, best, refut, cu_before: float, cu_after: float,
               last_capture_sq: int | None, phase: str | None = None,
               win_before: float | None = None) -> list[str]:
    """Motif tags for one mistake. `board` is the position before the user's move.

    best  = the engine's best move or its whole line (best move first)
    refut = the opponent's best reply to the played move, or the whole punishing line
    """
    best_line, reply_line = _as_line(best), _as_line(refut)
    best = best_line[0] if best_line else None
    refut = reply_line[0] if reply_line else None
    me = board.turn
    tags: list[str] = []
    after = board.copy(stack=False)
    after.push(played)

    lost = -line_material(board, [played] + reply_line, me)       # what the punishment costs you
    gain = line_material(board, best_line, me) if best_line else 0  # what the best line wins
    swing = gain + lost

    if cu_after <= -MATE_CP:
        tags.append("allowed_mate")
        if refut is not None and refut in after.legal_moves:
            chk = after.copy(stack=False)
            chk.push(refut)
            if chk.is_checkmate():
                ksq = after.king(me)
                if ksq is not None and chess.square_rank(ksq) in (0, 7):
                    tags.append("back_rank")
    if cu_before >= MATE_CP:
        tags.append("missed_mate")

    # ---- what the opponent can do after your move -----------------------------------
    if refut is not None and refut in after.legal_moves and "allowed_mate" not in tags:
        victim = _victim(after, refut)
        if after.is_capture(refut) and (see_gain(after, refut) > 0 or (victim and VALUES[victim.piece_type] >= 3)):
            if refut.to_square == played.to_square:
                tags.append("hung_piece")
            elif refut.to_square in _threat_squares(board) and played.from_square != refut.to_square:
                tags.append("ignored_threat")
            else:
                tags.append("left_piece_hanging")
        elif makes_fork(after, refut):
            tags.append("fork_allowed")
        elif lost >= 1 and makes_pin_or_skewer(after, refut):
            tags.append("pin_allowed")
        elif lost >= 1 and makes_discovery(after, refut):
            tags.append("discovery_allowed")
        elif lost >= 2 and swing >= 2:
            tags.append("tactic_allowed")

    # ---- what you could have done -----------------------------------------------------
    if best is not None and best in board.legal_moves and "missed_mate" not in tags:
        if last_capture_sq is not None and best.to_square == last_capture_sq and board.is_capture(best) \
                and played.to_square != last_capture_sq:
            tags.append("missed_recapture")
        elif board.is_capture(best) and see_gain(board, best) > 0:
            tags.append("missed_capture")
        elif makes_fork(board, best):
            tags.append("missed_fork")
        elif gain >= 1 and makes_pin_or_skewer(board, best):
            tags.append("missed_pin")
        elif gain >= 1 and makes_discovery(board, best):
            tags.append("missed_discovery")
        elif gain >= 2 and swing >= 2:
            tags.append("missed_tactic")
        elif board.gives_check(best) and cu_before < 85 and (not best_line[1:] or gain >= 1):
            tags.append("missed_check_tactic")

    if not tags and swing >= 1:
        tags.append("lost_pawn" if lost >= 1 else "missed_pawn")

    # ---- no tactic found: say what kind of positional mistake it is ---------------------
    if not tags:
        if phase == "opening":
            tags.append("opening_play")
        elif weakens_king(board, played):
            tags.append("king_safety")
        elif win_before is not None and win_before >= 70:
            tags.append("missed_conversion")
        elif win_before is not None and win_before <= 35:
            tags.append("missed_defense")
        elif phase == "endgame":
            tags.append("endgame_technique")
        else:
            tags.append("quiet_mistake")
    return tags


def context_tags(phase: str, color: str, win_before: float, win_after: float,
                 time_spent: float | None, clock: float | None, base_secs: int) -> list[str]:
    ctx = [phase, color]
    if win_before >= 65:
        ctx.append("winning")
        if win_after <= 55:
            ctx.append("threw_win")
    elif win_before <= 35:
        ctx.append("worse")
    else:
        ctx.append("equal")
    if clock is not None and base_secs:
        if clock <= max(20.0, 0.1 * base_secs):
            ctx.append("time_trouble")
    if time_spent is not None and time_spent <= FAST_MOVE_SECS and "time_trouble" not in ctx:
        ctx.append("fast_move")
    return ctx
