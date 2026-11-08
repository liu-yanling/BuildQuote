/* BuildQuote 工作台 · 前端逻辑（原生 JS，无构建） */

/* ===== 通用工具 ===== */
async function postJSON(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const d = await r.json().catch(() => ({ error: '响应非 JSON（HTTP ' + r.status + '）' }));
  return { ok: r.ok, status: r.status, d };
}
function fmtMoney(x) { return Number(x || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }); }
function fmtPct(x) { return (Number(x || 0) * 100).toFixed(1) + '%'; }
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function setBusy(btnId, busy, label) {
  const b = document.getElementById(btnId);
  if (!b) return;
  b.disabled = busy;
  if (busy) { if (!b.dataset.label) b.dataset.label = b.innerHTML; b.innerHTML = '<span class="spinner"></span>' + (label || '处理中…'); }
  else b.innerHTML = b.dataset.label || b.innerHTML;
}
function errText(d) { return d.detail || d.error || (typeof d === 'string' ? d : JSON.stringify(d)); }
const DEMO_USER = { user_id: 'demo', name: '成本经理', role: '成本经理' };

/* ===== 导航 ===== */
function switchView(name) {
  document.querySelectorAll('.nav-item').forEach(a => a.classList.toggle('active', a.dataset.view === name));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  if (name === 'monitor') checkHealth();
  if (name === 'eval') renderMetrics();
}

/* ===== 健康状态 ===== */
async function checkHealth() {
  const el = document.getElementById('health');
  try {
    const r = await fetch('/health');
    const d = await r.json();
    const emb = d.embedding || {};
    const txt = emb.degraded === false ? '本地向量 · 正常' : (emb.configured ? '向量 · API' : '向量 · 降级');
    const wf = (d.workflows && d.workflows.length) ? d.workflows.length + ' 个工作流' : '';
    el.innerHTML = '<span class="dot ok"></span>' + esc(txt) + (wf ? '<br><small>' + esc(wf) + '</small>' : '');
  } catch (e) {
    el.innerHTML = '<span class="dot err"></span>后端未连接';
  }
}

/* ===== ① 成本监控 ===== */
const SAMPLE_BUDGET = [
  ['人工费', 1000, '按劳务分包合同预估'], ['材料费', 2000, '含钢材 / 商品混凝土'], ['机械使用费', 500, '塔吊 / 施工电梯'],
  ['分包费', 3000, '装饰 / 安装分包'], ['其他直接费', 800, '措施费 / 临时设施'], ['现场管理费', 600, '现场管理人员'],
  ['公司管理费', 400, '按比例分摊'], ['税金', 300, '增值税'], ['预备费', 500, '不可预见费']
];
const SAMPLE_SOURCES = [
  ['合同', '劳务', 620, 'HT-RG-001', '2026-09-30', '成都建工劳务有限公司', '人工费合同累计'],
  ['进度款', '劳务', 440, 'JD-2026-09', '2026-10-25', '成都建工劳务有限公司', '9 月进度款'],
  ['合同', '钢材', 1200, 'HT-GC-002', '2026-09-15', '四川钢材商贸有限公司', '钢材采购合同'],
  ['进度款', '商品混凝土', 780, 'JD-2026-09', '2026-10-25', '成都商砼有限公司', 'C30 混凝土'],
  ['签证', '钢材', 380, 'QZ-2026-014', '2026-10-18', '四川钢材商贸有限公司', '设计变更增加钢材'],
  ['合同', '机械', 300, 'HT-JX-003', '2026-09-10', '成都机械租赁有限公司', '塔吊租赁'],
  ['进度款', '机械', 205, 'JD-2026-09', '2026-10-25', '成都机械租赁有限公司', '台班费'],
  ['合同', '分包', 2100, 'HT-FB-004', '2026-08-20', '四川壹术装饰工程有限公司', '装饰分包'],
  ['进度款', '分包', 900, 'JD-2026-09', '2026-10-25', '四川壹术装饰工程有限公司', '9 月产值'],
  ['进度款', '措施费', 520, 'JD-2026-09', '2026-10-25', '项目现场', '安全文明施工费'],
  ['签证', '措施费', 300, 'QZ-2026-015', '2026-10-20', '项目现场', '临时设施增补'],
  ['合同', '现场经费', 400, 'HT-XC-005', '2026-09-01', '项目现场', '现场管理人员'],
  ['进度款', '现场经费', 260, 'JD-2026-09', '2026-10-25', '项目现场', '10 月现场经费'],
  ['合同', '管理费', 380, 'HT-GL-006', '2026-09-01', '公司本部', '公司管理费分摊'],
  ['进度款', '税金', 310, 'JD-2026-09', '2026-10-25', '税务机关', '9 月增值税'],
  ['合同', '预备费', 520, 'HT-YB-007', '2026-09-01', '预备费', '不可预见费']
];

function addBudgetRow(subject, budget, note) {
  const tr = document.createElement('tr');
  tr.innerHTML = '<td><input class="c-subject" value="' + esc(subject == null ? '' : subject) + '" placeholder="科目"></td>' +
    '<td><input class="c-budget" type="number" step="0.01" value="' + esc(budget == null ? '' : budget) + '" placeholder="金额"></td>' +
    '<td class="num c-pct">—</td>' +
    '<td><input class="c-note" value="' + esc(note == null ? '' : note) + '" placeholder="如：按合同预估"></td>' +
    '<td><button class="btn-mini del" onclick="this.closest(\'tr\').remove();calcTotals()">删除</button></td>';
  document.getElementById('budget-tbody').appendChild(tr);
  calcTotals();
}
function addSourceRow(type, subject, amount, id, date, supplier, note) {
  const tr = document.createElement('tr');
  tr.innerHTML = '<td><input class="c-type" value="' + esc(type || '') + '"></td>' +
    '<td><input class="c-subject" value="' + esc(subject || '') + '" placeholder="科目/别名"></td>' +
    '<td><input class="c-amount" type="number" step="0.01" value="' + esc(amount == null ? '' : amount) + '"></td>' +
    '<td><input class="c-id" value="' + esc(id || '') + '"></td>' +
    '<td><input class="c-date" type="date" value="' + esc(date || '') + '"></td>' +
    '<td><input class="c-supplier" value="' + esc(supplier || '') + '" placeholder="供应商/对方"></td>' +
    '<td><input class="c-note" value="' + esc(note || '') + '" placeholder="备注"></td>' +
    '<td><button class="btn-mini del" onclick="this.closest(\'tr\').remove();calcTotals()">删除</button></td>';
  document.getElementById('source-tbody').appendChild(tr);
  calcTotals();
}
function loadSample() {
  document.getElementById('budget-tbody').innerHTML = '';
  document.getElementById('source-tbody').innerHTML = '';
  SAMPLE_BUDGET.forEach(r => addBudgetRow(r[0], r[1], r[2]));
  SAMPLE_SOURCES.forEach(r => addSourceRow(r[0], r[1], r[2], r[3], r[4], r[5], r[6]));
  calcTotals();
  document.getElementById('monitor-result').innerHTML = '<div class="empty">已加载样例数据（9 个科目 / 16 笔成本来源），点「运行监控」查看归集台账与预警。</div>';
}
/* 表格底部/顶部汇总：随输入实时重算（仅用于界面展示，正式数字仍由规则引擎计算） */
function calcTotals() {
  const budgetRows = [...document.querySelectorAll('#budget-tbody tr')];
  const budgets = budgetRows.map(tr => parseFloat(tr.querySelector('.c-budget').value) || 0);
  const budgetTotal = budgets.reduce((a, b) => a + b, 0);
  budgetRows.forEach((tr, i) => {
    const cell = tr.querySelector('.c-pct');
    if (cell) cell.textContent = budgetTotal > 0 ? (budgets[i] / budgetTotal * 100).toFixed(1) + '%' : '—';
  });
  const bTotal = document.getElementById('budget-total');
  const bPct = document.getElementById('budget-total-pct');
  const bNote = document.getElementById('budget-total-note');
  const bSum = document.getElementById('budget-sum');
  if (bTotal) bTotal.textContent = fmtMoney(budgetTotal);
  if (bPct) bPct.textContent = budgetTotal > 0 ? '100.0%' : '—';
  if (bNote) bNote.textContent = '共 ' + budgetRows.length + ' 个科目';
  if (bSum) bSum.textContent = '合计 ' + fmtMoney(budgetTotal) + ' 万元';

  const sourceRows = [...document.querySelectorAll('#source-tbody tr')];
  const amounts = sourceRows.map(tr => parseFloat(tr.querySelector('.c-amount').value) || 0);
  const sourceTotal = amounts.reduce((a, b) => a + b, 0);
  const sTotal = document.getElementById('source-total');
  const sNote = document.getElementById('source-total-note');
  const sSum = document.getElementById('source-sum');
  if (sTotal) sTotal.textContent = fmtMoney(sourceTotal);
  if (sNote) sNote.textContent = '共 ' + sourceRows.length + ' 笔';
  if (sSum) {
    const diff = sourceTotal - budgetTotal;
    sSum.textContent = '合计 ' + fmtMoney(sourceTotal) + ' 万元（较预算 ' + (diff >= 0 ? '+' : '') + fmtMoney(diff) + '）';
  }
  renderTypeSubtotals(sourceRows);
}

/* 按来源类型（合同 / 进度款 / 签证）实时小计 */
function renderTypeSubtotals(sourceRows) {
  const box = document.getElementById('source-subtotals');
  if (!box) return;
  const groups = new Map();
  sourceRows.forEach(tr => {
    const type = tr.querySelector('.c-type').value.trim() || '未分类';
    const amount = parseFloat(tr.querySelector('.c-amount').value) || 0;
    const g = groups.get(type) || { total: 0, count: 0 };
    g.total += amount; g.count += 1;
    groups.set(type, g);
  });
  const order = ['合同', '进度款', '签证'];
  const entries = [...groups.entries()].sort((a, b) => {
    const ia = order.indexOf(a[0]); const ib = order.indexOf(b[0]);
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a[0].localeCompare(b[0]);
  });
  box.innerHTML = entries.length
    ? '<span class="type-label">来源类型小计</span>' + entries.map(([type, g]) =>
        '<span class="type-chip"><b>' + esc(type) + '</b>' + fmtMoney(g.total) + ' 万元<em>' + g.count + ' 笔</em></span>'
      ).join('')
    : '';
}

/* 导入 / 导出（模板、完整台账） */
function downloadBlob(blob, filename) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
}
function monitorMsg(html) {
  const el = document.getElementById('monitor-result');
  if (el) el.innerHTML = html;
}
async function exportTemplate() {
  try {
    const resp = await fetch('/api/cost/template', { cache: 'no-store' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    downloadBlob(await resp.blob(), '成本台账模板.xlsx');
    monitorMsg('<div class="empty">已下载「成本台账模板.xlsx」：预算 / 成本来源 / 填写说明 三张表，填好后点「导入台账」。</div>');
  } catch (e) {
    monitorMsg('<div class="empty">模板下载失败：' + esc(String(e.message || e)) + '</div>');
  }
}
async function exportExcel() {
  const budget_rows = readBudgetRows();
  const sources = readSources();
  if (!budget_rows.length && !sources.length) {
    monitorMsg('<div class="empty">请先填写预算 / 成本来源，或点「加载样例」后再导出。</div>');
    return;
  }
  setBusy('monitor-btn', true, '导出中…');
  try {
    const resp = await fetch('/api/cost/export', {
      method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        budget_rows: budget_rows, sources: sources,
        yellow_ratio: parseFloat(document.getElementById('yellow').value) || 0.05,
        red_ratio: parseFloat(document.getElementById('red').value) || 0.10
      })
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    downloadBlob(await resp.blob(), '成本台账.xlsx');
    monitorMsg('<div class="empty">已导出「成本台账.xlsx」：预算 ' + budget_rows.length + ' 项 / 成本来源 ' +
      sources.length + ' 笔（含台账与合计行）。</div>');
  } catch (e) {
    monitorMsg('<div class="empty">导出失败：' + esc(String(e.message || e)) + '</div>');
  }
  setBusy('monitor-btn', false);
}
function triggerImport() {
  const input = document.getElementById('ledger-file');
  if (input) input.click();
}
function loadLedgerRows(budget_rows, sources) {
  document.getElementById('budget-tbody').innerHTML = '';
  document.getElementById('source-tbody').innerHTML = '';
  (budget_rows || []).forEach(r => addBudgetRow(r.subject, r.budget, r.note));
  (sources || []).forEach(r => addSourceRow(r.source_type, r.subject, r.amount, r.source_id, r.date, r.supplier, r.note));
  calcTotals();
}
async function handleImportFile(ev) {
  const input = ev && ev.target;
  const file = input && input.files ? input.files[0] : null;
  if (!file) return;
  input.value = '';
  try {
    const buf = await file.arrayBuffer();
    const resp = await fetch('/api/cost/import', {
      method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: buf
    });
    const d = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error((d && (d.detail || d.error)) || ('HTTP ' + resp.status));
    const nb = (d.budget_rows || []).length, ns = (d.sources || []).length;
    loadLedgerRows(d.budget_rows, d.sources);
    monitorMsg('<div class="empty">已导入台账：预算 ' + nb + ' 项 / 成本来源 ' + ns + ' 笔。点「运行监控」查看归集与预警。</div>');
  } catch (e) {
    monitorMsg('<div class="empty">导入失败：' + esc(String(e.message || e)) + '</div>');
  }
}

function readBudgetRows() {
  return [...document.querySelectorAll('#budget-tbody tr')].map(tr => ({
    subject: tr.querySelector('.c-subject').value.trim(),
    budget: parseFloat(tr.querySelector('.c-budget').value) || 0,
    note: tr.querySelector('.c-note').value.trim()
  })).filter(r => r.subject);
}
function readSources() {
  return [...document.querySelectorAll('#source-tbody tr')].map(tr => {
    const date = tr.querySelector('.c-date').value.trim();
    const supplier = tr.querySelector('.c-supplier').value.trim();
    const note = tr.querySelector('.c-note').value.trim();
    return {
      source_type: tr.querySelector('.c-type').value.trim() || '进度款',
      subject: tr.querySelector('.c-subject').value.trim(),
      amount: parseFloat(tr.querySelector('.c-amount').value) || 0,
      source_id: tr.querySelector('.c-id').value.trim(),
      date: date,
      supplier: supplier,
      note: [date, supplier, note].filter(Boolean).join(' · ')
    };
  }).filter(r => r.subject);
}

async function runMonitor() {
  const budget_rows = readBudgetRows();
  const sources = readSources();
  if (!budget_rows.length || !sources.length) {
    document.getElementById('monitor-result').innerHTML = '<div class="empty">请先填写预算与成本来源（或点「加载样例」）。</div>';
    return;
  }
  const yellow = parseFloat(document.getElementById('yellow').value) || 0.05;
  const red = parseFloat(document.getElementById('red').value) || 0.10;
  const body = { budget_rows, sources, yellow_ratio: yellow, red_ratio: red, user: DEMO_USER };

  setBusy('monitor-btn', true);
  const { ok, d } = await postJSON('/api/cost/monitor', body);
  setBusy('monitor-btn', false);
  if (!ok) { document.getElementById('monitor-result').innerHTML = '<div class="empty">错误：' + esc(errText(d)) + '</div>'; return; }
  renderMonitor(d);
}

function renderMonitor(d) {
  const ledger = d.ledger || [];
  const alerts = d.alerts || [];
  const rows = ledger.map(r => {
    const dev = r.deviation_ratio || 0;
    const cls = dev > 0 ? 'dev-up' : (dev < 0 ? 'dev-down' : 'dev-flat');
    const badge = dev >= (d.red_ratio || 0.10) ? '<span class="badge red">红警</span>'
      : dev >= (d.yellow_ratio || 0.05) ? '<span class="badge yellow">黄警</span>'
      : '<span class="badge normal">正常</span>';
    return '<tr><td>' + esc(r.subject) + '</td><td class="num">' + fmtMoney(r.budget) + '</td>' +
      '<td class="num">' + fmtMoney(r.actual) + '</td><td class="num ' + cls + '">' + (r.deviation >= 0 ? '+' : '') + fmtMoney(r.deviation) + '</td>' +
      '<td class="num ' + cls + '">' + fmtPct(dev) + '</td><td>' + badge + '</td></tr>';
  }).join('');

  const totBudget = ledger.reduce((s, r) => s + (Number(r.budget) || 0), 0);
  const totActual = ledger.reduce((s, r) => s + (Number(r.actual) || 0), 0);
  const totDev = totActual - totBudget;
  const totCls = totDev > 0 ? 'dev-up' : (totDev < 0 ? 'dev-down' : 'dev-flat');
  const totalRow = '<tr class="total-row"><td>合计</td><td class="num">' + fmtMoney(totBudget) + '</td>' +
    '<td class="num">' + fmtMoney(totActual) + '</td><td class="num ' + totCls + '">' + (totDev >= 0 ? '+' : '') + fmtMoney(totDev) + '</td>' +
    '<td class="num ' + totCls + '">' + (totBudget ? fmtPct(totDev / totBudget) : '—') + '</td><td></td></tr>';

  const alertHtml = alerts.length ? alerts.map(a => {
    const lv = a.level === '红警' ? 'red' : 'yellow';
    return '<div class="alert-item ' + lv + '"><span class="lv">' + esc(a.level) + ' ' + esc(a.subject) + '</span>' +
      '<span class="detail">偏差 +' + fmtMoney(a.deviation) + '（' + fmtPct(a.deviation_ratio) + '）</span></div>';
  }).join('') : '<div class="empty" style="padding:14px">（无超支/超付预警）</div>';

  const f = d.formulas || {};
  const formulaHtml = Object.keys(f).map(k => '<div><code>' + esc(k) + '</code> = ' + esc(f[k]) + '</div>').join('');

  document.getElementById('monitor-result').innerHTML =
    '<div class="card"><div class="card-head"><b>成本台账（' + esc(d.rule_version || '') + '）</b>' +
    '<span class="card-sum">预算 ' + fmtMoney(totBudget) + ' / 实际 ' + fmtMoney(totActual) + ' 万元</span></div>' +
    '<table class="result"><thead><tr><th>科目</th><th class="num">预算</th><th class="num">实际</th><th class="num">偏差</th><th class="num">偏差率</th><th>状态</th></tr></thead>' +
    '<tbody>' + rows + '</tbody><tfoot>' + totalRow + '</tfoot></table></div>' +
    '<div class="card"><div class="card-head"><b>预警清单</b><span class="muted">黄警 ≥' + fmtPct(d.yellow_ratio) + ' / 红警 ≥' + fmtPct(d.red_ratio) + '</span></div>' + alertHtml + '</div>' +
    '<div class="card"><div class="card-head"><b>口径公式</b></div><div class="formulas">' + formulaHtml + '</div></div>';
}

/* ===== ② 合同问答 ===== */
/* 回答用自然语言展示：按段落渲染，仅保留少量行内强调；
   若模型仍漏出 Markdown 标记，这里再兜底洗一遍（标题/列表/分割线符号）。 */
function renderAnswer(text) {
  const clean = String(text == null ? '' : text)
    .split('\n')
    .map(line => {
      let l = line.replace(/\u200b/g, '');
      if (/^\s*([-*_]\s*){3,}$/.test(l)) return '';
      l = l.replace(/^\s*>\s*/, '');
      l = l.replace(/^\s*#{1,6}\s*/, '');
      l = l.replace(/^\s*[-*+•]\s+/, '');
      l = l.replace(/^\s*\d+[.)、]\s*/, '');
      return l.trimEnd();
    })
    .join('\n');
  const paras = clean.split(/\n{2,}/).map(block => {
    const lines = block.split('\n').map(s => s.trim()).filter(Boolean);
    if (!lines.length) return '';
    const inline = s => esc(s)
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`\n]+)`/g, '<code>$1</code>');
    return '<p>' + lines.map(inline).join('<br>') + '</p>';
  }).filter(Boolean);
  return paras.join('') || '<p>（空回答）</p>';
}

function addMsg(role, text, citations) {
  const chat = document.getElementById('chat');
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  if (role === 'assistant') {
    div.classList.add('answer');
    div.innerHTML = renderAnswer(text);
  } else {
    div.textContent = text;
  }
  if (citations && citations.length) {
    const cite = document.createElement('div');
    cite.className = 'cite';
    citations.forEach(c => { const s = document.createElement('span'); s.textContent = c; cite.appendChild(s); });
    div.appendChild(cite);
  }
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

async function ask() {
  const q = document.getElementById('qa-input').value.trim();
  if (!q) return;
  document.getElementById('qa-input').value = '';
  addMsg('user', q);
  const st = document.getElementById('qa-status');
  st.innerHTML = '<span class="spinner"></span>检索 + 回答中（首次 5–10 秒）…';
  const { ok, d } = await postJSON('/api/qa/ask', { question: q, top_k: 6 });
  st.innerHTML = '';
  if (!ok) { addMsg('assistant', '错误：' + errText(d)); return; }
  addMsg('assistant', d.answer || '(空回答)', d.citations || []);
}

async function search() {
  const q = document.getElementById('search-input').value.trim();
  if (!q) return;
  const st = document.getElementById('search-status');
  st.innerHTML = '<span class="spinner"></span>检索中…';
  const { ok, d } = await postJSON('/api/knowledge/search', { query: q, top_k: 5 });
  st.innerHTML = '';
  const box = document.getElementById('hits');
  if (!ok) { box.innerHTML = '<div class="empty" style="padding:14px">错误：' + esc(errText(d)) + '</div>'; return; }
  const hits = d.hits || [];
  if (!hits.length) { box.innerHTML = '<div class="empty" style="padding:14px">无命中</div>'; return; }
  box.innerHTML = hits.map(h =>
    '<div class="hit"><div class="cite-line">' + esc(h.citation || h.source_id) +
    '<span class="score">score ' + (Number(h.score || 0).toFixed(3)) + '</span></div>' +
    '<div class="txt">' + esc(h.text || '') + '</div></div>'
  ).join('');
}

/* ===== ③ 询价比价五步 ===== */
const INQUIRY = { inquiry_id: '', suppliers: [], quotes: [], comparison: null, init_done: false };

const STEP_HINTS = {
  1: '点「加载演示数据」自动填好 3 家供应商 + 3 份报价，然后点「初始化询价单」。',
  2: '核对报价行后，点「保存报价（内部代录）」把 3 份报价存进系统。',
  3: '核口径已自动检查（看上方结果）→ 下一步点「运行规则引擎比价」生成排名。',
  4: '点「运行规则引擎比价」，由规则引擎算出排名与含税总价。',
  5: '填报告编号 → 点「提交审核（通过）」→ 点「导出报告」下载 PDF / Word。'
};
function updateStepper(step) {
  document.querySelectorAll('#inq-stepper .step').forEach(el => {
    const n = parseInt(el.dataset.step, 10);
    el.classList.toggle('done', n < step);
    el.classList.toggle('active', n === step);
  });
  const hint = document.getElementById('inq-next-hint');
  if (hint) hint.innerHTML = '<b>当前第 ' + step + ' 步</b> · ' + esc(STEP_HINTS[step] || '');
}
/* 完成一步后：更新步骤条 + 自动滚到下一张卡片，演示更顺 */
function focusStep(step) {
  updateStepper(step);
  const card = document.getElementById('inq-card-' + Math.min(step, 5));
  if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function addSupplier(name) {
  const input = document.getElementById('inq-supplier-input');
  const n = (name != null ? name : input.value).trim();
  if (!n) return;
  if (INQUIRY.suppliers.includes(n)) { input.value = ''; return; }
  INQUIRY.suppliers.push(n);
  input.value = '';
  renderSuppliers();
}
function renderSuppliers() {
  const el = document.getElementById('supplier-list');
  el.innerHTML = INQUIRY.suppliers.map((s, i) =>
    '<span class="chip">' + esc(s) + '<button class="chip-x" onclick="INQUIRY.suppliers.splice(' + i + ',1);renderSuppliers()">×</button></span>'
  ).join('') || '<span class="muted">尚未添加供应商</span>';
}

async function initInquiry() {
  const id = document.getElementById('inq-id').value.trim();
  const deadline = document.getElementById('inq-deadline').value.trim();
  if (!id) { document.getElementById('inq-init-result').innerHTML = '<div class="empty">请填写询价单号。</div>'; return; }
  if (INQUIRY.suppliers.length < 3) {
    document.getElementById('inq-init-result').innerHTML = '<div class="empty">一次询价至少 3 家供应商，请先添加。</div>';
    return;
  }
  setBusy('init-inquiry-btn', true);
  const { ok, d } = await postJSON('/api/inquiries/init', { inquiry_id: id, suppliers: INQUIRY.suppliers, deadline });
  setBusy('init-inquiry-btn', false);
  if (!ok) { document.getElementById('inq-init-result').innerHTML = '<div class="empty">错误：' + esc(errText(d)) + '</div>'; return; }
  INQUIRY.inquiry_id = id;
  INQUIRY.init_done = true;
  const steps = (d.steps || []).map(s => '<span class="chip">' + esc(s.subject) + ' · ' + esc(s.status) + '</span>').join('');
  document.getElementById('inq-init-result').innerHTML =
    '<div class="ok-box">✓ 询价单 <b>' + esc(id) + '</b> 已初始化，五步任务已创建：<div class="chip-row">' + steps + '</div></div>';
  focusStep(2);
}

/* 报价行 */
function addQuoteRow(supplier, material, spec, unit, price, qty, tax, freight) {
  const tbody = document.getElementById('quote-tbody');
  const tr = document.createElement('tr');
  tr.innerHTML = '<td><input class="q-supplier" value="' + esc(supplier) + '" placeholder="供应商"></td>' +
    '<td><input class="q-material" value="' + esc(material) + '" placeholder="材料"></td>' +
    '<td><input class="q-spec" value="' + esc(spec) + '" placeholder="规格"></td>' +
    '<td><input class="q-unit" value="' + esc(unit) + '" placeholder="kg/吨/米/根"></td>' +
    '<td><input class="q-price" type="number" step="0.01" value="' + esc(price) + '"></td>' +
    '<td><input class="q-qty" type="number" step="0.01" value="' + esc(qty) + '"></td>' +
    '<td><input class="q-tax" type="number" step="0.01" value="' + esc(tax) + '"></td>' +
    '<td><input class="q-freight" type="number" step="0.01" value="' + esc(freight) + '"></td>' +
    '<td><button class="btn-mini del" onclick="this.closest(\'tr\').remove()">删除</button></td>';
  tbody.appendChild(tr);
}
function readQuoteRows() {
  return [...document.querySelectorAll('#quote-tbody tr')].map(tr => ({
    supplier: tr.querySelector('.q-supplier').value.trim(),
    material: tr.querySelector('.q-material').value.trim(),
    spec: tr.querySelector('.q-spec').value.trim(),
    unit: tr.querySelector('.q-unit').value.trim(),
    unit_price: parseFloat(tr.querySelector('.q-price').value) || 0,
    quantity: parseFloat(tr.querySelector('.q-qty').value) || 0,
    tax_rate: parseFloat(tr.querySelector('.q-tax').value) || 0,
    freight: parseFloat(tr.querySelector('.q-freight').value) || 0
  })).filter(r => r.supplier);
}
/* 演示数据：第 1 步就把单号 / 供应商 / 3 份报价全部备好，之后按 ①→⑤ 逐个点 */
const DEMO_QUOTES = [
  ['四川钢材商贸', 3580, 200], ['成都建材供应', 3620, 150], ['成都金属材料', 3560, 260],
  ['重庆钢铁集团', 3575, 180], ['德阳建材有限公司', 3610, 220], ['资阳钢材市场', 3590, 240],
  ['宜宾金属材料', 3630, 120], ['泸州钢铁贸易', 3545, 300], ['南充建材集团', 3605, 160],
  ['遂宁钢铁物资', 3650, 140]
];
function loadDemoData() {
  resetInquiry();
  document.getElementById('inq-id').value = 'IQ-2026-001';
  document.getElementById('inq-deadline').value = '2026-11-20';
  INQUIRY.suppliers = DEMO_QUOTES.map(d => d[0]);
  renderSuppliers();
  document.getElementById('quote-tbody').innerHTML = '';
  DEMO_QUOTES.forEach(d => addQuoteRow(d[0], '螺纹钢', 'HRB400 Φ20', '吨', d[1], 100, 0.13, d[2]));
  INQUIRY.quotes = readQuoteRows();
  renderConfirm();
  const btn = document.getElementById('demo-load-btn');
  if (btn) btn.textContent = '重新加载演示数据';
  document.getElementById('inq-init-result').innerHTML =
    '<div class="ok-box">✓ 演示数据已就绪：<b>10 家供应商 / 10 份报价</b>（单价 3,545–3,650 元/吨、运费 120–300 元，均为吨计价 / 13% 税率）。请从第 1 步开始，点「初始化询价单」。</div>';
  updateStepper(1);
  document.getElementById('inq-card-1').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function loadQuoteSample() { loadDemoData(); }
async function saveQuotes() {
  const rows = readQuoteRows();
  if (!INQUIRY.inquiry_id) INQUIRY.inquiry_id = document.getElementById('inq-id').value.trim() || 'IQ-2026-001';
  if (!rows.length) { document.getElementById('quote-save-result').innerHTML = '<div class="empty">请先点「加载演示数据」自动填入报价行，或手动加一行。</div>'; return; }
  setBusy('save-quotes-btn', true, '保存中…');
  let okAll = true, msgs = [];
  for (const q of rows) {
    const { ok, d } = await postJSON('/api/quotes/manual', { inquiry_id: INQUIRY.inquiry_id, quote: q, user: DEMO_USER });
    if (ok) msgs.push('✓ ' + q.supplier + '（' + (d.record_id || '') + '）'); else { okAll = false; msgs.push('✗ ' + q.supplier + '：' + errText(d)); }
  }
  setBusy('save-quotes-btn', false);
  INQUIRY.quotes = rows;
  document.getElementById('quote-save-result').innerHTML = okAll
    ? '<div class="ok-box">已内部代录 ' + rows.length + ' 份报价（状态：待确认）。<div class="chip-row">' + msgs.map(esc).join('') + '</div></div>'
    : '<div class="err-box">' + msgs.map(esc).join('<br>') + '</div>';
  renderConfirm();
  focusStep(3);
}
function renderConfirm() {
  const quotes = INQUIRY.quotes.length ? INQUIRY.quotes : readQuoteRows();
  if (!quotes.length) {
    document.getElementById('confirm-preview').innerHTML = '<div class="empty">保存报价后，这里预览待确认字段与口径一致性。</div>';
    document.getElementById('confirm-status').textContent = '单位 / 税率 / 运费口径一致性自动检查';
    return;
  }
  const units = [...new Set(quotes.map(q => q.unit))];
  const taxes = [...new Set(quotes.map(q => q.tax_rate))];
  const freights = [...new Set(quotes.map(q => (q.freight ? '含运费' : '不含运费')))];
  const problems = [];
  if (units.length > 1) problems.push('单位不一致：' + units.join(' / '));
  if (taxes.length > 1) problems.push('税率不一致：' + taxes.join(' / '));
  if (freights.length > 1) problems.push('运费口径不一致');
  const st = document.getElementById('confirm-status');
  st.innerHTML = problems.length
    ? '<span class="badge yellow">' + problems.length + ' 项口径待确认</span> ' + problems.map(esc).join('；')
    : '<span class="badge normal">口径一致</span>';

  const rows = quotes.map(q => '<tr><td>' + esc(q.supplier) + '</td><td>' + esc(q.material) + '</td><td>' + esc(q.spec) + '</td>' +
    '<td>' + esc(q.unit) + '</td><td class="num">' + fmtMoney(q.unit_price) + '</td><td class="num">' + fmtMoney(q.quantity) + '</td>' +
    '<td class="num">' + fmtPct(q.tax_rate) + '</td><td class="num">' + fmtMoney(q.freight) + '</td></tr>').join('');
  document.getElementById('confirm-preview').innerHTML =
    '<table class="result"><colgroup><col style="width:20%"><col style="width:14%"><col style="width:18%"><col style="width:9%"><col style="width:12%"><col style="width:8%"><col style="width:9%"><col style="width:10%"></colgroup>' +
    '<thead><tr><th>供应商</th><th>材料</th><th>规格</th><th>单位</th><th class="num">不含税单价</th><th class="num">数量</th><th class="num">税率</th><th class="num">运费</th></tr></thead><tbody>' + rows + '</tbody></table>' +
    '<p class="hint">关键字段（单位/税率/运费）需人工确认后再进入比价。</p>';
}

async function searchRef() {
  const q = document.getElementById('ref-input').value.trim();
  if (!q) return;
  const refInput = document.getElementById('ref-input');
  refInput.disabled = true;
  const { ok, d } = await postJSON('/api/reference/search', { query: q, user: DEMO_USER });
  refInput.disabled = false;
  if (!ok) { document.getElementById('ref-result').innerHTML = '<div class="err-box">' + esc(errText(d)) + '</div>'; return; }
  const hits = (d.hits || []).map(h => '<div class="hit"><div class="cite-line">' + esc(h.source || '') + '</div><div class="txt">' + esc(h.note || '') + '</div></div>').join('');
  const local = (d.local_hits || []).map(h => '<div class="hit"><div class="cite-line">' + esc(h.source_id) + '</div><div class="txt">' + esc(h.text || '') + '</div></div>').join('');
  document.getElementById('ref-result').innerHTML =
    '<div class="ok-box">参考价检索（' + esc(d.note || '') + '），共 ' + d.count + ' 条：</div>' + hits + local ||
    '<div class="empty" style="padding:14px">无命中</div>';
}

async function computeComparison(auto) {
  const quotes = INQUIRY.quotes.length ? INQUIRY.quotes : readQuoteRows();
  INQUIRY.quotes = quotes;
  if (quotes.length < 3) { document.getElementById('compute-result').innerHTML = '<div class="empty">报价不足 3 份，不进入比价。请先保存 ≥3 份报价。</div>'; return; }
  setBusy('compute-btn', true, '规则计算中…');
  if (!auto) updateStepper(4);
  const { ok, d } = await postJSON('/api/quotes/compute', { inquiry_id: INQUIRY.inquiry_id || 'IQ-2026-001', items: quotes });
  setBusy('compute-btn', false);
  if (!ok) { document.getElementById('compute-result').innerHTML = '<div class="err-box">' + esc(errText(d)) + '</div>'; return; }
  INQUIRY.comparison = d;
  renderComparison(d);
  if (auto) return;
  focusStep(5);
}

function renderBestAnalysis(d) {
  const ranking = (d.ranking && d.ranking.length) ? d.ranking : [];
  if (!d.comparable || !ranking.length) return '';
  const best = ranking[0], second = ranking[1] || null;
  const unit = best.unit || '';
  const diff = (second && best.price_with_tax != null && second.price_with_tax != null)
    ? (second.price_with_tax - best.price_with_tax) : null;
  const diffPct = (diff != null && second.price_with_tax) ? diff / second.price_with_tax : null;
  const count = (d.items || []).length;
  const steps = [
    '口径检查：' + count + ' 份报价的单位、税率、运费口径一致 → 可进入比价。',
    '含税单价 = 报价单价 × (1 + 税率)：' + fmtMoney(best.unit_price) + ' × (1 + ' + fmtPct(best.tax_rate) + ') = ' + fmtMoney(best.price_with_tax) + ' 元/' + esc(unit) + '。',
    '含税总价 = 含税单价 × 数量 + 运费：' + fmtMoney(best.price_with_tax) + ' × ' + fmtMoney(best.quantity) + ' + ' + fmtMoney(best.freight) + ' = ' + fmtMoney(best.total) + ' 元。',
    '排名：按含税单价升序，' + esc(best.supplier) + ' 综合最优（第 1 名）' +
      (second ? '；第 2 名 ' + esc(second.supplier) + ' 含税单价 ' + fmtMoney(second.price_with_tax) +
        (diff != null ? '，高 ' + fmtMoney(diff) + ' 元/' + esc(unit) + '（' + fmtPct(diffPct) + '）' : '') : '') + '。'
  ];
  return '<div class="best-analysis">' +
    '<div class="best-head"><b>✓ 最优报价分析</b><span class="muted">规则引擎快照 ' + esc(d.snapshot_id || '—') + '</span></div>' +
    '<div class="best-winner"><div class="bw-main">最优推荐：<b>' + esc(best.supplier) + '</b></div>' +
    '<div class="bw-metrics"><span>含税单价 <b>' + fmtMoney(best.price_with_tax) + '</b> 元/' + esc(unit) + '</span>' +
    '<span>含税总价 <b>' + fmtMoney(best.total) + '</b> 元</span><span>参与比价 <b>' + count + '</b> 份</span></div></div>' +
    (diff != null ? '<div class="best-diff">比第 2 名 ' + esc(second.supplier) + ' 便宜 <b>' + fmtMoney(diff) + '</b> 元/' + esc(unit) + '（' + fmtPct(diffPct) + '）</div>' : '') +
    '<ol class="best-steps">' + steps.map(x => '<li>' + x + '</li>').join('') + '</ol></div>';
}

function renderComparison(d) {
  const reasons = (d.inconsistency_reasons || []);
  const reasonsHtml = reasons.length
    ? '<div class="err-box">⚠ 口径不一致，不排序：<br>' + reasons.map(esc).join('<br>') + '</div>'
    : '';
  const items = (d.ranking && d.ranking.length ? d.ranking : d.items) || [];
  const rows = items.map(r => {
    const rank = r.rank ? '<span class="badge normal">第 ' + r.rank + ' 名</span>' : '<span class="muted">—</span>';
    return '<tr><td>' + rank + '</td><td>' + esc(r.supplier) + '</td><td>' + esc(r.material) + '</td><td>' + esc(r.spec) + '</td>' +
      '<td>' + esc(r.unit) + '</td><td class="num">' + fmtMoney(r.unit_price) + '</td><td class="num">' + fmtMoney(r.price_with_tax) + '</td>' +
      '<td class="num">' + fmtMoney(r.total) + '</td><td class="num">' + fmtPct(r.tax_rate) + '</td><td class="num">' + fmtMoney(r.freight) + '</td>' +
      (r.diff != null ? '<td class="num">' + (r.diff >= 0 ? '+' : '') + fmtMoney(r.diff) + '（' + fmtPct(r.diff_ratio) + '）</td>' : '<td class="num">—</td>') + '</tr>';
  }).join('');
  const formulas = Object.keys(d.formulas || {}).map(k => '<div><code>' + esc(k) + '</code> = ' + esc(d.formulas[k]) + '</div>').join('');
  document.getElementById('compute-result').innerHTML =
    '<div class="ok-box">规则引擎比价结果（' + esc(d.rule_version || '') + ' · 快照 ' + esc(d.snapshot_id || '') + '）</div>' +
    reasonsHtml + renderBestAnalysis(d) +
    '<table class="result"><colgroup><col style="width:8%"><col style="width:15%"><col style="width:8%"><col style="width:10%"><col style="width:7%"><col style="width:8%"><col style="width:10%"><col style="width:10%"><col style="width:7%"><col style="width:7%"><col style="width:10%"></colgroup>' +
    '<thead><tr><th>排名</th><th>供应商</th><th>材料</th><th>规格</th><th>单位</th><th class="num">单价</th><th class="num">含税单价</th><th class="num">含税总价</th><th class="num">税率</th><th class="num">运费</th><th class="num">较参考价</th></tr></thead><tbody>' + rows + '</tbody></table>' +
    '<div class="card-head" style="margin-top:12px"><b>口径公式</b></div><div class="formulas">' + formulas + '</div>';
}

/* 第①步：导入报价单（PDF / Word / Excel）→ 自动回填 + 自动分析 */
async function importQuoteFile(ev) {
  const input = ev && ev.target;
  const file = input && input.files ? input.files[0] : null;
  if (!file) return;
  input.value = '';
  const box = document.getElementById('quote-import-result');
  if (box) box.innerHTML = '<div class="empty">正在识别报价单（' + esc(file.name) + '）…</div>';
  try {
    const buf = await file.arrayBuffer();
    const resp = await fetch('/api/quotes/import?filename=' + encodeURIComponent(file.name), {
      method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/octet-stream' }, body: buf
    });
    const d = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
    const rows = d.rows || [];
    if (!rows.length) {
      box.innerHTML = '<div class="err-box">未识别到报价行：' + esc(d.note || '') + '</div>';
      return;
    }
    document.getElementById('quote-tbody').innerHTML = '';
    rows.forEach(r => addQuoteRow(r.supplier, r.material, r.spec, r.unit, r.unit_price, r.quantity, r.tax_rate, r.freight));
    INQUIRY.suppliers = [...new Set(rows.map(r => r.supplier).filter(Boolean))];
    renderSuppliers();
    INQUIRY.quotes = readQuoteRows();
    renderConfirm();
    box.innerHTML = '<div class="ok-box">✓ 已识别 <b>' + rows.length + '</b> 份报价（' + esc(d.format || '') + '：' + esc(file.name) +
      '），已填入第②步并完成口径检查。' + (rows.length >= 3 ? '已自动分析最优报价 →' : '') + '<br><span class="muted">' + esc(d.note || '') + '</span></div>';
    const hint = document.getElementById('inq-next-hint');
    if (hint) hint.innerHTML = '<b>当前第 1 步</b> · 已导入 ' + rows.length + ' 份报价并自动分析最优报价 → 点「初始化询价单」继续流程。';
    if (rows.length >= 3) {
      await computeComparison(true);
      document.getElementById('compute-result').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  } catch (e) {
    if (box) box.innerHTML = '<div class="err-box">导入失败：' + esc(String(e.message || e)) + '</div>';
  }
}

async function approveReport(action) {
  if (!INQUIRY.comparison) { document.getElementById('report-result').innerHTML = '<div class="empty">请先完成「做比价」生成比价结果。</div>'; return; }
  const reportId = document.getElementById('report-id').value.trim() || 'RPT-2026-001';
  const reviewer = { ...DEMO_USER, role: '成本经理' };
  // 审核通过时把「人工确认后的报价明细」一并发给后端：服务端用规则引擎重算比价并固化成报告快照，
  // 导出只认这份快照（前端传的数字不被采信）。
  const report = {
    inquiry_id: INQUIRY.inquiry_id || 'IQ-2026-001',
    project_name: '江山云墅项目',
    suppliers: INQUIRY.suppliers,
    items: INQUIRY.quotes,
    summary: ''
  };
  setBusy('report-approve-btn', true, '提交中…');
  const { ok, d } = await postJSON('/api/reports/approve', { report_id: reportId, action, reviewer, report });
  setBusy('report-approve-btn', false);
  if (!ok) { document.getElementById('report-result').innerHTML = '<div class="err-box">' + esc(errText(d)) + '</div>'; return; }
  INQUIRY.report_id = reportId;
  INQUIRY.review_status = d.status;
  document.getElementById('report-result').innerHTML = '<div class="ok-box">✓ 报告 ' + esc(reportId) + ' 审核状态：<b>' + esc(d.status) + '</b>' +
    (d.status === '审核通过'
      ? '。已固化报告快照 <code>' + esc(d.snapshot_id || '—') + '</code>，现在可导出 PDF / Word。'
      : '（打回后需重新提交审核）') + '</div>';
  const hint = document.getElementById('inq-next-hint');
  if (hint) {
    hint.innerHTML = d.status === '审核通过'
      ? '<b>当前第 5 步</b> · 已审核通过（快照 ' + esc(d.snapshot_id || '—') + '）→ 点「导出报告」下载 PDF / Word。'
      : '<b>当前第 5 步</b> · 报告被打回，请修改后重新提交审核。';
  } else { focusStep(5); }
}
async function exportReport() {
  const reportId = document.getElementById('report-id').value.trim() || INQUIRY.report_id;
  if (!reportId) { document.getElementById('report-result').innerHTML = '<div class="empty">请先提交审核并填写报告编号。</div>'; return; }
  const reviewer = { ...DEMO_USER, role: '成本经理' };
  const formatEl = document.getElementById('report-format');
  const format = formatEl ? formatEl.value : '.pdf';
  setBusy('report-export-btn', true, '生成文件中…');
  const { ok, d } = await postJSON('/api/reports/export', { report_id: reportId, format, reviewer });
  setBusy('report-export-btn', false);
  if (!ok) { document.getElementById('report-result').innerHTML = '<div class="err-box">' + esc(errText(d)) + '</div>'; return; }
  const kb = (Number(d.size || 0) / 1024).toFixed(1);
  const label = format === '.docx' ? 'Word' : 'PDF';
  document.getElementById('report-result').innerHTML =
    '<div class="ok-box">✓ 已生成真实报告文件：<b>' + esc(d.file) + '</b>（' + kb + ' KB · ' + label + '）<br>' +
    '<a class="btn-mini" href="' + esc(d.download_url) + '" download style="margin-top:8px;display:inline-block">⬇ 下载 ' + label + '</a> ' +
    '<a class="btn-mini" href="' + esc(d.download_url) + '" target="_blank" style="display:inline-block">在新标签打开</a><br>' +
    '<span class="muted">' + esc(d.note || '') + '</span></div>';
  const hint = document.getElementById('inq-next-hint');
  if (hint) hint.innerHTML = '<b>演示完成</b> · 已导出报告，可点上方链接下载；想再演示一遍点「清空重来」。';
}
function resetInquiry() {
  Object.assign(INQUIRY, { inquiry_id: '', suppliers: [], quotes: [], comparison: null, init_done: false });
  document.getElementById('supplier-list').innerHTML = '';
  document.getElementById('quote-tbody').innerHTML = '';
  ['inq-init-result', 'quote-save-result', 'ref-result', 'compute-result', 'report-result'].forEach(id => document.getElementById(id).innerHTML = '');
  document.getElementById('confirm-preview').innerHTML = '<div class="empty">保存报价后，这里预览待确认字段与口径一致性。</div>';
  document.getElementById('confirm-status').textContent = '保存报价后自动检查（无需点击）';
  const btn = document.getElementById('demo-load-btn');
  if (btn) btn.textContent = '加载演示数据';
  updateStepper(1);
}

/* ===== ④ 评测看板 ===== */
const SAMPLE_EVALS = {
  "alerts": { "predicted": ["建筑工程费", "安装工程费", "预备费"], "gold": ["建筑工程费", "安装工程费", "工程建设其他费"] },
  "retrieval": { "predicted": ["src1#0-10", "src2#0-20", "src3#0-30"], "gold": ["src2#0-20", "src3#0-30"] },
  "answers": [{ "citation": "[src1#0-10]" }, { "citation": "" }],
  "answer_accuracy": { "predicted": ["建筑工程费", "安装工程费"], "gold": ["建筑工程费", "安装工程费"] }
};
/* 问答实跑抽测用（界面不显示题目，只显示指标） */
const QA_GOLD = [
  ["钢材采购合同怎么付款？", "JA-2025-010-04"],
  ["总承包合同的质量保证金怎么扣？", "JA-2025-010"],
  ["电梯设备买卖合同的税率是多少？", "JA-2025-035"],
  ["塔吊租赁合同怎么付款？", "JA-2025-010-05"],
  ["商品混凝土采购合同的金额怎么定？", "JA-2025-010-03"],
  ["监理服务合同的报酬怎么支付？", "JA-2025-004"],
  ["建筑劳务分包的范围是什么？", "JA-2025-010-01"],
  ["防水工程的保修期限？", "JA-2025-014"]
];

const EVAL_STATE = { retrieval: null, qa: null, alertCoverage: null, alertPrecision: null, citation: null };
const EVAL_HISTORY = {};
const METRIC_DEFS = [
  { key: 'retrieval', label: '检索召回率', scope: '50 道标准题', desc: '问合同问题时，前 5 条检索结果能找到正确合同的比例', good: 0.9 },
  { key: 'qa', label: '问答引用命中率', scope: '抽样实跑', desc: '完整回答里引用的合同，确实是题目要求的那份', good: 0.9 },
  { key: 'alertCoverage', label: '预警覆盖率', scope: '样例数据', desc: '该报警的科目，系统都报出来了（不遗漏）', good: 0.8 },
  { key: 'alertPrecision', label: '预警准确率', scope: '样例数据', desc: '系统报出的预警，有多少是真的（不误报）', good: 0.8 },
  { key: 'citation', label: '引用可追溯率', scope: '样例数据', desc: '回答里带合同出处的比例（可回溯原文）', good: 0.9 }
];

function _pct(v) { return v == null ? '—' : (Number(v) * 100).toFixed(1) + '%'; }
function _fmtAt(iso) {
  if (!iso) return '';
  const m = String(iso).match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  return m ? (m[1] + '-' + m[2] + ' ' + m[3] + ':' + m[4]) : String(iso).slice(0, 16);
}
function _nowLocal() {
  const d = new Date(), p = n => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' +
    p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

/* 指标历史曲线（SVG 迷你折线；每个点带日期时间提示） */
function sparkline(points) {
  const pts = (points || []).filter(p => p && p.value != null).slice(-10);
  if (pts.length < 2) return '';
  const W = 100, H = 30, padX = 2, padY = 5;
  const vals = pts.map(p => Number(p.value) * 100);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = Math.max(max - min, 1e-6);
  const xs = vals.map((v, i) => padX + (W - 2 * padX) * i / (vals.length - 1));
  const ys = vals.map(v => H - padY - (H - 2 * padY) * (v - min) / span);
  const line = pts.map((p, i) => xs[i].toFixed(1) + ',' + ys[i].toFixed(1)).join(' ');
  const dots = pts.map((p, i) =>
    '<circle cx="' + xs[i].toFixed(1) + '" cy="' + ys[i].toFixed(1) + '" r="1.6">' +
    '<title>' + esc(_fmtAt(p.at)) + ' · ' + (Number(p.value) * 100).toFixed(1) + '%</title></circle>').join('');
  const area = 'M' + xs[0].toFixed(1) + ',' + (H - padY) + ' L' + line.split(' ').join(' L ') +
    ' L' + xs[xs.length - 1].toFixed(1) + ',' + (H - padY) + ' Z';
  return '<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none">' +
    '<path class="spark-area" d="' + area + '"/><polyline class="spark-line" points="' + line + '"/>' + dots + '</svg>' +
    '<div class="spark-meta"><span>' + esc(_fmtAt(pts[0].at)) + '</span><span>' + esc(_fmtAt(pts[pts.length - 1].at)) + '</span></div>';
}

function applyEvalPayload(d) {
  if (!d) return;
  const res = d.results || {};
  const hist = d.history || {};
  Object.keys(EVAL_STATE).forEach(k => {
    if (res[k] && res[k].value != null) {
      EVAL_STATE[k] = { value: res[k].value, at: res[k].at, demo: !!res[k].demo };
    }
    if (Array.isArray(hist[k])) EVAL_HISTORY[k] = hist[k];
  });
  renderMetrics();
}

async function loadEvalLast() {
  try {
    const resp = await fetch('/api/eval/last', { cache: 'no-store' });
    const d = await resp.json();
    applyEvalPayload(d);
  } catch (e) { renderMetrics(); }
}

function renderMetrics() {
  const box = document.getElementById('eval-metrics');
  if (!box) return;
  box.innerHTML = METRIC_DEFS.map(def => {
    const st = EVAL_STATE[def.key];
    const waiting = !st || st.value == null;
    let cls = 'pending', badge = '待评测', sub = def.scope;
    if (!waiting) {
      const ratio = Number(st.value);
      if (ratio >= def.good) { cls = 'good'; badge = '达标'; }
      else if (ratio >= def.good * 0.75) { cls = 'warn'; badge = '待优化'; }
      else { cls = 'bad'; badge = '不达标'; }
      sub = (st.demo ? '演示数据' : '上次评测') + (st.at ? ' · ' + _fmtAt(st.at) : '');
    }
    const spark = waiting ? '' : sparkline(EVAL_HISTORY[def.key] || []);
    return '<div class="metric-card ' + cls + '">' +
      '<div class="m-top"><span class="m-label">' + esc(def.label) + '</span><span class="m-badge">' + esc(badge) + '</span></div>' +
      '<div class="m-value">' + (waiting ? '—' : _pct(st.value)) + '</div>' +
      '<div class="m-sub">' + esc(sub) + '</div>' + spark +
      '<div class="m-desc">' + esc(def.desc) + '</div></div>';
  }).join('');
}

/* 一键加载演示评测结果（不调模型、不导入真实数据） */
async function loadDemoEval() {
  try {
    const resp = await fetch('/api/eval/seed-demo', { method: 'POST', cache: 'no-store' });
    const d = await resp.json();
    if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
    applyEvalPayload(d);
  } catch (e) {
    const box = document.getElementById('eval-metrics');
    if (box) box.insertAdjacentHTML('afterbegin', '<div class="err-box">演示数据加载失败：' + esc(String(e.message || e)) + '</div>');
  }
}

function evalSampleSummary() {
  const s = SAMPLE_EVALS;
  const parts = [];
  if (s.alerts) parts.push('预警检查');
  if (s.retrieval) parts.push('检索检查');
  if (s.answers) parts.push('引用检查');
  if (s.answer_accuracy) parts.push('回答准确率');
  return '样例包含：' + parts.join(' · ');
}

function loadEvalSample() {
  const el = document.getElementById('eval-samples');
  if (el) el.value = JSON.stringify(SAMPLE_EVALS, null, 2);
  const sum = document.getElementById('eval-sample-summary');
  if (sum) sum.innerHTML = '<span class="muted">' + esc(evalSampleSummary()) + '</span>';
  const res = document.getElementById('eval-result');
  if (res) res.innerHTML = '';
}

/* 离线样例结果 → 自然语言说明（不展示原始 JSON） */
function renderOfflineResult(d) {
  const lines = [];
  const alerts = d['预警'];
  if (alerts) {
    const cov = alerts['预警覆盖率'], prec = alerts['预警准确率'];
    const hit = alerts['命中'];
    const miss = (hit == null || alerts['应预警数'] == null) ? '—' : (alerts['应预警数'] - hit);
    const falseAlarm = (hit == null || alerts['系统预警数'] == null) ? '—' : (alerts['系统预警数'] - hit);
    lines.push({
      ok: (cov == null ? 0 : cov) >= 0.8 && (prec == null ? 0 : prec) >= 0.8,
      title: '预警检查',
      text: '应预警 ' + alerts['应预警数'] + ' 个科目，系统报出 ' + alerts['系统预警数'] + ' 个、命中 ' + hit +
        ' 个 → 覆盖率 ' + _pct(cov) + '（漏报 ' + miss + ' 个），准确率 ' + _pct(prec) + '（误报 ' + falseAlarm + ' 个）。'
    });
  }
  const ret = d['检索'];
  if (ret) {
    lines.push({
      ok: (ret['召回准确率'] == null ? 0 : ret['召回准确率']) >= 0.8,
      title: '检索检查',
      text: '命中 ' + ret['命中'] + ' 条 → 召回准确率 ' + _pct(ret['召回准确率']) + '，召回率 ' + _pct(ret['召回率']) + '。'
    });
  }
  if (d['引用可追溯率'] != null) {
    lines.push({ ok: d['引用可追溯率'] >= 0.9, title: '引用检查',
      text: '样例回答中 ' + _pct(d['引用可追溯率']) + ' 带有合同出处（引用可追溯率）。' });
  }
  if (d['回答准确率'] != null) {
    lines.push({ ok: d['回答准确率'] >= 0.8, title: '回答准确率',
      text: '按样例比对，回答准确率 ' + _pct(d['回答准确率']) + '。' });
  }
  return lines.map(l => '<div class="eval-line ' + (l.ok ? 'ok' : 'warn') + '"><b>' + esc(l.title) + '</b><span>' + esc(l.text) + '</span></div>').join('');
}

async function saveEvalRecord(key, data) {
  try {
    const resp = await fetch('/api/eval/record', {
      method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: key, data: data })
    });
    if (resp.ok) applyEvalPayload(await resp.json());
  } catch (e) { /* 忽略持久化失败 */ }
}

async function runEval() {
  let samples;
  try { samples = JSON.parse(document.getElementById('eval-samples').value); }
  catch (e) { document.getElementById('eval-result').innerHTML = '<div class="err-box">JSON 解析失败：' + esc(e.message) + '</div>'; return; }
  setBusy('eval-run-btn', true, '评测中…');
  const { ok, d } = await postJSON('/api/eval/run', { samples });
  setBusy('eval-run-btn', false);
  if (!ok) { document.getElementById('eval-result').innerHTML = '<div class="err-box">' + esc(errText(d)) + '</div>'; return; }
  const at = _nowLocal();
  const alerts = d['预警'] || {};
  if (alerts['预警覆盖率'] != null) await saveEvalRecord('alertCoverage', { value: alerts['预警覆盖率'] });
  if (alerts['预警准确率'] != null) await saveEvalRecord('alertPrecision', { value: alerts['预警准确率'] });
  if (d['引用可追溯率'] != null) await saveEvalRecord('citation', { value: d['引用可追溯率'] });
  const html = renderOfflineResult(d);
  document.getElementById('eval-result').innerHTML = html
    ? '<div class="eval-lines">' + html + '</div>'
    : '<div class="empty" style="padding:14px">样例未包含可计算指标，请检查 JSON 结构。</div>';
}

async function runRetrievalEval() {
  const st = document.getElementById('eval-qa-status');
  const btns = document.querySelectorAll('button');
  setBusy('eval-retrieval-btn', true, '评测中…');
  if (st) st.innerHTML = '<span class="spinner"></span>正在跑 50 道标准题的检索召回（约 6 秒）…';
  try {
    const resp = await fetch('/api/eval/retrieval', {
      method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ top_k: 5 })
    });
    const d = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(d.detail || ('HTTP ' + resp.status));
    applyEvalPayload(d);
    if (st) st.innerHTML = '<b>检索召回率 ' + _pct(d.value) + '</b>（50 道标准题实测，越大越好）';
  } catch (e) {
    if (st) st.innerHTML = '<span class="badge red">评测失败：' + esc(String(e.message || e)) + '</span>';
  }
  setBusy('eval-retrieval-btn', false);
}

async function runQaEval() {
  if (!confirm('将调用 DeepSeek 抽样作答 8 题（约 40 秒），是否继续？')) return;
  const st = document.getElementById('eval-qa-status');
  setBusy('eval-qa-btn', true, '问答评测中…');
  let hitCount = 0;
  for (let i = 0; i < QA_GOLD.length; i++) {
    const [q, contract] = QA_GOLD[i];
    if (st) st.innerHTML = '<span class="spinner"></span>问答实跑评测 ' + (i + 1) + '/' + QA_GOLD.length + '：' + esc(q);
    const { ok, d } = await postJSON('/api/qa/ask', { question: q, top_k: 6 });
    const citations = d.citations || [];
    const hit = ok && (citations.some(c => c.includes(contract)) || (d.answer || '').includes(contract));
    if (hit) hitCount++;
  }
  setBusy('eval-qa-btn', false);
  const value = hitCount / QA_GOLD.length;
  await saveEvalRecord('qa', { value: value, hit: hitCount, total: QA_GOLD.length });
  if (st) st.innerHTML = '<b>问答引用命中率 ' + _pct(value) + '</b>（抽样 8 题完整问答实跑，越大越好）';
}

/* ===== 启动 ===== */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.nav-item').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); switchView(a.dataset.view); history.replaceState(null, '', '#' + a.dataset.view); });
  });
  // 按钮备份标签
  document.querySelectorAll('.btn.primary').forEach(b => b.dataset.label = b.innerHTML);
  const h = (location.hash || '#monitor').slice(1);
  switchView(document.querySelector('[data-view="' + h + '"]') ? h : 'monitor');
  loadSample();
  // 表格输入变化时实时重算合计与占比
  ['budget-tbody', 'source-tbody'].forEach(id => {
    const body = document.getElementById(id);
    if (body) body.addEventListener('input', calcTotals);
  });
  loadEvalSample();
  renderMetrics();
  loadEvalLast();
  renderSuppliers();
  updateStepper(1);
  checkHealth();
});
