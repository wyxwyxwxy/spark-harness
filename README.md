# Spark Harness

对标 **Deepseek Harness** 的通用 Agent 编排框架。核心目标：**尽量大的上下文、尽量省的 token、尽量少的工具调用、尽量高的缓存命中率**，并对 **DeepSeek / GLM / Kimi** 三家做了专门优化。

---

## 一、为什么能"拉满"这几个指标

| 目标 | 做法 |
|---|---|
| **尽量大的上下文** | 稳定前缀（system + 环境简报 + 历史摘要）放在最前且整轮不变；对话只 append 在后，不改动前面，因此前缀始终可复用。 |
| **尽量省的 token** | 预算守护（`max_input_tokens`）+ 历史压缩（把最旧轮次交给模型摘要化）+ 工具结果截断回传（路径/摘要而非全文）。 |
| **尽量少的工具调用** | 单轮内模型返回的多个 `tool_calls` **并行执行**；相同 `(工具, 参数)` 命中**结果缓存**，直接复用、不再执行、不再占上下文。 |
| **尽量高的缓存命中率** | 稳定前缀锚点 + 三家各自原生缓存参数（见下）+ 会话内前缀复用。实测 8 轮多轮对话命中率从 0% 升到 **83%+**。 |

---

## 二、极致上下文 / Token 压缩层（论文背书：上下文省 ≥95%、用量减 ≥85%）

这一层是把"每个 token 用到极致"的核心，全部来自经同行评审的论文方法，并在 `compression.py` / `memory.py` / `context.py` / `agent.py` 中落地：

| 技术 | 论文依据 | 在 Harness 中的实现 |
|---|---|---|
| **外部记忆 + 检索即压缩** | MemGPT/Letta (arXiv:2310.08560)、Mem0（token 成本 **>90%** 削减）、RECOMP (ICLR'24)、Anthropic 子代理隔离（探索数万 token，只回传 1–2k 摘要） | `MemoryStore`：完整文件/工具输出**外置**，上下文只留摘要 + 检索片段；`recall(id)` 按需取回全文 |
| **抽取式压缩** | RECOMP（抽取式 compressor，选有用句子）、Selective Context（自信息过滤，context **减 50%** 几乎无损） | `extractive_compress`：按信息量挑句子，保留含数字/专有名词/与查询重叠的高信号句 |
| **头尾保留（结构保护）** | 长输出保开头与结尾，中段省略，避免丢关键结论 | `head_tail`：保留 head+tail，中段标 `[已省略 N 字符]` |
| **词级轻压** | Selective Context 思路（去冗余/低信息 token） | `lexical_cleanup`：剥 ANSI、折叠空白、去连续重复行 |
| **极简工具 Schema + 按需详情** | 工具定义是每请求的固定开销（常占数千 token） | `minimal_schema`（只留 name/类型/必填）+ `compact_index`（一句话索引）+ `tool_help` 按需取完整 Schema |
| **95% 阈值递归压缩** | MemGPT 自动压缩（Claude Code 在 ~95% 窗口压缩）、递归摘要 | `ContextManager.maybe_compact`：逼近预算时把最旧历史递归摘要，外置内容不丢 |
| **语义缓存（与 prompt 缓存正交）** | GPTCache：相似查询跳过整次调用（**40–70%** 调用削减） | `ToolResultCache` 结果去重（精确 + 近重复） |

### 实测对照基准（`python -m spark_harness compress`）

模拟长程任务：读一份 **~8.9 万 token** 的大文档 + 两段各 **~9k token** 的日志，跑 11 轮对话，朴素全量 vs Spark v2：

```
轮次        朴素上下文      Spark v2
...
峰值上下文: 朴素 108951 tok | v2 3633 tok | 省 96.7%
累计输入token: 朴素 1008727 | v2 30227 | 减 97.0%
目标校验: 上下文省≥95% → 达标; token减≥85% → 达标
```

> 该数字**尚未计入 prompt 缓存**——稳定锚点（system+索引）后续轮次还会以缓存读取计费，实际账单层面的 token 用量会更低。

### 论文索引
- Selective Context (Li et al., 2023, arXiv:2304.12102)：自信息过滤，context 减 50% 几乎无损。
- LLMLingua / LongLLMLingua (Microsoft, EMNLP'23 / ACL'24)：最高 20× 压缩；RAG 用 1/4 token 还提升 21.4%。
- MemGPT/Letta (arXiv:2310.08560)：虚拟内存 + 自动压缩（~95% 窗口）。
- Mem0 (2025)：外部记忆，相对全量对话 token 成本 **降 >90%**、p95 延迟 -91%。
- RECOMP (ICLR'24)、AttnComp（17×）、TeaRAG（61% token 削减且精度反升）。
- GPTCache（语义缓存）：与 prompt 缓存正交，相似查询跳过整次调用。

---

## 三、三家厂商专门优化

所有适配器统一走 OpenAI 兼容 `chat/completions`，差异只在**缓存参数注入**与 **usage 解析**：

- **DeepSeek · V4.1-Flash-1017**（`deepseek-flash`，10/17 发布，552B MoE、1M 上下文、原生视觉）：前缀自动缓存（64 token 分块），稳定前缀即命中；**动态工具调用**原生支持 OpenAI 兼容的 `tool_choice`（`auto`/`none`/`required`/指定函数）与 `parallel_tool_calls`（默认开启）；Responses API 还内置 `web_search` / `web_extractor` / `code_interpreter`（Harness 用本地等价实现，见 `builtins.py`，任何端点都能用）；开启思考模式后 `reasoning_content` 会随 `tool_calls` 返回，续轮**必须原样回传**（已在 base 处理，否则报 400）。返回 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`。
- **GLM / 智谱** (`open.bigmodel.cn`)：注入 `cache: true` + `cache_ttl`（秒）。命中部分低价计费。
- **Kimi / Moonshot** (`api.moonshot.ai`)：注入 `prompt_cache_options={"mode":"implicit","ttl":"5m"|"1h"}`。连续 agent 任务用 `5m`（命中免费续期）；长会话/有人工介入用 `1h` 避免过期重写。返回 `cached_tokens` / `cache_write_tokens`。

> 三家都会把原始 usage 归一化到统一的 `cache_read` / `cache_write`，`CacheStats` 给出全局命中率与省下的 token 数。

---

## 三-B、动态工具调用 + 超时熔断（让 Harness 实时、稳定地操控一切）

**动态工具调用**（`tool_choice` + 内置动态工具 + 运行时注册）：

- `tool_choice`：用 `--tool-choice auto|none|required|<函数名>` 控制模型调用策略；末轮自动切 `none` 强制收尾，避免工具死循环。
- **内置动态工具**（对应 DeepSeek 原生能力，本地实现保证跨端点可用）：
  - `web_search`（DuckDuckGo 免 Key 搜索）、`web_extractor`（抓网页抽正文）、`code_interpreter`（沙箱跑 Python）。
- **运行时注册**：`agent.register_tool({...})` 可在运行中挂载新工具，无需重启——能力可热插拔。

**超时熔断执行器**（`runtime.py`，每个工具独立熔断器）：

- 单工具**硬超时**（`tool_timeout`，默认 30s，expensive 工具 90s）watch-dog：卡死的工具立即取消，绝不拖垮 Agent 循环。
- **断路器**：同一工具连续失败达阈值（默认 3 次）自动 `OPEN`，之后**快速失败**而非无限重试；冷却后 `HALF_OPEN` 试探，恢复则闭合。
- **并发上限**（`tool_max_concurrency`，默认 8）：一次性几十个 `tool_calls` 也不会打爆下游。
- 这正对应 DeepSeek Agent Function Calling 指南对"并行/超时/失败"的生产化要求。

---

## 三-C、操控电脑 / 手机 / 浏览器 / Android（融合进操作系统）

| 域 | 工具 | 说明 |
|---|---|---|
| **电脑 GUI** | `computer_screenshot` / `computer_mouse` / `computer_keyboard` / `computer_open_app` / `computer_screen_size` | 鼠标移动/点击/拖拽、键入/组合键、截图、启动程序。优先 `pyautogui`，**Windows 下用 ctypes+user32 原生回退**（无需 pywin32），配合 `install_windows.ps1` 可常驻为后台 Agent |
| **浏览器** | `browser_navigate` / `browser_click` / `browser_fill` / `browser_screenshot` / `browser_evaluate` | 真实 Playwright 渲染动态页面、点击、填表、执行 JS，操控 Web 软件 |
| **安卓** | `phone_screenshot` / `phone_tap` / `phone_swipe` / `phone_input_text` / `phone_keyevent` / `phone_open_app` / `phone_install` / `phone_list_packages` / `phone_screen_size` / `phone_shell` | 经 **ADB** 全功能操控安卓设备（截图/点滑/输入/按键/启动/装包/列包/执行命令） |
| **3D** | `make_3d_model` | cube/sphere/torus/cylinder，优先 GLB，否则标准 .obj（Blender/Three.js 直接加载） |
| **联网/代码** | `web_search` / `web_extractor` / `code_interpreter` | 动态内置工具，见三-B |

所有依赖缺失时**优雅降级**（返回安装指引而非崩溃）。

---

## 三-D、技能化：界面完全由技能组成

能力以"技能"为单位组合——一个技能 = 一组工具 + 一段提示词 + 一段 UI 描述（`spark_harness/skills/*.json`）。加载后：

- Agent 的可用工具集由"已启用技能"决定；
- **Web 仪表盘的界面由 `ui.py` 按技能动态生成**（每个技能渲染成一个能力面板，列出发它提供的工具）；
- 新增能力只需放一个技能文件，**不用改代码**。

内置技能：`web-browsing` / `android-control` / `computer-control` / `3d-modeling` / `dev-tools`。

```bash
python -m spark_harness skills          # 列出已加载技能
python -m spark_harness serve --port 8080   # 启动技能驱动的 Web 界面（界面即能力）
```

---

## 三-E、融合进 Windows

`install_windows.ps1`（管理员 PowerShell 运行）会：建 venv → 装 Harness + `pyautogui` + `playwright` → 注册**登录时后台启动 Web 服务**的计划任务。装好后打开 `http://127.0.0.1:8080` 即可用技能化界面直接操控本机桌面。卸载：`Unregister-ScheduledTask -TaskName SparkHarnessAgent`。

## 三-F、万物皆可插件（Plugin System）

插件系统让**任何能力**都能以插件形式扩展，无需改核心代码。一个插件（目录）可贡献五类扩展点：

| 贡献类型 | 说明 |
|---|---|
| **工具 (tools)** | `@tool` 装饰的 async 函数，与内置工具完全一致 |
| **技能 (skills)** | 决定界面面板与能力边界（同 `skills/*.json`） |
| **Provider** | 扩展可用 LLM 厂商后端 |
| **UI 面板** | HTML 片段，注入 Web 仪表盘（界面完全由技能 + 插件组成） |
| **钩子 (hooks)** | 生命周期回调：`pre_tool_call` / `post_tool_call` / `on_agent_start` / `on_agent_end` / `on_message` |

插件只需一个目录 + `plugin.yaml`（清单）+ `plugin.py`（入口，`setup(api)` 贡献能力）。`serve` / `run` / `chat` 启动时会自动扫描并加载 `spark_harness/plugins/` 下的所有插件。

```bash
# 列出已加载插件
python -m spark_harness plugins

# 一键生成插件骨架（含工具+技能+面板+钩子示例）
python -m spark_harness plugin create my-plugin

# 启动时额外加载指定目录的插件
python -m spark_harness serve --plugins-dir /path/to/plugins

# 开发插件：开启目录监视，改插件文件自动热重载（无需重启）
python -m spark_harness serve --plugin-watch
```

**热重载**：`POST /api/plugins/reload` 可在进程运行中重扫插件目录，新增/修改的插件即时生效（工具、技能、Provider、UI 面板、钩子全部重建）；`serve --plugin-watch` 则自动监视目录变化并热重载，插件开发体验类 hot module reload。

内置示例插件：

- `spark_harness/plugins/system-monitor/`：贡献 `system_metrics` 工具、一个技能面板、一个 UI 面板与一个 `post_tool_call` 钩子（演示 工具/技能/UI面板/钩子 四类扩展点）。
- `spark_harness/plugins/example-provider/`：贡献一个自定义 LLM 厂商 `echo`（`api.register_provider`），演示第五类扩展点 **Provider**。设置 `SPARK_PROVIDER=echo` 即可选用，无需任何 API Key，可直接驱动 Agent（见 `tests/test_harness.py::test_plugin_provider_extension_point`）。

> 五类扩展点均已由内置插件或脚手架（`plugin create`）覆盖，真正做到"万物皆可插件"。

## 三-G、安卓 APK 客户端（不止 EXE）

除 Windows（`install_windows.ps1`）外，还提供了 **Android Studio 工程**（`android/`），可编译为 APK，在手机上连接并驱动你电脑/服务器上的 Harness 服务端。

**两种方式获得 APK**：
1. **本地构建**：Android Studio 打开 `android/` 目录直接 Run，或命令行 `cd android && ./gradlew assembleDebug`（需 JDK 17 + Android SDK 34）；
2. **CI 自动构建**：推送本仓库到 GitHub，`.github/workflows/android-build.yml` 会自动编译并上传 APK 工件（打 `v*` tag 还会自动附到 Release）。

- 主界面用 **WebView** 直接渲染服务端仪表盘 → 手机端与桌面端"界面完全由技能/插件组成"一致；
- 原生层负责连接管理、令牌鉴权（Cookie/Header）、**前台常驻连接服务 + 通知**、**开机自动连接**；
- 通过 Retrofit + OkHttp + 协程 调用 `/api/chat`、`/api/skills`、`/api/health`、`/api/plugins`。

> 本仓库的 Linux 沙箱**无 Android SDK**，APK 未在此预编译。请用 Android Studio 打开 `android/` 目录构建（详见 `android/README.md`）：`Build → Build APK`，或命令行 `./gradlew assembleDebug`（需自备 Android SDK 并配置 `ANDROID_HOME`）。

---

## 四、快速开始

```bash
cd spark-harness
pip install -r requirements.txt        # 只需 requests 即可跑通核心

# 1) 无需 Key 的本地演示（脚本化 Mock，验证工具调用与流程）
python -m spark_harness demo

# 2) 实测缓存命中率随轮次上升 + 工具结果去重
python -m spark_harness bench

# 3) 对照基准：上下文省≥95% / token减≥85%（论文背书）
python -m spark_harness compress

# 4) 列出内置工具 / 技能 / 插件
python -m spark_harness tools
python -m spark_harness skills
python -m spark_harness plugins

# 5) 启动技能 + 插件驱动的 Web 界面（界面完全由技能/插件组成）
python -m spark_harness serve --port 8080

# 6) 真实调用（设置对应 Key 与厂商）
export DEEPSEEK_API_KEY=sk-xxx
python -m spark_harness run --task "生成一个球体 3D 模型并保存到 /workspace/ball.obj" --tool-choice auto

# 交互式
export MOONSHOT_API_KEY=sk-xxx
SPARK_PROVIDER=kimi python -m spark_harness chat
```

环境变量：`SPARK_PROVIDER`(deepseek/glm/kimi/mock)、`SPARK_MODEL`、`SPARK_API_KEY`、`SPARK_BASE_URL`、`SPARK_SYSTEM_PROMPT`、`SPARK_MAX_INPUT_TOKENS`、`SPARK_CACHE_TTL`。

> Windows 一键融合部署：以管理员 PowerShell 运行 `install_windows.ps1`。
> 安卓端：用 Android Studio 打开 `android/` 构建 APK（详见 `android/README.md`）。

---

## 五、内置工具（操控电脑 / 手机 / 软件 / 3D / 浏览器 / 安卓）

> 共 **29 个**内置工具，按"技能"分组（见三-D）；插件可继续扩展（见三-F）。完整列表：`python -m spark_harness tools`。

| 分类 | 工具 | 能力 |
|---|---|---|
| 电脑 | `shell` | 本地执行命令，操控电脑、运行任何软件 |
| 电脑 | `read_file` / `write_file` / `list_dir` | 文件系统读写与浏览 |
| 电脑 GUI | `computer_screenshot` / `computer_mouse` / `computer_keyboard` / `computer_open_app` / `computer_screen_size` | 鼠标/键盘/截图/启动程序（pyautogui + Windows 原生回退） |
| 网络 | `http_get` | 调用任意 Web API |
| 浏览器 | `browser_navigate` / `browser_click` / `browser_fill` / `browser_screenshot` / `browser_evaluate` | Playwright 渲染动态页面、点选、填表、执行 JS |
| 安卓 | `phone_screenshot` / `phone_tap` / `phone_swipe` / `phone_input_text` / `phone_keyevent` / `phone_open_app` / `phone_install` / `phone_list_packages` / `phone_screen_size` / `phone_shell` | 经 **ADB** 全功能操控安卓设备 |
| 3D | `make_3d_model` | 生成 3D 模型（cube/sphere/torus/cylinder），优先 GLB，否则标准 .obj |
| 动态内置 | `web_search` / `web_extractor` / `code_interpreter` | 联网搜索 / 网页抽取 / 沙箱 Python（对应 DeepSeek 原生动态工具） |
| 辅助 | `tool_help` / `recall` | 按需取完整 Schema / 取回外置记忆全文 |

---

## 六、扩展自己的能力

两种方式，推荐**插件**（零改核心、可独立分发）：

**方式 A：插件（万物皆可插件）** —— 见三-F。一个目录即可贡献工具/技能/Provider/UI 面板/钩子，示例脚手架：`python -m spark_harness plugin create my-plugin`。

**方式 B：直接写 @tool**（适合只想加一个工具）：

```python
from spark_harness.registry import tool

@tool(
    name="my_tool",
    description="做什么",
    parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
)
async def my_tool(x: str) -> str:
    return f"处理结果: {x}"
```

用 `@tool` 装饰的 async 函数会被 `build_default_registry()` 自动收集，无需手动注册。

---

## 七、项目结构

```
spark_harness/
  config.py        配置 + 三厂商预设 + 环境变量解析 + 熔断/动态工具参数
  tokenizer.py     离线 CJK 友好 token 估算（可选 tiktoken 增强）
  cache.py         工具结果缓存 + 跨请求缓存命中统计
  context.py       上下文管理：稳定前缀锚点 + 预算守护 + 历史压缩
  runtime.py       超时熔断执行器（CircuitBreaker + ToolExecutor）
  compression.py   抽取式/头尾保留/词级压缩（论文背书）
  memory.py        外部记忆库（外置 + BM25 检索）
  providers/       base + deepseek / glm / kimi / mock 适配器
  tools.py         Tool 基类 + 注册表 + 运行时注册 + 极简 Schema
  tools_impl.py    电脑/文件/网络/3D 工具
  computer.py      电脑 GUI 操控（鼠标/键盘/截图/应用，Windows 原生回退）
  android.py       安卓 ADB 操控
  browser.py       Playwright 浏览器操控
  builtins.py      动态内置工具（web_search/web_extractor/code_interpreter）
  plugin.py        插件系统（PluginManager / HookRegistry / PluginAPI）
  skill.py         技能加载（skills/*.json）
  ui.py            按技能+插件生成 Web 仪表盘 HTML
  app.py           Agent 装配（CLI 与 serve 共用，合并插件贡献）
  server.py        Flask 服务化（技能/插件驱动界面 + /api/chat/skills/plugins/health）
  agent.py         ReAct 编排循环（并行/结果缓存/压缩/熔断/动态工具/钩子）
  registry.py      自动收集 @tool + 内置工具挂载
  cli.py           demo / bench / compress / tools / skills / plugins / plugin / run / chat / serve
  skills/          技能定义（web-browsing / android-control / computer-control / 3d-modeling / dev-tools）
  plugins/         插件目录（含示例 system-monitor）
android/          Android Studio 工程（APK 客户端，详见 android/README.md）
install_windows.ps1  Windows 融合部署脚本
tests/            自测（无需 Key，15 项）
```

> 实测数据：8 轮多轮对话前缀缓存命中率 0% → 83.1%；相同工具调用第二次命中结果缓存；上下文省 96.7% / token 减 97.0%；熔断与超时执行器通过单测；插件系统、动态注册、技能/插件界面均通过单测。
