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
    "MAX_RENDER_FACES", "DRAG_RENDER_FACES", "MAX_FILE_BYTES",
    "fit_camera", "camera_points", "project", "sort_faces", "shade",
    "wireframe_edges", "hex_color",
]

# Canvas 每三角形一个 item，面数必须设上限；拖拽 / 自动旋转时进一步降质。
MAX_RENDER_FACES = 9000
DRAG_RENDER_FACES = 1800
MAX_FILE_BYTES = 256 * 1024 * 1024

DEFAULT_COLOR = (216, 182, 122)          # STL / 无颜色信息时的琥珀灰
_LIGHT_RAW = (-0.38, -0.55, 0.74)        # 相机空间方向光
_LIGHT_LEN = math.sqrt(sum(v * v for v in _LIGHT_RAW))
_LIGHT = tuple(v / _LIGHT_LEN for v in _LIGHT_RAW)
_VERTEX_RE = re.compile(r"\bvertex\s+(\S+)\s+(\S+)\s+(\S+)", re.I)


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
        """取前 n 个面做交互态降质渲染；包围盒 / 中心保持不变。"""
        if n >= len(self.faces):
            return self
        return PreviewMesh(
            vertices=self.vertices, faces=self.faces[:n],
            face_colors=self.face_colors[:n], fmt=self.fmt, name=self.name,
            original_faces=self.original_faces, bounds_min=self.bounds_min,
            bounds_max=self.bounds_max, simplified=self.simplified,
            notes=self.notes, volume_mm3=self.volume_mm3)


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


def _simplify(vertices, faces, colors, max_faces):
    """均匀抽样到 max_faces，并压缩掉不再被引用的顶点。"""
    total = len(faces)
    if total <= max_faces:
        return vertices, faces, colors, False
    picked = [int(i * total / max_faces) for i in range(max_faces)]
    used, remap = set(), {}
    for i in picked:
        used.update(faces[i][:3])
    new_verts = []
    for old in sorted(used):
        remap[old] = len(new_verts)
        new_verts.append(vertices[old])
    new_faces = [tuple(remap[j] for j in faces[i][:3]) for i in picked]
    new_colors = [colors[i] for i in picked]
    return new_verts, new_faces, new_colors, True


# ---------------------------------------------------------------- GLB
def _from_glb(path):
    try:
        doc = pe.load_glb(path)
        mesh = pe.extract_mesh(doc, name=path.stem)
    except pe.PrintExportError as e:
        raise Preview3DError(str(e))
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

    # 属性组：basematerials / colorgroup，id -> [rgb, ...]（0-based 索引）
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
                    if t.get("p1") is not None:
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

    lo = tuple(min(v[i] for v in verts) for i in range(3))
    hi = tuple(max(v[i] for v in verts) for i in range(3))
    volume = abs(_signed_volume(verts, faces))
    v2, f2, c2, simplified = _simplify(verts, faces, colors, max_faces)
    if simplified:
        notes.append("模型共 %d 个三角形，预览均匀抽样到 %d 个（仅影响显示，不改文件）"
                     % (len(faces), len(f2)))
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
