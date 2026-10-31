"""上下文/结果压缩管线（论文背书：Selective Context、LLMLingua、RECOMP、MemGPT）。

设计原则（把每个 token 用到极致）：
1. 外部化：大内容不进活上下文，存进 MemoryStore，上下文只留摘要 + 检索索引。
2. 抽取式压缩（RECOMP-extractive）：按信息量挑句子，去掉冗余/模板/日志。
3. 头尾保留（结构保护）：长输出保留开头与结尾，中段省略，避免丢关键结论。
4. 词级轻压（Selective Context 思路）：折叠空白、去重行、剥 ANSI/注释噪音。
"""

from __future__ import annotations

import re

# 轻量英文停用词（仅用于"可预测/低信息"判定；中文靠字符密度与去重）
_STOP = set(
    "the a an and or of to in is are was were be been being for on with as by at "
    "this that these those it its from we you they he she will can may should would "
    "do does did have has had not no yes i my your our their if then else so but because "
    "about into out up down over under between within which who what when where why how".split()
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_DUP_LINE = re.compile(r"(?m)^\s*(.{4,})\s*$\n(?=\s*\1\s*$)")  # 连续重复行


def lexical_cleanup(text: str) -> str:
    """轻量词级/行级清理：剥 ANSI、折叠空白、去连续重复行、去纯符号噪音行。"""
    if not text:
        return ""
    text = _ANSI.sub("", text)
    text = text.replace("\r\n", "\n")
    # 去掉连续重复的整行（如进度条、重复日志）
    text = _DUP_LINE.sub(r"\1\n", text)
    # 折叠多余空白（保留换行结构）
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def head_tail(text: str, head_chars: int = 700, tail_chars: int = 500) -> str:
    """结构保护截断：保留开头 head 与结尾 tail，中段用省略标记。"""
    text = lexical_cleanup(text)
    if len(text) <= head_chars + tail_chars + 40:
        return text
    head = text[:head_chars].rstrip()
    tail = text[-tail_chars:].lstrip()
    return f"{head}\n…〔中段已省略 {len(text)-head_chars-tail_chars} 字符〕…\n{tail}"


def _split_sentences(text: str) -> list[str]:
    # 以中英文句号/换行/分号切句，保留结构
    parts = re.split(r"(?<=[。.!?！？；;\n])\s*", text)
    return [p.strip() for p in parts if p and p.strip()]


def extractive_compress(text: str, keep_ratio: float = 0.5, query: str = "") -> str:
    """抽取式压缩（RECOMP-extractive 思路）：按信息量给句子打分，保留高信息量部分。

    打分维度：含数字、专有/大写词、长度适中、与 query 词汇重叠、非停用词密度。
    """
    text = lexical_cleanup(text)
    sents = _split_sentences(text)
    if len(sents) <= 2:
        return head_tail(text)
    q_tokens = set(_tokenize(query))
    scored = []
    for s in sents:
        score = 0.0
        toks = _tokenize(s)
        if not toks:
            continue
        # 数字 = 高信息
        if re.search(r"\d", s):
            score += 2.0
        # 大写/专有名词
        if re.search(r"[A-Z][a-z]{2,}|[一-龥]", s):
            score += 1.0
        # 长度甜区
        L = len(s)
        if 20 <= L <= 200:
            score += 1.0
        elif L < 8:
            score -= 1.0
        # 与查询重叠
        if q_tokens:
            overlap = len(q_tokens & set(toks)) / max(1, len(q_tokens))
            score += 3.0 * overlap
        # 非停用词密度
        content = [t for t in toks if t not in _STOP and len(t) > 1]
        score += min(1.0, len(content) / max(1, len(toks)))
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    keep_n = max(1, int(len(scored) * keep_ratio))
    kept = [s for _, s in scored[:keep_n]]
    # 还原原始顺序，保证可读性
    kept_set = set(kept)
    ordered = [s for s in sents if s in kept_set]
    out = "\n".join(ordered)
    return head_tail(out) if len(out) > 1200 else out


def compress_tool_result(text: str, hard_cap: int = 700, query: str = "") -> str:
    """工具结果压缩主入口：小则头尾保留，大则抽取式压缩（外部化由调用方完成）。"""
    if not text:
        return ""
    if len(text) <= hard_cap:
        return head_tail(text, head_chars=hard_cap, tail_chars=max(80, hard_cap // 2))
    return extractive_compress(text, keep_ratio=0.45, query=query)


def _tokenize(s: str) -> list[str]:
    # 轻量分词：英文按单词，中文按字；统一小写，去标点
    s = s.lower()
    toks = re.findall(r"[a-z0-9]+|[一-龥]", s)
    return toks
