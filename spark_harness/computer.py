"""OS 级计算机控制：鼠标 / 键盘 / 截图 / 应用。

设计目标：把 Harness "融合" 进操作系统——
- Windows：优先 pyautogui（跨平台），并附带 ctypes + user32 的
  SendInput 原生回退，无需 pywin32 即可驱动鼠标键盘；配合
  `install_windows.ps1` 可常驻为后台 Agent。
- macOS / Linux：pyautogui 同样可用；无显示器时截图降级为说明。

所有工具都"优雅降级"：依赖缺失时返回清晰的安装指引，而不是抛栈。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Dict, List, Optional

from .tools import tool

__all__ = ["OSController", "get_os_controller", "is_windows"]


def is_windows() -> bool:
    return platform.system().lower().startswith("win")


class OSController:
    """统一的鼠标/键盘/截图/应用后端，自动选择可用实现。"""

    def __init__(self):
        self.system = platform.system().lower()
        self._pg = None
        self.backend = "none"
        self._init_pyautogui()

    def _init_pyautogui(self):
        try:
            import pyautogui  # type: ignore

            pyautogui.FAILSAFE = True
            self._pg = pyautogui
            self.backend = "pyautogui"
        except Exception:
            self._pg = None
            if is_windows():
                self.backend = "windows-native"
            else:
                self.backend = "none"

    # ---------- 鼠标 ----------
    def mouse_move(self, x: int, y: int, duration: float = 0.0) -> str:
        if self._pg:
            self._pg.moveTo(x, y, duration=duration)
            return f"moved -> ({x},{y})"
        if self.backend == "windows-native":
            self._win_mouse(x, y)
            return f"moved(win) -> ({x},{y})"
        return self._need_pyautogui()

    def mouse_click(self, x: int = None, y: int = None, button: str = "left", clicks: int = 1) -> str:
        if x is not None and y is not None and self._pg:
            self._pg.click(x, y, button=button, clicks=clicks)
        elif self._pg:
            self._pg.click(button=button, clicks=clicks)
        elif self.backend == "windows-native":
            if x is not None and y is not None:
                self._win_mouse(x, y)
            self._win_click(button)
        else:
            return self._need_pyautogui()
        return f"clicked {button} x{clicks}" + (f" @({x},{y})" if x is not None else "")

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> str:
        if self._pg:
            self._pg.moveTo(x1, y1)
            self._pg.dragTo(x2, y2, duration=duration)
            return f"dragged ({x1},{y1})->({x2},{y2})"
        return self._need_pyautogui()

    def scroll(self, amount: int) -> str:
        if self._pg:
            self._pg.scroll(amount)
            return f"scrolled {amount}"
        return self._need_pyautogui()

    # ---------- 键盘 ----------
    def type_text(self, text: str, interval: float = 0.02) -> str:
        if self._pg:
            self._pg.write(text, interval=interval)
            return f"typed {len(text)} chars"
        return self._need_pyautogui()

    def key_press(self, keys: str) -> str:
        """keys 形如 'ctrl+s' / 'win+r' / 'esc'，用 + 连接组合键。"""
        if self._pg:
            key_list = [k.strip() for k in keys.lower().split("+")]
            if len(key_list) > 1:
                self._pg.hotkey(*key_list)
            else:
                self._pg.press(key_list[0])
            return f"pressed {keys}"
        if self.backend == "windows-native":
            self._win_hotkey(keys)
            return f"pressed(win) {keys}"
        return self._need_pyautogui()

    # ---------- 截图 ----------
    def screenshot(self, path: str = "screenshot.png") -> str:
        if self._pg:
            self._pg.screenshot(path)
            return f"saved -> {path} ({os.path.getsize(path)} bytes)"
        try:
            if shutil.which("scrot"):
                subprocess.run(["scrot", path], check=True)
                return f"saved -> {path}"
            if shutil.which("import"):  # imagemagick
                subprocess.run(["import", "-window", "root", path], check=True)
                return f"saved -> {path}"
        except Exception as e:
            return f"ERROR: {e}"
        return self._need_pyautogui()

    def screen_size(self) -> str:
        if self._pg:
            w, h = self._pg.size()
            return f"{w}x{h}"
        return self._need_pyautogui()

    # ---------- 应用 ----------
    def open_app(self, target: str, args: Optional[List[str]] = None) -> str:
        try:
            cmd = [target] + (args or [])
            if self.system == "windows":
                subprocess.Popen(["cmd", "/c", "start", "", target] + (args or []),
                                 shell=False)
            elif self.system == "darwin":
                subprocess.Popen(["open", target] + (args or []))
            else:
                subprocess.Popen(cmd)
            return f"launched {target}"
        except Exception as e:
            return f"ERROR launching {target}: {e}"

    def run_program(self, command: str) -> str:
        """运行任意程序/命令（与 shell 不同：用于 GUI 程序前台启动）。"""
        try:
            proc = subprocess.Popen(command, shell=True)
            return f"started pid={proc.pid}: {command}"
        except Exception as e:
            return f"ERROR: {e}"

    # ---------- Windows 原生回退（无需 pywin32） ----------
    def _win_mouse(self, x, y):
        try:
            import ctypes
            ctypes.windll.user32.SetCursorPos(int(x), int(y))
        except Exception:
            pass

    def _win_click(self, button):
        try:
            import ctypes
            # MOUSEEVENTF_LEFTDOWN/UP = 0x0002/0x0004 ; RIGHT=0x0008/0x0010
            down = 0x0002 if button == "left" else 0x0008
            up = 0x0004 if button == "left" else 0x0010
            ctypes.windll.user32.mouse_event(down, 0, 0, 0, 0)
            ctypes.windll.user32.mouse_event(up, 0, 0, 0, 0)
        except Exception:
            pass

    def _win_hotkey(self, keys):
        try:
            import ctypes
            vk_map = {
                "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
                "win": 0x5B, "windows": 0x5B, "esc": 0x1B, "enter": 0x0D,
                "tab": 0x09, "r": 0x52, "s": 0x53, "c": 0x43, "v": 0x56,
                "a": 0x41, "delete": 0x2E, "space": 0x20,
            }
            parts = [k.strip() for k in keys.lower().split("+")]
            vks = [vk_map.get(p, ord(p.upper()) if len(p) == 1 else 0) for p in parts]
            for vk in vks:
                ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            for vk in reversed(vks):
                ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        except Exception:
            pass

    def _need_pyautogui(self) -> str:
        base = (
            "ERROR: 需要 pyautogui 才能驱动鼠标/键盘/截图。\n"
            "  安装： pip install pyautogui\n"
        )
        if is_windows():
            base += "  Windows 已启用原生回退（ctypes+user32），但截图仍需 pyautogui/screenshot 工具。\n"
            base += "  推荐运行项目内的 install_windows.ps1 完成常驻部署。"
        return base


_controller: Optional[OSController] = None


def get_os_controller() -> OSController:
    global _controller
    if _controller is None:
        _controller = OSController()
    return _controller


# ============================ 工具定义 ============================


@tool(
    name="computer_screenshot",
    description="对当前屏幕截图并保存为文件，返回路径与大小。用于观察 GUI 状态。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "保存路径，默认 screenshot.png"},
        },
        "required": [],
    },
    cost="cheap",
)
async def computer_screenshot(path: str = "screenshot.png") -> str:
    return get_os_controller().screenshot(path)


@tool(
    name="computer_mouse",
    description="移动/点击/拖拽鼠标。action=move|click|drag|scroll。",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["move", "click", "drag", "scroll"]},
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "x2": {"type": "integer", "description": "drag 终点 x"},
            "y2": {"type": "integer", "description": "drag 终点 y"},
            "button": {"type": "string", "enum": ["left", "right"], "description": "click 按键"},
            "amount": {"type": "integer", "description": "scroll 滚动量"},
        },
        "required": ["action"],
    },
    cost="cheap",
)
async def computer_mouse(
    action: str, x: int = None, y: int = None, x2: int = None, y2: int = None,
    button: str = "left", amount: int = 0,
) -> str:
    c = get_os_controller()
    if action == "move":
        return c.mouse_move(x or 0, y or 0)
    if action == "click":
        return c.mouse_click(x, y, button)
    if action == "drag":
        return c.mouse_drag(x or 0, y or 0, x2 or 0, y2 or 0)
    if action == "scroll":
        return c.scroll(amount)
    return f"ERROR: 未知 action {action}"


@tool(
    name="computer_keyboard",
    description="键入文本或按下组合键。mode=type|key。key 形如 'ctrl+s'、'win+r'。",
    parameters={
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["type", "key"]},
            "text": {"type": "string", "description": "type 模式下的文本"},
            "keys": {"type": "string", "description": "key 模式下的组合键"},
        },
        "required": ["mode"],
    },
    cost="cheap",
)
async def computer_keyboard(mode: str, text: str = "", keys: str = "") -> str:
    c = get_os_controller()
    if mode == "type":
        return c.type_text(text)
    if mode == "key":
        return c.key_press(keys)
    return f"ERROR: 未知 mode {mode}"


@tool(
    name="computer_open_app",
    description="在操作系统里启动一个应用程序或打开文件（GUI 程序前台启动）。",
    parameters={
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "程序名或文件路径，如 notepad / code / calc"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "可选参数"},
        },
        "required": ["target"],
    },
    cost="cheap",
)
async def computer_open_app(target: str, args: Optional[List[str]] = None) -> str:
    return get_os_controller().open_app(target, args)


@tool(
    name="computer_screen_size",
    description="返回当前屏幕分辨率（宽x高），用于定位鼠标坐标。",
    parameters={"type": "object", "properties": {}},
    cost="cheap",
)
async def computer_screen_size() -> str:
    return get_os_controller().screen_size()
