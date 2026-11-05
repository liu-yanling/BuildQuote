"""Agent Loop（s01）+ 集成 Harness（s15）：单一循环、动态重建系统提示词、共享工具池（DeepSeek 消息格式）。

收敛规则：单任务单元最多 20 轮工具调用；终止条件为「产出候选字段等待确认」或「初稿生成等待审核」；
人工确认/审核导出由界面触发，把结果作为新消息交回循环。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app import hooks, permissions, prompts, tools
from app.llm import ModelClient
from app.models import User

logger = logging.getLogger("buildquote.agent_loop")

MAX_ROUNDS = 20

SYSTEM_PROMPT = (
    "你是 BuildQuote 的建材询价助手，服务甲方成本经理，把供应商报价整理成口径透明、可追溯的比价报告初稿。\n"
    "规则：\n"
    "1. 只负责读文件、找依据、写说明。单位换算、总价、差价、比例、排序一律调用 compute_price_rules，禁止心算或猜价。\n"
    "2. 缺失字段标「待确认」，不得补成事实；缺单位的行不参与比价。\n"
    "3. 口径不一致先列原因，不给排名。\n"
    "4. 每个数字附来源（文件名 + 页码或单元格），每段文字注明出处。\n"
    "5. 参考价只取自已核验许可的公开造价站点，并注明「参考价，非成交价」。\n"
    "6. 不推荐中标供应商，不做价格预测。\n"
    "7. 报告须经成本经理审核通过才能导出。\n"
    "工具：parse_quote_file 解析报价、record_manual_entry 内部代录、search_reference_price 检索参考价、"
    "compute_price_rules 规则计算、save_task_state 存进度、dispatch_async_job 超时转异步、"
    "approve_report 审核留痕、export_report 导出。\n"
    "知识目录：标准物料表、500 条历史报价库、四川公开造价参考源（仅授权范围内）。"
    "基线样本与金标准属离线评估资产，不进入模型上下文。\n"
    "流程五步：建需求 → 收报价 → 核口径 → 做比价 → 出报告。"
)


def assemble_system_prompt(extra_rules: list[str] | None = None) -> str:
    # V4：主控使用模块化四层提示词（科目识别/询价规则/预警解释/报告生成）
    parts = [prompts.assemble_system("lead")]
    parts += extra_rules or []
    return "\n".join(parts)


def assemble_tool_pool() -> list[dict[str, Any]]:
    """共享工具池：只给模型看 allow/ask 工具，deny 工具不下发。"""
    return tools.model_visible_tools()


class Harness:
    """集成 Harness：把 loop、工具、权限、钩子、技能、后台任务归一到单一循环。"""

    def __init__(self, model: ModelClient, ctx: dict):
        self.model = model
        self.ctx = ctx
        self.ctx.setdefault("model", model)
        self.system_prompt = assemble_system_prompt()
        self.tool_pool = assemble_tool_pool()

    def _dispatch(self, tool_name: str, tool_input: dict) -> dict:
        handler = tools.TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {"ok": False, "reason": f"未知工具 {tool_name}"}
        user: User = self.ctx["user"]
        ok, reason = permissions.check_permission(user, tool_name,
                                                  {"enterprise_id": self.ctx.get("enterprise_id"),
                                                   "project_id": self.ctx.get("project_id")})
        if not ok:
            return {"ok": False, "reason": reason, "blocked": True}
        return handler(tool_input, self.ctx)

    def run_agent_loop(self, user_message: str, max_rounds: int = MAX_ROUNDS) -> dict:
        """核心循环（OpenAI/DeepSeek 消息格式）。"""
        messages: list[dict] = [{"role": "user", "content": user_message}]
        trajectory: list[dict] = []
        rounds = 0
        while True:
            rounds += 1
            if rounds > max_rounds:
                return {"stop": "max_rounds", "rounds": rounds, "trajectory": trajectory,
                        "note": f"超过 {max_rounds} 轮工具调用，交还人工处理。"}

            resp = self.model.complete(self.system_prompt, messages, self.tool_pool)
            trajectory.append({"round": rounds, "stop_reason": resp["stop_reason"],
                               "text": resp["text"][:200]})

            if resp["stop_reason"] != "tool_calls" or not resp["tool_uses"]:
                return {"stop": resp["stop_reason"], "rounds": rounds,
                        "text": resp["text"], "trajectory": trajectory}

            tool_use = resp["tool_uses"][0]
            tool_name = tool_use["name"]
            tool_input = dict(tool_use["input"] or {})

            # 钩子：PreToolUse 可阻断（s04）
            pre_ctx = {"tool": tool_name, "input": tool_input, "actor": self.ctx["user"].user_id,
                       "enterprise_id": self.ctx.get("enterprise_id"),
                       "project_id": self.ctx.get("project_id")}
            blocked = hooks.trigger_hooks("PreToolUse", pre_ctx)
            if blocked:
                result = {"ok": False, "reason": blocked.get("reason", "被钩子阻断"), "blocked": True}
            else:
                result = self._dispatch(tool_name, tool_input)
                result = result if isinstance(result, dict) else {"ok": True, "data": result}

            hooks.trigger_hooks("PostToolUse", {**pre_ctx, "ok": result.get("ok", True),
                                                "reason": result.get("reason", "")})

            # OpenAI 格式：assistant 带 tool_calls，tool 角色回传结果
            messages.append({
                "role": "assistant", "content": resp["text"] or None,
                "tool_calls": [{"id": tool_use["id"], "type": "function",
                                "function": {"name": tool_name,
                                             "arguments": json.dumps(tool_input, ensure_ascii=False)}}],
            })
            messages.append({"role": "tool", "tool_call_id": tool_use["id"],
                             "content": json.dumps(result, ensure_ascii=False)[:2000]})
        return {"stop": "unknown", "rounds": rounds, "trajectory": trajectory}
