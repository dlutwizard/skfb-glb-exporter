# -*- coding: utf-8 -*-
"""纯 Python 的 osgjs → GLB 导出（不依赖 Node / sharp）。

与 Node 版（app/extlib/osgjs2gltf.js）的分工差异：
* 几何解码复用 dist_engine/osgjs_convert.py（已验证能还原 strip/量化/法线）
* 材质从 osgjs 的 osg.StateSet 读取（osg.Material + TextureAttributeList），
  不再请求 viewer embed 数据
* 贴图按 `<texuid>_*.png` 从工作目录取，按 textures API 的 pk 还原后内嵌
* **不导出骨骼与动画**（v1.2 的已知取舍），节点变换烘焙进顶点坐标
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np

# 引擎侧的几何解码器（打包后位于 engine/，源码运行时位于 dist_engine/）
_here = Path(__file__).resolve().parent
for _cand in (_here.parent.parent / "dist_engine", _here.parent.parent / "engine"):
    if (_cand / "osgjs_convert.py").is_file():
        if str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
        break

import osgjs_convert as oc  # noqa: E402

__all__ = ["convert_to_glb", "collect_scene", "has_skinning"]

_IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def _mat_mul(a, b):
    """列主序 4x4 相乘（osg/OpenGL 约定）：返回 a*b。"""
    a = np.asarray(a, dtype=np.float64).reshape(4, 4).T
    b = np.asarray(b, dtype=np.float64).reshape(4, 4).T
    return (a @ b).T.reshape(16).tolist()


def _udc_values(udc: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(udc, dict):
        for v in udc.get("Values") or []:
            name = v.get("Name")
            if name:
                out[str(name)] = str(v.get("Value"))
    return out


def _state_of(node: dict) -> Optional[dict]:
    ss = node.get("StateSet")
    if isinstance(ss, dict):
        return ss.get("osg.StateSet", ss)
    return None


def _texture_units(state: dict) -> dict[str, str]:
    """unit 语义 → 贴图文件名（不含目录）。

    osgjs 把「第几个 texture unit 是什么用途」记在 StateSet 的 UserDataContainer
    里（sDiffuse / sNormal / sEmissive ...），值是 unit 下标。
    """
    semantic: dict[int, str] = {}
    values = _udc_values(state.get("UserDataContainer"))
    for name, raw in values.items():
        if not name.startswith("s") or len(name) < 2:
            continue
        try:
            idx = int(float(raw))
        except (TypeError, ValueError):
            continue
        key = name[1:].lower()
        semantic[idx] = key

    units: dict[str, str] = {}
    for idx, unit in enumerate(state.get("TextureAttributeList") or []):
        for attr in unit or []:
            for val in attr.values():
                if not isinstance(val, dict):
                    continue
                file_ref = val.get("File")
                if not file_ref:
                    continue
                base = str(file_ref).replace("\\", "/").split("/")[-1]
                units[semantic.get(idx, f"unit{idx}")] = base
    return units


def _material_of(state: dict) -> dict:
    """从 osg.StateSet 提取材质因子与贴图槽。"""
    mat: dict = {}
    for attr in state.get("AttributeList") or []:
        m = attr.get("osg.Material")
        if isinstance(m, dict):
            mat = m
            break

    values = _udc_values(state.get("UserDataContainer"))

    def _vec(name: str, default):
        raw = values.get(name)
        if not raw:
            return default
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) >= 3:
                return [float(parsed[0]), float(parsed[1]), float(parsed[2])]
        except Exception:
            pass
        return default

    def _num(name: str, default):
        raw = values.get(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    diffuse = mat.get("Diffuse") or [1, 1, 1, 1]
    base = _vec("LambertDiffuseColor", [float(diffuse[0]), float(diffuse[1]), float(diffuse[2])])
    base_factor = _num("LambertDiffuseFactor", 1.0)
    alpha = 1.0 - _num("LambertTransparencyFactor", 0.0)
    if isinstance(diffuse, list) and len(diffuse) >= 4:
        alpha *= float(diffuse[3]) if float(diffuse[3]) < 1.0 else 1.0
    emissive = _vec("LambertEmissiveColor", [0.0, 0.0, 0.0])
    emissive_factor = _num("LambertEmissiveFactor", 1.0)
    shininess = _num("PhongShininess", float(mat.get("Shininess") or 25))
    spec_factor = _num("PhongSpecularFactor", 0.0)

    return {
        "name": str(mat.get("Name") or "material"),
        "baseColor": [min(1.0, max(0.0, c * base_factor)) for c in base],
        "alpha": min(1.0, max(0.0, alpha)),
        "emissive": [min(1.0, max(0.0, c * emissive_factor)) for c in emissive],
        "roughness": min(1.0, max(0.04, 1.0 - shininess / 1000.0)),
        "metallic": 0.0 if spec_factor <= 0 else min(1.0, spec_factor),
        "textures": _texture_units(state),
    }


def _decode_geometry(node: dict, bins: dict[str, bytes]) -> Optional[dict]:
    """把单个 osg.Geometry 解码成 positions/normals/uvs/indices。"""
    valist = node.get("VertexAttributeList") or {}
    if "Vertex" not in valist or not valist["Vertex"].get("Array"):
        return None
    user = oc.parse_user_data(node.get("UserDataContainer"))

    tris: list[int] = []
    predict_idx: Optional[list[int]] = None
    for prim in node.get("PrimitiveSetList") or []:
        try:
            idx, mode = oc.decode_indices(prim, user, bins)
        except Exception:
            continue
        if predict_idx is None:
            predict_idx = list(idx)
        if mode == "TRIANGLE_STRIP":
            tris.extend(oc.strip_to_triangles(idx))
        elif mode == "TRIANGLES":
            tris.extend(idx)
    if not tris:
        return None

    positions, _ = oc.decode_vertex_attr("Vertex", valist["Vertex"], user, bins, predict_idx)
    n_verts = len(positions) // 3
    if n_verts <= 0:
        return None
    tris = oc._filter_tris(tris, n_verts)
    if not tris:
        return None
    tris = oc.filter_spike_tris(positions, tris, max_edge_factor=80.0)
    if not tris:
        return None

    uvs = None
    if "TexCoord0" in valist and valist["TexCoord0"].get("Array"):
        try:
            uvs, _ = oc.decode_vertex_attr("TexCoord0", valist["TexCoord0"], user, bins, predict_idx)
            uvs = uvs[: n_verts * 2] if uvs and len(uvs) >= n_verts * 2 else None
        except Exception:
            uvs = None

    normals = None
    if "Normal" in valist and valist["Normal"].get("Array"):
        try:
            normals, _ = oc.decode_vertex_attr("Normal", valist["Normal"], user, bins)
            if not normals or len(normals) // 3 != n_verts:
                normals = None
        except Exception:
            normals = None
    if normals is None:
        normals = oc.recalc_vertex_normals(positions, tris)

    return {"positions": positions, "normals": normals, "uvs": uvs, "triangles": tris,
            "name": str(node.get("Name") or "mesh")}


def has_skinning(scene: dict) -> bool:
    """粗略判断模型是否带骨骼（用于提示用户导出的是静态网格）。"""
    def walk(n, depth=0):
        if depth > 64:
            return False
        if isinstance(n, dict):
            if n.get("BoneMap"):
                return True
            for v in n.values():
                if walk(v, depth + 1):
                    return True
        elif isinstance(n, list):
            for v in n:
                if walk(v, depth + 1):
                    return True
        return False

    return walk(scene)


def collect_scene(scene: dict, work_dir: Path) -> list[dict]:
    """遍历场景图，返回 [{mesh, matrix, material}]（世界矩阵已累积）。"""
    bins = oc._load_bins(Path(work_dir))
    if not bins:
        raise FileNotFoundError("No .bin buffers next to file.osgjs")

    out: list[dict] = []

    def visit(node: Any, matrix: list[float], state: Optional[dict]) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, matrix, state)
            return
        if not isinstance(node, dict):
            return

        # 包一层 {"osg.Xxx": {...}}
        for key, inner in node.items():
            if not isinstance(inner, dict) or not key.startswith("osg."):
                continue
            visit(inner, matrix, state)
        if any(k.startswith("osg.") for k in node.keys()):
            return

        local = node.get("Matrix")
        if isinstance(local, list) and len(local) == 16:
            matrix = _mat_mul(matrix, [float(v) for v in local])

        state = _state_of(node) or state

        if node.get("VertexAttributeList") and node.get("PrimitiveSetList"):
            geom = _decode_geometry(node, bins)
            if geom:
                geom["matrix"] = matrix
                geom["material"] = _material_of(state) if state else None
                out.append(geom)

        for child in node.get("Children") or []:
            visit(child, matrix, state)

    root = scene.get("osg.Node") or scene
    visit(root, list(_IDENTITY), None)
    return out


# ---------------------------------------------------------------- 贴图


def _manifest_pk(uid: str) -> dict[str, int]:
    """textures API：texuid → pk（贴图保护参数）。取不到就返回空表。"""
    import urllib.request

    url = f"https://sketchfab.com/i/models/{uid}/textures?optimized=1"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": f"https://sketchfab.com/models/{uid}/embed",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}
    out: dict[str, int] = {}
    for tex in data.get("results") or []:
        tuid = str(tex.get("uid") or "").lower()
        pk = tex.get("pk")
        if not tuid:
            continue
        try:
            if pk is not None:
                out[tuid] = int(pk)
        except (TypeError, ValueError):
            pass
        for img in tex.get("images") or []:
            iuid = str(img.get("uid") or "").lower()
            if iuid and pk is not None:
                try:
                    out[iuid] = int(pk)
                except (TypeError, ValueError):
                    pass
    return out


def _load_texture(work_dir: Path, filename: str, pks: dict[str, int]) -> Optional[bytes]:
    """找到贴图文件，必要时按 pk 还原，返回 PNG 字节。"""
    tex_dir = Path(work_dir) / "textures"
    if not tex_dir.is_dir():
        return None
    stem = Path(filename).stem.lower()
    candidates = [p for p in tex_dir.glob(f"{stem}_*")] or [p for p in tex_dir.glob(f"*{stem}*")]
    if not candidates:
        return None
    path = candidates[0]

    from PIL import Image
    import io

    pk = pks.get(stem)
    try:
        img = Image.open(path)
        img.load()
        if pk is not None and img.width >= 64 and img.height >= 64:
            from sfpy.descramble import descramble_image
            img = descramble_image(img, pk)
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, format="PNG", optimize=False)
        return buf.getvalue()
    except Exception:
        return None


# ---------------------------------------------------------------- GLB


def _pad4(b: bytearray, fill: int = 0) -> None:
    while len(b) % 4:
        b.append(fill)


def _build_glb(gltf: dict, bin_blob: bytes) -> bytes:
    json_bytes = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "
    bin_bytes = bytes(bin_blob)
    while len(bin_bytes) % 4:
        bin_bytes += b"\x00"

    total = 12 + 8 + len(json_bytes) + (8 + len(bin_bytes) if bin_bytes else 0)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    if bin_bytes:
        out += struct.pack("<II", len(bin_bytes), 0x004E4942) + bin_bytes
    return bytes(out)


def convert_to_glb(work_dir, out_path, model_name: str = "model", uid: str = "",
                   progress=None) -> dict:
    """主入口：读 file.osgjs → 写 GLB。返回统计信息。"""
    work_dir = Path(work_dir)
    scene = json.loads((work_dir / "file.osgjs").read_text(encoding="utf-8"))
    meshes = collect_scene(scene, work_dir)
    if not meshes:
        raise RuntimeError("No mesh data could be decoded from osgjs")

    pks = _manifest_pk(uid) if uid else {}
    skinned = has_skinning(scene)
    if progress:
        progress(f"step:convert|mesh {len(meshes)}｜材质贴图 {sum(1 for m in meshes if m.get('material'))}")
        if skinned:
            progress("该模型含骨骼，已导出静态网格（v1.2 已知取舍）")

    blob = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    gl_meshes: list[dict] = []
    nodes: list[dict] = []
    materials: list[dict] = []
    images: list[dict] = []
    textures: list[dict] = []
    samplers = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
    mat_index: dict[str, int] = {}
    tex_index: dict[str, int] = {}
    used_images: set[str] = set()

    def add_view(data: bytes, target: Optional[int] = None) -> int:
        _pad4(blob)
        offset = len(blob)
        blob.extend(data)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        buffer_views.append(view)
        return len(buffer_views) - 1

    def add_accessor(data: np.ndarray, comp: int, type_name: str, target: int,
                     with_bounds: bool = False) -> int:
        arr = np.ascontiguousarray(data)
        view = add_view(arr.tobytes(), target)
        per = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[type_name]
        acc = {"bufferView": view, "componentType": comp,
               "count": int(arr.size // per), "type": type_name}
        if with_bounds:
            verts = arr.reshape(-1, 3)
            acc["min"] = [float(verts[:, i].min()) for i in range(3)]
            acc["max"] = [float(verts[:, i].max()) for i in range(3)]
        accessors.append(acc)
        return len(accessors) - 1

    for m in meshes:
        mat = m.get("material")
        mat_key = mat["name"] if mat else "default"
        if mat_key not in mat_index:
            entry = {
                "name": mat_key,
                "pbrMetallicRoughness": {
                    "baseColorFactor": (mat["baseColor"] + [mat["alpha"]]) if mat else [1, 1, 1, 1],
                    "metallicFactor": float(mat["metallic"]) if mat else 0.0,
                    "roughnessFactor": float(mat["roughness"]) if mat else 0.9,
                },
                "doubleSided": True,
            }
            if mat and mat["alpha"] < 0.999:
                entry["alphaMode"] = "BLEND"
            if mat and any(c > 0.001 for c in mat["emissive"]):
                entry["emissiveFactor"] = mat["emissive"]

            # 贴图槽
            if mat:
                slots = {
                    "diffuse": "baseColorTexture",
                    "normal": "normalTexture",
                    "emissive": "emissiveTexture",
                }
                for semantic, gltf_key in slots.items():
                    fname = mat["textures"].get(semantic)
                    if not fname:
                        continue
                    key = fname.lower()
                    if key not in tex_index:
                        png = _load_texture(work_dir, fname, pks)
                        if not png:
                            continue
                        view = add_view(png)
                        images.append({"bufferView": view, "mimeType": "image/png"})
                        textures.append({"source": len(images) - 1, "sampler": 0})
                        tex_index[key] = len(textures) - 1
                        used_images.add(key)
                    idx = tex_index[key]
                    if gltf_key == "normalTexture":
                        entry[gltf_key] = {"index": idx}
                    else:
                        entry["pbrMetallicRoughness"][gltf_key] = {"index": idx}

            materials.append(entry)
            mat_index[mat_key] = len(materials) - 1

        positions = np.asarray(m["positions"], dtype=np.float32)
        normals = np.asarray(m["normals"], dtype=np.float32)
        matrix = np.asarray(m["matrix"], dtype=np.float64).reshape(4, 4).T
        rot = matrix[:3, :3]
        pos3 = positions.reshape(-1, 3)
        pos3 = (pos3 @ rot.T) + matrix[:3, 3]
        nrm3 = normals.reshape(-1, 3) @ np.linalg.inv(rot).T  # 法线用逆转置
        lengths = np.linalg.norm(nrm3, axis=1, keepdims=True)
        nrm3 = nrm3 / np.where(lengths == 0, 1, lengths)

        attrs = {"POSITION": add_accessor(pos3.astype(np.float32), 5126, "VEC3", 34962, True),
                 "NORMAL": add_accessor(nrm3.astype(np.float32), 5126, "VEC3", 34962)}
        if m.get("uvs"):
            uvs = np.asarray(m["uvs"], dtype=np.float32).reshape(-1, 2)[: len(pos3)]
            if len(uvs) == len(pos3):
                attrs["TEXCOORD_0"] = add_accessor(uvs, 5126, "VEC2", 34962)

        indices = np.asarray(m["triangles"], dtype=np.uint32)
        prim = {"attributes": attrs, "indices": add_accessor(indices, 5125, "SCALAR", 34963),
                "mode": 4, "material": mat_index[mat_key]}
        gl_meshes.append({"name": m["name"][:64], "primitives": [prim]})
        nodes.append({"mesh": len(gl_meshes) - 1, "name": m["name"][:64]})

    gltf = {
        "asset": {"version": "2.0", "generator": "Sketchfab下载器 (pure python)"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gl_meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(blob)}],
    }
    if images:
        gltf["images"] = images
        gltf["textures"] = textures
        gltf["samplers"] = samplers

    out_path = Path(out_path)
    out_path.write_bytes(_build_glb(gltf, blob))

    return {
        "path": out_path,
        "size": out_path.stat().st_size,
        "meshes": len(gl_meshes),
        "images": len(images),
        "materials": len(materials),
        "skinned": skinned,
    }
