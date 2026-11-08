"""FastAPI 入口：五步闭环 HTTP 接口 + 启动校验 + 定时催收启动。

启动即校验 .env 关键键；缺 MODEL_API_KEY / DATA_KEY / SESSION_SECRET 则失败。
"""
from __future__ import annotations

import base64
import json
import os
import logging
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import (contract_import, cost_io, cost_rules, cost_tools, eval as eval_mod,
                 export as export_mod, permissions, prompts, quote_import, rules, scheduler,
                 team as team_mod, tools, websearch, workflow)
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

app = FastAPI(title="BuildQuote", version="0.5.0")



# 可选访问控制：设置 APP_USERNAME / APP_PASSWORD 后，公网访问需 HTTP Basic 登录
_APP_USER = os.getenv("APP_USERNAME", "").strip()
_APP_PASS = os.getenv("APP_PASSWORD", "").strip()


@app.middleware("http")
async def _optional_basic_auth(request: Request, call_next):
    if not (_APP_USER and _APP_PASS):
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            user, pwd = base64.b64decode(header[6:]).decode("utf-8").split(":", 1)
        except Exception:  # noqa: BLE001
            user = pwd = ""
        if user == _APP_USER and pwd == _APP_PASS:
            return await call_next(request)
    from fastapi.responses import Response as _Resp

    return _Resp(status_code=401, content="BuildQuote 需要登录",
                 media_type="text/plain", headers={"WWW-Authenticate": 'Basic realm="BuildQuote"'})

@app.middleware("http")
async def _static_cache_headers(request: Request, call_next):
    """静态资源禁止启发式缓存：改完前端刷新即生效，避免「新 HTML + 旧 JS」版本错位。"""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif path == "/" or path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


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


@app.post("/api/quotes/import")
async def quote_import_upload(request: Request, filename: str = "") -> dict:
    """导入报价单（PDF / Word / Excel）：自动识别报价行，供前端回填并做最优报价分析。"""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（上限 10MB）")
    try:
        return quote_import.parse_quote_bytes(data, filename)
    except Exception as exc:  # noqa: BLE001 —— 解析失败统一 400
        raise HTTPException(status_code=400, detail=f"无法识别报价单：{exc}")


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


class CostExportIn(BaseModel):
    budget_rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    yellow_ratio: float | None = None
    red_ratio: float | None = None


@app.get("/api/cost/template")
def cost_template() -> Response:
    """下载成本台账模板（预算 / 成本来源 / 填写说明 三张表）。"""
    data = cost_io.build_template_bytes()
    return Response(content=data, media_type=cost_io.XLSX_MEDIA,
                    headers={"Content-Disposition": 'attachment; filename="cost_ledger_template.xlsx"',
                             "Cache-Control": "no-store"})


@app.post("/api/cost/export")
def cost_export(payload: CostExportIn) -> Response:
    """导出完整成本台账 Excel（预算 / 成本来源 / 台账 / 说明，含合计行）。"""
    yellow = payload.yellow_ratio or cost_rules.DEFAULT_YELLOW_RATIO
    red = payload.red_ratio or cost_rules.DEFAULT_RED_RATIO
    sources = [cost_rules.CostSource(source_type=s.get("source_type", ""),
                                     subject=s.get("subject", ""),
                                     amount=float(s.get("amount", 0) or 0),
                                     source_id=s.get("source_id", ""),
                                     note=s.get("note", ""))
               for s in payload.sources]
    result = cost_rules.compute_cost_monitor(payload.budget_rows, sources,
                                             yellow_ratio=yellow, red_ratio=red)
    data = cost_io.build_export_bytes(payload.budget_rows, payload.sources,
                                      ledger=result.get("ledger"),
                                      yellow_ratio=yellow, red_ratio=red)
    return Response(content=data, media_type=cost_io.XLSX_MEDIA,
                    headers={"Content-Disposition": 'attachment; filename="cost_ledger.xlsx"',
                             "Cache-Control": "no-store"})


@app.post("/api/cost/import")
async def cost_import(request: Request) -> dict:
    """导入成本台账 Excel（模板/导出文件均可，兼容旧版 5 列模板）。"""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（上限 10MB）")
    try:
        parsed = cost_io.parse_ledger_bytes(data)
    except Exception as exc:  # noqa: BLE001 —— 解析失败统一给 400
        raise HTTPException(status_code=400, detail=f"无法解析 Excel 台账：{exc}")
    if not parsed["budget_rows"] and not parsed["sources"]:
        raise HTTPException(status_code=400,
                            detail="未解析到预算或成本来源数据，请使用「导出模板」填写后再导入")
    return {"ok": True, **parsed}


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


# ---- 评测看板：指标持久化 + 历史趋势 + 批量检索评测（v0.8.0） ----
_ROOT = Path(__file__).resolve().parent.parent
_EVAL_LAST = settings.db_path.parent / "eval_last.json"
_EVAL_HISTORY = settings.db_path.parent / "eval_history.json"
_GOLD_LARGE = _ROOT / "evals" / "qa_gold_large.json"
_EVAL_KEYS = {"retrieval", "qa", "alertCoverage", "alertPrecision", "citation"}
_EVAL_LOCK = threading.Lock()
_EVAL_HISTORY_MAX = 30


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_eval_last() -> dict:
    return _read_json(_EVAL_LAST)


def _read_eval_history() -> dict:
    return _read_json(_EVAL_HISTORY)


def _write_eval(key: str, data: dict, *, demo: bool = False, at: str | None = None) -> dict:
    """同一把锁内原子写「上次结果 + 历史点」，避免并发请求互相覆盖。"""
    now = at or _now()
    with _EVAL_LOCK:
        last = _read_eval_last()
        history = _read_eval_history()
        rec = {**data, "at": now}
        if demo:
            rec["demo"] = True
        last[key] = rec
        points = list(history.get(key, []))
        points.append({"value": data.get("value"), "at": now})
        history[key] = points[-_EVAL_HISTORY_MAX:]
        _EVAL_LAST.parent.mkdir(parents=True, exist_ok=True)
        _EVAL_LAST.write_text(json.dumps(last, ensure_ascii=False, indent=1), encoding="utf-8")
        _EVAL_HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"results": last, "history": history}


@app.get("/api/eval/last")
def eval_last() -> dict:
    """上次评测结果 + 每个指标的历史趋势点（刷新后指标卡和曲线仍有数据）。"""
    with _EVAL_LOCK:
        return {"ok": True, "results": _read_eval_last(), "history": _read_eval_history()}


class EvalRecordIn(BaseModel):
    key: str
    data: dict[str, Any] = {}


@app.post("/api/eval/record")
def eval_record(payload: EvalRecordIn) -> dict:
    """前端跑完问答/样例评测后写入指标（自动追加历史点）。"""
    if payload.key not in _EVAL_KEYS:
        raise HTTPException(status_code=400, detail="未知评测指标")
    return {"ok": True, **_write_eval(payload.key, payload.data)}


# 演示数据：没有真实数据时也能一键看到完整看板（会标注「演示数据」）
_DEMO_SERIES = {
    "retrieval": [0.92, 0.94, 0.94, 0.96, 0.96],
    "qa": [1.0, 1.0, 0.9167, 1.0, 1.0],
    "alertCoverage": [0.6667, 0.8, 0.6667, 0.6667],
    "alertPrecision": [0.8, 0.6667, 0.6667, 0.6667],
    "citation": [0.5, 0.5, 0.5, 0.5],
}


@app.post("/api/eval/seed-demo")
def eval_seed_demo() -> dict:
    """一键写入演示评测数据（5 个历史点/指标），用于没有真实数据时展示看板。"""
    from datetime import datetime, timedelta

    now = datetime.now()
    result: dict = {}
    for key, series in _DEMO_SERIES.items():
        for i, value in enumerate(series):
            at = (now - timedelta(hours=(len(series) - 1 - i) * 6)).strftime("%Y-%m-%dT%H:%M:%S")
            result = _write_eval(key, {"value": value}, demo=True, at=at)
    return {"ok": True, **result, "demo": True}


class EvalRetrievalIn(BaseModel):
    top_k: int = 5


def _hit_contract(hits: list[dict], contract: str) -> bool:
    for h in hits:
        hay = (h.get("source_id", "")
               + ((h.get("meta") or {}).get("span", ""))
               + (h.get("text") or ""))
        if contract in hay:
            return True
    return False


@app.post("/api/eval/retrieval")
def eval_retrieval(payload: EvalRetrievalIn) -> dict:
    """批量检索评测：用精简金标准集（50 题）跑 top-k 召回，返回命中率并持久化。"""
    try:
        gold = json.loads(_GOLD_LARGE.read_text(encoding="utf-8")).get("samples", [])
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"金标准集读取失败：{exc}")
    if not gold:
        raise HTTPException(status_code=500, detail="金标准集为空")
    hit = 0
    for item in gold:
        hits = rag.retrieve(item["question"], top_k=payload.top_k)
        if _hit_contract(hits, item["contract"]):
            hit += 1
    value = round(hit / len(gold), 4)
    payload_out = _write_eval("retrieval", {"value": value, "hit": hit, "total": len(gold)})
    return {"ok": True, "value": value, "hit": hit, "total": len(gold), **payload_out}


class QaAskIn(BaseModel):
    question: str
    top_k: int = 5


@app.post("/api/qa/ask")
def qa_ask(payload: QaAskIn) -> dict:
    from app.qa import ask

    model = ModelClient(settings)
    return ask(payload.question, rag, model, top_k=payload.top_k)


# ---- 合同导入：成本来源自动填行 + 合同查看/分析（v0.10.0） ----
@app.post("/api/contracts/import")
async def contract_import_upload(request: Request, filename: str = "") -> dict:
    """导入合同（PDF / Word / Excel）：解析名称/编号/金额/日期/双方/科目，文件存档供查看。"""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（上限 20MB）")
    try:
        meta = contract_import.save_contract(data, filename)
    except Exception as exc:  # noqa: BLE001 —— 解析失败统一 400
        raise HTTPException(status_code=400, detail=f"无法识别合同：{exc}")
    return {"ok": True, **meta}


@app.get("/api/contracts/{contract_id}")
def contract_meta(contract_id: str) -> dict:
    meta = contract_import.load_contract(contract_id)
    if not meta:
        raise HTTPException(status_code=404, detail="合同不存在")
    return {"ok": True, "contract_id": contract_id, "filename": meta.get("filename", ""),
            "format": meta.get("format", ""), "fields": meta.get("fields", {}),
            "excerpt": meta.get("excerpt", ""), "analysis": meta.get("analysis", ""),
            "created_at": meta.get("created_at", "")}


@app.get("/api/contracts/{contract_id}/download")
def contract_download(contract_id: str):
    path = contract_import.contract_file(contract_id)
    meta = contract_import.load_contract(contract_id)
    if not path or not meta:
        raise HTTPException(status_code=404, detail="合同文件不存在")
    media = {"PDF": "application/pdf",
             "Word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
             "Excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(meta.get("format", ""), "application/octet-stream")
    return FileResponse(str(path), media_type=media, filename=meta.get("filename") or path.name)


@app.post("/api/contracts/{contract_id}/analyze")
def contract_analyze(contract_id: str) -> dict:
    """依据合同原文生成「合同要点」分析（DeepSeek；已分析过直接返回缓存）。"""
    meta = contract_import.load_contract(contract_id)
    if not meta:
        raise HTTPException(status_code=404, detail="合同不存在")
    result = contract_import.analyze(meta, ModelClient(settings))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("reason", "合同分析失败"))
    return {"ok": True, "analysis": result.get("analysis", ""), "cached": bool(result.get("cached"))}


class WebSearchIn(BaseModel):
    query: str
    top_k: int = 5
    answer: bool = True


@app.post("/api/web/search")
def web_search(payload: WebSearchIn) -> dict:
    """联网检索（建筑行业通用知识）：维基百科 + 百度网页 + 内置知识兜底；可选 DeepSeek 联网摘要。"""
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="请输入检索内容")
    if len(query) > 200:
        raise HTTPException(status_code=400, detail="检索内容过长（限 200 字）")
    top_k = max(1, min(int(payload.top_k or 5), 8))
    model = ModelClient(settings) if payload.answer else None
    return websearch.search(query, top_k=top_k, model=model, with_answer=payload.answer)


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
