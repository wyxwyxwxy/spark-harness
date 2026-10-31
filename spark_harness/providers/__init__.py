"""厂商工厂：按配置返回对应适配器。"""

from __future__ import annotations

from ..config import HarnessConfig
from .base import BaseProvider
from .deepseek import DeepSeekProvider
from .glm import GLMProvider
from .kimi import KimiProvider
from .mock import MockProvider

__all__ = [
    "BaseProvider",
    "DeepSeekProvider",
    "GLMProvider",
    "KimiProvider",
    "MockProvider",
    "get_provider",
]


def get_provider(config: HarnessConfig, extra_providers: Optional[dict] = None, **kwargs) -> BaseProvider:
    """根据 config.provider 构造真实适配器；provider='mock' 走 Mock。

    extra_providers: 插件贡献的 {name: ProviderClass}，用于扩展可用模型厂商。
    """
    if config.provider == "mock":
        return MockProvider(config, **kwargs)
    mapping = {
        "deepseek": DeepSeekProvider,
        "glm": GLMProvider,
        "kimi": KimiProvider,
    }
    # 插件 Provider 优先（允许覆盖内置）
    merged = dict(mapping)
    if extra_providers:
        merged.update(extra_providers)
    cls = merged.get(config.provider)
    if cls is None:
        raise ValueError(f"未实现 provider: {config.provider}（可用: {', '.join(merged)}）")
    return cls(config)
