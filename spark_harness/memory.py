"""外部记忆库（论文背书：MemGPT/Letta、Mem0、Zep、RECOMP）。

核心思想：活上下文只放"索引 + 检索到的相关片段"，完整内容外置。
这样无论任务多长、文件多大，活上下文都保持很小——这是把上下文砍掉 95% 的主杠杆。

检索用轻量 BM25（无需向量模型即可离线运行）；若环境有 sentence-transformers 可切换语义检索。
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .tokenizer import count_text


@dataclass
class MemoryItem:
    id: str
    kind: str          # file / tool / summary / note
    summary: str       # 简短摘要（进上下文索引）
    full: str          # 完整内容（外置，按需检索）
    tokens: int = 0
    created: float = 0.0
    refs: int = 0      # 被检索命中的次数


# 当前激活的记忆库（供 recall 等工具访问，由 Agent 初始化时绑定）
_MEMORY_STORE: Optional["MemoryStore"] = None


def set_active_store(store: Optional["MemoryStore"]) -> None:
    global _MEMORY_STORE
    _MEMORY_STORE = store


class MemoryStore:
    def __init__(self, persist_path: Optional[str] = None):
        self._items: dict[str, MemoryItem] = {}
        self._df: dict[str, int] = {}      # 词 -> 出现文档数（BM25 的 IDF）
        self._seq = 0
        self.persist_path = persist_path
        if persist_path and os.path.exists(persist_path):
            self._load()

    # —— 写入 ——
    def add(self, kind: str, summary: str, full: str) -> str:
        self._seq += 1
        mid = f"m{self._seq}"
        item = MemoryItem(
            id=mid, kind=kind, summary=summary[:300],
            full=full, tokens=count_text(full), created=time.time(),
        )
        self._items[mid] = item
        for t in set(self._terms(full)):  # 文档频率：每文档每个词只计一次
            self._df[t] = self._df.get(t, 0) + 1
        if self.persist_path:
            self._save()
        return mid

    # —— 检索 ——
    def retrieve(self, query: str, k: int = 4, max_tokens: int = 1500) -> str:
        """返回与 query 最相关的若干条摘要 + 小段摘录，按 token 预算截断。"""
        if not self._items:
            return ""
        scores = self._bm25_scores(query)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
        out_lines = []
        used = 0
        for mid, sc in ranked:
            if sc <= 0:
                break
            item = self._items[mid]
            item.refs += 1
            excerpt = item.full[:600]
            block = f"[{mid}|{item.kind}|score={sc:.1f}] {item.summary}\n  excerpt: {excerpt}"
            t = count_text(block)
            if used + t > max_tokens:
                # 截断到预算
                room = max(0, max_tokens - used)
                block = block[: room * 2]
            out_lines.append(block)
            used += count_text(block)
            if used >= max_tokens:
                break
        return "\n".join(out_lines)

    def _bm25_scores(self, query: str) -> dict:
        q_terms = self._terms(query)
        if not q_terms:
            return {}
        N = len(self._items)
        scores = {}
        for mid, item in self._items.items():
            doc_terms = self._terms(item.full)
            if not doc_terms:
                continue
            tf = {}
            for t in doc_terms:
                tf[t] = tf.get(t, 0) + 1
            dl = max(1, len(doc_terms))
            score = 0.0
            for t in set(q_terms):
                if t not in tf:
                    continue
                idf = math.log((N - self._df.get(t, 0) + 0.5) / (self._df.get(t, 0) + 0.5) + 1)
                # BM25 简化：去掉长文档惩罚的常数，保留核心项
                score += idf * (tf[t] * (1.2 + 1)) / (tf[t] + 1.2)
            scores[mid] = score
        return scores

    @staticmethod
    def _terms(text: str) -> List[str]:
        text = text.lower()
        return re.findall(r"[a-z0-9]{2,}|[一-龥]", text)

    # —— 上下文索引块（极小）——
    def index_block(self) -> str:
        if not self._items:
            return "（记忆库为空）"
        lines = [f"- [{m.id}|{m.kind}] {m.summary}" for m in self._items.values()]
        return "记忆索引:\n" + "\n".join(lines)

    def stats(self) -> str:
        total = sum(i.tokens for i in self._items.values())
        return (
            f"记忆条目 {len(self._items)} | 外置全量 {total} tok | "
            f"被检索命中 {sum(i.refs for i in self._items.values())} 次"
        )

    # —— 持久化（Mem0 式跨轮/跨会话保留）——
    def _save(self) -> None:
        try:
            with open(self.persist_path, "w", encoding="utf-8") as f:
                json.dump(
                    {k: vars(v) for k, v in self._items.items()},
                    f, ensure_ascii=False,
                )
        except Exception:
            pass

    def _load(self) -> None:
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                self._items[k] = MemoryItem(**v)
            self._seq = max((int(m[1:]) for m in self._items), default=0)
        except Exception:
            pass
