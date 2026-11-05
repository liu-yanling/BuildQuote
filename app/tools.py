"""工具系统（s02）：V3 的 9 个领域工具 + s16 Workflow 的定义与分派（TOOL_HANDLERS）。

export_report 对模型 deny，仅在工作台后端接口由人工触发；approve_report 对模型 ask，模型只发起，
最终审核由成本经理在界面完成。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from app import background, cost_tools, knowledge, permissions, rules, scheduler, workflow as wf
from app.models import FieldCandidate, User

# 支持的导入/导出格式（V3：导出 .pdf/.docx，去掉 .md）
IMPORT_FORMATS = {".xlsx", ".xls", ".pdf", ".md", ".docx"}
EXPORT_FORMATS = {".pdf", ".docx"}


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "parse_quote_file",
        "description": "解析上传的报价单/合同（.xlsx/.pdf/.md/.docx），产出带来源定位与可信度标记的候选字段。",
        "permission": "allow",
        "layer": "④ 工具与执行层",
        "input_schema": {
            "type": "object",
            "required": ["inquiry_id", "file_path", "format"],
            "properties": {
                "inquiry_id": {"type": "string"},
                "file_path": {"type": "string"},
                "format": {"type": "string", "enum": sorted(IMPORT_FORMATS)},
                "supplier": {"type": "string"},
                "text": {"type": "string"},
            },
        },
    },
    {
        "name": "record_manual_entry",
        "description": "成本经理在界面内部代录或修正供应商报价，写入结构化报价库并保留版本。",
        "permission": "allow",
        "layer": "① 交互入口层",
        "input_schema": {
            "type": "object",
            "required": ["inquiry_id", "quote"],
            "properties": {
                "inquiry_id": {"type": "string"},
                "quote": {"type": "object"},
            },
        },
    },
    {
        "name": "search_reference_price",
        "description": "联网检索已核验许可的四川省公开造价站点白名单，获取参考价（参考价非成交价）。",
        "permission": "allow",
        "layer": "③ 上下文与知识层",
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "object"},
            },
        },
    },
    {
        "name": "compute_price_rules",
        "description": "规则引擎做单位换算、总价、差价、比例与排序；少于 3 家/3 份不进入比价；口径不一致只列原因不排序。",
        "permission": "allow",
        "layer": "④ 工具与执行层",
        "input_schema": {
            "type": "object",
            "required": ["inquiry_id", "items"],
            "properties": {
                "inquiry_id": {"type": "string"},
                "items": {"type": "array"},
                "config": {"type": "object"},
                "reference": {"type": "object"},
            },
        },
    },
    {
        "name": "save_task_state",
        "description": "把询价单五步进度、需求版本与供应商回复状态写入 .tasks/ 与数据库，支持跨天续跑。",
        "permission": "allow",
        "layer": "⑦ 数据与留痕层",
        "input_schema": {
            "type": "object",
            "required": ["inquiry_id"],
            "properties": {
                "inquiry_id": {"type": "string"},
                "step": {"type": "string"},
                "suppliers_replied": {"type": "array"},
            },
        },
    },
    {
        "name": "dispatch_async_job",
        "description": "同步执行超过 30 秒阈值时转异步，投递后台任务并在完成后通知成本经理。",
        "permission": "allow",
        "layer": "⑤ 编排与任务层",
        "input_schema": {
            "type": "object",
            "required": ["description"],
            "properties": {
                "description": {"type": "string"},
                "payload": {"type": "object"},
            },
        },
    },
    {
        "name": "send_reminder",
        "description": "对超期未回的供应商报价发送催收提醒（站内消息），按询价单+供应商幂等。",
        "permission": "allow",
        "layer": "⑤ 编排与任务层",
        "input_schema": {
            "type": "object",
            "required": ["inquiry_id", "supplier"],
            "properties": {
                "inquiry_id": {"type": "string"},
                "supplier": {"type": "string"},
                "overdue_days": {"type": "integer"},
            },
        },
    },
    {
        "name": "approve_report",
        "description": "成本经理审核报告（通过/打回）。模型只发起，最终审核由人工在界面完成。",
        "permission": "ask",
        "layer": "⑥ 安全与权限层",
        "input_schema": {
            "type": "object",
            "required": ["report_id", "action"],
            "properties": {
                "report_id": {"type": "string"},
                "action": {"type": "string", "enum": ["通过", "打回"]},
                "reason": {"type": "string"},
            },
        },
    },
    {
        "name": "export_report",
        "description": "把经审核通过的报告导出为 .pdf/.docx 存档。仅工作台后端在审核通过后触发，模型不得调用。",
        "permission": "deny",
        "layer": "⑥ 安全与权限层",
        "input_schema": {
            "type": "object",
            "required": ["report_id", "format"],
            "properties": {
                "report_id": {"type": "string"},
                "format": {"type": "string", "enum": sorted(EXPORT_FORMATS)},
            },
        },
    },
]

# s16 Workflow 工具（宿主编排入口，模型可见）
TOOL_DEFS.append(wf.WORKFLOW_TOOL)

# V4 成本监控工具（归集/预警/问答/RAG）
COST_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "ingest_knowledge",
        "description": "把合同清单/签证单/结算资料/报价文档切块并向量化入库（RAG），保留来源定位。",
        "permission": "allow",
        "layer": "⑤ 能力集成层",
        "input_schema": {"type": "object", "required": ["text", "source_id"],
                         "properties": {"text": {"type": "string"}, "source_id": {"type": "string"},
                                         "meta": {"type": "object"}}},
    },
    {
        "name": "search_cost_knowledge",
        "description": "混合召回（向量+BM25）检索成本知识库，返回带 [来源#定位] 的依据片段。",
        "permission": "allow",
        "layer": "⑤ 能力集成层",
        "input_schema": {"type": "object", "required": ["query"],
                         "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}}},
    },
    {
        "name": "collect_cost_data",
        "description": "把合同/进度款/签证来源按成本科目归集，输出预算-实际台账（数字规则计算）。",
        "permission": "allow",
        "layer": "④ 工具与执行层",
        "input_schema": {"type": "object", "required": ["budget_rows", "sources"],
                         "properties": {"budget_rows": {"type": "array"}, "sources": {"type": "array"},
                                         "yellow_ratio": {"type": "number"}, "red_ratio": {"type": "number"}}},
    },
    {
        "name": "alert_deviation",
        "description": "按成本科目逐项比对预算 vs 实际，输出黄警/红警清单（数字规则计算，附依据）。",
        "permission": "allow",
        "layer": "④ 工具与执行层",
        "input_schema": {"type": "object", "required": ["budget_rows", "sources"],
                         "properties": {"budget_rows": {"type": "array"}, "sources": {"type": "array"},
                                         "yellow_ratio": {"type": "number"}, "red_ratio": {"type": "number"}}}
    },
    {
        "name": "answer_cost_question",
        "description": "对成本问题先检索 RAG 依据，再引用来源作答；无依据时明说「未见依据」。",
        "permission": "allow",
        "layer": "⑤ 能力集成层",
        "input_schema": {"type": "object", "required": ["question"],
                         "properties": {"question": {"type": "string"}, "top_k": {"type": "integer"}}},
    },
    {
        "name": "save_cost_ledger",
        "description": "持久化成本台账与预警清单（保留版本），供日维度监控与复验。",
        "permission": "allow",
        "layer": "⑦ 数据与留痕层",
        "input_schema": {"type": "object", "required": ["ledger"],
                         "properties": {"ledger": {"type": "array"}, "alerts": {"type": "array"}}},
    },
]
TOOL_DEFS.extend(COST_TOOL_DEFS)

TOOLS_BY_NAME = {t["name"]: t for t in TOOL_DEFS}


# ---------------------------------------------------------------------------
# 领域工具实现
# ---------------------------------------------------------------------------
def run_parse_quote_file(args: dict, ctx: dict) -> dict:
    """解析报价文件。骨架版为确定性启发式；.pdf/.docx 无文本层时降级为人工补录。"""
    fmt = args.get("format", "").lower()
    if fmt not in IMPORT_FORMATS:
        return {"ok": False, "reason": f"不支持的格式 {fmt}", "needs_manual": True}
    text = args.get("text") or ""
    if not text and fmt == ".md":
        p = Path(args.get("file_path", ""))
        text = p.read_text(encoding="utf-8") if p.is_file() else ""
    if not text:
        return {"ok": False, "reason": f"{fmt} 未提供可解析文本，转人工录入",
                "needs_manual": True, "file_path": args.get("file_path", "")}
    raw = text[:2000]
    candidates: list[dict] = []

    def _span(m):
        return raw[max(0, m.start() - 12):m.end() + 12].strip()

    patterns = {
        "material": r"(钢材|水泥|管材|螺纹钢|圆钢|线材|商品混凝土|镀锌管|无缝管|PE管|PVC管)",
        "spec": r"(?:规格|型号)[:：]?\s*([A-Za-z0-9./\u4e00-\u9fff-]+)",
        "unit": r"(?<!\d)(吨|公斤|千克|kg|t|米|m|根|立方米|m³)(?!\d)",
        "unit_price": r"(?:含税单价|单价|不含税单价)[:：]?\s*(\d+(?:\.\d+)?)",
        "tax_rate": r"(?:税率|增值税)[:：]?\s*(\d+(?:\.\d+)?)\s*%",
        "freight": r"(?:运费|运输费)[:：]?\s*(\d+(?:\.\d+)?)",
    }
    for field, pat in patterns.items():
        m = re.search(pat, raw)
        if m:
            val = m.group(1) if m.groups() else m.group(0)
            candidates.append(FieldCandidate(field=field, value=val, raw_value=m.group(0),
                                             source_span=_span(m), confidence=0.6).__dict__)
        else:
            candidates.append(FieldCandidate(field=field, value=None, raw_value="",
                                             source_span="", confidence=0.0).__dict__)
    store = ctx.get("store")
    if store is not None:
        store.append(ctx.get("enterprise_id", "-"), ctx.get("project_id", "-"), "parse_results", {
            "inquiry_id": args.get("inquiry_id", ""), "file_path": args.get("file_path", ""),
            "format": fmt, "supplier": args.get("supplier", ""), "candidates": candidates})
    return {"ok": True, "inquiry_id": args.get("inquiry_id", ""),
            "candidates": candidates, "needs_confirmation": True,
            "note": "候选字段仅供人工确认，不直接进入比价。"}


def run_record_manual_entry(args: dict, ctx: dict) -> dict:
    """内部代录：写草稿版本，不覆盖历史版本。"""
    store = ctx["store"]
    user: User = ctx["user"]
    ok, reason = permissions.check_permission(user, "record_manual_entry")
    if not ok:
        return {"ok": False, "reason": reason}
    record_id = store.append(user.enterprise_id, ctx.get("project_id", ""), "supplier_quotes", {
        "inquiry_id": args.get("inquiry_id", ""), "quote": args.get("quote", {}),
        "source": "内部代录", "status": "待确认",
        "enterprise_id": user.enterprise_id, "project_id": ctx.get("project_id", "")})
    return {"ok": True, "record_id": record_id, "source": "内部代录", "version": 1}


def run_search_reference_price(args: dict, ctx: dict) -> dict:
    res = knowledge.search_reference_price(args.get("query", ""), scope=args.get("scope"))
    store = ctx.get("store")
    if store is not None:
        store.write_log(ctx.get("enterprise_id", "-"), ctx.get("project_id", "-"),
                        "search_reference_price",
                        {"actor": ctx.get("user").user_id, "query": args.get("query", ""),
                         "hits": [h.get("source") for h in res.get("hits", [])]})
    return res


def run_compute_price_rules(args: dict, ctx: dict) -> dict:
    """规则引擎计算（数字只走这里，不经模型）。"""
    return rules.compute_price_rules(args.get("items", []), config=args.get("config"),
                                     reference=args.get("reference"))


def run_save_task_state(args: dict, ctx: dict) -> dict:
    """持久化询价单五步进度与供应商回复状态（.tasks/ + 数据库）。"""
    from app.tasks import InquiryBoard, TaskStore

    board = InquiryBoard(TaskStore())
    suppliers = args.get("suppliers_replied") or []
    board.init_inquiry(args.get("inquiry_id", ""), suppliers or ["?"])
    for s in suppliers:
        board.record_supplier_reply(args.get("inquiry_id", ""), s)
    store = ctx.get("store")
    if store is not None:
        store.append(ctx.get("enterprise_id", "-"), ctx.get("project_id", "-"), "task_state", {
            "inquiry_id": args.get("inquiry_id", ""), "step": args.get("step", ""),
            "suppliers_replied": suppliers})
    return {"ok": True, "inquiry_id": args.get("inquiry_id", ""),
            "steps": board.step_status(args.get("inquiry_id", ""))}


def run_dispatch_async_job(args: dict, ctx: dict) -> dict:
    """超时转异步：投递后台任务（骨架版把 parse+compute 打包为一个后台任务）。"""
    payload = args.get("payload") or {}

    def _work():
        items = payload.get("items", [])
        return rules.compute_price_rules(items, config=payload.get("config"),
                                         reference=payload.get("reference"))

    job_id = background.dispatch_async_job(args.get("description", "异步比价"), _work)
    return {"ok": True, "job_id": job_id, "status": "pending",
            "note": "已转异步处理，完成后通知成本经理"}


def run_send_reminder(args: dict, ctx: dict) -> dict:
    """对单个供应商发送催收提醒（幂等）。"""
    store = ctx["store"]
    user: User = ctx["user"]
    return {"ok": True, "result": scheduler.send_reminder(
        store, user.enterprise_id, ctx.get("project_id", ""),
        args.get("inquiry_id", ""), args.get("supplier", ""),
        int(args.get("overdue_days", 0)))}


def run_approve_report(args: dict, ctx: dict) -> dict:
    """模型只发起待人工审核；最终审核由 /api/reports/approve 完成。"""
    return {"ok": True, "status": "待人工审核", "report_id": args.get("report_id", ""),
            "note": "报告审核由成本经理在界面完成，模型不得代审。"}


def _make_tool_runner(ctx: dict) -> Callable[[str, dict], Any]:
    def _run(name: str, a: dict) -> Any:
        if name == "compute_price_rules":
            return run_compute_price_rules(a, ctx)
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            raise ValueError(f"工作流调用了未知工具 {name}")
        return handler(a, ctx)

    return _run


def run_workflow_tool(args: dict, ctx: dict) -> dict:
    """Workflow 工具：一次 tool_use 跑完保存好的五步编排，可断点续跑。"""
    from app.llm import MockRunner, ModelRunner

    model = ctx.get("model")
    runner = ModelRunner(model, wf.quote_comparison_system) if model is not None else MockRunner()
    out = wf.run_workflow_sync(args.get("name", ""), args=args.get("args"),
                               resume_from_run_id=args.get("resume_from_run_id"),
                               runner=runner, tool_runner=_make_tool_runner(ctx))
    return {"launched": True, "run_id": out["run_id"], "status": out["status"],
            "result": out.get("result"), "error": out.get("error"),
            "events": out.get("events", [])[-5:]}


# ---------------------------------------------------------------------------
# 分派表
# ---------------------------------------------------------------------------
TOOL_HANDLERS: dict[str, Callable[[dict, dict], dict]] = {
    "parse_quote_file": run_parse_quote_file,
    "record_manual_entry": run_record_manual_entry,
    "search_reference_price": run_search_reference_price,
    "compute_price_rules": run_compute_price_rules,
    "save_task_state": run_save_task_state,
    "dispatch_async_job": run_dispatch_async_job,
    "send_reminder": run_send_reminder,
    "approve_report": run_approve_report,
    "Workflow": run_workflow_tool,
    # export_report 不注册模型分派：由工作台后端接口（/api/reports/export）直接调用
}
TOOL_HANDLERS.update(cost_tools.COST_TOOL_HANDLERS)


def model_visible_tools() -> list[dict[str, Any]]:
    """模型可见工具池：排除 deny 工具；ask 工具可见但只发起待人工。"""
    return [{k: t[k] for k in ("name", "description", "input_schema")}
            for t in TOOL_DEFS if t["permission"] != "deny"]
