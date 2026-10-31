"""系统监控插件（示例）：演示插件可贡献的五类能力。

运行： python -m spark_harness serve   （自动加载 plugins/ 下所有插件）
或：   python -m spark_harness plugins  （仅列出已加载插件）
"""

import json
import os
import platform

from spark_harness.tools import tool


@tool(
    name="system_metrics",
    description="采集本机系统指标（CPU/内存/磁盘），用于监控与诊断。返回 JSON 文本。",
    parameters={
        "type": "object",
        "properties": {
            "sample": {
                "type": "string",
                "description": "采集项：cpu / mem / disk / all（默认 all）",
                "enum": ["cpu", "mem", "disk", "all"],
            }
        },
        "required": [],
    },
    cost="cheap",
)
async def system_metrics(sample: str = "all") -> str:
    info = {
        "platform": platform.system(),
        "node": platform.node(),
        "python": platform.python_version(),
    }
    try:
        import psutil  # type: ignore

        if sample in ("cpu", "all"):
            info["cpu_percent"] = psutil.cpu_percent(interval=0.3)
        if sample in ("mem", "all"):
            vm = psutil.virtual_memory()
            info["mem"] = {"percent": vm.percent, "available_mb": vm.available // 1024 // 1024}
        if sample in ("disk", "all"):
            du = psutil.disk_usage(os.path.expanduser("~"))
            info["disk"] = {"percent": du.percent, "free_gb": du.free // 1024 // 1024 // 1024}
    except Exception:
        info["note"] = "psutil 未安装，仅返回基础信息（pip install psutil 获取完整指标）"
    return json.dumps(info, ensure_ascii=False)


def setup(api):
    # 1) 贡献一个技能（界面面板 + 能力边界 + 领域提示）
    api.add_skill({
        "name": "system-monitor",
        "title": "系统监控",
        "description": "实时采集并分析本机运行状态（CPU/内存/磁盘）。",
        "tools": ["system_metrics"],
        "prompt": "当用户询问本机性能、资源占用、健康检查或需要诊断时，使用 system_metrics 工具。",
        "ui": {"icon": "📊", "kind": "metrics"},
    })

    # 2) 贡献一个 UI 面板（注入 Web 仪表盘，界面完全由技能/插件组成）
    api.add_ui_panel({
        "id": "system-monitor-panel",
        "title": "系统监控（插件面板）",
        "html": (
            '<div class="card"><h3>📊 系统监控</h3>'
            '<p class="desc">点击让 Agent 采集本机实时指标。</p>'
            '<button onclick="sparkSend(\'采集本机 CPU、内存、磁盘 指标并解读\')">采集指标</button></div>'
        ),
    })

    # 3) 贡献一个生命周期钩子（工具调用后记录）
    api.register_hook(
        "post_tool_call",
        lambda name, args, result, error: print(f"[sysmon hook] 工具 {name} 执行完毕")
        if not error else print(f"[sysmon hook] 工具 {name} 出错: {error}"),
    )
