# -*- coding: utf-8 -*-
"""
pytest 共享配置。

设计目标（SFD-006）：**干净检出即可跑**——不依赖任何本机路径、不依赖预置发布包。

被测发布物的来源按优先级：
  1. 环境变量 ``QA_PKG``（已解包的发布目录）/ ``QA_EXE``（可执行文件）
  2. 环境变量 ``QA_RELEASE_ZIP``（发布 zip）
  3. 仓库内 ``发布/Sketchfab下载器_v<当前版本>.zip``（构建后自动存在）
  4. 都没有 → 相关用例 ``skip`` 并说明如何获取，**不报 error**

第 3 种情况会自动解压到 pytest 临时目录，保证测的是真正的发布物，
而不是构建中间产物。
"""
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
for _p in (str(APP_DIR), str(ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from version import __version__  # noqa: E402  （app/version.py，零依赖）


def _default_release_zip() -> Path:
    """发布目录里与当前版本匹配的 zip；没有就返回约定路径（用于 skip 提示）。"""
    rel = ROOT / "发布"
    exact = rel / ("Sketchfab下载器_v%s.zip" % __version__)
    if exact.is_file():
        return exact
    cands = sorted(rel.glob("Sketchfab下载器_v*.zip"))
    if cands:
        return cands[-1]
    return exact


QA_PKG = Path(os.environ["QA_PKG"]) if os.environ.get("QA_PKG") else None
QA_EXE = Path(os.environ["QA_EXE"]) if os.environ.get("QA_EXE") else None
QA_RELEASE_ZIP_EXPLICIT = bool(os.environ.get("QA_RELEASE_ZIP"))
QA_RELEASE_ZIP = Path(os.environ["QA_RELEASE_ZIP"]) if QA_RELEASE_ZIP_EXPLICIT else _default_release_zip()

_HOWTO = (
    "未找到发布包。三种提供方式任选其一：\n"
    "  1) 构建：python build/build.py（会在 发布/ 生成 zip）\n"
    "  2) 设 QA_RELEASE_ZIP=<发布 zip 路径>\n"
    "  3) 设 QA_PKG=<已解包的发布目录>"
)


def _zip_version(path: Path) -> str:
    """从发布 zip 里读 version.json 的版本号；读不到就退回文件名推断。"""
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith("version.json") and name.count("/") <= 1:
                    try:
                        return str(json.loads(z.read(name).decode("utf-8")).get("version") or "")
                    except Exception:                       # noqa: BLE001
                        continue
    except Exception:                                       # noqa: BLE001
        pass
    m = re.search(r"_v([0-9][0-9.]*)\.zip$", path.name)
    return m.group(1) if m else ""


def qa_exe_healthy(pkg: Path) -> bool:
    """exe 是否可用（存在且体积合理；被安全软件隔离时通常直接消失）。"""
    exe = pkg / "Sketchfab下载器.exe"
    try:
        return exe.is_file() and exe.stat().st_size > 1_000_000
    except OSError:
        return False


def _unwrap(pkg: Path) -> Path:
    """发布 zip 顶层带一层 ``Sketchfab下载器/``；指向父目录时自动下钻。"""
    if (pkg / "Sketchfab下载器.exe").is_file() or (pkg / "_internal").is_dir():
        return pkg
    inner = pkg / "Sketchfab下载器"
    if (inner / "_internal").is_dir() or (inner / "Sketchfab下载器.exe").is_file():
        return inner
    return pkg


@pytest.fixture(scope="session")
def qa_pkg(tmp_path_factory) -> Path:
    """发布包根目录（含 _internal / version.json / 使用说明.txt）。"""
    if QA_PKG is not None:
        pkg = _unwrap(QA_PKG)
        if not (pkg / "_internal").is_dir():
            pytest.skip("QA_PKG 不是有效的发布目录：%s" % QA_PKG)
        return pkg
    if QA_EXE is not None and QA_EXE.parent.is_dir():
        return _unwrap(QA_EXE.parent)
    if QA_RELEASE_ZIP.is_file():
        got = _zip_version(QA_RELEASE_ZIP)
        if got and got != __version__ and not QA_RELEASE_ZIP_EXPLICIT:
            pytest.skip("发布包是 v%s，源码是 v%s —— 先构建再跑发布物用例（%s）"
                        % (got, __version__, QA_RELEASE_ZIP.name))
        dest = tmp_path_factory.mktemp("qa_pkg")
        with zipfile.ZipFile(QA_RELEASE_ZIP) as z:
            z.extractall(dest)
        return _unwrap(dest)
    pytest.skip(_HOWTO + "\n（当前期望路径：%s）" % QA_RELEASE_ZIP)


@pytest.fixture(scope="session")
def qa_res(qa_pkg: Path) -> Path:
    res = qa_pkg / "_internal"
    if not res.is_dir():
        pytest.skip("发布包缺少 _internal：%s" % res)
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
        pytest.skip(_HOWTO + "\n（当前期望路径：%s）" % QA_RELEASE_ZIP)
    got = _zip_version(QA_RELEASE_ZIP)
    if got and got != __version__ and not QA_RELEASE_ZIP_EXPLICIT:
        pytest.skip("发布包是 v%s，源码是 v%s —— 先构建再跑发布物用例" % (got, __version__))
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
