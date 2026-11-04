# -*- coding: utf-8 -*-
"""贴图解密移植的逐像素对照测试。

用 Node 侧的参考实现（app/extlib/descramble.js）生成期望像素，
再和 Python 版（app/sfpy/descramble.py）逐字节比较。

运行：
    python tests/test_descramble_parity.py
需要本机有 node（仅测试用，打包产物不依赖）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="逐像素对照需要 numpy（pip install numpy）")

ROOT = Path(__file__).resolve().parent.parent


def _find_node() -> str:
    """node 解释器：SFD_NODE_EXE → PATH。测试专用，打包产物不依赖。"""
    env = os.environ.get("SFD_NODE_EXE")
    if env and Path(env).is_file():
        return env
    for name in ("node", "node.exe", "nodejs"):
        hit = shutil.which(name)
        if hit:
            return hit
    return "node"


NODE = _find_node()
REF = ROOT / "tools" / "ref_descramble.mjs"

sys.path.insert(0, str(ROOT / "app"))
from sfpy.descramble import descramble_rgba  # noqa: E402


def make_src(w: int, h: int) -> np.ndarray:
    """与 ref_descramble.mjs 完全一致的确定性输入。"""
    n = w * h * 4
    out = np.empty(n, dtype=np.uint8)
    s = 123456789
    for i in range(n):
        s = (s * 1664525 + 1013904223) & 0xFFFFFFFF
        out[i] = s & 0xFF
    return out.reshape(h, w, 4)


def js_reference(w: int, h: int, pk: int) -> np.ndarray:
    node = NODE
    with tempfile.TemporaryDirectory() as td:
        raw = Path(td) / "ref.raw"
        subprocess.run([node, str(REF), str(w), str(h), str(pk), str(raw)],
                       check=True, capture_output=True)
        return np.frombuffer(raw.read_bytes(), dtype=np.uint8).reshape(h, w, 4)


CASES = [
    (64, 64, 12345),        # 最小可解密尺寸、8 的整数倍
    (128, 96, 7),           # 非方形
    (256, 256, 0),          # pk=0（偏移为 0）
    (400, 401, 98765),      # 非 8 的倍数（真实出现过 400x401 的毛发贴图）
    (512, 512, 2**31 - 1),  # 大 pk
]


def main() -> int:
    failures = 0
    for w, h, pk in CASES:
        src = make_src(w, h)
        got = descramble_rgba(src, w, h, pk, True)
        exp = js_reference(w, h, pk)
        if got.shape != exp.shape:
            print(f"[FAIL] {w}x{h} pk={pk}: shape {got.shape} != {exp.shape}")
            failures += 1
            continue
        diff = int(np.count_nonzero(got != exp))
        if diff:
            idx = np.argwhere(got != exp)[:3]
            print(f"[FAIL] {w}x{h} pk={pk}: {diff} 个字节不同，示例 {idx.tolist()}")
            failures += 1
        else:
            print(f"[ OK ] {w}x{h} pk={pk}: 逐像素一致（{w*h} px）")

    print("\n结果:", "全部一致" if not failures else f"{failures} 个用例不一致")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
