"""Plain-language reasons for a move: what the opponent does after it, and what the better move achieves.

Rule-based (python-chess only), working on engine lines that are already known: the punishing line after
the played move and the best line. Every sentence is a catalog string, so it is shown in the UI language.
"""
from __future__ import annotations

import chess

from . import classify as C
from .i18n import tr

_YOUR = {chess.PAWN: "deinen Bauern", chess.KNIGHT: "deinen Springer", chess.BISHOP: "deinen Läufer",
         chess.ROOK: "deinen Turm", chess.QUEEN: "deine Dame", chess.KING: "deinen König"}
TACTIC_GAIN = 2          # material (pawns) that counts as "the advantage is on the board"


def san_line(board: chess.Board, moves: list[chess.Move]) -> str:
    """'12…Nxe5 13.Qd1 Bxf2+' for the legal prefix of `moves`."""
    b, out = board.copy(stack=False), []
    for i, m in enumerate(moves):
        if m not in b.legal_moves:
            break
        if b.turn == chess.WHITE:
            out.append(f"{b.fullmove_number}.{b.san(m)}")
        else:
            out.append(f"{b.fullmove_number}…{b.san(m)}" if i == 0 else b.san(m))
        b.push(m)
    return " ".join(out)


def _legal_prefix(board: chess.Board, moves: list[chess.Move]) -> list[chess.Move]:
    b, out = board.copy(stack=False), []
    for m in moves:
        if m not in b.legal_moves:
            break
        out.append(m)
        b.push(m)
    return out


def _ends_in_mate(board: chess.Board, moves: list[chess.Move]) -> bool:
    b = board.copy(stack=False)
    for m in moves:
        b.push(m)
    return b.is_checkmate()


def _material_text(n: int, lose: bool) -> str:
    if lose:
        return tr("Am Ende der Variante fehlt dir ein Bauer.") if n == 1 else \
            tr("Am Ende der Variante fehlt dir Material im Wert von {n} Bauern.", n=n)
    return tr("Am Ende der Variante hast du einen Bauern mehr.") if n == 1 else \
        tr("Am Ende der Variante hast du Material im Wert von {n} Bauern mehr.", n=n)


def _fork_targets(board: chess.Board, move: chess.Move) -> list[chess.Piece]:
    """The valuable pieces of the side not moving that `move` attacks at once (mirrors classify.makes_fork)."""
    mover = board.turn
    b = board.copy(stack=False)
    b.push(move)
    piece = b.piece_at(move.to_square)
    out = []
    for t in b.attacks(move.to_square):
        p = b.piece_at(t)
        if p is None or p.color == mover:
            continue
        if p.piece_type == chess.KING or (C.VALUES[p.piece_type] >= 3 and (
                C.VALUES[p.piece_type] > C.VALUES[piece.piece_type] or not b.is_attacked_by(not mover, t))):
            out.append(p)
    return sorted(out, key=lambda p: -C._val(p))


def _threat_after(board: chess.Board) -> chess.Move | None:
    """The best material-winning capture of the side NOT to move, if it could move now (null-move threat)."""
    if board.is_check():
        return None
    b = board.copy(stack=False)
    b.push(chess.Move.null())
    best, gain = None, 0
    for m in b.legal_moves:
        if b.is_capture(m):
            g = C.see_gain(b, m)
            if g > gain:
                best, gain = m, g
    return best


def punishment(board: chess.Board, move: chess.Move, reply_line: list[chess.Move]) -> list[str]:
    """Why `move` (by board.turn) is bad, given the opponent's best line after it (reply first)."""
    me = board.turn
    after = board.copy(stack=False)
    after.push(move)
    reply_line = _legal_prefix(after, reply_line)
    if not reply_line or after.is_game_over():
        return []
    out: list[str] = []
    r = reply_line[0]
    rsan = after.san(r)
    seq = san_line(after, reply_line)
    if _ends_in_mate(after, reply_line):
        return [tr("Danach setzt dich der Gegner matt: {line}.", line=seq)]
    after_r = after.copy(stack=False)
    after_r.push(r)
    if after.is_capture(r):
        victim = C._victim(after, r)
        out.append(tr("Der Gegner antwortet {san} und schlägt {piece}.", san=rsan, piece=tr(_YOUR[victim.piece_type])))
        sq = chess.square_name(r.to_square)
        if r.to_square == move.to_square:
            out.append(tr("Das Feld {sq} war nach deinem Zug nicht ausreichend gedeckt.", sq=sq))
        elif r.to_square in C._threat_squares(board):
            out.append(tr("Diese Drohung bestand schon vor deinem Zug, und {san} hat nichts dagegen getan.",
                          san=board.san(move)))
        elif move.from_square in board.attackers(me, r.to_square):
            out.append(tr("Dein Zug hat die Deckung von {sq} aufgegeben.", sq=sq))
    elif C.makes_fork(after, r):
        t = _fork_targets(after, r)
        out.append(tr("Der Gegner antwortet {san} und greift {a} und {b} gleichzeitig an.", san=rsan,
                      a=tr(_YOUR[t[0].piece_type]), b=tr(_YOUR[t[1].piece_type])))
    else:
        threat = _threat_after(after_r)
        if threat is not None:
            victim = C._victim(after_r, threat) if after_r.piece_at(threat.to_square) else None
            out.append(tr("Der Gegner antwortet {san}{check} und droht, {piece} zu schlagen.", san=rsan,
                          check=tr(" mit Schach") if after_r.is_check() else "",
                          piece=tr(_YOUR[victim.piece_type]) if victim else "?"))
        elif after_r.is_check():
            out.append(tr("Der Gegner antwortet {san} mit Schach.", san=rsan))
        else:
            out.append(tr("Der Gegner antwortet {san}.", san=rsan))
    lost = -C.line_material(board, [move] + reply_line, me)
    if lost >= 1:
        out.append(_material_text(lost, True) + " " + tr("Variante: {line}", line=seq))
    elif not after.is_capture(r):
        out.append(tr("Material geht nicht sofort verloren, aber die Stellung wird deutlich schlechter "
                      "(Aktivität, Königssicherheit oder Bauernstruktur)."))
    return out


def merit(board: chess.Board, line: list[chess.Move]) -> list[str]:
    """What the best line achieves (mate, material, or parrying a threat)."""
    me = board.turn
    line = _legal_prefix(board, line)
    if not line:
        return []
    san = board.san(line[0])
    if _ends_in_mate(board, line):
        return [tr("{line} setzt matt.", line=san_line(board, line))]
    out = []
    gain = C.line_material(board, line, me)
    if gain >= 1:
        out.append(_material_text(gain, False) + " " + tr("Variante: {line}", line=san_line(board, line)))
    threat = _threat_after(board)
    victim = board.piece_at(threat.to_square) if threat is not None else None
    if gain < 1 and victim is not None and victim.color == me:
        b = board.copy(stack=False)
        b.push(line[0])
        still = any(m.to_square == threat.to_square and b.is_capture(m) and C.see_gain(b, m) > 0 for m in b.legal_moves)
        if not still:
            out.append(tr("{san} pariert die Drohung gegen {piece}.", san=san, piece=tr(_YOUR[victim.piece_type])))
    return out


def realized_moves(fen: str, line: list[str], max_moves: int = 4) -> int:
    """How many own moves of `line` a puzzle should ask for: until mate, or until the material gain is on the
    board (TACTIC_GAIN pawns, after the opponent's reply). Positional lines stay one-movers."""
    board = chess.Board(fen)
    me = board.turn
    moves = []
    b = board.copy(stack=False)
    for u in line:
        try:
            m = chess.Move.from_uci(u)
        except ValueError:
            break
        if m not in b.legal_moves:
            break
        moves.append(m)
        b.push(m)
    b = board.copy(stack=False)
    for k, m in enumerate(moves):
        b.push(m)
        if k % 2:
            continue
        n = k // 2 + 1
        if n > max_moves:
            break
        if b.is_checkmate():
            return n
        if C.line_material(board, moves[:k + 2], me) >= TACTIC_GAIN:
            return n
    return 1
