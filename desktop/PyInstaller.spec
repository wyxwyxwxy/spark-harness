# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格：把 Spark Harness 桌面端打包成单文件可执行（Windows EXE / AppImage / DMG）。

PyInstaller 会把 Analysis 的脚本路径按"本规格文件所在目录"解析，因此这里统一用
SPECPATH 推导出仓库根目录（ROOT），避免从 desktop/ 子目录找文件出错。

用法：
  pip install pyinstaller pywebview
  pyinstaller desktop/PyInstaller.spec          # 产物：dist/SparkHarness(.exe)

  - Windows 无需额外系统依赖（WebView2 已随系统）。
  - Linux  ：需先 sudo apt install gir1.2-webkit2-4.1 python3-gi
  - macOS  ：无需额外系统依赖（WKWebView 已随系统）。
"""
import os

block_cipher = None
ROOT = os.path.dirname(SPECPATH)  # 仓库根目录（desktop/ 的父级）

# 显式收集 spark_harness 下的数据资源（技能 JSON / 插件 YAML 等）
datas = []
for sub in ("skills", "plugins"):
    base = os.path.join(ROOT, "spark_harness", sub)
    if not os.path.isdir(base):
        continue
    for _root, _dirs, files in os.walk(base):
        for _f in files:
            src = os.path.join(_root, _f)
            rel = os.path.relpath(src, ROOT)
            datas.append((src, os.path.dirname(rel)))

a = Analysis(
    [os.path.join(ROOT, "desktop_gui.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "spark_harness",
        "spark_harness.server",
        "spark_harness.desktop",
        "spark_harness.providers",
        "spark_harness.plugins.system_monitor",
        "spark_harness.plugins.example_provider",
        "flask",
        "webview",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SparkHarness",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,  # 桌面端隐藏控制台窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
