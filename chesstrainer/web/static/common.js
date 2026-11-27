// Sync button, status line and tooltips (shared by all pages).
(function () {
  const st = document.getElementById('status'), btn = document.getElementById('sync-btn');
  let timer = null;
  async function refresh(reloadWhenDone) {
    let s;
    try { const r = await fetch('/api/stats'); if (r.status === 401) { location.href = window.MULTI ? '/' : '/login'; return; } s = await r.json(); }
    catch (e) { return; }
    const j = s.job || {};
    st.className = 'status' + (j.running ? ' busy' : j.error ? ' err' : '');
    if (j.running) {
      st.textContent = j.msg; btn.disabled = true; btn.textContent = t('Läuft');
      if (!timer) timer = setInterval(() => refresh(true), 1500);
    } else {
      btn.disabled = false; btn.textContent = t('Aktualisieren');
      st.textContent = j.error ? j.error : (reloadWhenDone && j.new === 0 ? t('Keine neuen Partien') : '');
      if (timer) { clearInterval(timer); timer = null; if (reloadWhenDone && !j.error && j.new > 0) location.reload(); }
    }
  }
  btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = t('Läuft');
    try { await fetch('/api/sync', { method: 'POST' }); } catch (e) { st.className = 'status err'; st.textContent = t('Server nicht erreichbar'); }
    refresh(true);
  });
  refresh(false);

  const tip = document.getElementById('tip');
  document.addEventListener('mousemove', e => {
    const t = e.target.closest ? e.target.closest('[data-tip]') : null;
    if (!t) { tip.style.display = 'none'; return; }
    tip.textContent = t.dataset.tip; tip.style.display = 'block';
    tip.style.left = Math.min(e.clientX + 14, innerWidth - 300) + 'px';
    tip.style.top = (e.clientY + 16) + 'px';
  });
  document.querySelectorAll('tr.link[data-href]').forEach(tr =>
    tr.addEventListener('click', e => { if (!e.target.closest('a')) location.href = tr.dataset.href; }));
})();
