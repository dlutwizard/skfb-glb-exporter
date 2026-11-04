# -*- coding: utf-8 -*-
"""
R6 / slim.py 守卫规则引擎测试。

用 tmp_path 造一个假 onedir（不碰真实发布物）：
  * S1/S2 顶层 libvips 重复副本 → MD5 一致才允许删
  * --dry-run 不动任何文件
  * MD5 不匹配 → exit 1 且一个文件都不删
  * @img/colour 在真实包内必须存在（PRD 曾误列可删）
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SLIM = ROOT / "build" / "slim.py"

PY = sys.executable

VIPS_TOP = "_internal/libvips-42.dll"
VIPS_TWIN = "_internal/node_modules/@img/sharp-win32-x64/lib/libvips-42.dll"
CPP_TOP = "_internal/libvips-cpp-8.18.7.dll"
CPP_TWIN = "_internal/node_modules/@img/sharp-win32-x64/lib/libvips-cpp-8.18.7.dll"
NODE_FILE = "_internal/node_modules/@img/sharp-win32-x64/lib/sharp-win32-x64-v6.node"


def _touch(p: Path, content: bytes = b"x" * 100):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


@pytest.fixture
def fake_onedir(tmp_path):
    """S1~S5 全部守卫可通过的最小 onedir 骨架。"""
    od = tmp_path / "od"
    od.mkdir()
    payload = b"libvips-payload" * 100
    _touch(od / VIPS_TOP, payload)
    _touch(od / VIPS_TWIN, payload)          # 同内容 → MD5 守卫通过
    cpp = b"vips-cpp" * 50
    _touch(od / CPP_TOP, cpp)
    _touch(od / CPP_TWIN, cpp)
    _touch(od / NODE_FILE, b"native-binding")
    _touch(od / "_internal/node_modules/@img/sharp-wasm32/index.js")
    _touch(od / "_internal/node_modules/@emnapi/runtime.js")
    _touch(od / "_internal/node_modules/tslib/tslib.js")
    _touch(od / "_internal/engine/tools/wasm/decrypt.wasm", b"w" * 20000)
    # S6 目标：legacy 解密后端（v1.1.3 起默认删除）
    _touch(od / "_internal/engine/tools/binz/binzDecrypt.exe", b"MZ" + b"\0" * 500)
    return od


def _run_slim(od: Path, *extra):
    # 子进程强制 UTF-8：Windows 控制台默认 GBK，会把中文断言变成乱码（SFD-006）
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([PY, str(SLIM), str(od), *extra],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300, env=env)


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------

def test_dry_run_touches_nothing(fake_onedir):
    before = {p: p.stat().st_size for p in fake_onedir.rglob("*") if p.is_file()}
    proc = _run_slim(fake_onedir, "--dry-run")
    assert proc.returncode == 0, proc.stderr
    after = {p: p.stat().st_size for p in fake_onedir.rglob("*") if p.is_file()}
    assert before == after, "--dry-run 修改了文件"
    assert "将删除" in proc.stdout


def test_dry_run_json_reports_planned_removals(fake_onedir):
    proc = _run_slim(fake_onedir, "--dry-run", "--json")
    assert proc.returncode == 0
    import json
    data = json.loads(proc.stdout)
    assert data["dry_run"] is True
    ids = {r["rule"] for r in data["removed"]}
    assert {"S1", "S2", "S3", "S4", "S5"} <= ids
    assert "S6" in ids, "S6 默认开启：legacy binzDecrypt.exe 不再随包分发（v1.1.3 起）"


# ---------------------------------------------------------------------------
# 真删
# ---------------------------------------------------------------------------

def test_real_run_deletes_duplicates_keeps_protected(fake_onedir):
    # 额外放保护对象，验证绝不被删
    colour = _touch(fake_onedir / "_internal/node_modules/@img/colour/index.js")
    node_exe = _touch(fake_onedir / "_internal/node.exe", b"node")
    sharp_pkg = _touch(fake_onedir / "_internal/node_modules/sharp/lib/index.js")
    wasm = fake_onedir / "_internal/engine/tools/wasm/decrypt.wasm"

    proc = _run_slim(fake_onedir)
    assert proc.returncode == 0, proc.stderr

    assert not (fake_onedir / VIPS_TOP).exists()
    assert not (fake_onedir / CPP_TOP).exists()
    assert not (fake_onedir / "_internal/node_modules/@img/sharp-wasm32").exists()
    assert not (fake_onedir / "_internal/node_modules/@emnapi").exists()
    assert not (fake_onedir / "_internal/node_modules/tslib").exists()

    # 保护清单完好
    assert colour.exists(), "@img/colour 被误删（sharp 核心模块）"
    assert node_exe.exists(), "node.exe 被误删"
    assert sharp_pkg.exists(), "sharp 本体被误删"
    assert wasm.exists(), "wasm 解密模块被误删"
    # 对照副本仍在
    assert (fake_onedir / VIPS_TWIN).exists()


# ---------------------------------------------------------------------------
# 守卫失败 → 整体放弃
# ---------------------------------------------------------------------------

def test_md5_mismatch_aborts_all(fake_onedir):
    """顶层 libvips 与副本内容不同 → exit 1 且一个文件都不删。"""
    (fake_onedir / VIPS_TOP).write_bytes(b"DIFFERENT-CONTENT" * 50)
    targets = [fake_onedir / VIPS_TOP, fake_onedir / CPP_TOP,
               fake_onedir / "_internal/node_modules/@img/sharp-wasm32",
               fake_onedir / "_internal/node_modules/@emnapi",
               fake_onedir / "_internal/node_modules/tslib"]
    proc = _run_slim(fake_onedir)
    assert proc.returncode == 1
    for t in targets:
        assert t.exists(), "守卫失败后仍删除了 %s" % t
    assert "MD5" in (proc.stdout + proc.stderr)


def test_missing_twin_aborts(fake_onedir):
    """对照副本不存在 → 不删。"""
    (fake_onedir / VIPS_TWIN).unlink()
    proc = _run_slim(fake_onedir)
    assert proc.returncode == 1
    assert (fake_onedir / VIPS_TOP).exists()


def test_forbidden_rule_rejected(qa_pkg):
    """规则命中保护清单（@img/colour）必须直接判失败。"""
    sys.path.insert(0, str(ROOT / "build"))
    import slim
    rule = slim.SlimRule(rule_id="X", rel_path=slim.MODULES + "/@img/colour",
                         kind="dir")
    s = slim.Slimmer(rules=[rule])
    results = s.guard_results(qa_pkg)
    assert results and results[0][1] is False
    assert "保护清单" in results[0][2]


# ---------------------------------------------------------------------------
# 真实包检查
# ---------------------------------------------------------------------------

def test_colour_present_in_real_package(qa_pkg):
    colour = qa_pkg / "_internal" / "node_modules" / "@img" / "colour"
    assert colour.is_dir(), "@img/colour 不在包内（sharp 必挂）"
    # sharp 源码确实 require 它
    colour_cjs = qa_pkg / "_internal" / "node_modules" / "sharp" / "dist" / "colour.cjs"
    assert "@img/colour" in colour_cjs.read_text(encoding="utf-8", errors="replace")


def test_slimmed_files_gone_in_real_package(qa_pkg):
    """真实发布包里 S1~S6 的目标必须已经不存在（构建期瘦身后）。"""
    internal = qa_pkg / "_internal"
    assert not (internal / "libvips-42.dll").exists()
    assert not (internal / "libvips-cpp-8.18.7.dll").exists()
    assert not (internal / "node_modules" / "@img" / "sharp-wasm32").exists()
    assert (internal / "node_modules" / "@img" / "sharp-win32-x64").is_dir()
    assert (internal / "engine" / "tools" / "wasm" / "decrypt.wasm").is_file()
    # S6：legacy 后端已不再分发（SFD-008 的“legacy 不可用”在打包层面的体现）
    assert not (internal / "engine" / "tools" / "binz" / "binzDecrypt.exe").exists()


def test_dry_run_on_real_package_is_noop(qa_pkg):
    """对真实包跑 --dry-run：exit 0（守卫应视为无活干/全过），且不删任何文件。"""
    def snapshot():
        return {str(p): p.stat().st_size for p in qa_pkg.rglob("*") if p.is_file()}
    before = snapshot()
    proc = _run_slim(qa_pkg, "--dry-run")
    after = snapshot()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert before == after
