# -*- coding: utf-8 -*-
"""
GLB → 3D 打印文件（二进制 STL + 3MF）。纯 Python 标准库实现，零新增打包依赖。

设计要点
--------
* 只读取一个已有的 ``.glb``（glTF 2.0 binary），不改下载 / Node 转换流程。
* 解析 JSON chunk + BIN chunk，支持 bufferView.byteStride（交错顶点）、
  5120/5121/5122/5123/5125/5126 各类 componentType、normalized 整数、
  sparse accessor、内嵌 / data URI / 外置 .bin 三种 buffer。
* 遍历 scene → nodes，累积每个节点的 4x4 变换（matrix 或 TRS 四元数），
  支持 TRIANGLES / TRIANGLE_STRIP / TRIANGLE_FAN；含镜像（det < 0）的节点
  自动翻转三角形绕序，避免导出后法线朝里。
* glTF 是 Y-up，切片软件（Cura / PrusaSlicer / Bambu Studio 等）按 Z-up 解释
  STL / 3MF，默认做一次纯旋转 ``(x, y, z) -> (x, -z, y)``，否则模型会躺着。
  这正是本项目 ``extlib/osg-scene.js`` 里 ZUP_TO_YUP_MAT3 的逆变换。
* STL 是无单位的二进制网格（不含颜色）；3MF 用 OPC 包 + Core Spec 的
  ``<basematerials>`` 写多色，颜色来自 glTF 的
  ``pbrMetallicRoughness.baseColorFactor``（线性 → sRGB）。

「直接可用」保证（切片软件导入即可打印）
----------------------------------------
* STL 与 3MF 的顶点统一平移到正八分象限，最小点落在 ``(0, 0, 0)``：
  模型底面贴在打印平台上，不会沉到平台下方被切掉。
* 闭合网格的有向体积为负时整体翻转三角形绕序，保证法线朝外；
  STL 法线与绕序一致，3MF 三角形按 CCW 书写。
* 3MF 严格按 Core Spec：``resources`` 内所有 ``id`` 全局唯一，
  ``<object>`` 同时带 ``pid`` 与 ``pindex``，``<metadata>`` 只用规范表 3-1
  的 well-known 名称；三角形不足 4 个时不谎称 ``type="model"``，
  降级为 ``other`` 并在日志里提示这类网格通常无法直接打印。
* 两种格式共用同一份几何，尺寸、朝向、摆放完全一致。

已知限制（会在转换日志里如实说明）
----------------------------------
* 贴图颜色无法无损转进 3MF：只取材质的 baseColorFactor 常量色，
  有 baseColorTexture 的材质退化为白色。
* 骨骼蒙皮（skins）、morph targets、动画取静态姿势（顶点本身），
  不重算骨骼变换。
* 点 / 线 primitive 不是实体，直接跳过。
* 3MF 的 ``basematerials`` 只能表达「每三角形一个纯色」，不能做顶点渐变。

单位换算
--------
glTF 规范长度单位是米，3MF 规范是毫米，STL 被切片软件普遍按毫米解释。
``scale=None``（默认）时按包围盒最长边自动判断：最长边 < 10 认为是米制
（×1000），否则认为数值本身已是毫米（1:1）。调用方可显式传 ``scale`` 覆盖，
返回结果里会写清实际换算与最终尺寸，便于核对。
"""

import base64
import json
import math
import struct
import zipfile
from pathlib import Path
from urllib.parse import unquote

__all__ = [
    "PrintExportError", "Mesh", "load_glb", "extract_mesh",
    "write_stl", "write_3mf", "convert",
    "placement_offset", "placed_vertices",
]


class PrintExportError(Exception):
    """GLB 解析 / 打印文件导出过程中的可预期错误。"""


# ---- glTF 常量 -----------------------------------------------------------
# componentType -> (struct 格式字符, 字节数)
_COMPONENT = {
    5120: ("b", 1),   # BYTE
    5121: ("B", 1),   # UNSIGNED_BYTE
    5122: ("h", 2),   # SHORT
    5123: ("H", 2),   # UNSIGNED_SHORT
    5125: ("I", 4),   # UNSIGNED_INT
    5126: ("f", 4),   # FLOAT
}
_TYPE_N = {
    "SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
    "MAT2": 4, "MAT3": 9, "MAT4": 16,
}
_SIGNED_MAX = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}

_GLB_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A        # 'JSON'
_BIN_CHUNK = 0x004E4942         # 'BIN\0'

# 列优先 4x4：Y-up → Z-up，(x, y, z) -> (x, -z, y)。纯旋转，det = +1。
_YUP_TO_ZUP = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, -1.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def _identity4():
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def _mul4(a, b):
    """列优先 4x4 相乘（先应用 b，再应用 a）。"""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def _node_matrix(node):
    """glTF node 的 matrix（列优先）或 TRS 组合。"""
    m = node.get("matrix")
    if m is not None:
        if len(m) != 16:
            raise PrintExportError("node.matrix 长度不是 16")
        return [float(v) for v in m]
    t = node.get("translation") or [0.0, 0.0, 0.0]
    r = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]   # x, y, z, w
    s = node.get("scale") or [1.0, 1.0, 1.0]
    x, y, z, w = (float(v) for v in r)
    x2, y2, z2 = x + x, y + y, z + z
    xx, xy, xz = x * x2, x * y2, x * z2
    yy, yz, zz = y * y2, y * z2, z * z2
    wx, wy, wz = w * x2, w * y2, w * z2
    return [
        (1.0 - (yy + zz)) * s[0], (xy + wz) * s[0], (xz - wy) * s[0], 0.0,
        (xy - wz) * s[1], (1.0 - (xx + zz)) * s[1], (yz + wx) * s[1], 0.0,
        (xz + wy) * s[2], (yz - wx) * s[2], (1.0 - (xx + yy)) * s[2], 0.0,
        float(t[0]), float(t[1]), float(t[2]), 1.0,
    ]


def _apply4(m, p):
    x, y, z = p[0], p[1], p[2]
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def _det3(m):
    """4x4 线性部分的行列式，用来判断节点是否含镜像缩放。"""
    return (m[0] * (m[5] * m[10] - m[9] * m[6])
            - m[4] * (m[1] * m[10] - m[9] * m[2])
            + m[8] * (m[1] * m[6] - m[5] * m[2]))


# ---- GLB 容器 ------------------------------------------------------------
class GlbDocument:
    def __init__(self, gltf, buffers, path):
        self.gltf = gltf
        self._buffers = buffers
        self.path = path

    def buffer(self, index):
        try:
            return self._buffers[index]
        except KeyError:
            raise PrintExportError("buffer[%d] 不存在或无法读取" % index)


def load_glb(path):
    """读取并校验 GLB，返回 :class:`GlbDocument`。"""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as e:
        raise PrintExportError("无法读取 GLB：%s" % e)
    if len(data) < 20:
        raise PrintExportError("文件太短，不是有效的 GLB（至少需要 20 字节头）")
    magic, version, declared = struct.unpack_from("<4sII", data, 0)
    if magic != _GLB_MAGIC:
        raise PrintExportError("不是 GLB 文件（缺少 'glTF' 魔数），请选 .glb 而不是 .gltf")
    if version != 2:
        raise PrintExportError("只支持 glTF 2.0，文件声明的版本是 %d" % version)
    if declared > len(data):
        raise PrintExportError("GLB 头部声明长度 %d，实际只有 %d 字节（文件被截断）"
                               % (declared, len(data)))

    chunks = []
    off = 12
    limit = min(declared, len(data))
    while off + 8 <= limit:
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        if clen > limit - off:
            raise PrintExportError("GLB chunk 长度越界（声明 %d，剩余 %d）"
                                   % (clen, limit - off))
        chunks.append((ctype, data[off:off + clen]))
        off += clen

    json_chunk = next((c for t, c in chunks if t == _JSON_CHUNK), None)
    if json_chunk is None:
        raise PrintExportError("GLB 里没有 JSON chunk")
    bin_chunk = next((c for t, c in chunks if t == _BIN_CHUNK), b"")
    try:
        gltf = json.loads(json_chunk.decode("utf-8").rstrip(" \t\r\n\x00"))
    except (UnicodeDecodeError, ValueError) as e:
        raise PrintExportError("GLB 的 JSON chunk 无法解析：%s" % e)
    if not isinstance(gltf, dict):
        raise PrintExportError("GLB 的 JSON chunk 不是对象")

    buffers = {}
    for i, b in enumerate(gltf.get("buffers") or []):
        uri = b.get("uri")
        if uri is None:
            buffers[i] = bin_chunk
        elif isinstance(uri, str) and uri.startswith("data:"):
            head, _, payload = uri.partition(",")
            if "base64" not in head:
                raise PrintExportError("buffer[%d] 的 data URI 不是 base64" % i)
            try:
                buffers[i] = base64.b64decode(payload)
            except Exception as e:
                raise PrintExportError("buffer[%d] 的 data URI 解码失败：%s" % (i, e))
        else:
            rel = unquote(str(uri))
            fp = (p.parent / rel)
            try:
                buffers[i] = fp.read_bytes()
            except OSError as e:
                raise PrintExportError("外置 buffer 读取失败（%s）：%s" % (rel, e))
    return GlbDocument(gltf, buffers, p)


# ---- accessor -------------------------------------------------------------
def _read_view(doc, bv_index, byte_offset, comp, n, count):
    """按 bufferView 读 ``count`` 个 n 维元素，返回 list[list[float]]。"""
    if comp not in _COMPONENT:
        raise PrintExportError("不支持的 componentType %r" % comp)
    views = doc.gltf.get("bufferViews") or []
    if not isinstance(bv_index, int) or not (0 <= bv_index < len(views)):
        raise PrintExportError("bufferView 索引越界：%r" % bv_index)
    bv = views[bv_index]
    fmt, size = _COMPONENT[comp]
    elem = n * size
    stride = bv.get("byteStride") or elem
    if stride < elem:
        raise PrintExportError("bufferView.byteStride %d 小于元素大小 %d" % (stride, elem))
    buf = doc.buffer(bv.get("buffer", 0))
    base = int(bv.get("byteOffset", 0)) + int(byte_offset or 0)
    code = "<" + fmt * n
    out = []
    for i in range(count):
        off = base + i * stride
        if off < 0 or off + elem > len(buf):
            raise PrintExportError("accessor 读取越界：offset=%d, buffer=%d"
                                   % (off, len(buf)))
        out.append(list(struct.unpack_from(code, buf, off)))
    return out


def read_accessor(doc, index):
    """读取一个 accessor，处理 normalized 与 sparse，返回 list[list[float]]。"""
    accs = doc.gltf.get("accessors") or []
    if not isinstance(index, int) or not (0 <= index < len(accs)):
        raise PrintExportError("accessor 索引越界：%r" % index)
    acc = accs[index]
    n = _TYPE_N.get(acc.get("type"))
    if not n:
        raise PrintExportError("不支持的 accessor.type %r" % acc.get("type"))
    comp = acc.get("componentType")
    count = int(acc.get("count", 0))
    if count < 0:
        raise PrintExportError("accessor.count 非法")

    bv_index = acc.get("bufferView")
    if bv_index is None:
        values = [[0.0] * n for _ in range(count)]
    else:
        values = _read_view(doc, bv_index, acc.get("byteOffset", 0), comp, n, count)

    if acc.get("normalized") and comp in _SIGNED_MAX:
        mx = _SIGNED_MAX[comp]
        for row in values:
            for j in range(n):
                row[j] = max(-1.0, float(row[j]) / mx)

    sparse = acc.get("sparse")
    if sparse:
        try:
            ic = sparse["indices"]
            vc = sparse["values"]
            n_idx = int(sparse["count"])
            idx_rows = _read_view(doc, ic["bufferView"], ic.get("byteOffset", 0),
                                  ic["componentType"], 1, n_idx)
            val_rows = _read_view(doc, vc["bufferView"], vc.get("byteOffset", 0),
                                  comp, n, n_idx)
            for k in range(n_idx):
                target = int(idx_rows[k][0])
                if 0 <= target < count:
                    row = list(val_rows[k])
                    if acc.get("normalized") and comp in _SIGNED_MAX:
                        row = [max(-1.0, float(v) / _SIGNED_MAX[comp]) for v in row]
                    values[target] = row
        except (KeyError, TypeError, ValueError) as e:
            raise PrintExportError("sparse accessor 格式错误：%s" % e)
    return values


# ---- 场景图 ---------------------------------------------------------------
def _iter_nodes(doc):
    gltf = doc.gltf
    nodes = gltf.get("nodes") or []
    scenes = gltf.get("scenes") or []
    roots = []
    scene = gltf.get("scene")
    if isinstance(scene, int) and 0 <= scene < len(scenes):
        roots = list((scenes[scene] or {}).get("nodes") or [])
    elif scenes:
        roots = list((scenes[0] or {}).get("nodes") or [])
    if not roots:
        # 没有 scene 的 GLB：把所有节点当根（顺序遍历，重复引用会被合并）
        roots = list(range(len(nodes)))
    stack = [(i, _identity4()) for i in reversed(roots)]
    seen = 0
    while stack:
        idx, parent = stack.pop()
        if not isinstance(idx, int) or not (0 <= idx < len(nodes)):
            continue
        seen += 1
        if seen > 100000:            # 防止畸形文件里的循环引用
            raise PrintExportError("节点层级异常（疑似循环引用）")
        node = nodes[idx] or {}
        world = _mul4(parent, _node_matrix(node))
        yield idx, node, world
        for child in reversed(list(node.get("children") or [])):
            stack.append((child, world))


def _iter_primitives(doc):
    meshes = doc.gltf.get("meshes") or []
    for _idx, node, world in _iter_nodes(doc):
        mi = node.get("mesh")
        if not isinstance(mi, int) or not (0 <= mi < len(meshes)):
            continue
        for prim in (meshes[mi] or {}).get("primitives") or []:
            yield prim, world


def _triangulate(indices, mode):
    if mode == 4:                                     # TRIANGLES
        for i in range(0, len(indices) - 2, 3):
            yield indices[i], indices[i + 1], indices[i + 2]
    elif mode == 5:                                   # TRIANGLE_STRIP
        for i in range(len(indices) - 2):
            if i % 2 == 0:
                yield indices[i], indices[i + 1], indices[i + 2]
            else:
                yield indices[i + 1], indices[i], indices[i + 2]
    elif mode == 6:                                   # TRIANGLE_FAN
        for i in range(1, len(indices) - 1):
            yield indices[0], indices[i], indices[i + 1]


def _linear_to_srgb(c):
    c = max(0.0, min(1.0, float(c)))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _material_color(doc, index):
    """glTF 材质常量色 → ``#RRGGBB``；贴图色无法表达，退回白色。"""
    if index is None:
        return "#FFFFFF"
    mats = doc.gltf.get("materials") or []
    if not isinstance(index, int) or not (0 <= index < len(mats)):
        return "#FFFFFF"
    pbr = (mats[index] or {}).get("pbrMetallicRoughness") or {}
    f = pbr.get("baseColorFactor")
    if not isinstance(f, (list, tuple)) or len(f) < 3:
        return "#FFFFFF"
    out = []
    for v in f[:3]:
        try:
            out.append("%02X" % max(0, min(255, int(round(_linear_to_srgb(v) * 255)))))
        except (TypeError, ValueError):
            out.append("FF")
    return "#" + "".join(out)


def _normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 1e-20:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _finite(p):
    return all(math.isfinite(float(v)) for v in p)


def _signed_volume(vertices, faces):
    """闭合网格的有向体积；> 0 表示法线朝外，< 0 表示整体绕序反了。"""
    total = 0.0
    for i0, i1, i2, _c in faces:
        a, b, c = vertices[i0], vertices[i1], vertices[i2]
        total += (a[0] * (b[1] * c[2] - b[2] * c[1])
                  + a[1] * (b[2] * c[0] - b[0] * c[2])
                  + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return total / 6.0


def placement_offset(mesh):
    """把模型放到平台上方所需的平移量（mm）：让被引用顶点的最小坐标落在 (0,0,0)。

    切片软件按 STL / 3MF 里的绝对坐标摆放模型；负 Z 会让模型沉到平台下被切掉。
    只统计「被三角形实际引用」的有限顶点，避免坏顶点占位影响摆放。
    """
    used = set()
    for face in mesh.faces:
        used.update(face[:3])
    pts = [mesh.vertices[i] for i in used if _finite(mesh.vertices[i])]
    if not pts:
        return (0.0, 0.0, 0.0)
    return tuple(-min(float(p[i]) for p in pts) for i in range(3))


def placed_vertices(mesh, offset=None):
    """返回平移到正八分象限后的顶点列表（不改原 mesh；幂等）。"""
    dx, dy, dz = offset if offset is not None else placement_offset(mesh)
    if dx == 0.0 and dy == 0.0 and dz == 0.0:
        return list(mesh.vertices)
    return [(v[0] + dx, v[1] + dy, v[2] + dz) for v in mesh.vertices]


class Mesh:
    """从 GLB 抽出的、已变换到 Z-up 且已换算单位的三角网格。"""

    __slots__ = ("vertices", "faces", "colors", "scale", "unit_note",
                 "bbox", "skipped", "primitives", "axis_fixed", "name",
                 "normals_fixed")

    def __init__(self):
        self.vertices = []       # list[(x, y, z)]
        self.faces = []          # list[(i0, i1, i2, color_index)]
        self.colors = []         # list["#RRGGBB"]，去重后的颜色表
        self.scale = 1.0
        self.unit_note = ""
        self.bbox = (0.0, 0.0, 0.0)
        self.skipped = 0
        self.primitives = 0
        self.axis_fixed = True
        self.name = "model"
        self.normals_fixed = False

    @property
    def triangle_count(self):
        return len(self.faces)


def extract_mesh(doc, *, fix_up_axis=True, scale=None, name="model"):
    """把 GLB 展平成一个三角网格（含节点变换、坐标轴修正、单位换算）。"""
    mesh = Mesh()
    mesh.axis_fixed = bool(fix_up_axis)
    mesh.name = name
    axis = _YUP_TO_ZUP if fix_up_axis else _identity4()
    color_map = {}

    for prim, world in _iter_primitives(doc):
        mesh.primitives += 1
        mode = int(prim.get("mode", 4))
        if mode not in (4, 5, 6):
            continue
        attrs = prim.get("attributes") or {}
        pos_index = attrs.get("POSITION")
        if pos_index is None:
            continue
        try:
            positions = read_accessor(doc, pos_index)
        except PrintExportError:
            raise
        if not positions:
            continue

        idx_index = prim.get("indices")
        if idx_index is None:
            indices = list(range(len(positions)))
        else:
            indices = [int(row[0]) for row in read_accessor(doc, idx_index)]
        indices = [i for i in indices if 0 <= i < len(positions)]

        color = _material_color(doc, prim.get("material"))
        if color not in color_map:
            color_map[color] = len(mesh.colors)
            mesh.colors.append(color)
        cidx = color_map[color]

        transform = _mul4(axis, world)
        flip = _det3(transform) < 0
        base = len(mesh.vertices)
        bad_vertices = set()
        transformed = []
        for j, p in enumerate(positions):
            q = _apply4(transform, p[:3])
            if _finite(q):
                transformed.append(q)
            else:
                # 坏顶点不能写进 XML（"nan" 不是合法 3MF 数值）；占位并记下来，
                # 凡是引用它的三角形直接跳过。
                transformed.append((0.0, 0.0, 0.0))
                bad_vertices.add(base + j)
        mesh.vertices.extend(transformed)

        for a, b, c in _triangulate(indices, mode):
            if flip:
                b, c = c, b
            i0, i1, i2 = base + a, base + b, base + c
            if i0 in bad_vertices or i1 in bad_vertices or i2 in bad_vertices:
                mesh.skipped += 1
                continue
            va, vb, vc = (mesh.vertices[i] for i in (i0, i1, i2))
            if _normal(va, vb, vc) == (0.0, 0.0, 0.0):
                mesh.skipped += 1                  # 退化三角形，切片软件不接受
                continue
            mesh.faces.append((i0, i1, i2, cidx))

    if not mesh.vertices or not mesh.faces:
        raise PrintExportError("这个 GLB 里没有可导出的三角形（可能只有点/线，或网格为空）")

    lo = [min(v[i] for v in mesh.vertices if _finite(v)) for i in range(3)]
    hi = [max(v[i] for v in mesh.vertices if _finite(v)) for i in range(3)]
    extent = max(hi[i] - lo[i] for i in range(3))
    if scale is None:
        if extent < 10.0:
            factor, note = 1000.0, "包围盒最长边 %.3f < 10，按 glTF 米制 → 毫米（×1000）" % extent
        else:
            factor, note = 1.0, "包围盒最长边 %.3f ≥ 10，数值本身按毫米解释（1:1）" % extent
    else:
        try:
            factor = float(scale)
        except (TypeError, ValueError):
            raise PrintExportError("scale 必须是数值")
        if not math.isfinite(factor) or factor <= 0:
            raise PrintExportError("scale 必须是正的有限数值")
        note = "使用调用方指定的换算 ×%g" % factor
    mesh.scale = factor
    mesh.unit_note = note
    mesh.bbox = tuple((hi[i] - lo[i]) * factor for i in range(3))
    if factor != 1.0:
        mesh.vertices = [tuple(v * factor for v in p) for p in mesh.vertices]

    # 法线朝外：闭合网格的有向体积为负说明整体绕序反了（部分导出器会这样），
    # 翻转后切片软件才会把它当成实体，而不是把它当成「内部」。
    if _signed_volume(mesh.vertices, mesh.faces) < -1e-9:
        mesh.faces = [(a, c, b, ci) for a, b, c, ci in mesh.faces]
        mesh.normals_fixed = True
    return mesh


# ---- 输出 -----------------------------------------------------------------
_STL_HEADER = b"Sketchfab Downloader | GLB-to-print binary STL".ljust(80, b" ")[:80]


def write_stl(path, mesh):
    """写二进制 STL（每三角形 50 字节，小端 float32），顶点已放到平台上方。"""
    p = Path(path)
    verts = placed_vertices(mesh)
    with p.open("wb") as f:
        f.write(_STL_HEADER)
        f.write(struct.pack("<I", len(mesh.faces)))
        for i0, i1, i2, _c in mesh.faces:
            a, b, c = verts[i0], verts[i1], verts[i2]
            n = _normal(a, b, c)
            f.write(struct.pack(
                "<12fH",
                n[0], n[1], n[2],
                a[0], a[1], a[2],
                b[0], b[1], b[2],
                c[0], c[1], c[2],
                0,
            ))
    return p


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="xml" ContentType="application/xml"/>
</Types>
"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""

_MODEL_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _build_model_xml(mesh):
    verts = placed_vertices(mesh)
    # Core Spec：<object type="model"> 的 mesh 至少要 4 个三角形才能构成实体；
    # 单/双三角形不能谎称实体，降级为 other（切片软件仍能显示几何）。
    obj_type = "model" if mesh.triangle_count >= 4 else "other"
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<model unit="millimeter" xml:lang="en-US" xmlns="%s">' % _MODEL_NS,
             ' <metadata name="Application">Sketchfab Downloader</metadata>',
             ' <metadata name="Title">%s</metadata>' % _xml_escape(mesh.name),
             ' <resources>', '  <basematerials id="1">']
    for i, color in enumerate(mesh.colors):
        parts.append('   <base name="mat%d" displaycolor="%s"/>' % (i, color))
    # id 必须在整个 <resources> 内全局唯一：basematerials 占 1，object 占 2。
    # pid 指向 basematerials 组；pindex 是组内默认属性索引（规范：三角形带
    # p1 时，object 必须同时给出 pid 与 pindex）。
    parts += ['  </basematerials>',
              '  <object id="2" name="%s" type="%s" pid="1" pindex="0">'
              % (_xml_escape(mesh.name), obj_type),
              '   <mesh>', '    <vertices>']
    for v in verts:
        parts.append('     <vertex x="%.6f" y="%.6f" z="%.6f"/>' % v)
    parts += ['    </vertices>', '    <triangles>']
    for i0, i1, i2, cidx in mesh.faces:
        parts.append('     <triangle v1="%d" v2="%d" v3="%d" p1="%d"/>'
                     % (i0, i1, i2, cidx))
    parts += ['    </triangles>', '   </mesh>', '  </object>',
              ' </resources>', ' <build>', '  <item objectid="2"/>',
              ' </build>', '</model>', '']
    return "\n".join(parts)


def write_3mf(path, mesh):
    """写 OPC（ZIP）容器里的 3MF Core Spec 模型，含 basematerials 多色。"""
    p = Path(path)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES,
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("_rels/.rels", _RELS, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("3D/3dmodel.model", _build_model_xml(mesh),
                   compress_type=zipfile.ZIP_DEFLATED)
    return p


def convert(glb_path, stl_path=None, mf3_path=None, *,
            fix_up_axis=True, scale=None, want_stl=True, want_3mf=True, log=None):
    """
    高层入口：把一个 ``.glb`` 转成同目录下的 ``.stl`` 与 ``.3mf``。

    ``log`` 是可选的进度回调（只收一个字符串），GUI 用它把后台线程里的
    阶段消息送回主线程队列。返回统计字典，异常统一为 :class:`PrintExportError`。
    """
    def say(msg):
        if log:
            log(msg)

    if not (want_stl or want_3mf):
        raise PrintExportError("至少要选一种输出格式")
    src = Path(glb_path)
    if not src.is_file():
        raise PrintExportError("GLB 文件不存在：%s" % src)
    out_stl = Path(stl_path) if stl_path else src.with_suffix(".stl")
    out_3mf = Path(mf3_path) if mf3_path else src.with_suffix(".3mf")

    say("读取 GLB 容器（JSON chunk + BIN chunk）…")
    doc = load_glb(src)
    say("展开场景图、节点变换与三角形…")
    mesh = extract_mesh(doc, fix_up_axis=fix_up_axis, scale=scale, name=src.stem)

    off = placement_offset(mesh)
    if any(abs(v) > 1e-9 for v in off):
        say("摆放：把模型平移到平台上方（正八分象限），偏移 (%.2f, %.2f, %.2f) mm" % off)
    else:
        say("摆放：模型已在平台上方（正八分象限），无需平移")
    if mesh.normals_fixed:
        say("法线：有向体积为负，已整体翻转绕序，保证法线朝外")
    else:
        say("法线：方向朝外，无需翻转")
    if mesh.triangle_count < 4:
        say('⚠ 仅 %d 个三角形：3MF 按 type="other" 输出（不足 4 个三角形不能声明为实体），这类网格通常无法直接打印'
            % mesh.triangle_count)

    made = []
    if want_stl:
        say("写出二进制 STL…")
        write_stl(out_stl, mesh)
        made.append(out_stl)
    if want_3mf:
        say("写出 3MF（OPC 容器 + basematerials 多色）…")
        write_3mf(out_3mf, mesh)
        made.append(out_3mf)

    return {
        "glb": str(src),
        "outputs": [str(p) for p in made],
        "stl": str(out_stl) if want_stl else None,
        "3mf": str(out_3mf) if want_3mf else None,
        "vertices": len(mesh.vertices),
        "triangles": mesh.triangle_count,
        "skipped": mesh.skipped,
        "primitives": mesh.primitives,
        "colors": list(mesh.colors),
        "scale": mesh.scale,
        "unit_note": mesh.unit_note,
        "bbox_mm": [round(v, 3) for v in mesh.bbox],
        "axis_fixed": mesh.axis_fixed,
        "placement_mm": [round(v, 3) for v in off],
        "normals_fixed": mesh.normals_fixed,
        "object_type": "model" if mesh.triangle_count >= 4 else "other",
        "bytes": {str(p): p.stat().st_size for p in made},
    }
