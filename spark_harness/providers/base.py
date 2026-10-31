"""厂商适配器基类：统一 OpenAI 兼容接口 + 缓存参数注入 + usage 解析。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import requests

from ..cache import CacheStats
from ..config import HarnessConfig
from ..tokenizer import count_messages


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_content: str = ""  # 思考模式回传，续轮必须原样回发
    raw: dict = None  # type: ignore

    @property
    def uncached_tokens(self) -> int:
        return max(0, self.prompt_tokens - self.cache_read_tokens - self.cache_write_tokens)

    @property
    def hit_rate(self) -> float:
        denom = self.cache_read_tokens + self.cache_write_tokens
        return self.cache_read_tokens / denom if denom else 0.0


class BaseProvider:
    """OpenAI 兼容 chat/completions 的通用封装。子类只需覆盖缓存注入与 usage 解析。"""

    def __init__(self, config: HarnessConfig):
        self.config = config
        self.stats = CacheStats()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {config.api_key or ''}",
                "Content-Type": "application/json",
            }
        )

    # —— 子类可覆盖 ——
    def _inject_cache(self, payload: dict) -> None:
        """注入厂商特有的缓存参数。默认无（如 DeepSeek 自动前缀缓存）。"""
        pass

    def _inject_extra(self, payload: dict) -> None:
        """注入厂商特有的额外体（如 DeepSeek 思考模式）。默认无。"""
        pass

    def _parse_usage(self, u: dict, usage: Usage) -> None:
        """把厂商原始 usage 归一化到 cache_read / cache_write。默认无缓存字段。"""
        pass

    # —— 公共逻辑 ——
    async def complete(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        tool_choice: str = "auto",
    ) -> Tuple[Optional[str], List[dict], Usage]:
        """返回 (文本内容, 工具调用列表, 用量)。"""
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
            if self.config.parallel_tool_calls:
                payload["parallel_tool_calls"] = True
        self._inject_cache(payload)
        self._inject_extra(payload)

        raw = await self._post(payload)
        return self._parse(raw)

    async def _post(self, payload: dict) -> dict:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        loop = asyncio.get_event_loop()

        def _sync() -> dict:
            resp = self._session.post(url, data=json.dumps(payload), timeout=self.config.timeout)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"HTTP {resp.status_code} from {self.config.provider}: {resp.text[:500]}"
                )
            return resp.json()

        return await loop.run_in_executor(None, _sync)

    def _parse(self, raw: dict) -> Tuple[Optional[str], List[dict], Usage]:
        choice = raw["choices"][0]["message"]
        content = choice.get("content")
        reasoning_content = choice.get("reasoning_content") or ""
        tool_calls: List[dict] = []
        for tc in choice.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments", "")}
            tool_calls.append(
                {"id": tc.get("id"), "name": fn.get("name"), "arguments": args}
            )

        u = raw.get("usage", {}) or {}
        usage = Usage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            reasoning_content=reasoning_content,
            raw=u,
        )
        self._parse_usage(u, usage)
        if usage.prompt_tokens == 0:
            usage.prompt_tokens = count_messages(raw.get("messages", [])) or 0

        self.stats.add(
            cache_read=usage.cache_read_tokens,
            cache_write=usage.cache_write_tokens,
            uncached=usage.uncached_tokens,
            prompt=usage.prompt_tokens,
            completion=usage.completion_tokens,
        )
        return content, tool_calls, usage
