"""Spark Harness —— 对标 Deepseek Harness 的极致上下文/缓存/工具编排框架。

特性：
- 三厂商专门优化：DeepSeek（前缀自动缓存）、GLM（cache 参数）、Kimi（prompt_cache_options）。
- 前缀缓存友好：system + briefing + summary 置于上下文最前且稳定，最大化缓存命中率。
- 极致省 token：预算守护 + 历史压缩 + 工具结果缓存去重。
- 并行工具调用：单轮内多个 tool_calls 并发执行。
- 内置工具：电脑(shell/文件/HTTP)、手机(ADB)、浏览器、3D 建模，可扩展。
"""

from __future__ import annotations

from .agent import Agent
from .cache import CacheStats, ToolResultCache
from .config import HarnessConfig, load_from_env_and_args
from .context import ContextManager
from .providers import (
    BaseProvider,
    DeepSeekProvider,
    GLMProvider,
    KimiProvider,
    MockProvider,
    get_provider,
)
from .registry import build_default_registry
from .tools import ToolRegistry, ToolSpec, tool

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "CacheStats",
    "ToolResultCache",
    "HarnessConfig",
    "load_from_env_and_args",
    "ContextManager",
    "BaseProvider",
    "DeepSeekProvider",
    "GLMProvider",
    "KimiProvider",
    "MockProvider",
    "get_provider",
    "build_default_registry",
    "ToolRegistry",
    "ToolSpec",
    "tool",
]
