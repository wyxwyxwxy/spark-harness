"""离线、CJK 友好的 token 估算。

设计目标：
- 不依赖联网或额外模型权重，开箱即用。
- 中文按字近似 1 token，西文按 4 字符 1 token，符合主流中英混合场景的经验比例。
- 若环境中装了 tiktoken，则对纯西文更准；否则退回启发式。

缓存命中率优化极度依赖"token 数估算稳定且可复现"——估算本身不要求精确，
但要求同一段文本在不同请求里给出完全一致的计数，否则预算判断会抖动。
"""

from __future__ import annotations

import re

# CJK 统一表意文字 + 兼容/全角区
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")

_TK = None  # 懒加载 tiktoken


def _get_tiktoken():
    global _TK
    if _TK is not None:
        return _TK
    try:
        import tiktoken  # type: ignore

        _TK = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _TK = False
    return _TK


def count_text(text: str) -> int:
    """估算一段文本的 token 数。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    non_cjk = len(text) - cjk
    est = cjk + (non_cjk + 3) // 4  # 西文每 4 字符约 1 token
    tk = _get_tiktoken()
    if tk is not False and non_cjk > 0 and cjk == 0:
        # 纯西文优先用 tiktoken，更准确
        try:
            return len(tk.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, est)


# 单条消息的固定开销（role 标记、结构等），OpenAI 风格约 4 token/条
_MSG_OVERHEAD = 4
# 每个 tool_call 的额外开销（id、name、结构）
_TOOL_CALL_OVERHEAD = 12


def count_message_tokens(message: dict) -> int:
    """估算单条消息的 token 数（含结构开销）。"""
    n = _MSG_OVERHEAD
    content = message.get("content")
    if isinstance(content, str):
        n += count_text(content)
    elif isinstance(content, list):  # 多模态 content parts
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                n += count_text(part.get("text", ""))
            else:
                n += 8  # 非文本 part 粗略计
    for tc in message.get("tool_calls", []) or []:
        n += _TOOL_CALL_OVERHEAD
        fn = tc.get("function", {})
        n += count_text(fn.get("name", ""))
        n += count_text(fn.get("arguments", "") or "")
    if message.get("tool_call_id"):
        n += count_text(message["tool_call_id"])
    return n


def count_messages(messages: list[dict]) -> int:
    return sum(count_message_tokens(m) for m in messages)
