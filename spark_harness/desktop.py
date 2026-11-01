"""Spark Harness 桌面端 GUI。

把已有的 Flask 仪表盘（由技能 + 插件动态生成）嵌入一个**原生桌面窗口**：
- Windows  : WebView2（Edge 内核）
- macOS    : WKWebView（WebKit）
- Linux    : WebKit2GTK（或 Qt 后端）

这样技能、插件、动态工具、热重载等全部**无需重写**即可在桌面端运行。
若未安装 pywebview，则自动回退为「启动本地服务 + 打开系统浏览器」。

运行：
  python -m spark_harness desktop [--port 8080] [--plugin-watch]
"""

from __future__ import annotations

import threading
import time
from typing import List, Optional

import requests

from .config import load_from_env_and_args
from .server import create_app


def start_server(
    config,
    skills_dir: Optional[str] = None,
    plugin_dirs: Optional[List[str]] = None,
    port: int = 8080,
    host: str = "127.0.0.1",
    plugin_watch: bool = False,
):
    """在后台线程启动 Flask 服务，返回 app 与线程。"""
    app = create_app(config, skills_dir, plugin_dirs, plugin_watch=plugin_watch)
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, threaded=True),
        daemon=True,
    )
    t.start()
    return app, t


def wait_for_server(port: int, host: str = "127.0.0.1", timeout: float = 20.0) -> bool:
    """轮询健康检查，直到服务可用或超时。"""
    url = f"http://{host}:{port}/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def _make_menu(port: int, host: str = "127.0.0.1"):
    """桌面原生菜单：热重载插件 / 在浏览器中打开 / 退出。"""
    def reload_plugins(window):
        try:
            requests.post(f"http://{host}:{port}/api/plugins/reload", timeout=5)
            if window is not None:
                window.evaluate_js("alert('插件已热重载 ✅')")
        except Exception as e:  # noqa
            if window is not None:
                window.evaluate_js(f"alert('热重载失败: {e}')")

    def open_browser(_window):
        import webbrowser
        webbrowser.open(f"http://{host}:{port}/")

    return [
        {"title": "🔌 热重载插件", "func": reload_plugins},
        {"title": "🌐 在浏览器中打开", "func": open_browser},
    ]


def run_desktop(
    config,
    skills_dir: Optional[str] = None,
    plugin_dirs: Optional[List[str]] = None,
    port: int = 8080,
    host: str = "127.0.0.1",
    plugin_watch: bool = False,
    skip_window: bool = False,
):
    """启动桌面端：先起服务，再开窗口（或回退到浏览器）。

    skip_window=True 时只起服务并返回 URL（用于测试 / 无界面环境）。
    """
    app, _t = start_server(config, skills_dir, plugin_dirs, port, host, plugin_watch)
    if not wait_for_server(port, host):
        raise RuntimeError("Flask 服务启动失败，请检查端口是否被占用。")
    url = f"http://{host}:{port}/"
    print(f"Spark Harness 桌面端服务就绪：{url}")

    if skip_window:
        return url

    try:
        import webview  # noqa
    except ImportError:
        print("[warn] 未安装 pywebview，回退为「打开系统浏览器」模式（pip install pywebview）。")
        import webbrowser
        webbrowser.open(url)
        try:
            input("服务已在浏览器打开，按回车退出…")
        except (EOFError, KeyboardInterrupt):
            pass
        return url

    window = webview.create_window(
        "⚡ Spark Harness",
        url,
        width=1320,
        height=840,
        min_size=(900, 600),
        resizable=True,
        text_select=True,
        confirm_close=False,
        menu=_make_menu(port, host),
    )
    # webview.start 阻塞到最后一个窗口关闭；之后退出进程（Flask 为守护线程）
    webview.start()
    return url


def main(argv=None) -> int:
    """供 PyInstaller / 独立入口调用：直接以桌面模式启动。"""
    cfg = load_from_env_and_args({"tool_choice": "auto"})
    run_desktop(cfg, port=8080)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
