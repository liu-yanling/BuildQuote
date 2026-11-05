"""子 Agent（s06）：全新消息列表的嵌套循环，最终文本返回父循环。

隔离的是消息，不是进程或文件系统；子 Agent 工具走同一权限与生命周期钩子，最多 30 轮。
"""
from __future__ import annotations

import json
from typing import Any

from app import hooks, permissions

SUB_MAX_ROUNDS = 30


def run_subagent(model, system: str, prompt: str, tools: list[dict], handlers: dict,
                 ctx: dict, max_rounds: int = SUB_MAX_ROUNDS) -> dict:
    """运行一段独立上下文的子 Agent 循环，返回 {text, rounds, trajectory}。"""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    trajectory: list[dict] = []
    for r in range(1, max_rounds + 1):
        resp = model.complete(system, messages, tools)
        trajectory.append({"round": r, "stop_reason": resp["stop_reason"],
                           "text": (resp["text"] or "")[:200]})
        if resp["stop_reason"] != "tool_calls" or not resp["tool_uses"]:
            return {"text": resp["text"] or "", "rounds": r, "trajectory": trajectory}

        tool_use = resp["tool_uses"][0]
        name = tool_use["name"]
        tool_input = dict(tool_use["input"] or {})
        pre = {"tool": name, "input": tool_input,
               "actor": getattr(ctx.get("user"), "user_id", None),
               "enterprise_id": ctx.get("enterprise_id"), "project_id": ctx.get("project_id")}
        blocked = hooks.trigger_hooks("PreToolUse", pre)
        if blocked:
            result = {"ok": False, "reason": blocked.get("reason", "被钩子阻断"), "blocked": True}
        else:
            handler = handlers.get(name)
            user = ctx.get("user")
            ok, reason = permissions.check_permission(user, name, {
                "enterprise_id": ctx.get("enterprise_id"), "project_id": ctx.get("project_id")})
            if handler is None:
                result = {"ok": False, "reason": f"未知工具 {name}"}
            elif not ok:
                result = {"ok": False, "reason": reason, "blocked": True}
            else:
                out = handler(tool_input, ctx)
                result = out if isinstance(out, dict) else {"ok": True, "data": out}

        hooks.trigger_hooks("PostToolUse", {**pre, "ok": result.get("ok", True),
                                            "reason": result.get("reason", "")})

        messages.append({"role": "assistant", "content": resp["text"] or None,
                         "tool_calls": [{"id": tool_use["id"], "type": "function",
                                         "function": {"name": name,
                                                      "arguments": json.dumps(tool_input, ensure_ascii=False)}}]})
        messages.append({"role": "tool", "tool_call_id": tool_use["id"],
                         "content": json.dumps(result, ensure_ascii=False)[:2000]})
    return {"text": "子 Agent 超过轮数上限仍未给出最终结论。", "rounds": max_rounds,
            "trajectory": trajectory, "stopped": "max_rounds"}
