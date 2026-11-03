# 第三方组件与许可

本项目自身以 **MIT** 许可发布（见 `LICENSE`）。下表列出随项目分发或链接的第三方组件。
凡是要求保留版权声明的，其原文均放在对应目录下的 `LICENSE` 文件里，未做删改。

## 源码级依赖（随仓库分发）

| 组件 | 位置 | 许可 | 说明 |
|---|---|---|---|
| Sketchfab CLI 下载引擎 | `dist_engine/` | **MIT** | Copyright (c) 2026 Sketchfab CLI contributors。全文见 `dist_engine/LICENSE`。本项目对其做了少量修补（模型名非法字符清洗、解密后端选择），已在文件内注明。 |
| Sketchfab-Ripper 转换链 | `app/extlib/` | **MIT** | Copyright (c) 2026 [anotherRandomPersonlol](https://github.com/anotherRandomPersonlol/Sketchfab-Ripper)。上游为单文件 userscript，本项目拆分为 18 个 ES 模块，功能未删减。全文见 `app/extlib/LICENSE`。 |

## 运行时依赖（打包进 Release）

| 组件 | 许可 | 说明 |
|---|---|---|
| Node.js | MIT | 随包提供 `node.exe`，用于运行 WASM 解密与 GLB 转换。 |
| sharp | Apache-2.0 | 图像处理，`app/node_modules/sharp`。 |
| libvips | LGPL-2.1-or-later | sharp 的底层库。以动态链接（DLL）方式分发，符合 LGPL 要求；如需替换版本，直接替换 `libvips-42.dll` 即可。 |
| Python | PSF-2.0 | 运行时。 |
| Tcl/Tk | BSD-style | tkinter 界面依赖。 |
| certifi | MPL-2.0 | CA 证书包，用于绕开系统证书存储。 |
| PyInstaller | GPL-2.0-or-later（含打包例外） | 仅用于构建，其许可例外明确允许打包非自由程序。 |

## 已移除：`binzDecrypt.exe`

历史版本（≤ v1.1.2）曾随包分发 `binzDecrypt.exe`（约 17.9 MB），用作 WASM 解密失败时的兜底后端。

该文件来自第三方项目 `CyberSys/Sketchfab-Ripper`，**上游未提供任何明确许可**，因此本项目**不再分发该二进制**，也不再把它放进构建产物。当前的解密链路只有一条：

```
Node.js + tools/wasm/decrypt.wasm（随包提供）
```

功能上无损失——WASM 路径本来就是生产环境实际生效的那条，`binzDecrypt.exe` 只在找不到 Node 时才会被调用，而 `node.exe` 始终随包提供。

如果你在本地保留了这个文件，请自行确认其授权状况；仓库的 `.gitignore` 已将 `_private/` 排除在外。

## 关于模型与贴图

本项目不包含、也不分发任何 Sketchfab 模型、贴图或其衍生数据。用户下载的内容版权归原作者所有，
使用时须遵守该模型在 Sketchfab 页面标注的授权条款。
