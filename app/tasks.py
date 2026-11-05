"""任务系统（s10）：大目标拆小任务，持久化 + blockedBy 依赖 + 认领/完成 + 断点续跑。

存储：.tasks/{task_id}.json，跨会话保留。BuildQuote 用它持久化「询价单」的五步进度、
供应商回复状态与需求版本；询价单跨天存在时，续跑从这里重新加载状态，不重放历史对话。
"""
from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / ".tasks"

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"


@dataclass
class Task:
    """一个可持久化、可认领、可恢复的任务。"""
    id: str
    subject: str
    description: str = ""
    status: str = STATUS_PENDING          # pending | in_progress | completed
    owner: str | None = None              # 负责当前任务的执行者（agent / 角色 / 人）
    blockedBy: list[str] = field(default_factory=list)   # 依赖的任务 ID 列表


class TaskStore:
    """.tasks/ 目录下的文件持久化任务图。排他写入，依赖检查，原子状态变更。"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else TASKS_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- 读写 ----
    def _path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"

    @staticmethod
    def _valid_id(task_id: str) -> bool:
        return bool(task_id) and "/" not in task_id and "\\" not in task_id

    def load(self, task_id: str) -> Task | None:
        if not self._valid_id(task_id):
            return None
        p = self._path(task_id)
        if not p.is_file():
            return None
        try:
            return Task(**json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return None

    def save(self, task: Task) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(task.id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path(task.id))   # 原子替换，避免读到半截文件

    def _new_id(self) -> str:
        while True:
            tid = "task_" + secrets.token_hex(4)
            if not self._path(tid).exists():
                return tid

    def create(self, subject: str, description: str = "",
               blockedBy: list[str] | None = None) -> Task:
        blockedBy = blockedBy or []
        missing = [b for b in blockedBy if self.load(b) is None]
        if missing:
            raise ValueError(f"依赖任务不存在：{missing}")
        task = Task(id=self._new_id(), subject=subject, description=description,
                    blockedBy=blockedBy)
        self.save(task)
        return task

    def get(self, task_id: str) -> Task | None:
        return self.load(task_id)

    def list(self) -> list[Task]:
        out: list[Task] = []
        for p in sorted(self.root.glob("*.json")):
            try:
                out.append(Task(**json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    # ---- 依赖与状态机 ----
    def incomplete_dependencies(self, task: Task) -> list[str]:
        return [b for b in task.blockedBy
                if (dep := self.load(b)) is None or dep.status != STATUS_COMPLETED]

    def can_start(self, task_id: str) -> bool:
        task = self.load(task_id)
        return (task is not None and task.status == STATUS_PENDING
                and not self.incomplete_dependencies(task))

    def claim(self, task_id: str, owner: str = "agent") -> str:
        task = self.load(task_id)
        if task is None:
            return f"任务 {task_id} 不存在"
        if task.status != STATUS_PENDING:
            return f"任务 {task_id} 处于 {task.status}，无法认领"
        blocked = self.incomplete_dependencies(task)
        if blocked:
            return f"被依赖阻塞：{blocked}"
        task.owner = owner
        task.status = STATUS_IN_PROGRESS
        self.save(task)
        return f"已认领 {task_id}（{task.subject}）"

    def complete(self, task_id: str, owner: str = "agent") -> str:
        task = self.load(task_id)
        if task is None:
            return f"任务 {task_id} 不存在"
        if task.status != STATUS_IN_PROGRESS:
            return f"任务 {task_id} 处于 {task.status}，无法完成"
        if task.owner != owner:
            return f"任务 {task_id} 属于 {task.owner}，不是 {owner}"
        ready_before = {t.id for t in self.list()
                        if t.status == STATUS_PENDING and t.blockedBy and self.can_start(t.id)}
        task.status = STATUS_COMPLETED
        self.save(task)
        unblocked = [t.subject for t in self.list()
                     if t.status == STATUS_PENDING and t.blockedBy
                     and t.id not in ready_before and self.can_start(t.id)]
        msg = f"已完成 {task_id}（{task.subject}）"
        if unblocked:
            msg += f"\n解锁：{', '.join(unblocked)}"
        return msg


# ---------------------------------------------------------------------------
# BuildQuote 专用：把「五步闭环」映射成任务 DAG，持久化询价单进度与供应商回复状态
# ---------------------------------------------------------------------------
INQUIRY_STEPS = ["建需求", "收报价", "核口径", "做比价", "出报告"]


class InquiryBoard:
    """一张询价单的任务面板：五步线性依赖 + 每家供应商的收报价子任务。

    - 五步之间 blockedBy 线性串联（建需求 → 收报价 → 核口径 → 做比价 → 出报告）。
    - 「收报价」被拆成每家供应商一个子任务，全部完成后「收报价」才解锁。
    """

    def __init__(self, store: TaskStore | None = None):
        self.store = store or TaskStore()

    def init_inquiry(self, inquiry_id: str, suppliers: list[str]) -> list[Task]:
        """为一张询价单创建任务图，返回按步骤顺序的任务列表。"""
        if self._step_tasks(inquiry_id):
            return self._step_tasks(inquiry_id)
        steps: dict[str, Task] = {}
        for i, step in enumerate(INQUIRY_STEPS):
            blocked = [steps[INQUIRY_STEPS[i - 1]].id] if i > 0 else []
            steps[step] = self.store.create(
                subject=f"[{inquiry_id}] {step}",
                description=f"询价单 {inquiry_id} 的「{step}」步骤",
                blockedBy=blocked,
            )
        # 收报价拆成每家供应商一个子任务，收报价步骤依赖全部子任务
        supplier_tasks = [self.store.create(
            subject=f"[{inquiry_id}] 收报价：{s}",
            description=f"收集供应商 {s} 的报价",
            blockedBy=[steps["建需求"].id],
        ) for s in suppliers]
        steps["收报价"].blockedBy = [steps["建需求"].id] + [t.id for t in supplier_tasks]
        self.store.save(steps["收报价"])
        return [steps[s] for s in INQUIRY_STEPS]

    def _step_tasks(self, inquiry_id: str) -> list[Task]:
        return [t for t in self.store.list()
                if t.subject.startswith(f"[{inquiry_id}] ") and t.subject.split(" ")[1] in INQUIRY_STEPS]

    def step_status(self, inquiry_id: str) -> dict[str, str]:
        """返回五步各自的当前状态（续跑从这里重建进度）。"""
        by_step = {t.subject.split(" ")[1]: t.status for t in self._step_tasks(inquiry_id)}
        return {s: by_step.get(s, "未创建") for s in INQUIRY_STEPS}

    def record_supplier_reply(self, inquiry_id: str, supplier: str, status: str = STATUS_COMPLETED) -> str:
        """记录供应商回复状态（收报价子任务）。"""
        for t in self.store.list():
            if t.subject == f"[{inquiry_id}] 收报价：{supplier}":
                if status == STATUS_COMPLETED and t.status == STATUS_PENDING:
                    self.store.claim(t.id, owner="采购")
                    return self.store.complete(t.id, owner="采购")
                return f"{t.subject} 当前 {t.status}"
        return f"未找到供应商 {supplier} 的收报价任务"
