# -*- coding: utf-8 -*-
"""
v1.3.0：3D 预览回归测试。

* ``preview3d`` 是纯标准库层，干净环境（无 tkinter / 无发布包）也必须能跑；
* 真实 Tk 控件用例用 importorskip 降级为 skip，而不是 error；
* GUI / spec 接线用静态守卫，防止后续换皮或重构把页签悄悄摘掉。
"""
import io
import json
import math
import struct
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

import preview3d as p3  # noqa: E402  (conftest 已把 app/ 放进 sys.path)

# ---------------------------------------------------------------- 样本构造
TETRA_V = [(0, 0, 0), (20, 0, 0), (0, 15, 0), (0, 0, 10)]
TETRA_F = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]

CUBE_V = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0),
          (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
CUBE_F = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
          (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
          (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]


def make_bin_stl(path, verts=None, faces=None):
    verts = verts or TETRA_V
    faces = faces or TETRA_F
    buf = bytearray(b"proto".ljust(80, b" "))
    buf += struct.pack("<I", len(faces))
    for f in faces:
        buf += struct.pack("<3f", 0, 0, 1)
        for i in f:
            buf += struct.pack("<3f", *verts[i])
        buf += struct.pack("<H", 0)
    Path(path).write_bytes(bytes(buf))
    return path


def make_ascii_stl(path):
    lines = ["solid proto"]
    for f in TETRA_F:
        lines += ["  facet normal 0 0 1", "    outer loop"]
        lines += ["      vertex %g %g %g" % TETRA_V[i] for i in f]
        lines += ["    endloop", "  endfacet"]
    lines.append("endsolid proto")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


def make_glb(path):
    pos = b"".join(struct.pack("<3f", *p) for p in TETRA_V)
    idx = struct.pack("<12H", *[i for f in TETRA_F for i in f])
    g = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4, "material": 0},
        ]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.91, 0.42, 0.12, 1]}}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 12, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx)},
        ],
        "buffers": [{"byteLength": len(pos) + len(idx)}],
    }
    j = json.dumps(g, separators=(",", ":")).encode()
    j += b" " * ((-len(j)) % 4)
    b = pos + idx
    b += b"\x00" * ((-len(b)) % 4)
    total = 12 + 8 + len(j) + 8 + len(b)
    Path(path).write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(j), 0x4E4F534A) + j
        + struct.pack("<II", len(b), 0x004E4942) + b)
    return path


def _3mf_package(model_xml):
    ct = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" '
        'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        '</Types>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("3D/3dmodel.model", model_xml)
    return buf.getvalue()


def _3mf_model(triangles, *, transform=None, build=True, components=None,
               basematerials=True, metadata=True):
    vs = "".join('<vertex x="%g" y="%g" z="%g"/>' % v for v in CUBE_V)
    ts = "".join('<triangle v1="%d" v2="%d" v3="%d"%s/>'
                 % (a, b, c, ' p1="1"' if i % 2 else "")
                 for i, (a, b, c) in enumerate(triangles))
    mats = ('<basematerials id="1"><base name="amber" displaycolor="#E8A23B"/>'
            '<base name="blue" displaycolor="#3366CC"/></basematerials>'
            if basematerials else "")
    pid = ' pid="1" pindex="0"' if basematerials else ""
    meta = '<metadata name="Title">Proto Cube</metadata>' if metadata else ""
    comps = ("<components>%s</components>" % components) if components else ""
    item = ('<item objectid="2" transform="%s"/>' % transform) if transform else '<item objectid="2"/>'
    build_xml = "<build>%s</build>" % item if build else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model unit="millimeter" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '%s<resources>%s'
        '<object id="2" type="model"%s><mesh>'
        '<vertices>%s</vertices><triangles>%s</triangles>'
        '</mesh></object>%s</resources>%s</model>'
        % (meta, mats, pid, vs, ts, comps, build_xml))


def make_3mf(path, **kw):
    Path(path).write_bytes(_3mf_package(_3mf_model(CUBE_F, **kw)))
    return path


# ---------------------------------------------------------------- 解析层
def test_binary_and_ascii_stl_match(tmp_path):
    a = p3.load_mesh(make_bin_stl(tmp_path / "bin.stl"))
    b = p3.load_mesh(make_ascii_stl(tmp_path / "ascii.stl"))
    assert (a.fmt, b.fmt) == ("STL", "STL")
    assert a.triangle_count == b.triangle_count == 4
    assert a.bounds_min == b.bounds_min == (0.0, 0.0, 0.0)
    assert a.bounds_max == b.bounds_max == (20.0, 15.0, 10.0)
    assert a.size_mm == b.size_mm == (20.0, 15.0, 10.0)
    assert a.volume_mm3 == pytest.approx(500.0)
    assert set(a.face_colors) == {p3.DEFAULT_COLOR}
    assert a.center == (10.0, 7.5, 5.0)
    assert len(a.notes) >= 2 and "STL" in " ".join(a.notes)


def test_glb_uses_print_export_pipeline(tmp_path):
    m = p3.load_mesh(make_glb(tmp_path / "cube.glb"))
    assert m.fmt == "GLB"
    assert m.triangle_count == 4
    assert len(set(m.face_colors)) == 1
    assert m.face_colors[0] != p3.DEFAULT_COLOR, "GLB 材质常量色没读出来"
    assert "Z-up" in " ".join(m.notes)
    assert m.size_mm == (20.0, 10.0, 15.0) or m.size_mm == (20.0, 15.0, 10.0)


def test_3mf_colors_metadata_and_item_transform(tmp_path):
    m = p3.load_mesh(make_3mf(tmp_path / "cube.3mf",
                              transform="1 0 0 0 1 0 0 0 1 10 20 30"))
    assert m.fmt == "3MF"
    assert m.triangle_count == 12
    assert m.original_faces == 12
    assert m.bounds_min == (10.0, 20.0, 30.0), "build/item transform 没生效"
    assert m.size_mm == (10.0, 10.0, 10.0)
    assert m.volume_mm3 == pytest.approx(1000.0)
    assert (232, 162, 59) in m.face_colors
    assert (51, 102, 204) in m.face_colors
    assert len(set(m.face_colors)) == 2
    assert "Title=Proto Cube" in " ".join(m.notes)


def test_3mf_colorgroup_is_supported(tmp_path):
    model = _3mf_model(CUBE_F).replace(
        '<basematerials id="1"><base name="amber" displaycolor="#E8A23B"/>'
        '<base name="blue" displaycolor="#3366CC"/></basematerials>',
        '<colorgroup id="1"><color color="#E8A23B"/><color color="#3366CC"/></colorgroup>')
    p = tmp_path / "colorgroup.3mf"
    p.write_bytes(_3mf_package(model))
    m = p3.load_mesh(p)
    assert (232, 162, 59) in m.face_colors and (51, 102, 204) in m.face_colors


def test_3mf_without_build_shows_every_object(tmp_path):
    m = p3.load_mesh(make_3mf(tmp_path / "nobuild.3mf", build=False))
    assert m.triangle_count == 12, "没有 <build> 时应把所有 object 都摆出来"


def test_3mf_components_are_instantiated(tmp_path):
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model unit="millimeter" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources>'
        '<object id="2" type="model"><mesh><vertices>%s</vertices>'
        '<triangles>%s</triangles></mesh></object>'
        '<object id="1"><components>'
        '<component objectid="2" transform="1 0 0 0 1 0 0 0 1 30 0 0"/>'
        '</components></object>'
        '</resources><build><item objectid="1"/></build></model>'
        % ("".join('<vertex x="%g" y="%g" z="%g"/>' % v for v in CUBE_V),
           "".join('<triangle v1="%d" v2="%d" v3="%d"/>' % f for f in CUBE_F)))
    p = tmp_path / "components.3mf"
    p.write_bytes(_3mf_package(model))
    m = p3.load_mesh(p)
    assert m.triangle_count == 12
    assert min(v[0] for v in m.vertices) == pytest.approx(30.0)


def test_stl_vertex_dedup_and_bad_coordinates(tmp_path):
    text = ("solid x\n facet normal 0 0 1\n  outer loop\n"
            "   vertex 0 0 0\n   vertex 1 0 0\n   vertex 0 1 0\n"
            "  endloop\n endfacet\n"
            " facet normal 0 0 1\n  outer loop\n"
            "   vertex 0 0 0\n   vertex 1 0 0\n   vertex nan nan nan\n"
            "  endloop\n endfacet\nendsolid x\n")
    p = tmp_path / "bad.stl"
    p.write_text(text, encoding="utf-8")
    m = p3.load_mesh(p)
    assert m.triangle_count == 1, "含非法坐标的三角形应被跳过"
    assert len(m.vertices) == 3, "共享顶点应去重"


def test_large_stl_is_simplified_but_bounds_unchanged(tmp_path):
    p = tmp_path / "big.stl"
    n = 30000
    buf = bytearray(b"big".ljust(80, b" "))
    buf += struct.pack("<I", n)
    for k in range(n):
        buf += struct.pack("<3f", 0, 0, 1)
        for j in range(3):
            buf += struct.pack("<3f", k % 97, j, (k * 7) % 53)
        buf += struct.pack("<H", 0)
    p.write_bytes(bytes(buf))
    m = p3.load_mesh(p)
    assert m.original_faces == n
    assert m.triangle_count <= p3.MAX_RENDER_FACES
    assert m.simplified is True
    assert any("抽样" in note for note in m.notes)
    # 抽样的子集不能改变包围盒 / 中心（Fit 用得到）
    sub = m.subset(100)
    assert sub.bounds_min == m.bounds_min and sub.center == m.center


# ---------------------------------------------------------------- 错误路径
def test_load_mesh_rejects_missing_and_unsupported(tmp_path):
    with pytest.raises(p3.Preview3DError, match="找不到文件"):
        p3.load_mesh(tmp_path / "nope.glb")
    p = tmp_path / "mesh.obj"
    p.write_bytes(b"o mesh\n")
    with pytest.raises(p3.Preview3DError, match="不支持的格式"):
        p3.load_mesh(p)


def test_load_mesh_rejects_empty_and_broken_files(tmp_path):
    p = tmp_path / "empty.stl"
    p.write_bytes(b"solid empty\nendsolid empty\n")
    with pytest.raises(p3.Preview3DError):
        p3.load_mesh(p)
    p = tmp_path / "broken.3mf"
    p.write_bytes(b"not a zip")
    with pytest.raises(p3.Preview3DError, match="不是有效的 3MF"):
        p3.load_mesh(p)
    p = tmp_path / "nomodel.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/readme.txt", "hi")
    with pytest.raises(p3.Preview3DError, match="找不到 .model"):
        p3.load_mesh(p)
    p = tmp_path / "noobject.3mf"
    p.write_bytes(_3mf_package(
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources/><build/></model>'))
    with pytest.raises(p3.Preview3DError, match="没有 <object>"):
        p3.load_mesh(p)


def test_file_size_guard(monkeypatch, tmp_path):
    p = make_bin_stl(tmp_path / "guarded.stl")
    monkeypatch.setattr(p3, "MAX_FILE_BYTES", 16)
    with pytest.raises(p3.Preview3DError, match="上限"):
        p3.load_mesh(p)


# ---------------------------------------------------------------- 渲染数学
def test_projection_is_finite_even_at_extreme_zoom(tmp_path):
    m = p3.load_mesh(make_bin_stl(tmp_path / "proj.stl"))
    cpts = p3.camera_points(m.vertices, p3.Camera(), m.center)
    for zoom in (1e-9, 0.01, 1.0, 25.0):
        cam = p3.Camera(zoom=zoom)
        pts = p3.project(cpts, cam, m, 640, 480)
        assert all(math.isfinite(v) and abs(v) < 1e9 for p in pts for v in p)
    # 缩放应当真的改变投影尺寸
    near = p3.project(cpts, p3.Camera(zoom=25.0), m, 640, 480)
    far = p3.project(cpts, p3.Camera(zoom=0.1), m, 640, 480)
    assert max(abs(a[0] - b[0]) for a, b in zip(near, far)) > 1.0


def test_projection_depth_modulation_is_mild():
    """前后两点的缩放差必须很小，否则立方体会被透视拉成「折开的纸片」。"""
    mesh = p3.PreviewMesh(
        vertices=[(0, 0, -10), (0, 0, 10)], faces=[], face_colors=[],
        fmt="STL", name="depth", original_faces=0,
        bounds_min=(0, 0, -10), bounds_max=(0, 0, 10))
    pts = p3.project([(10, 0, -10), (10, 0, 10)], p3.Camera(), mesh, 640, 480)
    k1 = (pts[0][0] - 320) / 10.0
    k2 = (pts[1][0] - 320) / 10.0
    assert 0 < k1 < k2, "近处的面应当略大"
    assert (k2 - k1) / k1 < 0.15, "透视太强，模型形状会被拉变形"


def test_sort_faces_is_far_to_near_and_cull_works(tmp_path):
    m = p3.load_mesh(make_bin_stl(tmp_path / "sort.stl"))
    cpts = p3.camera_points(m.vertices, p3.Camera(), m.center)
    ordered = p3.sort_faces(cpts, m.faces)
    assert len(ordered) == len(m.faces)
    assert all(ordered[i][0] <= ordered[i + 1][0] for i in range(len(ordered) - 1))
    assert all(isinstance(t[2], tuple) and len(t[2]) == 3 for t in ordered)
    culled = p3.sort_faces(cpts, m.faces, cull=True)
    assert len(culled) < len(ordered)
    assert all(t[2][2] > 0 for t in culled), "背面剔除只保留朝向相机的面"


def test_wireframe_edges_are_deduplicated():
    assert len(p3.wireframe_edges(TETRA_F)) == 6
    assert len(p3.wireframe_edges(TETRA_F + [TETRA_F[0]])) == 6


def test_shade_and_hex_color_are_clamped():
    lit = p3.shade((0, 0, 1), (200, 200, 200))
    assert all(0 <= v <= 200 for v in lit)
    assert lit != (200, 200, 200), "Lambert 明暗没有生效"
    assert p3.shade((0, 0, 0), (10, 20, 30)) == (10, 20, 30)
    assert p3.hex_color((0, 255, 16)) == "#00ff10"
    assert p3.hex_color((-5, 300, 1)) == "#00ff01"


def test_fit_camera_resets_view_but_keeps_display_flags():
    cam = p3.Camera(yaw=9, pitch=1.2, zoom=8, pan_x=123, pan_y=-45,
                    wireframe=True, grid=False, axes=False, spin=True)
    got = p3.fit_camera(cam)
    assert got is cam
    assert (got.yaw, got.pitch, got.zoom) == (0.65, -0.45, 1.0)
    assert (got.pan_x, got.pan_y) == (0.0, 0.0)
    assert (got.wireframe, got.grid, got.axes, got.spin) == (True, False, False, True)


# ---------------------------------------------------------------- 接线守卫
def test_gui_wires_preview_tab_and_queue():
    src = (ROOT / "app" / "sketchfab_gui.py").read_text(encoding="utf-8")
    assert 'enumerate(("下载", "模型搜索", "3D 预览"))' in src
    assert "tab_preview = ttk.Frame(" in src
    assert 'self.nb.add(tab_preview, text="  3D 预览  ")' in src
    assert "def _build_preview_tab(" in src
    assert "def _init_preview_state(" in src
    assert "preview_view.PreviewView(main)" in src
    assert "def _preview_worker(" in src
    assert "threading.Thread(target=self._preview_worker" in src
    assert '("preview_ready", mesh)' in src
    assert 'kind == "preview_ready"' in src
    assert 'kind == "preview_error"' in src
    assert "PREVIEW_FILETYPES" in src
    # v1.2.7 的教训：按钮文案必须单一真源，恢复态不许硬编码
    assert src.count("text=PREVIEW_OPEN_BTN_TEXT") == 3
    assert src.count("text=PREVIEW_OPEN_BTN_BUSY") == 1
    assert 'text="打开文件…"' not in src, "恢复态硬编码了按钮文案"
    # 三个页签，别把搜索页挤掉
    assert 'self.nb.add(tab_dl, text="  下载  ")' in src
    assert 'self.nb.add(tab_search, text="  模型搜索  ")' in src


def test_specs_include_preview_hiddenimports():
    for name in ("Sketchfab下载器.spec", "single_file.spec"):
        src = (ROOT / "build" / name).read_text(encoding="utf-8")
        assert '"preview3d"' in src, "%s 漏了 preview3d" % name
        assert '"preview_view"' in src, "%s 漏了 preview_view" % name


def test_parsing_layer_has_no_gui_dependency():
    """用 AST 查真实 import，别被文档字符串里「不 import tkinter」的说明误伤。"""
    import ast
    tree = ast.parse((ROOT / "app" / "preview3d.py").read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    assert not any(m == "tkinter" or m.startswith("tkinter.") for m in modules)


def test_docs_and_version_are_bumped_to_130():
    ver = (ROOT / "app" / "version.py").read_text(encoding="utf-8")
    assert '__version__ = "1.3.0"' in ver
    assert "VERSION_TUPLE = (1, 3, 0)" in ver
    clog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [1.3.0]" in clog and "3D 预览" in clog
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "### 3D 预览（v1.3.0）" in readme
    assert "preview3d.py" in readme and "preview_view.py" in readme
    tpl = (ROOT / "app" / "使用说明.template.txt").read_text(encoding="utf-8")
    assert "三个页签" in tpl and "二之三、3D 文件预览" in tpl


def test_preview_view_has_neutral_message_state():
    src = (ROOT / "app" / "preview_view.py").read_text(encoding="utf-8")
    assert "def show_message(" in src
    assert "self.message_text" in src
    assert "def set_mesh(" in src and "def show_error(" in src


# ---------------------------------------------------------------- 真实 Tk
def test_preview_view_renders_on_real_tk(tmp_path):
    pytest.importorskip("tkinter")
    import tkinter as tk
    import preview_view

    root = tk.Tk()
    root.withdraw()
    try:
        view = preview_view.PreviewView(root)
        view.pack(fill="both", expand=True)
        view.canvas.configure(width=640, height=480)
        root.update()
        if view.canvas.winfo_width() < 40:   # withdraw 的窗口可能不做几何计算
            root.geometry("700x560")
            root.deiconify()
            root.update()
        assert view.canvas.winfo_width() >= 40, "Canvas 没拿到可用尺寸"
        assert view.canvas.find_all(), "空状态立方体没画出来"

        m = p3.load_mesh(make_bin_stl(tmp_path / "tk.stl"))
        view.set_mesh(m)
        root.update()
        polys = [i for i in view.canvas.find_all()
                 if view.canvas.type(i) == "polygon"]
        assert len(polys) == m.triangle_count, "实体渲染的三角形数不对"

        view.set_wireframe(True)
        root.update()
        assert view.canvas.find_all()
        view.set_wireframe(False)
        view.set_grid(False)
        view.set_axes(False)
        view.set_backface_cull(True)
        view.fit()
        root.update()

        view.show_message("正在解析 demo.glb…")
        root.update()
        assert view.canvas.find_all()
        view.show_error("打不开这个文件")
        root.update()
        assert view.canvas.find_all()

        view.set_spin(False)
    finally:
        try:
            root.destroy()
        except Exception:
            pass
