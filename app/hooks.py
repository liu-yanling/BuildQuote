"""钩子系统（s04）：PreToolUse 可阻断、PostToolUse 可观察，异常不影响主循环。"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("buildquote.hooks")

_HOOKS: dict[str, list[Callable[[dict], dict | None]]] = {
    "PreToolUse": [],
    "PostToolUse": [],
}


def register_hook(event: str, fn: Callable[[dict], dict | None]) -> None:
    """注册钩子。event ∈ {PreToolUse, PostToolUse}。"""
    if event not in _HOOKS:
        raise ValueError(f"未知钩子事件 {event}")
    _HOOKS[event].append(fn)


def trigger_hooks(event: str, ctx: dict) -> dict | None:
    """触发一类钩子。PreToolUse 返回非空 dict 即视为阻断（含 reason）。"""
    for fn in _HOOKS.get(event, []):
        try:
            result = fn(ctx)
            if event == "PreToolUse" and result:
                return result
        except Exception as exc:  # 钩子异常不影响主循环
            logger.warning("hook %s 异常：%s", event, exc)
    return None


class AuditHook:
    """审计钩子：把工具调用与权限拒绝写进操作日志。"""

    def __init__(self, store, log_fn=None):
        self.store = store
        self._log = log_fn or (lambda ent, proj, action, detail: None)

    def pre(self, ctx: dict) -> dict | None:
        if ctx.get("blocked"):
            return {"reason": ctx.get("reason", "被权限系统阻断")}
        return None

    def post(self, ctx: dict) -> None:
        ent = ctx.get("enterprise_id", "-")
        proj = ctx.get("project_id", "-")
        self._log(ent, proj, ctx.get("tool", "unknown"), {
            "actor": ctx.get("actor", ""),
            "ok": ctx.get("ok", True),
            "reason": ctx.get("reason", ""),
        })
