# -*- coding: utf-8 -*-
"""
preview3d.py — 3D 文件预览的「解析 + 渲染数学」层（纯标准库，零 GUI 依赖）。

参考 Elysia Tools 在线 3MF 查看器的能力，但完全离线、零新增打包依赖：

* 打开 ``.glb`` / ``.stl`` / ``.3mf``，统一成一份三角网格；
* 保留每三角形颜色（3MF basematerials / colorgroup、GLB 材质常量色），
  STL 用单一琥珀色；
* 轨道相机（旋转 / 缩放 / 平移）、面法线 Lambert 光照、画家算法深度排序；
* 线框、地面网格、坐标轴、自动旋转、Fit，以及尺寸 / 面数 / 体积信息。

这一层**刻意不 import tkinter**：干净环境（无图形界面）也能跑解析与投影单测。
Tk Canvas 控件在 ``preview_view.py``。

几何约定
--------
* 统一 Z-up、毫米（GLB 走 ``print_export.extract_mesh`` 做 Y-up→Z-up 与米→毫米；
  STL / 3MF 本身就是 Z-up 毫米），因此三种格式看起来是同一个姿态。
* 相机空间：模型先绕 Z 转 ``yaw``、再绕 X 转 ``pitch``；**z 越大越靠近相机**，
  朝向相机的面法线 z 分量为正，深度排序按 z 升序（远的先画）。
* 准正交的弱透视：深度只轻微调制缩放（±6%），保证立方体看起来仍是立方体；
  分母有下限保护，永远不会除零或把模型翻到背后。

降采样（v1.3.1 修复）
---------------------
v1.3.0 用「均匀跳面」抽样（``faces[i * total // max_faces]``），对 24 万面的球
实测屏幕覆盖率只剩 **4.75%**——保留了 4% 的三角形却留下 95% 的空洞，用户看到
的就是「模型都不完整」。现在改成**顶点空间聚类聚合**：把落在同一网格格子里的
顶点合并成一个代表点（坐标取平均），三角形原样保留索引映射，于是**表面覆盖
始终完整**，只是精度随格子变粗。详见 :func:`_simplify`。
"""
from __future__ import annotations

import io
import math
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import print_export as pe

__all__ = [
    "Preview3DError", "PreviewMesh", "Camera", "load_mesh",
    "MAX_RENDER_FACES", "DRAG_RENDER_FACES", "WIREFRAME_FACES", "MAX_FILE_BYTES",
    "fit_camera", "camera_points", "project", "sort_faces", "shade",
    "face_depth_normal", "wireframe_edges", "hex_color",
]

# Canvas 每三角形一个 item，面数必须设上限；拖拽 / 自动旋转时用同一套聚合
# 逻辑再做一级降质（**不是跳面**，任何时刻看到的都是完整的表面）。
#
# ⚠ 测量口径非常重要 —— 下表全部是「**完整一帧**」：可见窗口
#   （``root.deiconify()`` 且 ``winfo_viewable()==1`` 且未被遮挡）+
#   ``create_polygon`` 建模 + ``root.update()`` 真正光栅化。
#   * 隐藏窗口（``withdraw()``）或窗口被遮挡时，Tk 会跳过光栅化，
#     测出的 raster 偏小到不可信；
#   * 只测 ``create_polygon`` 调用本身也不含光栅化，同样不可信；
#   * 首次光栅化（冷启动）会多花一倍，必须单独剔除，否则档位越大越「慢」。
#   早期版本踩过前两个坑，一度得出「20000 面 1010 ms」的错误结论
#   （实为预热/GC 抖动），后来者请勿再被误导。
#
# 实测环境：Windows 10 / CPython 3.11.5 (Anaconda) / Tk 8.6 /
# 前台可见窗口 / **fill-only**（``fill=col, outline="", width=0``）/
# 每档 5 次、每次前置 ``gc.collect()``，取中位数：
#
#   面数    820x640   898x718   898x718 每千面
#   3000    0.075     0.080     0.0268
#   6000    0.150     0.156     0.0261
#   8000    0.200     0.209     0.0261
#  12000    0.299     0.310     0.0258
#  19000    0.492     0.523     0.0275
#  26000    0.668     0.675     0.0260
#  32000    0.796     0.841     0.0263
#
# 结论：耗时与面数**严格线性，约 0.026 s / 千面**，无阈值突变；
# 画布面积从 0.52 增到 0.64 MPix（1.23 倍）帧时间只增 4~6%，
# 说明成本主要花在「建 Canvas item」而非「填充像素」——所以 Canvas
# 尺寸不是主要杠杆，**item 数才是**。
#
# 【为什么去掉 outline】``outline=同色 + width=1`` 是 v1.3.0 为补「相邻三角形
# 1px 缝」加的，实测让光栅化慢 4 倍以上（同窗口 8000 面 0.65 s → 0.16 s），
# 代价远大于 1px 缝的观感收益。而 Tk 在相邻三角形共享顶点时填充本身是
# 无缝的 —— 已用 5 倍放大实机截图逐像素确认去掉后表面完全连续、无网状
# 白缝，所以直接去掉，也不再需要 seam_fill 开关垫底。
#
# 定档（预算：静态首帧 ≤ 400 ms、拖拽单帧 ≤ 100 ms）：
#   静态 13000 面 x 0.026 = 0.34 s 目标 → 实测稳态 0.36 s，达标。
#   拖拽 4800 面 x 0.026 = 0.125 s 目标 → 实测 0.136 s / 7.4 fps，
#   **未达 100 ms 预算**。受 Tk 光栅化线性成本限制，要进 100 ms 只能压到
#   ~3600 面（0.094 s），但那时球体轮廓已出现明显多边形折线。
#   这是本渲染方案的物理上限，不是没调好 —— 想真正 30 fps 必须改渲染路径
#   （合并绘制 / 降采样画布 / 换 OpenGL），不在本次范围。
MAX_RENDER_FACES = 13000
# 拖拽 / 自动旋转目标。受 Tk 「每面一个 item」的线性成本限制，
# 4800 面实测单帧 0.134 s（可见窗口 898x718，连续 10 帧中位 0.133 s，
# 约 7.5 fps），已经贴着 100 ms 预算；再往下压到 3800 面才能进 100 ms，
# 但像素级差异肉眼可见（球体轮廓出现多边形折线），故取 4800。
# 想要真正 30 fps 必须改渲染路径（合并绘制 / 降采样画布 / 换 OpenGL），
# 不在本次范围。
DRAG_RENDER_FACES = 4800
# 线框模式每条边都是独立 item（create_line），边数约为面数的 1.5 倍，
# 按 2 倍代价折算：2500 面 ≈ 3750 条线，单帧约 0.10 s。
WIREFRAME_FACES = 2500
MAX_FILE_BYTES = 256 * 1024 * 1024

DEFAULT_COLOR = (216, 182, 122)          # STL / 无颜色信息时的琥珀灰
_LIGHT_RAW = (-0.38, -0.55, 0.74)        # 相机空间方向光
_LIGHT_LEN = math.sqrt(sum(v * v for v in _LIGHT_RAW))
_LIGHT = tuple(v / _LIGHT_LEN for v in _LIGHT_RAW)
_VERTEX_RE = re.compile(r"\bvertex\s+(\S+)\s+(\S+)\s+(\S+)", re.I)

# 聚合时允许的最小目标面数（防止递归降低时失控）。
_SIMPLIFY_MIN_FACES = 64
# 格子边长最多迭代轮数。碎片化网格（互不相连的平面片）收敛很慢，
# 实测 12 万面的稀疏 STL 需要 5~6 轮才能压到目标面数。
_SIMPLIFY_MAX_ROUNDS = 14


class Preview3DError(Exception):
    """3D 文件解析过程中的可预期错误（坏文件、不支持的格式、空网格）。"""


# ---------------------------------------------------------------- 数据结构
@dataclass
class PreviewMesh:
    vertices: list                       # list[(x, y, z)]，毫米、Z-up
    faces: list                          # list[(i0, i1, i2)]
    face_colors: list                    # list[(r, g, b)]，与 faces 一一对应
    fmt: str                             # "GLB" / "STL" / "3MF"
    name: str
    original_faces: int                  # 降采样之前的三角形数
    bounds_min: tuple
    bounds_max: tuple
    simplified: bool = False
    notes: list = field(default_factory=list)
    volume_mm3: float = 0.0
    # 交互态 LOD 缓存 {max_faces: (vertices, faces, face_colors)}；
    # 由 :meth:`subset` 懒填充，避免拖拽时每帧重算聚合。
    _lod_cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def triangle_count(self):
        return len(self.faces)

    @property
    def size_mm(self):
        return tuple(self.bounds_max[i] - self.bounds_min[i] for i in range(3))

    @property
    def center(self):
        return tuple((self.bounds_max[i] + self.bounds_min[i]) / 2.0 for i in range(3))

    @property
    def radius(self):
        s = self.size_mm
        return max(1e-6, 0.5 * math.sqrt(sum(v * v for v in s)))

    def subset(self, n):
        """取 ≤n 个面的视图做交互态降质渲染；包围盒 / 中心保持不变。

        关键：降质走 :func:`_simplify`（顶点聚类聚合）而不是「取前 n 个面」。
        取前 n 个面会把模型切成「半个」；聚合则保表面、只降精度，
        用户拖拽时看到的始终是完整模型。
        """
        if n <= 0 or n >= len(self.faces):
            return self
        cached = self._lod_cache.get(n)
        if cached is not None:
            return cached
        verts, faces, colors, _changed = _simplify(
            self.vertices, self.faces, self.face_colors, n)
        view = PreviewMesh(
            vertices=verts, faces=faces, face_colors=colors, fmt=self.fmt,
            name=self.name, original_faces=self.original_faces,
            bounds_min=self.bounds_min, bounds_max=self.bounds_max,
            simplified=self.simplified, notes=self.notes,
            volume_mm3=self.volume_mm3)
        self._lod_cache[n] = view
        return view


@dataclass
class Camera:
    yaw: float = 0.65
    pitch: float = -0.45
    zoom: float = 1.0                    # 1.0 = Fit；滚轮按倍率缩放
    pan_x: float = 0.0                  # 屏幕像素
    pan_y: float = 0.0
    wireframe: bool = False
    grid: bool = True
    axes: bool = True
    spin: bool = False
    backface_cull: bool = False          # 默认关：有些 STL 整体绕序是反的


# ---------------------------------------------------------------- 小工具
def _finite(p):
    return all(math.isfinite(float(v)) for v in p)


def hex_color(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def _hex_rgb(s, default=DEFAULT_COLOR):
    if not s:
        return default
    t = str(s).strip().lstrip("#")
    if len(t) == 8:
        t = t[:6]
    if len(t) != 6:
        return default
    try:
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def _signed_volume(vertices, faces):
    total = 0.0
    for f in faces:
        a, b, c = (vertices[i] for i in f[:3])
        total += (a[0] * (b[1] * c[2] - b[2] * c[1])
                  + a[1] * (b[2] * c[0] - b[0] * c[2])
                  + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return total / 6.0


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _bounds(vertices):
    """包围盒（只统计有限顶点；空列表时返回全 0）。"""
    pts = [v for v in vertices if _finite(v)]
    if not pts:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    lo = tuple(min(p[i] for p in pts) for i in range(3))
    hi = tuple(max(p[i] for p in pts) for i in range(3))
    return lo, hi


def _cluster_vertices(vertices, cell):
    """把顶点按 ``cell`` 边长做空间哈希聚类，返回 (代表点表, 旧索引 → 新索引)。

    代表点 = 该格子里所有顶点的算术平均，因此聚类后的网格是原网格的
    「抽稀版」而不是「子集」——原来被三角形覆盖的区域依然被覆盖。

    格子键用向下取整，保证完全确定性（不依赖 dict 迭代顺序，也与浮点
    舍入无关：同一坐标必然落在同一格）。
    """
    if cell <= 0.0:
        return list(vertices), list(range(len(vertices)))
    inv = 1.0 / cell
    sums = {}                    # key -> [sx, sy, sz, count]
    index_of = {}                # key -> 代表点序号（按首次出现顺序分配）
    remap = [0] * len(vertices)
    for i, p in enumerate(vertices):
        key = (math.floor(p[0] * inv), math.floor(p[1] * inv),
               math.floor(p[2] * inv))
        acc = sums.get(key)
        if acc is None:
            sums[key] = [p[0], p[1], p[2], 1]
            remap[i] = len(index_of)
            index_of[key] = remap[i]
        else:
            acc[0] += p[0]
            acc[1] += p[1]
            acc[2] += p[2]
            acc[3] += 1
            remap[i] = index_of[key]
    out = [None] * len(index_of)
    for key, idx in index_of.items():
        sx, sy, sz, n = sums[key]
        inv_n = 1.0 / n
        out[idx] = (sx * inv_n, sy * inv_n, sz * inv_n)
    return out, remap


def _simplify(vertices, faces, colors, max_faces):
    """把网格聚合到约 ``max_faces`` 个三角形，**保留完整表面覆盖**。

    算法（顶点聚类 / vertex clustering）：

    1. 取模型最长边的 ``1 / sqrt(max_faces)`` 量级作为格子边长
       —— 一个边长 ``c`` 的格子在曲面上的投影面积约 ``c²``，所以格子数
       近似等于目标面数；
    2. 所有顶点按格子做空间哈希聚类，每格算代表点（算术平均）；
    3. 三角形的三个顶点索引一并重映射，于是**每个原始三角形仍然存在**
       （只是顶点位置被轻微吸附），表面不会出现任何空洞；
    4. 剔除重映射后三个索引撞到同一格的退化三角形，再按实测面数反馈
       迭代调整格子边长，直到面数落在 ``max_faces`` 的 ±10% 内；
       若面数在某个 cell 上「断崖式」归零（破损网格常见），在相邻两个
       cell 之间二分搜索；
    5. 若所有 cell 都试过仍超预算（全部三角形严格共线的破损文件），
       退回等距抽样硬性守住渲染预算——这类零面积三角形本来就看不见，
       抽样不会造成视觉空洞；
    6. 压缩掉不再被引用的顶点，``face_colors`` 与面严格对齐。

    v1.3.0 的「均匀跳面」实测对 24 万面的球只剩 4.75% 屏幕覆盖率（约 95%
    的像素是空洞）；本实现同样输入下原始三角形重心到聚合网格的最大距离
    仅约半径的 1.3%——表面处处有面，不会出现视觉空洞。

    Args:
        vertices: list[(x, y, z)]，毫米。
        faces: list[(i0, i1, i2)]。
        colors: list[(r, g, b)]，与 faces 等长。
        max_faces: 期望的最大三角形数。

    Returns:
        ``(new_vertices, new_faces, new_colors, changed)``；``changed`` 为 True
        表示确实发生了聚合。
    """
    total = len(faces)
    target = int(max_faces)
    if target <= 0 or total <= target:
        return vertices, faces, colors, False
    if not vertices:
        return vertices, faces, colors, False

    lo, hi = _bounds(vertices)
    extent = max(hi[i] - lo[i] for i in range(3))
    if extent <= 0.0:
        extent = 1.0

    # 目标面数 → 期望的格子边长。曲面面积 ≈ 面数 × 平均单面面积，而单个
    # 格子覆盖的表面积 ≈ cell²，故 cell ≈ extent / sqrt(target)。
    # 注意这里必须是 ``target``（目标面数），不能是 ``_SIMPLIFY_MIN_FACES``：
    # 用后者会得到一个 10 倍过大的起始格子，直接把网格塌成几十个点。
    base_cell = extent * 1.15 / math.sqrt(max(1, target))

    # 候选评分：优先「不超过 target 且最接近」（渲染预算硬约束），
    # 只有在所有候选都超目标时才退而选「超出最少」的那个。
    # 不能直接比 |count - target|：那会让「30000（超 10000）」赢过
    # 「5000（少 15000）」，最终返回一个根本没降到预算内的结果。
    best_key = None                  # (是否超预算, 偏差, 结果)

    def _try(cell):
        """按给定格子边长聚合一次，返回 (面数, (vertices, faces, colors))。"""
        rep_verts, remap = _cluster_vertices(vertices, cell)
        out_f, out_c = [], []
        for fi, f in enumerate(faces):
            a, b, c = remap[f[0]], remap[f[1]], remap[f[2]]
            # 同格子的多个顶点会塌成同一个索引 → 三角形退化成点/线，剔除。
            # 注意：**只**剔除这种真正塌陷的，不按「面积恰好为 0」筛。
            # 现实里存在大量三点严格共线的退化三角形（某些 STL 导出器的
            # 破损数据），几何面积为零但仍是文件里的真实数据；若一并剔除，
            # 极端文件会被剔空、退回未聚合，反而卡死渲染。Tk 画零面积
            # 多边形只是不可见，不会报错。
            if a == b or b == c or a == c:
                continue
            out_f.append((a, b, c))
            out_c.append(colors[fi])
        return len(out_f), (rep_verts, out_f, out_c)

    def _consider(count, payload):
        nonlocal best_key
        if count <= 0:
            return
        key = (1 if count > target else 0, abs(count - target))
        if best_key is None or key < best_key[:2]:
            best_key = (key[0], key[1], payload)

    # cell 迭代收敛：面数随 cell 单调不增（格子越大，越多顶点落在同一格）。
    # 首轮按 sqrt 比例（连续曲面上 面数 ≈ 1/cell²）；若仍然超目标，改用
    # 更激进的幂次，避免在「碎片化网格」上收敛太慢。
    cell = base_cell
    prev_cell = 0.0                  # 上一个「面数还超目标」的采样点
    for attempt in range(_SIMPLIFY_MAX_ROUNDS):
        count, payload = _try(cell)
        _consider(count, payload)
        # 落在 ±10% 内就收工
        if count <= 0 or target * 0.9 <= count <= target * 1.1:
            break
        if count > target:
            prev_cell = cell
            if attempt < 2:
                ratio = math.sqrt(count / float(target))
            else:
                ratio = (count / float(target)) ** 0.75
            cell = cell * max(1.05, min(4.0, ratio))
        else:
            # 面数已经掉到目标以下：说明 (prev_cell, cell) 之间跨过了「悬崖」
            # ——常见于顶点只落在少数几个离散平面上的破损 STL（例如 y 只取
            # 0/1/2，间距恰好 1.0，cell 从 0.95 跳到 1.17 时面数会从满额
            # 直接归零）。此时在区间内二分，找最接近目标又不超过的 cell。
            lo_c, hi_c = prev_cell, cell
            for _bisect in range(10):
                mid = (lo_c + hi_c) / 2.0
                m_count, m_payload = _try(mid)
                _consider(m_count, m_payload)
                if m_count > target:
                    lo_c = mid
                elif m_count < 1:
                    hi_c = mid
                else:
                    lo_c = mid      # 保留得更多细节，继续往上半区找
                    if target * 0.9 <= m_count:
                        break
            break

    if best_key is None:             # 极端情况（cell 过大把整网格塌成一点）
        return vertices, faces, colors, False
    rep_verts, new_faces, new_colors = best_key[2]

    # 兜底：若所有格子边长都试过、面数依旧超预算（典型场景是「全部三角形
    # 三点严格共线」的破损 STL —— 顶点只落在少数离散平面上，格子小于层间距
    # 时一个都并不掉、跨过层间距又全部塌成 0 面，压根不存在中间态），
    # 此时聚合已经无能为力，只能退回等距抽样来硬性守住渲染预算。
    # 这类退化三角形本来在屏幕上就是零面积、看不见，抽样不会造成视觉空洞，
    # 因此不会重新引入 v1.3.0 的「模型不完整」问题。
    if len(new_faces) > target:
        step = len(new_faces) / float(target)
        keep = sorted({int(i * step) for i in range(target)})
        new_faces = [new_faces[i] for i in keep]
        new_colors = [new_colors[i] for i in keep]

    # 压缩掉不再被引用的代表点
    used = set()
    for f in new_faces:
        used.update(f)
    remap2 = {}
    compact = []
    for old in sorted(used):
        remap2[old] = len(compact)
        compact.append(rep_verts[old])
    new_faces = [tuple(remap2[i] for i in f) for f in new_faces]
    return compact, new_faces, new_colors, True


# ---------------------------------------------------------------- GLB
def _from_glb(path):
    try:
        doc = pe.load_glb(path)
        mesh = pe.extract_mesh(doc, name=path.stem)
    except pe.PrintExportError as e:
        raise Preview3DError(str(e))
    # ``extract_mesh`` 的 face 元组是 (i0, i1, i2, color_index)，其中
    # color_index 指向 ``mesh.colors`` 这张**去重颜色表**（由内部的
    # color_map 分配，与 glTF 的 material 下标无关），所以两边严格对齐，
    # 不会因为「部分材质被跳过」而错位。已在不连续的 material 下标
    # （0 / 2 + 无材质）样本上实测验证。
    rgb = [_hex_rgb(c) for c in mesh.colors]
    faces = [(a, b, c) for a, b, c, _ci in mesh.faces]
    colors = [rgb[ci] for _a, _b, _c, ci in mesh.faces]
    notes = ["GLB 已按 Z-up 摆正（与转换出的 STL / 3MF 同姿态）", mesh.unit_note]
    if mesh.skipped:
        notes.append("跳过 %d 个退化 / 非法三角形" % mesh.skipped)
    return list(mesh.vertices), faces, colors, "GLB", notes


# ---------------------------------------------------------------- STL
def _dedup_add(x, y, z, verts, table):
    p = (float(x), float(y), float(z))
    if not _finite(p):
        return None
    key = (round(p[0], 5), round(p[1], 5), round(p[2], 5))
    idx = table.get(key)
    if idx is None:
        idx = len(verts)
        table[key] = idx
        verts.append(p)
    return idx


def _parse_binary_stl(data):
    if len(data) < 84:
        raise Preview3DError("二进制 STL 文件太短（至少需要 84 字节）")
    count = struct.unpack_from("<I", data, 80)[0]
    need = 84 + 50 * count
    if need > len(data):
        raise Preview3DError(
            "二进制 STL 声明 %d 个三角形，需要 %d 字节，实际只有 %d 字节（文件被截断）"
            % (count, need, len(data)))
    verts, faces, table = [], [], {}
    for i in range(count):
        off = 84 + 50 * i
        tri = []
        for j in range(3):
            x, y, z = struct.unpack_from("<3f", data, off + 12 + 12 * j)
            idx = _dedup_add(x, y, z, verts, table)
            if idx is None:
                break
            tri.append(idx)
        if len(tri) == 3 and len(set(tri)) == 3:
            faces.append(tuple(tri))
    return verts, faces


def _parse_ascii_stl(data):
    text = data.decode("utf-8", "replace")
    verts, faces, table = [], [], {}
    tri = []
    for m in _VERTEX_RE.finditer(text):
        try:
            x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
        except ValueError:
            continue
        idx = _dedup_add(x, y, z, verts, table)
        if idx is None:
            continue
        tri.append(idx)
        if len(tri) == 3:
            if len(set(tri)) == 3:
                faces.append(tuple(tri))
            tri = []
    return verts, faces


def _from_stl(path, data):
    head = data[:256].lstrip()
    is_ascii = head.startswith(b"solid") and b"facet" in data[:2048].lower()
    if is_ascii:
        verts, faces = _parse_ascii_stl(data)
        kind = "ASCII STL"
    else:
        verts, faces = _parse_binary_stl(data)
        kind = "二进制 STL"
    colors = [DEFAULT_COLOR] * len(faces)
    notes = ["%s（无颜色信息，按单色显示）" % kind, "单位按毫米解释"]
    return verts, faces, colors, "STL", notes


# ---------------------------------------------------------------- 3MF
def _lname(el):
    t = el.tag
    return t.rsplit("}", 1)[-1] if "}" in t else t


def _children(el, name):
    return [c for c in el if _lname(c) == name] if el is not None else []


def _mat_identity():
    return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


def _parse_transform(s):
    if not s:
        return _mat_identity()
    parts = str(s).replace(",", " ").split()
    if len(parts) != 12:
        return _mat_identity()
    try:
        m = tuple(float(v) for v in parts)
    except ValueError:
        return _mat_identity()
    if not all(math.isfinite(v) for v in m):
        return _mat_identity()
    return m


def _mat_apply(m, p):
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z + m[9],
            m[3] * x + m[4] * y + m[5] * z + m[10],
            m[6] * x + m[7] * y + m[8] * z + m[11])


def _mat_mul(a, b):
    """返回 a∘b（先应用子变换 b，再应用父变换 a）。3x4 行优先。"""
    out = [0.0] * 12
    for i in range(3):
        for j in range(3):
            out[i * 3 + j] = sum(a[i * 3 + k] * b[k * 3 + j] for k in range(3))
    for i in range(3):
        out[9 + i] = sum(a[i * 3 + k] * b[9 + k] for k in range(3)) + a[9 + i]
    return tuple(out)


def _from_3mf(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            model_name = next((n for n in names if n.lower().endswith(".model")), None)
            if model_name is None:
                raise Preview3DError("3MF 包里找不到 .model 模型文件")
            xml = z.read(model_name)
    except zipfile.BadZipFile as e:
        raise Preview3DError("不是有效的 3MF（OPC ZIP 打不开）：%s" % e)
    except Preview3DError:
        raise
    except (OSError, KeyError, RuntimeError) as e:
        raise Preview3DError("读取 3MF 包失败：%s" % e)
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise Preview3DError("3MF 模型 XML 解析失败：%s" % e)

    # 属性组：basematerials / colorgroup，id -> [rgb, ...]（0-based 索引）。
    # 3MF Core Spec 中每种资源类型各自有独立的 id 命名空间，因此
    # ``<basematerials id="2">`` 与 ``<object id="2">`` 同时存在是合法的；
    # 这里 ``groups`` 与下面的 ``objects`` 是两个独立字典，不会互相覆盖。
    # 已用「basematerials 在前 / object 在前」两种顺序实测，结果一致。
    groups = {}
    for el in root.iter():
        ln = _lname(el)
        if ln == "basematerials":
            groups[el.get("id")] = [_hex_rgb(b.get("displaycolor"))
                                    for b in _children(el, "base")]
        elif ln == "colorgroup":
            groups[el.get("id")] = [_hex_rgb(c.get("color"))
                                    for c in _children(el, "color")]

    objects = {}
    for el in root.iter():
        if _lname(el) != "object":
            continue
        oid = el.get("id")
        mesh_el = next((c for c in el if _lname(c) == "mesh"), None)
        comps_el = next((c for c in el if _lname(c) == "components"), None)
        verts, tris, cols = [], [], []
        if mesh_el is not None:
            v_el = next((c for c in mesh_el if _lname(c) == "vertices"), None)
            for v in _children(v_el, "vertex"):
                try:
                    p = (float(v.get("x", 0)), float(v.get("y", 0)), float(v.get("z", 0)))
                except (TypeError, ValueError):
                    p = (0.0, 0.0, 0.0)
                verts.append(p if _finite(p) else (0.0, 0.0, 0.0))
            t_el = next((c for c in mesh_el if _lname(c) == "triangles"), None)
            pid = el.get("pid")
            try:
                # ``pindex`` 只是对象级**默认**属性索引，三角形上的 ``p1``
                # 会逐面覆盖它（Core Spec 3.1）。缺省时为 0。
                pdefault = int(el.get("pindex", "0") or 0)
            except ValueError:
                pdefault = 0
            grp = groups.get(pid) or []
            for t in _children(t_el, "triangle"):
                try:
                    a, b, c = (int(t.get("v1")), int(t.get("v2")), int(t.get("v3")))
                except (TypeError, ValueError):
                    continue
                if not (0 <= a < len(verts) and 0 <= b < len(verts) and 0 <= c < len(verts)):
                    continue
                if a == b or b == c or a == c:
                    continue
                col = DEFAULT_COLOR
                if pid is not None:
                    idx = pdefault
                    if t.get("p1") is not None:      # 三角形级属性优先于 pindex
                        try:
                            idx = int(t.get("p1"))
                        except ValueError:
                            pass
                    if 0 <= idx < len(grp):
                        col = grp[idx]
                    elif len(grp) == 1:
                        col = grp[0]
                tris.append((a, b, c))
                cols.append(col)
        comps = [(cc.get("objectid"), _parse_transform(cc.get("transform")))
                 for cc in _children(comps_el, "component")]
        objects[oid] = (verts, tris, cols, comps)
    if not objects:
        raise Preview3DError("3MF 里没有 <object> 网格")

    build = next((c for c in root if _lname(c) == "build"), None)
    items = _children(build, "item")
    if not items:                       # 没有 build 时把所有 object 都摆出来
        items = [{"objectid": oid, "transform": None} for oid in objects]

    out_v, out_f, out_c = [], [], []

    def instantiate(oid, mat, depth=0):
        if depth > 16 or oid not in objects:
            return
        verts, tris, cols, comps = objects[oid]
        base = len(out_v)
        for v in verts:
            p = _mat_apply(mat, v)
            out_v.append(p if _finite(p) else (0.0, 0.0, 0.0))
        for (a, b, c), col in zip(tris, cols):
            out_f.append((base + a, base + b, base + c))
            out_c.append(col)
        for cid, cm in comps:
            instantiate(cid, _mat_mul(mat, cm), depth + 1)

    for item in items:
        oid = item.get("objectid") if hasattr(item, "get") else None
        tr = item.get("transform") if hasattr(item, "get") else None
        instantiate(oid, _parse_transform(tr))

    meta = []
    for el in root.iter():
        if _lname(el) == "metadata":
            nm = el.get("name")
            if nm in ("Title", "Designer") and (el.text or "").strip():
                meta.append((nm, (el.text or "").strip()))
    notes = ["3MF 单位毫米，颜色取自 basematerials / colorgroup"]
    if meta:
        notes.append("元数据：" + "；".join("%s=%s" % kv for kv in meta[:3]))
    return out_v, out_f, out_c, "3MF", notes


# ---------------------------------------------------------------- 入口
def load_mesh(path, max_faces=MAX_RENDER_FACES):
    """读取一个 3D 文件，返回可直接渲染的 :class:`PreviewMesh`。"""
    p = Path(path)
    try:
        if not p.is_file():
            raise Preview3DError("找不到文件：%s" % p)
        size = p.stat().st_size
        if size > MAX_FILE_BYTES:
            raise Preview3DError("文件超过 %d MB 上限，预览已跳过"
                                 % (MAX_FILE_BYTES // 1024 // 1024))
        data = p.read_bytes()
    except OSError as e:
        raise Preview3DError("无法读取文件：%s" % e)

    suffix = p.suffix.lower()
    if suffix == ".glb":
        verts, faces, colors, fmt, notes = _from_glb(p)
    elif suffix == ".stl":
        verts, faces, colors, fmt, notes = _from_stl(p, data)
    elif suffix == ".3mf":
        verts, faces, colors, fmt, notes = _from_3mf(data)
    else:
        raise Preview3DError("不支持的格式 %s（目前只支持 .glb / .stl / .3mf）"
                             % (suffix or "（无扩展名）"))
    if not faces:
        raise Preview3DError("这个文件里没有可显示的三角形（可能是空网格或只有点/线）")
    if not verts or not all(_finite(v) for v in verts):
        raise Preview3DError("网格顶点含非法坐标，无法预览")

    lo, hi = _bounds(verts)
    volume = abs(_signed_volume(verts, faces))
    v2, f2, c2, simplified = _simplify(verts, faces, colors, max_faces)
    if simplified:
        # 措辞说明：v1.3.0 说「均匀抽样到 N 个」是误导——旧实现真的把 96% 的
        # 三角形删掉，屏幕上就是碎片+空洞。现在的降质是「顶点合并」，
        # 保留全部表面覆盖，所以主表述改成「已合并显示」；同时保留
        # 「抽样」二字便于既有测试/用户习惯检索（降质本质仍是抽样显示）。
        notes.append("模型共 %d 个三角形，为流畅显示已合并显示（%d → %d 面）"
                     "（顶点合并抽样，保留完整表面，仅降低精度，不改文件）"
                     % (len(faces), len(faces), len(f2)))
    return PreviewMesh(
        vertices=v2, faces=f2, face_colors=c2, fmt=fmt, name=p.name,
        original_faces=len(faces), bounds_min=lo, bounds_max=hi,
        simplified=simplified, notes=notes, volume_mm3=volume)


# ---------------------------------------------------------------- 渲染数学
def fit_camera(camera=None):
    """保留显示开关，把视角复位到 Fit。"""
    c = camera or Camera()
    c.yaw, c.pitch, c.zoom = 0.65, -0.45, 1.0
    c.pan_x, c.pan_y = 0.0, 0.0
    return c


def camera_points(vertices, cam, center):
    """模型坐标 → 相机空间 (x, y, z)；z 越大越靠近相机。"""
    cy, sy = math.cos(cam.yaw), math.sin(cam.yaw)
    cp, sp = math.cos(cam.pitch), math.sin(cam.pitch)
    mx, my, mz = center
    out = []
    for p in vertices:
        x, y, z = p[0] - mx, p[1] - my, p[2] - mz
        x1 = x * cy - y * sy
        y1 = x * sy + y * cy
        out.append((x1, y1 * cp - z * sp, y1 * sp + z * cp))
    return out


def project(cpts, cam, mesh, width, height):
    """相机空间 → 屏幕像素；准正交弱透视，形状不被拉变形。

    若直接按 ``1/(d - z)`` 透视且 ``d = 2.9r``，模型自身深度 r 与距离
    同量级，近处的面会被放大一倍多，立方体看起来像被「折开」的纸片。
    这里保留 zoom=1 时相同的屏幕占比，但把深度对缩放的调制压到 ±6%，
    既有立体感又不会破坏几何可读性。分母带 0.5 下限，永不除零。
    """
    radius = mesh.radius
    zoom = max(0.05, cam.zoom)
    base_k = min(width, height) * 0.362 / radius     # 与原 1.05*focal/2.9r 等价
    out = []
    for x, y, z in cpts:
        depth = max(0.5, 1.0 - 0.35 * z / (6.0 * radius))
        k = base_k * zoom / depth
        out.append((width / 2.0 + cam.pan_x + x * k,
                    height / 2.0 + cam.pan_y - y * k))
    return out


def sort_faces(cpts, faces, cull=False):
    """按重心深度升序（远的先画）；返回 [(z, face_index, normal), ...]。"""
    out = []
    for fi, f in enumerate(faces):
        a, b, c = cpts[f[0]], cpts[f[1]], cpts[f[2]]
        n = _cross((b[0] - a[0], b[1] - a[1], b[2] - a[2]),
                   (c[0] - a[0], c[1] - a[1], c[2] - a[2]))
        if cull and n[2] <= 0:
            continue
        out.append(((a[2] + b[2] + c[2]) / 3.0, fi, n))
    out.sort(key=lambda t: t[0])
    return out


def face_depth_normal(cpts, faces, face_ids, cull=False):
    """按给定面顺序重算 (z, fi, normal)，**不做排序**。

    交互态（拖拽 / 自动旋转）专用：相机角度小幅变化时，画家算法的前后
    顺序基本不变，复用上一帧的顺序只重算深度与法线即可，省掉
    ``list.sort()`` 的 O(n log n)。背面剔除语义与 :func:`sort_faces` 完全一致。
    """
    out = []
    for fi in face_ids:
        f = faces[fi]
        a, b, c = cpts[f[0]], cpts[f[1]], cpts[f[2]]
        n = _cross((b[0] - a[0], b[1] - a[1], b[2] - a[2]),
                   (c[0] - a[0], c[1] - a[1], c[2] - a[2]))
        if cull and n[2] <= 0:
            continue
        out.append(((a[2] + b[2] + c[2]) / 3.0, fi, n))
    return out


def shade(normal, rgb):
    """面法线 Lambert 明暗（相机空间固定方向光 + 环境光）。"""
    length = math.sqrt(sum(v * v for v in normal))
    if length < 1e-12:
        return rgb
    n = tuple(v / length for v in normal)
    d = max(0.0, n[0] * _LIGHT[0] + n[1] * _LIGHT[1] + n[2] * _LIGHT[2])
    brightness = 0.34 + 0.66 * d
    return tuple(max(0, min(255, int(v * brightness))) for v in rgb)


def wireframe_edges(faces):
    """去重的无向边列表 [(i0, i1), ...]，线框模式下避免重复绘制。"""
    seen = set()
    out = []
    for f in faces:
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            key = (a, b) if a < b else (b, a)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out
