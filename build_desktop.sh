#!/usr/bin/env bash
# 一键构建 Spark Harness 桌面端可执行文件（EXE / AppImage / DMG）。
#
# 依赖：
#   pip install pyinstaller pywebview
#   # Linux 额外：sudo apt install gir1.2-webkit2-4.1 python3-gi
#   # 或 Qt 后端：pip install "pywebview[qt]" PyQt5
#
# 用法：
#   ./build_desktop.sh            # 构建当前平台
#   ./build_desktop.sh --onedir   # 目录式（便于调试）
set -euo pipefail

cd "$(dirname "$0")"

ONE_FILE="--onefile"
if [[ "${1:-}" == "--onedir" ]]; then ONE_FILE="--onedir"; fi

echo "==> 安装/确认构建依赖"
python3 -m pip install --quiet pyinstaller "pywebview>=5.0" flask pyyaml requests || true

echo "==> 运行 PyInstaller（单文件 GUI，隐藏控制台）"
pyinstaller $ONE_FILE --noconsole --name SparkHarness --clean \
  --hidden-import spark_harness.desktop \
  --hidden-import spark_harness.server \
  --hidden-import spark_harness.providers \
  --add-data "spark_harness/skills:spark_harness/skills" \
  --add-data "spark_harness/plugins:spark_harness/plugins" \
  desktop_gui.py

echo "==> 完成。产物位于 dist/SparkHarness"
ls -lh dist/SparkHarness* 2>/dev/null || ls -lh dist/
