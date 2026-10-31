"""浏览器控制：基于 Playwright 的网页自动化（操控 Web 软件 / 抓取动态页面）。

提供：导航、点击、填充表单、截图、执行 JS、提取文本。
无 Playwright 时返回安装指引；动态页面渲染依赖它，静态抓取可用 http_get 兜底。
"""

from __future__ import annotations

import shutil
from typing import Optional

from .tools import tool

__all__ = ["get_browser", "has_playwright"]


def has_playwright() -> bool:
    return shutil.which("playwright") is not None or _playwright_importable()


def _playwright_importable() -> bool:
    try:
        import playwright  # type: ignore
        return True
    except Exception:
        return False


class BrowserController:
    def __init__(self):
        self._pw = None
        self._browser = None
        self._page = None

    def _ensure(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception:
            raise RuntimeError(
                "ERROR: 需要 Playwright。安装： pip install playwright && playwright install chromium"
            )
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_page()
        return self._page

    def navigate(self, url: str) -> str:
        try:
            p = self._ensure()
            p.goto(url, timeout=30000)
            return f"navigated -> {url} | title={p.title}"
        except Exception as e:  # noqa
            return f"ERROR: {e}"

    def click(self, selector: str) -> str:
        try:
            p = self._ensure()
            p.click(selector, timeout=10000)
            return f"clicked {selector}"
        except Exception as e:  # noqa
            return f"ERROR: {e}"

    def fill(self, selector: str, value: str) -> str:
        try:
            p = self._ensure()
            p.fill(selector, value)
            return f"filled {selector} = {value!r}"
        except Exception as e:  # noqa
            return f"ERROR: {e}"

    def screenshot(self, path: str = "web.png") -> str:
        try:
            p = self._ensure()
            p.screenshot(path=path)
            return f"saved -> {path}"
        except Exception as e:  # noqa
            return f"ERROR: {e}"

    def evaluate(self, js: str) -> str:
        try:
            p = self._ensure()
            out = p.evaluate(js)
            txt = str(out)
            if len(txt) > 4000:
                txt = txt[:4000] + "\n... (truncated)"
            return txt
        except Exception as e:  # noqa
            return f"ERROR: {e}"

    def extract_text(self, url: str) -> str:
        try:
            p = self._ensure()
            p.goto(url, timeout=30000)
            txt = p.inner_text("body")
            if len(txt) > 6000:
                txt = txt[:6000] + "\n... (truncated)"
            return txt
        except Exception as e:  # noqa
            return f"ERROR: {e}"

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass


_controller: Optional[BrowserController] = None


def get_browser() -> BrowserController:
    global _controller
    if _controller is None:
        _controller = BrowserController()
    return _controller


@tool(
    name="browser_navigate",
    description="用真实浏览器打开一个 URL（渲染 JS 动态页面）。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标网址"},
            "extract": {"type": "boolean", "description": "是否同时抽取正文文本"},
        },
        "required": ["url"],
    },
    cost="expensive",
)
async def browser_navigate(url: str, extract: bool = False) -> str:
    b = get_browser()
    r = b.navigate(url)
    if extract and not r.startswith("ERROR"):
        return r + "\n---TEXT---\n" + b.extract_text(url)
    return r


@tool(
    name="browser_click",
    description="点击页面上的元素（CSS 选择器）。",
    parameters={
        "type": "object",
        "properties": {"selector": {"type": "string", "description": "CSS 选择器"}},
        "required": ["selector"],
    },
    cost="cheap",
)
async def browser_click(selector: str) -> str:
    return get_browser().click(selector)


@tool(
    name="browser_fill",
    description="向页面输入框填充文本。",
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string"},
            "value": {"type": "string"},
        },
        "required": ["selector", "value"],
    },
    cost="cheap",
)
async def browser_fill(selector: str, value: str) -> str:
    return get_browser().fill(selector, value)


@tool(
    name="browser_screenshot",
    description="对当前网页截图保存。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "路径，默认 web.png"}},
        "required": [],
    },
    cost="cheap",
)
async def browser_screenshot(path: str = "web.png") -> str:
    return get_browser().screenshot(path)


@tool(
    name="browser_evaluate",
    description="在页面上下文执行 JavaScript 并返回结果（高级抓取/操控）。",
    parameters={
        "type": "object",
        "properties": {"js": {"type": "string", "description": "JS 表达式"}},
        "required": ["js"],
    },
    cost="expensive",
)
async def browser_evaluate(js: str) -> str:
    return get_browser().evaluate(js)
