"""工具系统：基类、注册表、结果缓存接入。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .cache import ToolResultCache


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON Schema（object）
    func: Callable[..., Awaitable[Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具已存在: {spec.name}")
        self._tools[spec.name] = spec

    def register_from_dict(self, d: dict) -> ToolSpec:
        """运行时动态注册一个工具（来自技能/配置/模型请求）。

        d 形如 {"name","description","parameters"，可选 "func"(可调用 async)}。
        若不提供 func，则注册为一个占位（调用时返回提示），用于"按需加载"的场景。
        """
        name = d["name"]
        spec = ToolSpec(
            name=name,
            description=d.get("description", ""),
            parameters=d.get("parameters", {"type": "object", "properties": {}}),
            func=d.get("func") or _placeholder(name),
        )
        self._tools[name] = spec  # 允许覆盖（动态重定义）
        return spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def get_fn(self, name: str):
        """返回工具的 async 函数，供超时熔断执行器直接调用。"""
        spec = self._tools.get(name)
        return spec.func if spec else None

    def names(self) -> List[str]:
        return list(self._tools)

    def schema(self) -> List[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def minimal_schema(self) -> List[dict]:
        """极简 Schema：去掉描述与示例，只保留 name / 类型 / 必填——可正常发起工具调用，
        但体积远小于完整 schema。配合 compact_index + tool_help 进一步省 token。"""

        def _strip(node: dict) -> dict:
            if not isinstance(node, dict):
                return node
            out = {"type": node.get("type", "string")}
            if "properties" in node:
                props = {}
                for pname, pdef in node["properties"].items():
                    props[pname] = _strip(pdef)
                out["properties"] = props
            if "required" in node:
                out["required"] = node["required"]
            return out

        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "parameters": _strip(t.parameters),
                },
            }
            for t in self._tools.values()
        ]

    def compact_index(self) -> str:
        """一句话工具索引（进 system 锚点，极小），用于可发现性；细节用 tool_help 按需取。"""
        lines = []
        for t in self._tools.values():
            first_line = t.description.split("。")[0].split("\n")[0][:60]
            lines.append(f"- {t.name}: {first_line}")
        return "可用工具(简述，详情用 tool_help 获取):\n" + "\n".join(lines)

    async def call(self, name: str, args: dict) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            return f"ERROR: 未知工具 {name}"
        return await spec.func(**args)


def tool(name: str, description: str, parameters: dict, cost: str = "cheap"):
    """装饰器：把一个 async 函数注册为工具。

    cost: cheap/expensive —— 用于超时映射与技能展示（expensive 默认更长超时）。
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        # 注册由 build_default_registry 统一收集，这里仅做标记
        fn._tool_spec = ToolSpec(  # type: ignore
            name=name, description=description, parameters=parameters, func=fn
        )
        fn._tool_cost = cost  # type: ignore
        return fn

    return decorator


def _placeholder(name: str):
    async def _p(**kwargs) -> str:
        return f"ERROR: 工具 {name} 尚未提供实现（运行时注册但未绑定 func）"
    return _p
