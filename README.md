# Sketchfab 模型下载器

> **非官方项目，与 Sketchfab / Epic Games 无任何关联。** 名称仅用于说明用途。

把 Sketchfab 公开模型的链接粘进桌面程序，自动下载几何体、还原受保护的贴图、导出 Blender 能直接打开的 `.glb`。

Windows 桌面应用（tkinter），带批量并行下载、进度条、依赖自检、一键构建。

---

## 免责声明

- 本工具仅用于**个人学习、研究与备份你自己拥有授权的模型**。
- 它抓取的是公开预览页面的数据，不改变原作者设定的授权范围。下载到的模型版权归原作者所有，
  商用、再分发须遵守该模型在 Sketchfab 页面标注的许可（CC 系列需署名，Editorial 仅限个人参考）。
- 请勿用于批量抓取、二次售卖或规避付费模型。
- 使用本工具产生的一切后果由使用者自行承担。

---

## 隐私与网络行为

- 下载过程只与 `sketchfab.com` / `api.sketchfab.com` / `media.sketchfab.com` 通信，**不上传任何本地文件**，不需要登录。
- **短链例外**：Sketchfab 已对 `skfb.ly` 短链启用反爬挑战，程序本身无法解开，
  会自动把该**短链**（仅短链本身，不含其他信息）发给第三方还原服务 `unshorten.me` 换取真实地址。
  介意的话请直接粘贴浏览器地址栏里的完整链接 `https://sketchfab.com/3d-models/...`，这条路径完全本地解析、不经过任何第三方。
- **TLS 全局强制校验**：早期版本在证书异常时会静默降级为“不校验”，v1.1.7 起已移除该兜底。
  如果出现证书报错，说明链路可能被代理/杀软拦截，程序会直接失败而不是偷偷放行。

**发布包校验**：每个 Release 附带 `SHA256SUMS.txt`，下载后可用
`certutil -hashfile SketchfabDownloader_vX.Y.Z.zip SHA256` 比对。EXE 未做代码签名，
于是 SmartScreen 首次运行会提示“未知发布者”。

---

## 直接使用（推荐）

到 [Releases](../../releases) 下载最新的 `Sketchfab下载器_vX.Y.Z.zip`，解压到任意目录，双击
`Sketchfab下载器.exe`。

- 不需要装 Python / Node.js / 任何运行库，全部随包提供
- 配置写在 `%APPDATA%\Sketchfab下载器\`，不写注册表，不污染系统
- 首次启动会自动挑一个存在的下载目录

> 首次打开若出现「Windows 已保护你的电脑」→ 点「更多信息」→「仍要运行」。
> 程序未购买代码签名证书，属未知发布者。

**别只把 exe 单独拷走**——同目录的 `_internal\` 是程序本体。

---

## 从源码运行

```bash
# 依赖：Python 3.11+（需带 tkinter）、Node.js 18+
npm install --prefix app            # 装 sharp
python app/sketchfab_gui.py         # 启动界面
```

依赖查找顺序是「随程序附带的 → 仓库内的 → 环境变量 → PATH」，可用以下变量覆盖：

| 变量 | 作用 |
|---|---|
| `SFD_CLI_DIR` | 指定下载引擎目录（默认 `dist_engine/`） |
| `SFD_NODE_EXE` | 指定 `node.exe` 路径 |

命令行用法：

```bash
python app/sketchfab_gui.py --run "链接1,链接2" --out "D:\模型" --conc 3
python app/sketchfab_gui.py --env          # 打印依赖解析结果
python app/sketchfab_gui.py --version      # 版本号
python app/sketchfab_gui.py --selftest     # 只建界面，输出 SELFTEST_OK
```

---

## 构建

```bash
pip install pyinstaller==6.10.0     # 版本必须锁 6.10.x，见下
python build/build.py               # 一条命令出 zip + 体积报告
```

两个硬性前提：

1. **PyInstaller 必须锁 6.10.x**。6.20+ 的引导器会被 Windows Defender 误判为
   `Trojan:Win32/Bearfoos.B!ml`，用户解压后文件直接被隔离。`build.py` 里有版本闸门，
   不匹配会直接失败退出（确需试新版可设 `SFD_ALLOW_PYI=1`）。
2. **`node.exe` 要放在仓库根目录**，或用 `SFD_NODE_EXE` 指定。它会被打进包里，
   让引擎的 WASM 解密可用。

构建流水线：读版本 → 生成元文件 → PyInstaller → 瘦身（带守卫）→ 写 exe 版本信息 →
体积闸门 → 打包 zip → 记体积账本。瘦身规则见 `build/slim.py`，每条都要求「替代实现到位」
或「引用扫描 0 命中」才允许删除，守卫不过直接退出、一个字节都不删。

---

## 目录结构

```
app/
  sketchfab_gui.py      GUI + 三条命令行路径
  version.py            版本号唯一真源
  diagnostics.py        依赖自检（--env）
  update_checker.py     可选的 GitHub Release 更新检查
  extlib/               贴图还原与 osgjs→glTF 转换（派生自 Sketchfab-Ripper，MIT）
  sfpy/                 纯 Python 版解密（实验，未接入主链路）
dist_engine/            下载引擎（MIT）
  tools/wasm/           WASM 解密模块
build/                  构建与瘦身
tests/                  回归测试
docs/                   PRD、架构设计、测试报告
```

---

## 许可

MIT，见 `LICENSE`。第三方组件许可见 `THIRD-PARTY-NOTICES.md`。

本项目不分发任何模型或贴图数据。
