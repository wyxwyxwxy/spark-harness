"""示例 Provider 插件：演示"万物皆可插件"的第五类扩展点——自定义 LLM 厂商。

把本目录放入 plugins/ 后，设置 SPARK_PROVIDER=echo 即可选用插件贡献的 EchoProvider，
无需任何 API Key 就能验证"插件可扩展模型厂商"这条接线是否打通。

真实场景下，你应当在这里注册任意 OpenAI 兼容 / 私有模型的 Provider 子类：
  - 覆盖 _inject_cache / _inject_extra / _parse_usage 注入厂商特有参数与缓存统计；
  - 或像本例一样直接重写 complete()，对接你自己的推理后端。
"""

from spark_harness.providers.base import BaseProvider, Usage


class EchoProvider(BaseProvider):
    """示例 Provider：不发起真实网络请求，确定性回显，专用于验证插件→Provider 接线。

    真实插件应改为调用你的模型后端（可复用基类 _post / _parse，只覆盖差异部分）。
    """

    async def complete(self, messages, tools=None, tool_choice="auto"):
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        content = f"[echo-provider] 已收到指令：{last_user[:100]}"
        usage = Usage(prompt_tokens=12, completion_tokens=6)
        self.stats.add(prompt=12, completion=6)
        return content, [], usage


def setup(api):
    # 注册一个自定义厂商：之后 SPARK_PROVIDER=echo 即可选用
    api.register_provider("echo", EchoProvider)
