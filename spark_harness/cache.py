"""缓存相关：工具结果缓存 + 跨请求缓存命中统计。

两个层面都在省 token / 省钱：
1. 工具结果缓存：相同 (工具名, 参数) 在 TTL 内直接复用结果，避免重复执行与重复占用上下文。
2. 缓存命中统计：统一三家厂商的 usage 字段，给出全局缓存命中率，用于 bench 与监控。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field


@dataclass
class ToolResultCache:
    """按 (工具名 + 参数) 哈希去重的短时效结果缓存。"""

    ttl: int = 600
    enabled: bool = True
    _store: dict = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    @staticmethod
    def _key(name: str, args: dict) -> str:
        payload = json.dumps({"n": name, "a": args}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, name: str, args: dict):
        if not self.enabled:
            self.misses += 1
            return None
        k = self._key(name, args)
        entry = self._store.get(k)
        if entry is not None:
            result, ts = entry
            if time.time() - ts < self.ttl:
                self.hits += 1
                return result
        self.misses += 1
        return None

    def put(self, name: str, args: dict, result) -> None:
        if not self.enabled:
            return
        self._store[self._key(name, args)] = (result, time.time())

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


@dataclass
class CacheStats:
    """跨请求的缓存命中累计统计（统一三家厂商口径）。"""

    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    uncached_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def add(self, *, cache_read: int = 0, cache_write: int = 0,
            uncached: int = 0, prompt: int = 0, completion: int = 0) -> None:
        self.cache_read_tokens += cache_read
        self.cache_write_tokens += cache_write
        self.uncached_tokens += uncached
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls += 1

    @property
    def hit_rate(self) -> float:
        """命中率 = 缓存读取 / (读取 + 写入)。写入即冷启动成本。"""
        denom = self.cache_read_tokens + self.cache_write_tokens
        return self.cache_read_tokens / denom if denom else 0.0

    @property
    def saved_tokens(self) -> int:
        """相比全部按未缓存计，省下的输入 token。"""
        return self.cache_read_tokens

    def summary(self) -> str:
        return (
            f"调用 {self.calls} 次 | 输入 {self.prompt_tokens} tok | "
            f"缓存读取 {self.cache_read_tokens} / 写入 {self.cache_write_tokens} | "
            f"命中率 {self.hit_rate:.1%} | 省 {self.saved_tokens} tok"
        )
