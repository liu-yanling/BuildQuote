"""成本监控领域工具（V4）：归集/预警/问答/RAG 检索/知识入库/台账保存。

数字计算一律走 app/cost_rules.py 规则引擎；模型只做科目识别/归集对齐/解释引用。
"""
from __future__ import annotations

from typing import Any

from app import cost_rules
from app.cost_rules import CostSource
from app.rag import format_evidence


def run_ingest_knowledge(inp: dict, ctx: dict) -> dict:
    rag = ctx["rag"]
    n = rag.ingest(inp.get("text", ""), inp.get("source_id", "doc"), inp.get("meta"))
    return {"ok": True, "ingested_chunks": n, "note": "已切块并向量化入库（含引用定位）"}


def run_search_cost_knowledge(inp: dict, ctx: dict) -> dict:
    rag = ctx["rag"]
    hits = rag.retrieve(inp.get("query", ""), top_k=int(inp.get("top_k", 5)))
    return {"ok": True, "hits": format_evidence(hits), "degraded": rag.embedder.degraded}


def run_collect_cost_data(inp: dict, ctx: dict) -> dict:
    sources = [CostSource(source_type=s.get("source_type", ""), subject=s.get("subject", ""),
                          amount=float(s.get("amount", 0) or 0), source_id=s.get("source_id", ""),
                          note=s.get("note", "")) for s in inp.get("sources", [])]
    result = cost_rules.compute_cost_monitor(
        inp.get("budget_rows", []), sources,
        yellow_ratio=float(inp.get("yellow_ratio", cost_rules.DEFAULT_YELLOW_RATIO)),
        red_ratio=float(inp.get("red_ratio", cost_rules.DEFAULT_RED_RATIO)))
    return {"ok": True, **result}


def run_alert_deviation(inp: dict, ctx: dict) -> dict:
    """预警：与归集共用同一规则引擎；返回 ledger + alerts。"""
    return run_collect_cost_data(inp, ctx)


def run_answer_cost_question(inp: dict, ctx: dict) -> dict:
    rag = ctx["rag"]
    hits = rag.retrieve(inp.get("question", ""), top_k=int(inp.get("top_k", 5)))
    return {"ok": True, "evidence": format_evidence(hits),
            "note": "基于 evidence 引用来源作答；无依据时明说「未见依据」，不得编造。"}


def run_save_cost_ledger(inp: dict, ctx: dict) -> dict:
    store = ctx["store"]
    ent = ctx.get("enterprise_id", "default")
    proj = ctx.get("project_id", "default")
    store.append(ent, proj, "cost_ledger",
                 {"ledger": inp.get("ledger"), "alerts": inp.get("alerts")})
    return {"ok": True, "saved": "cost_ledger"}


COST_TOOL_HANDLERS = {
    "ingest_knowledge": run_ingest_knowledge,
    "search_cost_knowledge": run_search_cost_knowledge,
    "collect_cost_data": run_collect_cost_data,
    "alert_deviation": run_alert_deviation,
    "answer_cost_question": run_answer_cost_question,
    "save_cost_ledger": run_save_cost_ledger,
}
