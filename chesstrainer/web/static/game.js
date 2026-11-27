// Review of one game: step through the moves, jump to each mistake, look at the variations.
(function () {
  const G = window.GAME, N = G.plies.length;
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const board = new ChessBoard($('board'));
  const byPly = Object.fromEntries(G.mistakes.map(m => [m.ply, m]));
  let i = -1;            // index of the last move played on the board (-1 = start position)
  let vari = null;       // {m, key, j} while looking at a variation of mistake m
  const arrow = u => ({ from: u.slice(0, 2), to: u.slice(2, 4) });

  // ---- move list ----
  const moves = $('moves');
  G.plies.forEach((p, k) => {
    if (p.white) moves.append(el('span', 'no', p.no + '.'));
    else if (k === 0) moves.append(el('span', 'no', p.no + '…'), el('span'));
    const b = el('button', p.mine && (p.cls === 'mistake' || p.cls === 'blunder') ? p.cls : '', p.san);
    b.dataset.k = k;
    b.addEventListener('click', () => go(k));
    moves.append(b);
  });

  // ---- mistakes ----
  const list = $('mistakes');
  if (list) G.mistakes.forEach(m => {
    const li = el('li'), b = el('button');
    b.dataset.ply = m.ply;
    b.append(el('span', 'mono', `${m.no}${G.plies[m.ply].white ? '.' : '…'} ${m.san}`),
      el('span', null, m.motifs.map(x => x.title).join(', ') || t('Fehlzug')),
      el('span', 'c', '−' + Math.round(m.loss) + ' %'));
    b.addEventListener('click', () => go(m.ply - 1));
    li.append(b); list.append(li);
  });

  // ---- evaluation graph ----
  const graph = $('graph'), NS = 'http://www.w3.org/2000/svg';
  const x = k => N > 1 ? (k + 1) / N * 100 : 50;
  const pts = G.plies.filter(p => p.mine && p.win != null).map(p => [x(p.ply), 100 - p.win]);
  function svg(tag, attrs) { const e = document.createElementNS(NS, tag); for (const a in attrs) e.setAttribute(a, attrs[a]); return e; }
  function drawGraph() {
    const out = [svg('line', { class: 'mid', x1: 0, x2: 100, y1: 50, y2: 50 })];
    if (pts.length > 1) {
      const d = pts.map(p => p[0].toFixed(2) + ',' + p[1].toFixed(2)).join(' ');
      out.push(svg('polygon', { class: 'area', points: `${pts[0][0]},100 ${d} ${pts[pts.length - 1][0]},100` }));
      out.push(svg('polyline', { class: 'ln', points: d }));
    }
    for (const m of G.mistakes)
      out.push(svg('line', { x1: x(m.ply), x2: x(m.ply), y1: 90, y2: 100, stroke: 'var(--bad)', 'stroke-width': 2, 'vector-effect': 'non-scaling-stroke' }));
    out.push(svg('line', { class: 'cur', x1: x(i), x2: x(i), y1: 0, y2: 100 }));
    graph.replaceChildren(...out);
  }
  graph.addEventListener('click', e => {
    const r = graph.getBoundingClientRect();
    go(Math.round((e.clientX - r.left) / r.width * N) - 1);
  });

  // ---- detail of the mistake that is about to be played ----
  function detail() {
    const m = byPly[i + 1], box = $('detail');
    if (!m) { box.replaceChildren(); return; }
    const d = el('div', 'explain');
    d.append(costBadge(m.loss, m.eval_best, m.eval_played));
    const p1 = el('p');
    const spent = m.spent != null ? t(' nach {s} s', { s: Math.round(m.spent) }) : '';
    p1.append(t('Du hast '), el('b', 'mono', m.san), t('{t} gespielt. Besser war ', { t: spent }), el('b', 'mono', m.best_san), '.');
    d.append(p1);
    if (m.why_played && m.why_played.length) d.append(reasonBlock(t('Warum {san} schlecht war', { san: m.san }), m.why_played));
    if (m.why_best && m.why_best.length) d.append(reasonBlock(t('Was {san} bringt', { san: m.best_san }), m.why_best));
    for (const x of m.motifs) { const p = el('p'); p.append(el('b', null, x.title + '. '), x.hint); d.append(p); }
    const lines = el('div', 'lines');
    for (const [key, title, ev] of [['best', t('Beste Fortsetzung'), m.eval_best], ['your', t('Nach deinem Zug'), m.eval_played]]) {
      const row = el('div', 'line-row' + (vari && vari.key === key ? ' active' : '')); row.dataset.line = key;
      const head = el('div', 'line-head'); head.append(el('span', null, title), el('span', 'ev', ev != null ? fmtEval(ev) : ''));
      const mvs = el('div', 'mvs');
      m[key + '_line'].forEach((st, j) => {
        const b = el('button', 'mv' + (vari && vari.key === key && vari.j === j ? ' on' : ''), st.label);
        b.addEventListener('click', () => { vari = { m, key, j }; render(); });
        mvs.append(b);
      });
      row.append(head, mvs); lines.append(row);
    }
    d.append(lines);
    box.replaceChildren(d);
  }

  function render() {
    const m = byPly[i + 1];
    let fen, last = null, arrows = [];
    if (vari) {
      const st = vari.m[vari.key + '_line'][vari.j];
      fen = st.fen; last = [st.uci.slice(0, 2), st.uci.slice(2, 4)];
    } else {
      const p = i >= 0 ? G.plies[i] : null;
      fen = p ? p.fen : G.start; last = p ? [p.uci.slice(0, 2), p.uci.slice(2, 4)] : null;
      if (m) arrows = [{ ...arrow(m.uci), color: 'red' }, { ...arrow(m.best_uci), color: 'green' }];
    }
    board.clearDrawings();
    board.set({ fen, orientation: G.color, last, sel: null, targets: [] });
    board.setFixedArrows(arrows);
    moves.querySelectorAll('button').forEach(b => b.classList.toggle('on', !vari && +b.dataset.k === i));
    if (list) list.querySelectorAll('button').forEach(b => b.classList.toggle('on', +b.dataset.ply === i + 1));
    const on = moves.querySelector('button.on');
    if (on) moves.scrollTop = Math.max(0, on.offsetTop - moves.offsetTop - moves.clientHeight / 2);
    const p = i >= 0 ? G.plies[i] : null;
    $('pos-label').textContent = vari ? t('Variante') : p ? `${p.no}${p.white ? '.' : '…'} ${p.san}` : t('Ausgangsstellung');
    drawGraph(); detail();
    $('analysis').replaceChildren(...analysisLinks(fen, G.color));
  }

  function go(k) { vari = null; i = Math.max(-1, Math.min(N - 1, k)); render(); }
  function step(dir) {
    if (vari) {
      const j = vari.j + dir, line = vari.m[vari.key + '_line'];
      if (j < 0) vari = null; else vari.j = Math.min(line.length - 1, j);
      render();
    } else go(i + dir);
  }
  $('g-start').addEventListener('click', () => go(-1));
  $('g-end').addEventListener('click', () => go(N - 1));
  $('g-prev').addEventListener('click', () => step(-1));
  $('g-next').addEventListener('click', () => step(1));
  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
    else if (e.key === 'Home') go(-1); else if (e.key === 'End') go(N - 1);
  });
  $('arrow-mode').addEventListener('click', e => {
    const on = !board.arrowMode;
    board.setArrowMode(on); if (!on) board.clearDrawings();
    e.target.classList.toggle('on', on); e.target.setAttribute('aria-pressed', on);
    e.target.textContent = on ? t('Pfeile beenden') : t('Pfeile zeichnen');
  });

  const q = new URLSearchParams(location.search).get('ply');
  const first = G.mistakes.length ? G.mistakes[0].ply - 1 : -1;
  go(q != null && !isNaN(+q) ? +q - 1 : first);
})();
