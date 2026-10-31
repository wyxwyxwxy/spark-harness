"""超时熔断执行器（Timeout + Circuit Breaker）。

目标：让 Harness 能"实时、稳定"地动态调用工具——
- 单个工具卡死不会拖垮整个 Agent 循环（硬超时 watch-dog）。
- 反复失败的坏工具自动熔断，快速失败而不是无限重试。
- 并发上限防止一次性把 20 个 tool_calls 全打爆下游服务。

对应业界实践：Agent 生产化必须把工具执行当作"不可信的外部依赖"
（参考 DeepSeek Agent Function Calling 指南中对并行/超时/失败的强调）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional

__all__ = [
    "BreakerState",
    "CircuitBreaker",
    "ToolExecutor",
    "ToolTimeout",
    "ToolBreakerOpen",
]


class BreakerState(str, Enum):
    CLOSED = "closed"      # 正常放行
    OPEN = "open"          # 熔断：快速失败，不再调用
    HALF_OPEN = "half_open"  # 试探：放一个请求验证是否恢复


class ToolTimeout(Exception):
    """工具执行超过硬超时。"""


class ToolBreakerOpen(Exception):
    """熔断器处于 OPEN，工具被快速拒绝。"""


@dataclass
class _BreakerRecord:
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    successes_in_half: int = 0
    opened_at: float = 0.0


class CircuitBreaker:
    """每个工具名一个独立熔断器。

    - failure_threshold：连续失败多少次后熔断（默认 3）。
    - cooldown：熔断后多久进入 HALF_OPEN 试探（默认 30s）。
    - success_threshold：HALF_OPEN 阶段连续成功多少次后闭合（默认 2）。
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown: float = 30.0,
        success_threshold: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.success_threshold = success_threshold
        self._records: Dict[str, _BreakerRecord] = {}

    def _rec(self, name: str) -> _BreakerRecord:
        return self._records.setdefault(name, _BreakerRecord())

    def state(self, name: str) -> BreakerState:
        rec = self._rec(name)
        if rec.state == BreakerState.OPEN:
            if (time.monotonic() - rec.opened_at) >= self.cooldown:
                rec.state = BreakerState.HALF_OPEN
                rec.successes_in_half = 0
        return rec.state

    def allow(self, name: str) -> bool:
        return self.state(name) != BreakerState.OPEN

    def record_success(self, name: str) -> None:
        rec = self._rec(name)
        if rec.state == BreakerState.HALF_OPEN:
            rec.successes_in_half += 1
            if rec.successes_in_half >= self.success_threshold:
                rec.state = BreakerState.CLOSED
                rec.failures = 0
        else:
            rec.failures = 0

    def record_failure(self, name: str) -> None:
        rec = self._rec(name)
        if rec.state == BreakerState.HALF_OPEN:
            rec.state = BreakerState.OPEN
            rec.opened_at = time.monotonic()
            return
        rec.failures += 1
        if rec.failures >= self.failure_threshold:
            rec.state = BreakerState.OPEN
            rec.opened_at = time.monotonic()

    def reset(self, name: Optional[str] = None) -> None:
        if name is None:
            self._records.clear()
        else:
            self._records.pop(name, None)

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {
            n: {"state": r.state.value, "failures": r.failures}
            for n, r in self._records.items()
        }


# 工具名 -> 该工具的硬超时（秒）。未列出者用默认超时。
TimeoutMap = Dict[str, float]

ToolFn = Callable[..., Awaitable[str]]


class ToolExecutor:
    """带超时 + 熔断的工具执行器。

    - 实时：收到 tool_call 立刻执行（不批处理延迟）。
    - 超时：单个工具超过 hard_timeout 立即取消。
    - 熔断：坏工具自动 OPEN，快速失败。
    - 并发：semaphore 限制同时跑的工具数（针对一次性大量 tool_calls）。
    """

    def __init__(
        self,
        circuit: Optional[CircuitBreaker] = None,
        default_timeout: float = 30.0,
        timeout_map: Optional[TimeoutMap] = None,
        max_concurrency: int = 8,
        retries: int = 0,
        retry_base: float = 0.5,
    ):
        self.circuit = circuit or CircuitBreaker()
        self.default_timeout = default_timeout
        self.timeout_map = timeout_map or {}
        self.max_concurrency = max(1, max_concurrency)
        self.retries = retries
        self.retry_base = retry_base
        self._sem = asyncio.Semaphore(max_concurrency)
        # 统计
        self.stats: Dict[str, Dict[str, int]] = {}

    def _timeout_for(self, name: str) -> float:
        return float(self.timeout_map.get(name, self.default_timeout))

    def _count(self, name: str, ok: bool, breaker: bool = False, timeout: bool = False):
        s = self.stats.setdefault(name, {"calls": 0, "ok": 0, "fail": 0, "timeout": 0, "breaker": 0})
        s["calls"] += 1
        if breaker:
            s["breaker"] += 1
        elif timeout:
            s["timeout"] += 1
            s["fail"] += 1
        elif ok:
            s["ok"] += 1
        else:
            s["fail"] += 1

    async def _invoke(self, fn: ToolFn, args: Dict[str, Any], timeout: float) -> str:
        try:
            return await asyncio.wait_for(fn(**args), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise ToolTimeout(f"timeout after {timeout}s") from e

    async def execute(self, name: str, fn: ToolFn, args: Dict[str, Any]) -> str:
        """执行单个工具，返回结果字符串。异常也以字符串形式返回（不抛出），
        这样并行扇出时一个工具炸不影响其它工具。"""
        if not self.circuit.allow(name):
            msg = f"ERROR[breaker-open]: 工具 {name} 已熔断，快速失败（{self.circuit.cooldown}s 后试探）"
            self._count(name, ok=False, breaker=True)
            return msg

        timeout = self._timeout_for(name)
        async with self._sem:
            attempt = 0
            last_err: Optional[Exception] = None
            while attempt <= self.retries:
                try:
                    result = await self._invoke(fn, args, timeout)
                    self.circuit.record_success(name)
                    self._count(name, ok=True)
                    return result
                except ToolTimeout as e:
                    last_err = e
                    self.circuit.record_failure(name)
                    self._count(name, ok=False, timeout=True)
                    break  # 超时不再重试
                except Exception as e:  # 业务异常（工具自己抛的）
                    last_err = e
                    # 指数退避后重试
                    if attempt < self.retries:
                        await asyncio.sleep(self.retry_base * (2 ** attempt))
                        attempt += 1
                        continue
                    self.circuit.record_failure(name)
                    self._count(name, ok=False)
                    return f"ERROR[{type(e).__name__}]: {e}"
            return f"ERROR[{type(last_err).__name__}]: {last_err}"

    async def execute_many(self, items: list[tuple[str, ToolFn, Dict[str, Any]]]) -> list[str]:
        """并发执行多个工具调用（实时扇出），返回与输入同序的结果列表。"""
        tasks = [self.execute(n, f, a) for (n, f, a) in items]
        return await asyncio.gather(*tasks)

    def report(self) -> Dict[str, Any]:
        return {
            "concurrency": self.max_concurrency,
            "default_timeout": self.default_timeout,
            "breakers": self.circuit.snapshot(),
            "stats": self.stats,
        }
