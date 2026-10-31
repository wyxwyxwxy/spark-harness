"""工具注册表构建：自动收集所有 @tool 模块，并按配置挂载内置动态工具与技能工具。"""

from __future__ import annotations

import inspect

from . import android, browser, computer, tools_impl
from . import builtins as builtins_mod
from .tools import ToolRegistry, ToolSpec, tool

# 默认自动扫描的工具模块（不含 builtins：内置动态工具由 attach_builtins 条件挂载）
_TOOL_MODULES = [tools_impl, computer, android, browser]
# builtins 模块单独持有，供 attach_builtins 按需挂载
_BUILTIN_MODULE = builtins_mod


def build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for mod in _TOOL_MODULES:
        for _name, fn in inspect.getmembers(mod, inspect.isfunction):
            spec: ToolSpec = getattr(fn, "_tool_spec", None)
            if isinstance(spec, ToolSpec):
                reg.register(spec)
    return reg


def attach_builtins(reg: ToolRegistry, names: tuple) -> None:
    """按需挂载本地版动态内置工具（web_search / web_extractor / code_interpreter）。"""
    for _name, fn in inspect.getmembers(_BUILTIN_MODULE, inspect.isfunction):
        spec: ToolSpec = getattr(fn, "_tool_spec", None)
        if isinstance(spec, ToolSpec) and spec.name in names:
            reg.register_from_dict(
                {"name": spec.name, "description": spec.description,
                 "parameters": spec.parameters, "func": spec.func}
            )


def cost_timeout_map(reg: ToolRegistry) -> dict:
    """根据工具 cost 生成默认超时映射：expensive 给更长超时。"""
    m = {}
    for name, fn in _iter_tool_funcs():
        cost = getattr(fn, "_tool_cost", "cheap")
        m[name] = 90.0 if cost == "expensive" else 30.0
    return m


def _iter_tool_funcs():
    for mod in _TOOL_MODULES + [_BUILTIN_MODULE]:
        for _n, fn in inspect.getmembers(mod, inspect.isfunction):
            if getattr(fn, "_tool_spec", None) is not None:
                yield _n, fn


__all__ = ["build_default_registry", "attach_builtins", "cost_timeout_map", "ToolRegistry", "ToolSpec", "tool"]
