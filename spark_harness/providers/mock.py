"""Mock 厂商：用于本地自测与 bench，真实模拟"前缀缓存"行为。

模拟逻辑：
- 每次请求计算输入 token 总数。
- 与本适配器上一次请求逐条比较 messages，找到从开头起连续相同的"前缀 token 数"。
- 这部分相同前缀 = 缓存读取（cache_read）；本次新增/变化部分 = 缓存写入（cache_write）。
- 因此：稳定前缀不变 -> 命中率随轮次上升；这正是真实前缀缓存（DeepSeek/GLM/Kimi）的语义。

返回内容可由 `script` 驱动，便于确定性测试工具调用流程。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from ..config import HarnessConfig
from ..tokenizer import count_message_tokens, count_messages
from .base import BaseProvider, Usage


class MockProvider(BaseProvider):
    def __init__(self, config: HarnessConfig, script: Optional[List[dict]] = None):
        super().__init__(config)
        self.script = list(script or [])
        self._script_idx = 0
        self._last_messages: Optional[List[dict]] = None
        # 让 mock 也展示三家一致的 usage 归一化口径
        self._vendor = config.provider

    async def complete(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        tool_choice: str = "auto",
    ) -> Tuple[Optional[str], List[dict], Usage]:
        input_tokens = count_messages(messages)

        # —— 模拟前缀缓存：与上一次请求比较公共前缀 ——
        cache_read = 0
        if self._last_messages is not None:
            cache_read = self._common_prefix_tokens(self._last_messages, messages)
        cache_write = max(0, input_tokens - cache_read)

        self._last_messages = [dict(m) for m in messages]

        # —— 决定返回内容 ——
        if self.script:
            resp = self.script[min(self._script_idx, len(self.script) - 1)]
            self._script_idx += 1
            content = resp.get("content")
            tool_calls = resp.get("tool_calls", []) or []
            completion_tokens = count_messages(
                [{"role": "assistant", "content": content or ""}]
            )
        else:
            content = "（mock 默认回复）已收到，继续。"
            tool_calls = []
            completion_tokens = 8

        usage = Usage(
            prompt_tokens=input_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )
        if self._vendor == "deepseek":
            pass  # 归一化字段已对齐
        self.stats.add(
            cache_read=cache_read,
            cache_write=cache_write,
            uncached=max(0, input_tokens - cache_read - cache_write),
            prompt=input_tokens,
            completion=completion_tokens,
        )
        return content, tool_calls, usage

    @staticmethod
    def _common_prefix_tokens(a: List[dict], b: List[dict]) -> int:
        n = min(len(a), len(b))
        total = 0
        for i in range(n):
            if _msg_eq(a[i], b[i]):
                total += count_message_tokens(a[i])
            else:
                break
        return total


def _msg_eq(a: dict, b: dict) -> bool:
    # 忽略易变字段，比较结构性内容
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(
        b, sort_keys=True, ensure_ascii=False
    )
