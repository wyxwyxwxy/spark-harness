"""Spark Harness Web 服务：把 Harness 暴露为本地 Web 应用。

- GET  /                 仪表盘（界面由各技能 + 插件面板动态生成）
- POST /api/chat          {"message": "..."} -> {"reply": "..."}
- GET  /api/skills        列出已加载技能
- GET  /api/tools         列出可用工具
- GET  /api/plugins       列出已加载插件（万物皆可插件）
- POST /api/plugins/reload 热重载插件（不重启进程，改动即时生效）
- GET  /api/health        健康检查

运行： python -m spark_harness serve --port 8080 [--plugin-watch]
  --plugin-watch：监视插件目录，文件变化自动热重载（开发插件时推荐）。
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import List, Optional

from .app import build_agent
from .skill import SkillManager
from .ui import generate_dashboard_html


def _plugin_dirs_fingerprint(dirs: Optional[List[str]]) -> dict:
    """插件目录指纹：每个 plugin.yaml / plugin.py 的 mtime，用于检测变化。"""
    fp: dict = {}
    for base in dirs or []:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f in ("plugin.yaml", "plugin.py"):
                    p = os.path.join(root, f)
                    try:
                        fp[p] = os.path.getmtime(p)
                    except OSError:
                        pass
    return fp


def create_app(config, skills_dir: Optional[str] = None, plugin_dirs: Optional[List[str]] = None,
               plugin_watch: bool = False):
    try:
        from flask import Flask, jsonify, request
    except ImportError:
        raise RuntimeError("需要 Flask：pip install flask")

    if plugin_dirs is None:
        plugin_dirs = [os.path.join(os.path.dirname(__file__), "plugins")]

    app = Flask(__name__)

    # —— 可重建的应用状态（热重载即整体重建，路由一律读 state）——
    state: dict = {}
    state_lock = threading.Lock()

    def _build_state() -> None:
        skills = SkillManager(skills_dir)
        agent, registry, memory, skills, pm = build_agent(
            config, skills, restrict_to_skills=True, plugin_dirs=plugin_dirs
        )
        with state_lock:
            state.update(
                agent=agent, registry=registry, memory=memory, skills=skills, pm=pm,
                ui_panels=pm.ui_panels if pm else [],
            )

    _build_state()

    @app.get("/")
    def index():
        return generate_dashboard_html(
            state["skills"].list(), state["registry"],
            title="Spark Harness", ui_panels=state["ui_panels"],
        )

    @app.post("/api/chat")
    def chat():
        data = request.get_json(force=True, silent=True) or {}
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message 为空"}), 400
        agent = state["agent"]  # 热重载后取新实例；重载前的旧实例可自然完成
        reply = asyncio.run(agent.run(message))  # agent.run 为异步，需事件循环驱动
        return jsonify({"reply": reply})

    @app.get("/api/skills")
    def list_skills():
        return jsonify([s.to_dict() for s in state["skills"].list()])

    @app.get("/api/tools")
    def list_tools():
        return jsonify(
            [
                {"name": t.name, "description": t.description}
                for t in state["registry"]._tools.values()
            ]
        )

    @app.get("/api/plugins")
    def list_plugins():
        pm = state["pm"]
        return jsonify([p.to_dict() for p in (pm.plugins if pm else [])])

    @app.post("/api/plugins/reload")
    def reload_plugins():
        _build_state()
        pm = state["pm"]
        return jsonify({
            "ok": True,
            "reloaded": True,
            "plugins": [p.to_dict() for p in (pm.plugins if pm else [])],
            "tools": len(state["registry"].names()),
            "skills": len(state["skills"].list()),
        })

    @app.get("/api/health")
    def health():
        pm = state["pm"]
        return jsonify({
            "ok": True,
            "provider": config.provider,
            "tools": len(state["registry"].names()),
            "skills": len(state["skills"].list()),
            "plugins": len(pm.plugins if pm else []),
        })

    # —— 插件目录监视：文件变化自动热重载（依赖-free，mtime 轮询）——
    if plugin_watch:
        def _watch_loop():
            last_fp = _plugin_dirs_fingerprint(plugin_dirs)
            while True:
                time.sleep(2.0)
                try:
                    fp = _plugin_dirs_fingerprint(plugin_dirs)
                    if fp != last_fp:
                        last_fp = fp
                        print("[plugin-watch] 检测到插件变化，热重载…")
                        _build_state()
                        pm = state["pm"]
                        print(f"[plugin-watch] 热重载完成：{len(pm.plugins)} 个插件，"
                              f"{len(state['registry'].names())} 个工具")
                except Exception as e:  # noqa
                    print(f"[plugin-watch] 热重载出错: {e}")

        threading.Thread(target=_watch_loop, daemon=True).start()

    return app


def serve(config, skills_dir: Optional[str] = None, plugin_dirs: Optional[List[str]] = None,
          port: int = 8080, host: str = "127.0.0.1", plugin_watch: bool = False):
    app = create_app(config, skills_dir, plugin_dirs, plugin_watch=plugin_watch)
    print(f"Spark Harness 已在 http://{host}:{port} 启动（技能 + 插件驱动界面）")
    if plugin_watch:
        print("插件监视已开启：修改插件目录将自动热重载")
    app.run(host=host, port=port, threaded=True)
