// Warm-up: five puzzles from your own mistakes. Legality and solutions are checked by the server.
const $ = id => document.getElementById(id);
const MODE = ['opening', 'repertoire'].includes(new URLSearchParams(location.search).get('mode')) ? new URLSearchParams(location.search).get('mode') : 'mixed';
const LABEL = { mixed: t('Warm-up'), opening: t('Eröffnungstraining'), repertoire: t('Repertoire') };
const apiUrl = p => p.url || `/api/puzzle/${p.id}`;       // repertoire drills live under /api/drill/<key>
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

let puzzles = [], idx = 0, results = [], cur = null, view = null, playTimer = null;
const board = new ChessBoard($('board'), { onSquare });
board.set({ fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' });

async function api(url, body) {
  const r = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  if (r.status === 401) { location.href = '/login'; throw new Error('login'); }
  return r.json();
}

function drawBoard() {
  const targets = (!cur.locked && cur.sel && cur.legal[cur.sel]) || [];
  board.set({ fen: cur.fen, orientation: cur.p.turn, sel: cur.sel, last: cur.last, targets });
}

function renderProgress() {
  $('progress').replaceChildren(...puzzles.map((_, i) =>
    el('span', 'dot ' + (i < results.length ? (results[i] ? 'ok' : 'no') : i === idx ? 'cur' : ''))));
  $('progress').setAttribute('aria-label', t('Puzzle {i} von {n}', { i: idx + 1, n: puzzles.length }));
}

async function load() {
  document.querySelectorAll('#mode a').forEach(a => a.classList.toggle('on', a.dataset.mode === MODE));
  $('loading').hidden = false; $('task').hidden = true; $('done').hidden = true; $('empty').hidden = true;
  try { puzzles = await api(`/api/warmup?n=5&mode=${MODE}`); }
  catch (e) { $('loading').textContent = t('Der Server ist nicht erreichbar'); $('loading').className = 'msg bad'; return; }
  $('loading').hidden = true;
  idx = 0; results = [];
  if (!puzzles.length) {
    if (MODE === 'opening') $('empty-text').textContent = t('In deinen analysierten Partien gibt es keine Fehler in den ersten zehn Zügen.');
    $('empty').hidden = false;
    if (window.MULTI && MODE !== 'opening') watchJob();
    return;
  }
  $('task').hidden = false;
  show();
}

// Public mode: while a player's games are being fetched and analysed, show what is going on instead of "no puzzles".
async function watchJob() {
  const box = $('empty-job'), head = $('empty').querySelector('h1'), text = $('empty-text');
  let s;
  try { s = await (await fetch('/api/stats')).json(); } catch (e) { return; }
  const j = s.job || {};
  if (j.running) {
    head.textContent = j.queued ? t('Du bist in der Warteschlange') : t('Deine Partien werden analysiert');
    text.textContent = t('Das dauert ein paar Minuten. Du kannst die Seite offen lassen, sie lädt sich selbst neu, sobald die ersten Puzzles da sind.');
    const pct = j.total ? Math.round(100 * j.done / j.total) : 0;
    box.hidden = false;
    box.replaceChildren(el('p', 'job-msg', j.msg || t('Starte …')),
      (() => { const m = el('div', 'meter' + (j.total ? '' : ' wait')); const i = el('i'); i.style.width = pct + '%'; m.append(i); return m; })());
    setTimeout(watchJob, 1500);
  } else if (j.error) {
    head.textContent = t('Das hat nicht geklappt');
    text.textContent = j.error;
    const b = el('button', 'primary', t('Erneut versuchen'));
    b.addEventListener('click', async () => { b.disabled = true; await fetch('/api/sync', { method: 'POST' }); watchJob(); });
    box.hidden = false; box.replaceChildren(b);
  } else {
    box.hidden = true;
    head.textContent = t('Noch keine Puzzles');
    text.textContent = t('In deinen analysierten Partien wurden keine Fehler gefunden, aus denen sich Puzzles bauen lassen. Spiele ein paar Partien mehr und tippe dann auf „Aktualisieren“.');
  }
}

function show() {
  const p = puzzles[idx];
  // step: plies of the solution line on the board; pos/legal: the position the player has to move in right now
  cur = { p, fen: p.fen, pos: p.fen, legal: p.legal, step: 0, goal: p.goal || 1, sel: null, last: null, posLast: null,
          locked: false, wrong: 0, t0: performance.now(), finished: false, pendingReset: false };
  view = null; stopPlay();
  board.clearDrawings(); board.setFixedArrows([]);
  $('kicker').textContent = t('{label} · {kind} {i} von {n}', { label: LABEL[MODE], kind: p.drill ? t('Stellung') : t('Puzzle'), i: idx + 1, n: puzzles.length });
  $('turn-i').className = p.turn;
  $('prompt').textContent = p.turn === 'white' ? t('Weiß am Zug') : t('Schwarz am Zug');
  $('meta').textContent = p.drill ? `${p.group} · ${p.moves}`
    : p.opponent ? t('Aus deiner Partie gegen {opp} vom {date}, Zug {n}.', { opp: p.opponent, date: p.date || '?', n: p.move_no })
    : t('Aus deiner Partie vom {date}, Zug {n}.', { date: p.date || '?', n: p.move_no });
  setMsg(cur.goal > 1 ? t('Finde den besten Zug. Danach geht es weiter, bis der Vorteil klar auf dem Brett ist ({n} Züge).', { n: cur.goal })
    : t('Finde den besten Zug.'), '');
  $('reveal').replaceChildren(); $('lines').hidden = true;
  $('next').hidden = true; $('give-up').hidden = false;
  renderProgress(); drawBoard();
}

function setMsg(text, cls) { $('msg').textContent = text; $('msg').className = 'msg ' + cls; }

function resetToPosition() {
  cur.fen = cur.pos; cur.last = cur.posLast; cur.sel = null; cur.pendingReset = false;
  board.setFixedArrows([]);
}

function onSquare(sq) {
  if (!cur || cur.locked || cur.finished) return;
  if (cur.pendingReset) resetToPosition();          // the refuted move stays visible until the next tap
  const legal = cur.legal;
  if (cur.sel && legal[cur.sel] && legal[cur.sel].includes(sq)) { submit(cur.sel, sq); return; }
  cur.sel = legal[sq] ? (cur.sel === sq ? null : sq) : null;
  drawBoard();
}

async function submit(from, to) {
  cur.locked = true;
  cur.fen = cur.pos; cur.sel = null; drawBoard();
  const r = await api(`${apiUrl(cur.p)}/check`, { uci: from + to, step: cur.step });
  if (r.error) { cur.locked = false; drawBoard(); return; }
  cur.fen = r.fen_after; cur.last = [from, to]; drawBoard();
  if (r.correct && r.done) {
    board.flash('ok');
    cur.alt = r.alt ? r.best_san : null;
    await finish(cur.wrong === 0, true); return;
  }
  if (r.correct) {                                   // multi-move puzzle: the opponent answers, you go on
    board.flash('ok');
    setMsg(t('Richtig. Der Gegner antwortet {san}.', { san: r.reply_san }), 'good');
    await new Promise(ok => setTimeout(ok, 650));
    cur.fen = cur.pos = r.fen_reply; cur.legal = r.legal; cur.step = r.next_step;
    cur.last = cur.posLast = [r.reply.slice(0, 2), r.reply.slice(2, 4)];
    cur.locked = false; drawBoard();
    setMsg(t('Richtig. Der Gegner antwortet {san}. Wie geht es weiter?', { san: r.reply_san }), 'good');
    return;
  }
  board.flash('bad');
  cur.wrong++;
  const left = 3 - cur.wrong;
  if (r.refute) board.setFixedArrows([{ from: r.refute.slice(0, 2), to: r.refute.slice(2, 4), color: 'red' }]);
  const head = r.original_mistake ? t('Das war dein Zug in der Partie. ') : t('{san} ist nicht der beste Zug. ', { san: r.san });
  const tail = left > 1 ? t('Noch {n} Versuche.', { n: left }) : left === 1 ? t('Noch 1 Versuch.') : '';
  $('msg').className = 'msg bad';
  $('msg').replaceChildren(el('span', null, head + (r.why || []).slice(0, 2).join(' ')), ...(tail ? [el('b', 'retry', ' ' + tail)] : []));
  if (left <= 0) {
    await new Promise(ok => setTimeout(ok, 2200));
    resetToPosition(); drawBoard();
    await finish(false, false); return;
  }
  cur.locked = false; cur.pendingReset = true;     // next tap returns to the position
}

async function finish(firstTry, solved) {
  cur.locked = true; cur.finished = true;
  $('give-up').hidden = true;
  const ms = Math.round(performance.now() - cur.t0);
  const r = await api(`${apiUrl(cur.p)}/finish`, { first_try: firstTry, attempts: cur.wrong + (solved ? 1 : 0), ms });
  results.push(firstTry);
  if (!solved) {                       // show the solution in the position where you got stuck
    const st = cur.step > 0 ? r.best_line[cur.step] : null, u = st ? st.uci : r.solution;
    cur.fen = cur.pos; cur.last = cur.posLast; cur.sel = null; drawBoard();
    board.setFixedArrows([{ from: u.slice(0, 2), to: u.slice(2, 4), color: 'green' }]);
    setMsg(t('Die Lösung ist {san}.', { san: st ? st.label.split(' ').pop() : r.solution_san }), 'bad');
  } else {
    const ok = firstTry ? t('Richtig, im ersten Versuch.') : t('Gelöst, aber nicht im ersten Versuch.');
    setMsg(cur.alt ? ok + ' ' + t('Dein Zug ist ähnlich gut. Die Engine hätte {san} gespielt.', { san: cur.alt }) : ok, firstTry ? 'good' : 'bad');
  }
  setupLines(r, solved);

  const box = el('div', 'explain');
  box.append(costBadge(r.loss, r.eval_best, r.eval_played));
  const p1 = el('p');
  const spent = r.time_spent != null ? t(' nach {s} s Bedenkzeit', { s: Math.round(r.time_spent) }) : '';
  if (r.drill) {
    if (r.played_san) p1.append(t('Hier spielst du oft '), el('b', 'mono', r.played_san), t('. Besser ist '), el('b', 'mono', r.solution_san), '.');
    else p1.append(t('Der beste Zug ist '), el('b', 'mono', r.solution_san), '.');
  } else p1.append(t('In der Partie hast du '), el('b', 'mono', r.played_san), t('{t} gespielt. Besser war ', { t: spent }), el('b', 'mono', r.solution_san), '.');
  box.append(p1);
  if (r.why_played && r.why_played.length) box.append(reasonBlock(r.played_san ? t('Warum {san} schlecht war', { san: r.played_san }) : '', r.why_played));
  if (r.why_best && r.why_best.length) box.append(reasonBlock(t('Was {san} bringt', { san: r.solution_san }), r.why_best));
  for (const m of r.motifs) { const p = el('p'); p.append(el('b', null, m.title + '. '), m.hint); box.append(p); }
  const ctx = [r.opening, ...r.context.filter(c => c !== t('Mit Weiß') && c !== t('Mit Schwarz'))].filter(Boolean).join(' · ');
  if (ctx) box.append(el('p', 'ctx', ctx));
  const links = el('p', 'links');
  if (r.game) {
    const a = el('a', 'textlink', t('Ganze Partie ansehen'));
    a.href = `/game/${r.game}?ply=${encodeURIComponent(r.fen.split(' ')[1] === 'w' ? (cur.p.move_no - 1) * 2 : (cur.p.move_no - 1) * 2 + 1)}`;
    links.append(a);
  }
  links.append(...analysisLinks(r.fen, cur.p.turn));
  box.append(links);
  $('reveal').replaceChildren(box);
  renderProgress();
  $('next').hidden = false;
  $('next').textContent = idx + 1 < puzzles.length ? t('Weiter') : t('Runde beenden');
  $('next').focus({ preventScroll: true });
}

// ---- variations: step through the best line and through what follows your move ----
function setupLines(r, solved) {
  view = { start: r.fen, lines: { best: r.best_line, your: r.your_line }, key: 'best', i: solved ? 0 : -1, solution: r.solution };
  $('ev-best').textContent = r.eval_best != null ? fmtEval(r.eval_best) : '';
  $('ev-your').textContent = r.eval_played != null ? fmtEval(r.eval_played) : '';
  for (const key of ['best', 'your']) {
    $('mv-' + key).replaceChildren(...view.lines[key].map((st, i) => {
      const b = el('button', 'mv', st.label);
      b.addEventListener('click', () => { stopPlay(); go(key, i); });
      return b;
    }));
  }
  $('lines').hidden = false;
  paintLines();
}

function go(key, i) {
  if (!view) return;
  const line = view.lines[key];
  view.key = key; view.i = Math.max(-1, Math.min(line.length - 1, i));
  const st = view.i >= 0 ? line[view.i] : null;
  board.clearDrawings();
  board.setFixedArrows(st ? [] : [{ from: view.solution.slice(0, 2), to: view.solution.slice(2, 4), color: 'green' }]);
  board.set({ fen: st ? st.fen : view.start, sel: null, targets: [], last: st ? [st.uci.slice(0, 2), st.uci.slice(2, 4)] : null });
  paintLines();
}

function paintLines() {
  for (const key of ['best', 'your'])
    [...$('mv-' + key).children].forEach((b, i) => b.classList.toggle('on', view.key === key && i === view.i));
  document.querySelectorAll('.line-row').forEach(row => row.classList.toggle('active', row.dataset.line === view.key));
}

function stopPlay() { if (playTimer) { clearInterval(playTimer); playTimer = null; } $('ln-play').textContent = t('Abspielen'); }

$('ln-start').addEventListener('click', () => { stopPlay(); go(view.key, -1); });
$('ln-prev').addEventListener('click', () => { stopPlay(); go(view.key, view.i - 1); });
$('ln-next').addEventListener('click', () => { stopPlay(); go(view.key, view.i + 1); });
$('ln-play').addEventListener('click', () => {
  if (playTimer) { stopPlay(); return; }
  if (view.i >= view.lines[view.key].length - 1) go(view.key, -1);
  $('ln-play').textContent = t('Anhalten');
  playTimer = setInterval(() => {
    if (view.i >= view.lines[view.key].length - 1) { stopPlay(); return; }
    go(view.key, view.i + 1);
  }, 900);
});
document.querySelectorAll('.line-row .line-head').forEach(h =>
  h.addEventListener('click', () => { stopPlay(); go(h.parentElement.dataset.line, 0); }));

$('give-up').addEventListener('click', () => { if (cur && !cur.finished) finish(false, false); });
$('next').addEventListener('click', () => { if (idx + 1 < puzzles.length) { idx++; show(); scrollTo({ top: 0 }); } else summary(); });
$('again').addEventListener('click', load);
$('arrow-mode').addEventListener('click', e => {
  const on = !board.arrowMode;
  board.setArrowMode(on); if (!on) board.clearDrawings();
  e.target.classList.toggle('on', on); e.target.setAttribute('aria-pressed', on);
  e.target.textContent = on ? t('Pfeile beenden') : t('Pfeile zeichnen');
});
document.addEventListener('keydown', e => {
  if (view && !$('lines').hidden && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
    e.preventDefault(); stopPlay(); go(view.key, view.i + (e.key === 'ArrowRight' ? 1 : -1)); return;
  }
  if (e.key === 'Enter' && !$('next').hidden && document.activeElement !== $('next')) { e.preventDefault(); $('next').click(); }
});

function summary() {
  $('task').hidden = true; $('done').hidden = false; stopPlay();
  const ok = results.filter(Boolean).length;
  $('done-head').textContent = t('{ok} von {n} im ersten Versuch', { ok, n: puzzles.length });
  $('summary').replaceChildren(...puzzles.map((p, i) => {
    const tr = el('tr');
    tr.append(el('td', null, t('Puzzle {i}, Partie vom {date}', { i: i + 1, date: p.date || '?' })),
      el('td', results[i] ? 'v-better' : 'v-worse', results[i] ? t('richtig') : t('kommt beim nächsten Mal wieder')));
    return tr;
  }));
}
load();
