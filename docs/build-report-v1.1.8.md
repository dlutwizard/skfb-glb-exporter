# 构建报告 · v1.1.8

> 由 `build/build.py` 自动生成，请勿手改。构建日期：2026-11-04

## 1. 结论

| 项 | 值 | 门禁 | 结果 |
|---|---|---|---|
| onedir 体积 | 132.5 MiB（138918923 B） | ≤ 160 MiB（M1） | PASS |
| zip 体积 | 53.1 MB（55660474 B） | ≤ 72 MB（M2） | PASS |
| 启动耗时（--selftest 中位） | 0.90 s | ≤ 1.7 s（M3） | PASS |
| 版本一致性（exe 属性/--version/version.py） | 1.1.8 | 一致（M5） | PASS |
| exe 自检 | SELFTEST_OK ×3 | 必须（R4） | PASS |
| 实际解密后端 | wasm | 必须可观测 | PASS |
| 包内禁止物（config.json / psutil / 旧使用说明） | 无 | 必须（R7） | PASS |
| zip 内文件数 | 1157 | 连续两次一致 | 记录 |
| 构建总耗时 | 98.0 s | — | — |

## 2. 体积分项与增量

| 组件 | v1.1.8 | 上一版 v1.1.7 | 增量 | 告警（+2 MiB） |
|---|---|---|---|---|
| node_exe | 83.0 MiB | 83.0 MiB | +0.0 MiB |  |
| libvips_dup | 0.0 MiB | - | +0.0 MiB |  |
| binzDecrypt_exe | 0.0 MiB | - | +0.0 MiB |  |
| sharp_wasm32 | 0.0 MiB | - | +0.0 MiB |  |
| emnapi | 0.0 MiB | - | +0.0 MiB |  |
| tslib | 0.0 MiB | - | +0.0 MiB |  |
| sharp_win32_x64 | 18.9 MiB | 18.9 MiB | +0.0 MiB |  |
| engine | 0.4 MiB | 0.4 MiB | +0.0 MiB |  |
| node_modules | 20.0 MiB | 20.0 MiB | +0.0 MiB |  |
| **onedir 合计** | **132.5 MiB** | **132.4 MiB** | **+0.0 MiB** | |
| **zip** | **53.1 MB** | **53.1 MB** | **+0.0 MB** | |

## 3. 瘦身明细（build/slim.py，每条都过守卫）

| 规则 | 路径 | 释放 |
|---|---|---|
| S1 | `_internal/libvips-42.dll` | 18.14 MiB |
| S2 | `_internal/libvips-cpp-8.18.7.dll` | 0.32 MiB |
| S3 | `_internal/node_modules/@img/sharp-wasm32` | 8.89 MiB |
| S4 | `_internal/node_modules/@emnapi` | 0.42 MiB |
| S5 | `_internal/node_modules/tslib` | 0.09 MiB |
| **合计** | | **27.85 MiB** |

明确不删（PRD 曾误列，实测是 sharp 真依赖）：`@img/colour`、`node_modules/{sharp,detect-libc,semver}`、`_internal/node.exe`、`engine/tools/wasm/*`。

## 4. exe 自证输出

```
$ Sketchfab下载器.exe --version
Sketchfab下载器 v1.1.8 (build 2026-11-04)

$ Sketchfab下载器.exe --env
RES = C:\Users\Lenovo\Sketchfab下载\build\dist_dir\Sketchfab下载器\_internal [OK]
BASE = C:\Users\Lenovo\Sketchfab下载\build\dist_dir\Sketchfab下载器 [OK]
ENGINE = C:\Users\Lenovo\Sketchfab下载\build\dist_dir\Sketchfab下载器\_internal\engine [OK]
NODE = C:\Users\Lenovo\Sketchfab下载\build\dist_dir\Sketchfab下载器\_internal\node.exe [OK]
CONVERT = C:\Users\Lenovo\Sketchfab下载\build\dist_dir\Sketchfab下载器\_internal\convert.mjs [OK]
SHARP = [OK]
WASM = [OK]
EXTLIB = [OK]
NODE_LOOKUP=C:\Users\Lenovo\Sketchfab下载\build\dist_dir\Sketchfab下载器\_internal\node.EXE
DECRYPT_BACKEND=wasm
BUILD=1.1.8 (2026-11-04), py3.11.5, pyinstaller 6.10.0

$ Sketchfab下载器.exe --selftest  (×3)
SELFTEST_OK  0.90s  1.02s  0.85s
```

## 5. 未纳入本报告的门禁

- **M4 峰值内存 / M6 六模型回归**：需要真实下载，由 `tools/bench.py` 产出 `docs/benchmark-v1.1.md`（QA 执行，不在构建期做）
- **zip_mb 未写入 build_info.json**：zip 体积在打包后才确定，写进包内会导致两次压缩，故记录在 `build/sizes.json` 与本报告
