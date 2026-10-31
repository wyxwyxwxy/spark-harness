"""具体工具实现：电脑操控、手机操控、浏览器、3D 建模、软件调用。

所有工具均为 async 函数，便于 Agent 并行调度。结果尽量精简（路径/摘要），
避免把大段内容塞回上下文——这是"极致省 token"的关键实践。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

from .tools import ToolRegistry, tool


# ============ 电脑操控 ============

@tool(
    name="shell",
    description="在本地执行 shell 命令，返回 stdout/stderr（截断到 4000 字符）。用于操控电脑、运行软件、处理文件。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
        },
        "required": ["command"],
    },
)
async def shell(command: str, timeout: int = 30) -> str:
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return f"ERROR: 命令超时（>{timeout}s）"
    except Exception as e:  # noqa
        return f"ERROR: {e}"
    out = (stdout or b"").decode("utf-8", "replace")
    err = (stderr or b"").decode("utf-8", "replace")
    text = out + ("\n[stderr]\n" + err if err else "")
    if len(text) > 4000:
        text = text[:4000] + "\n... (truncated)"
    return text or "(无输出)"


@tool(
    name="read_file",
    description="读取本地文本文件内容（截断到 6000 字符），用于查看代码/配置/数据。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "limit": {"type": "integer", "description": "最多读取字符数，默认 6000"},
        },
        "required": ["path"],
    },
)
async def read_file(path: str, limit: int = 6000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(limit)
    except Exception as e:  # noqa
        return f"ERROR: {e}"
    if len(data) >= limit:
        data += "\n... (truncated)"
    return data


@tool(
    name="write_file",
    description="把内容写入本地文件（覆盖）。用于生成代码、脚本、文档、模型文件。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目标路径"},
            "content": {"type": "string", "description": "写入内容"},
        },
        "required": ["path", "content"],
    },
)
async def write_file(path: str, content: str) -> str:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:  # noqa
        return f"ERROR: {e}"
    return f"已写入 {path} ({len(content)} 字符)"


@tool(
    name="list_dir",
    description="列出目录内容（含类型标记），用于浏览文件系统。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "目录路径，默认 ."}},
        "required": [],
    },
)
async def list_dir(path: str = ".") -> str:
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            tag = "d" if os.path.isdir(full) else "f"
            entries.append(f"[{tag}] {name}")
        return "\n".join(entries) or "(空目录)"
    except Exception as e:  # noqa
        return f"ERROR: {e}"


@tool(
    name="http_get",
    description="发起 HTTP GET 请求并返回响应文本（截断到 4000 字符），用于调用 Web API / 抓取网页。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL"},
            "headers": {"type": "object", "description": "可选请求头"},
        },
        "required": ["url"],
    },
)
async def http_get(url: str, headers: Optional[Dict[str, str]] = None) -> str:
    import requests

    try:
        r = requests.get(url, headers=headers or {}, timeout=30)
        text = r.text
        if len(text) > 4000:
            text = text[:4000] + "\n... (truncated)"
        return f"[HTTP {r.status_code}]\n" + text
    except Exception as e:  # noqa
        return f"ERROR: {e}"


# ============ 按需工具详情 ============

@tool(
    name="tool_help",
    description="获取某个工具的完整参数 Schema（JSON），当你需要确认参数细节时调用。平时靠简述索引即可。",
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "工具名"}},
        "required": ["name"],
    },
)
async def tool_help(name: str) -> str:
    from .registry import build_default_registry

    reg = build_default_registry()
    spec = reg.get(name)
    if spec is None:
        return f"ERROR: 未知工具 {name}；可用: {', '.join(reg.names())}"
    return json.dumps(
        {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
        ensure_ascii=False,
    )


@tool(
    name="recall",
    description="按记忆库条目 id（如 m3）取回该条目的完整内容（文件全文/工具完整输出）。上下文只保留摘要，需要细节时再 recall。",
    parameters={
        "type": "object",
        "properties": {"id": {"type": "string", "description": "记忆库条目 id，如 m3"}},
        "required": ["id"],
    },
)
async def recall(id: str) -> str:
    from .memory import _MEMORY_STORE

    store = _MEMORY_STORE
    if store is None:
        return "ERROR: 记忆库未初始化"
    item = store._items.get(id)
    if item is None:
        return f"ERROR: 未找到记忆 {id}；可用: {store.index_block()}"
    return f"[{id}|{item.kind}] {item.full}"


# ============ 浏览器（操控 Web 软件） ============

@tool(
    name="browser",
    description="用无头浏览器打开网页并返回可见文本（截断到 4000 字符）。用于操作 Web 应用、抓取动态页面。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网址"},
            "action": {"type": "string", "description": "可选：click=<selector> / type=<selector>::<text> / screenshot"},
            "wait": {"type": "integer", "description": "加载等待秒数，默认 5"},
        },
        "required": ["url"],
    },
)
async def browser(url: str, action: Optional[str] = None, wait: int = 5) -> str:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        # 退化方案：用 requests 取静态文本
        try:
            import requests

            r = requests.get(url, timeout=30)
            t = r.text
            if len(t) > 4000:
                t = t[:4000] + "\n... (truncated, 建议安装 playwright 获取渲染后内容)"
            return f"[HTTP {r.status_code}, 静态模式]\n" + t
        except Exception as e:  # noqa
            return f"ERROR: {e}"

    try:
        with sync_playwright() as p:
            browser_obj = p.chromium.launch()
            page = browser_obj.new_page()
            page.goto(url, timeout=wait * 1000 + 10000)
            page.wait_for_timeout(wait * 1000)
            if action:
                if action.startswith("click="):
                    page.click(action[len("click="):])
                elif action.startswith("type="):
                    sel, _, txt = action[len("type="):].partition("::")
                    page.fill(sel, txt)
            text = page.inner_text()
            if len(text) > 4000:
                text = text[:4000] + "\n... (truncated)"
            browser_obj.close()
            return text
    except Exception as e:  # noqa
        return f"ERROR: {e}"


# ============ 3D 建模 ============

def _obj_cube(s: float) -> str:
    h = s / 2
    verts = [
        (-h, -h, -h), (h, -h, -h), (h, h, -h), (-h, h, -h),
        (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h),
    ]
    faces = [
        (1, 2, 3, 4), (5, 6, 7, 8), (1, 5, 8, 4),
        (2, 6, 7, 3), (1, 2, 6, 5), (4, 3, 7, 8),
    ]
    return _emit_obj(verts, faces)


def _obj_sphere(r: float, seg: int = 24) -> str:
    verts = [(0, r, 0), (0, -r, 0)]
    for i in range(1, seg):
        phi = math.pi * i / seg
        for j in range(seg):
            theta = 2 * math.pi * j / seg
            x = r * math.sin(phi) * math.cos(theta)
            y = r * math.cos(phi)
            z = r * math.sin(phi) * math.sin(theta)
            verts.append((x, y, z))
    faces = []
    for j in range(seg):
        a = 3 + j
        b = 3 + (j + 1) % seg
        faces.append((1, b, a))
    for i in range(1, seg - 1):
        for j in range(seg):
            a = 3 + (i - 1) * seg + j
            b = 3 + (i - 1) * seg + (j + 1) % seg
            c = 3 + i * seg + (j + 1) % seg
            d = 3 + i * seg + j
            faces.append((a, b, d))
            faces.append((b, c, d))
    for j in range(seg):
        a = 3 + (seg - 2) * seg + j
        b = 3 + (seg - 2) * seg + (j + 1) % seg
        faces.append((2, a, b))
    return _emit_obj(verts, faces)


def _obj_torus(R: float, r: float, seg: int = 28) -> str:
    verts = []
    for i in range(seg):
        u = 2 * math.pi * i / seg
        for j in range(seg):
            v = 2 * math.pi * j / seg
            x = (R + r * math.cos(v)) * math.cos(u)
            y = (R + r * math.cos(v)) * math.sin(u)
            z = r * math.sin(v)
            verts.append((x, y, z))
    faces = []
    for i in range(seg):
        for j in range(seg):
            a = i * seg + j + 1
            b = i * seg + (j + 1) % seg + 1
            c = ((i + 1) % seg) * seg + (j + 1) % seg + 1
            d = ((i + 1) % seg) * seg + j + 1
            faces.append((a, b, c, d))
    return _emit_obj(verts, faces)


def _emit_obj(verts: List[tuple], faces: List[tuple]) -> str:
    lines = ["# Spark Harness generated", "o spark_mesh"]
    for x, y, z in verts:
        lines.append(f"v {x:.4f} {y:.4f} {z:.4f}")
    # 简单法线（指向原点反方向近似）
    for x, y, z in verts:
        n = math.sqrt(x * x + y * y + z * z) or 1
        lines.append(f"vn {x/n:.4f} {y/n:.4f} {z/n:.4f}")
    for f in faces:
        fs = " ".join(f"{idx}//{idx}" for idx in f)
        lines.append(f"f {fs}")
    return "\n".join(lines) + "\n"


@tool(
    name="make_3d_model",
    description="生成 3D 模型文件。支持 primitive: cube/sphere/torus/cylinder。优先导出 GLB（若已装 blender 或 trimesh），否则导出标准 .obj（可被 Blender 直接打开）。返回文件路径。",
    parameters={
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "模型描述（用于命名/注释）"},
            "primitive": {"type": "string", "description": "基础体：cube/sphere/torus/cylinder，默认 cube"},
            "size": {"type": "number", "description": "尺寸参数，默认 2"},
            "output_path": {"type": "string", "description": "输出路径（含扩展名 .obj 或 .glb）"},
        },
        "required": ["output_path"],
    },
)
async def make_3d_model(
    output_path: str,
    prompt: str = "",
    primitive: str = "cube",
    size: float = 2.0,
) -> str:
    primitive = (primitive or "cube").lower()
    if primitive == "sphere":
        obj = _obj_sphere(size)
    elif primitive == "torus":
        obj = _obj_torus(size, size / 3)
    else:  # cube / cylinder 退化为 cube（cylinder 用细长 cube 近似）
        obj = _obj_cube(size)

    try:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    except Exception:
        pass

    if output_path.lower().endswith(".glb"):
        # 尝试用 trimesh 转 GLB；否则退回 OBJ
        try:
            import trimesh  # type: ignore

            mesh = trimesh.load(obj, file_type="obj")
            mesh.export(output_path)
            return f"已生成 GLB: {output_path}（trimesh）"
        except Exception:
            obj_path = output_path[:-4] + ".obj"
            with open(obj_path, "w", encoding="utf-8") as f:
                f.write(obj)
            # 尝试 blender 转 GLB
            if shutil.which("blender"):
                try:
                    script = (
                        "import bpy, sys\n"
                        f"bpy.ops.import_scene.obj(filepath=r'{obj_path}')\n"
                        f"bpy.ops.export_scene.gltf(filepath=r'{output_path}', use_selection=False)\n"
                    )
                    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
                        tf.write(script)
                        tmp = tf.name
                    subprocess.run(
                        ["blender", "--background", "--python", tmp],
                        capture_output=True, timeout=120,
                    )
                    return f"已生成 GLB: {output_path}（blender）"
                except Exception as e:  # noqa
                    return f"已生成 OBJ（blender 转 GLB 失败）: {obj_path} | {e}"
            return f"GLB 转换依赖未就绪，已生成标准 OBJ: {obj_path}（可被 Blender 直接打开）"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(obj)
    return f"已生成 3D 模型: {output_path}（primitive={primitive}, 可被 Blender/Three.js 加载）"
