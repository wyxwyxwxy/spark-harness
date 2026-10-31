"""DeepSeek 适配器（含 V4.1-Flash-1017 动态工具调用优化）。

缓存机制：前缀自动缓存（64 token 分块），无需显式参数；只要请求前缀稳定即可命中。
返回值：usage.prompt_cache_hit_tokens（命中）/ usage.prompt_cache_miss_tokens（冷写）。
优化要点：
- 保持 system + tools + briefing 在前且不变；多轮对话前缀自然复用，命中率极高。
- 动态工具调用：原生支持 OpenAI 兼容的 tool_choice（auto/none/required/指定函数），
  以及 Responses API 内置 web_search / web_extractor / code_interpreter（Harness 用本地
  等价实现，见 builtins.py）。
- 思考模式：开启后 reasoning_content 会随 tool_calls 返回，续轮必须原样回发（已在 base 处理）。
"""

from __future__ import annotations

from .base import BaseProvider, Usage


class DeepSeekProvider(BaseProvider):
    def _parse_usage(self, u: dict, usage: Usage) -> None:
        hit = u.get("prompt_cache_hit_tokens", 0)
        miss = u.get("prompt_cache_miss_tokens", 0)
        usage.cache_read_tokens = hit
        usage.cache_write_tokens = miss
        if usage.prompt_tokens == 0:
            usage.prompt_tokens = hit + miss

    def _inject_extra(self, payload: dict) -> None:
        # V4.1-Flash-1017 思考模式（可选）。开启后模型在输出最终答案前会做多轮
        # 推理与工具调用；reasoning_content 需原样回传，base._parse 已处理。
        if self.config.deepseek_thinking:
            payload.setdefault("extra_body", {})
            payload["extra_body"]["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = self.config.reasoning_effort
