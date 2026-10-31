<# ─────────────────────────────────────────────────────────────
   Spark Harness · Windows 融合安装脚本
   把 Harness 常驻为"后台智能体"，并装好操控桌面所需的依赖。
   以管理员 PowerShell 运行：  .\install_windows.ps1
# ───────────────────────────────────────────────────────────── #>
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\SparkHarness",
    [int]$ServePort = 8080,
    [switch]$NoScheduledTask   # 不加则注册"登录时后台启动 Web 服务"
)

$ErrorActionPreference = "Stop"

function Need([string]$cmd){ (Get-Command $cmd -ErrorAction SilentlyContinue) -ne $null }

if (-not (Need python)) {
    Write-Error "未找到 python，请先安装 Python 3.11+ 并加入 PATH：https://www.python.org/downloads/"
}

# 1) 准备目录与虚拟环境
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Set-Location $InstallDir
if (-not (Test-Path "venv")) { python -m venv venv }
$venvPy = Join-Path $InstallDir "venv\Scripts\python.exe"

# 2) 安装 Harness 与桌面操控依赖（pyautogui 驱动鼠标/键盘/截图）
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -e . flask pyautogui beautifulsoup4
& $venvPy -m pip install playwright; & $venvPy -m playwright install chromium

# 3) 写一份启动脚本（后台常驻）
$launcher = Join-Path $InstallDir "launch_harness.ps1"
@"
`$env:PYTHONPATH='$InstallDir'
& '$venvPy' -m spark_harness serve --port $ServePort --host 127.0.0.1
"@ | Set-Content -Encoding UTF8 $launcher

# 4) 注册"登录时后台启动"（把 Harness 融合进 Windows 开机自启）
if (-not $NoScheduledTask) {
    $taskName = "SparkHarnessAgent"
    $act = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-WindowStyle Hidden -File `"$launcher`""
    $trig = New-ScheduledTaskTrigger -AtLogOn
    $set = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $act -Trigger $trig -Settings $set -Force | Out-Null
    Write-Host "已注册计划任务 '$taskName'（登录时后台启动 Web 服务）。"
    # 立即启动一次
    Start-ScheduledTask -TaskName $taskName
}

Write-Host ""
Write-Host "✅ 安装完成。打开浏览器访问 http://127.0.0.1:$ServePort 即可用技能化界面操控本机。"
Write-Host "卸载：Unregister-ScheduledTask -TaskName SparkHarnessAgent -Confirm:`$false"
