// Renders example positions (mini boards with arrows) from JSON embedded in the page.
// Each <div class="ex-group" data-src="key"> shows the cards of window.EXAMPLES[key].
(function () {
  const PAGE = 6;
  const SITUATION_CHIPS = [t('Zu schnell gezogen'), t('Zeitnot'), t('Vorteil verspielt')];
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const date = ts => ts ? new Date(ts * 1000).toLocaleDateString({ de: 'de-DE', en: 'en-GB', fr: 'fr-FR', es: 'es-ES' }[window.LANG] || 'de-DE') : '';

  function card(c) {
    const box = el('div', 'ex-card');
    const b = el('div', 'ex-board'); box.append(b);
    const board = new ChessBoard(b, { coords: false });
    board.set({ fen: c.fen, orientation: c.fen.split(' ')[1] === 'b' ? 'black' : 'white' });
    const arrows = [];
    for (const u of (c.played || [])) arrows.push({ from: u.slice(0, 2), to: u.slice(2, 4), color: 'red' });
    if (c.best_uci) arrows.push({ from: c.best_uci.slice(0, 2), to: c.best_uci.slice(2, 4), color: 'green' });
    board.setFixedArrows(arrows);
    const txt = el('div', 'ex-text');
    if (c.cost) txt.append(costBadge(c.cost.loss, c.cost.best, c.cost.played, c.cost.prefix));
    txt.append(el('div', 'ex-head', c.head));
    for (const line of c.lines) {
      const p = el('p');
      for (const part of line) p.append(typeof part === 'string' ? part : el('b', part.cls || null, part.b));
      txt.append(p);
    }
    if (c.chips && c.chips.length) txt.append(el('p', 'ctx', c.chips.join(' · ')));
    const links = el('p', 'links');
    if (c.game) { const a = el('a', 'textlink', t('Partie ansehen')); a.href = `/game/${c.game}` + (c.ply != null ? `?ply=${c.ply}` : ''); links.append(a); }
    links.append(...analysisLinks(c.fen, c.fen.split(' ')[1] === 'b' ? 'black' : 'white'));
    txt.append(links);
    box.append(txt);
    return box;
  }

  // puzzle-style example (report) -> generic card
  function fromMistake(c) {
    const spent = c.time_spent != null ? t(' nach {s} s', { s: Math.round(c.time_spent) }) : '';
    return {
      fen: c.fen, played: [c.played_uci], best_uci: c.best_uci, game: c.game, ply: c.ply,
      head: t('Zug {n} · {date}', { n: c.move_no, date: date(c.end_time) }) + (c.opponent ? t(' · gegen {opp}', { opp: c.opponent }) : ''),
      cost: { loss: c.loss, best: c.eval_best, played: c.eval_played },
      lines: [[t('Gespielt: '), { b: c.played_san, cls: 'bad' }, spent], [t('Besser: '), { b: c.best_san, cls: 'good' }]],
      chips: [c.opening, ...(c.context || []).filter(x => SITUATION_CHIPS.includes(x))].filter(Boolean),
    };
  }
  // recurring opening position -> generic card
  function fromLeak(c) {
    return {
      fen: c.fen, played: c.played.filter(p => p.loss >= 5).map(p => p.uci), best_uci: c.best_uci, game: c.game, ply: c.ply,
      cost: { loss: c.played[0].loss, prefix: 'Ø ' },
      head: t('Zug {n} · {k}× erreicht · zuletzt {date}', { n: c.move_no, k: c.n, date: date(c.last_seen) }),
      lines: [
        [t('Du spielst: '), ...c.played.flatMap((p, i) => [i ? ', ' : '', { b: p.san, cls: p.loss >= 5 ? 'bad' : null }, ` ${p.n}× (−${Math.round(p.loss)})`])],
        [t('Besser: '), { b: c.best_san, cls: 'good' }],
      ],
      chips: [c.family],
    };
  }
  function fromOpeningMistake(c) {
    const spent = c.time_spent != null ? t(' nach {s} s', { s: Math.round(c.time_spent) }) : '';
    return {
      fen: c.fen, played: [c.uci], best_uci: c.best_uci, game: c.game, ply: c.ply,
      head: t('Zug {n} · {date}', { n: c.move_no, date: date(c.end_time) }) + (c.opponent ? t(' · gegen {opp}', { opp: c.opponent }) : ''),
      cost: { loss: c.loss },
      lines: [[t('Gespielt: '), { b: c.san, cls: 'bad' }, spent], [t('Besser: '), { b: c.best_san, cls: 'good' }]],
      chips: [c.name],
    };
  }
  // recurring opening position with its status in your recent games (progress page)
  function fromRepeat(c) {
    const card = fromLeak(c);
    const status = { again: t('Wieder passiert'), fixed: t('Zuletzt richtig gespielt'), open: t('Zuletzt nicht mehr erreicht') }[c.status];
    card.head = t('{color} · Zug {n} · zuletzt {date}', { color: c.color, n: c.move_no, date: date(c.last_seen) });
    card.lines.unshift([{ b: status, cls: c.status === 'again' ? 'bad' : c.status === 'fixed' ? 'good' : null },
      t(' · {n}× falsch, davon {r}× zuletzt', { n: c.bad_total, r: c.bad_recent })]);
    return card;
  }
  const CONVERT = { mistake: fromMistake, leak: fromLeak, opening: fromOpeningMistake, repeat: fromRepeat };

  function fill(group) {
    if (group.dataset.done) return;
    group.dataset.done = '1';
    const items = (window.EXAMPLES || {})[group.dataset.src] || [];
    const conv = CONVERT[group.dataset.kind || 'mistake'];
    const grid = el('div', 'ex-grid'); group.append(grid);
    let shown = 0;
    const more = el('button', 'ex-more', '');
    const add = () => {
      items.slice(shown, shown + PAGE).forEach(c => grid.append(card(conv(c))));
      shown = Math.min(items.length, shown + PAGE);
      more.hidden = shown >= items.length;
      more.textContent = t('{n} weitere zeigen', { n: Math.min(PAGE, items.length - shown) });
    };
    more.addEventListener('click', add);
    group.append(more);
    add();
  }

  document.querySelectorAll('.ex-group').forEach(g => {
    const d = g.closest('details');
    if (!d || d.open) fill(g);
    else d.addEventListener('toggle', () => { if (d.open) fill(g); });
  });
})();
