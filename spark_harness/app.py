"""应用装配：从配置 + 技能 + 插件构建可用的 Agent / 注册表 / 记忆库。

供 CLI（run/chat/demo）与 server（serve）复用，保证两条路径行为一致。
插件系统（万物皆可插件）在此合并：插件贡献的工具/技能/Provider/钩子汇入主干。
"""

from __future__ import annotations

from typing import List, Optional

from .cache import ToolResultCache
from .memory import MemoryStore
from .providers import get_provider
from .registry import attach_builtins, build_default_registry, cost_timeout_map
from .runtime import CircuitBreaker, ToolExecutor
from .skill import SkillManager


def build_agent(
    config,
    skills: Optional[SkillManager] = None,
    restrict_to_skills: bool = False,
    plugin_dirs: Optional[List[str]] = None,
):
    """构造 (agent, registry, memory, skills, plugin_manager)。"""
    from .agent import Agent
    from .plugin import PluginManager

    registry = build_default_registry()

    # 内置动态工具（web_search/web_extractor/code_interpreter）
    if config.enable_builtin_tools:
        attach_builtins(registry, config.builtin_tools)

    # —— 插件系统：万物皆可插件 ——
    pm = PluginManager(plugin_dirs or [], verbose=False)
    if plugin_dirs:
        pm.load_all()
        for spec in pm.tools:
            try:
                registry.register(spec)
            except ValueError:
                registry._tools[spec.name] = spec  # 允许插件覆盖同名工具
        if skills is not None:
            pm.apply_skills(skills)

    # 技能约束：若启用且要求，只保留技能声明的工具（界面即能力边界）
    if restrict_to_skills and skills is not None:
        allowed = set(skills.all_tool_names())
        for name in list(registry.names()):
            if name not in allowed:
                registry._tools.pop(name, None)

    provider = get_provider(config, extra_providers=pm.providers)

    memory = MemoryStore()
    tool_cache = ToolResultCache(ttl=config.tool_cache_ttl, enabled=config.enable_tool_cache)
    timeout_map = dict(cost_timeout_map(registry))
    if config.tool_timeout_map:
        timeout_map.update(config.tool_timeout_map)
    executor = ToolExecutor(
        circuit=CircuitBreaker(
            failure_threshold=config.breaker_threshold,
            cooldown=config.breaker_cooldown,
        ),
        default_timeout=config.tool_timeout,
        timeout_map=timeout_map,
        max_concurrency=config.tool_max_concurrency,
        retries=config.tool_retries,
    )
    agent = Agent(
        config, provider, registry, memory,
        tool_cache=tool_cache, executor=executor, verbose=True,
        hooks=pm.hooks,
    )
    return agent, registry, memory, skills, pm
