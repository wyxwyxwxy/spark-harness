"""Harness 配置：默认值 + 环境变量解析 + 三厂商预设。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-flash",  # 现由 DeepSeek-V4.1-Flash-1017 服务
        "env_key": "DEEPSEEK_API_KEY",
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-5.3-flash",
        "env_key": "ZAI_API_KEY",
    },
    "kimi": {
        "base_url": "https://api.moonshot.ai/v1",
        "default_model": "kimi-k3.1-pro",
        "env_key": "MOONSHOT_API_KEY",
    },
    "mock": {
        "base_url": "http://mock.local",
        "default_model": "mock-model",
        "env_key": "",
    },
}

# 稳定的系统提示词：作为缓存锚点的最前端，整轮会话保持不变。
DEFAULT_SYSTEM_PROMPT = """你是 Spark Harness 驱动的通用智能体。你可以通过工具操控电脑与手机、\
生成 3D 模型、调用各类软件。请遵循以下铁律：
1. 先想后做：用最少的工具调用达成目标，能并行就并行。
2. 同一种工具调用若参数完全相同，结果可直接复用，不要重复执行。
3. 输出简洁，把细节留给工具结果；不要把大段文件内容原样复述回上下文。
4. 遇到需要长上下文的任务，优先引用工具返回的摘要，而非反复粘贴原文。
5. 每步只做必要动作，完成后明确汇报结果。"""


@dataclass
class HarnessConfig:
    # —— 厂商与模型 ——
    provider: str = "deepseek"
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None

    # —— 生成参数 ——
    temperature: float = 0.6
    max_tokens: int = 4096
    timeout: int = 120

    # —— 上下文预算（极致省 token 的核心）——
    max_input_tokens: int = 60000          # 单次请求输入预算上限
    keep_recent_turns: int = 8             # 压缩时始终保留的最近轮数
    compact_threshold: float = 0.85        # 已用/预算 >= 此比例时触发压缩
    stream: bool = False

    # —— 缓存 ——
    cache_ttl: str = "1h"                  # kimi/glm 用：5m 或 1h
    cache_ttl_seconds: int = 3600
    enable_tool_cache: bool = True
    tool_cache_ttl: int = 600              # 工具结果缓存秒数

    # —— 动态工具调用（DeepSeek V4.1-Flash-1017 风格）——
    # tool_choice: auto / none / required / 具体函数名 {"type":"function","name":...}
    tool_choice: str = "auto"
    parallel_tool_calls: bool = True       # 并行工具调用（部分厂商支持）
    # 内置"动态工具"：DeepSeek 在 Responses API 原生提供 web_search/web_extractor/
    # code_interpreter；Harness 用本地等价实现，保证任何端点都能用。
    enable_builtin_tools: bool = True
    builtin_tools: tuple = ("web_search", "web_extractor", "code_interpreter")
    # 思考模式（DeepSeek 原生）：reasoning_content 会随 tool_calls 回传，必须原样回发
    deepseek_thinking: bool = False
    reasoning_effort: str = "high"

    # —— 超时熔断（工具执行稳定性）——
    tool_timeout: float = 30.0             # 单个工具硬超时（秒）
    tool_timeout_map: dict = field(default_factory=dict)  # 工具名 -> 超时
    tool_max_concurrency: int = 8          # 单次扇出最大并发
    tool_retries: int = 1                  # 业务异常重试次数
    breaker_threshold: int = 3             # 连续失败几次熔断
    breaker_cooldown: float = 30.0         # 熔断冷却（秒）

    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    def resolve(self) -> "HarnessConfig":
        preset = PROVIDER_PRESETS.get(self.provider)
        if preset is not None:
            if not self.base_url:
                self.base_url = preset["base_url"]
            if not self.model:
                self.model = preset["default_model"]
            if not self.api_key:
                self.api_key = os.getenv(preset["env_key"], "")
        else:
            # 未在内置预设中：允许插件贡献的自定义厂商（万物皆可插件·第五类扩展点）。
            # 真实厂商由插件通过 api.register_provider 注入，get_provider 会合并 extra_providers。
            if not self.base_url:
                self.base_url = ""
            if not self.model:
                self.model = self.provider  # 占位；自定义 Provider 通常忽略
            if not self.api_key:
                self.api_key = ""
        # 把 "1h" -> 3600 等
        self.cache_ttl_seconds = self._parse_ttl(self.cache_ttl)
        return self

    @staticmethod
    def _parse_ttl(ttl: str) -> int:
        ttl = (ttl or "1h").strip().lower()
        if ttl.endswith("m"):
            return int(ttl[:-1]) * 60
        if ttl.endswith("h"):
            return int(ttl[:-1]) * 3600
        if ttl.endswith("s"):
            return int(ttl[:-1])
        return int(ttl) if ttl.isdigit() else 3600


def load_from_env_and_args(overrides: Optional[dict] = None) -> HarnessConfig:
    """从环境变量 SPARK_* 与显式覆盖项构造配置。"""
    cfg = HarnessConfig()
    mapping = {
        "SPARK_PROVIDER": "provider",
        "SPARK_MODEL": "model",
        "SPARK_API_KEY": "api_key",
        "SPARK_BASE_URL": "base_url",
        "SPARK_SYSTEM_PROMPT": "system_prompt",
        "SPARK_MAX_INPUT_TOKENS": "max_input_tokens",
        "SPARK_CACHE_TTL": "cache_ttl",
    }
    for env, attr in mapping.items():
        val = os.getenv(env)
        if val is not None:
            if attr in ("max_input_tokens",):
                setattr(cfg, attr, int(val))
            else:
                setattr(cfg, attr, val)
    if overrides:
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
    return cfg.resolve()
