#!/usr/bin/env python3
"""Spark Harness 桌面端启动器（根入口）。

- 双击 / PyInstaller 打包后的 exe 直接运行即进入桌面 GUI。
- 也可作为脚本：python desktop_gui.py
"""
import sys

from spark_harness.desktop import main

if __name__ == "__main__":
    sys.exit(main())
