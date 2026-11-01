# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格：把 Spark Harness 桌面端打包成单文件可执行（EXE/AppImage/DMG）。

用法：
  pip install pyinstaller
  pyinstaller desktop/PyInstaller.spec
  # 产物：dist/SparkHarness(.exe)

提示：
  - Windows 无需额外系统依赖（WebView2 已随系统）。
  - Linux 需先：sudo apt install gir1.2-webkit2-4.1 python3-gi
  - macOS  无需额外系统依赖（WKWebView 已随系统）。
"""
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# 打包 spark_harness 包下所有数据（技能 JSON / 插件 YAML / py）
datas = collect_data_files("spark_harness", include_py_files=True)

a = Analysis(
    ["desktop_gui.py"],
    pathex=[],
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
    console=False,           # 桌面端隐藏控制台窗口
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
