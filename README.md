# Sketchfab 模型下载器

> **非官方项目，与 Sketchfab / Epic Games 无任何关联。** 名称仅用于说明用途。

把 Sketchfab 公开模型的链接粘进桌面程序，自动下载几何体、还原受保护的贴图、导出 Blender 能直接打开的 `.glb`；还能把 `.glb` 一键转成 3D 打印机用的 `.stl` / `.3mf`，并用本地 WebGL 查看器丝滑预览模型。

Windows 桌面应用（tkinter），带批量并行下载、模型搜索、3D 预览、进度条、依赖自检、一键构建。

**能导出什么**：静态几何 + 材质贴图（`.glb` / `.gltf`），以及用于 3D 打印的二进制 `.stl`、带颜色的 `.3mf`。
**不导出什么**：动画、骨骼、形变目标——模型含动画时日志与 `info.json` 会明确标注，不做"完整还原"的暗示。

---

## 免责声明

- 本工具仅用于**个人学习、研究与备份你自己拥有授权的模型**。
- 它抓取的是公开预览页面的数据，不改变原作者设定的授权范围。下载到的模型版权归原作者所有，
  商用、再分发须遵守该模型在 Sketchfab 页面标注的许可（CC 系列需署名，Editorial 仅限个人参考）。
- 请勿用于批量抓取、二次售卖或规避付费模型。
- 使用本工具产生的一切后果由使用者自行承担。

---

## 隐私与网络行为

- 下载与搜索过程只与 `sketchfab.com` / `api.sketchfab.com` / `media.sketchfab.com` 通信，**不上传任何本地文件**，不需要登录。v1.2.0 的搜索只发送关键词、排序方式和分页游标。
- **短链例外**：Sketchfab 已对 `skfb.ly` 短链启用反爬挑战，程序本身无法解开，
  会自动把该**短链**（仅短链本身，不含其他信息）发给第三方还原服务 `unshorten.me` 换取真实地址。
  介意的话请直接粘贴浏览器地址栏里的完整链接 `https://sketchfab.com/3d-models/...`，这条路径完全本地解析、不经过任何第三方。
- **TLS 全局强制校验**：早期版本在证书异常时会静默降级为“不校验”，v1.1.7 起已移除该兜底。
  如果出现证书报错，说明链路可能被代理/杀软拦截，程序会直接失败而不是偷偷放行。

**发布包校验**：每个 Release 附带 `SHA256SUMS.txt`，下载后可用
`certutil -hashfile SketchfabDownloader_vX.Y.Z.zip SHA256` 比对。EXE 未做代码签名，
于是 SmartScreen 首次运行会提示“未知发布者”。发布包顶层同时带有 `LICENSE` 与
`THIRD-PARTY-NOTICES.md`（MIT 要求随副本分发许可声明）。

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

### 界面（v1.2.1）

v1.2.1 重做了桌面界面：暖调石墨底 + 单一琥珀强调色的「暗房工作台」风格，
顶栏带等距立方体标记与自绘页签，表单用编辑式规则线分区，底部状态条指示点
随下载状态变色。设计令牌集中在 `app/ui_theme.py`（配色 / 字阶 / 间距 / ttk 样式），
字体优先思源黑体与 Cascadia Mono，未安装时自动降级到系统字体；正文对比度
≥ 4.5:1。要换配色或字号，改这一处即可全局生效。

v1.2.2 把「♥ 打赏支持」移到顶栏最右侧，改用实心琥珀按钮（原先是下载页
工具栏里的弱样式文字按钮）；打赏完全自愿，不影响任何功能。

v1.2.3 把深色底整体提亮一档（主背景 `#1e222a`，整条色阶同步上移），
层次差、琥珀强调色与文字对比度都保持原样，不再近黑。

v1.2.4 给搜索结果加上图片预览：每行一张缩略图，选中一条在右侧看大图和
作者 / 面数 / 许可等完整信息；图片走官方 CDN 异步加载并缓存，失败自动显示占位图。

v1.2.5 在下载页工具栏加了「GLB → STL / 3MF…」按钮：手动选任意 `.glb`，
同目录生成 `.stl` 与 `.3mf`；下载流程不变，不会下载完自动导出。

v1.2.6 把这两份输出加固到「导入切片软件即可打印」：模型摆到平台上方、
法线朝外、3MF 严格符合 Core Spec，并做了独立严格验证。

v1.2.7 把这个入口做醒目：下载页新增独占一行的琥珀描边「3D 打印导出」分区，
带标题、说明和次级强调按钮；窗口默认高度同步 +60px，日志区不被挤压。
功能与输出格式完全不变。

v1.4.0 把「3D 预览」从 Tk Canvas 软件渲染换成**本地 WebGL**：Three.js 随软件离线打包，
旋转 / 缩放 / 平移由 GPU 完成，14.7 万面的 GLB 也能流畅查看。模型只通过
`127.0.0.1` 在本机读取，不上传、不依赖外网。

### 模型搜索（v1.2.0）

主窗口切到「模型搜索」页签，输入关键词后回车即可搜索 Sketchfab 全站模型，
**免登录、免 API key**。可勾选「只看可下载」、切换排序、上下翻页；双击结果打开原页面，
选中一条或多条点「下载选中」会直接把链接加入下载列表并开始下载。
结果列表每行带一张缩略图，选中某条时右侧预览面板会显示大图与作者、面数、许可等信息。

- 用的是官方公开接口 `GET https://api.sketchfab.com/v3/search?type=models&q=...`，只发送关键词与分页游标，不上传任何本地信息。
- **范围只限 Sketchfab 站内**：其它站点要么需要各自申请 API key、要么没有公开搜索接口；抓页面既不稳定也可能违反站点条款，因此不做。
- 结果里的「仅浏览」模型表示作者没开放下载，自动跳过并提示；含动画的模型按静态几何导出，列表里会标出动画段数。
- 缩略图来自官方公开 CDN 静态图（`media.sketchfab.com`），后台异步加载并按 URL 缓存；
  用 Windows 自带的 GDI+ 把 JPEG 转成 Tk 可显示的 PNG，**不引入 Pillow、发布包体积不变**。

### 3D 预览（v1.4.0：本地 WebGL）

切到「3D 预览」页签，点「打开模型…」选本地 `.glb` / `.stl` / `.3mf`，或点
「预览输出目录最新模型」直接看刚下载 / 刚转换出来的成果。程序会启动一个只监听
`127.0.0.1` 的临时服务，并用默认浏览器打开本地 Three.js WebGL 查看器。
Three.js 随软件离线打包，不联网、不上传模型、不依赖外部 CDN。

| 操作 | 效果 |
|---|---|
| 左键拖拽 | 旋转 |
| 滚轮 | 缩放 |
| 右键拖拽 | 平移 |
| 双击 / `Fit` | 自动适配模型 |
| `W` / `G` / `R` / `A` | 线框 / 网格 / 自动旋转 / 坐标轴 |

- 直接加载原始 GLB / STL / 3MF，**不在预览阶段降面**；右下角显示三角形数、顶点数、
  尺寸和格式。
- GLB 保留材质与贴图，并用本地环境贴图提供 PBR 反射；STL 显示琥珀单色；
  3MF 读取包内颜色。
- 渲染在 GPU 上完成，实测 14.7 万面 Nissan Tiida GLB 可流畅旋转，交互接近网页查看器。
- 实现位于 `app/preview_web.py`（本地服务与启动器）和 `app/assets/webgl/`
  （Three.js r160，MIT 许可），Python 侧仍然只用标准库。
- 预览不改变打印文件：高级重建与基础转换都使用完整网格导出 STL / 3MF。

### 把任意 3D 模型转成可打印的水密文件（v1.5.0）

下载页「3D 打印导出（水密重建）」分区可以选 `.glb` / `.gltf` / `.stl` / `.obj` /
`.ply` / `.3mf` / `.off`，默认走**高级重建**：

1. 展开场景图并把 glTF 修正为 Z-up；
2. 按「成品最长边」等比缩放到毫米（默认 244 mm，约 1:18 车模）；
3. 表面采样 → 体素化 → 填洞 / 合并壳体 → 高斯平滑 → Marching Cubes；
4. 合并顶点、校正法线、逐连通域校验水密；
5. 输出同目录同名 `.stl`（单色几何）与 `.3mf`（单色，Bambu Studio 可直接打开）。

- 打印前会校验：**边界边 0、非流形边 0**；不通过就直接报错，不交付坏模型。
- 「适配 H2D Pro 平台」按 350×320×325 mm、留 8 mm 余量自动定尺寸。
- 「质量」：标准 / 精细 / 超精细，约 60 万 / 120 万 / 200 万三角面预算。
- 日志给出体积、PLA 实心克重估算、体素间距、过滤掉的碎件数量。

发布包已内置高级转换 `worker.exe`（打包了 numpy / scipy / scikit-image / trimesh），
**解压即用，目标电脑不需要安装 Python 或 Anaconda**。发布包因此从约 55 MB 增至约 130 MB。

从源码运行时才会需要外部环境：

```bash
pip install numpy scipy scikit-image trimesh
# 或指定解释器
set SFD_ADVANCED_PYTHON=D:\Anaconda\python.exe
```

源码模式会依次尝试：`build/advanced_dist` 里的本地 worker → 外部 Python。
也可以用「基础转换（仅几何）」走旧的纯标准库路径（只转格式，不重建、不补洞）。

限制：高级重建输出为单色，贴图颜色丢失；体素间距决定最小细节，默认精度下约 1 mm
以下的小特征会被平滑；破洞很大的模型会按外轮廓补成实体，窗缝、格栅等镂空可能被填平。

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
pip install -r requirements-dev.txt  # pytest（+ 可选 pefile）
pip install pyinstaller==6.10.0      # 版本必须锁 6.10.x，见下
python build/build.py                # 测试门禁 → 打包 → 瘦身 → 出 zip
```

`build.py` 会**先跑一遍 `pytest`**，测试不过直接中止（`--no-tests` 可跳过，正常发版别用）。

两个硬性前提：

1. **PyInstaller 必须锁 6.10.x**。6.20+ 的引导器会被 Windows Defender 误判为
   `Trojan:Win32/Bearfoos.B!ml`，用户解压后文件直接被隔离。`build.py` 里有版本闸门，
   不匹配会直接失败退出（确需试新版可设 `SFD_ALLOW_PYI=1`）。
2. **`node.exe` 要放在仓库根目录**，或用 `SFD_NODE_EXE` 指定。它会被打进包里，
   让引擎的 WASM 解密可用。

构建流水线：**跑测试** → 读版本 → 生成元文件 → PyInstaller → 瘦身（带守卫）→
写 exe 版本信息 → 体积闸门 → 打包 zip（含 LICENSE / THIRD-PARTY-NOTICES）→
记体积账本 → 自动清理中间产物。瘦身规则见 `build/slim.py`，每条都要求「替代实现到位」
或「引用扫描 0 命中」才允许删除，守卫不过直接退出、一个字节都不删。

---

## 测试

```bash
python -m pytest -q          # 干净检出即可运行，不需要先构建
```

- 引擎流水线用例全部离线（网络与 Node 都 mock 掉）：进度回调并发、原子替换回滚、
  `info.json` 路径、贴图选择。
- 打印导出用例自己用 `struct` 拼最小 GLB，覆盖容器守卫、accessor / byteStride /
  sparse、节点层级与坐标轴变换、STL 二进制布局、3MF OPC 结构与多色；
  v1.2.6 另加平台摆放、资源 id 唯一、`pindex`、法线翻转等「直接可用」用例；
  v1.2.7 另加界面守卫（醒目样式、脱离工具栏、文案恢复）与对比度用例；
  v1.3.1 另加 3D 预览用例（三种格式解析、颜色 / 装配 / 顶点合并降采样、投影与深度排序、
  错误路径、弱透视守卫、真实 Tk 渲染与 GUI / spec 接线守卫）。
- 发布包相关用例在没有 zip 时自动 `skip` 并说明怎么获取（构建一次，或设 `QA_RELEASE_ZIP`）。
- CI：`.github/workflows/ci.yml` 在 Windows + Python 3.11/3.12 上跑全量测试，
  并静态扫描「关闭 TLS 校验」的代码。

---

## 目录结构

```
app/
  sketchfab_gui.py      GUI + 三条命令行路径
  version.py            版本号唯一真源
  print_advanced.py     高级打印转换调用层（纯标准库，探测外部 Python）
  advanced_print_worker.py  体素重建 worker（numpy / scipy / scikit-image / trimesh）
  print_export.py       基础转换：GLB → 二进制 STL / 带颜色 3MF（纯标准库）
  preview_web.py        本地 WebGL 预览服务与浏览器启动器
  assets/webgl/         随包分发的 Three.js 查看器资源（MIT）
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
