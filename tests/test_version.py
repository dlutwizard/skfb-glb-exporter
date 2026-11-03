# -*- coding: utf-8 -*-
"""
M5 / R1：版本号单一真源与一致性。

四处必须一致（PRD M5）：
  1. app/version.py（真源）
  2. exe 文件属性 FileVersion
  3. exe --version 输出
  4. GUI 窗口标题（源码级验证 + 副标题由同一常量拼出）
另附带验证包内 version.json 与构建产物 version_info 不含第二硬编码。
"""
import ctypes
import json
import re
import subprocess
import sys

import pytest

from conftest import ROOT
from version import APP_NAME, VERSION_TUPLE, __version__, version_string

# ---------------------------------------------------------------------------
# 1. version.py 真源自洽
# ---------------------------------------------------------------------------


def test_version_tuple_matches_string():
    # 不写死具体版本号：升版时只改 app/version.py，这里只校验自洽性。
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), \
        "__version__ 应为 X.Y.Z 形式，实际 %r" % (__version__,)
    assert tuple(int(x) for x in __version__.split(".")) == VERSION_TUPLE
    assert version_string() == __version__


def test_version_module_imports_nothing_heavy():
    """version.py 声明不得 import 任何东西（构建期会在裸解释器里 exec）。"""
    src = (__import__("pathlib").Path(
        __import__("version").__file__).read_text(encoding="utf-8"))
    imports = [ln.strip() for ln in src.splitlines()
               if ln.strip().startswith(("import ", "from "))]
    assert imports == [], "version.py 不应包含任何 import，实际：%r" % imports


# ---------------------------------------------------------------------------
# 2. exe 文件属性 FileVersion（ctypes 读 VS_VERSION_INFO）
# ---------------------------------------------------------------------------

def _file_version(exe_path) -> tuple:
    size = ctypes.windll.version.GetFileVersionInfoSizeW(str(exe_path), None)
    assert size, "GetFileVersionInfoSizeW 失败：%s" % exe_path
    buf = ctypes.create_string_buffer(size)
    ok = ctypes.windll.version.GetFileVersionInfoW(str(exe_path), 0, size, buf)
    assert ok
    val = ctypes.c_void_p()
    val_len = ctypes.c_uint()
    ok = ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(val),
                                             ctypes.byref(val_len))
    assert ok
    # VS_FIXEDFILEINFO 前 4 个 DWORD：dwSignature, dwStrucVersion, MS, LS
    data = ctypes.string_at(val.value, 16)
    import struct
    sig, _sv, ms, ls = struct.unpack("4I", data)
    assert sig == 0xFEEF04BD
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def test_exe_file_version(qa_exe):
    fv = _file_version(qa_exe)
    assert fv[:3] == VERSION_TUPLE, "exe FileVersion=%r 与 version.py %r 不一致" % (
        fv, VERSION_TUPLE)


def test_release_zip_exe_file_version(release_zip):
    """不执行 exe 也能验 M5：直接从发布 zip 里读 PE 资源段。

    为什么需要这条：安全软件可能隔离 exe（v1.1.1 实测 Trojan:Win32/Bearfoos.B!ml），
    此时 ``test_exe_file_version`` 只能 skip，但版本一致性仍必须被验证。
    读 zip 里的字节不落盘、不执行，绕开杀软的执行拦截。
    """
    pefile = pytest.importorskip("pefile")
    import zipfile

    with zipfile.ZipFile(release_zip) as z:
        names = [n for n in z.namelist() if n.endswith("Sketchfab下载器.exe")]
        assert len(names) == 1, "zip 里应恰有一个主 exe，实际 %r" % names
        data = z.read(names[0])

    pe = pefile.PE(data=data)
    ff = pe.VS_FIXEDFILEINFO[0]
    fv = (ff.FileVersionMS >> 16, ff.FileVersionMS & 0xFFFF,
          ff.FileVersionLS >> 16, ff.FileVersionLS & 0xFFFF)
    assert fv[:3] == VERSION_TUPLE, \
        "发布 zip 内 exe FileVersion=%r 与 version.py %r 不一致" % (fv, VERSION_TUPLE)
    assert fv[3] == 0, "FileVersion 第 4 段应为 0，实际 %r" % (fv,)


# ---------------------------------------------------------------------------
# 3. exe --version 输出
# ---------------------------------------------------------------------------

def test_exe_version_output(qa_exe):
    proc = subprocess.run([str(qa_exe), "--version"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"v(\d+\.\d+\.\d+)", out)
    assert m, "--version 输出里没有版本号：%r" % out
    assert m.group(1) == __version__


def test_exe_version_json(qa_exe):
    proc = subprocess.run([str(qa_exe), "--version", "--json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["version"] == __version__
    assert payload["version_tuple"] == list(VERSION_TUPLE)


# ---------------------------------------------------------------------------
# 4. GUI 窗口标题 / 副标题（源码级：同一份 version.py 常量）
# ---------------------------------------------------------------------------

def test_gui_window_title(tk_root):
    import sketchfab_gui as gui
    root = tk_root
    app = gui.App(root)
    assert root.title() == "Sketchfab 模型下载器 v" + __version__
    # 关于面板标题也带版本
    app.show_about()
    root.update()
    assert app.about is not None
    assert __version__ in app.about.title()
    app._close_about()


# ---------------------------------------------------------------------------
# 5. 包内 version.json 与 CHANGELOG（R2/R3）
# ---------------------------------------------------------------------------

def test_packaged_version_json(qa_res):
    vj = qa_res.parent / "version.json"
    assert vj.is_file(), "包根缺 version.json"
    data = json.loads(vj.read_text(encoding="utf-8"))
    assert data.get("version") == __version__


def test_build_report_version_source():
    """构建生成的 version_info.txt 必须由真源派生（R1：无第二处硬编码）。

    注意：使用说明.txt 里出现 1.0.0 属 CHANGELOG 历史条目、3.11.5 属 Python
    版本号，均为合法内容，不在本检查范围。
    """
    import pathlib
    vi = ROOT / "build" / "gen" / "version_info.txt"
    if not vi.is_file():
        pytest.skip("build/gen/version_info.txt 不存在（未在本机构建）")
    text = vi.read_text(encoding="utf-8", errors="replace")
    found = re.findall(r"'(?:File|Product)Version',\s*'([\d.]+)'", text)
    assert found, "version_info.txt 里没有 FileVersion/ProductVersion"
    for v in found:
        assert v.split(".")[:3] == [str(x) for x in VERSION_TUPLE], \
            "version_info.txt 的 %s 与真源 %s 不一致" % (v, __version__)
