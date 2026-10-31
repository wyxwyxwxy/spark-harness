"""Kimi (Moonshot) 适配器。

缓存机制：prompt_cache_options 隐式写入，TTL 支持 5m / 1h。连续 agent 任务用 5m 即可
（命中自动免费续期）；长会话/有人工介入用 1h 避免过期重写。
返回值：usage.prompt_tokens_details.cached_tokens（读）/ cache_write_tokens（写）。
注意：Kimi 不支持显式断点，前缀自动匹配，因此"稳定前缀放最前"原则同样最关键。
"""

from __future__ import annotations

from .base import BaseProvider, Usage


class KimiProvider(BaseProvider):
    def _inject_cache(self, payload: dict) -> None:
        ttl = self.config.cache_ttl if self.config.cache_ttl in ("5m", "1h") else "1h"
        payload["prompt_cache_options"] = {"mode": "implicit", "ttl": ttl}

    def _parse_usage(self, u: dict, usage: Usage) -> None:
        details = u.get("prompt_tokens_details", {}) or {}
        usage.cache_read_tokens = details.get("cached_tokens", 0)
        usage.cache_write_tokens = details.get("cache_write_tokens", 0)
