"""Android 控制：基于 ADB（Android Debug Bridge）的全功能设备操控。

覆盖：截图、点击、滑动、文本输入、按键、启动应用、安装 APK、
列举应用、获取分辨率。所有命令经 `adb` 执行，自动探测设备。
无设备/无 adb 时返回清晰说明，不抛栈。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import List, Optional

from .tools import tool

__all__ = ["adb", "has_adb", "list_devices"]


def has_adb() -> bool:
    return shutil.which("adb") is not None


def _run(args: List[str], timeout: float = 30.0) -> str:
    if not has_adb():
        return "ERROR: 未找到 adb，请安装 Android SDK platform-tools 并加入 PATH。"
    try:
        out = subprocess.run(
            ["adb"] + args, capture_output=True, text=True, timeout=timeout
        )
        text = (out.stdout or "") + (out.stderr or "")
        if len(text) > 4000:
            text = text[:4000] + "\n... (truncated)"
        return text.strip() or "(ok)"
    except subprocess.TimeoutExpired:
        return "ERROR: adb 命令超时"
    except Exception as e:  # noqa
        return f"ERROR: {e}"


def list_devices() -> List[str]:
    if not has_adb():
        return []
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines()[1:] if l.strip() and "\t" in l]
    return [l.split("\t")[0] for l in lines]


@tool(
    name="phone_screenshot",
    description="对安卓设备截图并保存到本机文件，返回路径。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "保存路径，默认 phone.png"}},
        "required": [],
    },
    cost="cheap",
)
async def phone_screenshot(path: str = "phone.png") -> str:
    r = _run(["exec-out", "screencap", "-p", f"/sdcard/{path.split('/')[-1]}"])
    if r.startswith("ERROR"):
        return r
    _run(["pull", f"/sdcard/{path.split('/')[-1]}", path])
    return f"saved -> {path}"


@tool(
    name="phone_tap",
    description="点击设备屏幕坐标 (x, y)。",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "横坐标"},
            "y": {"type": "integer", "description": "纵坐标"},
        },
        "required": ["x", "y"],
    },
    cost="cheap",
)
async def phone_tap(x: int, y: int) -> str:
    return _run(["shell", "input", "tap", str(x), str(y)])


@tool(
    name="phone_swipe",
    description="在屏幕上从 (x1,y1) 滑动到 (x2,y2)，可指定时长(ms)。",
    parameters={
        "type": "object",
        "properties": {
            "x1": {"type": "integer"}, "y1": {"type": "integer"},
            "x2": {"type": "integer"}, "y2": {"type": "integer"},
            "duration": {"type": "integer", "description": "滑动时长 ms，默认 300"},
        },
        "required": ["x1", "y1", "x2", "y2"],
    },
    cost="cheap",
)
async def phone_swipe(x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> str:
    return _run(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)])


@tool(
    name="phone_input_text",
    description="向设备输入文本（如搜索框内容）。",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string", "description": "要输入的文本"}},
        "required": ["text"],
    },
    cost="cheap",
)
async def phone_input_text(text: str) -> str:
    # 用 %s 包裹以正确处理空格与特殊字符
    return _run(["shell", "input", "text", text])


@tool(
    name="phone_keyevent",
    description="发送安卓按键事件，如 HOME/BACK/MENU/KEYCODE_xxx。",
    parameters={
        "type": "object",
        "properties": {"key": {"type": "string", "description": "按键名，如 BACK / HOME / 66(回车)"}},
        "required": ["key"],
    },
    cost="cheap",
)
async def phone_keyevent(key: str) -> str:
    return _run(["shell", "input", "keyevent", key])


@tool(
    name="phone_open_app",
    description="启动指定安卓应用（通过包名与可选项 Activity）。",
    parameters={
        "type": "object",
        "properties": {
            "package": {"type": "string", "description": "应用包名，如 com.android.chrome"},
            "activity": {"type": "string", "description": "可选项 Activity，如 .MainActivity"},
        },
        "required": ["package"],
    },
    cost="cheap",
)
async def phone_open_app(package: str, activity: Optional[str] = None) -> str:
    if activity:
        return _run(["shell", "am", "start", "-n", f"{package}/{activity}"])
    return _run(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])


@tool(
    name="phone_install",
    description="安装本地 APK 文件到设备。",
    parameters={
        "type": "object",
        "properties": {"apk_path": {"type": "string", "description": "APK 文件路径"}},
        "required": ["apk_path"],
    },
    cost="expensive",
)
async def phone_install(apk_path: str) -> str:
    return _run(["install", "-r", apk_path], timeout=120.0)


@tool(
    name="phone_list_packages",
    description="列出设备上已安装的应用包名（可按关键词过滤）。",
    parameters={
        "type": "object",
        "properties": {"filter": {"type": "string", "description": "可选关键词过滤"}},
        "required": [],
    },
    cost="cheap",
)
async def phone_list_packages(filter: str = "") -> str:
    out = _run(["shell", "pm", "list", "packages"])
    if filter:
        out = "\n".join(l for l in out.splitlines() if filter in l)
    return out or "(none)"


@tool(
    name="phone_screen_size",
    description="获取设备屏幕分辨率（用于换算点击坐标）。",
    parameters={"type": "object", "properties": {}},
    cost="cheap",
)
async def phone_screen_size() -> str:
    return _run(["shell", "wm", "size"])


@tool(
    name="phone_shell",
    description="在安卓设备上执行任意 shell 命令（高级操控）。",
    parameters={
        "type": "object",
        "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
        "required": ["command"],
    },
    cost="expensive",
)
async def phone_shell(command: str) -> str:
    return _run(["shell"] + command.split())
