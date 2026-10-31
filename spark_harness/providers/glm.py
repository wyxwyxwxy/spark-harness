"""智谱 GLM 适配器。

缓存机制：在请求体中加 cache 参数。OpenAI 兼容路径下用：
    extra_body={"cache": True, "cache_ttl": <秒>}
直连 HTTP 时把参数放在 JSON 顶层即可（本实现走直连）。
优化要点：cache_ttl 与 HarnessConfig.cache_ttl_seconds 对齐；稳定前缀命中后低价计费。
"""

from __future__ import annotations

from .base import BaseProvider, Usage


class GLMProvider(BaseProvider):
    def _inject_cache(self, payload: dict) -> None:
        payload["cache"] = True
        payload["cache_ttl"] = self.config.cache_ttl_seconds

    def _parse_usage(self, u: dict, usage: Usage) -> None:
        details = u.get("prompt_tokens_details", {}) or {}
        usage.cache_read_tokens = details.get("cached_tokens", 0)
        usage.cache_write_tokens = details.get("cache_write_tokens", 0)
        # GLM 也可能直接给出顶层字段
        if not usage.cache_read_tokens and not usage.cache_write_tokens:
            usage.cache_read_tokens = u.get("cached_tokens", 0)
            usage.cache_write_tokens = u.get("cache_write_tokens", 0)
