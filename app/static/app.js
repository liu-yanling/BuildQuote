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
  if (name === 'eval') renderGoldTable();
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
  ['人工费', 1000], ['材料费', 2000], ['机械使用费', 500], ['分包费', 3000],
  ['其他直接费', 800], ['现场管理费', 600], ['公司管理费', 400], ['税金', 300], ['预备费', 500]
];
const SAMPLE_SOURCES = [
  ['进度款', '劳务', 1060, 'S1'], ['进度款', '钢材', 2280, 'S2'],
  ['进度款', '机械', 490, 'S3'], ['进度款', '分包', 2950, 'S4'],
  ['进度款', '措施费', 800, 'S5'], ['进度款', '现场经费', 650, 'S6'],
  ['进度款', '管理费', 400, 'S7'], ['进度款', '税金', 300, 'S8'], ['进度款', '预备费', 500, 'S9']
];

function addBudgetRow(subject, budget) {
  const tr = document.createElement('tr');
  tr.innerHTML = '<td><input class="c-subject" value="' + esc(subject) + '" placeholder="科目"></td>' +
    '<td><input class="c-budget" type="number" step="0.01" value="' + esc(budget) + '" placeholder="金额"></td>' +
    '<td><button class="btn-mini del" onclick="this.closest(\'tr\').remove()">删除</button></td>';
  document.getElementById('budget-tbody').appendChild(tr);
}
function addSourceRow(type, subject, amount, id) {
  const tr = document.createElement('tr');
  tr.innerHTML = '<td><input class="c-type" value="' + esc(type) + '"></td>' +
    '<td><input class="c-subject" value="' + esc(subject) + '" placeholder="科目/别名"></td>' +
    '<td><input class="c-amount" type="number" step="0.01" value="' + esc(amount) + '"></td>' +
    '<td><input class="c-id" value="' + esc(id) + '"></td>' +
    '<td><button class="btn-mini del" onclick="this.closest(\'tr\').remove()">删除</button></td>';
  document.getElementById('source-tbody').appendChild(tr);
}
function loadSample() {
  document.getElementById('budget-tbody').innerHTML = '';
  document.getElementById('source-tbody').innerHTML = '';
  SAMPLE_BUDGET.forEach(r => addBudgetRow(r[0], r[1]));
  SAMPLE_SOURCES.forEach(r => addSourceRow(r[0], r[1], r[2], r[3]));
  document.getElementById('monitor-result').innerHTML = '<div class="empty">已加载样例数据，点「运行监控」查看归集台账与预警。</div>';
}
function readBudgetRows() {
  return [...document.querySelectorAll('#budget-tbody tr')].map(tr => ({
    subject: tr.querySelector('.c-subject').value.trim(),
    budget: parseFloat(tr.querySelector('.c-budget').value) || 0
  })).filter(r => r.subject);
}
function readSources() {
  return [...document.querySelectorAll('#source-tbody tr')].map(tr => ({
    source_type: tr.querySelector('.c-type').value.trim() || '进度款',
    subject: tr.querySelector('.c-subject').value.trim(),
    amount: parseFloat(tr.querySelector('.c-amount').value) || 0,
    source_id: tr.querySelector('.c-id').value.trim()
  })).filter(r => r.subject);
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

  const alertHtml = alerts.length ? alerts.map(a => {
    const lv = a.level === '红警' ? 'red' : 'yellow';
    return '<div class="alert-item ' + lv + '"><span class="lv">' + esc(a.level) + ' ' + esc(a.subject) + '</span>' +
      '<span class="detail">偏差 +' + fmtMoney(a.deviation) + '（' + fmtPct(a.deviation_ratio) + '）</span></div>';
  }).join('') : '<div class="empty" style="padding:14px">（无超支/超付预警）</div>';

  const f = d.formulas || {};
  const formulaHtml = Object.keys(f).map(k => '<div><code>' + esc(k) + '</code> = ' + esc(f[k]) + '</div>').join('');

  document.getElementById('monitor-result').innerHTML =
    '<div class="card"><div class="card-head"><b>成本台账（' + esc(d.rule_version || '') + '）</b></div>' +
    '<table class="result"><thead><tr><th>科目</th><th class="num">预算</th><th class="num">实际</th><th class="num">偏差</th><th class="num">偏差率</th><th>状态</th></tr></thead>' +
    '<tbody>' + rows + '</tbody></table></div>' +
    '<div class="card"><div class="card-head"><b>预警清单</b><span class="muted">黄警 ≥' + fmtPct(d.yellow_ratio) + ' / 红警 ≥' + fmtPct(d.red_ratio) + '</span></div>' + alertHtml + '</div>' +
    '<div class="card"><div class="card-head"><b>口径公式</b></div><div class="formulas">' + formulaHtml + '</div></div>';
}

/* ===== ② 合同问答 ===== */
function addMsg(role, text, citations) {
  const chat = document.getElementById('chat');
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
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

function updateStepper(step) {
  document.querySelectorAll('#inq-stepper .step').forEach(el => {
    const n = parseInt(el.dataset.step, 10);
    el.classList.toggle('done', n < step);
    el.classList.toggle('active', n === step);
  });
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
  updateStepper(INQUIRY.suppliers.length >= 3 ? 2 : 1);
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
  updateStepper(2);
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
function loadQuoteSample() {
  document.getElementById('quote-tbody').innerHTML = '';
  addQuoteRow('四川钢材商贸', '螺纹钢', 'HRB400 Φ20', '吨', 3580, 100, 0.13, 200);
  addQuoteRow('成都建材供应', '螺纹钢', 'HRB400 Φ20', '吨', 3620, 100, 0.13, 150);
  addQuoteRow('绵阳金属材料', '螺纹钢', 'HRB400 Φ20', '吨', 3560, 100, 0.13, 260);
  if (!INQUIRY.suppliers.length) {
    INQUIRY.suppliers = ['四川钢材商贸', '成都建材供应', '绵阳金属材料'];
    renderSuppliers();
  }
}
async function saveQuotes() {
  const rows = readQuoteRows();
  if (!INQUIRY.inquiry_id) INQUIRY.inquiry_id = document.getElementById('inq-id').value.trim() || 'IQ-2026-001';
  if (!rows.length) { document.getElementById('quote-save-result').innerHTML = '<div class="empty">请先添加报价行（或点「加载样例报价」）。</div>'; return; }
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
  updateStepper(3);
}
async function parseQuote() {
  const text = document.getElementById('parse-text').value.trim();
  if (!text) { document.getElementById('parse-result').innerHTML = '<div class="empty">请粘贴报价文本。</div>'; return; }
  const supplier = document.getElementById('parse-supplier').value.trim() || '待确认供应商';
  const format = document.getElementById('parse-format').value;
  setBusy('parse-btn', true, '解析中…');
  const { ok, d } = await postJSON('/api/quotes/parse', { inquiry_id: INQUIRY.inquiry_id || 'IQ-2026-001', file_path: '', format, supplier, text, user: DEMO_USER });
  setBusy('parse-btn', false);
  if (!ok) { document.getElementById('parse-result').innerHTML = '<div class="err-box">' + esc(errText(d)) + (d.needs_manual ? '（需人工录入）' : '') + '</div>'; return; }
  const cands = d.candidates || [];
  const found = cands.filter(c => c.value != null);
  const rows = cands.map(c => '<tr><td>' + esc(c.field) + '</td><td>' + esc(c.value || '—') + '</td><td>' + esc(c.raw_value || '') + '</td><td>' + esc(c.source_span || '') + '</td><td class="num">' + (c.confidence || 0).toFixed(2) + '</td></tr>').join('');
  document.getElementById('parse-result').innerHTML =
    '<div class="ok-box">已提取 ' + found.length + '/' + cands.length + ' 个候选字段（仅供人工确认）：</div>' +
    '<table class="result"><thead><tr><th>字段</th><th>值</th><th>原文</th><th>来源片段</th><th class="num">置信度</th></tr></thead><tbody>' + rows + '</tbody></table>';
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
    '<td>' + esc(q.unit) + '</td><td class="num">' + fmtMoney(q.unit_price) + '</td><td class="num">' + q.quantity + '</td>' +
    '<td class="num">' + fmtPct(q.tax_rate) + '</td><td class="num">' + fmtMoney(q.freight) + '</td></tr>').join('');
  document.getElementById('confirm-preview').innerHTML =
    '<table class="result"><thead><tr><th>供应商</th><th>材料</th><th>规格</th><th>单位</th><th class="num">含税单价</th><th class="num">数量</th><th class="num">税率</th><th class="num">运费</th></tr></thead><tbody>' + rows + '</tbody></table>' +
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

async function computeComparison() {
  const quotes = INQUIRY.quotes.length ? INQUIRY.quotes : readQuoteRows();
  INQUIRY.quotes = quotes;
  if (quotes.length < 3) { document.getElementById('compute-result').innerHTML = '<div class="empty">报价不足 3 份，不进入比价。请先保存 ≥3 份报价。</div>'; return; }
  setBusy('compute-btn', true, '规则计算中…');
  const { ok, d } = await postJSON('/api/quotes/compute', { inquiry_id: INQUIRY.inquiry_id || 'IQ-2026-001', items: quotes });
  setBusy('compute-btn', false);
  if (!ok) { document.getElementById('compute-result').innerHTML = '<div class="err-box">' + esc(errText(d)) + '</div>'; return; }
  INQUIRY.comparison = d;
  renderComparison(d);
  updateStepper(4);
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
    reasonsHtml +
    '<table class="result"><thead><tr><th>排名</th><th>供应商</th><th>材料</th><th>规格</th><th>单位</th><th class="num">单价</th><th class="num">含税单价</th><th class="num">含税总价</th><th class="num">税率</th><th class="num">运费</th><th class="num">较参考价</th></tr></thead><tbody>' + rows + '</tbody></table>' +
    '<div class="card-head" style="margin-top:12px"><b>口径公式</b></div><div class="formulas">' + formulas + '</div>';
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
  updateStepper(5);
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
}
function resetInquiry() {
  Object.assign(INQUIRY, { inquiry_id: '', suppliers: [], quotes: [], comparison: null, init_done: false });
  document.getElementById('supplier-list').innerHTML = '';
  document.getElementById('quote-tbody').innerHTML = '';
  ['inq-init-result', 'quote-save-result', 'parse-result', 'ref-result', 'compute-result', 'report-result'].forEach(id => document.getElementById(id).innerHTML = '');
  document.getElementById('confirm-preview').innerHTML = '<div class="empty">保存报价后，这里预览待确认字段与口径一致性。</div>';
  document.getElementById('confirm-status').textContent = '单位 / 税率 / 运费口径一致性自动检查';
  updateStepper(1);
}

/* ===== ④ 评测看板 ===== */
const SAMPLE_EVALS = {
  "alerts": { "predicted": ["建筑工程费", "安装工程费", "预备费"], "gold": ["建筑工程费", "安装工程费", "工程建设其他费"] },
  "retrieval": { "predicted": ["src1#0-10", "src2#0-20", "src3#0-30"], "gold": ["src2#0-20", "src3#0-30"] },
  "answers": [{ "citation": "[src1#0-10]" }, { "citation": "" }],
  "answer_accuracy": { "predicted": ["建筑工程费", "安装工程费"], "gold": ["建筑工程费", "安装工程费"] }
};
const QA_GOLD = [
  ["钢材采购合同怎么付款？", "JA-2025-010-04"],
  ["总承包合同的质量保证金怎么扣？", "JA-2025-010"],
  ["电梯设备买卖合同的税率是多少？", "JA-2025-035"],
  ["塔吊租赁合同怎么付款？", "JA-2025-010-05"],
  ["商品混凝土采购合同的金额怎么定？", "JA-2025-010-03"],
  ["监理服务合同的报酬怎么支付？", "JA-2025-004"],
  ["建筑劳务分包的范围是什么？", "JA-2025-010-01"],
  ["防水工程的保修期限？", "JA-2025-014"],
  ["造价咨询服务的付款节点？", "JA-2025-005"],
  ["临时用电工程的价款怎么结算？", "JA-2025-007"],
  ["土方开挖合同的价格怎么定？", "JA-2025-008"],
  ["边坡支护工程的付款方式？", "JA-2025-013"]
];

function loadEvalSample() {
  document.getElementById('eval-samples').value = JSON.stringify(SAMPLE_EVALS, null, 2);
  document.getElementById('eval-result').innerHTML = '';
}
function metricCard(k, v) {
  const val = v == null ? '—' : (typeof v === 'number' ? fmtPct(v) : v);
  return '<div class="metric"><div class="m-label">' + esc(k) + '</div><div class="m-value">' + esc(val) + '</div></div>';
}
async function runEval() {
  let samples;
  try { samples = JSON.parse(document.getElementById('eval-samples').value); }
  catch (e) { document.getElementById('eval-result').innerHTML = '<div class="err-box">JSON 解析失败：' + esc(e.message) + '</div>'; return; }
  setBusy('eval-run-btn', true, '评测中…');
  const { ok, d } = await postJSON('/api/eval/run', { samples });
  setBusy('eval-run-btn', false);
  if (!ok) { document.getElementById('eval-result').innerHTML = '<div class="err-box">' + esc(errText(d)) + '</div>'; return; }
  const cards = Object.entries(d).map(([k, v]) => {
    if (typeof v === 'object' && v !== null) {
      return '<div class="metric-group"><b>' + esc(k) + '</b><div class="metric-row">' +
        Object.entries(v).map(([kk, vv]) => metricCard(kk, vv)).join('') + '</div></div>';
    }
    return metricCard(k, v);
  }).join('');
  document.getElementById('eval-result').innerHTML = cards || '<div class="empty">无指标输出</div>';
}

function renderGoldTable(results) {
  const el = document.getElementById('qa-gold-table');
  const rows = QA_GOLD.map(([q, contract], i) => {
    let cell = '<span class="muted">—</span>';
    if (results) {
      const r = results[i];
      if (r && r.hit) cell = '<span class="badge normal">✓ ' + esc(r.found || contract) + '</span>';
      else if (r && r.error) cell = '<span class="badge yellow">' + esc(r.error) + '</span>';
      else if (r) cell = '<span class="badge red">✗ 未命中</span>';
    }
    return '<tr><td>' + (i + 1) + '</td><td>' + esc(q) + '</td><td class="mono">' + esc(contract) + '</td><td>' + cell + '</td></tr>';
  }).join('');
  el.innerHTML = '<table class="result"><thead><tr><th style="width:32px">#</th><th>问题</th><th>应命中合同</th><th style="width:140px">结果</th></tr></thead><tbody>' + rows + '</tbody></table>';
}

function hitsContainContract(hits, contract) {
  if (!hits || !hits.length) return false;
  return hits.some(h => {
    const s = (h.citation || h.source_id || h.source || '') + (h.text || '');
    return s.includes(contract);
  });
}

async function runRetrievalEval() {
  const st = document.getElementById('eval-qa-status');
  setBusy('eval-retrieval-btn', true, '评测中…');
  const results = [];
  let hitCount = 0;
  for (let i = 0; i < QA_GOLD.length; i++) {
    const [q, contract] = QA_GOLD[i];
    st.innerHTML = '<span class="spinner"></span>检索评测 ' + (i + 1) + '/' + QA_GOLD.length + '：' + esc(q);
    const { ok, d } = await postJSON('/api/knowledge/search', { query: q, top_k: 5 });
    const hit = ok && hitsContainContract(d.hits, contract);
    if (hit) hitCount++;
    results.push({ hit, found: contract, error: ok ? '' : errText(d) });
    renderGoldTable(results);
  }
  setBusy('eval-retrieval-btn', false);
  st.innerHTML = '';
  const acc = (hitCount / QA_GOLD.length * 100).toFixed(1);
  document.getElementById('eval-qa-status').innerHTML = '<b>检索召回准确率 top-5：' + hitCount + '/' + QA_GOLD.length + ' = ' + acc + '%</b>';
}

async function runQaEval() {
  if (!confirm('将调用 DeepSeek 逐题作答（12 题，约需 1–3 分钟），是否继续？')) return;
  const st = document.getElementById('eval-qa-status');
  setBusy('eval-qa-btn', true, '问答评测中…');
  const results = [];
  let hitCount = 0;
  for (let i = 0; i < QA_GOLD.length; i++) {
    const [q, contract] = QA_GOLD[i];
    st.innerHTML = '<span class="spinner"></span>问答评测 ' + (i + 1) + '/' + QA_GOLD.length + '：' + esc(q);
    const { ok, d } = await postJSON('/api/qa/ask', { question: q, top_k: 6 });
    const citations = d.citations || [];
    const hit = ok && (citations.some(c => c.includes(contract)) || (d.answer || '').includes(contract));
    if (hit) hitCount++;
    results.push({ hit, found: contract, error: ok ? '' : errText(d) });
    renderGoldTable(results);
  }
  setBusy('eval-qa-btn', false);
  st.innerHTML = '';
  document.getElementById('eval-qa-status').innerHTML = '<b>问答引用命中率：' + hitCount + '/' + QA_GOLD.length + ' = ' + (hitCount / QA_GOLD.length * 100).toFixed(1) + '%</b>';
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
  loadEvalSample();
  renderGoldTable();
  renderSuppliers();
  updateStepper(1);
  checkHealth();
});
