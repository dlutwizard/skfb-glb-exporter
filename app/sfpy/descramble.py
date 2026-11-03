# -*- coding: utf-8 -*-
"""Sketchfab 贴图保护还原（descramble）的 Python 实现。

算法来自 Sketchfab 查看器的 texture-protection 片元着色器（pv=1），
JS 参考实现见 app/extlib/descramble.js。这里是逐函数移植，语义必须保持一致：

* JS 的 `Math.trunc(a / b)` 对负数向零取整，Python 的 `//` 是向下取整，
  所以**不能**直接用 `//`，统一走 `_trunc_div`。
* 像素映射只与 (w, h, pk) 有关，因此用 numpy 向量化一次算出置换表，
  再整块取样，2048×2048 也能在 1 秒内完成（纯 Python 循环要几十秒）。

对外接口：
    descramble_rgba(rgba, w, h, pk, flip_y=True) -> np.ndarray  # (h, w, 4) uint8
    descramble_image(img: PIL.Image, pk) -> PIL.Image           # 输出 RGBA PNG 语义
"""

from __future__ import annotations

import numpy as np

__all__ = ["descramble_rgba", "descramble_image", "offset_from_pk", "build_permutation"]


def _trunc_div(a, b):
    """JS 语义的整数除法：向零取整。"""
    a = np.asarray(a, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    q = np.abs(a) // np.abs(b)
    return np.where((a < 0) != (b < 0), -q, q)


def _mod(a, m):
    """JS 语义取模（结果符号跟随被除数）。"""
    return a - _trunc_div(a, m) * m


def _f3(y, t, f):
    x = np.minimum(y, t)
    n = np.maximum(y, t)
    tri_x = x * (x + 1) // 2
    r = f - n
    tail = tri_x + x * (n - x) + (x - 1) * r - ((r - 1) * r) // 2
    middle = tri_x + x * (f - x)
    head = (f * (f + 1)) // 2
    return np.where(f < x, head, np.where(f < n, middle, tail))


def _i3(y, t, xx, xy):
    r = np.minimum(y, t)
    n = np.maximum(y, t)
    v = xx + xy
    even = _mod(v, 2) == 0

    b1 = np.where(even, _f3(y, t, v) + v - xy, _f3(y, t, v) + xy)

    s2 = np.where(y < t, r - (y - xx), t - xy - 1)
    b2 = np.where(even, _f3(y, t, v) + s2, _f3(y, t, v) + r - s2 - 1)

    s3 = t - xy - 1
    e3 = r + n - v - 1
    b3 = np.where(even, _f3(y, t, v) + s3, _f3(y, t, v) + e3 - s3 - 1)

    return np.where(v < r, b1, np.where(v < n, b2, b3))


def _u3(y, t, x):
    v = np.minimum(y, t)
    r = np.maximum(y, t)
    tri = v * (v + 1) // 2

    # --- 分支 1：x < trunc(v*(v+1)/2) ---
    cond1 = x < tri
    n1 = ((-1 + 1e-6 + np.sqrt(8 * x + 1)) / 2).astype(np.int64)
    h1 = x - _f3(y, t, n1)
    even1 = _mod(n1, 2) == 0
    a1 = np.where(even1, h1, n1 - h1)
    b1 = np.where(even1, n1 - h1, h1)

    # --- 分支 2：中间条带 ---
    cond2 = x < tri + v * (r - v)
    x2 = x - tri
    safe_v = np.where(v == 0, 1, v)
    n2 = v + _trunc_div(x2, safe_v)
    s2 = _mod(x2, safe_v)
    even2 = _mod(n2, 2) == 0
    g2 = n2 - v + s2 + 1
    e2 = v - s2 - 1
    S2 = n2 - s2
    T2 = s2
    y_gt = y > t
    a2 = np.where(y_gt, np.where(even2, g2, S2), np.where(even2, T2, e2))
    b2 = np.where(y_gt, np.where(even2, e2, T2), np.where(even2, S2, g2))

    # --- 分支 3：尾部 ---
    n3a = v * (v - 1) // 2 - (x - (tri + v * (r - v))) - 1
    sqrt_term = np.floor(np.sqrt(np.maximum(8 * n3a + 1, 0))).astype(np.int64)
    s3 = _trunc_div(-1 + sqrt_term, 2)
    n3 = r + v - s3 - 2
    h3 = x - _f3(y, t, n3)
    even3 = _mod(n3, 2) == 0
    e3 = v + r - n3 - 1
    h3b = np.where(even3, e3 - h3 - 1, h3)
    S3 = n3 + h3b - y + 1
    a3 = n3 - S3
    b3 = S3

    return (
        np.where(cond1, a1, np.where(cond2, a2, a3)),
        np.where(cond1, b1, np.where(cond2, b2, b3)),
    )


def _fvec(vx, vy, uSx, uSy):
    y = uSx // 8
    t = uSy // 8
    xt = _i3(y, t, _trunc_div(vx, 8), _trunc_div(vy, 8))
    n4 = _mod(xt, 4)
    rx0 = _mod(vx, 8)
    ry0 = _mod(vy, 8)
    rx = np.select([n4 == 1, n4 == 2, n4 == 3], [7 - rx0, ry0, 7 - ry0], rx0)
    ry = np.select([n4 == 1, n4 == 2, n4 == 3], [ry0, rx0, rx0], ry0)
    return xt * 64 + rx + ry * 8


def _i1(i, uSx, uSy):
    area = uSx * uSy
    i = np.where(i < 0, i + area, i)
    i = _mod(i, area)
    y = uSx // 8
    n = uSy // 8
    tile = _trunc_div(i, 64)
    r = i - tile * 64
    s = _trunc_div(r, 8)
    S = r - s * 8
    e = _mod(tile, 4)
    gx, gy = _u3(y, n, tile)
    Tx = gx * 8
    Ty = gy * 8
    Tx = np.select([e == 0, e == 1, e == 2, e == 3], [Tx + S, Tx + 7 - S, Tx + s, Tx + s], Tx)
    Ty = np.select([e == 0, e == 1, e == 2, e == 3], [Ty + s, Ty + s, Ty + S, Ty + 7 - S], Ty)
    return Tx, Ty


def offset_from_pk(pk, w, h):
    """查看器 uniform：uO = -(pk * 64 % (w * h))。"""
    area = w * h
    if not area:
        return 0
    rem = (int(pk) * 64) % area
    if rem < 0:
        rem += area
    return -rem


def build_permutation(w, h, pk, flip_y=True):
    """算出每个输出像素对应的源线性索引，越界处为 -1。"""
    area = w * h
    offset = offset_from_pk(pk, w, h)

    ys = np.arange(h, dtype=np.int64)
    xs = np.arange(w, dtype=np.int64)
    X, Y = np.meshgrid(xs, ys)
    YY = (h - 1 - Y) if flip_y else Y

    n = _fvec(X, YY, w, h) + offset
    n = np.where(n > area, n - area, n)
    n = np.where(n < 0, n + area, n)
    valid = (n <= area) & (n >= 0)

    sx, sy = _i1(np.where(valid, n, 0), w, h)
    inside = valid & (sx >= 0) & (sy >= 0) & (sx < w) & (sy < h)
    return np.where(inside, sy * w + sx, -1)


def descramble_rgba(rgba, w, h, pk, flip_y=True):
    """把 RGBA（top-left 原点）按 pk 还原，返回新的 (h, w, 4) uint8。"""
    src = np.asarray(rgba, dtype=np.uint8).reshape(-1, 4)
    perm = build_permutation(w, h, pk, flip_y).reshape(-1)
    out = np.zeros((w * h, 4), dtype=np.uint8)
    mask = perm >= 0
    out[mask] = src[perm[mask]]
    return out.reshape(h, w, 4)


def descramble_image(img, pk):
    """PIL.Image → 解密后的 RGBA Image。"""
    from PIL import Image

    rgba = img.convert("RGBA")
    w, h = rgba.size
    if w < 64 or h < 64:
        raise ValueError(f"too small {w}x{h}")
    out = descramble_rgba(np.asarray(rgba), w, h, int(pk))
    return Image.fromarray(out, mode="RGBA")
