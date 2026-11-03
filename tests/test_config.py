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
# default_out_dir：无 F 盘回退（R7 / 任务 D）
# ---------------------------------------------------------------------------

def test_default_out_dir_without_f_drive(monkeypatch):
    import sketchfab_gui as gui
    real_exists = Path.exists
    calls = []

    def fake_exists(self):
        if str(self).rstrip("\\/") in ("F:", "F:/"):
            calls.append(str(self))
            return False          # 模拟“别人电脑没有 F 盘”
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)
    result = gui.default_out_dir()
    assert calls, "应该探测过 F 盘"
    assert not result.startswith("F:"), "无 F 盘时不应返回 F:\\迅雷下载"
    home = Path.home()
    expected = None
    for name in ("Downloads", "下载", "下载文件"):
        if (home / name).is_dir():
            expected = str(home / name / "Sketchfab模型")
            break
    assert result == (expected or str(home / "Sketchfab模型"))


def test_default_out_dir_with_f_drive():
    import sketchfab_gui as gui
    from pathlib import Path as _P
    if _P("F:/").exists():
        assert gui.default_out_dir() == r"F:\迅雷下载"


# ---------------------------------------------------------------------------
# decrypt_backend 配置收敛
# ---------------------------------------------------------------------------

def test_normalize_decrypt_mode():
    import sketchfab_gui as gui
    assert gui.normalize_decrypt_mode("legacy") == "legacy"
    assert gui.normalize_decrypt_mode(" WASM ") == "wasm"
    assert gui.normalize_decrypt_mode("bogus") == "auto"
    assert gui.normalize_decrypt_mode(None) == "auto"


def test_apply_node_visibility_legacy_removes_res(tk_root):
    """GUI 的 App.apply_node_visibility 是一键回滚开关（legacy = 剔除 RES）。"""
    import os
    import sketchfab_gui as gui
    root = tk_root
    app = gui.App(root)
    app.apply_node_visibility("auto")
    assert any(os.path.normcase(str(gui.RES)) == os.path.normcase(p)
               for p in os.environ["PATH"].split(os.pathsep))
    mode = app.apply_node_visibility("legacy")
    assert mode == "legacy"
    parts = [os.path.normcase(p) for p in os.environ["PATH"].split(os.pathsep)]
    assert os.path.normcase(str(gui.RES)) not in parts, \
        "legacy 模式必须把 RES 从 PATH 剔除"
    assert app.apply_node_visibility("auto") == "auto"
