"""Spark Harness 自测：验证核心逻辑（无需任何 API Key）。

运行：python -m pytest tests/  或  python tests/test_harness.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spark_harness.agent import Agent
from spark_harness.app import build_agent
from spark_harness.cache import ToolResultCache
from spark_harness.compression import compress_tool_result, extractive_compress, head_tail
from spark_harness.config import load_from_env_and_args
from spark_harness.memory import MemoryStore
from spark_harness.plugin import PluginManager
from spark_harness.providers.mock import MockProvider
from spark_harness.registry import build_default_registry
from spark_harness.runtime import CircuitBreaker, ToolExecutor, ToolTimeout
from spark_harness.skill import SkillManager
from spark_harness.tokenizer import count_text, count_messages
from spark_harness.ui import generate_dashboard_html

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spark_harness")
BUILTIN_PLUGINS = os.path.join(PKG, "plugins")


def test_tokenizer():
    assert count_text("") == 0
    assert count_text("hello world") >= 2
    # 中文按字近似 1 token
    assert count_text("你好世界") == 4
    print("[ok] tokenizer")


def test_registry():
    reg = build_default_registry()
    names = reg.names()
    for need in ["shell", "read_file", "write_file", "list_dir", "http_get",
                 "browser", "phone_screenshot", "phone_tap", "phone_swipe",
                 "phone_input_text", "phone_shell", "make_3d_model"]:
        assert need in names, f"缺少工具 {need}"
    assert len(names) >= 12
    # schema 可被 JSON 序列化
    json.dumps(reg.schema(), ensure_ascii=False)
    print(f"[ok] registry: {len(names)} 个工具")


def test_scripted_agent_flow():
    import tempfile
    out_obj = os.path.join(tempfile.gettempdir(), "_t_cube.obj")
    script = [
        {
            "content": None,
            "tool_calls": [
                {"id": "c1", "name": "make_3d_model",
                 "arguments": {"output_path": out_obj, "primitive": "cube"}},
                {"id": "c2", "name": "list_dir", "arguments": {"path": "."}},
            ],
        },
        {"content": "已完成模型生成与目录列举。", "tool_calls": []},
    ]
    cfg = load_from_env_and_args({"provider": "mock", "compact_threshold": 5.0})
    reg = build_default_registry()
    provider = MockProvider(cfg, script=script)
    memory = MemoryStore()
    agent = Agent(cfg, provider, reg, memory, ToolResultCache(enabled=False), verbose=False)
    answer = asyncio.run(agent.run("生成立方体并列出目录"))
    assert "已完成" in answer
    assert os.path.exists(out_obj), "3D 模型文件应已生成"
    with open(out_obj) as f:
        data = f.read()
    assert data.startswith("# Spark Harness generated")
    assert "v " in data and "f " in data
    # 工具结果应被外置到记忆库（上下文只留摘要）
    assert len(memory._items) >= 2, "工具结果应外置到记忆库"
    print("[ok] scripted agent flow + 3D 模型生成 + 结果外置")


def test_prefix_cache_hit_rate_rises():
    cfg = load_from_env_and_args({"provider": "mock", "compact_threshold": 5.0})
    provider = MockProvider(cfg)
    reg = build_default_registry()
    base = [
        {"role": "system", "content": cfg.system_prompt},
        {"role": "user", "content": "简报: " + ", ".join(reg.names())},
    ]
    rates = []
    for i in range(6):
        messages = base + [{"role": "user", "content": f"任务 {i}"}]
        asyncio.run(provider.complete(messages, tools=reg.schema()))
        rates.append(provider.stats.hit_rate)
    # 命中率应随轮次单调不降，且末轮明显高于首轮（首轮为 0）
    assert rates[0] == 0.0
    assert rates[-1] > rates[1], f"命中率未上升: {rates}"
    print(f"[ok] 前缀缓存命中率: {[f'{r:.0%}' for r in rates]}")


def test_tool_result_cache_dedup():
    reg = build_default_registry()
    cache = ToolResultCache(ttl=600, enabled=True)
    a = {"command": "echo dedup-test"}

    async def call():
        c = cache.get("shell", a)
        if c is not None:
            return c, True
        r = await reg.call("shell", a)
        cache.put("shell", a, r)
        return r, False

    r1, hit1 = asyncio.run(call())
    r2, hit2 = asyncio.run(call())
    assert hit1 is False
    assert hit2 is True
    assert r1 == r2
    assert cache.hit_rate == 0.5
    print("[ok] 工具结果缓存去重")


def test_briefing_is_prefix_stable():
    # 验证 system(含工具索引) + 环境简报在最前，保证前缀缓存锚点
    cfg = load_from_env_and_args({"provider": "mock", "compact_threshold": 5.0})
    reg = build_default_registry()
    agent = Agent(cfg, MockProvider(cfg), reg, MemoryStore(), verbose=False)
    msgs = agent.ctx.build_messages()
    assert msgs[0]["role"] == "system"
    assert "可用工具(简述" in msgs[0]["content"]      # 压缩工具索引在锚点内
    assert "环境" in msgs[1]["content"]                 # 环境简报稳定锚点
    print("[ok] 前缀锚点稳定 (system+索引 + 环境简报 在最前)")


def test_compression_shrinks_large_text():
    big = (
        "本段为冗余背景说明，用于铺垫，没有关键数值。\n"
        + "关键指标 2024 年营收 1.2 亿，净利润 3400 万，增长率 18.6%。\n"
        + "这是又一段冗余的铺垫文字，和主线无关，可以压缩掉。\n"
        + "服务器节点 A 延迟 12ms，节点 B 延迟 45ms，超时阈值 200ms。\n"
        + "此处为模板化描述，包含大量可预测的连接词与废话，应当被抽取式压缩剔除。\n"
    ) * 40
    comp = compress_tool_result(big, hard_cap=700, query="营收与延迟指标是多少")
    assert count_text(comp) < count_text(big)
    assert "1.2 亿" in comp or "3400 万" in comp or "12ms" in comp  # 关键数值应保留
    print(f"[ok] 大文本压缩: {count_text(big)} -> {count_text(comp)} tok")


def test_memory_externalize_and_retrieve():
    mem = MemoryStore()
    mid = mem.add("file", "配置文档摘要", "port=8080\ndb=postgres\nkey=abc123\n" * 50)
    block = mem.retrieve("port db postgres 配置", k=2, max_tokens=500)
    assert mid in block
    assert "port=8080" in block or "db=postgres" in block
    print("[ok] 记忆外置 + BM25 检索")


def test_minimal_schema_smaller_than_full():
    reg = build_default_registry()
    full = count_text(json.dumps(reg.schema(), ensure_ascii=False))
    mini = count_text(json.dumps(reg.minimal_schema(), ensure_ascii=False))
    assert mini < full
    print(f"[ok] 极简 schema 更小: {mini} < {full} tok")


def test_circuit_breaker_opens_on_failures():
    # 一个总会失败的坏工具，连续失败应触发熔断（OPEN），之后快速失败
    breaker = CircuitBreaker(failure_threshold=3, cooldown=0.2)
    ex = ToolExecutor(circuit=breaker, default_timeout=2.0, retries=0)

    async def bad(**kwargs):
        raise RuntimeError("boom")

    async def run():
        return [await ex.execute("bad_tool", bad, {}) for _ in range(5)]

    outs = asyncio.run(run())
    assert breaker.state("bad_tool") == "open", "连续失败应熔断"
    assert any("breaker-open" in o for o in outs), "熔断后应快速失败"
    assert ex.stats["bad_tool"]["breaker"] >= 1
    print("[ok] 熔断: 连续失败触发 OPEN 并快速失败")


def test_executor_timeout():
    # 工具执行超过硬超时，应被取消且不阻塞（超时熔断）
    ex = ToolExecutor(circuit=CircuitBreaker(), default_timeout=0.2, retries=0)

    async def slow(**kwargs):
        await asyncio.sleep(2.0)
        return "should-not-reach"

    async def run():
        return await ex.execute("slow_tool", slow, {})

    out = asyncio.run(run())
    assert isinstance(ex.stats["slow_tool"]["timeout"], int) and ex.stats["slow_tool"]["timeout"] >= 1
    assert "timeout" in out or "ERROR" in out
    print("[ok] 超时熔断: 超硬超时即取消")


def test_dynamic_tool_registration():
    # 运行时动态注册工具（动态工具调用的一部分）
    reg = build_default_registry()
    called = {}

    async def my_tool(x: int) -> str:
        called["x"] = x
        return f"got {x}"

    reg.register_from_dict({
        "name": "dyn_echo", "description": "动态工具", "func": my_tool,
        "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    })
    assert reg.get("dyn_echo") is not None
    assert reg.get_fn("dyn_echo") is not None
    out = asyncio.run(reg.call("dyn_echo", {"x": 7}))
    assert out == "got 7" and called["x"] == 7
    print("[ok] 运行时动态注册工具")


def test_skills_loaded_and_ui_generated():
    sm = SkillManager()
    names = sm.names()
    for need in ["web-browsing", "android-control", "computer-control", "3d-modeling", "dev-tools"]:
        assert need in names, f"缺少技能 {need}"
    # 界面由技能动态生成，且包含技能标题
    html = generate_dashboard_html(sm.list(), build_default_registry())
    assert "Spark Harness" in html
    for s in sm.list():
        assert s.title in html, "界面应渲染技能标题"
    assert "界面由" in html
    print(f"[ok] 技能加载 {len(names)} 个 + 界面由技能生成")


def test_builtin_plugin_loads():
    pm = PluginManager([BUILTIN_PLUGINS], verbose=False).load_all()
    assert len(pm.plugins) >= 1, "应至少加载 1 个内置插件"
    assert any(p.name == "system-monitor" and p.ok for p in pm.plugins)
    assert any(t.name == "system_metrics" for t in pm.tools)
    assert any(s.get("name") == "system-monitor" for s in pm.skills)
    assert len(pm.ui_panels) >= 1
    assert "post_tool_call" in pm.hooks.events()
    print(f"[ok] 内置插件加载: 工具 {len(pm.tools)} 面板 {len(pm.ui_panels)} 钩子 {pm.hooks.events()}")


def _write_temp_plugin(base: str, name: str) -> str:
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "plugin.yaml"), "w", encoding="utf-8") as f:
        f.write(f"name: {name}\nversion: 1.0.0\ndescription: tmp\nentry: plugin.py\n")
    with open(os.path.join(d, "plugin.py"), "w", encoding="utf-8") as f:
        f.write(
            "from spark_harness.tools import tool\n\n"
            "@tool(name='tmp_tool', description='tmp', parameters={'type':'object','properties':{},'required':[]})\n"
            "async def tmp_tool() -> str:\n"
            "    return 'tmp-ok'\n\n"
            "def setup(api):\n"
            "    api.add_skill({'name':'tmp-skill','title':'临时技能','description':'d','tools':['tmp_tool'],'prompt':'','ui':{}})\n"
            "    api.add_ui_panel({'id':'tmp-panel','title':'临时面板','html':'<div>hi</div>'})\n"
            "    api.register_hook('on_agent_start', lambda t: None)\n"
        )
    return d


def test_plugin_load_and_integration():
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp()
    try:
        _write_temp_plugin(tmp, "my-tmp-plugin")
        # 1) PluginManager 直接加载
        pm = PluginManager([tmp], verbose=False).load_all()
        assert any(t.name == "tmp_tool" for t in pm.tools)
        assert any(s["name"] == "tmp-skill" for s in pm.skills)
        assert any(p["id"] == "tmp-panel" for p in pm.ui_panels)
        assert "on_agent_start" in pm.hooks.events()
        # 2) 集成进 Agent：工具进入注册表
        cfg = load_from_env_and_args({"provider": "mock", "compact_threshold": 5.0})
        agent, reg, mem, skills, pm2 = build_agent(cfg, skills=None, restrict_to_skills=False, plugin_dirs=[tmp])
        assert "tmp_tool" in reg.names()
        # 3) 仪表盘渲染插件面板（面板 html 片段被注入）
        html = generate_dashboard_html(skills.list() if skills else [], reg, ui_panels=pm2.ui_panels)
        assert "<div>hi</div>" in html
        print("[ok] 插件加载 + 集成进 Agent + 仪表盘渲染面板")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_plugin_provider_extension_point():
    # 插件可注册自定义厂商（万物皆可插件：第五类扩展点），且能真正驱动 Agent。
    example_dir = os.path.join(BUILTIN_PLUGINS, "example-provider")
    assert os.path.isdir(example_dir), "example-provider 插件应存在"
    cfg = load_from_env_and_args(
        {"provider": "echo", "system_prompt": "x", "compact_threshold": 5.0}
    )
    agent, reg, mem, skills, pm = build_agent(
        cfg, skills=None, restrict_to_skills=False, plugin_dirs=[example_dir]
    )
    assert "echo" in pm.providers, "插件应贡献 echo 厂商"
    # 该 Provider 无真实后端，确定性回显，可直接驱动 Agent 一轮完成
    answer = asyncio.run(agent.run("ping from plugin test", max_iterations=4))
    assert "echo-provider" in answer, "应使用插件贡献的 Provider 生成答复"
    print("[ok] 插件 Provider 扩展点：自定义厂商驱动 Agent")


def test_plugin_hot_reload():
    # 热重载：清空状态并重新扫描，新增/修改插件无需重启进程即生效
    import tempfile
    import shutil
    import time
    tmp = tempfile.mkdtemp()
    try:
        _write_temp_plugin(tmp, "p1")
        pm = PluginManager([tmp], verbose=False).load_all()
        assert len(pm.plugins) == 1 and pm.tools and pm.skills

        time.sleep(0.01)  # 确保 mtime 变化
        _write_temp_plugin(tmp, "p2")
        pm.reload()
        names = {p.name for p in pm.plugins}
        assert names == {"p1", "p2"}, f"热重载后应有 2 个插件: {names}"
        tool_names = {t.name for t in pm.tools}
        assert tool_names == {"tmp_tool"}, f"reload 应清空再重载工具: {tool_names}"
        assert "on_agent_start" in pm.hooks.events()
        print("[ok] 插件热重载：reload 后新增插件即时生效")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_tokenizer()
    test_registry()
    test_scripted_agent_flow()
    test_prefix_cache_hit_rate_rises()
    test_tool_result_cache_dedup()
    test_briefing_is_prefix_stable()
    test_compression_shrinks_large_text()
    test_memory_externalize_and_retrieve()
    test_minimal_schema_smaller_than_full()
    test_circuit_breaker_opens_on_failures()
    test_executor_timeout()
    test_dynamic_tool_registration()
    test_skills_loaded_and_ui_generated()
    test_builtin_plugin_loads()
    test_plugin_load_and_integration()
    test_plugin_provider_extension_point()
    test_plugin_hot_reload()
    print("\n全部自测通过 ✅")
