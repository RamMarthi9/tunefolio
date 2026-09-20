function setDataState(message, connection = 'Data unavailable') {
  const banner = document.getElementById('data-state');
  banner.hidden = false;
  document.getElementById('data-state-message').textContent = message;
  document.getElementById('connection-status').textContent = connection;
  document.getElementById('logoutBtn').hidden = connection === 'Not connected';
  for (const id of ['changes-status', 'performance-status']) document.getElementById(id).textContent = message;
  document.querySelectorAll('td.loading').forEach(el => { el.textContent = message; });
}

function initializeNavigation() {
  const sync = document.createElement('button');
  sync.type = 'button';
  sync.textContent = "Sync today's trades";
  const syncStatus = document.createElement('p');
  syncStatus.setAttribute('role', 'status');
  syncStatus.textContent = 'Trade sync covers today only; earlier history requires an owner-verified import.';
  document.querySelector('[data-section-id="historical"]').prepend(sync, syncStatus);
  sync.addEventListener('click', async () => {
    sync.disabled = true;
    syncStatus.textContent = "Syncing today's trades…";
    try {
      const response = await fetch('/portfolio/trade-sync/trigger', {method: 'POST', credentials: 'include'});
      if (!response.ok) throw new Error('unavailable');
      const result = await response.json();
      if (result.status !== 'ok') throw new Error('unavailable');
      syncStatus.textContent = `${result.inserted} new trades saved from ${result.fetched} trades today. Reload to update history. Earlier history is not included.`;
    } catch (_) {
      syncStatus.textContent = 'Trade sync unavailable. Reconnect or retry shortly. Existing history is preserved.';
    } finally { sync.disabled = false; }
  });
  document.getElementById('holding-search').addEventListener('input', () => {
    const rows = getFilteredAndSorted();
    renderHoldingsTable(rows);
    document.getElementById('holdings-count').textContent = `${rows.length} matching holdings`;
  });
  document.getElementById('holding-sort').addEventListener('change', e => {
    currentSort = {key: e.target.value, dir: e.target.value === 'symbol' ? 'asc' : 'desc'};
    renderHoldingsTable(getFilteredAndSorted());
  });
  const groups = {allocation: 'overview', analysis: 'holdings', holdings: 'holdings', historical: 'history', comparative: 'performance'};
  Object.entries(groups).forEach(([section, view]) => {
    document.querySelector(`[data-section-id="${section}"]`).dataset.viewPanel = view;
  });
  const content = document.querySelector('main.content');
  content.insertBefore(document.querySelector('[data-section-id="holdings"]'), document.querySelector('[data-section-id="analysis"]'));
  const allocationCharts = document.querySelectorAll('.allocation-chart');
  allocationCharts[1].hidden = true;
  document.getElementById('allocation-basis').addEventListener('change', e => {
    allocationCharts[0].hidden = e.target.value !== 'current';
    allocationCharts[1].hidden = e.target.value !== 'invested';
    window.dispatchEvent(new Event('resize'));
  });
  document.querySelectorAll('th.sortable').forEach(th => {
    th.tabIndex = 0;
    th.setAttribute('aria-label', `Sort by ${th.textContent.trim()}`);
    th.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); th.click(); }
    });
  });
  function show(view) {
    if (!['overview', 'holdings', 'performance', 'history'].includes(view)) view = 'overview';
    document.querySelectorAll('[data-view-panel]').forEach(el => { el.hidden = el.dataset.viewPanel !== view; });
    document.querySelector('.kpi-strip').hidden = view !== 'overview';
    document.querySelectorAll('[data-view]').forEach(btn => {
      if (btn.dataset.view === view) btn.setAttribute('aria-current', 'page');
      else btn.removeAttribute('aria-current');
    });
    // Panels becoming visible need a Chart.js resize.
    window.dispatchEvent(new Event('resize'));
  }
  document.querySelectorAll('[data-view]').forEach(btn => btn.addEventListener('click', () => {
    location.hash = btn.dataset.view;
    show(btn.dataset.view);
  }));
  window.addEventListener('hashchange', () => show(location.hash.slice(1)));
  document.getElementById('retry-data').addEventListener('click', () => location.reload());
  show(location.hash.slice(1));
}

async function loadEvidence() {
  for (const type of ['changes', 'performance']) {
    const target = document.getElementById(`${type}-status`);
    try {
      const response = await fetch(`/portfolio/${type}`, {credentials: 'include'});
      if (!response.ok) throw new Error('unavailable');
      const data = await response.json();
      if (data.status !== 'available') {
        target.textContent = data.reason;
        continue;
      }
      if (type === 'performance') {
        target.textContent = `${data.return_pct.toFixed(2)}% time-weighted return`;
        const result = document.getElementById('performance-result');
        result.textContent = `${data.points[0].at} to ${data.points.at(-1).at}. ${data.method}`;
      } else {
        target.textContent = `Between retrievals ${new Date(data.from).toLocaleString()} and ${new Date(data.to).toLocaleString()}`;
        const list = document.getElementById('changes-list');
        list.replaceChildren();
        const changed = data.data.filter(row => row.value_change !== 0);
        if (!changed.length) target.textContent += ' — no holding-value changes observed.';
        changed.slice(0, 5).forEach(row => {
          const li = document.createElement('li');
          li.textContent = row.status === 'available'
            ? `${row.symbol}: ${formatINR(row.value_change)} value change; ${formatINR(row.price_effect)} from prices, ${formatINR(row.quantity_effect)} from quantity changes (${row.quantity_before} → ${row.quantity_after}).`
            : `${row.symbol}: cannot explain change because prices are missing.`;
          li.title = row.explanation || '';
          list.appendChild(li);
        });
      }
    } catch (_) { target.textContent = 'Could not load this data. Retry when the service is available.'; }
  }
}
