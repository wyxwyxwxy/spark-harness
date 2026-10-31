"""上下文管理器 v2：最大化上下文、最小化 token、保住缓存前缀、外部化大内容。

缓存前缀布局（从前往后，越靠前越该稳定）：
  [0] system = 系统提示 + 压缩工具索引        ← 稳定锚点（最大块，始终命中缓存）
  [1] user   = 环境简报                      ← 稳定锚点
  [2] user   = 记忆索引（随外置条目增长，可能变化，但体积很小）
  [3] user   = 历史摘要（递归压缩，95% 阈值触发）
  [4..]      对话历史（append-only）
  [尾] user   = 检索到的相关记忆（transient，每次按当前查询检索，不落库）

要点：完整文件/工具输出全部外置到 MemoryStore，上下文只保留摘要 + 检索片段 + 索引，
因此无论任务多长，活上下文都极小——这是把上下文砍掉 95% 的主杠杆。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from .tokenizer import count_messages

if TYPE_CHECKING:
    from .config import HarnessConfig
    from .memory import MemoryStore
    from .providers.base import BaseProvider
    from .tools import ToolRegistry


class ContextManager:
    def __init__(self, config: "HarnessConfig", memory: "MemoryStore", registry: "ToolRegistry"):
        self.config = config
        self.memory = memory
        self.registry = registry
        # 稳定锚点：系统提示 + 一句话工具索引（工具集不变 → 索引稳定 → 命中缓存）
        self.system: str = config.system_prompt + "\n\n" + registry.compact_index()
        self.env_briefing: str = (
            "【环境】本地 shell / 文件系统可用；手机经 ADB；3D 经 make_3d_model。"
            "完整内容已外置到记忆库，上下文只保留摘要与按需检索片段。"
        )
        self.summary: Optional[str] = None
        self.history: List[dict] = []
        self.last_query: str = ""

    # —— 构建最终 messages ——
    def build_messages(self) -> List[dict]:
        msgs: List[dict] = []
        msgs.append({"role": "system", "content": self.system})
        msgs.append({"role": "user", "content": self.env_briefing})
        msgs.append(
            {"role": "user", "content": "【记忆索引】\n" + self.memory.index_block()}
        )
        if self.summary:
            msgs.append({"role": "user", "content": self.summary})
        msgs.extend(self.history)
        # transient 检索块：按当前查询取最相关记忆，不写入 history（避免累积膨胀）
        retrieved = self.memory.retrieve(self.last_query, k=4, max_tokens=1500)
        if retrieved:
            msgs.append({"role": "user", "content": "【检索到的相关记忆】\n" + retrieved})
        return msgs

    # —— 写入接口 ——
    def set_last_query(self, q: str) -> None:
        self.last_query = q

    def add_turn(self, message: dict) -> None:
        self.history.append(message)

    def total_tokens(self) -> int:
        return count_messages(self.build_messages())

    def used_ratio(self) -> float:
        return self.total_tokens() / max(1, self.config.max_input_tokens)

    # —— 预算守护 + 95% 阈值递归压缩 ——
    async def maybe_compact(self, provider: "BaseProvider") -> bool:
        """逼近预算（默认 95%）且历史够长时，把最旧历史递归摘要化并并入 summary。

        外置内容仍在记忆库，压缩只丢掉活上下文里的冗余复述，几乎不损信息。
        """
        if self.used_ratio() < self.config.compact_threshold:
            return False
        if len(self.history) <= self.config.keep_recent_turns + 2:
            return False

        keep = self.config.keep_recent_turns
        old = self.history[: len(self.history) - keep]
        recent = self.history[len(self.history) - keep:]
        if not old:
            return False

        compact_text = "\n".join(_render_turn(m) for m in old)
        prompt = (
            "请把下面较早的对话历史压缩为极简结构化摘要（保留：已完成操作、关键结论、"
            "重要文件路径/数值/决策、记忆库条目 id），去掉冗余复述。中文，≤300 字：\n\n"
            + compact_text
        )
        try:
            content, _, _ = await provider.complete(
                messages=[{"role": "user", "content": prompt}], tools=None
            )
        except Exception:
            content = f"（压缩失败，保留摘要）{compact_text[:600]}"

        header = "【早期对话递归摘要】\n" if not self.summary else ""
        self.summary = (self.summary or "") + header + (content or "")
        self.history = recent
        return True


def _render_turn(m: dict) -> str:
    role = m.get("role", "user")
    if role == "tool":
        c = str(m.get("content", ""))
        return f"[工具 {m.get('tool_call_id','')}]: {c[:400]}"
    if role == "assistant":
        tcs = m.get("tool_calls")
        if tcs:
            names = ", ".join(tc.get("function", {}).get("name", "") for tc in tcs)
            return f"[助手] 调用: {names}"
        return f"[助手]: {str(m.get('content',''))[:400]}"
    return f"[{role}]: {str(m.get('content',''))[:400]}"
