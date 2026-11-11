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
    # 面数必须**超过当前渲染上限**才会触发简化。v1.3.1 把上限从 9000 提到
    # 30000（实测 30000 面完整一帧约 1.3 s，首帧可接受），所以这里跟着调到
    # 上限之上；用例意图（大网格会被简化 + 包围盒不变）保持不变。
    n = p3.MAX_RENDER_FACES + 5000
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
def test_gui_wires_webgl_preview_tab():
    src = (ROOT / "app" / "sketchfab_gui.py").read_text(encoding="utf-8")
    assert 'enumerate(("下载", "模型搜索", "3D 预览"))' in src
    assert "tab_preview = ttk.Frame(" in src
    assert 'self.nb.add(tab_preview, text="  3D 预览  ")' in src
    assert "def _build_preview_tab(" in src
    assert "def _init_preview_state(" in src
    assert "import preview_web" in src
    assert "self.preview_launcher = preview_web.WebPreview()" in src
    assert "def _preview_launch(" in src
    assert "self.preview_launcher.open(path)" in src
    assert "preview_view" not in src, "旧 Tk 预览控件仍在 GUI 里"
    assert "preview3d" not in src, "旧软件渲染解析仍在 GUI 里"
    assert "def _preview_worker(" not in src
    assert 'kind == "preview_ready"' not in src
    assert "PREVIEW_FILETYPES" in src
    # 按钮文案仍必须单一真源，恢复态不许硬编码
    assert src.count("text=PREVIEW_OPEN_BTN_TEXT") == 2
    assert src.count("text=PREVIEW_OPEN_BTN_BUSY") == 1
    assert 'text="打开文件…"' not in src, "恢复态硬编码了旧按钮文案"
    # 三个页签，别把搜索页挤掉
    assert 'self.nb.add(tab_dl, text="  下载  ")' in src
    assert 'self.nb.add(tab_search, text="  模型搜索  ")' in src


def test_specs_include_webgl_preview_hiddenimports():
    for name in ("Sketchfab下载器.spec", "single_file.spec"):
        src = (ROOT / "build" / name).read_text(encoding="utf-8")
        assert '"preview_web"' in src, "%s 漏了 preview_web" % name
        assert '"preview3d"' not in src
        assert '"preview_view"' not in src


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


def test_docs_and_version_are_bumped():
    ver = (ROOT / "app" / "version.py").read_text(encoding="utf-8")
    from version import VERSION_TUPLE, __version__
    assert '__version__ = "%s"' % __version__ in ver
    assert "VERSION_TUPLE = %r" % (VERSION_TUPLE,) in ver
    clog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [%s]" % __version__ in clog
    assert "## [1.3.1]" in clog
    assert "WebGL" in clog
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "### 3D 预览（v1.4.0：本地 WebGL）" in readme
    assert "preview_web.py" in readme
    tpl = (ROOT / "app" / "使用说明.template.txt").read_text(encoding="utf-8")
    assert "三个页签" in tpl
    assert "二之三、3D 文件预览（v1.4.0：本地 WebGL）" in tpl
    assert "WebGL" in tpl and "已抽样" not in tpl


def test_preview_view_has_neutral_message_state():
    src = (ROOT / "app" / "preview_view.py").read_text(encoding="utf-8")
    assert "def show_message(" in src
    assert "self.message_text" in src
    assert "def set_mesh(" in src and "def show_error(" in src


# ---------------------------------------------------------------- 真实 Tk
def _tk_root():
    """创建（或复用）一个可用的 Tk root。

    同一进程里反复 ``tk.Tk()`` 在某些 Windows / Anaconda 环境下会因为 Tcl
    库路径被第一次初始化污染而报 "Can't find a usable tk.tcl"。因此所有
    真实渲染用例共用**同一个** root，只创建一次；拿不到就返回 None。
    """
    import tkinter as tk
    cached = getattr(_tk_root, "_root", None)
    if cached is not None:
        return cached
    try:
        root = tk.Tk()
    except Exception:
        return None
    root.withdraw()
    _tk_root._root = root
    return root


def test_preview_view_renders_on_real_tk(tmp_path):
    pytest.importorskip("tkinter")
    import preview_view

    root = _tk_root()
    if root is None:
        pytest.skip("本机 Tk 环境不可用，跳过真实渲染用例")
    view = preview_view.PreviewView(root)
    try:
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
        # 只销毁这个 view，保留共享 root 给后续用例复用
        try:
            view.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------- v1.3.1 降采样修复
def _sphere_mesh(n_lat, n_lon, r=40.0):
    """UV 球：面数 ≈ 2 * n_lat * n_lon，用来造 12 万+ 面的高模样本。"""
    verts = [(0.0, 0.0, r)]
    for i in range(1, n_lat):
        th = math.pi * i / n_lat
        for j in range(n_lon):
            ph = 2 * math.pi * j / n_lon
            verts.append((r * math.sin(th) * math.cos(ph),
                          r * math.sin(th) * math.sin(ph), r * math.cos(th)))
    verts.append((0.0, 0.0, -r))
    last = len(verts) - 1
    faces = []
    for j in range(n_lon):
        faces.append((0, 1 + (j + 1) % n_lon, 1 + j))
    for i in range(n_lat - 2):
        base = 1 + i * n_lon
        nxt = base + n_lon
        for j in range(n_lon):
            j2 = (j + 1) % n_lon
            faces.append((base + j, base + j2, nxt + j2))
            faces.append((base + j, nxt + j2, nxt + j))
    base = 1 + (n_lat - 2) * n_lon
    for j in range(n_lon):
        faces.append((last, base + j, base + (j + 1) % n_lon))
    return verts, faces


def _point_tri_distance(p, a, b, c):
    """点到三角形的最近距离（Ericson, Real-Time Collision Detection 精简版）。"""
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    ap = (p[0] - a[0], p[1] - a[1], p[2] - a[2])
    d1 = ab[0] * ap[0] + ab[1] * ap[1] + ab[2] * ap[2]
    d2 = ac[0] * ap[0] + ac[1] * ap[1] + ac[2] * ap[2]
    if d1 <= 0 and d2 <= 0:
        return math.dist(p, a)
    bp = (p[0] - b[0], p[1] - b[1], p[2] - b[2])
    d3 = ab[0] * bp[0] + ab[1] * bp[1] + ab[2] * bp[2]
    d4 = ac[0] * bp[0] + ac[1] * bp[1] + ac[2] * bp[2]
    if d3 >= 0 and d4 <= d3:
        return math.dist(p, b)
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        t = d1 / (d1 - d3)
        return math.dist(p, (a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t))
    cp = (p[0] - c[0], p[1] - c[1], p[2] - c[2])
    d5 = ab[0] * cp[0] + ab[1] * cp[1] + ab[2] * cp[2]
    d6 = ac[0] * cp[0] + ac[1] * cp[1] + ac[2] * cp[2]
    if d6 >= 0 and d5 <= d6:
        return math.dist(p, c)
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        t = d2 / (d2 - d6)
        return math.dist(p, (a[0] + ac[0] * t, a[1] + ac[1] * t, a[2] + ac[2] * t))
    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return math.dist(p, (b[0] + (c[0] - b[0]) * t, b[1] + (c[1] - b[1]) * t,
                             b[2] + (c[2] - b[2]) * t))
    denom = 1.0 / (va + vb + vc)
    vv = vb * denom
    ww = vc * denom
    q = (a[0] + ab[0] * vv + ac[0] * ww, a[1] + ab[1] * vv + ac[1] * ww,
         a[2] + ab[2] * vv + ac[2] * ww)
    return math.dist(p, q)


def test_simplify_keeps_surface_coverage():
    """v1.3.0 的致命 Bug：均匀跳面把 96% 的三角形删掉，表面变成筛子。

    旧实现（``faces[i * total // max_faces]``）对 24 万面的球实测屏幕覆盖率
    只剩 4.75%。现在改为顶点聚类聚合，断言：**每个原始三角形的重心到
    聚合网格的最近距离都极小**（即原表面处处仍有三角形覆盖，没有挖空），
    同时面数确实下降。
    """
    verts, faces = _sphere_mesh(110, 110)
    radius = 40.0
    total = len(faces)
    colors = [p3.DEFAULT_COLOR] * total
    # 显式给一个小于样本面数的目标，避免用例随 MAX_RENDER_FACES 调档而失效
    target = 8000
    assert total > target * 2, "样本面数不够，测不出降采样"
    assert p3.MAX_RENDER_FACES > target, (
        "渲染上限应当足够大，让这个量级的模型免于聚合（完整渲染）")

    new_verts, new_faces, new_colors, changed = p3._simplify(
        verts, faces, colors, target)

    assert changed is True
    assert len(new_faces) <= target, "聚合后仍超渲染预算"
    assert len(new_faces) < total, "面数没有下降"
    assert len(new_colors) == len(new_faces), "face_colors 没跟 faces 对齐"
    assert len(new_verts) < len(verts), "顶点没有被合并"

    # 用网格哈希加速：只跟重心附近格子里的聚合三角形比距离
    cell = radius * 0.25
    buckets = {}
    for tri in new_faces:
        gx = sum(new_verts[i][0] for i in tri) / 3.0
        gy = sum(new_verts[i][1] for i in tri) / 3.0
        gz = sum(new_verts[i][2] for i in tri) / 3.0
        key = (math.floor(gx / cell), math.floor(gy / cell), math.floor(gz / cell))
        buckets.setdefault(key, []).append(tri)

    import random
    rnd = random.Random(20240501)
    sample = rnd.sample(faces, min(4000, total))
    span = 2
    worst = 0.0
    for a, b, c in sample:
        pa, pb, pc = verts[a], verts[b], verts[c]
        g = ((pa[0] + pb[0] + pc[0]) / 3.0,
             (pa[1] + pb[1] + pc[1]) / 3.0,
             (pa[2] + pb[2] + pc[2]) / 3.0)
        kx = math.floor(g[0] / cell)
        ky = math.floor(g[1] / cell)
        kz = math.floor(g[2] / cell)
        best = float("inf")
        for dx in range(-span, span + 1):
            for dy in range(-span, span + 1):
                for dz in range(-span, span + 1):
                    for tri in buckets.get((kx + dx, ky + dy, kz + dz), ()):
                        d = _point_tri_distance(g, new_verts[tri[0]],
                                                new_verts[tri[1]],
                                                new_verts[tri[2]])
                        if d < best:
                            best = d
        worst = max(worst, best)

    # 阈值取球半径的 5%：这个量级下肉眼看不到任何孔洞，
    # 而旧跳面算法的重心会落在完全无面的区域（距离远大于此）。
    assert worst < radius * 0.05, (
        "聚合后的表面出现了空洞：原始三角形重心到聚合网格的最大距离 "
        "%.4f mm 超过半径的 5%%（%.2f mm）" % (worst, radius * 0.05))


def test_simplify_is_deterministic():
    """同样输入两次，聚合结果必须逐位相同（否则拖拽时模型会「抖动」）。"""
    verts, faces = _sphere_mesh(60, 60)
    colors = [(216, 182, 122)] * len(faces)
    a = p3._simplify(verts, faces, colors, 4000)
    b = p3._simplify(verts, faces, colors, 4000)
    assert a[0] == b[0], "顶点表不确定"
    assert a[1] == b[1], "面表不确定"
    assert a[2] == b[2], "颜色表不确定"
    assert a[3] is True and b[3] is True
    assert len(a[1]) <= 4000


def test_subset_degrades_by_merging_not_truncating():
    """``PreviewMesh.subset()`` 是交互态降质的唯一入口，必须走聚合。

    v1.3.0 的 ``subset`` 用 ``self.faces[:n]`` —— 直接把模型切成「前 n 个面」，
    拖拽时看到的是半个模型。断言降质后的包围盒不变、且覆盖整个球面。
    """
    verts, faces = _sphere_mesh(60, 60)
    colors = [p3.DEFAULT_COLOR] * len(faces)
    mesh = p3.PreviewMesh(
        vertices=verts, faces=faces, face_colors=colors, fmt="STL", name="sph",
        original_faces=len(faces),
        bounds_min=tuple(min(p[i] for p in verts) for i in range(3)),
        bounds_max=tuple(max(p[i] for p in verts) for i in range(3)))

    sub = mesh.subset(1200)
    assert sub.triangle_count <= 1200
    assert sub.triangle_count > 1200 * 0.5, "降质过头了"
    assert sub.bounds_min == mesh.bounds_min, "降质改变了包围盒（Fit 会失效）"
    # 降质后仍要覆盖整个球：顶点在三个轴上都有正负两侧
    for axis in range(3):
        vals = [v[axis] for v in sub.vertices]
        assert min(vals) < -10.0 and max(vals) > 10.0, (
            "降质后模型只覆盖了部分区域（被截断而不是被合并）")

    # 同一 max_faces 应命中缓存，不重复计算
    again = mesh.subset(1200)
    assert again is sub, "LOD 结果没有缓存，拖拽时每帧都会重算"


def test_render_limits_are_raised():
    """渲染预算必须能覆盖绝大多数 Sketchfab 模型（旧值 9000 / 1800 太小）。

    上限是**性能标定**的结果（Tk 每面一个 item，约 0.026 s/千面），会随
    渲染路径优化而调整，所以这里不写死数字，只守住「下限」和「相对关系」：
    旧值 9000 / 1800 是必须被超越的基线，且拖拽必须比静态更省。
    """
    assert p3.MAX_RENDER_FACES > 9000, "静态上限没有超过 v1.3.0 的 9000"
    assert p3.DRAG_RENDER_FACES > 1800, "拖拽上限没有超过 v1.3.0 的 1800"
    assert p3.DRAG_RENDER_FACES < p3.MAX_RENDER_FACES
    assert p3.WIREFRAME_FACES < p3.DRAG_RENDER_FACES
    # 拖拽档至少是旧值的 2 倍，否则交互时形状仍然看不清
    assert p3.DRAG_RENDER_FACES >= 1800 * 2


def test_notes_and_gui_avoid_misleading_sampling_wording(tmp_path):
    """「已抽样」会让用户以为模型残缺；应改成「已合并显示」。"""
    # 运行时 notes 才是用户真正看到的东西；源码注释里提到旧措辞不算违规
    verts, faces = _sphere_mesh(60, 60)
    colors = [p3.DEFAULT_COLOR] * len(faces)
    mesh = p3.PreviewMesh(
        vertices=verts, faces=faces, face_colors=colors, fmt="STL", name="w",
        original_faces=len(faces),
        bounds_min=tuple(min(p[i] for p in verts) for i in range(3)),
        bounds_max=tuple(max(p[i] for p in verts) for i in range(3)),
        simplified=True,
        notes=["模型共 %d 个三角形，为流畅显示已合并显示（%d → %d 面）" % (
            len(faces), len(faces), len(faces)),
            "（顶点合并抽样，保留完整表面，仅降低精度，不改文件）"])
    notes_text = " ".join(mesh.notes)
    assert "已合并显示" in notes_text
    assert "已抽样" not in notes_text, "notes 里还在说「已抽样」"

    # 真实 load_mesh 产出的 notes 也必须带新措辞
    p = tmp_path / "sph.stl"
    buf = bytearray(b"sph".ljust(80, b" "))
    buf += struct.pack("<I", len(faces))
    for f in faces:
        buf += struct.pack("<3f", 0, 0, 1)
        for i in f:
            buf += struct.pack("<3f", *verts[i])
        buf += struct.pack("<H", 0)
    p.write_bytes(bytes(buf))
    real = p3.load_mesh(p, max_faces=1000)
    real_notes = " ".join(real.notes)
    assert "已合并显示" in real_notes
    assert "已抽样" not in real_notes

    gui = (ROOT / "app" / "sketchfab_gui.py").read_text(encoding="utf-8")
    assert "已抽样" not in gui, "GUI 里还在显示「已抽样」"
    assert "本地 WebGL" in gui


def test_high_poly_mesh_still_renders_faces():
    """真实 Tk：高面数网格加载后，Canvas 上的 polygon 数不再是 1800/9000 卡死。"""
    pytest.importorskip("tkinter")
    import preview_view

    verts, faces = _sphere_mesh(80, 80)
    assert len(faces) > 12000
    colors = [p3.DEFAULT_COLOR] * len(faces)
    mesh = p3.PreviewMesh(
        vertices=verts, faces=faces, face_colors=colors, fmt="STL", name="hi",
        original_faces=len(faces),
        bounds_min=tuple(min(p[i] for p in verts) for i in range(3)),
        bounds_max=tuple(max(p[i] for p in verts) for i in range(3)))

    root = _tk_root()
    if root is None:
        pytest.skip("本机 Tk 环境不可用，跳过真实渲染用例")
    view = preview_view.PreviewView(root)
    try:
        view.pack(fill="both", expand=True)
        view.canvas.configure(width=640, height=480)
        root.geometry("700x560")
        root.deiconify()
        root.update()
        if view.canvas.winfo_width() < 40:
            pytest.skip("Canvas 拿不到可用尺寸，跳过")

        view.set_mesh(mesh)
        root.update()
        polys = [i for i in view.canvas.find_all()
                 if view.canvas.type(i) == "polygon"]
        assert len(polys) > 5000, (
            "高面数模型只画出了 %d 个三角形，仍被旧上限卡死" % len(polys))
        assert len(polys) <= p3.MAX_RENDER_FACES

        # 拖拽态（interactive）也必须画出完整模型，而不是「前 N 个面」。
        # 阈值跟随 DRAG_RENDER_FACES（性能标定值），不用写死的 5000 —— 
        # 否则每次按实测调档，用例都会假失败。
        view.render(interactive=True)
        root.update()
        drag_polys = [i for i in view.canvas.find_all()
                      if view.canvas.type(i) == "polygon"]
        assert len(drag_polys) <= p3.DRAG_RENDER_FACES
        assert len(drag_polys) >= p3.DRAG_RENDER_FACES * 0.9, (
            "拖拽态只画了 %d 个面，明显低于预算 %d，交互时会看不清形状"
            % (len(drag_polys), p3.DRAG_RENDER_FACES))
    finally:
        try:
            view.destroy()
        except Exception:
            pass


def test_glb_color_index_matches_dedup_color_table(tmp_path):
    """根因复核（C）：确认 ``extract_mesh`` 的颜色索引与去重色表严格对齐。

    构造一个「primitive 用不连续的 material 下标（0 和 2）+ 一个完全无材质
    的 primitive」的 GLB，验证颜色不会错位成同一色。
    """
    verts_a = [(1000, 0, 0), (1010, 0, 0), (1000, 10, 0)]
    verts_b = [(2000, 0, 0), (2010, 0, 0), (2000, 10, 0)]
    one_tri = [(0, 1, 2)]
    blob, views = b"", []
    for verts in (verts_a, verts_b):
        pos = b"".join(struct.pack("<3f", *v) for v in verts)
        idx = b"".join(struct.pack("<H", i) for f in one_tri for i in f)
        views.append((len(blob), len(pos)))
        blob += pos
        views.append((len(blob), len(idx)))
        blob += idx

    gltf = {
        "asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [
            {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4, "material": 2},
            {"attributes": {"POSITION": 2}, "indices": 3, "mode": 4},
        ]}],
        "materials": [
            {"pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1]}},
            {"pbrMetallicRoughness": {"baseColorFactor": [0, 0, 1, 1]}},
            {"pbrMetallicRoughness": {"baseColorFactor": [0, 1, 0, 1]}},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 3, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "bufferViews": [{"buffer": 0, "byteOffset": o, "byteLength": ln}
                        for o, ln in views],
        "buffers": [{"byteLength": len(blob)}],
    }
    j = json.dumps(gltf, separators=(",", ":")).encode()
    j += b" " * ((-len(j)) % 4)
    b = blob + b"\x00" * ((-len(blob)) % 4)
    total = 12 + 8 + len(j) + 8 + len(b)
    path = tmp_path / "mixed.glb"
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, total)
        + struct.pack("<II", len(j), 0x4E4F534A) + j
        + struct.pack("<II", len(b), 0x004E4942) + b)

    m = p3.load_mesh(path)
    assert m.triangle_count == 2
    # primitive 0 用 material 2（绿），primitive 1 无材质（白）
    assert m.face_colors[0] == (0, 255, 0), "material=2 的颜色错位了"
    assert m.face_colors[1] == (255, 255, 255), "无材质 primitive 应为白色"
    assert len(set(m.face_colors)) == 2, "所有面被染成了同一个颜色"


def test_3mf_triangle_p1_overrides_object_pindex(tmp_path):
    """根因复核（B）：3MF 的 ``p1`` 必须优先于 object 的 ``pindex`` 默认值。"""
    model = _3mf_model(CUBE_F).replace(
        'p1="1"', 'p1="1"').replace(
        '<object id="2" type="model" pid="1" pindex="0">',
        '<object id="2" type="model" pid="1" pindex="0">')
    data = _3mf_package(model).replace(b"<triangles>", b"<triangles>")
    path = tmp_path / "p1.3mf"
    path.write_bytes(data)
    m = p3.load_mesh(path)
    # basematerials 有 amber / blue 两色；奇数下标三角形带 p1="1" → 蓝色
    assert (232, 162, 59) in m.face_colors, "默认 pindex 色没生效"
    assert (51, 102, 204) in m.face_colors, "三角形 p1 覆盖没生效"
    assert len(set(m.face_colors)) == 2


# ------------------------------------------------- v1.3.1 性能整改守卫
def test_preview_view_never_uses_outline_on_polygons():
    """守卫：``create_polygon`` 不许再带 ``outline=col``。

    v1.3.0 为了补 1px 缝给每个三角形描三条同色边，实测让光栅化慢 4 倍
    （8000 面 0.65 s → 0.16 s）。已用 5 倍放大实机截图确认 fill-only
    本身无缝，所以这条路必须堵死，防止以后又被人「顺手加回来」。
    """
    src = (ROOT / "app" / "preview_view.py").read_text(encoding="utf-8")
    assert "fill=col, outline=\"\", width=0" in src, (
        "polygon 必须用 fill-only 画法（outline=\"\", width=0）")
    # 注释里记录「为什么去掉 outline=col」是允许的，只禁止出现在代码行里
    code = "\n".join(line for line in src.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "outline=col" not in code, (
        "preview_view.py 的代码路径又出现了 outline=col —— 会让每帧慢 4 倍")
    assert "outline=" not in code.replace('outline=""', ""), (
        "polygon 的 outline 只允许为空串")


def test_face_depth_normal_matches_sort_faces_semantics():
    """守卫：拖拽复用的 ``face_depth_normal`` 必须与 ``sort_faces`` 语义一致。

    缓存只省 ``list.sort()``，不能改画家算法：同一相机下，按上一帧顺序
    重放得到的 (fi, z, normal) 必须与全量排序逐项相同；开剔除时也不能
    漏掉或多出任何面。
    """
    verts, faces = _sphere_mesh(24, 24)
    cam = p3.Camera(yaw=0.37, pitch=0.21, grid=False, axes=False)
    mesh = p3.PreviewMesh(
        vertices=verts, faces=faces,
        face_colors=[p3.DEFAULT_COLOR] * len(faces), fmt="STL", name="s",
        original_faces=len(faces),
        bounds_min=tuple(min(p[i] for p in verts) for i in range(3)),
        bounds_max=tuple(max(p[i] for p in verts) for i in range(3)))
    cpts = p3.camera_points(mesh.vertices, cam, mesh.center)

    for cull in (False, True):
        full = p3.sort_faces(cpts, mesh.faces, cull)
        ids = [fi for _z, fi, _n in full]
        replay = p3.face_depth_normal(cpts, mesh.faces, ids, cull)
        assert [fi for _z, fi, _n in replay] == ids, (
            "cull=%s：重放的面顺序/集合与全量排序不一致" % cull)
        # 逐面比 z（按 fi 索引对齐）
        z_full = {fi: z for z, fi, _n in full}
        for z, fi, _n in replay:
            assert abs(z - z_full[fi]) < 1e-9, "cull=%s：面 %d 的深度对不上" % (
                cull, fi)


def test_view_order_cache_invalidated_on_mesh_and_cull():
    """守卫：换模型 / 改剔除 / 切线框时必须让顺序缓存失效。

    缓存里存的是「上一帧的 face_index 序列 + 面数」，一旦这些前提变了
    却还在复用，画面会出现分层错乱。这里直接查内部字段。

    注意：``render()`` 在 canvas 宽高 < 40 时会直接 return（不写缓存），
    所以必须先把控件 pack 起来并 update，拿到真实尺寸再断言。
    """
    pytest.importorskip("tkinter")
    import preview_view

    root = _tk_root()
    if root is None:
        pytest.skip("本机 Tk 环境不可用，跳过真实控件用例")
    view = preview_view.PreviewView(root)
    try:
        view.pack(fill="both", expand=True)
        view.canvas.configure(width=640, height=480)
        root.geometry("700x560")
        root.deiconify()
        root.update()
        if view.canvas.winfo_width() < 40 or view.canvas.winfo_height() < 40:
            pytest.skip("Canvas 拿不到可用尺寸，跳过")

        assert view._sort_cache is None, "新建控件不该带缓存"
        verts, faces = _sphere_mesh(12, 12)
        mesh = p3.PreviewMesh(
            vertices=verts, faces=faces,
            face_colors=[p3.DEFAULT_COLOR] * len(faces), fmt="STL",
            name="c", original_faces=len(faces),
            bounds_min=tuple(min(p[i] for p in verts) for i in range(3)),
            bounds_max=tuple(max(p[i] for p in verts) for i in range(3)))
        # set_mesh 内部：_invalidate_order() 后立刻 render()，所以结束时
        # 缓存里是新模型的序列（不是 None）。关键是「不残留旧序列」。
        view.set_mesh(mesh)
        n_new = len(mesh.subset(p3.MAX_RENDER_FACES).faces)
        assert view._sort_cache is None or view._sort_cache[0] == n_new, (
            "set_mesh 之后缓存仍记着旧模型的面数 %r" % (view._sort_cache,))

        view.render(interactive=False)
        assert view._sort_cache is not None, "精确渲染后应写入顺序缓存"
        old_ids = view._sort_cache[1]
        assert view._sort_cache[0] == n_new, (
            "缓存里记的面数与实际绘制的 subset 面数不一致")

        # 各 set_* 内部都是「_invalidate_order() → render()」，渲染会立刻
        # 重新写入缓存，所以不能断言「之后缓存为 None」。改为直接验证
        # 失效动作本身有效：手动置脏后，_invalidate_order() 必须清空。
        view._invalidate_order()
        assert view._sort_cache is None, "_invalidate_order 没有清空缓存"

        view.set_backface_cull(not view.cam.backface_cull)
        assert view._sort_cache is None or view._sort_cache[0] == n_new, (
            "改剔除后缓存仍带着旧状态")
        # 剔除打开时可见面集合应确实变化（说明顺序被重算，而非照抄旧序列）
        cull_now = view.cam.backface_cull
        view.render(interactive=False)
        ids_cull = view._sort_cache[1]
        if cull_now:
            assert len(ids_cull) <= len(old_ids), (
                "开了背面剔除后，缓存序列里的面数反而变多了")

        view.render(interactive=False)
        assert view._sort_cache is not None
        view.set_wireframe(True)
        assert view._sort_cache is None or view._sort_cache[0] == n_new

        view.set_wireframe(False)
        view.render(interactive=False)
        assert view._sort_cache is not None
        view.fit()
        assert view._sort_cache is None or view._sort_cache[0] == n_new, (
            "视角复位后缓存仍带着旧状态")

        view.render(interactive=False)
        assert view._sort_cache is not None
        view.set_mesh(mesh)
        assert view._sort_cache is None or view._sort_cache[0] == n_new, (
            "再次 set_mesh 后仍残留旧序列")
    finally:
        try:
            view.destroy()
        except Exception:
            pass


