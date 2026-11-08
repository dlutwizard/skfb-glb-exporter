# -*- coding: utf-8 -*-
"""
v1.2.5 GLB → STL / 3MF 转换的离线回归测试。

全部用例自己用 struct 拼最小 GLB，不需要网络、不需要真实模型、不需要发布包；
干净检出即可运行（SFD-006）。
"""
import ast
import json
import math
import struct
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"

import print_export as pe  # noqa: E402  （conftest 已把 app/ 放进 sys.path）


# ---------------------------------------------------------------- 工具
def _pack_glb(gltf, bin_data=b""):
    j = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    j += b" " * ((-len(j)) % 4)
    b = bytes(bin_data)
    b += b"\x00" * ((-len(b)) % 4)
    total = 12 + 8 + len(j) + 8 + len(b)
    return (struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<II", len(j), 0x4E4F534A) + j
            + struct.pack("<II", len(b), 0x004E4942) + b)


def _write(tmp_path, name, gltf, bin_data=b""):
    p = tmp_path / name
    p.write_bytes(_pack_glb(gltf, bin_data))
    return p


def _simple_gltf(positions, indices, *, mode=4, materials=None,
                 nodes=None, scenes=None, attributes=None,
                 buffer_views=None, accessors=None, buffers=None,
                 primitives=None):
    """默认：一个 VEC3/float POSITION + 一个 U16 SCALAR indices 的 mesh。"""
    pos_bytes = b"".join(struct.pack("<3f", *p) for p in positions)
    idx_bytes = b"".join(struct.pack("<H", i) for i in indices)
    gltf = {
        "asset": {"version": "2.0", "generator": "sfd-test"},
        "scene": 0,
        "scenes": scenes if scenes is not None else [{"nodes": [0]}],
        "nodes": nodes if nodes is not None else [{"mesh": 0}],
        "meshes": [{"primitives": primitives if primitives is not None else [{
            "attributes": attributes if attributes is not None else {"POSITION": 0},
            "indices": 1, "mode": mode,
            **({"material": 0} if materials else {}),
        }]}],
        "accessors": accessors if accessors is not None else [
            {"bufferView": 0, "componentType": 5126,
             "count": len(positions), "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123,
             "count": len(indices), "type": "SCALAR"},
        ],
        "bufferViews": buffer_views if buffer_views is not None else [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes)},
            {"buffer": 0, "byteOffset": len(pos_bytes),
             "byteLength": len(idx_bytes)},
        ],
        "buffers": buffers if buffers is not None else [
            {"byteLength": len(pos_bytes) + len(idx_bytes)}],
    }
    if materials:
        gltf["materials"] = materials
    return gltf, pos_bytes + idx_bytes


def _tri_glb(tmp_path, name="tri.glb", **kw):
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2], **kw)
    return _write(tmp_path, name, gltf, bin_data)


def _cube_glb(tmp_path, name="cube.glb", *, flip=False, materials=None):
    """边长 1、含负坐标、CCW 外向的封闭立方体；flip=True 时整体反向绕序。"""
    v = [(-.5, -.5, -.5), (.5, -.5, -.5), (.5, .5, -.5), (-.5, .5, -.5),
         (-.5, -.5, .5), (.5, -.5, .5), (.5, .5, .5), (-.5, .5, .5)]
    tris = [(0, 2, 1), (0, 3, 2),      # -Z
            (4, 5, 6), (4, 6, 7),      # +Z
            (0, 1, 5), (0, 5, 4),      # -Y
            (3, 7, 6), (3, 6, 2),      # +Y
            (0, 4, 7), (0, 7, 3),      # -X
            (1, 2, 6), (1, 6, 5)]      # +X
    if flip:
        tris = [(a, c, b) for a, b, c in tris]
    idx = [i for t in tris for i in t]
    pos = b"".join(struct.pack("<3f", *p) for p in v)
    ib = b"".join(struct.pack("<H", i) for i in idx)
    gltf = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0}, "indices": 1, "mode": 4,
            **({"material": 0} if materials else {}),
        }]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123,
             "count": len(idx), "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(ib)},
        ],
        "buffers": [{"byteLength": len(pos) + len(ib)}],
    }
    if materials:
        gltf["materials"] = materials
    return _write(tmp_path, name, gltf, pos + ib)


# ---------------------------------------------------------------- 依赖
def test_module_is_dependency_clean():
    """只允许标准库：不引入 numpy / trimesh / Pillow，也别 import 项目 GUI 模块。"""
    src = (APP / "print_export.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    allowed = {"base64", "json", "math", "struct", "zipfile",
               "pathlib", "urllib", "__future__"}
    assert mods <= allowed, "print_export 引入了非标准库依赖：%s" % (mods - allowed)
    assert "tkinter" not in mods and "numpy" not in mods


# ---------------------------------------------------------------- GLB 容器
@pytest.mark.parametrize("blob,why", [
    (b"", "空文件"),
    (b"not a glb at all....", "魔数错"),
    (struct.pack("<4sII", b"glTF", 3, 20), "版本不是 2"),
    (struct.pack("<4sII", b"glTF", 2, 9999), "声明长度大于实际（截断）"),
])
def test_load_glb_rejects_bad_containers(tmp_path, blob, why):
    p = tmp_path / ("bad_%s.glb" % abs(hash(why)))
    p.write_bytes(blob)
    with pytest.raises(pe.PrintExportError):
        pe.load_glb(p)


def test_load_glb_rejects_missing_json_chunk(tmp_path):
    p = tmp_path / "nojson.glb"
    b = b"\x00" * 8
    blob = (struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(b))
            + struct.pack("<II", len(b), 0x004E4942) + b)
    p.write_bytes(blob)
    with pytest.raises(pe.PrintExportError, match="JSON chunk"):
        pe.load_glb(p)


def test_load_glb_reads_embedded_bin_and_data_uri(tmp_path):
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    doc = pe.load_glb(_write(tmp_path, "embed.glb", gltf, bin_data))
    assert doc.gltf["asset"]["version"] == "2.0"
    assert doc.buffer(0)[:12] == struct.pack("<3f", 0, 0, 0)

    # data URI 形式的 buffer（有些导出器会这么写）
    import base64
    g2 = json.loads(json.dumps(gltf))
    g2["buffers"] = [{"byteLength": len(bin_data),
                      "uri": "data:application/octet-stream;base64,"
                             + base64.b64encode(bin_data).decode("ascii")}]
    doc2 = pe.load_glb(_write(tmp_path, "datauri.glb", g2, b""))
    assert doc2.buffer(0) == bin_data


def test_load_glb_reads_external_bin(tmp_path):
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
    (tmp_path / "mesh.bin").write_bytes(bin_data)
    gltf["buffers"] = [{"byteLength": len(bin_data), "uri": "mesh.bin"}]
    doc = pe.load_glb(_write(tmp_path, "external.glb", gltf, b""))
    assert doc.buffer(0) == bin_data


# ---------------------------------------------------------------- accessor
def test_accessor_byte_stride_and_normalized(tmp_path):
    # 交错顶点：每个顶点 3 个 float + 1 个 padding float，byteStride = 16
    interleaved = b""
    pts = [(0, 0, 0), (2, 0, 0), (0, 2, 0)]
    for p in pts:
        interleaved += struct.pack("<4f", p[0], p[1], p[2], 0.5)
    indices = b"".join(struct.pack("<B", i) for i in (0, 1, 2))
    bin_data = interleaved + indices
    gltf = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5121, "count": 3,
             "type": "SCALAR", "normalized": True},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(interleaved),
             "byteStride": 16},
            {"buffer": 0, "byteOffset": len(interleaved),
             "byteLength": len(indices)},
        ],
        "buffers": [{"byteLength": len(bin_data)}],
    }
    doc = pe.load_glb(_write(tmp_path, "stride.glb", gltf, bin_data))
    pos = pe.read_accessor(doc, 0)
    assert pos == [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 2.0, 0.0]]
    norm = pe.read_accessor(doc, 1)
    assert norm == [[0.0], [1.0 / 255.0], [2.0 / 255.0]]


def test_accessor_sparse_override(tmp_path):
    # POSITION 用 bufferView 给 3 个零顶点，sparse 把第 1 个覆盖成 (5,0,0)
    base = struct.pack("<9f", *([0.0] * 9))
    sparse_idx = struct.pack("<3H", 1, 0, 0)[:2]      # 一个 U16 索引
    sparse_val = struct.pack("<3f", 5, 0, 0)
    bin_data = base + sparse_idx + sparse_val
    gltf = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "sparse": {"count": 1,
                        "indices": {"bufferView": 1, "componentType": 5123,
                                    "byteOffset": 0},
                        "values": {"bufferView": 2, "byteOffset": 0}}},
            {"bufferView": 3, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(base)},
            {"buffer": 0, "byteOffset": len(base), "byteLength": 2},
            {"buffer": 0, "byteOffset": len(base) + 2,
             "byteLength": len(sparse_val)},
            {"buffer": 0, "byteOffset": len(base) + 2 + len(sparse_val),
             "byteLength": 6},
        ],
        "buffers": [{"byteLength": len(bin_data) + 6}],
    }
    bin_data += struct.pack("<3H", 0, 1, 2)
    doc = pe.load_glb(_write(tmp_path, "sparse.glb", gltf, bin_data))
    pos = pe.read_accessor(doc, 0)
    assert pos[1] == [5.0, 0.0, 0.0]
    assert pos[0] == [0.0, 0.0, 0.0]


# ---------------------------------------------------------------- 场景图 / 坐标
def test_node_hierarchy_and_axis_fix(tmp_path):
    positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    gltf, bin_data = _simple_gltf(
        positions, [0, 1, 2],
        nodes=[{"translation": [10, 0, 0], "children": [1]},
               {"mesh": 0, "scale": [2, 2, 2]}],
        scenes=[{"nodes": [0]}])
    doc = pe.load_glb(_write(tmp_path, "nodes.glb", gltf, bin_data))

    m = pe.extract_mesh(doc, scale=1.0)
    # 子节点 ×2 → 父节点 +10 → Y-up 转 Z-up (x, y, z) → (x, -z, y)
    assert m.vertices[0] == pytest.approx((10, 0, 0))
    assert m.vertices[1] == pytest.approx((12, 0, 0))
    assert m.vertices[2] == pytest.approx((10, 0, 2))
    assert m.axis_fixed is True

    m2 = pe.extract_mesh(doc, fix_up_axis=False, scale=1.0)
    assert m2.vertices[2] == pytest.approx((10, 2, 0))
    assert m2.axis_fixed is False


def test_quaternion_rotation_applied(tmp_path):
    # 绕 Z 轴 +90°：q = (0, 0, sin45, cos45)，(1,0,0) → (0,1,0)
    q = math.sqrt(0.5)
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2],
        nodes=[{"mesh": 0, "rotation": [0, 0, q, q]}])
    doc = pe.load_glb(_write(tmp_path, "quat.glb", gltf, bin_data))
    m = pe.extract_mesh(doc, scale=1.0)
    assert m.vertices[1] == pytest.approx((0, 0, 1), abs=1e-6)
    assert m.vertices[2] == pytest.approx((-1, 0, 0), abs=1e-6)


def test_mirror_node_flips_winding(tmp_path):
    plain = _tri_glb(tmp_path, "plain.glb")
    mirrored = _tri_glb(tmp_path, "mirrored.glb",
                        nodes=[{"mesh": 0, "scale": [-1, 1, 1]}])
    a = pe.extract_mesh(pe.load_glb(plain), scale=1.0)
    b = pe.extract_mesh(pe.load_glb(mirrored), scale=1.0)
    na = pe._normal(*(a.vertices[i] for i in a.faces[0][:3]))
    nb = pe._normal(*(b.vertices[i] for i in b.faces[0][:3]))
    # 镜像后几何翻到 -X，但翻转绕序让法线仍朝 -Y（不朝里）
    assert nb == pytest.approx(na, abs=1e-6)
    assert b.vertices[1][0] == pytest.approx(-1.0)


@pytest.mark.parametrize("mode,expected_faces,second", [
    (5, 2, (2, 1, 3)),      # TRIANGLE_STRIP：第二个三角形按规范翻转
    (6, 2, (0, 2, 3)),      # TRIANGLE_FAN
])
def test_triangle_strip_and_fan(tmp_path, mode, expected_faces, second):
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [0, 1, 2, 3], mode=mode)
    doc = pe.load_glb(_write(tmp_path, "strip%d.glb" % mode, gltf, bin_data))
    m = pe.extract_mesh(doc, scale=1.0)
    assert len(m.faces) == expected_faces
    assert m.faces[1][:3] == second


def test_points_lines_and_degenerate_are_skipped(tmp_path):
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0)], [0, 1, 2],
        primitives=[
            {"attributes": {"POSITION": 0}, "mode": 0},     # POINTS
            {"attributes": {"POSITION": 0}, "mode": 1},     # LINES
            {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4},
        ])
    # POSITION 里第 4 个顶点与第 1 个重合；再塞一个退化三角形
    bin2 = bin_data + struct.pack("<3H", 0, 0, 3)
    gltf["accessors"][1]["count"] = 6
    gltf["bufferViews"][1]["byteLength"] = 12
    gltf["meshes"][0]["primitives"][2]["indices"] = 1
    doc = pe.load_glb(_write(tmp_path, "skip.glb", gltf, bin2))
    m = pe.extract_mesh(doc, scale=1.0)
    assert m.primitives == 3
    assert len(m.faces) == 1
    assert m.skipped == 1


def test_glb_without_triangles_raises(tmp_path):
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0)], [0, 1], mode=1)
    with pytest.raises(pe.PrintExportError, match="没有可导出的三角形"):
        pe.extract_mesh(pe.load_glb(_write(tmp_path, "lines.glb", gltf, bin_data)))


# ---------------------------------------------------------------- 颜色
def test_material_colors_linear_to_srgb_basematerials(tmp_path):
    mats = [
        {"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}},
        {"pbrMetallicRoughness": {"baseColorFactor": [0.5, 0, 0, 1]}},
        {"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
    ]
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0),
         (10, 0, 0), (11, 0, 0), (10, 1, 0),
         (20, 0, 0), (21, 0, 0), (20, 1, 0)],
        [0, 1, 2, 3, 4, 5, 6, 7, 8], materials=mats,
        primitives=[
            {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4, "material": 0},
            {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4, "material": 1},
            {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4, "material": 2},
        ])
    doc = pe.load_glb(_write(tmp_path, "colors.glb", gltf, bin_data))
    m = pe.extract_mesh(doc, scale=1.0)
    assert m.colors == ["#FF0000", "#BC0000", "#FFFFFF"]
    used = {f[3] for f in m.faces}
    assert used == {0, 1, 2}


# ---------------------------------------------------------------- STL
def test_write_stl_binary_layout(tmp_path):
    path = _tri_glb(tmp_path)
    res = pe.convert(path, want_3mf=False, scale=1.0)
    raw = Path(res["stl"]).read_bytes()
    assert len(raw) == 84 + 50 * res["triangles"]
    assert raw.startswith(b"Sketchfab Downloader")
    assert struct.unpack_from("<I", raw, 80)[0] == 1
    vals = struct.unpack_from("<12fH", raw, 84)
    assert vals[12] == 0                      # attribute byte count
    assert vals[:3] == pytest.approx((0.0, -1.0, 0.0), abs=1e-6)
    assert vals[3:6] == pytest.approx((0.0, 0.0, 0.0))
    assert vals[6:9] == pytest.approx((1.0, 0.0, 0.0))
    assert vals[9:12] == pytest.approx((0.0, 0.0, 1.0))


# ---------------------------------------------------------------- 3MF
def test_write_3mf_opc_structure_and_multicolor(tmp_path):
    path = _tri_glb(tmp_path, materials=[
        {"pbrMetallicRoughness": {"baseColorFactor": [0, 0, 1, 1]}}])
    res = pe.convert(path, want_stl=False, scale=1.0)
    with zipfile.ZipFile(res["3mf"]) as z:
        names = z.namelist()
        assert names == ["[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"]
        ct = z.read("[Content_Types].xml").decode("utf-8")
        rels = z.read("_rels/.rels").decode("utf-8")
        model = z.read("3D/3dmodel.model").decode("utf-8")
        assert z.getinfo("[Content_Types].xml").compress_type == zipfile.ZIP_STORED
    assert 'Extension="model"' in ct
    assert "3dmanufacturing-3dmodel+xml" in ct
    assert "3dmodel" in rels and "rel0" in rels
    assert 'unit="millimeter"' in model
    assert 'displaycolor="#0000FF"' in model
    # v1.2.6：resources 内 id 全局唯一、object 必须带 pindex、
    # 单三角形不谎称 type="model"（Core Spec 要求 >= 4 个三角形）
    assert '<basematerials id="1">' in model
    assert '<object id="2"' in model
    assert 'pindex="0"' in model
    assert 'type="other"' in model
    assert '<item objectid="2"/>' in model
    assert '<triangle v1="0" v2="1" v3="2" p1="0"/>' in model

    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    root = ET.fromstring(model)
    assert root.tag == "{%s}model" % ns["m"]
    verts = root.findall(".//m:vertex", ns)
    tris = root.findall(".//m:triangle", ns)
    bases = root.findall(".//m:base", ns)
    assert len(verts) == 3 and len(tris) == 1 and len(bases) == 1
    assert verts[2].get("z") == "1.000000"
    assert verts[2].get("y") == "0.000000"


def test_basematerials_only_p1_no_gradient(tmp_path):
    path = _tri_glb(tmp_path)
    res = pe.convert(path, want_stl=False, scale=1.0)
    with zipfile.ZipFile(res["3mf"]) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8")
    # basematerials 不支持顶点渐变：三角形只写 p1，不写 p2/p3
    tri = [ln for ln in model.splitlines() if "<triangle " in ln][0]
    assert "p1=" in tri and "p2=" not in tri and "p3=" not in tri


# ---------------------------------------------------------------- 端到端
def test_convert_end_to_end_outputs_and_stats(tmp_path):
    path = _tri_glb(tmp_path, materials=[
        {"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}])
    seen = []
    res = pe.convert(path, log=seen.append)
    assert any("GLB 容器" in s for s in seen)
    assert any("STL" in s for s in seen)
    assert any("3MF" in s for s in seen)
    assert res["triangles"] == 1
    assert res["vertices"] == 3
    assert res["colors"] == ["#FF0000"]
    assert res["axis_fixed"] is True
    assert res["scale"] == 1000.0
    assert res["bbox_mm"] == [1000.0, 0.0, 1000.0]
    assert Path(res["stl"]).name == "tri.stl"
    assert Path(res["3mf"]).name == "tri.3mf"
    assert all(Path(p).is_file() for p in res["outputs"])
    assert all(res["bytes"][p] > 0 for p in res["outputs"])


def test_auto_scale_metres_vs_millimetres(tmp_path):
    small = _tri_glb(tmp_path, "small.glb")
    big = _write(tmp_path, "big.glb", *_simple_gltf(
        [(0, 0, 0), (100, 0, 0), (0, 100, 0)], [0, 1, 2]))
    assert pe.convert(small, want_3mf=False)["scale"] == 1000.0
    assert pe.convert(big, want_3mf=False)["scale"] == 1.0
    assert "1:1" in pe.convert(big, want_3mf=False)["unit_note"]
    assert pe.convert(small, want_3mf=False, scale=2.5)["scale"] == 2.5
    with pytest.raises(pe.PrintExportError, match="scale"):
        pe.convert(small, want_3mf=False, scale=0)


def test_convert_requires_at_least_one_format(tmp_path):
    path = _tri_glb(tmp_path)
    with pytest.raises(pe.PrintExportError, match="至少"):
        pe.convert(path, want_stl=False, want_3mf=False)


def test_bad_vertex_does_not_leak_nan_into_3mf(tmp_path):
    """坐标非法的顶点必须被跳过，XML 里不能出现 nan。"""
    gltf, bin_data = _simple_gltf(
        [(0, 0, 0), (1, 0, 0), (0, 1, 0), (float("nan"), 0, 0)],
        [0, 1, 2, 3])
    gltf["accessors"][0]["count"] = 4
    gltf["bufferViews"][0]["byteLength"] = 48
    gltf["meshes"][0]["primitives"][0]["indices"] = 1
    gltf["accessors"][1]["count"] = 6
    gltf["bufferViews"][1]["byteLength"] = 12
    bin_data += struct.pack("<3H", 0, 1, 3)
    path = _write(tmp_path, "badvertex.glb", gltf, bin_data)
    res = pe.convert(path, want_stl=False, scale=1.0)
    with zipfile.ZipFile(res["3mf"]) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8")
    assert "nan" not in model.lower() and "inf" not in model.lower()
    assert res["skipped"] == 1


# ---------------------------------------------------------------- 接线守卫
def test_gui_wires_convert_button_and_queue():
    src = (APP / "sketchfab_gui.py").read_text(encoding="utf-8")
    assert "import print_export" in src
    assert "self._converting = False" in src
    assert "self.print_btn = ttk.Button(" in src
    assert "GLB → STL / 3MF…" in src
    # v1.2.7：入口升级为独立的醒目分区，不再用 Quiet 弱样式
    assert 'style="Convert.TButton"' in src
    assert "3D 打印导出" in src
    assert "convert_bar = tk.Frame(" in src
    # 文案单一真源：初始态与 print_done 恢复态都引用常量，
    # 防止点过一次转换之后按钮文案悄悄退回弱简写。
    assert 'CONVERT_BTN_TEXT = "◆ 3D 打印：GLB → STL / 3MF…"' in src
    assert src.count("text=CONVERT_BTN_TEXT") == 2
    assert 'text="GLB → STL / 3MF…"' not in src, "仍有硬编码的旧恢复文案"
    assert "def convert_clicked(self):" in src
    assert "def _convert_worker(self, glb_path):" in src
    assert "sfd-print-export" in src
    assert '("print_log",' in src and '("print_done",' in src
    assert 'elif kind == "print_log":' in src
    assert 'elif kind == "print_done":' in src
    assert "print_export.convert(glb_path" in src
    # v1.2.6：转换完成日志要说清「直接可用」的摆放 / 法线与颜色限制
    assert "直接可用：已摆正 Z-up" in src
    assert "displaycolor 只用于显示" in src
    # 只加转换按钮：下载流程入口保持不变
    assert "def start(self):" in src
    assert "def download_task(" in src
    assert "def run_batch(" in src


def test_both_specs_include_print_export():
    for name in ("Sketchfab下载器.spec", "single_file.spec"):
        src = (ROOT / "build" / name).read_text(encoding="utf-8")
        assert '"print_export"' in src, "%s 缺少 hiddenimports" % name


# ------------------------------------------------- v1.2.6「直接可用」加固
def _model_root(model_xml):
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    root = ET.fromstring(model_xml)
    ids = [el.get("id") for el in root.iter() if el.get("id")]
    assert ids and len(ids) == len(set(ids)), "resources 内 id 不唯一：%s" % ids
    return root, ns


def test_3mf_resource_ids_unique_pindex_and_type(tmp_path):
    path = _cube_glb(tmp_path, materials=[
        {"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}}])
    res = pe.convert(path, want_stl=False)
    with zipfile.ZipFile(res["3mf"]) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8")
    root, _ns = _model_root(model)
    assert 'type="model"' in model               # 12 个三角形 → 合法实体
    assert 'pindex="0"' in model
    assert 'pid="1"' in model
    assert '<item objectid="2"/>' in model
    assert res["object_type"] == "model"
    assert [el.get("id") for el in root.iter() if el.get("id")] == ["1", "2"]


def test_3mf_metadata_only_spec_wellknown_names(tmp_path):
    # 规范表 3-1 的 well-known 名称；Application 是允许的，自定义名必须带命名空间
    allowed = {"Title", "Designer", "Description", "Copyright",
               "LicenseTerms", "Rating", "CreationDate",
               "ModificationDate", "Application"}
    res = pe.convert(_cube_glb(tmp_path), want_stl=False)
    with zipfile.ZipFile(res["3mf"]) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8")
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    root = ET.fromstring(model)
    names = [m.get("name") for m in root.findall(".//m:metadata", ns)]
    assert names and set(names) <= allowed
    assert len(names) == len(set(names))


def test_stl_and_3mf_share_platform_placement(tmp_path):
    """负坐标模型必须被平移到正八分象限，且两种格式几何完全一致。"""
    res = pe.convert(_cube_glb(tmp_path))
    assert res["placement_mm"] == [500.0, 500.0, 500.0]

    raw = Path(res["stl"]).read_bytes()
    count = struct.unpack_from("<I", raw, 80)[0]
    stl_pts = []
    for k in range(count):
        vals = struct.unpack_from("<12fH", raw, 84 + 50 * k)
        stl_pts += [tuple(vals[3:6]), tuple(vals[6:9]), tuple(vals[9:12])]
    assert min(min(p) for p in stl_pts) >= -1e-4
    assert min(p[2] for p in stl_pts) == pytest.approx(0.0, abs=1e-4)

    with zipfile.ZipFile(res["3mf"]) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8")
    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    root = ET.fromstring(model)
    mf_pts = [(float(v.get("x")), float(v.get("y")), float(v.get("z")))
              for v in root.findall(".//m:vertex", ns)]
    assert min(min(p) for p in mf_pts) >= -1e-9
    assert min(p[2] for p in mf_pts) == pytest.approx(0.0, abs=1e-9)
    assert sorted(set(stl_pts)) == sorted(set(mf_pts))


def test_3mf_object_type_degrades_below_four_triangles(tmp_path):
    res = pe.convert(_tri_glb(tmp_path), want_stl=False)
    with zipfile.ZipFile(res["3mf"]) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8")
    assert 'type="other"' in model
    assert 'type="model"' not in model
    assert res["object_type"] == "other"


def test_normals_flipped_when_signed_volume_negative(tmp_path):
    good = _cube_glb(tmp_path, "good.glb")
    bad = _cube_glb(tmp_path, "bad.glb", flip=True)
    a = pe.extract_mesh(pe.load_glb(good), scale=1.0)
    b = pe.extract_mesh(pe.load_glb(bad), scale=1.0)
    assert a.normals_fixed is False
    assert b.normals_fixed is True
    assert b.faces == a.faces
    na = pe._normal(*(a.vertices[i] for i in a.faces[0][:3]))
    nb = pe._normal(*(b.vertices[i] for i in b.faces[0][:3]))
    assert nb == pytest.approx(na, abs=1e-6)
    assert pe._signed_volume(b.vertices, b.faces) > 0


def test_placement_offset_ignores_unreferenced_bad_vertex(tmp_path):
    gltf, bin_data = _simple_gltf(
        [(10, 10, 10), (11, 10, 10), (10, 11, 10), (-100, -100, -100)],
        [0, 1, 2])
    m = pe.extract_mesh(pe.load_glb(
        _write(tmp_path, "off.glb", gltf, bin_data)), scale=1.0)
    # Y-up → Z-up 后 (10,10,10) → (10,-10,10)；未被引用的 -100 顶点不能影响摆放
    assert pe.placement_offset(m) == pytest.approx((-10.0, 10.0, -10.0))


def test_convert_reports_placement_and_normals(tmp_path):
    seen = []
    res = pe.convert(_cube_glb(tmp_path, "flip.glb", flip=True),
                     log=seen.append)
    assert res["normals_fixed"] is True
    assert res["object_type"] == "model"
    assert res["placement_mm"] == [500.0, 500.0, 500.0]
    assert any("平移到平台上方" in s for s in seen)
    assert any("翻转绕序" in s for s in seen)
    assert any("法线" in s for s in seen)
