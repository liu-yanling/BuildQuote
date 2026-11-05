"""Agent 团队（s13）：Lead + 持久队友 + 文件邮箱 + 原子任务认领 + 任务工作区。

- 每个队友绑定独立工作区 `.worktrees/<name>/`。
- 通信走文件收件箱 `.mailboxes/<name>.jsonl`（模型上下文之外）。
- 任务用 TaskStore 原子认领/完成（复用 s10）。
- 并行分派用线程池；模型调用各自独立。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.subagent import run_subagent
from app.tasks import TaskStore

ROOT = Path(__file__).resolve().parent.parent
MAILBOX_DIR = ROOT / ".mailboxes"
WORKTREE_DIR = ROOT / ".worktrees"


class MessageBus:
    """文件收件箱：.mailboxes/<agent>.jsonl。"""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else MAILBOX_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, agent: str) -> Path:
        return self.root / f"{agent}.jsonl"

    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict | None = None) -> None:
        with self._path(to_agent).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"from": from_agent, "to": to_agent, "content": content,
                                "type": msg_type, "metadata": metadata or {}}, ensure_ascii=False) + "\n")

    def drain(self, agent: str) -> list[dict]:
        p = self._path(agent)
        if not p.is_file():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()
        p.write_text("", encoding="utf-8")
        out: list[dict] = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


@dataclass
class Teammate:
    name: str
    role: str
    system: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    handlers: dict[str, Any] = field(default_factory=dict)
    workdir: Path | None = None


class Team:
    """Lead 管理团队：spawn 队友 → 认领任务 → 子 Agent 循环 → 结果投递邮箱。"""

    def __init__(self, model, ctx: dict, store: TaskStore | None = None,
                 bus: MessageBus | None = None, root: Path | None = None):
        self.model = model
        self.ctx = ctx
        self.store = store or TaskStore()
        self.bus = bus or MessageBus()
        self.root = Path(root) if root else WORKTREE_DIR
        self.teammates: dict[str, Teammate] = {}

    def spawn(self, name: str, role: str, system: str,
              tools: list[dict] | None = None, handlers: dict | None = None) -> Teammate:
        workdir = self.root / name
        workdir.mkdir(parents=True, exist_ok=True)
        tm = Teammate(name=name, role=role, system=system, tools=tools or [],
                      handlers=handlers or {}, workdir=workdir)
        self.teammates[name] = tm
        return tm

    def _run_one(self, name: str, prompt: str) -> str:
        tm = self.teammates[name]
        ctx = {**self.ctx, "workdir": str(tm.workdir), "agent": name}
        task = self.store.create(subject=f"[team:{name}] {prompt[:40]}")
        claimed = self.store.claim(task.id, owner=name)
        if not claimed.startswith("已认领"):
            self.bus.send(name, "lead", claimed, msg_type="error")
            return claimed
        result = run_subagent(self.model, tm.system, prompt, tm.tools, tm.handlers, ctx)
        self.store.complete(task.id, owner=name)
        text = result.get("text", "")
        self.bus.send(name, "lead", text, msg_type="result",
                      metadata={"rounds": result.get("rounds"), "stopped": result.get("stopped")})
        return text

    def dispatch(self, name: str, prompt: str) -> str:
        return self._run_one(name, prompt)

    def run_parallel(self, assignments: dict[str, str]) -> dict[str, str]:
        """并行分派：每个队友一个任务（线程池）。"""
        with ThreadPoolExecutor(max_workers=max(1, len(assignments))) as ex:
            futs = {name: ex.submit(self._run_one, name, prompt)
                    for name, prompt in assignments.items()}
            return {name: f.result() for name, f in futs.items()}

    def collect(self, agent: str = "lead") -> list[dict]:
        return self.bus.drain(agent)
