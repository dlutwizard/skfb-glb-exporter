# -*- coding: utf-8 -*-
"""
C 组：发布包 CLI 冒烟 + 冷启动（M3）。

被测对象 = 从发布 zip 解包到全新目录的那一份 exe。
"""
import re
import statistics
import subprocess
import time

import pytest

from version import __version__


def _run(exe, *args, timeout=180):
    return subprocess.run([str(exe), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def test_selftest_ok(qa_exe):
    proc = _run(qa_exe, "--selftest")
    assert proc.returncode == 0
    assert "SELFTEST_OK" in (proc.stdout + proc.stderr)


def test_env_has_three_new_lines(qa_exe):
    """--env 必须包含 NODE_LOOKUP / DECRYPT_BACKEND / BUILD 三行（架构 T02 验收）。"""
    proc = _run(qa_exe, "--env")
    assert proc.returncode == 0
    out = proc.stdout + proc.stderr
    for key in ("RES", "BASE", "ENGINE", "NODE", "CONVERT",
                "SHARP", "WASM", "EXTLIB"):
        assert re.search(r"^%s\s+=" % key, out, re.M), "缺旧行 %s" % key
    m = re.search(r"NODE_LOOKUP=(.+)", out)
    assert m and "node.exe" in m.group(1).lower(), "NODE_LOOKUP 应回到包内 node.exe：%r" % out
    m = re.search(r"DECRYPT_BACKEND=(\w+)", out)
    assert m, "缺 DECRYPT_BACKEND 行"
    assert m.group(1) == "wasm", "生产后端应为 wasm，实际 %s" % m.group(1)
    # 版本号从 app/version.py 读，不在测试里写死（SFD-006）
    m = re.search(r"BUILD=(\S+) \(", out)
    assert m, "BUILD 行缺版本：%r" % out
    assert m.group(1) == __version__, "BUILD=%s 与 version.py 的 %s 不一致" % (m.group(1), __version__)


def test_env_all_ok(qa_exe):
    proc = _run(qa_exe, "--env")
    out = proc.stdout + proc.stderr
    assert "MISSING" not in out, "发布包自检不应有 MISSING：\n%s" % out


def test_cold_start_median_5(qa_exe):
    """M3：--selftest ×5 中位 ≤ 1.7 s。"""
    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        proc = _run(qa_exe, "--selftest")
        elapsed = time.perf_counter() - t0
        assert "SELFTEST_OK" in (proc.stdout + proc.stderr)
        samples.append(elapsed)
    median = statistics.median(samples)
    print("\n冷启动 5 次: %r → 中位 %.2f s" % ([round(s, 2) for s in samples], median))
    assert median <= 1.7, "冷启动中位 %.2f s 超过 1.7 s（样本 %r）" % (median, samples)


# ---------------------------------------------------------------------------
# R3 / R8：包内 CHANGELOG 与使用说明
# ---------------------------------------------------------------------------

def test_packaged_changelog_has_both_versions(qa_res):
    clog = qa_res / "CHANGELOG.md"
    assert clog.is_file(), "包内缺 CHANGELOG.md（R3）"
    text = clog.read_text(encoding="utf-8", errors="replace")
    # 当前版本必须有条目；1.0.0 是历史基线，必须保留（不写死中间版本号）
    assert __version__ in text, "CHANGELOG 应含当前版本 v%s" % __version__
    assert "1.0.0" in text, "CHANGELOG 应保留 v1.0.0 基线条目"


def test_packaged_manual_no_hardcoded_sizes(qa_pkg, qa_res):
    """R8：使用说明.txt 不应有写死的体积/版本数字（构建时注入真实值）。"""
    manual = qa_pkg / "使用说明.txt"
    assert manual.is_file()
    text = manual.read_text(encoding="utf-8", errors="replace")
    for banned in ("150MB", "150 MB", "40秒", "40 秒"):
        assert banned not in text, "使用说明仍含写死数字：%s" % banned
    # 版本号应来自构建注入
    assert __version__ in text, "使用说明应含构建注入的版本号 v%s" % __version__
