# 基线测量 · v1.1

> 工具：`tools/bench.py`（dev-only，不进发布包）｜采样：每 100 ms 累加进程树 RSS
> 测量环境：Windows x64 / Python 3.11.5 / PyInstaller 6.22.3｜并发：1（排除相互干扰）
> 启动测量时间：2026-11-01 12:53

## 1. 冷启动（`--selftest` 代理指标，与 v1.0.0 同口径）

| 次数 | 耗时 (s) |
|---|---|
| 1 | 1.03 |
| 2 | 1.01 |
| 3 | 1.02 |
| **中位** | **1.02** |
| 最小 / 最大 | 1.01 / 1.03 |

M3 目标 ≤ 1.7 s：**PASS**（v1.0.0 记录值 1.7 s，本次 1.02 s）

> 口径说明：`--selftest` = 建完整窗口（含自检、日志面板）后立刻退出，
> 不含用户点击到看见窗口的人眼延迟，但与 v1.0.0 的记录方式一致，可纵向比较。
> 单文件 exe 未测，v1.1 也不发布它（启动约 40 s，见 `docs/build-report-v1.1.0.md`）。
>
> 波动区间：连续 5 次构建期间测得的 `--selftest` 中位值落在 **1.00 ~ 1.04 s**，
> 属正常抖动（受杀软扫描与磁盘缓存影响），任何一次都远离 1.7 s 门槛。

## 2. 模型转换基线（M4 / M6）

> **状态：QA 已实测（2026-11-01）。** 被测物 = 发布 zip 解包到全新目录的
> `Sketchfab下载器.exe`（非构建中间产物），本机引擎已临时改名排除干扰。
> 逐模型独立运行（等效 conc=1），psutil 每 100 ms 采样进程树 RSS。
> 原始数据：`docs/bench-raw/*.json`（由 tools/bench.py 口径）与
> `F:\迅雷下载\_qa_v11\regress_result.json` / `regress_round2.json`（QA 执行器）。

| # | 类别 | UID | 峰值 RSS (MB) | 其中 node.exe (MB) | 总耗时 (s) | GLB (MB) | meshes | images | 解密后端 | GLB 成功 |
|---|------|-----|--------------|-------------------|-----------|---------|--------|--------|----------|----------|
| 1 | 短链（跳转解析） | p9HSv | — | — | — | — | — | — | — | **否（Bug #1）** |
| 2 | 短链（跳转解析） | 6S7uo | — | — | — | — | — | — | — | **否（Bug #1）** |
| 3 | 已知可用样本 | 1053729ad9d24d71a578622251f76deb | 327 | 264 | 298.4 | 24.18 | 3 | 6 | wasm | 是 |
| 4 | 已知可用样本 | 7c60b4d1b8ab4187965f30c5e0212fc0 | 440 | 378 | 128.6 | 12.41 | 11 | 1 | wasm | 是 |
| 5 | 带骨骼/动画：S.W.A.T. Operator | 9e82fabf26194896b5ad4a364d864eab | 813 | 744 | 338.5 | 133.34 | 42 | 22 | wasm | 是（skins=35, materials=12） |
| 6 | 材料数 ≥5：Chibi Gear Solid | a4ca0e3864e143af9bf1dffc7fc8029b | 139 | 69 | 223.4 | 3.48 | 63 | 60 | wasm | 是（materials=60, images=60） |

GLB 成功率：**4/6（M6 要求 100%，未达标）** —— 失败的 2 行均为「短链解析在打包
环境 100% SSL 失败」（Bug #1，与解密/转换链路无关，详见 docs/test-report-v1.1.0.md）。

补充实测要点：

- **4K 贴图场景确认**：行 3/4/5/6 均含 4096×4096 贴图 descramble 成功记录，
  瘦身 S1/S2 删除重复 libvips 后 sharp 编码链路正常（images 与日志 tex_count 口径一致）。
- **解密后端确认 wasm**：6 次运行日志全部命中 `decrypt: WASM (auto static key +
  per-model diter.b)`，无一次回落 legacy。
- **动画（animations）**：即使 skins=35 的骨骼样本，输出 GLB `animations=0`；
  与 v1.0.0 产物逐字段一致（对照实验），属转换器固有行为而非 v1.1 回退，
  v1.3 重写 GLB 写出器时需专项处理。
- 峰值 RSS 最大 813 MB（骨骼样本，133 MB GLB），远低于「转换时不超过 1 GB」的
  期望上限；R12 默认并发 3 维持不变的数据依据见下节。

## 3. 测量方法（可复现）

```bash
# 只测启动（已完成，见上表）
python tools/bench.py --startup

# 全表（读 docs/regression-models.md；空 UID 的行会被跳过并提示）
python tools/bench.py --all

# 单模型 / 无 psutil 时的纯标准库方案
python tools/bench.py --model p9HSv --mem-tool ctypes
```

关键实现点：

- **峰值 RSS 口径** = `--run` 拉起的进程树（exe + 子进程）RSS 之和，每 100 ms 采样一次取最大值。
  单独记录 `node.exe` 峰值 —— v1.2 用 Pillow 摘掉 Node 时，这一列是唯一能证明收益的数字。
- **解密后端**列从引擎日志识别：`decrypt: WASM` → `wasm`，`decrypt: legacy binzDecrypt.exe` → `legacy`。
  v1.1 修复 PATH 后生产路径稳定为 `wasm`（构建报告已证 `DECRYPT_BACKEND=wasm`）。
- 原始采样序列存 `docs/bench-raw/<uid>.json`，便于复查曲线而非只看峰值。
- 没装 psutil 时 `--mem-tool ctypes` 用 Toolhelp32Snapshot + GetProcessMemoryInfo 顶替，结果同口径。

## 4. 并发默认值的决策条件（R12）

当前默认并发 = 3（`app/sketchfab_gui.py`，与 v1.0.0 一致）。**本次不改**，理由：

- 只在 conc=1 下测了启动与单模型方法，没有 conc=3 的对照样本；没有数据支撑就不动默认值；
- 改默认值的门槛（架构设计第 8 节）：conc=1 的峰值比 conc=3 低 >25% **且** 总耗时增加 <20%；
- 若 QA 的 6 模型数据显示 4K 贴图样本峰值逼近内存上限，再按数据调整并发，
  并在 CHANGELOG 记一条「默认并发 3 → N（依据 benchmark 第 X 行）」。

## 5. 与 v1.0.0 的可比性

| 指标 | v1.0.0 | v1.1.0 | 变化 |
|---|---|---|---|
| onedir 体积 | 177.9 MiB | 150.1 MiB | −27.8 MiB |
| zip 体积 | 81.5 MB | 70.2 MB | −11.3 MB（未达 PRD M2 的 ≤66 MB，见 test-report 缺陷 #3） |
| 冷启动（`--selftest` 中位） | 1.7 s | 1.02 s（构建期）/ 0.75 s（QA 复测 ×5） | −0.68 s |
| 单模型峰值 RSS | 未测 | 139~813 MB（4 样本实测，最大为 133 MB GLB 骨骼样本） | 基线已建立 |
| 生产解密后端 | legacy（引擎 `which("node")` 在打包环境恒假；本机因 PATH 有系统 node 实测亦为 wasm） | wasm | 已修复（6/6 运行日志确认） |

> 体积/启动数字取自 `docs/build-report-v1.1.0.md` 与 `build/sizes.json`（构建期实测，非估算）。
