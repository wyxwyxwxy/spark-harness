"""Agent 编排循环 v2：ReAct + 并行工具 + 结果外置 + 极简 Schema + 95% 自动压缩 + 检索注入
+ 超时熔断执行器 + 动态工具调用（tool_choice / 内置动态工具 / 运行时注册）。

极致优化落点（每层都省 token）：
- 工具结果外置：大输出存进 MemoryStore，上下文只留「[记忆 id] 摘要」。
- 极简 Schema + 一句话索引：每请求只发 name/类型/必填。
- 检索注入：每步只把相关记忆摘要拉进上下文（≤1500 tok）。
- 前缀缓存锚点稳定：system(含索引)+环境简报到最前不变。
- 95% 阈值递归压缩：历史逼近预算时递归摘要，外置内容不丢。
- 结果缓存去重：相同 (工具, 参数) 直接复用。
- 超时熔断：单个工具硬超时 + 坏工具自动熔断，Agent 循环不被拖垮。
- 动态工具调用：tool_choice 控制调用策略；内置动态工具 web_search/web_extractor/
  code_interpreter 按需挂载；运行时可 register_tool 扩展。
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Optional

from .cache import CacheStats, ToolResultCache
from .compression import compress_tool_result
from .context import ContextManager
from .plugin import HookRegistry
from .providers.base import BaseProvider
from .runtime import ToolExecutor
from .tools import ToolRegistry


class Agent:
    def __init__(
        self,
        config,
        provider: BaseProvider,
        registry: ToolRegistry,
        memory,
        tool_cache: Optional[ToolResultCache] = None,
        executor: Optional[ToolExecutor] = None,
        verbose: bool = True,
        hooks: Optional[HookRegistry] = None,
    ):
        self.config = config
        self.provider = provider
        self.registry = registry
        self.memory = memory
        self.ctx = ContextManager(config, memory, registry)
        self.tool_cache = tool_cache or ToolResultCache(
            ttl=config.tool_cache_ttl, enabled=config.enable_tool_cache
        )
        self.executor = executor or ToolExecutor(
            default_timeout=config.tool_timeout,
            timeout_map=config.tool_timeout_map or None,
            max_concurrency=config.tool_max_concurrency,
            retries=config.tool_retries,
            circuit=CircuitBreakerFromConfig(config),
        )
        self.verbose = verbose
        self.hooks = hooks or HookRegistry()  # 插件钩子（生命周期扩展点）
        # 绑定当前记忆库，供 recall 工具访问
        from .memory import set_active_store

        set_active_store(memory)

    # —— 运行时动态注册工具（动态工具调用的一部分）——
    def register_tool(self, spec: dict) -> str:
        self.registry.register_from_dict(spec)
        # 工具集变了，system 锚点里的索引需刷新（保持缓存命中：仅当真正新增时）
        self.ctx.system = self.config.system_prompt + "\n\n" + self.registry.compact_index()
        return f"已动态注册工具 {spec.get('name')}"

    async def run(self, user_input: str, max_iterations: int = 16) -> str:
        self.ctx.add_turn({"role": "user", "content": user_input})
        final_answer = ""
        await self.hooks.atrigger("on_agent_start", user_input)

        for step in range(max_iterations):
            self.ctx.set_last_query(user_input)
            # 1) 预算守护（95% 阈值递归压缩）
            await self.ctx.maybe_compact(self.provider)
            if self.ctx.total_tokens() > self.config.max_input_tokens:
                self.ctx.history = self.ctx.history[-self.config.keep_recent_turns:]

            messages = self.ctx.build_messages()
            tools = self.registry.minimal_schema()  # 极简 Schema

            # 末轮强制只生成文本，避免工具循环不休
            tc = self.config.tool_choice
            if step >= max_iterations - 1:
                tc = "none"

            try:
                content, tool_calls, usage = await self.provider.complete(
                    messages, tools=tools, tool_choice=tc
                )
            except Exception as e:  # 重试一次后退避
                if self.verbose:
                    print(f"[retry] 请求失败: {e}")
                await asyncio.sleep(1)
                try:
                    content, tool_calls, usage = await self.provider.complete(
                        messages, tools=tools, tool_choice=tc
                    )
                except Exception as e2:  # noqa
                    return f"ERROR: 模型请求持续失败: {e2}"

            if self.verbose:
                print(
                    f"[step {step}] 上下文 {usage.prompt_tokens} tok | "
                    f"读缓存 {usage.cache_read_tokens} / 写缓存 {usage.cache_write_tokens} | "
                    f"输出 {usage.completion_tokens} | 命中率 {usage.hit_rate:.0%}"
                )

            if not tool_calls:
                if content:
                    self.ctx.add_turn({"role": "assistant", "content": content})
                    final_answer = content
                break

            # 2) 记录 assistant 的 tool_calls（保持 wire 格式以便续轮；含 reasoning_content）
            assistant_msg = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {
                        "id": tc_["id"],
                        "type": "function",
                        "function": {
                            "name": tc_["name"],
                            "arguments": json.dumps(tc_["arguments"], ensure_ascii=False),
                        },
                    }
                    for tc_ in tool_calls
                ],
            }
            if usage.reasoning_content:
                assistant_msg["reasoning_content"] = usage.reasoning_content
            self.ctx.add_turn(assistant_msg)

            # 3) 并行执行（超时熔断执行器）+ 结果外置 + 结果缓存去重
            results = await asyncio.gather(*[self._run_tool(tc_) for tc_ in tool_calls])
            for tc_, (display, cached) in zip(tool_calls, results):
                if self.verbose and cached:
                    print(f"  ↳ 工具 {tc_['name']} 命中结果缓存，跳过执行")
                self.ctx.add_turn(
                    {"role": "tool", "tool_call_id": tc_["id"], "content": display}
                )

        if self.verbose:
            print("\n=== 缓存与熔断统计 ===")
            print(self.provider.stats.summary())
            print(
                f"工具结果缓存: 命中 {self.tool_cache.hits} / 未命中 {self.tool_cache.misses} "
                f"({self.tool_cache.hit_rate:.0%})"
            )
            print("熔断/超时:", json.dumps(self.executor.report(), ensure_ascii=False, indent=2))
            print("记忆库:", self.memory.stats())
        await self.hooks.atrigger("on_agent_end", final_answer)
        return final_answer

    async def _run_tool(self, tc: dict):
        name = tc["name"]
        # —— 插件钩子 pre_tool_call：允许改写参数 ——
        args = await self.hooks.atrigger("pre_tool_call", name, tc["arguments"] or {}, default=tc["arguments"] or {})
        args = args or {}
        cached = self.tool_cache.get(name, args)
        if cached is not None:
            return cached, True
        # —— 超时熔断执行：实时执行，单工具卡死不影响整体 ——
        fn = self.registry.get_fn(name)
        raw = None
        err = None
        if fn is None:
            raw = f"ERROR: 未知工具 {name}（可用 register_tool 动态注册）"
        else:
            try:
                raw = await self.executor.execute(name, fn, args)
            except Exception as e:  # noqa
                err = str(e)
                raw = f"ERROR: {err}"
        raw = str(raw)
        # —— 插件钩子 post_tool_call ——
        await self.hooks.atrigger("post_tool_call", name, args, raw, err)
        # —— 结果外置：大内容存记忆库，上下文只留摘要 + id ——
        compressed = compress_tool_result(raw, hard_cap=700, query=self.ctx.last_query)
        mid = self.memory.add(
            kind="tool", summary=f"{name}→{compressed[:120]}", full=raw
        )
        display = f"[记忆 {mid}] {compressed}"
        self.tool_cache.put(name, args, display)
        return display, False


def CircuitBreakerFromConfig(config):
    from .runtime import CircuitBreaker

    return CircuitBreaker(
        failure_threshold=config.breaker_threshold,
        cooldown=config.breaker_cooldown,
        success_threshold=2,
    )
