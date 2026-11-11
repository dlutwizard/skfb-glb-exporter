# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 结构，
版本号遵循语义化版本（`MAJOR.MINOR.PATCH`）。

打包时本文件会被复制到 `_internal/CHANGELOG.md`，「关于」面板离线读取它，
因此日志正文必须是纯文本 Markdown，不要写图片或外链依赖。

## [1.5.0] - 2026-11-10

3D 打印导出从「格式转换」升级为「水密实体重建」：任何下载下来的模型都能直接切片。

### 新功能

- 新增高级打印重建：表面采样 → 体素化 → 填洞 / 合并壳体 → 高斯平滑 →
  Marching Cubes，输出保证边界边 0、非流形边 0 的封闭实体。
- 支持 `.glb` / `.gltf` / `.stl` / `.obj` / `.ply` / `.3mf` / `.off` 输入，
  不再只限 GLB。
- 新增成品尺寸控制：默认最长边 244 mm（约 1:18 车模），可一键「适配 H2D Pro 平台」
  （350×320×325 mm，自动留 8 mm 余量）。
- 新增质量档位：标准 / 精细 / 超精细（约 60 万 / 120 万 / 200 万三角面预算）。
- 日志显示水密校验、体积、PLA 实心克重估算、体素间距与过滤掉的碎件数量。
- 发布包内置独立的 `advanced_print_worker.exe`（numpy / scipy / scikit-image /
  trimesh 全部打包），目标电脑无需安装 Python / Anaconda，解压即用。

### 变更

- 原「GLB → STL / 3MF」按钮改为「高级 3D 打印转换」；旧的纯几何导出保留为
  「基础转换（仅几何）」应急入口。
- 高级重建默认调用包内 worker.exe；只有从源码运行时才需要外部 Python 环境，
  程序自动探测 Anaconda，也支持 `SFD_ADVANCED_PYTHON` 指定。
- 由于内置了科学计算栈，发布包体积从约 55 MB 增加到约 130 MB（解压后 onedir 约 335 MiB）。

### 已知限制

- 高级重建输出为单色几何，贴图颜色不会写进 STL / 3MF。
- 体素间距决定最小细节；默认精度下约 1 mm 以下的小特征会被平滑。
- 对破洞很大的模型，重建会按外轮廓补成实体，窗缝、格栅等镂空可能被填平。

## [1.4.0] - 2026-11-09

3D 预览改为本地 WebGL：用 GPU 渲染替代 Tk Canvas 软件渲染，大模型也能丝滑旋转和缩放。

### 新功能

- 「3D 预览」页改为本地 WebGL 查看器：Three.js 随软件离线分发，Python 只启动
  `127.0.0.1` 临时服务并打开默认浏览器，模型不上传、不依赖外网。
- 直接加载原始 `.glb` / `.stl` / `.3mf`，不再为预览做顶点聚类降面；实测
  14.7 万面 GLB 可正常显示全部三角形。
- 查看器支持左键旋转、滚轮缩放、右键平移、双击 Fit，以及线框、网格、坐标轴、
  自动旋转；右下角显示三角形数、顶点数、尺寸和格式。
- GLB 的 PBR 材质使用本地环境反射，车漆、金属和贴图显示更接近网页查看器。
- 关闭主程序时自动关闭本地预览 HTTP 服务。

### 变更

- 移除旧 Tk Canvas 预览页的 GUI 接线；打印导出仍使用完整网格，预览不会改变
  STL / 3MF 的精度。
- 新增 `app/preview_web.py` 与 `app/assets/webgl/`（Three.js r160，MIT 许可）。

## [1.3.1] - 2026-11-08

修复 3D 预览「模型不完整」：降采样从均匀跳面改为顶点合并，任何模型表面都完整显示。

### 修复

- **模型不完整（核心修复）**：旧版降采样用「均匀跳面」，24 万面的模型会抽掉
  约 96% 的三角形，屏幕上只剩零星碎片（实测覆盖率仅 4.75%）。现改为
  **顶点空间聚类合并**：相近顶点合并为代表点、三角形索引原样保留，
  表面不会出现任何空洞，只降低几何精度。实测 24 万面球的屏幕覆盖率为 100%。
- **拖拽卡顿**：去掉三角形同色描边（实测占一半帧时间），拖拽时复用上一帧
  深度排序顺序。实测 24 万面模型拖拽约 7.5 fps，松手恢复精细画面且与
  精确排序逐像素一致。

### 变更

- 渲染预算按可见窗口实测重新标定：静态上限 13000 面（约 0.35 s）、
  拖拽 4800 面（约 0.13 s）、线框 2500 面；2.7 万面以下的模型完全免降级、
  零开销直接渲染（覆盖绝大多数 Sketchfab 模型）。
- 面数超限时信息栏措辞由「已抽样」改为「已合并显示」，更符合实际行为。

### 已知限制

1. 万级顶点下 Tk「每面一个画布对象」的光栅化是硬上限，追求 30 fps 需更换渲染路径。
2. 细长 / 碎片化网格的合并可能偏保守（返回面数略低于目标），仅影响异常网格。
3. 全退化三角形文件（顶点全部共线）仍退回等距抽样，屏幕上本就零面积、无视觉影响。
4. 顶点合并会轻微吸附几何（误差不超过半径的 1.3%），精确尺寸请以信息栏为准。
5. 3MF `<multiproperties>` 未支持，回落默认色。

## [1.3.0] - 2026-11-08

新增「3D 预览」页签：本地打开 GLB / STL / 3MF，纯标准库软件渲染，可旋转、缩放、平移并检查颜色与尺寸。

### 新功能

- 新增第三个页签「3D 预览」：打开文件 / 一键预览输出目录最新模型 / 适配窗口，
  右侧信息栏显示格式、三角形数、顶点数、毫米尺寸、体积、颜色种数与解析说明。
- 交互对齐在线 3D 查看器：左键旋转、滚轮缩放、右键或中键平移、双击 Fit，
  以及自动旋转、线框、网格、坐标轴、背面剔除开关。
- 支持三种格式：GLB 复用 `print_export` 的 Z-up 摆正与米→毫米换算；STL 兼容
  二进制与 ASCII；3MF 解析 OPC/XML、basematerials / colorgroup 颜色、
  `<build><item transform>` 与 `<components>` 装配层级。
- 渲染为画家算法 + 面法线 Lambert 明暗，每三角形保留颜色；超过 9000 面均匀抽样，
  拖拽 / 自动旋转时降到 1800 面，保证交互不卡。弱透视避免模型被拉变形。

### 工程

- 新增 `app/preview3d.py`（解析 + 渲染数学，**刻意不 import tkinter**，干净环境可跑）
  与 `app/preview_view.py`（Tk Canvas 控件与交互），零新增依赖、完全离线。
- 解析放后台线程，结果经 `preview_ready` / `preview_error` 队列回主线程，
  不阻塞 Tk 主循环；按钮文案提成 `PREVIEW_OPEN_BTN_TEXT` 单一真源。
- 两个 PyInstaller spec 补 `preview3d` / `preview_view` hiddenimports。

### 测试

- 新增 `tests/test_preview3d.py`：22 条用例覆盖三种格式、颜色 / 装配 / 元数据、
  降采样、投影与深度排序、错误路径、弱透视守卫、GUI 与 spec 接线，以及真实 Tk 渲染。
- 全量 pytest 175 passed, 2 skipped；真实 Tk 集成验收通过（三页签、双色 3MF、
  线框、STL、错误态、搜索页未受影响）。

## [1.2.7] - 2026-11-07

把「GLB → 3D 打印」入口从工具栏里最不显眼的文字按钮，升级成下载页独占一行的琥珀描边操作区。

### 界面

- 新增独立的「3D 打印导出」分区：琥珀描边框、加粗标题、一行说明文字，
  右侧是次级强调按钮「◆ 3D 打印：GLB → STL / 3MF…」；不再和
  「清空日志 / 清空链接 / 关于」这些辅助按钮挤在一起。
- `ui_theme.py` 新增 `Convert.TButton`：琥珀描边 + 琥珀粗体字，
  hover / 按下时填充琥珀；保持描边而不是实心，不与一级主按钮
  「开始下载」抢层级。
- 窗口默认高度从 860 提到 920，同时把分区压紧，保证新增一行后
  CONSOLE 日志区仍有约 97px（上一版约 105px），转换日志仍可读。
- 按钮文案提成 `CONVERT_BTN_TEXT` 单一真源：修掉点过一次转换之后
  `print_done` 把文案恢复成旧简写、入口又变弱的问题。

### 测试

- 静态守卫确认入口不会再退回 `Quiet.TButton` 弱样式；真实 Tk 里验证
  `Convert.TButton` 生效、对比度达标、按钮脱离工具栏、转换完成后
  文案与状态都正确恢复；全量 pytest 153 passed, 2 skipped。
- 功能不变：仍然只有一个转换按钮、不接下载流程、不自动导出，
  STL / 3MF 输出与 v1.2.6 完全一致。

## [1.2.6] - 2026-11-07

把 GLB → STL / 3MF 加固到切片软件导入即可打印，并按 3MF Core Spec 1.4 独立验证通过。

### 直接可用修正

- STL 与 3MF 共用同一份几何，顶点统一平移到正八分象限、最小点落在
  `(0, 0, 0)`：底面贴在打印平台上，不会因负 Z 沉到平台下方被切掉。
- 闭合网格的有向体积为负时整体翻转三角形绕序，保证法线朝外；STL 法线
  与绕序一致，3MF 三角形按 CCW 书写。
- 3MF 按 Core Spec 1.4 修正：`resources` 内所有 `id` 全局唯一
  （`basematerials` = 1、`object` = 2，`build/item.objectid` 同步）；
  `<object>` 同时带 `pid` 与 `pindex`；三角形不足 4 个时不再谎称
  `type="model"`，降级为 `other` 并在日志提示这类网格通常无法直接打印。
- 转换日志新增摆放偏移 / 法线是否翻转 / 3MF object 类型；结果统计新增
  `placement_mm`、`normals_fixed`、`object_type` 便于核对。
- GUI 转换完成日志补充「已贴在平台 Z=0、法线朝外、导入后按需居中」以及
  `displaycolor 只用于显示` 的规范说明。

### 验证

- 独立验证脚本用 `trimesh` 加载 STL / 3MF（watertight、winding、volume、
  bounds、euler），用 `lxml` 按 Core Spec 检查 OPC、id 唯一性、metadata、
  `pid` / `pindex`、`p1` 范围、三角形数与坐标；正常立方体与反向绕序立方体
  两个样本均为 0 问题。
- `tests/test_print_export.py` 新增 7 条「直接可用」回归用例（34 条全通过）。

### 说明

- 3MF 的 `displaycolor` 按规范只用于渲染显示，实际打印颜色由切片软件的
  材料 / 耗材映射决定；导入后如需把模型移到平台中心，请在切片软件里移动。

## [1.2.5] - 2026-11-07

新增 GLB → 3D 打印文件转换：一键导出二进制 STL 与带颜色的 3MF，纯标准库实现、零新增打包依赖。

### 功能

- 下载页工具栏新增「GLB → STL / 3MF…」按钮：手动选硬盘上任意 `.glb`，
  在**同目录**生成同名 `.stl` 与 `.3mf`。下载流程完全不变，不会在下载后自动导出。
- `app/print_export.py` 用 `struct` / `json` / `zipfile` 直接解析 glTF 2.0 binary：
  JSON chunk + BIN chunk、bufferView.byteStride 交错顶点、6 种 componentType、
  normalized 整数、sparse accessor、内嵌 / data URI / 外置 `.bin` 三种 buffer。
- 遍历 scene → nodes 累积 4x4 变换（matrix 或 TRS 四元数），支持
  TRIANGLES / TRIANGLE_STRIP / TRIANGLE_FAN；含镜像缩放的节点自动翻转绕序，
  避免法线朝里；退化三角形与坐标非法的顶点会被跳过并计数。

### 打印相关修正

- glTF 是 Y-up，切片软件按 Z-up 解释 STL / 3MF。默认做一次纯旋转
  `(x, y, z) → (x, -z, y)`（本项目 `extlib/osg-scene.js` 里 ZUP_TO_YUP_MAT3 的逆），
  否则导出的模型会躺着。
- 单位：glTF 规范是米、3MF 是毫米。按包围盒最长边自动判断（< 10 视为米制 ×1000，
  否则 1:1），日志逐条写明换算依据与成品毫米尺寸，模块 API 也允许显式指定 scale。
- 3MF 按 OPC 包写 `[Content_Types].xml` / `_rels/.rels` / `3D/3dmodel.model`，
  用 Core Spec 的 `<basematerials>` + 三角形 `p1` 表达多色；颜色取自
  `pbrMetallicRoughness.baseColorFactor`（线性 → sRGB）。basematerials 不支持顶点渐变，
  因此三角形只写 `p1`，不写 `p2/p3`。

### 限制（日志与使用说明里如实标注）

- STL 是无单位的单色三角网格；**多色请用 `.3mf`** 导入切片软件。
- 贴图颜色无法无损转进 3MF，含 `baseColorTexture` 的材质退化为白色；
  需要原贴图请直接用 `.glb`。
- 蒙皮 / 形变目标 / 动画只取静态顶点，不重算骨骼变换。
- 点、线 primitive 不是实体，跳过。

### 工程

- 两个 PyInstaller spec 的 `hiddenimports` 加入 `print_export`；不新增任何第三方依赖，
  发布包体积基本不变。
- 新增 `tests/test_print_export.py`（27 条离线用例，自己拼最小 GLB，覆盖容器守卫、
  accessor、节点层级 / 四元数 / 镜像、坐标轴、STL 二进制布局、3MF OPC 结构、
  多色、单位换算、GUI 接线与 spec 守卫）。

## [1.2.4] - 2026-11-07

模型搜索结果加上图片预览：每行显示缩略图，选中一条可在右侧查看大图与完整信息，零新增打包依赖。

### 界面

- 结果表新增「预览」列，每行显示 64×36 缩略图；行高调到 46px，用独立的
  `Preview.Treeview` 样式，不影响「关于 / 诊断」面板里的表格。
- 结果表右侧新增预览面板：选中一条后显示 192×108 大图、模型名称、作者、
  面数、许可与「可下载 / 仅浏览」状态；多选时标出已选数量。
- 没有缩略图或加载失败时显示深色「图片位」占位图，不会出现空白或破图。

### 图片管线

- 搜索结果本来就带官方 CDN 的缩略图 URL，v1.2.0 只存没用；v1.2.4 新增
  `sketchfab_search.fetch_thumbnail()`，走标准库 HTTPS 默认证书校验，
  8 秒超时、2 MiB 体积上限，网络层可注入便于离线测试。
- Tk 8.6 的 `PhotoImage` 只认 PNG/GIF/PPM，而官方缩略图是 JPEG。新增
  `app/image_codec.py`，用 ctypes 调 Windows 自带的 GDI+ 把 JPEG 转成 PNG：
  **不引入 Pillow、发布包体积不变**，也不动既有构建门禁。
- 转码前先做格式守卫（JPEG 魔数 + 结尾 EOI）：被网络截断的畸形数据会让
  GDI+ 抛 Windows 结构化异常，Python 层接不住，必须在进 GDI+ 之前挡掉。
- 缩略图在 4 线程后台并发下载，结果按 URL 缓存，翻页回来不重复下载；
  工作线程只做网络 + 转码，图片经主线程队列回填（Tk 不是线程安全的）；
  用 `search_gen` 丢弃过期请求，连续搜索 / 翻页不会串图。
- 所有 `PhotoImage` 显式绑定 master，避免同进程多个 Tk 实例时图片建到
  别的解释器里导致 `image "pyimageN" doesn't exist`。

### 工程

- 两个 PyInstaller spec 的 `hiddenimports` 加入 `image_codec`。
- 新增 `tests/test_image_codec.py`、`tests/test_thumbnails.py`；全量 pytest
  `118 passed, 1 skipped`，`--selftest` 通过，GUI 离线冒烟与真实缩略图截图验收通过。

## [1.2.3] - 2026-11-07

把深色底整体提亮一档，仍是暗房工作台风格，面板层次与文字对比度保持不变。

### 界面

- 主背景 / 面板 / 规则线 / 控制台整条色阶统一上移 (12, 13, 16)，
  相对亮度从 0.0074 提到 0.0159（约 2.15 倍），不再近黑；各层之间的
  亮度差保持原样，面板不会糊成一片。
- `DIM` 弱文字同步上移，避免底色变亮后反而更看不清。
- 琥珀强调色与文字主色不变；TEXT/BG 13.15:1、MUTED/BG 5.33:1、
  LOG_FG/LOG_BG 10.75:1，均高于 WCAG AA 的 4.5:1。

### 工程

- `tests/test_ui_theme.py` 增加底色亮度下界、层次顺序与对比度守卫，
  防止以后又被调回近黑或改成浅灰底。
- 全量 pytest 回归通过；`--selftest` 输出 `SELFTEST_OK`。

## [1.2.2] - 2026-11-07

把打赏入口移到顶栏最右侧并改为实心琥珀按钮，比原来藏在底部工具栏更醒目。

### 界面

- 移除下载页工具栏里的弱样式「♡ 打赏」，改为顶栏最右侧的「♥ 打赏支持」
  按钮，位置最高、视觉最跳；打赏完全自愿，不影响任何功能。
- 新增 `Donate.TButton` 样式（实心琥珀 + 深色字，hover / pressed 分级），
  尺寸比主操作按钮小一号，不抢「开始下载」的视觉重心；配色与字阶仍由
  `app/ui_theme.py` 单一真源提供。
- 更新 `app/使用说明.template.txt` 的顶栏说明，标注打赏按钮位置。

### 工程

- `tests/test_ui_theme.py` 增加顶栏打赏接线、旧按钮移除的静态守卫，
  并在真实 Tk 上验证 `Donate.TButton` 样式落地。
- 全量 pytest 回归通过；`--selftest` 输出 `SELFTEST_OK`。

## [1.2.1] - 2026-11-07

界面全面改版：深色石墨工作台配琥珀强调色，重做顶栏、页签、表单、进度与日志排版。

### 界面：Editorial / Industrial「暗房工作台」

- 新增 `app/ui_theme.py` 作为设计令牌单一真源（配色 / 字阶 / 间距 / 样式），
  控件不再散落十六进制色值；不同 Tk 版本逐项降级配置，个别样式选项
  不被支持也不会让界面启动失败。
- 放弃浅灰底 + 白卡片 + 蓝按钮的默认外观，改为暖调石墨底 + 单一琥珀强调色
  （主色 60% / 面板 30% / 强调 10%）；正文对比度 ≥ 4.5:1。
- 字体分级：展示字与正文用思源黑体，数字、路径、日志、眉题用 Cascadia Mono；
  本机没有这些字族时逐级降级，不会在别人的电脑上变成方框。
- 顶栏加入等距立方体线框标记与自绘页签栏（选中态琥珀下划线），隐藏原生
  页签栏；`nb.select()` 与 `<<NotebookTabChanged>>` 保持可用。
- 表单改为编辑式规则线分区：字段标签 + 说明 + 深色输入框（焦点琥珀描边）、
  大号等宽百分比数字、细进度条、独立控制台区与底部状态条。
- 状态条左侧指示点随状态变色（就绪 / 进行中 / 警告 / 完成）；搜索结果列表
  新增空状态引导，不再是一整块空白。

### 工程

- 生产 spec 与单文件 spec 的 `hiddenimports` 同步加入 `ui_theme`。
- 新增 `tests/test_ui_theme.py`：令牌格式、WCAG 对比度、字体元组、GUI 接线
  与旧配色残留守卫、真实 Tk 上样式落地与原生页签隐藏验证。
- 全量 pytest 回归通过（含新增主题用例）；`--selftest` 输出 `SELFTEST_OK`；
  搜索页离线集成冒烟 5 项通过。

## [1.2.0] - 2026-11-07

新增「模型搜索」页签：关键词搜索 Sketchfab 全站模型，结果可一键加入下载队列。

### 新增：模型搜索页签

- 主窗口新增「下载 / 模型搜索」两个页签。搜索页签调用 Sketchfab 官方公开
  v3 搜索接口（`api.sketchfab.com/v3/search`），**免登录、免 API key**。
- 支持关键词、只看可下载、四种排序（相关度 / 最多喜欢 / 最新发布 / 最多浏览）、
  上一页 / 下一页。
- 结果列表显示名称、作者、面数、是否可下载、许可、动画段数；双击打开原页面。
- 选中一条或多条后点「下载选中」，链接自动追加到下载列表，切回下载页签并
  **直接开跑**；「仅浏览」的模型会自动跳过并给出提示。
- 搜索在后台线程执行，结果按请求代号回收，快速连搜不会串页；失败（限流、
  断网、接口改版）在状态栏给出可读原因，不会卡住界面。

**范围说明**：本版只聚合 Sketchfab 站内搜索。Thingiverse / MyMiniFactory /
Poly Pizza 等站需要各自申请 API key 且不能直接下载；Printables / TurboSquid 等
无公开搜索接口，抓取页面既不稳定也可能违反站点条款，因此不纳入。

### 工程

- 新增 `app/sketchfab_search.py`（纯标准库、零 GUI 依赖、网络层可注入）。
  全程使用 HTTPS 默认证书校验，不降低 TLS 安全性。
- 新增 `tests/test_search.py` 13 条离线用例，覆盖 URL 编码、分页游标、
  字段缺失容错、限流提示、页签接线守卫；全量 `97 passed, 2 skipped`。
- 生产 spec 与单文件 spec 的 `hiddenimports` 同步加入 `sketchfab_search`。

### 已知边界

- 结果列表暂不显示缩略图（发布包不含图像解码库，不做额外依赖）。
- 搜索接口分页由服务端游标驱动，没有总页数，只能逐页翻。

## [1.1.8] - 2026-11-04

本轮对应独立审查 ROUND-01 的门禁项，编号沿用 `ISSUES.md` 的 SFD 编号。

### 修复（阻塞项）
- **SFD-003 并发进度回调丢失**：回调改为**显式参数**从批处理线程传给每个下载任务
  （`download_one(..., progress=...)`）。此前靠 `threading.local`，但真正下载的
  `ThreadPoolExecutor` 工作线程是另外的线程，local 里根本没有回调，导致下载阶段的
  进度事件全丢、进度条只在任务完成时跳一下。GUI 与 CLI 两条路径均已修复。
- **SFD-005 贴图双管线**：引擎下载的贴图与 Node 转换器重复下载同一批图（实测 36 张 + 14 张）。
  现在引擎是唯一下载点，转换器改为读本地 `textures/` + `textures_manifest.json`，
  就地解密并**回写**，散装贴图与 GLB 内嵌贴图完全一致；只在缺少 manifest 的历史目录上
  才回退联网（兼容旧目录）。
  - 复验补丁（自查发现，审查方尚未提出）：清单实际写在 `textures/` 子目录，而转换器只会
    在模型根目录找，于是**每次**都判成"没有本地贴图"又联网抓了一遍——SFD-005 表面已修、
    实际没接上。现改为两处都探；一旦走联网兜底，GUI 日志会显式告警。
  - 复验补丁：引擎取清单用了不带参数的 `/textures`（全集 36 张），转换器用的是查看器端点
    `?optimized=1`（14 张）。已统一为 optimized 优先、普通兜底，下载量与 GLB 内嵌张数对齐。
- **SFD-006 测试不可复现**：移除 `F:\迅雷下载`、`v1.1.1.zip` 等本机路径与旧版本硬编码；
  发布包 fixture 找不到时 `skip` 并给出获取方式（不再 `error`）；版本断言一律读
  `app/version.py`；子进程统一 UTF-8。新增 `pytest.ini`、`requirements-dev.txt`、
  GitHub Actions（Windows + 3.11/3.12，含"禁止关闭 TLS 校验"的静态扫描），
  并把 `pytest` 设为 `build.py` 的**硬前置**（`--no-tests` 可跳过）。
- **SFD-007 `info.json` 指向已删除的临时目录**：改为在目录原子替换**之后**写，
  `output` 指向最终真实文件；新增 `primary` / `deliverables` 字段；
  GUI 生成 GLB 后再回写一次，把主交付物切到 `.glb`。

### 修复（不阻断项）
- **SFD-010 输出卫生**：默认删除 `key.txt` / `key2.txt`（`SKFB_KEEP_INTERMEDIATES=1` 保留）；
  保留清单从 `*.txt` 通配改为白名单；`info.json.textures` 只统计图片，
  并新增 `texture_files` / `texture_manifest`。
- **SFD-011 贴图解密失败可见**：转换器写 `convert_report.json` 汇总失败项，
  GUI 显示警告并写入 `info.json.texture_warnings`，不再静默用原始字节冒充成品。

### 变更（决策项）
- **SFD-004 动画**：明确采用"书面豁免"路线——本工具只导出静态几何 + 材质贴图。
  `animationCount > 0` 时 GUI/CLI 都会告警，`info.json.animations.supported = false`；
  成品文件名去掉容易误导的 `_full` 后缀（`<模型名>_full.glb` → `<模型名>.glb`）。
- **SFD-008 文档与打包声明**：
  - 新增 `LICENSE` 与 `THIRD-PARTY-NOTICES.md` 到**发布包顶层**（MIT 合规）。
  - 删除"legacy 后端可用/可回滚"表述：`decrypt_backend` 只剩 `auto` / `wasm`，
    配置里残留 `legacy` 自动按 `auto` 处理并提示；引擎缺后端时给出明确中文错误。
  - 更正"程序默认不联网"为"仅下载与手动检查更新时联网，无遥测、无后台自动更新"。
  - 版本号单一真源：移除 `app/package.json` 的 `version` 字段，`version.py` 内部
    注释里的 `1.1.0` 一并改掉；`version.json` 新增 `commit` 字段，发布物可反查源码版本。

## [1.1.7] - 2026-11-04

### 修复（数据安全）
- **重新下载不再先删旧目录**：原先下载前会 `rmtree` 目标目录，断网/中途退出会让已下好的模型一起消失。
  现在先下到同盘临时目录，全部成功后才原子替换，替换失败还会回滚上一版。
- **移除 TLS「不校验证书」的静默兜底**：早期 HTTP 后端链最后一级会关掉证书校验（requests → urllib → curl → 不校验）。
  在代理/杀软/中间人环境下用户完全无感知地接受被篡改的流量。现已删除该分支，证书异常会直接报错。

### 修复（并发与文案）
- 代理与进度回调由模块级全局变量改为 `threading.local`，并发任务不再互相覆盖进度。
- 外部工具报错中的俄语（`Команда код`、`У модели нет mesh-файлов`）改为中文。
- 清理 CHANGELOG 中 1.1.5 / 1.1.6 重复条目（此前会连带让《使用说明》的更新摘要重复）。

## [1.1.6] - 2026-11-04

### 新增
- **实时下载进度条**（日志区上方）：按引擎步骤（获取信息 → 下载网格 → 解密 → 贴图 → 转 GLB）
  显示整体完成百分比与当前步骤，多任务时同时显示「已下载 n/总数」。

### 改进
- 进度条样式加深加粗，完成后显示「全部下载完成 100%」。

## [1.1.5] - 2026-11-04

### 修复
- **下载时反复弹出命令行黑框**：引擎内两处调用外部程序（node 解密、curl 兜底下载）未设置隐藏窗口标志，
  每次解密贴图都会闪现控制台窗口。现已统一加 `CREATE_NO_WINDOW` + `STARTF_USESHOWWINDOW`，全程无黑框。

## [1.1.4] - 2026-11-03

### 修复
- **skfb.ly 短链无法解析**：Sketchfab 于近期对 `skfb.ly` 与 `sketchfab.com/s/` 启用了 AWS WAF
  JS 挑战（HTTP 202 `x-amzn-waf-action: challenge`），纯 HTTP 客户端无法通过，短链解析全部失败。
  现在直连两条路径失败后自动改用第三方短链还原服务（unshorten.me）兜底，
  仍失败时给出明确指引（改粘浏览器地址栏完整链接）。

## [1.1.3] - 2026-11-03

### 新增
- **打赏入口**：工具栏新增「♡ 打赏」按钮，弹窗并排展示微信 / 支付宝收款码
  （图片位于 `app/assets/donate/wechat.png`、`alipay.png`，替换文件即可换码）。

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
