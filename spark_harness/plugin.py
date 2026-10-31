"""插件系统：万物皆可插件。

一个插件是一个目录，包含：
  - plugin.yaml   清单（name/version/description/author/entry/tags）
  - <entry>.py    入口模块（默认 plugin.py），可定义 setup(api) 或用 @tool 装饰函数

插件可贡献五类能力（覆盖 Harness 的一切扩展点）：
  - 工具(tools)    ：@tool 装饰的 async 函数（与内置工具完全一致）
  - 技能(skills)   ：dict（同 skills/*.json，决定界面面板与能力边界）
  - Provider       ：LLM 后端类（扩展可用模型厂商）
  - UI 面板        ：HTML 片段（注入 Web 仪表盘，界面完全由技能/插件组成）
  - 钩子(hooks)    ：生命周期回调（pre_tool_call / post_tool_call / on_agent_start / on_agent_end / on_message）

加载后 PluginManager 把贡献注册进：ToolRegistry / SkillManager / ProviderRegistry /
UI 构建器 / HookRegistry。新增能力只需放一个插件目录，零改核心代码。
"""

from __future__ import annotations

import importlib.util
import inspect
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .skill import Skill
from .tools import ToolSpec

__all__ = ["HookRegistry", "PluginAPI", "Plugin", "PluginManager", "get_plugin_manager"]


# ----------------------------------------------------------------------------
# 钩子注册表：生命周期扩展点（同步 + 异步双通道）
# ----------------------------------------------------------------------------
class HookRegistry:
    """管理插件钩子。支持已知事件名，trigger 容错（单钩子出错不影响其它）。"""

    KNOWN = {
        "pre_tool_call",    # (name, args) -> 可返回修改后的 args
        "post_tool_call",   # (name, args, result, error)
        "on_agent_start",   # (task)
        "on_agent_end",     # (answer)
        "on_message",       # (role, content)
    }

    def __init__(self) -> None:
        self._h: Dict[str, List[Callable]] = {e: [] for e in self.KNOWN}

    def register(self, event: str, fn: Callable) -> None:
        if event not in self._h:
            self._h[event] = []
        if fn not in self._h[event]:
            self._h[event].append(fn)

    def events(self) -> List[str]:
        return [e for e, lst in self._h.items() if lst]

    async def atrigger(self, event: str, *args, default: Any = None) -> Any:
        out = default
        for fn in self._h.get(event, []):
            try:
                r = fn(*args)
                if inspect.iscoroutine(r):
                    r = await r
                if r is not None:
                    out = r
            except Exception as e:  # noqa
                print(f"[hook:{event}] 执行出错: {e}")
        return out

    def trigger(self, event: str, *args, default: Any = None) -> Any:
        out = default
        for fn in self._h.get(event, []):
            try:
                r = fn(*args)
                if r is not None:
                    out = r
            except Exception as e:  # noqa
                print(f"[hook:{event}] 执行出错: {e}")
        return out


# ----------------------------------------------------------------------------
# 插件 API：插件入口通过它贡献能力
# ----------------------------------------------------------------------------
class PluginAPI:
    def __init__(self, plugin: "Plugin", manager: "PluginManager") -> None:
        self.plugin = plugin
        self._m = manager

    # —— 工具 ——
    def register_tool(self, func: Callable) -> ToolSpec:
        spec: ToolSpec = getattr(func, "_tool_spec", None)
        if not isinstance(spec, ToolSpec):
            raise ValueError("register_tool 需要 @tool 装饰的 async 函数")
        self._m.tools.append(spec)
        return spec

    def register_tools(self, *funcs: Callable) -> None:
        for f in funcs:
            self.register_tool(f)

    # —— 技能 ——
    def add_skill(self, skill_dict: dict) -> None:
        self._m.skills.append(skill_dict)

    # —— Provider（LLM 后端） ——
    def register_provider(self, name: str, cls) -> None:
        self._m.providers[name] = cls

    # —— UI 面板（HTML 片段） ——
    def add_ui_panel(self, panel_dict: dict) -> None:
        panel_dict = dict(panel_dict)
        panel_dict.setdefault("id", f"plugin-{self.plugin.name}")
        self._m.ui_panels.append(panel_dict)

    # —— 钩子 ——
    def register_hook(self, event: str, fn: Callable) -> None:
        self._m.hooks.register(event, fn)


# ----------------------------------------------------------------------------
# 插件元信息
# ----------------------------------------------------------------------------
@dataclass
class Plugin:
    name: str
    path: str
    manifest: Dict[str, Any] = field(default_factory=dict)
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    ok: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "path": self.path,
            "ok": self.ok,
            "error": self.error,
        }


# ----------------------------------------------------------------------------
# 插件管理器：发现 + 加载 + 汇总贡献
# ----------------------------------------------------------------------------
class PluginManager:
    def __init__(self, paths: Optional[List[str]] = None, verbose: bool = False) -> None:
        self.paths: List[str] = list(paths or [])
        self.verbose = verbose
        self.plugins: List[Plugin] = []
        self.tools: List[ToolSpec] = []
        self.skills: List[dict] = []
        self.providers: Dict[str, Any] = {}
        self.ui_panels: List[dict] = []
        self.hooks = HookRegistry()

    # —— 发现：扫描每个路径下的子目录，含 plugin.yaml 即为插件 ——
    # 同时兼容"路径本身就是一个插件目录"（含 plugin.yaml）
    def discover(self) -> List[str]:
        found: List[str] = []
        for base in self.paths:
            if not os.path.isdir(base):
                if self.verbose:
                    print(f"[plugin] 路径不存在，跳过: {base}")
                continue
            # 路径本身即为插件
            if os.path.exists(os.path.join(base, "plugin.yaml")):
                found.append(base)
                continue
            for name in sorted(os.listdir(base)):
                d = os.path.join(base, name)
                if os.path.isdir(d) and os.path.exists(os.path.join(d, "plugin.yaml")):
                    found.append(d)
        return found

    def load_all(self) -> "PluginManager":
        for d in self.discover():
            self._load_one(d)
        if self.verbose:
            self._print_summary()
        return self

    def reload(self) -> "PluginManager":
        """热重载：清空已加载状态（插件/工具/技能/Provider/面板/钩子）并重新扫描加载。

        用于不改核心代码、不重启进程的前提下，让插件目录的新增/修改即时生效。
        """
        self.plugins.clear()
        self.tools.clear()
        self.skills.clear()
        self.providers.clear()
        self.ui_panels.clear()
        self.hooks = HookRegistry()
        return self.load_all()

    def _load_one(self, d: str) -> None:
        try:
            manifest = self._read_manifest(d)
            name = manifest.get("name") or os.path.basename(d)
            plugin = Plugin(
                name=name, path=d, manifest=manifest,
                version=str(manifest.get("version", "0.0.0")),
                description=manifest.get("description", ""),
                author=manifest.get("author", ""),
            )
            api = PluginAPI(plugin, self)
            entry = manifest.get("entry", "plugin.py")
            entry_path = os.path.join(d, entry)
            if os.path.exists(entry_path):
                mod = self._import(entry_path, name)
                # 自动收集入口模块里 @tool 装饰的函数（与 registry 同样的约定）
                for _n, fn in inspect.getmembers(mod, inspect.isfunction):
                    spec = getattr(fn, "_tool_spec", None)
                    if isinstance(spec, ToolSpec):
                        self.tools.append(spec)
                setup = getattr(mod, "setup", None)
                if callable(setup):
                    setup(api)
            plugin.ok = True
            self.plugins.append(plugin)
        except Exception as e:  # noqa
            pname = os.path.basename(d)
            if self.plugins and self.plugins[-1].path == d:
                self.plugins[-1].ok = False
                self.plugins[-1].error = str(e)
            else:
                self.plugins.append(Plugin(name=pname, path=d, ok=False, error=str(e)))
            if self.verbose:
                print(f"[plugin] 加载失败 {pname}: {e}")

    @staticmethod
    def _read_manifest(d: str) -> dict:
        path = os.path.join(d, "plugin.yaml")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        try:
            import yaml  # type: ignore
            return yaml.safe_load(text) or {}
        except Exception:
            # 极简兜底：行级 key: value（仅支持扁平结构）
            out: Dict[str, Any] = {}
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip()
            return out

    @staticmethod
    def _import(path: str, name: str):
        mod_name = f"spark_plugin_{name}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载插件入口: {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        return mod

    # —— 把插件技能汇入 SkillManager ——
    def apply_skills(self, skill_manager) -> None:
        valid = {"name", "title", "description", "tools", "prompt", "ui"}
        for sd in self.skills:
            sd = {k: v for k, v in sd.items() if k in valid}
            if "name" not in sd:
                continue
            skill_manager.add(Skill(**sd))

    def _print_summary(self) -> None:
        print(
            f"[plugin] 已加载 {len(self.plugins)} 个插件 | "
            f"工具 {len(self.tools)} | 技能 {len(self.skills)} | "
            f"面板 {len(self.ui_panels)} | Provider {len(self.providers)} | "
            f"钩子 {len(self.hooks.events())}"
        )

    def summary(self) -> str:
        lines = [f"插件 {len(self.plugins)} 个："]
        for p in self.plugins:
            state = "OK" if p.ok else f"失败({p.error})"
            lines.append(f"  - {p.name} v{p.version} [{state}]")
        return "\n".join(lines)


def get_plugin_manager(plugin_dirs: Optional[List[str]] = None, verbose: bool = False) -> PluginManager:
    """便捷构造并加载插件管理器。plugin_dirs 为 None 时不扫描（返回空管理器）。"""
    pm = PluginManager(plugin_dirs or [], verbose=verbose)
    if plugin_dirs:
        pm.load_all()
    return pm
