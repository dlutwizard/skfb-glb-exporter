# -*- coding: utf-8 -*-
"""pytest 共享配置：把 app/ 加入 sys.path，提供被测 exe / 包路径 fixture。

v1.1.1 变更（QA）：
  * 包路径 fixture 拆成 ``qa_pkg``（包根目录）与 ``qa_exe``（可执行文件）两级，
    让「只读包内容」的用例（CHANGELOG / version.json / 使用说明）在 exe 不可用时
    依然能跑 —— 安全软件隔离 exe 不该连带包内容校验一起失败。
  * ``qa_exe`` 缺失时 ``pytest.skip`` 而非 assert，避免把环境问题误报成用例失败
    （skip 原因写明「被测 exe 不可用」，不会静默放过真实回归）。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
for p in (str(APP_DIR), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# QA 目标：默认用「从发布 zip 解包到全新临时目录」的那一份，
# 保证测的是发布物而不是构建中间产物。
_DEFAULT_PKG = Path(os.environ.get(
    "QA_PKG",
    r"F:\迅雷下载\_qa_v111_fresh\Sketchfab下载器"))
QA_EXE = Path(os.environ.get("QA_EXE", str(_DEFAULT_PKG / "Sketchfab下载器.exe")))
QA_RELEASE_ZIP = Path(os.environ.get(
    "QA_RELEASE_ZIP", str(ROOT / "发布" / "Sketchfab下载器_v1.1.1.zip")))

import pytest  # noqa: E402


def qa_exe_healthy(pkg: Path) -> bool:
    """exe 是否真的可用（存在且体积合理；被隔离时通常直接消失或不可读）。"""
    exe = pkg / "Sketchfab下载器.exe"
    try:
        return exe.is_file() and exe.stat().st_size > 1_000_000
    except OSError:
        return False


@pytest.fixture(scope="session")
def qa_pkg() -> Path:
    """发布包根目录（含 _internal / version.json / 使用说明.txt）。"""
    pkg = QA_EXE.parent
    assert pkg.is_dir(), "找不到发布包解包目录：%s" % pkg
    return pkg


@pytest.fixture(scope="session")
def qa_res(qa_pkg: Path) -> Path:
    res = qa_pkg / "_internal"
    assert res.is_dir(), "发布包缺少 _internal：%s" % res
    return res


@pytest.fixture(scope="session")
def qa_exe(qa_pkg: Path) -> Path:
    """被测 exe。不存在（如被安全软件隔离）时跳过，而不是误判为失败。"""
    if not qa_exe_healthy(qa_pkg):
        pytest.skip("被测 exe 不可用（疑似被安全软件隔离/删除）：%s"
                    % (qa_pkg / "Sketchfab下载器.exe"))
    return qa_pkg / "Sketchfab下载器.exe"


@pytest.fixture(scope="session")
def release_zip() -> Path:
    """发布 zip 本体：用于「不执行 exe 也能校验」的静态用例（PE 版本号等）。"""
    if not QA_RELEASE_ZIP.is_file():
        pytest.skip("找不到发布 zip：%s" % QA_RELEASE_ZIP)
    return QA_RELEASE_ZIP


@pytest.fixture(scope="session")
def tk_root():
    """全 session 共享一个 Tk 根：同进程反复建/毁根窗口会触发 Tcl 状态错误。"""
    tk = pytest.importorskip("tkinter")
    r = tk.Tk()
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:
        pass
