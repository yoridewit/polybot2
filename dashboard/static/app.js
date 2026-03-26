'use strict';

// ─── Navigation ───────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-link').forEach(link => {
  link.addEventListener('click', e => {
    e.preventDefault();
    const target = link.dataset.section;
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    document.getElementById(target).classList.add('active');
    link.classList.add('active');
    loadSection(target);
  });
});

function loadSection(name) {
  if (name === 'overview') loadOverview();
  else if (name === 'bets') loadBets();
  else if (name === 'research') loadMarkets();
  else if (name === 'learning') loadLearning();
  else if (name === 'tokens') loadTokens();
}

function refreshAll() {
  const active = document.querySelector('.section.active');
  if (active) loadSection(active.id);
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
async function api(path) {
  const res = await fetch(path);
  return res.json();
}

function fmt$(v) {
  if (v == null) return '—';
  return '$' + parseFloat(v).toFixed(2);
}

function fmtPct(v) {
  if (v == null) return '—';
  return (parseFloat(v) * 100).toFixed(1) + '%';
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function statusBadge(status) {
  if (status === 'open') return '<span class="badge badge-open">Open</span>';
  if (status === 'resolved_win') return '<span class="badge badge-win">Won</span>';
  if (status === 'resolved_loss') return '<span class="badge badge-loss">Lost</span>';
  return `<span class="badge">${status}</span>`;
}

function sideBadge(side) {
  if (side === 'YES') return '<span class="badge badge-side-yes">YES</span>';
  return '<span class="badge badge-side-no">NO</span>';
}

function pnlSpan(pnl) {
  if (pnl == null) return '<span class="pnl-neu">—</span>';
  const v = parseFloat(pnl);
  if (v > 0) return `<span class="pnl-pos">+$${v.toFixed(2)}</span>`;
  if (v < 0) return `<span class="pnl-neg">-$${Math.abs(v).toFixed(2)}</span>`;
  return `<span class="pnl-neu">$0.00</span>`;
}

// ─── Charts registry ──────────────────────────────────────────────────────────
const charts = {};

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

// ─── OVERVIEW ─────────────────────────────────────────────────────────────────
async function loadOverview() {
  const [p, hist] = await Promise.all([api('/api/portfolio'), api('/api/portfolio/history')]);

  document.getElementById('stat-total').textContent = fmt$(p.total_value);
  document.getElementById('stat-cash').textContent  = fmt$(p.cash_balance);
  const roi = parseFloat(p.roi_pct);
  const roiEl = document.getElementById('stat-roi');
  roiEl.textContent = (roi >= 0 ? '+' : '') + roi.toFixed(2) + '%';
  roiEl.className = 'stat-value ' + (roi >= 0 ? 'pnl-pos' : 'pnl-neg');
  document.getElementById('stat-wr').textContent   = fmtPct(p.win_rate) + ` (${p.num_wins}W/${p.num_losses}L)`;
  document.getElementById('stat-open').textContent = p.num_open_bets;
  const pnlEl = document.getElementById('stat-pnl');
  const pnl = parseFloat(p.realized_pnl);
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmt$(pnl);
  pnlEl.className = 'stat-value ' + (pnl >= 0 ? 'pnl-pos' : 'pnl-neg');

  // Portfolio chart
  destroyChart('portfolioChart');
  const ctx = document.getElementById('portfolioChart').getContext('2d');
  const labels = hist.map(h => fmtDate(h.snapshot_at));
  const values = hist.map(h => h.total_value);
  charts['portfolioChart'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Portfolio Value ($)',
        data: values,
        borderColor: '#6c63ff',
        backgroundColor: 'rgba(108,99,255,0.1)',
        fill: true,
        tension: 0.3,
        pointRadius: 2,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#8892a4', maxTicksLimit: 8 }, grid: { color: '#2e3250' } },
        y: { ticks: { color: '#8892a4', callback: v => '$' + v.toFixed(0) }, grid: { color: '#2e3250' } },
      }
    }
  });
}

// ─── BETS ─────────────────────────────────────────────────────────────────────
async function loadBets() {
  const status = document.getElementById('bet-status-filter').value;
  const cat    = document.getElementById('bet-category-filter').value;
  const params = new URLSearchParams({ limit: 200 });
  if (status) params.set('status', status);
  if (cat)    params.set('category', cat);

  const bets = await api('/api/bets?' + params);
  const tbody = document.getElementById('bets-body');
  tbody.innerHTML = '';

  for (const b of bets) {
    const edge = parseFloat(b.edge || 0);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td title="${b.question}">${b.question ? b.question.substring(0, 60) + (b.question.length > 60 ? '…' : '') : '—'}</td>
      <td>${sideBadge(b.side)}</td>
      <td>${fmt$(b.amount)}</td>
      <td>${(parseFloat(b.price_at_bet || 0) * 100).toFixed(1)}%</td>
      <td>${(parseFloat(b.model_probability || 0) * 100).toFixed(1)}%</td>
      <td class="${edge >= 0 ? 'pnl-pos' : 'pnl-neg'}">${(edge * 100).toFixed(1)}%</td>
      <td>${b.category || '—'}</td>
      <td>${statusBadge(b.status)}</td>
      <td>${pnlSpan(b.pnl)}</td>
      <td>${fmtDate(b.placed_at)}</td>
    `;
    tr.addEventListener('click', () => openBetDetail(b.bet_id));
    tbody.appendChild(tr);
  }

  if (!bets.length) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#8892a4;padding:24px">No bets yet</td></tr>';
  }
}

async function openBetDetail(betId) {
  const d = await api(`/api/bets/${betId}`);
  const bet = d.bet;
  const res = d.research;
  const est = d.estimate;

  let html = `
    <div class="detail-section">
      <h3>Market</h3>
      <p>${bet.question}</p>
    </div>
    <div class="detail-section">
      <h3>Bet Details</h3>
      <table class="kv-table"><tbody>
        <tr><td>Side</td><td>${sideBadge(bet.side)}</td></tr>
        <tr><td>Amount</td><td>${fmt$(bet.amount)}</td></tr>
        <tr><td>Entry Price (YES)</td><td>${(parseFloat(bet.price_at_bet || 0) * 100).toFixed(1)}%</td></tr>
        <tr><td>Model Probability</td><td>${(parseFloat(bet.model_probability || 0) * 100).toFixed(1)}%</td></tr>
        <tr><td>Edge</td><td>${((parseFloat(bet.edge || 0)) * 100).toFixed(1)}%</td></tr>
        <tr><td>Kelly Fraction</td><td>${(parseFloat(bet.kelly_fraction || 0) * 100).toFixed(1)}%</td></tr>
        <tr><td>Research Tier</td><td>${bet.research_tier || '—'}</td></tr>
        <tr><td>Status</td><td>${statusBadge(bet.status)}</td></tr>
        <tr><td>P&L</td><td>${pnlSpan(bet.pnl)}</td></tr>
        <tr><td>Placed</td><td>${fmtDate(bet.placed_at)}</td></tr>
        ${bet.resolved_at ? `<tr><td>Resolved</td><td>${fmtDate(bet.resolved_at)}</td></tr>` : ''}
      </tbody></table>
    </div>`;

  if (est.reasoning) {
    html += `
    <div class="detail-section">
      <h3>Claude's Reasoning</h3>
      <p>${est.reasoning}</p>
      ${est.key_factors && est.key_factors.length ? `<ul>${est.key_factors.map(f => `<li>${f}</li>`).join('')}</ul>` : ''}
    </div>`;
  }

  if (res.summary) {
    html += `
    <div class="detail-section">
      <h3>Research Summary (${res.tier || ''})</h3>
      <p>${res.summary}</p>
      ${res.key_facts && res.key_facts.length ? `<ul>${res.key_facts.map(f => `<li>${f}</li>`).join('')}</ul>` : ''}
    </div>`;
  }

  if (res.search_queries && res.search_queries.length) {
    html += `
    <div class="detail-section">
      <h3>Search Queries Used</h3>
      <ul>${res.search_queries.map(q => `<li>${q}</li>`).join('')}</ul>
    </div>`;
  }

  document.getElementById('bet-detail-content').innerHTML = html;
  document.getElementById('bet-detail').classList.remove('hidden');
}

function closeBetDetail() {
  document.getElementById('bet-detail').classList.add('hidden');
}

// ─── MARKETS (Research Log) ───────────────────────────────────────────────────
async function loadMarkets() {
  const markets = await api('/api/markets?limit=100');
  const tbody = document.getElementById('markets-body');
  tbody.innerHTML = '';

  for (const m of markets) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td title="${m.question}">${m.question ? m.question.substring(0, 70) + (m.question.length > 70 ? '…' : '') : '—'}</td>
      <td>${m.category || '—'}</td>
      <td>${(parseFloat(m.yes_price_latest || 0) * 100).toFixed(1)}%</td>
      <td>${fmt$(m.volume_24h_latest)}</td>
      <td>${m.research_tier || 'none'}</td>
      <td>${fmtDate(m.last_seen_at)}</td>
    `;
    tbody.appendChild(tr);
  }

  if (!markets.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#8892a4;padding:24px">No markets scanned yet</td></tr>';
  }
}

// ─── LEARNING ─────────────────────────────────────────────────────────────────
async function loadLearning() {
  const [learn, calib, catPerf] = await Promise.all([
    api('/api/learning'),
    api('/api/calibration'),
    api('/api/category-performance'),
  ]);

  // Params table
  const p = learn.params;
  const tbody = document.getElementById('params-body');
  const rows = [
    ['Min Edge Threshold', (p.min_edge_threshold * 100).toFixed(1) + '%'],
    ['Kelly Fraction Multiplier', (p.kelly_fraction_multiplier * 100).toFixed(0) + '%'],
    ['Max Bet Fraction', (p.max_bet_fraction * 100).toFixed(0) + '% of portfolio'],
    ['Tier 2 Research Threshold', (p.tier2_edge_threshold * 100).toFixed(1) + '%'],
    ['Min Confidence', (p.min_confidence * 100).toFixed(0) + '%'],
    ['Calibration Bias', (p.calibration_bias * 100).toFixed(2) + '%'],
    ['Total Resolved Bets', p.total_resolved],
    ['Params Version', 'v' + p.version],
    ['Last Updated', fmtDate(p.last_updated)],
  ];
  tbody.innerHTML = rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('');

  // Calibration chart
  destroyChart('calibrationChart');
  const ctx2 = document.getElementById('calibrationChart').getContext('2d');
  charts['calibrationChart'] = new Chart(ctx2, {
    type: 'bar',
    data: {
      labels: calib.map(c => c.bucket),
      datasets: [
        {
          label: 'Actual Win Rate',
          data: calib.map(c => (c.actual_win_rate * 100).toFixed(1)),
          backgroundColor: 'rgba(108,99,255,0.6)',
        },
        {
          label: 'Perfect Calibration',
          data: calib.map(c => c.predicted_win_rate * 100),
          type: 'line',
          borderColor: '#ef4444',
          borderWidth: 2,
          pointRadius: 0,
          fill: false,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8892a4' } } },
      scales: {
        x: { ticks: { color: '#8892a4' }, grid: { color: '#2e3250' } },
        y: { ticks: { color: '#8892a4', callback: v => v + '%' }, grid: { color: '#2e3250' }, max: 100, min: 0 },
      }
    }
  });

  // Category chart
  destroyChart('categoryChart');
  const ctx3 = document.getElementById('categoryChart').getContext('2d');
  charts['categoryChart'] = new Chart(ctx3, {
    type: 'bar',
    data: {
      labels: catPerf.map(c => c.category),
      datasets: [
        {
          label: 'Win Rate %',
          data: catPerf.map(c => (c.win_rate * 100).toFixed(1)),
          backgroundColor: catPerf.map(c => c.win_rate >= 0.5 ? 'rgba(34,197,94,0.6)' : 'rgba(239,68,68,0.6)'),
          yAxisID: 'y',
        },
        {
          label: 'Total P&L ($)',
          data: catPerf.map(c => c.total_pnl),
          type: 'line',
          borderColor: '#6c63ff',
          borderWidth: 2,
          pointRadius: 4,
          fill: false,
          yAxisID: 'y2',
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8892a4' } } },
      scales: {
        x: { ticks: { color: '#8892a4' }, grid: { color: '#2e3250' } },
        y:  { ticks: { color: '#8892a4', callback: v => v + '%' }, grid: { color: '#2e3250' }, position: 'left' },
        y2: { ticks: { color: '#8892a4', callback: v => '$' + v }, grid: { display: false }, position: 'right' },
      }
    }
  });

  // History table
  const hbody = document.getElementById('history-body');
  hbody.innerHTML = learn.history.map(h => `
    <tr>
      <td>${fmtDate(h.changed_at)}</td>
      <td>${h.param_name}</td>
      <td style="color:#8892a4">${h.old_value}</td>
      <td style="color:#6c63ff;font-weight:600">${h.new_value}</td>
      <td style="color:#8892a4;font-size:12px">${h.reason || '—'}</td>
    </tr>
  `).join('') || '<tr><td colspan="5" style="text-align:center;color:#8892a4;padding:24px">No parameter changes yet</td></tr>';
}

// ─── TOKEN USAGE ──────────────────────────────────────────────────────────────
async function loadTokens() {
  const tok = await api('/api/token-usage');

  document.getElementById('tok-used').textContent   = tok.today_used.toLocaleString();
  document.getElementById('tok-budget').textContent = tok.daily_budget.toLocaleString();
  const pct = parseFloat(tok.budget_pct);
  const pctEl = document.getElementById('tok-pct');
  pctEl.textContent = pct.toFixed(1) + '%';
  pctEl.className = 'stat-value ' + (pct > 80 ? 'pnl-neg' : pct > 50 ? 'pnl-neu' : 'pnl-pos');

  // Group history by date
  const byDate = {};
  for (const row of tok.history) {
    const d = row.usage_date;
    if (!byDate[d]) byDate[d] = { haiku: 0, sonnet: 0 };
    if (row.model && row.model.includes('haiku')) byDate[d].haiku += row.total_tokens;
    else byDate[d].sonnet += row.total_tokens;
  }
  const dates = Object.keys(byDate).sort();

  destroyChart('tokenChart');
  const ctx = document.getElementById('tokenChart').getContext('2d');
  charts['tokenChart'] = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: dates,
      datasets: [
        {
          label: 'Haiku tokens',
          data: dates.map(d => byDate[d].haiku),
          backgroundColor: 'rgba(59,130,246,0.6)',
          stack: 'tokens',
        },
        {
          label: 'Sonnet tokens',
          data: dates.map(d => byDate[d].sonnet),
          backgroundColor: 'rgba(108,99,255,0.6)',
          stack: 'tokens',
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8892a4' } } },
      scales: {
        x: { ticks: { color: '#8892a4' }, grid: { color: '#2e3250' }, stacked: true },
        y: { ticks: { color: '#8892a4' }, grid: { color: '#2e3250' }, stacked: true },
      }
    }
  });
}

// ─── Initial load ─────────────────────────────────────────────────────────────
loadOverview();
