# -*- coding: utf-8 -*-
"""
高级 3D 打印重建 worker（独立进程，不依赖 tkinter / 应用代码）。

输入任意 trimesh 可读的模型（.glb/.gltf/.stl/.obj/.ply/.3mf/.off），
输出可直接切片的、水密的 .stl 与 .3mf。

算法（v1.5.0）：
  1. trimesh 展开场景图、烘焙节点变换，glTF 修正为 Z-up
  2. 按目标最长边等比缩放到毫米
  3. 依据表面积与面数预算自动选择体素间距（pitch）
  4. 表面点云体素化 -> 闭运算 -> 填洞 -> 去除碎屑连通域
  5. 高斯模糊占据场后 Marching Cubes（等值面天然闭合，边界边 0）
  6. 合并顶点、去退化面、校正法线，逐连通域校验水密
  7. 导出二进制 STL 与 3MF，并写出 advanced_report.json

依赖：numpy、scipy、scikit-image、trimesh（由外部 Python 提供）。
stdout 协议：每行一个 JSON 对象。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path


def _emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _log(message, progress=None):
    item = {"stage": "log", "message": str(message)}
    if progress is not None:
        item["progress"] = round(float(progress), 4)
    _emit(item)


def _fail(message):
    _emit({"stage": "error", "message": str(message)})
    return 2


SUPPORTED = {".glb", ".gltf", ".stl", ".obj", ".ply", ".3mf", ".off"}
Y_UP_FORMATS = {".glb", ".gltf"}
QUALITY_FACES = {"standard": 600000, "fine": 1200000, "ultra": 2000000}
CELL_BUDGET = 26_000_000
PITCH_FLOOR = 0.35


def _import_deps():
    errors = []
    mods = {}
    for name in ("numpy", "scipy", "skimage", "trimesh"):
        try:
            mods[name] = __import__(name)
        except Exception as exc:
            errors.append("%s: %s: %s" % (name, type(exc).__name__, exc))
    if errors:
        raise RuntimeError(
            "依赖导入失败：%s。若在源码环境运行请执行："
            "pip install numpy scipy scikit-image trimesh" % " | ".join(errors))
    return mods


def _load_mesh(path, trimesh_mod):
    obj = trimesh_mod.load(str(path), force="scene", process=False)
    if isinstance(obj, trimesh_mod.Scene):
        parts = []
        for geom in obj.dump():
            if getattr(geom, "faces", None) is not None and len(geom.faces) > 0:
                parts.append(geom)
        if not parts:
            raise RuntimeError("模型里没有可用的三角网格")
        mesh = trimesh_mod.util.concatenate(parts)
    else:
        mesh = obj
    if mesh is None or len(getattr(mesh, "faces", [])) == 0:
        raise RuntimeError("模型里没有可用的三角网格")
    return mesh


def _choose_pitch(mesh, max_faces, np):
    area = float(mesh.area)
    if not math.isfinite(area) or area <= 0:
        area = 1.0
    pitch = math.sqrt(2.0 * area / float(max_faces))
    pitch = max(pitch, PITCH_FLOOR)
    extents = mesh.extents
    guard = 0
    while guard < 200:
        shape = np.ceil((extents + 9.0 * pitch) / pitch).astype(np.int64) + 1
        cells = int(shape[0] * shape[1] * shape[2])
        if cells <= CELL_BUDGET:
            return pitch, area, cells
        pitch *= 1.12
        guard += 1
    return pitch, area, CELL_BUDGET


def _sample_surface(mesh, pitch, area, np):
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    target = int(min(8_000_000, max(1_000_000, 3.2 * area / (pitch * pitch))))
    rng = np.random.default_rng(20261110)
    chunks = []
    chunk_faces = 200_000
    remaining = target
    start = 0
    while start < len(faces) and remaining > 0:
        block = faces[start:start + chunk_faces]
        tri = vertices[block]
        cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        block_area = 0.5 * np.linalg.norm(cross, axis=1)
        total = float(block_area.sum())
        if total > 0:
            last = (start + len(block)) >= len(faces)
            want = remaining if last else int(round(target * total / area))
            want = max(1, min(want, remaining))
            counts = rng.multinomial(want, block_area / total)
            picked = np.repeat(np.arange(len(block)), counts)
            if len(picked):
                r1 = rng.random(len(picked))
                r2 = rng.random(len(picked))
                s = np.sqrt(r1)
                tri_pick = tri[picked]
                pts = ((1.0 - s)[:, None] * tri_pick[:, 0]
                       + (s * (1.0 - r2))[:, None] * tri_pick[:, 1]
                       + (s * r2)[:, None] * tri_pick[:, 2])
                chunks.append(pts.astype(np.float32))
                remaining -= len(pts)
        start += chunk_faces
    if not chunks:
        raise RuntimeError("表面采样失败：网格面积为 0")
    return np.concatenate(chunks, axis=0)


def _edge_stats(mesh, np):
    edges = mesh.edges_sorted
    if len(edges) == 0:
        return 0, 0
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int((counts == 1).sum()), int((counts > 2).sum())


def _extract_isosurface(solid, lo, pitch, sigma, deps):
    np = deps["numpy"]
    ndimage = deps["scipy"].ndimage
    measure = deps["skimage"].measure
    trimesh_mod = deps["trimesh"]
    field = ndimage.gaussian_filter(solid.astype(np.float32), sigma=sigma,
                                    mode="constant", cval=0.0)
    field = np.pad(field, 3, mode="constant", constant_values=0.0)
    verts, faces, _normals, _values = measure.marching_cubes(
        field, level=0.5, spacing=(pitch, pitch, pitch))
    verts = verts.astype(np.float64) + (lo - 3.0 * pitch)
    out = trimesh_mod.Trimesh(vertices=verts, faces=faces, process=False)
    out.merge_vertices()
    out.update_faces(out.nondegenerate_faces())
    out.remove_unreferenced_vertices()
    if not out.is_winding_consistent:
        out.fix_normals()
    if float(out.volume) < 0:
        out.invert()
    return out


def _rebuild(mesh, pitch, area, deps, max_faces, closing_iterations=2):
    np = deps["numpy"]
    ndimage = deps["scipy"].ndimage
    trimesh_mod = deps["trimesh"]

    _log("采样表面点（pitch %.2f mm，面积 %.0f mm2）…" % (pitch, area), 0.18)
    points = _sample_surface(mesh, pitch, area, np)

    lo = np.asarray(mesh.bounds[0], dtype=np.float64) - 4.5 * pitch
    hi = np.asarray(mesh.bounds[1], dtype=np.float64) + 4.5 * pitch
    shape = np.ceil((hi - lo) / pitch).astype(np.int64) + 1
    _log("体素化：%d x %d x %d（%.1f 万格）…"
         % (shape[0], shape[1], shape[2], shape.prod() / 10000.0), 0.30)
    ijk = np.floor((points - lo) / pitch).astype(np.int64)
    np.clip(ijk, 0, shape - 1, out=ijk)
    surface = np.zeros(tuple(shape), dtype=bool)
    surface[ijk[:, 0], ijk[:, 1], ijk[:, 2]] = True
    del points, ijk

    structure = ndimage.generate_binary_structure(3, 3)
    surface = ndimage.binary_closing(surface, structure=structure,
                                      iterations=closing_iterations)
    _log("填充内部空腔、合并壳体…", 0.42)
    solid = ndimage.binary_fill_holes(surface)
    del surface

    labels, count = ndimage.label(solid, structure=structure)
    sizes = np.bincount(labels.ravel())
    solid_voxels = int(solid.sum())
    min_voxels = max(12, int(solid_voxels * 1.5e-5))
    keep_ids = np.where(sizes >= min_voxels)[0]
    keep_ids = keep_ids[keep_ids != 0]
    dropped = max(0, count - len(keep_ids))
    solid = np.isin(labels, keep_ids)
    del labels, sizes
    if not solid.any():
        raise RuntimeError("体素重建失败：没有留下任何实体")

    out = None
    boundary = nonmanifold = -1
    for attempt, sigma in enumerate((0.75, 0.85, 0.95, 1.05)):
        _log("提取等值面（平滑 %.2f）…" % sigma, 0.56 + 0.03 * attempt)
        out = _extract_isosurface(solid, lo, pitch, sigma, deps)
        boundary, nonmanifold = _edge_stats(out, np)
        if out.is_watertight:
            break
        _log("本次结果未水密（边界边 %d，非流形边 %d），提高平滑后重试…"
             % (boundary, nonmanifold), 0.60 + 0.03 * attempt)
    del solid

    if not out.is_watertight:
        _log("等值面未闭合，尝试补洞兜底…", 0.74)
        out.fill_holes()
        out.fix_normals()
        if float(out.volume) < 0:
            out.invert()
        boundary, nonmanifold = _edge_stats(out, np)
    if not out.is_watertight:
        raise RuntimeError(
            "重建结果未通过水密校验（边界边 %d，非流形边 %d）" % (boundary, nonmanifold))

    parts = out.split(only_watertight=False)
    measured = []
    for part in parts:
        if len(part.faces) == 0:
            continue
        measured.append((abs(float(part.volume)), part.is_watertight, part))
    if not measured:
        raise RuntimeError("重建结果没有留下可打印的封闭实体")
    largest = max(v for v, _ok, _p in measured)
    min_keep = max(0.5, largest * 5e-4)
    kept = []
    dropped_parts = 0
    for volume, ok, part in measured:
        if volume < min_keep or not ok:
            dropped_parts += 1
            continue
        kept.append(part)
    if not kept:
        raise RuntimeError("重建结果没有留下可打印的封闭实体")
    out = trimesh_mod.util.concatenate(kept) if len(kept) > 1 else kept[0]

    boundary, nonmanifold = _edge_stats(out, np)
    if len(out.faces) > max_faces * 1.6:
        _log("结果面数 %d 超出预算，但为保持水密不做破坏性减面。" % len(out.faces), 0.80)
    return out, {
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "components": len(kept),
        "dropped_components": dropped_parts + dropped,
        "voxel_dropped_islands": dropped,
    }



_3MF_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""

_3MF_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _write_3mf(path, mesh, np):
    """流式写 3MF Core Spec（单色实体），百万面也不会把 XML 全塞进内存。"""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    title = escape(str(getattr(mesh, "metadata", {}).get("name", "") or "model"))
    head = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        ' <metadata name="Application">Sketchfab Downloader</metadata>\n'
        ' <metadata name="Title">%s</metadata>\n'
        ' <resources>\n'
        '  <object id="1" type="model">\n'
        '   <mesh>\n'
        '    <vertices>\n' % title)
    tail = (
        '    </vertices>\n'
        '    <triangles>\n')
    end = (
        '    </triangles>\n'
        '   </mesh>\n'
        '  </object>\n'
        ' </resources>\n'
        ' <build>\n'
        '  <item objectid="1"/>\n'
        ' </build>\n'
        '</model>\n')
    with zipfile.ZipFile(str(path), "w",
                           compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _3MF_CONTENT_TYPES,
                    compress_type=zipfile.ZIP_STORED)
        zf.writestr("_rels/.rels", _3MF_RELS,
                    compress_type=zipfile.ZIP_DEFLATED)
        with zf.open("3D/3dmodel.model", "w", force_zip64=True) as raw:
            with io.TextIOWrapper(raw, encoding="utf-8", newline="\n") as fh:
                fh.write(head)
                step = 50000
                for i in range(0, len(verts), step):
                    chunk = verts[i:i + step]
                    fh.write("".join(
                        '<vertex x="%.4f" y="%.4f" z="%.4f"/>\n' % tuple(v)
                        for v in chunk))
                fh.write(tail)
                step = 100000
                for i in range(0, len(faces), step):
                    chunk = faces[i:i + step]
                    fh.write("".join(
                        '<triangle v1="%d" v2="%d" v3="%d"/>\n' % tuple(t)
                        for t in chunk))
                fh.write(end)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--target-length", type=float, required=True)
    parser.add_argument("--quality", default="fine",
                        choices=sorted(QUALITY_FACES.keys()))
    parser.add_argument("--pitch", type=float, default=0.0,
                        help="手动指定体素间距；0 = 自动")
    parser.add_argument("--name", default="")
    parser.add_argument("--close-iters", type=int, default=2,
                        help="体素闭运算次数；越大壁越厚，细节略钝")
    parser.add_argument("--no-3mf", action="store_true")
    args = parser.parse_args(argv)

    started = time.time()
    try:
        deps = _import_deps()
    except Exception as exc:
        return _fail(str(exc))
    np = deps["numpy"]
    trimesh_mod = deps["trimesh"]

    source = Path(args.source)
    if not source.is_file():
        return _fail("源文件不存在：%s" % source)
    if source.suffix.lower() not in SUPPORTED:
        return _fail("不支持的输入格式：%s" % source.suffix)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        _log("读取模型并展开场景图…", 0.04)
        mesh = _load_mesh(source, trimesh_mod)
        source_faces = len(mesh.faces)
        _log("源模型：%d 个三角形" % source_faces, 0.10)

        if source.suffix.lower() in Y_UP_FORMATS:
            mesh.apply_transform(trimesh_mod.transformations.rotation_matrix(
                math.pi / 2.0, [1.0, 0.0, 0.0]))
        mesh.metadata = {}
        try:
            mesh.visual = trimesh_mod.visual.ColorVisuals()
        except Exception:
            pass

        original_extents = [float(v) for v in mesh.extents]
        longest = max(original_extents)
        if longest <= 0:
            return _fail("模型尺寸无效")
        scale = float(args.target_length) / longest
        mesh.apply_scale(scale)
        bounds = mesh.bounds
        mesh.apply_translation([
            -(bounds[0][0] + bounds[1][0]) / 2.0,
            -(bounds[0][1] + bounds[1][1]) / 2.0,
            -bounds[0][2],
        ])
        _log("成品尺寸：%.2f x %.2f x %.2f mm（源最长边 %.2f，缩放 x%.4g）"
             % (mesh.extents[0], mesh.extents[1], mesh.extents[2],
                longest, scale), 0.14)

        max_faces = QUALITY_FACES[args.quality]
        if args.pitch > 0:
            pitch = max(float(args.pitch), 0.05)
            area = float(mesh.area)
        else:
            pitch, area, _cells = _choose_pitch(mesh, max_faces, np)
        _log("体素间距 %.2f mm（预算 %d 面）" % (pitch, max_faces), 0.16)

        rebuilt, stats = _rebuild(mesh, pitch, area, deps, max_faces,
                                    closing_iterations=args.close_iters)
        del mesh

        stem = args.name or source.stem
        stl_path = outdir / (stem + ".stl")
        mf3_path = outdir / (stem + ".3mf")
        _log("导出 STL…", 0.88)
        rebuilt.export(str(stl_path))
        outputs = [stl_path]
        if not args.no_3mf:
            _log("导出 3MF…", 0.92)
            _write_3mf(mf3_path, rebuilt, np)
            outputs.append(mf3_path)

        volume_mm3 = abs(float(rebuilt.volume))
        grams = volume_mm3 / 1000.0 * 1.24
        report = {
            "source": str(source),
            "outputs": [str(p) for p in outputs],
            "stl": str(stl_path),
            "3mf": None if args.no_3mf else str(mf3_path),
            "facets": len(rebuilt.faces),
            "vertices": len(rebuilt.vertices),
            "source_faces": source_faces,
            "pitch_mm": round(pitch, 3),
            "target_length_mm": round(float(args.target_length), 3),
            "bbox_mm": [round(float(v), 3) for v in rebuilt.extents],
            "volume_cm3": round(volume_mm3 / 1000.0, 3),
            "grams_estimate_pla": round(grams, 1),
            "watertight": bool(rebuilt.is_watertight),
            "winding_consistent": bool(rebuilt.is_winding_consistent),
            "boundary_edges": stats["boundary_edges"],
            "nonmanifold_edges": stats["nonmanifold_edges"],
            "components": stats["components"],
            "dropped_components": stats["dropped_components"],
            "quality": args.quality,
            "closing_iterations": args.close_iters,
            "elapsed_sec": round(time.time() - started, 1),
            "sizes_bytes": {str(p): p.stat().st_size for p in outputs},
        }
        (outdir / "advanced_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        _emit({"stage": "done", "progress": 1.0, "report": report})
        return 0
    except Exception as exc:
        return _fail("%s: %s" % (type(exc).__name__, exc))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())