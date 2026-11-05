"""FastAPI 入口：五步闭环 HTTP 接口 + 启动校验 + 定时催收启动。

启动即校验 .env 关键键；缺 MODEL_API_KEY / DATA_KEY / SESSION_SECRET 则失败。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import (cost_rules, cost_tools, eval as eval_mod, export as export_mod,
                 permissions, prompts, rules, scheduler, team as team_mod, tools,
                 workflow)
from app.agent_loop import Harness
from app.config import load_settings
from app.embeddings import make_embedder
from app.llm import ModelClient, ModelRunner
from app.models import User
from app.rag import RagEngine
from app.storage import Store
from app.tasks import InquiryBoard, TaskStore
from app.vectorstore import VectorStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("buildquote")

settings = load_settings()
store = Store(settings.db_path, settings.data_key)
tasks = TaskStore()
board = InquiryBoard(tasks)

# 真实报告导出目录（.pdf/.docx 落盘；data/ 已被 .gitignore 忽略）
_EXPORT_DIR = settings.db_path.parent / "exports"

# V4 RAG：独立 embedding API（OpenAI 兼容；无 key 时本地确定性降级）+ SQLite 向量库
embedder = make_embedder(settings)
vector_store = VectorStore(settings.db_path.with_name("buildquote_vectors.db"))
rag = RagEngine(embedder, vector_store)

app = FastAPI(title="BuildQuote", version="0.3.0")


def _user_from(payload: dict) -> User:
    return User(user_id=payload.get("user_id", ""), name=payload.get("name", ""),
                role=payload.get("role", "成本经理"), enterprise_id=payload.get("enterprise_id", ""),
                project_ids=payload.get("project_ids", []))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "BuildQuote", "workflows": workflow.list_workflows(),
            "embedding": {"configured": bool(settings.embedding_api_key),
                          "degraded": embedder.degraded}}


_STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ---- 报价 ----
class ParseIn(BaseModel):
    inquiry_id: str
    file_path: str
    format: str = ".md"
    supplier: str = ""
    text: str = ""
    user: dict[str, Any]
    project_id: str = ""


@app.post("/api/quotes/parse")
def parse_quote(payload: ParseIn) -> dict:
    user = _user_from(payload.user)
    ok, reason = permissions.check_permission(user, "parse_quote_file")
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    return tools.run_parse_quote_file(payload.dict(),
                                      {"user": user, "store": store,
                                       "enterprise_id": user.enterprise_id,
                                       "project_id": payload.project_id})


class ManualIn(BaseModel):
    inquiry_id: str
    quote: dict[str, Any]
    user: dict[str, Any]
    project_id: str = ""


@app.post("/api/quotes/manual")
def manual_entry(payload: ManualIn) -> dict:
    user = _user_from(payload.user)
    ok, reason = permissions.check_permission(user, "record_manual_entry")
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    return tools.run_record_manual_entry(payload.dict(),
                                         {"user": user, "store": store,
                                          "enterprise_id": user.enterprise_id,
                                          "project_id": payload.project_id})


class ComputeIn(BaseModel):
    inquiry_id: str = ""
    items: list[dict[str, Any]]
    config: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None


@app.post("/api/quotes/compute")
def compute(payload: ComputeIn) -> dict:
    return rules.compute_price_rules(payload.items, payload.config, payload.reference)


# ---- 参考价 ----
class SearchIn(BaseModel):
    query: str
    scope: dict[str, Any] | None = None
    user: dict[str, Any]
    project_id: str = ""


@app.post("/api/reference/search")
def search_reference(payload: SearchIn) -> dict:
    user = _user_from(payload.user)
    return tools.run_search_reference_price(payload.dict(),
                                            {"user": user, "store": store,
                                             "enterprise_id": user.enterprise_id,
                                             "project_id": payload.project_id})


# ---- 任务状态 ----
class TaskStateIn(BaseModel):
    inquiry_id: str
    step: str = ""
    suppliers_replied: list[str] = []
    user: dict[str, Any]
    project_id: str = ""


@app.post("/api/tasks/save")
def save_task(payload: TaskStateIn) -> dict:
    return tools.run_save_task_state(payload.dict(),
                                     {"user": _user_from(payload.user), "store": store,
                                      "enterprise_id": payload.user.get("enterprise_id", ""),
                                      "project_id": payload.project_id})


# ---- 询价单 ----
class InquiryInitIn(BaseModel):
    inquiry_id: str
    suppliers: list[str]
    deadline: str = ""


@app.post("/api/inquiries/init")
def init_inquiry(payload: InquiryInitIn) -> dict:
    if len(payload.suppliers) < 3:
        raise HTTPException(status_code=400, detail="一次询价至少 3 家供应商")
    steps = board.init_inquiry(payload.inquiry_id, payload.suppliers)
    store.append("default", "default", "inquiries", {
        "inquiry_id": payload.inquiry_id, "suppliers": payload.suppliers,
        "deadline": payload.deadline, "replied_suppliers": []})
    return {"ok": True, "inquiry_id": payload.inquiry_id,
            "steps": [{"subject": s.subject, "status": s.status} for s in steps]}


@app.get("/api/inquiries/{inquiry_id}/status")
def inquiry_status(inquiry_id: str) -> dict:
    return {"inquiry_id": inquiry_id, "steps": board.step_status(inquiry_id)}


# ---- 催收（s12） ----
class RemindIn(BaseModel):
    enterprise_id: str = "default"
    project_id: str = "default"


@app.post("/api/reminders/run")
def run_reminders(payload: RemindIn) -> dict:
    inquiries = store.query(payload.enterprise_id, payload.project_id, "inquiries")
    actions = scheduler.scan_and_remind(store, payload.enterprise_id, payload.project_id, inquiries)
    return {"ok": True, "actions": actions, "count": len(actions)}


# ---- 报告审核与导出 ----
class ApproveIn(BaseModel):
    report_id: str
    action: str          # 通过 / 打回
    reviewer: dict[str, Any]
    project_id: str = ""
    report: dict[str, Any] | None = None   # 报告快照素材（人工确认后的报价明细）；审核通过时固化


@app.post("/api/reports/approve")
def approve_report(payload: ApproveIn) -> dict:
    user = _user_from(payload.reviewer)
    ok, reason = permissions.check_permission(user, "approve_report", {"project_id": payload.project_id})
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    status = "审核通过" if payload.action == "通过" else "打回"
    record: dict[str, Any] = {
        "report_id": payload.report_id, "review_status": status, "reviewer_role": user.role,
        "reviewer": user.user_id, "reviewed_at": _now()}
    # 铁律：审核通过时把「人工确认后的报价明细」固化为报告快照，由服务端规则引擎重算比价；
    # 导出只认这份快照，前端传什么数字都不采信。
    if status == "审核通过" and payload.report:
        try:
            record.update(export_mod.make_report_snapshot(
                payload.report, payload.report_id, payload.reviewer, reviewed_at=_now()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    store.append(user.enterprise_id, payload.project_id, "reports", record)
    store.write_log(user.enterprise_id, payload.project_id, "approve_report",
                    {"actor": user.user_id, "report_id": payload.report_id, "action": payload.action})
    return {"ok": True, "status": status, "has_snapshot": bool(record.get("comparison")),
            "snapshot_id": record.get("snapshot_id")}


class ExportIn(BaseModel):
    report_id: str
    format: str = ".pdf"
    reviewer: dict[str, Any]
    project_id: str = ""


@app.post("/api/reports/export")
def export_report(payload: ExportIn) -> dict:
    user = _user_from(payload.reviewer)
    ok, reason = permissions.check_permission(user, "export_report", {"project_id": payload.project_id})
    if not ok:
        raise HTTPException(status_code=403, detail=reason)
    fmt = (payload.format or "").lower()
    if fmt not in tools.EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式 {payload.format}")
    reports = store.query(user.enterprise_id, payload.project_id, "reports",
                          predicate=lambda r: r.get("report_id") == payload.report_id)
    reviewed = [r for r in reports if permissions.assert_report_reviewed(r)]
    snapshots = [r for r in reviewed if r.get("comparison")]
    if not snapshots:
        if reviewed:
            raise HTTPException(status_code=400,
                                detail="报告内容快照缺失：请在提交审核时附带报价明细后重新审核")
        raise HTTPException(status_code=403, detail="报告未通过成本经理审核，不可导出")
    snapshot = snapshots[-1]
    try:
        out = export_mod.export_report_file(snapshot, fmt, _EXPORT_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    store.write_log(user.enterprise_id, payload.project_id, "export_report",
                    {"actor": user.user_id, "report_id": payload.report_id,
                     "format": fmt, "file": out["file"], "size": out["size"]})
    safe_id = export_mod.safe_report_id(payload.report_id)
    return {"ok": True, "file": out["file"], "format": fmt, "size": out["size"],
            "download_url": f"/api/reports/{safe_id}/download?format={fmt}",
            "rule_version": snapshot.get("rule_version", ""),
            "snapshot_id": snapshot.get("snapshot_id"),
            "note": f"已生成真实 {fmt} 报告文件（规则引擎快照 {snapshot.get('snapshot_id') or '—'}）"}


@app.get("/api/reports/{report_id}/download")
def download_report(report_id: str, format: str = ".pdf"):
    """下载已导出的报告文件。

    MVP 单人使用：权限门禁在 POST /api/reports/export（成本经理 + 审核通过）；
    本接口只负责把已生成的文件流式返回。
    """
    fmt = (format or "").lower()
    if fmt not in tools.EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的导出格式 {format}")
    path = _EXPORT_DIR / f"{export_mod.safe_report_id(report_id)}{fmt}"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="报告文件不存在，请先调用 /api/reports/export 导出")
    return FileResponse(str(path), media_type=export_mod.MEDIA_TYPES[fmt], filename=path.name)


# ---- 工作流 ----
class WorkflowRunIn(BaseModel):
    name: str = "quote_comparison"
    args: dict[str, Any] = {}
    resume_from_run_id: str | None = None


@app.post("/api/workflows/run")
def run_workflow(payload: WorkflowRunIn) -> dict:
    model = ModelClient(settings)
    runner = ModelRunner(model, workflow.quote_comparison_system)

    def tool_runner(name: str, a: dict) -> Any:
        if name == "compute_price_rules":
            return rules.compute_price_rules(a.get("items", []), a.get("config"), a.get("reference"))
        raise ValueError(f"工作流调用了未知工具 {name}")

    try:
        return workflow.run_workflow_sync(payload.name, payload.args, payload.resume_from_run_id,
                                          runner=runner, tool_runner=tool_runner)
    except workflow.WorkflowInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---- Agent 演示 ----
class AgentRunIn(BaseModel):
    message: str
    user: dict[str, Any]
    project_id: str = ""


@app.post("/api/agent/run")
def agent_run(payload: AgentRunIn) -> dict:
    user = _user_from(payload.user)
    model = ModelClient(settings)
    harness = Harness(model, {"store": store, "user": user, "rag": rag,
                              "enterprise_id": user.enterprise_id,
                              "project_id": payload.project_id})
    return harness.run_agent_loop(payload.message)


# ---- V4 成本监控 / RAG / 团队 / 评测 ----
class CostMonitorIn(BaseModel):
    budget_rows: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    yellow_ratio: float | None = None
    red_ratio: float | None = None
    user: dict[str, Any]
    project_id: str = ""


@app.post("/api/cost/monitor")
def cost_monitor(payload: CostMonitorIn) -> dict:
    user = _user_from(payload.user)
    ctx = {"user": user, "store": store, "enterprise_id": user.enterprise_id,
           "project_id": payload.project_id, "rag": rag}
    return cost_tools.run_collect_cost_data({
        "budget_rows": payload.budget_rows, "sources": payload.sources,
        "yellow_ratio": payload.yellow_ratio or cost_rules.DEFAULT_YELLOW_RATIO,
        "red_ratio": payload.red_ratio or cost_rules.DEFAULT_RED_RATIO}, ctx)


class KnowledgeIngestIn(BaseModel):
    text: str
    source_id: str
    meta: dict[str, Any] | None = None


@app.post("/api/knowledge/ingest")
def knowledge_ingest(payload: KnowledgeIngestIn) -> dict:
    return cost_tools.run_ingest_knowledge(payload.dict(), {"rag": rag})


class KnowledgeSearchIn(BaseModel):
    query: str
    top_k: int = 5


@app.post("/api/knowledge/search")
def knowledge_search(payload: KnowledgeSearchIn) -> dict:
    return cost_tools.run_search_cost_knowledge(payload.dict(), {"rag": rag})


class TeamRunIn(BaseModel):
    prompt: str
    user: dict[str, Any]
    project_id: str = ""


@app.post("/api/team/run")
def team_run(payload: TeamRunIn) -> dict:
    user = _user_from(payload.user)
    model = ModelClient(settings)
    ctx = {"user": user, "store": store, "enterprise_id": user.enterprise_id,
           "project_id": payload.project_id, "rag": rag, "model": model}
    visible = {t["name"]: t for t in tools.model_visible_tools()}

    def _tools(*names):
        return [visible[n] for n in names if n in visible]

    team = team_mod.Team(model, ctx)
    team.spawn("collect", "collect", prompts.assemble_system("collect"),
               _tools("collect_cost_data", "alert_deviation", "save_cost_ledger", "search_cost_knowledge"),
               tools.TOOL_HANDLERS)
    team.spawn("alert", "alert", prompts.assemble_system("alert"),
               _tools("alert_deviation", "collect_cost_data", "save_cost_ledger", "search_cost_knowledge"),
               tools.TOOL_HANDLERS)
    team.spawn("qa", "qa", prompts.assemble_system("qa"),
               _tools("search_cost_knowledge", "answer_cost_question"),
               tools.TOOL_HANDLERS)
    results = team.run_parallel({
        "collect": f"请归集以下成本数据并输出台账：{payload.prompt}",
        "alert": f"请对以下成本数据做预算-实际比对并输出预警：{payload.prompt}",
        "qa": f"请回答成本问题并引用依据：{payload.prompt}",
    })
    return {"ok": True, "results": results, "mailbox": team.collect("lead")}


class EvalIn(BaseModel):
    samples: dict[str, Any]


@app.post("/api/eval/run")
def eval_run(payload: EvalIn) -> dict:
    return eval_mod.run_eval(payload.samples)


class QaAskIn(BaseModel):
    question: str
    top_k: int = 5


@app.post("/api/qa/ask")
def qa_ask(payload: QaAskIn) -> dict:
    from app.qa import ask

    model = ModelClient(settings)
    return ask(payload.question, rag, model, top_k=payload.top_k)


def start_scheduler():
    """启动定时催收（s12）。注入 scan_and_remind，扫描 default/default 下的询价单。"""

    def _scan():
        inquiries = store.query("default", "default", "inquiries")
        scheduler.scan_and_remind(store, "default", "default", inquiries)

    scheduler.start_scheduler(task_fn=_scan)
    logger.info("定时催收已启动（每小时扫描一次）")


if __name__ == "__main__":  # 一条命令能起
    import uvicorn

    start_scheduler()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
