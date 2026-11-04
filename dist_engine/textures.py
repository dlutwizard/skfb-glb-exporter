#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sketchfab texture download (public API, no login).

v1.1.8 起这里是**唯一**的贴图下载入口：Node 转换器不再自己联网取图，
改为读取本目录写出的 ``textures_manifest.json`` 并对带 pk 的图做解密回写。
这样同一张贴图在一次下载中只会发生一次网络请求（SFD-005）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

Progress = Optional[Callable[[str], None]]

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _log(progress: Progress, msg: str) -> None:
    if progress:
        try:
            progress(msg)
        except Exception:
            pass


def fetch_texture_manifest(uid: str, http_get_json) -> list[dict]:
    """
    取贴图清单。``?optimized=1`` 是查看器实际使用的端点，返回的是**按材质需要的
    贴图**（本例 14 张）；不带参数是全集（36 张，包含大量用不到的变体）。
    先 optimized 再退普通，与 app/extlib/sf-api.js 的 fetchTextures() 完全一致，
    否则本地会多下 1.5～2 倍的图，且与 GLB 内嵌张数对不上。
    """
    for suffix in ("?optimized=1", ""):
        try:
            data = http_get_json(f"https://sketchfab.com/i/models/{uid}/textures{suffix}")
        except Exception:
            continue
        results = list((data or {}).get("results") or [])
        if results:
            return results
    return []


def _is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def read_pk(tex: dict, img: dict) -> Optional[int]:
    """与 app/extlib/sf-api.js 的 readPk() 保持一致：image 上的 pk 优先于 texture。"""
    raw = img.get("pk") if "pk" in (img or {}) else (tex or {}).get("pk")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def is_descramblable(img: dict, pk: Optional[int]) -> bool:
    """
    对应 sf-api.js 的 isDescramblable()：只有 2 的幂尺寸的图才能正确反混淆。

    非 2 的幂的原图虽然也带 pk，但按混淆用的置换在非 8 对齐网格上不是双射，
    解出来是拼贴错位的图——这类变体必须让位给 2 的幂副本。
    """
    if pk is None:
        return True
    w = int(img.get("width") or 0)
    h = int(img.get("height") or 0)
    if not w or not h:
        return True
    return _is_power_of_two(w) and _is_power_of_two(h)


def _score_image(img: dict) -> float:
    w = int(img.get("width") or 0)
    h = int(img.get("height") or 0)
    area = w * h
    size = int(img.get("size") or 0)
    url = str(img.get("url") or "").lower()
    is_png = url.endswith(".png") or not (img.get("options") or {}).get("quality")
    return area * 10 + (5 if is_png else 0) + min(size, 1_000_000_000) / 1e12


def pick_best_image(images: list[dict], pk: Optional[int]) -> Optional[dict]:
    """挑最大的一张，但优先挑「能解密」的变体（与 sf-api.pickBestImage 对齐）。"""
    if not images:
        return None
    usable = [
        i for i in images
        if not (int(i.get("width") or 0) > 0 and int(i.get("height") or 0) > 0
                and (int(i.get("width") or 0) < 32 or int(i.get("height") or 0) < 32))
    ]
    pool = usable or list(images)
    decodable = [i for i in pool if is_descramblable(i, pk)]
    if decodable:
        pool = decodable
    return max(pool, key=_score_image)


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "tex")
    name = name.strip(" .") or "tex"
    return name


def _image_ids(tex: dict) -> tuple[list[str], list[str]]:
    """返回 (image uids, url 文件名)——供 GLB 材质按 uid/文件名匹配贴图。"""
    uids: list[str] = []
    names: list[str] = []
    tuid = str(tex.get("uid") or "").lower()
    if re.fullmatch(r"[a-f0-9]{32}", tuid):
        uids.append(tuid)
    for im in tex.get("images") or []:
        iu = str((im or {}).get("uid") or "").lower()
        if re.fullmatch(r"[a-f0-9]{32}", iu) and iu not in uids:
            uids.append(iu)
        fn = (str((im or {}).get("url") or "").split("?")[0].split("/")[-1] or "").lower()
        if fn:
            names.append(fn)
            bare = re.sub(r"\.(png|jpe?g|webp|gif)$", "", fn)
            if re.fullmatch(r"[a-f0-9]{32}", bare) and bare not in uids:
                uids.append(bare)
    return uids, names


def download_textures(
    uid: str,
    out_dir: Path,
    *,
    http_get_json,
    download_file,
    progress: Progress = None,
) -> dict[str, Path]:
    """
    下载模型全部贴图（每张一次网络请求），并写出 ``textures_manifest.json``。

    Returns mapping: texture uid / 原始名 / 落盘文件名 → 本地路径。
    Progress tokens (i18n): step:tex_count|N, step:tex_item|i|N|name,
                            step:tex_skip|name, step:tex_fail|name|err
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = fetch_texture_manifest(uid, http_get_json)
    mapping: dict[str, Path] = {}
    entries: list[dict] = []
    failures: list[dict] = []
    _log(progress, f"step:tex_count|{len(results)}")

    used: set[str] = set()
    for i, tex in enumerate(results, start=1):
        tuid = str(tex.get("uid") or "")
        name = _safe_name(str(tex.get("name") or tuid or f"tex_{i}"))
        pk = read_pk(tex, {})
        img = pick_best_image(list(tex.get("images") or []), pk)
        if not img or not img.get("url"):
            _log(progress, f"step:tex_skip|{name}")
            failures.append({"name": name, "error": "no image url"})
            continue
        pk = read_pk(tex, img)
        url = img["url"]

        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXTS:
            url_ext = Path(url.split("?", 1)[0]).suffix.lower()
            ext = url_ext if url_ext in IMAGE_EXTS else ".png"
            if not name.lower().endswith(ext):
                name = f"{name}{ext}"

        # 重名贴图：加上 uid 前缀避免互相覆盖（并发下顺序不稳定）
        fname = f"{tuid}_{name}" if tuid else name
        if fname.lower() in used:
            fname = f"{Path(fname).stem}_{tuid[:8] or i}{Path(fname).suffix}"
        used.add(fname.lower())
        dest = out_dir / fname

        try:
            _log(progress, f"step:tex_item|{i}|{len(results)}|{name}")
            download_file(url, dest)
            image_uids, file_names = _image_ids(tex)
            entries.append({
                "uid": tuid.lower(),
                "name": name,
                "file": dest.name,
                "pk": pk,
                "width": int(img.get("width") or 0),
                "height": int(img.get("height") or 0),
                "url": url,
                "image_uids": image_uids,
                "file_names": file_names,
                "descrambled": False,      # Node 解密成功后回写为 true
            })
            if tuid:
                mapping[tuid] = dest
            mapping[name] = dest
            mapping[fname] = dest
            mapping[dest.name] = dest
        except Exception as e:
            _log(progress, f"step:tex_fail|{name}|{e}")
            failures.append({"name": name, "error": str(e)})

    manifest = {
        "uid": uid,
        "schema": 2,
        "count": len(results),          # API 里的贴图数
        "images": len(entries),         # 实际落盘图片数
        "files": [e["file"] for e in entries],
        "textures": entries,
        "failures": failures,
    }
    (out_dir / "textures_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return mapping
