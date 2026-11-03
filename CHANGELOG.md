# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 结构，
版本号遵循语义化版本（`MAJOR.MINOR.PATCH`）。

打包时本文件会被复制到 `_internal/CHANGELOG.md`，「关于」面板离线读取它，
因此日志正文必须是纯文本 Markdown，不要写图片或外链依赖。

## [未发布]

面向开源的整理，不改变已发布 v1.1.2 的功能行为。

### 移除
- **不再分发 `binzDecrypt.exe`**（17.9 MB）。该二进制来自第三方项目且上游未提供任何明确许可，
  继续随包分发属于未授权再分发。删除后解密链路只剩 `Node.js + tools/wasm/decrypt.wasm` 一条，
  功能无损失——WASM 本来就是生产环境实际生效的路径，`binzDecrypt.exe` 仅在找不到 Node 时才会被调用，
  而 `node.exe` 始终随包提供。
- 瘦身规则 S6 由「默认关闭」改为「默认开启」，构建时自动剥离该文件（WASM 缺失则拒绝删除并报错）。

### 变更
- 构建参数 `--no-legacy-decrypt` 改为 `--keep-legacy-decrypt`（语义反转：现在是「想保留才需要显式声明」）。
- 清除源码中的开发机绝对路径：`app/sketchfab_gui.py` 的引擎/Node 查找改为
  「随包 → 仓库 → `SFD_CLI_DIR`/`SFD_NODE_EXE` 环境变量 → PATH」，`build/build.py` 的 node 查找同理。
- 默认输出目录不再试探 `F:\迅雷下载`，只用系统下载目录。

### 新增
- `LICENSE`（MIT）、`THIRD-PARTY-NOTICES.md`、`app/extlib/LICENSE`（上游 MIT 声明）、
  `.gitignore`、`.gitattributes`、`README.md`。

## [1.1.2] - 2026-11-01

### 修复
- **打包出来的 exe 被 Windows Defender 隔离**（`Trojan:Win32/Bearfoos.B!ml`，机器学习误报）：用户解压后点开就被删，属于"发出去也用不了"的严重问题。定位过程：最小 PyInstaller 程序不报毒 → 加上同样的图标/版本信息/文件名也不报毒 → 旧版单文件 exe 不报毒 → 仅本次 onedir 构建报毒；最终确认触发点是 **PyInstaller 6.22.3 的引导器**，改用 **6.10.0** 打包后同一份代码扫描干净、完整下载流程（短链 → 解密 9 张贴图 → 37.9 MB GLB）全程未被拦截
- 构建脚本新增版本闸门：`build/build.py` 只允许 6.10.x，其他版本直接报错并说明原因（确需强制可设 `SFD_ALLOW_PYI=1`）

### 变更
- 版本号同步到 1.1.2（窗口标题 / 关于面板 / `--version` / exe 属性四处同源）

## [1.1.1] - 2026-11-01

### 修复
- **短链 `skfb.ly` 在打包环境必现 TLS 失败**（`SSL: UNEXPECTED_EOF_WHILE_READING`，QA 实测 5/5）：同一个 exe 访问 `sketchfab.com` 完全正常，说明问题出在 `skfb.ly` 一侧的握手兼容性。现在解析短链时先把 `https://skfb.ly/<code>` 改写成官方落地页 `https://sketchfab.com/s/<code>` 再请求（**只替换域名与路径前缀，code 原样保留**——短 code 区分大小写），改写后拿不到 UID 才回退原始 `skfb.ly` 地址；两条路径都失败才报错，并提示「改粘完整链接（sketchfab.com/3d-models/...）」
- 版本号同步到 1.1.1（窗口标题 / 关于面板 / `--version` / exe 属性四处同源）

### 变更
- **M2 体积门禁口径统一为 ≤72 MB**（与 PRD 对齐；实测 70.2 MB）。PRD 里 ≈66 MB 的理想值改为 **v1.2 目标**，不再作为 v1.1 系列的通过条件（`node.exe` 已 UPX，实测不可再压，要降到 66 MB 以下必须去掉 Node，属于 v1.2/v1.3 范围）

## [1.1.0] - 2026-11-01

### 新增
- 版本号成为程序的一等公民：窗口标题、副标题右侧、关于面板、`--version`、exe 文件属性五处同源（`app/version.py` 为唯一真源）
- 「关于 / 诊断」面板：版本、构建日期、数据目录、配置路径、更新日志前 5 条、依赖自检表格，缺依赖时给人话指引
- `--version` 与 `--version --json` 两个命令行输出
- `--portable` 开关：强制把配置写在程序目录（便携模式）
- `decrypt_backend` 配置项（`auto` / `wasm` / `legacy`）：一键把解密路径回滚到 `binzDecrypt.exe`，不改引擎一行代码
- `build/build.py` 一键构建：出绿色版 zip + 体积分项报告 + 体积账本
- `build/slim.py` 瘦身规则引擎：每条规则带可判定守卫（MD5 相等 / 引用扫描），支持 `--dry-run` 预演
- `tools/bench.py` 测量工具（仅开发用，不进包）：启动耗时、峰值 RSS、单列 `node.exe` 峰值、实际解密后端
- `docs/upgrade.md` 覆盖解压升级说明，`docs/regression-models.md` 回归模型固化清单

### 变更
- **引擎现在能找到随包 `node.exe`**：GUI 启动时把资源目录前置进 `PATH`。此前引擎用 `shutil.which("node")` 找 Node，打包后必然找不到，实际一直在走 legacy 解密分支；修复后默认走 WASM 现代路径，`binzDecrypt.exe` 变为真正的兜底（保留在包内，作为回滚手段）
- `--env` 改为调用统一的自检实现，新增 `NODE_LOOKUP` / `DECRYPT_BACKEND` / `BUILD` 三行，原有 `RES` / `BASE` / `ENGINE` / `NODE` / `CONVERT` / `SHARP` / `WASM` / `EXTLIB` 行保持不变
- 配置文件读写规则改为「只写一处、读时兜底另一处」；`save_cfg` 改为**合并语义**，不再整份覆盖（此前会抹掉新加的配置键）
- `使用说明.txt` 改为构建时从模板生成，注入真实版本号/日期/体积/启动耗时/更新日志摘要，不再有写死的数字

### 修复
- 引擎 WASM 解密路径被绕过（`shutil.which("node")` 在打包环境下恒为假），导致新格式模型依赖「often broken」的 legacy 后端
- 覆盖解压升级后配置丢失：新版默认把配置写入 `%APPDATA%\Sketchfab下载器\`，程序目录内配置视为便携模式

### 移除
- 包内重复的 `libvips-42.dll`（18.1 MiB）与 `libvips-cpp-8.18.7.dll`（0.3 MiB）：`node_modules/@img/sharp-win32-x64/lib/` 下已有一模一样的副本，删除前用 MD5 相等断言守卫
- 包内 `@img/sharp-wasm32`（8.9 MiB）、`@emnapi`（0.4 MiB）、`tslib`（0.1 MiB）：win32-x64 原生绑定在位时永不加载，删除前做引用扫描
- 仓库内手写的 `app/使用说明.txt`（改由 `app/使用说明.template.txt` 生成，避免双真源）
- v1.1 不再产出 82 MB 的单文件 exe（启动约 40 秒，只有调试价值），`--single-file` 仍保留

## [1.0.0] - 2026-10-31

首个对外分发的形态（本版为追认登记，此前无版本概念，`build/version_info.txt` 里写死 `1.0.0.0`）。

### 新增
- 桌面 GUI（tkinter）：多链接一次粘贴、1-5 并发下载、进度条、彩色日志
- 下载引擎（`sketchfab-cli` 内核）：短链跳转解析、`.binz` 分片抓取与解密
- 贴图还原 + osgjs → glTF 转换，产出内嵌材质的 `.glb`
- 下载前显示面数、授权、作者是否开放官方下载
- `--run` / `--env` / `--selftest` 三个命令行模式
- 打包为「解压即用」的 onedir 绿色版 zip（免安装 Python / Node / 运行库）
- 内置 certifi 证书，绕开打包后 OpenSSL 读取 Windows 证书存储报 `ASN1: NOT_ENOUGH_DATA` 的问题
- 输出目录自适应：用户目录不存在时自动挑一个「一定存在且可写」的目录

### 已知问题
- 包内重复携带一份 `libvips-42.dll`（18.1 MiB），onedir 合计 181 MiB、zip 81.5 MB
- 单文件 exe 启动约 40 秒（每次都要把全部内部文件解压到临时目录）
- `使用说明.txt` 里的「150MB / 40 秒」是估算值，与实测不符
- 无版本号、无更新日志、无构建脚本，打包靠手工敲 PyInstaller 长命令
