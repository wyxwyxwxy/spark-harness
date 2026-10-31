"""本地版"动态内置工具"：对应 DeepSeek V4.1-Flash-1017 在 Responses API 原生提供的
web_search / web_extractor / code_interpreter。

之所以本地实现：保证无论接哪个 OpenAI 兼容端点（DeepSeek / GLM / Kimi / 自建），
Harness 都拥有同等的"联网搜索 / 网页抽取 / 代码解释器"能力；同时如果你用 DeepSeek
Responses API，也可以直接改用其托管版（无需本地执行）。
"""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
from typing import List

from .tools import tool

__all__ = ["BUILTIN_TOOLS"]


@tool(
    name="web_search",
    description="联网搜索（无需 API Key，走 DuckDuckGo HTML）。返回前若干条结果的标题与链接。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "top_k": {"type": "integer", "description": "返回条数，默认 5"},
        },
        "required": ["query"],
    },
    cost="expensive",
)
async def web_search(query: str, top_k: int = 5) -> str:
    try:
        import requests
        from bs4 import BeautifulSoup
    except Exception as e:  # noqa
        return f"ERROR: 需要 requests + beautifulsoup4：pip install requests beautifulsoup4 ({e})"
    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        soup = BeautifulSoup(r.text, "html.parser")
        results: List[str] = []
        for a in soup.select(".result__a")[:top_k]:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            results.append(f"- {title}\n  {href}")
        return "\n".join(results) or "(无结果)"
    except Exception as e:  # noqa
        return f"ERROR: 搜索失败 {e}"


@tool(
    name="web_extractor",
    description="抓取一个网页并抽取可读正文文本（截断到 6000 字符）。",
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "目标 URL"}},
        "required": ["url"],
    },
    cost="expensive",
)
async def web_extractor(url: str) -> str:
    try:
        import requests
        from bs4 import BeautifulSoup
    except Exception as e:  # noqa
        return f"ERROR: 需要 requests + beautifulsoup4：pip install requests beautifulsoup4 ({e})"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        if len(text) > 6000:
            text = text[:6000] + "\n... (truncated)"
        return text or "(空页面)"
    except Exception as e:  # noqa
        return f"ERROR: 抓取失败 {e}"


@tool(
    name="code_interpreter",
    description="在沙箱里执行 Python 代码并返回 stdout/stderr（超时 25s）。用于数据分析、计算、生成文件。",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python 代码"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 25"},
        },
        "required": ["code"],
    },
    cost="expensive",
)
async def code_interpreter(code: str, timeout: int = 25) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name
    try:
        proc = subprocess.run(
            ["python3.11", path], capture_output=True, text=True, timeout=timeout
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if len(out) > 4000:
            out = out[:4000] + "\n... (truncated)"
        return out or "(无输出)"
    except subprocess.TimeoutExpired:
        return f"ERROR: 代码执行超过 {timeout}s"
    except Exception as e:  # noqa
        return f"ERROR: {e}"
    finally:
        try:
            import os
            os.unlink(path)
        except Exception:
            pass


# 内置工具名 -> 模块内函数（供 registry 动态挂载）
BUILTIN_TOOLS = ("web_search", "web_extractor", "code_interpreter")
