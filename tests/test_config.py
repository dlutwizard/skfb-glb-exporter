# -*- coding: utf-8 -*-
"""
R7 / 配置与程序分离 + 合并式保存。

  * save_cfg 局部更新不得抹掉其他键（v1.0.0 的整份覆盖缺陷）
  * 便携模式 / APPDATA 优先级（resolve_cfg_paths）
  * --portable 强制写程序目录
"""
import json
from pathlib import Path
from unittest import mock

import pytest

import diagnostics as diag


def _gui():
    """
    取 GUI 模块；没有 tkinter 的环境（精简 Python / 无 tcl 的 Linux 镜像）
    一律 skip，而不是让用例 error —— 干净环境要能只靠 pytest 判断"过没过"（SFD-006）。
    """
    pytest.importorskip("tkinter", reason="GUI 用例需要 tkinter")
    import sketchfab_gui as gui
    return gui


# ---------------------------------------------------------------------------
# resolve_cfg_paths：便携 / APPDATA 优先级
# ---------------------------------------------------------------------------

def test_appdata_mode_when_no_portable_cfg(tmp_path):
    base, appdata = tmp_path / "base", tmp_path / "appdata"
    base.mkdir()
    write, fallback = diag.resolve_cfg_paths(base, appdata, portable=False)
    assert write == appdata / diag.APP_NAME / "config.json"
    assert fallback == base / "config.json"


def test_portable_mode_when_base_cfg_exists(tmp_path):
    base, appdata = tmp_path / "base", tmp_path / "appdata"
    base.mkdir()
    (base / "config.json").write_text("{}", encoding="utf-8")
    write, fallback = diag.resolve_cfg_paths(base, appdata, portable=False)
    assert write == base / "config.json", "程序目录有 config.json 应进入便携模式"


def test_portable_flag_forces_base(tmp_path):
    base, appdata = tmp_path / "base", tmp_path / "appdata"
    base.mkdir()   # 没有 config.json，但显式 --portable
    write, _ = diag.resolve_cfg_paths(base, appdata, portable=True)
    assert write == base / "config.json"


# ---------------------------------------------------------------------------
# save_cfg / load_cfg 合并式保存（需 import sketchfab_gui 的实现）
# ---------------------------------------------------------------------------

@pytest.fixture
def gui_module(tmp_path, monkeypatch):
    """加载 sketchfab_gui 并把 CFG_WRITE/CFG_READ_FALLBACK 指向临时目录。"""
    tk = pytest.importorskip("tkinter")
    import sketchfab_gui as gui
    write_cfg = tmp_path / "cfg.json"
    monkeypatch.setattr(gui, "CFG_WRITE", write_cfg)
    monkeypatch.setattr(gui, "CFG_READ_FALLBACK", tmp_path / "fallback.json")
    return gui


def test_save_cfg_partial_update_keeps_other_keys(gui_module, tmp_path):
    gui = gui_module
    write = gui.CFG_WRITE
    gui.save_cfg({"out_dir": r"F:\a", "concurrency": 3,
                  "update_repo": "owner/repo", "decrypt_backend": "auto"})
    # 只改 out_dir，其他键必须原样保留
    merged = gui.save_cfg({"out_dir": r"F:\b"})
    data = json.loads(write.read_text(encoding="utf-8"))
    assert data["out_dir"] == r"F:\b"
    assert data["concurrency"] == 3
    assert data["update_repo"] == "owner/repo"
    assert data["decrypt_backend"] == "auto"
    assert merged == data


def test_save_cfg_creates_nested_dirs(gui_module):
    gui = gui_module
    write = gui.CFG_WRITE
    assert not write.parent.exists() or write.parent.is_dir()
    gui.save_cfg({"out_dir": "x"})
    assert write.is_file()


def test_load_cfg_reads_fallback_when_write_missing(gui_module, tmp_path):
    gui = gui_module
    fb = gui.CFG_READ_FALLBACK
    fb.parent.mkdir(parents=True, exist_ok=True)
    fb.write_text(json.dumps({"out_dir": r"D:\legacy"}), encoding="utf-8")
    assert gui.load_cfg().get("out_dir") == r"D:\legacy"


def test_load_cfg_corrupt_file_tolerated(gui_module):
    gui = gui_module
    gui.CFG_WRITE.write_text("{corrupt json", encoding="utf-8")
    assert gui.load_cfg() == {}


# ---------------------------------------------------------------------------
# default_out_dir：跨机器可用（v1.1.7 起不再探测 F 盘）
# ---------------------------------------------------------------------------

def test_default_out_dir_uses_downloads_when_present(monkeypatch):
    """有系统下载目录时返回 <下载>/Sketchfab模型，且一定是绝对路径。"""
    gui = _gui()
    home = Path.home()
    real_is_dir = Path.is_dir

    def fake_is_dir(self):
        if str(self).replace("\\", "/").rstrip("/").lower().endswith(("/downloads", "/下载", "/下载文件")):
            return True
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    result = gui.default_out_dir()
    assert Path(result).is_absolute()
    assert result.endswith("Sketchfab模型")
    assert "迅雷" not in result, "不应再出现开发机专用路径"


def test_default_out_dir_falls_back_to_home(monkeypatch):
    """没有任何下载目录时退回家目录下的 Sketchfab模型。"""
    gui = _gui()
    real_is_dir = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self: False if "Sketchfab模型" not in str(self) else real_is_dir(self))
    result = gui.default_out_dir()
    assert result == str(Path.home() / "Sketchfab模型")


# ---------------------------------------------------------------------------
# decrypt_backend 配置收敛
# ---------------------------------------------------------------------------

def test_normalize_decrypt_mode():
    gui = _gui()
    # legacy（binzDecrypt.exe）自 v1.1.3 随包移除，配置里残留时收敛到 auto
    assert gui.normalize_decrypt_mode("legacy") == "auto"
    assert gui.normalize_decrypt_mode(" WASM ") == "wasm"
    assert gui.normalize_decrypt_mode("bogus") == "auto"
    assert gui.normalize_decrypt_mode(None) == "auto"
    assert "legacy" not in gui.DECRYPT_MODES


def test_apply_node_visibility_always_keeps_res_on_path(tk_root):
    """v1.1.8：只剩 WASM 后端，任何配置值都必须保证 RES 在 PATH 上（SFD-008）。"""
    import os
    import sketchfab_gui as gui
    app = gui.App(tk_root)
    app.apply_node_visibility("auto")
    assert any(os.path.normcase(str(gui.RES)) == os.path.normcase(p)
               for p in os.environ["PATH"].split(os.pathsep))
    # 配置里残留 legacy 时收敛为 auto，但仍保留 RES（否则 node 找不到，直接不可用）
    assert app.apply_node_visibility("legacy") == "auto"
    parts = [os.path.normcase(p) for p in os.environ["PATH"].split(os.pathsep)]
    assert os.path.normcase(str(gui.RES)) in parts
    assert app.apply_node_visibility("auto") == "auto"
