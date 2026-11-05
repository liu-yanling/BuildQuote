"""工作流运行时（s16）：模型决定单步，脚本决定编排。Workflow 工具 + journal 断点续跑。

五步闭环「建需求 → 收报价 → 核口径 → 做比价 → 出报告」是固定编排，写成注册在宿主里的
可信脚本；模型只提交 workflow 名称、参数和可选的续跑 run_id，不提交可执行代码。脚本运行时
发出生命周期与进度事件，每步结果写进磁盘 journal，断掉后用 run_id 续跑，没改过的步骤直接
用缓存，不重跑。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / ".runtime"
WORKFLOW_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

MISS = object()


class WorkflowInputError(ValueError):
    """工作流输入/元数据不合法（启动前校验，别等到执行时才发现）。"""


# ---------------------------------------------------------------------------
# 结构化输出校验（极简 JSON Schema 子集：只挡最明显的类型错误，子 agent 输出不可全信）
# ---------------------------------------------------------------------------
class SimpleJsonSchema:
    def __init__(self, schema: dict | None):
        self.schema = schema or {}

    def validate(self, value: Any) -> tuple[bool, str]:
        t = self.schema.get("type")
        if t == "object" and not isinstance(value, dict):
            return False, f"期望对象，得到 {type(value).__name__}"
        if t == "array" and not isinstance(value, list):
            return False, f"期望数组，得到 {type(value).__name__}"
        return True, ""


def _stable_hash(basis: str) -> int:
    return int.from_bytes(hashlib.sha256(basis.encode("utf-8")).digest()[:8], "big")


# ---------------------------------------------------------------------------
# Journal：断点续跑的核心，逐条记录每个 agent() 的结果
# ---------------------------------------------------------------------------
class WorkflowJournal:
    def __init__(self, path: Path):
        self.path = path
        self.cache: dict[str, Any] = {}
        self._f = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    self.cache[rec["key"]] = rec["value"]
                except (json.JSONDecodeError, KeyError):
                    continue
        self._f = open(self.path, "a", encoding="utf-8")

    def record(self, key: str, value: Any) -> None:
        self._f.write(json.dumps({"key": key, "value": value}, ensure_ascii=False) + "\n")
        self._f.flush()
        self.cache[key] = value

    def cached(self, key: str) -> Any:
        return self.cache.get(key, MISS)

    def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = None


# ---------------------------------------------------------------------------
# 任务状态与进度事件
# ---------------------------------------------------------------------------
@dataclass
class LocalWorkflowTask:
    run_id: str
    name: str
    status: str = "running"                     # running | completed | failed
    events: list[dict] = field(default_factory=list)
    result: Any = None

    def event(self, ptype: str, **data: Any) -> None:
        self.events.append({"type": ptype, **data})

    def progress_event(self, ptype: str, **data: Any) -> None:
        self.event(ptype, **data)


# ---------------------------------------------------------------------------
# 执行状态：脚本只暴露编排原语，不直接读写文件、不跑 shell
# ---------------------------------------------------------------------------
class ExecutionState:
    def __init__(self, task: LocalWorkflowTask, journal: WorkflowJournal,
                 runner: Any, tool_runner: Callable[[str, dict], Any] | None = None):
        self.task = task
        self.journal = journal
        self.runner = runner                # 有 .run(prompt, schema, label) -> dict
        self.tool_runner = tool_runner      # 宿主工具调用（规则引擎等），可选

    # ---- 编排原语 ----
    def phase(self, title: str) -> None:
        self.task.progress_event("phase", phase=title)

    def log(self, message: str) -> None:
        self.task.progress_event("log", message=message)

    def tool(self, name: str, args: dict | None = None) -> Any:
        """宿主工具调用（BuildQuote 扩展：数字计算必须走规则引擎，不经模型）。"""
        if self.tool_runner is None:
            raise WorkflowInputError(f"未注入 tool_runner，无法调用 {name}")
        self.task.progress_event("workflow_tool", tool=name)
        return self.tool_runner(name, args or {})

    def _key(self, kind: str, label: str, prompt: str, schema: Any) -> str:
        basis = f"{kind}|{label}|{prompt}|{json.dumps(schema, sort_keys=True, ensure_ascii=False)}"
        return f"{kind}-{_stable_hash(basis) % 10**10:010d}"

    async def agent(self, prompt: str, schema: dict | None = None,
                    label: str = "agent", phase: str | None = None) -> Any:
        """派一个子 agent 干活；schema 存在则校验并重试一次；命中 journal 直接返回缓存。"""
        if phase:
            self.phase(phase)
        key = self._key("agent", label, prompt, schema)
        cached = self.journal.cached(key)
        if cached is not MISS:
            self.task.progress_event("workflow_agent", label=label, status="cached")
            return cached

        result = await asyncio.to_thread(self.runner.run, prompt, schema, label)
        if schema is not None:
            ok, err = SimpleJsonSchema(schema).validate(result)
            if not ok:
                retry = await asyncio.to_thread(self.runner.run, prompt + "\n\n返回合法 JSON。", schema, label)
                ok, err = SimpleJsonSchema(schema).validate(retry)
                if not ok:
                    raise WorkflowInputError(f"agent({label}) 输出不合法: {err}")
                result = retry
        self.journal.record(key, result)
        self.task.progress_event("workflow_agent", label=label, status="done")
        return result

    async def parallel(self, thunks: list[Callable[[], Any]]) -> list[Any]:
        """等齐屏障：所有任务并行跑完，一起等结果回来。"""
        return await asyncio.gather(*[t() for t in thunks])

    async def pipeline(self, items: list[Any], *stages: Callable) -> list[Any]:
        """每个 item 分阶段跑，不等齐；跑完一个往下走一个。"""

        async def run_item(item: Any, idx: int) -> Any:
            value = item
            for stage in stages:
                value = await stage(value, item, idx)
            return value

        return await asyncio.gather(*[run_item(it, i) for i, it in enumerate(items)])


# ---------------------------------------------------------------------------
# 元数据校验 + registry
# ---------------------------------------------------------------------------
def validate_meta(meta: dict) -> dict:
    if not isinstance(meta, dict):
        raise WorkflowInputError("meta 必须是对象字面量")
    if not meta.get("name") or not meta.get("description"):
        raise WorkflowInputError("meta 必须包含 name 和 description")
    if not isinstance(meta["name"], str) or not WORKFLOW_NAME_RE.fullmatch(meta["name"]):
        raise WorkflowInputError("meta.name 必须是 1-64 字符的安全 slug")
    if "phases" in meta and (not isinstance(meta["phases"], list)
                             or not all(isinstance(p, str) and p for p in meta["phases"])):
        raise WorkflowInputError("meta.phases 必须包含非空字符串")
    return meta


WORKFLOWS: dict[str, tuple[dict, Callable]] = {}


def register_workflow(meta: dict, fn: Callable) -> None:
    validate_meta(meta)
    WORKFLOWS[meta["name"]] = (meta, fn)


def list_workflows() -> list[dict]:
    return [{"name": name, "description": meta["description"],
             "phases": meta.get("phases", [])} for name, (meta, _fn) in WORKFLOWS.items()]


# ---------------------------------------------------------------------------
# 运行 / 续跑
# ---------------------------------------------------------------------------
def _snapshot_path(run_id: str) -> Path:
    return RUNTIME_DIR / f"{run_id}.snapshot.json"


def _load_snapshot(run_id: str) -> dict | None:
    p = _snapshot_path(run_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_snapshot(snap: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    _snapshot_path(snap["run_id"]).write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_workflow(name: str, args: dict | None = None,
                       resume_from_run_id: str | None = None,
                       runner: Any = None, tool_runner: Callable[[str, dict], Any] | None = None) -> dict:
    if name not in WORKFLOWS:
        raise WorkflowInputError(f"未知工作流：{name}")
    meta, fn = WORKFLOWS[name]
    args = args or {}

    if resume_from_run_id:
        snap = _load_snapshot(resume_from_run_id)
        if snap is None:
            raise WorkflowInputError(f"续跑 run_id 不存在：{resume_from_run_id}")
        if snap.get("name") != name:
            raise WorkflowInputError(f"续跑 run_id 属于工作流 {snap.get('name')}，不是 {name}")
        run_id = resume_from_run_id
    else:
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        _save_snapshot({"run_id": run_id, "name": name, "args": args, "status": "running"})

    task = LocalWorkflowTask(run_id=run_id, name=name)
    journal = WorkflowJournal(RUNTIME_DIR / f"{run_id}.journal.jsonl")
    journal.open()
    ctx = ExecutionState(task, journal, runner, tool_runner)
    try:
        task.event("task_started", run_id=run_id, name=name)
        result = await fn(ctx, args)
        task.status = "completed"
        task.result = result
        task.event("task_notification", status="completed", result=result)
        _save_snapshot({"run_id": run_id, "name": name, "args": args, "status": "completed"})
        return {"run_id": run_id, "name": name, "status": "completed",
                "result": result, "events": task.events}
    except Exception as exc:  # noqa: BLE001 —— 失败也要落状态，交给上层处理
        task.status = "failed"
        task.event("task_notification", status="failed", error=str(exc))
        _save_snapshot({"run_id": run_id, "name": name, "args": args,
                        "status": "failed", "error": str(exc)})
        return {"run_id": run_id, "name": name, "status": "failed",
                "error": str(exc), "events": task.events}
    finally:
        journal.close()


def run_workflow_sync(name: str, args: dict | None = None,
                      resume_from_run_id: str | None = None,
                      runner: Any = None, tool_runner: Callable[[str, dict], Any] | None = None) -> dict:
    """同步封装：给 FastAPI 同步端点和模型工具分派用。"""
    return asyncio.run(run_workflow(name, args, resume_from_run_id, runner, tool_runner))


# ---------------------------------------------------------------------------
# 保存好的 BuildQuote 编排：五步闭环
# ---------------------------------------------------------------------------
FIELDS_SCHEMA = {"type": "object"}


async def quote_comparison_workflow(ctx: ExecutionState, args: dict) -> dict:
    """五步闭环的固定编排：脚本决定顺序，模型只决定单步的读文件/写说明，数字走规则引擎。"""
    inquiry_id = args.get("inquiry_id", "")
    quotes = args.get("quotes", [])

    # ① 建需求
    ctx.phase("建需求")
    suppliers = sorted({q.get("supplier", "") for q in quotes if q.get("supplier")})
    if len(suppliers) < 3:
        ctx.log(f"供应商 {len(suppliers)} 家，不足 3 家，提示补录后再比价")

    # ② 收报价：每家报价独立解析出候选字段（并行）
    ctx.phase("收报价")
    parsed = await ctx.parallel([
        (lambda q=q, i=i: ctx.agent(
            f"解析报价并提取候选字段（材料/规格/单位/含税单价/税率/运费）：{json.dumps(q, ensure_ascii=False)}",
            schema=FIELDS_SCHEMA, label=f"parse:{q.get('supplier', i)}", phase="收报价"))
        for i, q in enumerate(quotes)
    ])

    # ③ 核口径：模型标出单位/税率/运费差异，供人工确认（不写最终值）
    ctx.phase("核口径")
    confirmed = await ctx.agent(
        f"对候选字段做口径核对，标出单位、税率、运费不一致项：{json.dumps(parsed, ensure_ascii=False)}",
        schema=FIELDS_SCHEMA, label="confirm:口径", phase="核口径")

    # ④ 做比价：数字一律走规则引擎，不经模型
    ctx.phase("做比价")
    items = confirmed.get("items", []) if isinstance(confirmed, dict) else []
    comparison = ctx.tool("compute_price_rules", {"inquiry_id": inquiry_id,
                                                  "items": items, "config": args.get("config", {})})

    # ⑤ 出报告：模型只写说明文字，引用来源，不生成数字
    ctx.phase("出报告")
    summary = await ctx.agent(
        f"根据比价结果撰写分析文字（引用来源，不写数字）：{json.dumps(comparison, ensure_ascii=False)}",
        schema=None, label="report:分析文字", phase="出报告")

    return {"inquiry_id": inquiry_id, "comparison": comparison, "summary": summary,
            "parsed_count": len(parsed), "supplier_count": len(suppliers)}


register_workflow(
    {"name": "quote_comparison",
     "description": "五步闭环比价：建需求 → 收报价 → 核口径 → 做比价 → 出报告",
     "phases": ["建需求", "收报价", "核口径", "做比价", "出报告"]},
    quote_comparison_workflow,
)

# 工作流内 agent 步骤使用的系统提示（只读文件、找依据、写说明，不心算数字）
quote_comparison_system = (
    "你是 BuildQuote 比价分析助手。只读文件、找依据、写说明；单位换算、总价、差价、比例由规则引擎计算，"
    "你不得心算或编造数字；缺失数据只标注「待确认」；关键字段须人工确认。"
)


# ---------------------------------------------------------------------------
# Workflow 工具（模型可见面）
# ---------------------------------------------------------------------------
WORKFLOW_TOOL = {
    "name": "Workflow",
    "description": "运行保存好的编排工作流（如 quote_comparison 五步比价）。可带 run_id 断点续跑。",
    "permission": "allow",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "args": {"type": "object"},
            "resume_from_run_id": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}
