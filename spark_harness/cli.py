"""Spark Harness 命令行入口。

子命令：
  demo      脚本化演示（无需 Key，Mock 厂商）
  bench     实测前缀缓存命中率 + 工具结果缓存去重
  compress  对照基准：上下文/Token 压缩（≥95%/≥85%）
  tools     列出已注册工具
  skills    列出已加载技能（界面由技能组成）
  run       执行单个任务（--task）
  chat      交互式对话
  serve     启动 Web 服务（技能驱动界面）
  desktop   启动桌面端 GUI（原生窗口嵌入仪表盘）

示例：
  python -m spark_harness demo
  python -m spark_harness bench
  SPARK_PROVIDER=deepseek SPARK_API_KEY=sk-xxx python -m spark_harness run --task "生成球体模型" --tool-choice auto
  python -m spark_harness serve --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .app import build_agent
from .config import load_from_env_and_args
from .providers.mock import MockProvider
from .registry import build_default_registry
from .cache import ToolResultCache
from .memory import MemoryStore
from .plugin import PluginManager

import os as _os


def _default_plugin_dirs(extra=None):
    """内置 plugins/ 目录（含示例插件）+ 用户额外目录。"""
    base = [_os.path.join(_os.path.dirname(__file__), "plugins")]
    if extra:
        base.extend(extra)
    return base


def _make_agent(config, script=None, verbose=True, skills_dir=None, plugin_dirs=None):
    from .skill import SkillManager
    from .providers import get_provider

    skills = SkillManager(skills_dir) if skills_dir else None
    pdirs = plugin_dirs if plugin_dirs is not None else _default_plugin_dirs()
    agent, registry, memory, skills, _pm = build_agent(
        config, skills, restrict_to_skills=False, plugin_dirs=pdirs
    )
    if config.provider == "mock" and script is not None:
        # 替换 provider 为脚本化 Mock
        agent.provider = get_provider(config, script=script)
    return agent


def cmd_demo(args) -> int:
    script = [
        {
            "content": None,
            "tool_calls": [
                {"id": "call_1", "name": "make_3d_model",
                 "arguments": {"output_path": "/workspace/demo_cube.obj", "primitive": "cube", "size": 2.0, "prompt": "演示立方体"}},
                {"id": "call_2", "name": "list_dir", "arguments": {"path": "."}},
            ],
        },
        {"content": "已完成：生成了立方体 3D 模型 /workspace/demo_cube.obj，并列出当前目录。", "tool_calls": []},
    ]
    cfg = load_from_env_and_args({"provider": "mock", "compact_threshold": 5.0, "system_prompt": "你是 Spark Harness 演示智能体。"})
    agent = _make_agent(cfg, script=script, verbose=True)
    print(">>> 任务：生成一个立方体模型，并列出当前目录\n")
    answer = asyncio.run(agent.run("生成一个立方体模型，并列出当前目录", max_iterations=8))
    print("\n>>> 最终答复：", answer)
    return 0


def cmd_bench(args) -> int:
    cfg = load_from_env_and_args({"provider": "mock", "compact_threshold": 5.0})
    provider: MockProvider = MockProvider(cfg)
    registry = build_default_registry()
    tool_cache = ToolResultCache(ttl=600, enabled=True)

    base = [
        {"role": "system", "content": cfg.system_prompt},
        {"role": "user", "content": "【环境简报】可用工具: " + ", ".join(registry.names())},
    ]
    print("=== 前缀缓存命中率随轮次变化 ===")
    for i in range(8):
        messages = base + [{"role": "user", "content": f"第 {i+1} 轮任务：做点事"}]
        asyncio.run(provider.complete(messages, tools=registry.schema()))
        print(f"轮 {i+1}: 累计命中率 {provider.stats.hit_rate:.1%} | 读 {provider.stats.cache_read_tokens} / 写 {provider.stats.cache_write_tokens}")

    print("\n=== 工具结果缓存去重 ===")
    args_a = {"command": "echo spark-bench"}
    r1, c1 = asyncio.run(_call_tool(registry, tool_cache, "shell", args_a))
    r2, c2 = asyncio.run(_call_tool(registry, tool_cache, "shell", args_a))
    print(f"第一次执行: cached={c1} -> {r1.strip()}")
    print(f"相同调用第二次: cached={c2} -> 命中缓存")
    print(f"工具缓存命中率: {tool_cache.hit_rate:.0%}")
    return 0


async def _call_tool(registry, tool_cache, name, args):
    cached = tool_cache.get(name, args)
    if cached is not None:
        return cached, True
    res = await registry.call(name, args)
    tool_cache.put(name, args, res)
    return res, False


def cmd_compress(args) -> int:
    from .compression import compress_tool_result
    from .context import ContextManager
    from .memory import MemoryStore
    from .tokenizer import count_messages, count_text

    cfg = load_from_env_and_args({"provider": "mock", "compact_threshold": 5.0})
    registry = build_default_registry()

    def long_text(target_chars, label):
        base = "项目背景：本模块负责数据处理与异常监控，包含配置加载、批处理、重试与告警。在长上下文场景中信息密度不均，仅有少数句子携带关键决策与数值。"
        parts, total, i = [], 0, 0
        while total < target_chars:
            s = f"\n{label} 段落{i}：关键指标 {i*7}，状态正常，决策点见下文。{base}"
            parts.append(s); total += len(s); i += 1
        return "".join(parts)

    BIG_DOC = long_text(100000, "文档")
    OUT1 = long_text(10000, "命令输出")
    OUT2 = long_text(10000, "命令输出")

    steps = [
        ("user", "请阅读这份大文档并总结要点"), ("tool", BIG_DOC),
        ("assistant", "已读取，开始分析关键指标"),
        ("user", "第一步：运行预处理并提取数值"), ("tool", OUT1),
        ("assistant", "第一步完成"),
        ("user", "第二步：生成报告"), ("tool", OUT2),
        ("assistant", "第二步完成"), ("user", "给出最终结论"),
        ("assistant", "最终结论：……（任务完成）"),
    ]

    base_history: list = []
    base_schema_tok = count_text(json.dumps(registry.schema(), ensure_ascii=False))
    base_system = [{"role": "system", "content": cfg.system_prompt}]
    base_contexts, base_total = [], 0
    for kind, payload in steps:
        if kind == "tool":
            base_history.append({"role": "tool", "tool_call_id": f"t{len(base_history)}", "content": payload})
        else:
            base_history.append({"role": kind, "content": payload})
        tok = count_messages(base_system + base_history) + base_schema_tok
        base_contexts.append(tok); base_total += tok

    memory = MemoryStore()
    comp_ctx = ContextManager(cfg, memory, registry)
    comp_schema_tok = count_text(json.dumps(registry.minimal_schema(), ensure_ascii=False))
    comp_contexts, comp_total = [], 0
    last_query = ""
    for kind, payload in steps:
        if kind == "tool":
            mid = memory.add(kind="tool", summary=f"tool→{payload[:60]}", full=payload)
            disp = f"[记忆 {mid}] " + compress_tool_result(payload, hard_cap=700)[:120]
            comp_ctx.add_turn({"role": "tool", "tool_call_id": f"t{len(comp_ctx.history)}", "content": disp})
        else:
            comp_ctx.add_turn({"role": kind, "content": payload})
            if kind == "user":
                last_query = payload
        comp_ctx.set_last_query(last_query)
        tok = count_messages(comp_ctx.build_messages()) + comp_schema_tok
        comp_contexts.append(tok); comp_total += tok

    base_max, comp_max = max(base_contexts), max(comp_contexts)
    ctx_red, tok_red = 1 - comp_max / base_max, 1 - comp_total / base_total

    print("=== Spark Harness 上下文/Token 压缩对照基准 ===")
    print(f"大文档 {count_text(BIG_DOC)} tok + 两段日志各 {count_text(OUT1)} tok（外置）\n")
    print(f"{'轮次':<4}{'朴素上下文':>14}{'Spark v2':>14}")
    for i, (b, c) in enumerate(zip(base_contexts, comp_contexts), 1):
        print(f"{i:<4}{b:>14}{c:>14}")
    print("-" * 34)
    print(f"峰值上下文: 朴素 {base_max} tok | v2 {comp_max} tok | 省 {ctx_red:.1%}")
    print(f"累计输入token: 朴素 {base_total} | v2 {comp_total} | 减 {tok_red:.1%}")
    print(f"\n目标校验: 上下文省≥95% → {'达标' if ctx_red>=0.95 else '未达标'}; token减≥85% → {'达标' if tok_red>=0.85 else '未达标'}")
    return 0


def cmd_tools(args) -> int:
    registry = build_default_registry()
    print(f"已注册 {len(registry.names())} 个工具：")
    for spec in registry._tools.values():
        print(f"  - {spec.name}: {spec.description}")
    return 0


def cmd_skills(args) -> int:
    from .skill import SkillManager
    sm = SkillManager(args.skills_dir)
    print(f"已加载 {len(sm.list())} 个技能（界面由它们组成）：")
    for s in sm.list():
        print(f"  - {s.name} [{s.title}]: 工具 {', '.join(s.tools)}")
    return 0


def cmd_plugins(args) -> int:
    pdirs = _default_plugin_dirs(args.plugins_dir)
    pm = PluginManager(pdirs, verbose=False).load_all()
    print(pm.summary())
    return 0


def cmd_plugin_create(args) -> int:
    """脚手架：生成一个插件目录骨架（plugin.yaml + plugin.py）。"""
    name = args.name
    target = args.target or _os.path.join(_os.path.dirname(__file__), "plugins", name)
    if _os.path.exists(target):
        print(f"[err] 目录已存在: {target}")
        return 1
    _os.makedirs(target, exist_ok=True)
    yaml_path = _os.path.join(target, "plugin.yaml")
    py_path = _os.path.join(target, "plugin.py")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(
            f"name: {name}\n"
            f"version: 1.0.0\n"
            f"description: {args.desc or (name + ' 插件')}\n"
            f"author: you\n"
            f"entry: plugin.py\n"
            f"tags: [custom]\n"
        )
    with open(py_path, "w", encoding="utf-8") as f:
        f.write(
            '"""示例插件：万物皆可插件。\n\n'
            '在这里贡献 工具 / 技能 / Provider / UI 面板 / 钩子。\n'
            '运行: python -m spark_harness plugin create my-plugin\n'
            '      python -m spark_harness serve  (自动加载 plugins/ 下所有插件)\n'
            '"""\n\n'
            'from spark_harness.tools import tool\n\n\n'
            '@tool(\n'
            '    name="hello_plugin",\n'
            '    description="插件示例工具：回显一条问候。",\n'
            '    parameters={\n'
            '        "type": "object",\n'
            '        "properties": {"name": {"type": "string", "description": "名字"}},\n'
            '        "required": ["name"],\n'
            '    },\n'
            '    cost="cheap",\n'
            ')\n'
            'async def hello_plugin(name: str) -> str:\n'
            '    return f"你好, {name}! 这条回复来自一个插件。"\n\n\n'
            'def setup(api):\n'
            '    # 贡献一个技能（界面面板 + 能力边界）\n'
            '    api.add_skill({\n'
            '        "name": "my-plugin",\n'
            '        "title": "我的插件",\n'
            '        "description": "由插件提供的自定义能力。",\n'
            '        "tools": ["hello_plugin"],\n'
            '        "prompt": "当用户提到插件示例时使用 hello_plugin 工具。",\n'
            '        "ui": {"icon": "🔌", "kind": "custom"},\n'
            '    })\n'
            '    # 贡献一个 UI 面板（注入 Web 仪表盘）\n'
            '    api.add_ui_panel({\n'
            '        "id": "my-plugin-panel",\n'
            '        "title": "我的插件面板",\n'
            '        "html": (\n'
            '            \'<div class="card"><h3>🔌 我的插件</h3>\'\n'
            '            \'<p class="desc">点击“运行示例”让 Agent 调用插件工具（可改用 sparkSend 注入指令）。</p>\'\n'
            '            \'<button onclick="sparkSend(\' + chr(39) + \'用 hello_plugin 问候一下 Spark\' + chr(39) + \')">运行示例</button></div>\'\n'
            '        ),\n'
            '    })\n'
            '    # 贡献一个钩子（生命周期扩展点）\n'
            '    api.register_hook("post_tool_call",\n'
            '                     lambda n, a, r, e: print(f"[plugin hook] {n} 执行完毕"))\n'
        )
    print(f"[ok] 已生成插件骨架: {target}")
    print(f"     运行 `python -m spark_harness plugins` 查看，或 `serve` 自动加载。")
    return 0


def cmd_run(args) -> int:
    cfg = load_from_env_and_args({"tool_choice": args.tool_choice})
    # 仅对内置、需要 Key 的厂商做兜底；插件贡献的厂商（如 echo）无需 Key，不应被覆盖
    if cfg.provider in ("deepseek", "glm", "kimi") and not cfg.api_key:
        print(f"[warn] 未检测到 {cfg.provider} 的 API Key，退回 Mock 演示。")
        cfg.provider = "mock"; cfg.resolve()
    agent = _make_agent(cfg, verbose=True, skills_dir=args.skills_dir, plugin_dirs=args.plugins_dir)
    answer = asyncio.run(agent.run(args.task, max_iterations=args.max_iter))
    print("\n>>> 最终答复：", answer)
    return 0


def cmd_chat(args) -> int:
    cfg = load_from_env_and_args({"tool_choice": args.tool_choice})
    if cfg.provider in ("deepseek", "glm", "kimi") and not cfg.api_key:
        print("[warn] 未检测到 API Key，使用 Mock 进行本地交互。")
        cfg.provider = "mock"; cfg.resolve()
    agent = _make_agent(cfg, verbose=False, skills_dir=args.skills_dir, plugin_dirs=args.plugins_dir)
    print("Spark Harness 交互模式（输入 exit 退出）")
    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text or text.lower() in ("exit", "quit"):
            break
        answer = asyncio.run(agent.run(text, max_iterations=args.max_iter))
        print("AI>", answer)
    return 0


def cmd_desktop(args) -> int:
    from .desktop import run_desktop
    cfg = load_from_env_and_args({"tool_choice": args.tool_choice})
    if cfg.provider in ("deepseek", "glm", "kimi") and not cfg.api_key:
        print(f"[warn] 未检测到 {cfg.provider} 的 API Key，桌面端将使用 Mock。")
        cfg.provider = "mock"; cfg.resolve()
    pdirs = _default_plugin_dirs(args.plugins_dir)
    run_desktop(cfg, skills_dir=args.skills_dir, plugin_dirs=pdirs, port=args.port,
                host=args.host, plugin_watch=args.plugin_watch)
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    cfg = load_from_env_and_args({"tool_choice": args.tool_choice})
    if cfg.provider in ("deepseek", "glm", "kimi") and not cfg.api_key:
        print(f"[warn] 未检测到 {cfg.provider} 的 API Key，serve 将使用 Mock。")
        cfg.provider = "mock"; cfg.resolve()
    pdirs = _default_plugin_dirs(args.plugins_dir)
    serve(cfg, skills_dir=args.skills_dir, plugin_dirs=pdirs, port=args.port,
          host=args.host, plugin_watch=args.plugin_watch)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spark", description="Spark Harness —— 极致上下文/缓存/工具编排")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="脚本化演示（无需 Key）")
    sub.add_parser("bench", help="实测缓存命中率与工具去重")
    sub.add_parser("compress", help="对照基准：上下文/Token 压缩（≥95%/≥85%）")
    sub.add_parser("tools", help="列出工具")
    s = sub.add_parser("skills", help="列出已加载技能")
    s.add_argument("--skills-dir", default=None)
    pl = sub.add_parser("plugins", help="列出已加载插件（万物皆可插件）")
    pl.add_argument("--plugins-dir", action="append", default=None)
    pc = sub.add_parser("plugin", help="插件脚手架 / 管理")
    psub = pc.add_subparsers(dest="pcmd", required=True)
    pcreate = psub.add_parser("create", help="生成插件骨架")
    pcreate.add_argument("name", help="插件目录名")
    pcreate.add_argument("--desc", default=None, help="插件描述")
    pcreate.add_argument("--target", default=None, help="目标目录（默认 plugins/<name>）")
    r = sub.add_parser("run", help="执行单个任务")
    r.add_argument("--task", required=True)
    r.add_argument("--max-iter", type=int, default=16)
    r.add_argument("--tool-choice", default="auto", help="auto/none/required/函数名")
    r.add_argument("--skills-dir", default=None)
    r.add_argument("--plugins-dir", action="append", default=None)
    c = sub.add_parser("chat", help="交互式对话")
    c.add_argument("--max-iter", type=int, default=16)
    c.add_argument("--tool-choice", default="auto")
    c.add_argument("--skills-dir", default=None)
    c.add_argument("--plugins-dir", action="append", default=None)
    sv = sub.add_parser("serve", help="启动 Web 服务（技能 + 插件驱动界面）")
    sv.add_argument("--port", type=int, default=8080)
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--tool-choice", default="auto")
    sv.add_argument("--skills-dir", default=None)
    sv.add_argument("--plugins-dir", action="append", default=None)
    sv.add_argument("--plugin-watch", action="store_true",
                    help="监视插件目录，文件变化自动热重载")
    dt = sub.add_parser("desktop", help="启动桌面端 GUI（原生窗口嵌入仪表盘）")
    dt.add_argument("--port", type=int, default=8080)
    dt.add_argument("--host", default="127.0.0.1")
    dt.add_argument("--tool-choice", default="auto")
    dt.add_argument("--skills-dir", default=None)
    dt.add_argument("--plugins-dir", action="append", default=None)
    dt.add_argument("--plugin-watch", action="store_true",
                    help="监视插件目录，文件变化自动热重载")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "demo": cmd_demo, "bench": cmd_bench, "compress": cmd_compress,
        "tools": cmd_tools, "skills": cmd_skills, "plugins": cmd_plugins,
        "plugin": cmd_plugin_create, "run": cmd_run,
        "chat": cmd_chat, "serve": cmd_serve, "desktop": cmd_desktop,
    }
    if args.cmd == "plugin":
        return handlers["plugin"](args)
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
