# Spark Harness 安卓客户端（APK）

手机端 App：连接并驱动你电脑/服务器上运行的 **Spark Harness** 服务端，复用同一套"技能组合界面"，在手机上调度 Agent 操控电脑、手机、浏览器与 3D 工具。

> 这是 **Android Studio 工程源码**，需用 Android Studio（或装有 Android SDK 的环境）编译为 APK。
> 本仓库的沙箱环境无 Android SDK，因此 APK 未在此预编译——请在本机按下方步骤构建。

## 架构

```
Spark Harness 服务端 (PC/服务器, :8080)
        ▲  REST (/api/chat, /api/skills, /api/health, /api/plugins)
        │
Spark Harness 安卓 App
  ├─ MainActivity    —— WebView 加载服务端仪表盘（界面=技能组合，跨端一致）
  ├─ SettingsActivity—— 服务端地址 / 令牌 / 开机自启 / 常驻服务
  ├─ ConnectionService— 前台连接服务 + 常驻通知，随时回到控制台
  ├─ BootReceiver    —— 开机自动连接（可选）
  └─ net/HarnessClient—— Retrofit + OkHttp + 协程，Bearer 令牌鉴权
```

- 主界面用 **WebView** 直接渲染服务端 Web 仪表盘，因此"界面完全由技能/插件组成"在手机端同样成立，无需为每端重写 UI。
- 原生层负责：连接管理、鉴权令牌注入（Cookie/Header）、前台常驻服务、开机自启。
- 服务端地址填你 PC 的**局域网 IP**（如 `http://192.168.1.10:8080`），手机与服务端需在同一局域网；或经内网穿透/公网暴露。

## 构建 APK

### 方式一：Android Studio（推荐）
1. 打开 Android Studio → `File → Open` → 选择本 `android/` 目录。
2. 等待 Gradle 同步（会自动下载 Gradle 8.2 与依赖）。
3. `Build → Make Project`（或 `Build → Build Bundle(s) / APK(s) → Build APK`）。
4. 生成的 APK 位于 `android/app/build/outputs/apk/debug/app-debug.apk`，安装到手机即可。

### 方式二：命令行（需已安装 Android SDK 并配置 ANDROID_HOME）
```bash
cd android
./gradlew assembleDebug        # 生成 debug APK
# 或 ./gradlew assembleRelease  # 需自行配置签名
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

> 若 Gradle 发行包下载慢，可编辑 `android/gradle/wrapper/gradle-wrapper.properties` 的
> `distributionUrl` 改为国内镜像，例如
> `https://mirrors.cloud.tencent.com/gradle/gradle-8.2-bin.zip`（路径以镜像实际为准）。

## 使用
1. 在电脑端启动服务端：`python -m spark_harness serve --port 8080 --host 0.0.0.0`
2. 手机 App 打开"设置"，填入 `http://<电脑局域网IP>:8080`，可选填令牌，保存。
3. 自动加载仪表盘：用手机下达指令，由 Agent 调度技能中的工具执行。

## 权限说明
- `INTERNET`：连接服务端。
- `FOREGROUND_SERVICE` / `POST_NOTIFICATIONS`：常驻连接通知。
- `RECEIVE_BOOT_COMPLETED`：开机自动连接（仅在设置中开启时生效）。

## 依赖
- AndroidX（AppCompat / Material Components / Lifecycle / ConstraintLayout）
- Retrofit 2.9 + Gson Converter + OkHttp 4.12
- Kotlin 1.9 / Coroutines
- minSdk 26，targetSdk 34，compileSdk 34
