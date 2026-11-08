# BuildQuote（骨架 · V4）

房建成本工作台 MVP 骨架：把「建材询价比价 + 成本动态归集 + 超支预警」串成一条可对话链路。

> V4 在 V3 询价比价骨架之上新增：三独立子 Agent（归集/预警/问答，s06+s13）、RAG 向量/混合召回（独立 embedding API）、
> 成本科目规则引擎、模块化 Prompt 四层、评测体系。硬化与产品化在后续迭代补齐。

## 能力

### 询价比价（V1–V3，沿用）

建需求 → 收报价 → 核口径 → 做比价 → 出报告（s16 工作流 + s10 任务 + s11 后台 + s12 催收 + 规则引擎）。

### 成本动态监控（V4 新增）

- 归集 Agent：对齐合同/进度款/签证 → 按科目归集实际成本。
- 预警 Agent：按科目比对预算 vs 实际 → 超支预警（规则计算 + 依据解释）。
- 问答 Agent：自然语言追问费用构成 → RAG 引用依据作答。
- 三子 Agent 由 Lead 主控（`app/team.py` s13：文件邮箱 + 原子认领 + 任务工作区；`app/subagent.py` s06 独立上下文）。

### RAG 知识库（V4 新增）

多源文档切分 → 向量化（默认本地开源 bge-small-zh，免费；也可 EMBEDDING_BACKEND=api 接付费接口）→ SQLite 向量库 → 混合召回（向量 + BM25）→ 引用治理（每条结论带 `[来源#定位]`）。

## 快速开始

```sh
make bootstrap          # install + migrate
cp .env.example .env    # 填 MODEL_API_KEY / DATA_KEY / SESSION_SECRET；可选 EMBEDDING_API_KEY
make up                 # http://localhost:8000
make test               # 跑测试
```

缺 `MODEL_API_KEY` / `DATA_KEY` / `SESSION_SECRET` 任一，启动失败；`EMBEDDING_API_KEY` 留空则 RAG 降级本地确定性向量（仅离线演示）。

## 配置与数据接入（V4）

| 配置 | 说明 |
|---|---|
| EMBEDDING_BACKEND | `local_model`（默认，bge-small-zh 免费本地）或 `api`（OpenAI 兼容付费） |
| EMBEDDING_MODEL | 默认 `BAAI/bge-small-zh-v1.5`；api 后端时填对应模型名 |
| ALERT_YELLOW_RATIO / ALERT_RED_RATIO | 预警阈值：偏差率 ≥5% 黄警、≥10% 红警（默认） |
| COST_SUBJECTS_FILE | 成本科目口径文件（默认 skills/成本科目/subjects.json，9 个建筑科目） |

成本数据接入（CSV 列：source_type,subject,amount,source_id,note）：

```sh
# 生成空白 Excel 模板（成本来源 + 预算 + 填写说明 三张表）
.venv/bin/python -m app.ingest --make-template examples/成本台账模板.xlsx
# 跑样例 CSV
.venv/bin/python -m app.ingest examples/sample_cost_sources.csv --budget examples/sample_budget.json
# 跑你填好的 Excel
.venv/bin/python -m app.ingest examples/成本台账模板.xlsx
```

## 目录

```
app/
  main.py          # FastAPI 入口 + 询价/成本/RAG/团队/评测接口
  agent_loop.py    # s01 Loop + s15 集成 Harness（Lead 主控）
  prompts.py       # 模块化 Prompt 四层 + Lead/三子 Agent 系统提示词
  subagent.py      # s06 子 Agent：独立上下文嵌套循环（≤30 轮）
  team.py          # s13 团队：MessageBus + Teammate + 原子认领 + 任务工作区
  cost_rules.py    # 成本科目 + 归集对齐 + 预警规则（纯函数，不经模型）
  cost_tools.py    # 成本监控工具：归集/预警/问答/RAG 检索/知识入库/台账保存
  embeddings.py    # embedding 三后端：本地 bge-small-zh / API / hash 降级
  ingest.py        # 成本数据接入：CSV/Excel/JSON → 归集 + 预警；生成 Excel 模板
  vectorstore.py   # SQLite 向量库（余弦相似度）
  rag.py           # 多源切分 + 混合召回（向量+BM25）+ 引用治理
  eval.py          # 评测指标：预警覆盖率/准确率、召回准确率、引用可追溯率等
  export.py        # 真实报告导出：审核快照 → .docx/.pdf（python-docx + reportlab）
  workflow.py      # s16 工作流运行时（询价五步）
  tasks.py         # s10 任务系统（含团队认领复用）
  background.py    # s11 后台任务
  scheduler.py     # s12 定时调度（到期催收）
  tools.py         # s02 工具系统：询价 9 工具 + Workflow + 成本 6 工具
  permissions.py   # s03 权限系统
  hooks.py         # s04 钩子系统
  knowledge.py     # s07 技能加载 + BM25 + 联网参考价检索（白名单）
  rules.py         # 询价规则引擎（价格计算）
  storage.py       # SQLite 存储（AES 加密）
  crypto.py        # AES-256-GCM
  llm.py           # DeepSeek 客户端 + ModelRunner/MockRunner
  config.py        # .env 加载与校验
skills/
  口径规则/SKILL.md   # 钢材/水泥/管材询价口径
  成本科目/SKILL.md   # 房建成本科目与预警口径（V4）
tests/             # pytest：规则、权限、任务、工作流、成本规则、RAG、团队
evals/             # 离线评测样例（V4）
```

## 工具面

### 询价侧（V3，沿用）

| 工具 | 权限 | 说明 |
|---|---|---|
| parse_quote_file | allow | 解析 .xlsx/.pdf/.md/.docx 报价文件 → 候选字段 |
| record_manual_entry | allow | 成本经理内部代录报价（V3 不开放供应商自助填报） |
| search_reference_price | allow | 联网检索白名单内公开造价站点（参考价非成交价） |
| compute_price_rules | allow | 规则引擎计算（<3 家/3 份不进入比价） |
| save_task_state | allow | 持久化五步进度与供应商回复状态 |
| dispatch_async_job | allow | 超时转异步后台任务 |
| send_reminder | allow | 超期未回自动催收（幂等） |
| approve_report | ask | 成本经理审核（模型只发起，人工在界面完成） |
| export_report | deny | 模型不得调用，审核通过后由后端导出 .pdf/.docx |
| Workflow | allow | 运行保存好的五步比价编排，可断点续跑 |

### 成本监控侧（V4 新增）

| 工具 | 权限 | 说明 |
|---|---|---|
| ingest_knowledge | allow | 文档切块+向量化入库（RAG） |
| search_cost_knowledge | allow | 混合召回检索依据（带引用） |
| collect_cost_data | allow | 按科目归集实际成本（规则） |
| alert_deviation | allow | 预算-实际比对+超支预警（规则） |
| answer_cost_question | allow | RAG 依据+引用作答 |
| save_cost_ledger | allow | 持久化台账/预警 |

## 红线（不可违背）

1. 价格计算与预警数字（偏差/超支率）全部由规则引擎完成，禁止模型心算。
2. 关键字段由人确认；`export_report` 对模型 deny，`approve_report` 只发起待人工。
3. 每个数字可追溯；每段文字引用来源；报告经成本经理审核后才可导出。
4. 模型不把缺失数据补成事实；无依据时明说「未见依据」。

## V4 接口

- `POST /api/cost/monitor` 成本归集 + 预警
- `POST /api/knowledge/ingest` 知识入库
- `POST /api/knowledge/search` 混合召回
- `POST /api/team/run` 三子 Agent 并行分派
- `POST /api/eval/run` 离线评测
- `POST /api/qa/ask` 合同问答（RAG + DeepSeek 带引用）

## 问答 Agent

```sh
# 问合同（RAG 检索原文 → DeepSeek 带引用作答）
.venv/bin/python -m app.qa "钢材采购合同的进度款支付比例和质保金怎么约定？"
# 只看检索证据，不调模型
.venv/bin/python -m app.qa "质保金" --no-model
```

回答用**自然语言段落**：prompt 明确要求不用 Markdown、不分点、不用 [1] 编号（引用写成「按《合同编号》第X条……」）；
`qa.naturalize_answer` 再做一层兜底清洗，前端按段落渲染，引用来源单独用标签展示。

## 报告导出（硬化阶段 · 真实 .pdf/.docx）

审核通过时后端用规则引擎重算并**固化报告快照**；导出只认这份快照，前端传的数字不被采信，
每份报告带规则版本 + 计算快照 ID，可回溯报价明细。

- `POST /api/reports/approve`：审核（通过 / 打回）。通过时附 `report.items`（人工确认后的报价明细），
  后端重算比价并写入报告快照；打回只记录审核状态。
- `POST /api/reports/export`：生成真实 `.pdf` / `.docx` 文件（须审核通过；门禁不变，模型仍 deny）。
- `GET /api/reports/{report_id}/download?format=.pdf|.docx`：下载已生成文件（attachment）。

前端「⑤ 出报告」卡片有格式下拉（PDF / Word），导出后直接给下载链接。

实现：`app/export.py`（python-docx 生成 docx；reportlab + STSong-Light 生成中文 PDF；A4 横向）。
文件落在 `data/exports/`（`data/` 已 gitignore）。新增依赖见 `requirements.txt`。

## 成本台账导入 / 导出（v0.5.0 · 硬化阶段）

成本监控页支持 Excel 台账的完整往返（openpyxl，`app/cost_io.py`）：

- `GET /api/cost/template`：下载模板（预算 / 成本来源 / 填写说明 三张表，含科目下拉）。
- `POST /api/cost/import`：上传 Excel（模板或导出文件均可，兼容旧版 5 列模板）→ 回填前端两张表。
- `POST /api/cost/export`：导出完整台账（预算 / 成本来源 / 台账 / 说明，含合计行与按来源类型小计；
  台账行由规则引擎归集计算）。
- 前端操作：`导出模板` → 填表 → `导入台账`；或直接 `导出 Excel` 备份当前台账。
- 输入表本人实时合计/占比（仅界面展示），正式数字仍只由规则引擎计算。

## 评测看板（v0.5.2 · 一眼看懂）

- 顶部 5 张指标卡：**检索召回率 / 问答引用命中率 / 预警覆盖率 / 预警准确率 / 引用可追溯率**
  （大号数字 + 一句话解释 + 达标/待优化/不达标颜色）。
- ① 金标准问答评测（12 题）：`检索召回评测` 不调模型（约 10 秒）；`问答实跑评测` 调 DeepSeek（1–3 分钟）。
  表格逐题显示 ✓命中 / ✗未命中。
- ② 离线指标评测（样例数据）：秒出结果，输出为**自然语言解释**（应报几个、命中几个、漏报/误报几个），
  原始样例 JSON 收进「高级」折叠区。
- 作用：验证「找得到依据、答得有出处、预警不漏不误报」，是给非技术同事看的验收面板。

## 验证

在引擎根目录运行 `blueprint verify`（或对应工作区路径），差距报告写入 `../verify/`。
