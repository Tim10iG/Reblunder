// Chess board widget: renders a FEN, reports square clicks, and lets you draw arrows.
//   Right-click + drag: arrow     Right-click on a square: mark it     Left-click: clear all
//   Shift or Ctrl = red, Alt = blue, both = yellow (default green)
//   Touch: switch on arrowMode, then tap the start square and the target square.
const FILES = 'abcdefgh';
const SVGNS = 'http://www.w3.org/2000/svg';

function parseFen(fen) {
  const map = {};
  fen.split(' ')[0].split('/').forEach((row, i) => {
    let f = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) f += +ch;
      else { map[FILES[f] + (8 - i)] = ch; f++; }
    }
  });
  return map;
}

class ChessBoard {
  constructor(root, opts = {}) {
    this.root = root;
    this.opts = Object.assign({ drawable: true, coords: true, onSquare: null }, opts);
    this.state = { fen: '8/8/8/8/8/8/8/8 w - - 0 1', orientation: 'white', sel: null, last: null, targets: [] };
    this.arrows = [];      // drawn by the user: {from, to, color}
    this.marks = [];       // marked squares: {sq, color}
    this.fixed = [];       // arrows set by the program
    this.drag = null;
    this.arrowMode = false;   // touch: taps draw arrows instead of moving pieces
    this.pending = null;
    root.classList.add('cb');
    this.grid = document.createElement('div'); this.grid.className = 'board';
    this.svg = document.createElementNS(SVGNS, 'svg');
    this.svg.setAttribute('viewBox', '0 0 8 8'); this.svg.classList.add('overlay');
    root.append(this.grid, this.svg);
    this.grid.addEventListener('click', e => {
      const el = e.target.closest('.sq');
      if (!el) return;
      if (this.arrowMode) { this._tapArrow(el.dataset.sq); return; }
      if (this.opts.onSquare) this.opts.onSquare(el.dataset.sq);
    });
    if (this.opts.drawable) this._bindDrawing();
    this.draw();
  }

  set(state) { Object.assign(this.state, state); this.draw(); }
  setFixedArrows(list) { this.fixed = list || []; this._overlay(); }
  setArrowMode(on) { this.arrowMode = on; this.pending = null; this.draw(); }
  flash(kind) {
    this.root.classList.add('flash-' + kind);
    setTimeout(() => this.root.classList.remove('flash-' + kind), 350);
  }
  _tapArrow(sq) {
    if (!this.pending) { this.pending = sq; this.draw(); return; }
    const from = this.pending; this.pending = null;
    if (from === sq) this._toggle(this.marks, m => m.sq === sq, { sq, color: 'green' });
    else this._toggle(this.arrows, a => a.from === from && a.to === sq, { from, to: sq, color: 'green' });
    this.draw();
  }
  clearDrawings() { if (this.arrows.length || this.marks.length) { this.arrows = []; this.marks = []; this._overlay(); } }

  draw() {
    const s = this.state, white = s.orientation !== 'black', pieces = parseFen(s.fen);
    const files = white ? [...FILES] : [...FILES].reverse();
    const ranks = white ? [8, 7, 6, 5, 4, 3, 2, 1] : [1, 2, 3, 4, 5, 6, 7, 8];
    const frag = document.createDocumentFragment();
    for (const r of ranks) for (const f of files) {
      const sq = f + r, d = document.createElement('div');
      d.className = 'sq ' + ((FILES.indexOf(f) + r) % 2 === 0 ? 'd' : 'l');   // a1 is dark
      d.dataset.sq = sq;
      if (s.last && s.last.includes(sq)) d.classList.add('last');
      if (s.sel === sq) d.classList.add('sel');
      if (this.pending === sq) d.classList.add('pending');
      if (s.targets && s.targets.includes(sq)) { d.classList.add('target'); if (pieces[sq]) d.classList.add('cap'); }
      if (this.opts.coords) {
        if (f === files[0]) { const c = document.createElement('span'); c.className = 'coord r'; c.textContent = r; d.appendChild(c); }
        if (r === ranks[7]) { const c = document.createElement('span'); c.className = 'coord f'; c.textContent = f; d.appendChild(c); }
      }
      const pc = pieces[sq];
      if (pc) {
        const el = document.createElement('img');
        el.className = 'piece'; el.alt = ''; el.draggable = false;
        el.src = (window.PIECE_BASE || '/static/pieces/') + (pc === pc.toUpperCase() ? 'w' : 'b') + pc.toLowerCase() + '.svg';
        d.appendChild(el);
      }
      frag.appendChild(d);
    }
    this.grid.replaceChildren(frag);
    this._overlay();
  }

  _center(sq) {
    const f = FILES.indexOf(sq[0]), r = +sq[1];
    return this.state.orientation !== 'black' ? [f + .5, 8 - r + .5] : [7 - f + .5, r - .5];
  }

  _arrowEl(a, preview) {
    const [x1, y1] = this._center(a.from), [x2, y2] = this._center(a.to);
    const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy);
    if (!len) return null;
    const ux = dx / len, uy = dy / len, head = .44, half = .25, w = .085, start = .3;
    const sx = x1 + ux * start, sy = y1 + uy * start;            // start a bit off the centre
    const bx = x2 - ux * head, by = y2 - uy * head;              // base of the arrow head
    const px = -uy, py = ux;
    const pts = [
      [sx + px * w, sy + py * w], [bx + px * w, by + py * w], [bx + px * half, by + py * half],
      [x2, y2],
      [bx - px * half, by - py * half], [bx - px * w, by - py * w], [sx - px * w, sy - py * w],
    ].map(p => p[0].toFixed(3) + ',' + p[1].toFixed(3)).join(' ');
    const el = document.createElementNS(SVGNS, 'polygon');
    el.setAttribute('points', pts);
    el.setAttribute('class', 'arrow ' + a.color + (preview ? ' preview' : ''));
    return el;
  }

  _overlay() {
    const out = [];
    for (const m of this.marks) {
      const [x, y] = this._center(m.sq), c = document.createElementNS(SVGNS, 'circle');
      c.setAttribute('cx', x); c.setAttribute('cy', y); c.setAttribute('r', .44);
      c.setAttribute('class', 'mark ' + m.color);
      out.push(c);
    }
    for (const a of [...this.fixed, ...this.arrows]) { const el = this._arrowEl(a); if (el) out.push(el); }
    if (this.drag && this.drag.over && this.drag.over !== this.drag.from) {
      const el = this._arrowEl({ from: this.drag.from, to: this.drag.over, color: this.drag.color }, true);
      if (el) out.push(el);
    }
    this.svg.replaceChildren(...out);
  }

  _squareAt(e) {
    const r = this.grid.getBoundingClientRect();
    const fx = Math.floor((e.clientX - r.left) / (r.width / 8)), fy = Math.floor((e.clientY - r.top) / (r.height / 8));
    if (fx < 0 || fx > 7 || fy < 0 || fy > 7) return null;
    return this.state.orientation !== 'black' ? FILES[fx] + (8 - fy) : FILES[7 - fx] + (fy + 1);
  }

  _bindDrawing() {
    const color = e => {
      const red = e.shiftKey || e.ctrlKey || e.metaKey;
      return red && e.altKey ? 'yellow' : red ? 'red' : e.altKey ? 'blue' : 'green';
    };
    this.root.addEventListener('contextmenu', e => e.preventDefault());
    this.root.addEventListener('mousedown', e => {
      if (e.button === 2) {
        const sq = this._squareAt(e);
        if (sq) { this.drag = { from: sq, over: sq, color: color(e) }; e.preventDefault(); }
      } else if (e.button === 0 && !this.arrowMode) {
        this.clearDrawings();
      }
    });
    document.addEventListener('mousemove', e => {
      if (!this.drag) return;
      const sq = this._squareAt(e);
      if (sq !== this.drag.over) { this.drag.over = sq; this._overlay(); }
    });
    document.addEventListener('mouseup', e => {
      if (!this.drag || e.button !== 2) return;
      const d = this.drag, to = this._squareAt(e);
      this.drag = null;
      if (to && to === d.from) this._toggle(this.marks, m => m.sq === to, { sq: to, color: d.color });
      else if (to) this._toggle(this.arrows, a => a.from === d.from && a.to === to, { from: d.from, to, color: d.color });
      this._overlay();
    });
  }

  _toggle(list, same, item) {
    const i = list.findIndex(same);
    if (i >= 0 && list[i].color === item.color) list.splice(i, 1);   // same again: remove
    else if (i >= 0) list[i] = item;                                 // other colour: recolour
    else list.push(item);
  }
}

// ---- shared formatting -------------------------------------------------------------------
// Engine evaluation from your point of view: centipawns -> "+1.4", forced mate -> "+M3"
function fmtEval(cp) {
  const sign = cp > 0 ? '+' : cp < 0 ? '\u2212' : '';
  if (Math.abs(cp) >= 9000) return sign + 'M' + Math.max(1, Math.ceil((10000 - Math.abs(cp)) / 2));
  return sign + (Math.abs(cp) / 100).toFixed(1);
}

// Prominent "what did it cost" block: lost win chance, plus the evaluation in pawns if known.
function costBadge(loss, evalBest, evalPlayed, prefix) {
  const box = document.createElement('div'); box.className = 'cost';
  const v = document.createElement('span'); v.className = 'cost-v';
  v.textContent = (prefix || '') + '\u2212' + Math.round(loss) + ' %';
  const k = document.createElement('span'); k.className = 'cost-k';
  k.append(t('Gewinnchance '));
  if (evalBest != null && evalPlayed != null) {
    const small = document.createElement('small');
    small.textContent = `${fmtEval(evalBest)} \u2192 ${fmtEval(evalPlayed)}`;
    k.append(small);
  }
  box.append(v, k);
  return box;
}

// Links that open a position in the Lichess / Chess.com analysis board (new tab).
function analysisLinks(fen, orientation) {
  const mk = (text, href) => { const a = document.createElement('a'); a.className = 'textlink'; a.textContent = text; a.href = href; a.target = '_blank'; a.rel = 'noopener'; return a; };
  const black = orientation === 'black';
  return [
    mk(t('Analyse auf Lichess'), `https://lichess.org/analysis/standard/${fen.replace(/ /g, '_')}?color=${black ? 'black' : 'white'}`),
    mk(t('Analyse auf Chess.com'), `https://www.chess.com/analysis?fen=${encodeURIComponent(fen)}${black ? '&flip=true' : ''}`),
  ];
}

// "Why" paragraph: a small heading and the reasons as one text.
function reasonBlock(title, lines) {
  const p = document.createElement('p'); p.className = 'why';
  if (title) { const b = document.createElement('b'); b.textContent = title + '. '; p.append(b); }
  p.append(lines.join(' '));
  return p;
}
