"""模型 API 客户端（DeepSeek，OpenAI 兼容协议）：httpx 直连 + 重试 + 超时 + finish_reason 处理。

还提供 s16 工作流的 agent() 原语适配器：ModelRunner 接真实模型，MockRunner 供离线测试。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("buildquote.llm")


class ModelClient:
    def __init__(self, settings):
        self.base_url = settings.model_base_url.rstrip("/")
        self.api_key = settings.model_api_key
        self.model = settings.model_name
        self.timeout = settings.model_timeout

    def complete(self, system: str, messages: list[dict], tools: list[dict],
                 max_tokens: int = 2000, retries: int = 2) -> dict:
        """调用 DeepSeek（OpenAI 兼容），返回 {text, tool_uses, stop_reason}。

        messages 为 OpenAI 格式（user/assistant/tool）；tools 内部转换为 function calling 格式。
        tool_uses 归一化为 [{"id", "name", "input"}]。
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                }} for t in tools
            ]
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = httpx.post(f"{self.base_url}/chat/completions",
                               headers={"Authorization": f"Bearer {self.api_key}",
                                        "Content-Type": "application/json"},
                               json=payload, timeout=self.timeout)
                r.raise_for_status()
                data = r.json()
                choice = data["choices"][0]
                msg = choice.get("message") or {}
                text = msg.get("content") or ""
                tool_uses = []
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    tool_uses.append({"id": tc.get("id"), "name": fn.get("name", ""), "input": args})
                return {"text": text, "tool_uses": tool_uses,
                        "stop_reason": choice.get("finish_reason")}
            except Exception as exc:  # noqa: BLE001 —— 超时/网关错误统一降级
                last_exc = exc
                logger.warning("模型调用失败（第 %d 次）：%s", attempt + 1, exc)
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"模型调用失败，已重试 {retries} 次：{last_exc}")


class ModelRunner:
    """s16 工作流的 agent() 原语适配器：把 (prompt, schema, label) 映射到模型调用。"""

    def __init__(self, client: ModelClient, system: str = ""):
        self.client = client
        self.system = system

    def run(self, prompt: str, schema: dict | None = None, label: str = "agent") -> dict:
        resp = self.client.complete(self.system, [{"role": "user", "content": prompt}], tools=[])
        text = resp["text"]
        if schema is not None:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"value": text}
        return {"text": text}


class MockRunner:
    """离线确定性 runner：供单元测试与无模型环境观察事件/journal/续跑。"""

    def run(self, prompt: str, schema: dict | None = None, label: str = "agent") -> dict:
        if schema is not None:
            return {"items": [], "note": f"mock:{label}"}
        return {"text": f"mock 分析文字（{label}）"}
