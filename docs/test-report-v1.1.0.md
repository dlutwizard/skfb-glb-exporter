# 测试报告 · Sketchfab 模型下载器 v1.1.0 发布前验证

> QA：严过关｜日期：2026-11-01｜被测物：`发布/Sketchfab下载器_v1.1.0.zip`
> （73,570,615 B / 1152 条目，解包到全新目录 F:\迅雷下载\_qa_v11\ 后验证，
> **非**构建中间产物；验证期间本机开发引擎 sketchfab-cli-main 全程临时改名排除干扰）

---

## 0. 最终判定：**阻断**（1 个功能缺陷 + 1 项门禁口径分歧，详见第 5 节）

- **自动化测试 66/66 全绿**（A/C/D 组，tests\ 下 7 个文件）。
- **真实下载回归 4/6 + 1 项差分验证**：UID 直连与完整链接全通（含 52 万面 4K 场景大模型），
  **skfb.ly 短链在打包 exe 内 5/5 必失败**（Bug #1）。
- 解密后端确认 **wasm**（6 次生产运行日志全部命中 `decrypt: WASM`，无一次回落 legacy）。

## 1. 测试范围与方法

| 组 | 内容 | 方法 |
|---|---|---|
| A | version 单源 / update_checker / slim 守卫 / 配置合并 | pytest（D:\Anaconda\python.exe，源码级 + mock，不真联网） |
| B | M6 六模型真实下载回归 | zip 解包→隐藏本机引擎→exe --run（QA 执行器 tests/qa_download_regress.py，psutil 采样进程树 RSS，剥离沙箱代理环境变量） |
| C | 冷启动 / --selftest / --env / --version / 关于面板 / 检查更新零联网 / 双链接并行 | pytest 冒烟 + 源码级 GUI 测试 + 真实 --run |
| D | legacy 回滚 / 无 F 盘回退 | 移除 wasm worker 强制 legacy + 受限 PATH + 源码级 PATH 剔除断言 |

## 2. 用例结论表（按组，66 条自动化用例的汇总口径）

| 编号 | 用例 | 预期 | 实际 | 结论 |
|---|---|---|---|---|
| A-01~06 | version.py 真源自洽、无 import、parse/is_newer 边界 | 一致且可解析 v 前缀/预发布 | 全部符合 | PASS |
| A-07~12 | exe FileVersion / --version / --version --json / 窗口标题 / 关于面板标题 / version.json | 四处 + 面板均 = 1.1.0 | 全部 = 1.1.0 | PASS（M5） |
| A-13 | version_info.txt 由真源生成 | FileVersion=1.1.0.0 | 一致 | PASS |
| A-14~24 | update_checker：未配置零联网（含 async）、超时 ≤6s 返回 error 不抛、全异常吞掉、404 映射、PAT 仅从配置读（env 泄漏不采用）、GitHub/json 流程 | 见 PRD R9 | 全部符合 | PASS |
| A-25~31 | slim：--dry-run 不动文件（假包+真包）、真删 S1~S5、保护清单完好、MD5 不匹配 exit 1 且零删除、对照副本缺失不删、@img/colour 在包内且被 sharp require | 见架构 S1~S6 | 全部符合 | PASS |
| A-32~37 | 真实包：S1~S5 目标已不在、binzDecrypt/wasm/sharp-win32 在位、--dry-run 对真实包 noop | 瘦身已生效 | 符合 | PASS |
| A-38~44 | 配置：APPDATA 默认 / 便携模式 / --portable / 合并式保存不抹键 / 损坏配置容错 / legacy 收敛 / PATH 剔除 | 见 R7 | 全部符合 | PASS |
| A-45 | 无 F 盘 default_out_dir 回退 | 回落 Downloads | 符合 | PASS |
| C-01~05 | --selftest OK / --env 8 旧行+3 新行且全 OK / DECRYPT_BACKEND=wasm / BUILD=1.1.0 / 冷启动×5 | 中位 ≤1.7s | **0.75s**（0.74~0.79） | PASS（M3） |
| C-06~10 | 关于面板打开/复用/未配置点检查更新零联网无弹窗/配置后异步返回/双链接并行管线（mock） | 见 R2/R9 | 全部符合 | PASS |
| C-11 | 双短链 --run --conc 2 真实并行 | 两链均成功 | 两链均 skfb.ly SSL 失败（Bug #1） | **FAIL** |
| D-01 | wasm worker 移除后 --env | DECRYPT_BACKEND=legacy | 一致 | PASS |
| D-02 | legacy 后端真实下载 | binzDecrypt.exe 出 GLB | 成功（结构与 wasm 产物一致） | PASS |
| D-03 | GUI 配置 legacy → PATH 剔除 RES | RES 不在 PATH | 符合（源码级） | PASS |

## 3. M1~M7 逐条结论

| 项 | 指标 | 目标 | 实测 | 结论 |
|---|---|---|---|---|
| M1 | onedir 体积 | ≤153 MiB | 150.1 MiB（157,422,547 B） | **PASS** |
| M2 | zip 体积 | **PRD ≤66 MB** | 70.2 MB（73,570,615 B） | **FAIL（口径分歧 #2）**：build.py 门禁写的是 ≤72 MB，与 PRD 的 66 MB 不一致；70.2 MB 过 72 门禁、超 66 目标 |
| M3 | 冷启动（--selftest 中位） | ≤1.7 s | **0.75 s**（QA ×5；构建期自报 1.02 s） | **PASS** |
| M4 | 峰值内存基线测量 | 完成并落文档 | 139~813 MB（4 样本），已写入 benchmark-v1.1.md | **PASS** |
| M5 | 版本四处一致 | 1.1.0 | version.py / exe 属性 / --version / 窗口标题（另验 version.json、build_info） | **PASS** |
| M6 | 六模型 100% 出 GLB | 100% | UID/完整链接 5/5 全通（含 52 万面车模、skins=58 骨骼、materials=60 多材质、4K 贴图）；**skfb.ly 短链 0/5** | **FAIL（Bug #1）** |
| M7 | 一键构建可复现 | 单命令出包+报告 | build.py 产出 sizes.json / build-report / version_info / 使用说明注入齐全（QA 未重跑构建，以构建报告+产物一致性佐证） | **PASS** |

## 4. 六模型回归结果（M6，逐 100ms 采样进程树 RSS）

| # | 类别 | UID | 峰值 RSS | node | 耗时 | GLB | meshes/images/materials/skins | 后端 | 结果 |
|---|------|-----|---------|------|------|-----|-------------------------------|------|------|
| 1 | 短链 | p9HSv | — | — | — | — | — | — | **失败（Bug #1）** |
| 2 | 短链 | 6S7uo | — | — | — | — | — | — | **失败（Bug #1）** |
| 3 | 全链路基线 | 1053729ad…76deb | 327 MB | 264 MB | 298.4s | 24.18 MB | 3 / 6 / 3 / 0 | wasm | 成功 |
| 4 | 全链路+贴图 | 7c60b4d1…2fc0 | 440 MB | 378 MB | 128.6s | 12.41 MB | 11 / 1 / 1 / 0 | wasm | 成功 |
| 5 | 骨骼/动画（补选） | 9e82fabf…64eab（S.W.A.T. Operator） | **813 MB** | 744 MB | 338.5s | 133.34 MB | 42 / 22 / 12 / **35** | wasm | 成功 |
| 6 | 材料数≥5（补选） | a4ca0e38…8029b（Chibi Gear Solid） | 139 MB | 69 MB | 223.4s | 3.48 MB | 63 / 60 / **60** / 0 | wasm | 成功 |
| + | 差分：p9HSv 本体 | 经 `sketchfab.com/s/p9HSv` | 364 MB | — | 171.1s | 37.89 MB | 74 / 9 / 9 / **58** | wasm | 成功 |

- 4K 贴图场景：行 3/4/5/6 均含 4096×4096 descramble 成功记录；瘦身删 DLL 后 sharp 编码链路完好，GLB images 计数与日志 tex 口径一致（逐一核对）。
- 补选理由与弃选记录已写入 docs/regression-models.md（Buster Drone 无骨骼蒙皮、Noble Craftsman 仅 1 材质，均弃用）。
- **animations=0 属 v1.0.0 固有行为**（v1.0.0 对照产物逐字段一致），非本次回归；v1.3 重写 GLB 写出器时需专项。

## 5. 缺陷清单

### Bug #1（阻断 M6）：打包 exe 解析 skfb.ly 短链 100% SSL 失败
- 现象：`URLError: [SSL: UNEXPECTED_EOF_WHILE_READING]`，5/5 次失败（3 个独立进程 + 1 次双链并发 + 1 次重试）。
- 差分定位：**仅 skfb.ly 域失败**。同一 exe 内 sketchfab.com / api.sketchfab.com / CDN 全部正常；
  同机系统 Python（含同款 certifi 补丁）访问 skfb.ly 3/3 成功。疑为打包 OpenSSL 3.5.x 与
  skfb.ly（awselb/2.0）的 TLS 握手兼容性问题，需工程师在打包环境复现定位。
- 复现：解包 zip → `Sketchfab下载器.exe --run p9HSv --out <目录> --conc 2`（必失败）；
  对照：`Sketchfab下载器.exe --run sketchfab.com/s/p9HSv ...`（成功）。
- 影响：用户粘贴 skfb.ly 短链即失败；UID、完整链接、sketchfab.com/s/ 均不受影响。
- 处置建议（二选一，请主理人裁决）：① 工程师修复后重发 v1.1.1；② 若短期难定位，
  可在 GUI 加「短链解析失败请改粘完整链接」提示并把 M6 短链行降级为 P1，先放行。

### #2（口径分歧，需裁决）：M2 门禁 72 MB vs PRD 66 MB
- build/build.py 的门禁实现为 ≤72 MB（构建报告 PASS），PRD M2 写 ≤66 MB。实测 70.2 MB：
  按前者 PASS、按后者 FAIL。两文档必须对齐一个数字。

### #3（观察项，不阻断）：`--run` 模式不读取 decrypt_backend 配置
- 一键回滚开关（apply_node_visibility）只在 GUI 启动/开始下载时生效；`--run` 无界面路径
  恒为 wasm。属架构设计口径内（T02 归 GUI），但排障时用 --run 验证 legacy 会被误导。

### #4（观察项，不阻断）：legacy 回滚有效性依赖「本机无其他 Node」
- 本机 PATH 上有系统 node 时，配置 legacy 后引擎 `which("node")` 仍命中（本次用「移除
  wasm worker」强制 legacy 才完成验证）。真实用户机器通常无 Node，风险低。
- 另外：binzDecrypt.exe 在当前 Sketchfab 页面上**实测可用**（legacy 产物与 wasm 结构一致），
  v1.2 删除它之前的风险评估可参考。

### #5（既有行为，非缺陷）：GLB 无动画轨道
- 即使 skins=35/58 的骨骼样本，`animations=0`；v1.0.0 与 v1.1.0 输出逐字段一致。

## 6. 智能路由判定

- 自动化测试首轮 5 处失败均为**测试自身问题**（断言口径、Tk 根窗口复用、引用错误），QA 自行修复，Round 2 全绿（66/66）。
- **源码缺陷 1 项（Bug #1）→ 报 team-lead 转工程师**：sketchfab_gui.py::resolve_and_uid /
  打包环境 TLS，建议优先在打包 OpenSSL 3.5.x 下复现 skfb.ly 握手。
- 其余为文档口径与观察项，无其他源码修改要求。

## 7. 测试产物与清理

- 测试脚本（保留）：`tests/`（conftest.py、test_version.py、test_update_checker.py、
  test_slim.py、test_config.py、test_release_smoke.py、test_gui_about.py、
  qa_download_regress.py、qa_legacy_check.py）
- 数据（保留）：`docs/qa-v1.1.0/`（regress_result.json / regress_round2.json /
  regress_sharan.json / legacy_result.json / out_dual.log / out_legacy3.log / out_v100.log）
- 已更新文档：docs/benchmark-v1.1.md（6 行表）、docs/regression-models.md（第 5/6 行补选）
- 临时目录 F:\迅雷下载\_qa_v11\（解包体/输出/旧版对照）测试完成后删除；
  发布\ 与 build\dist_dir\ 未做任何改动。
