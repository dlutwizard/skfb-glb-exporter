# -*- coding: utf-8 -*-
"""
C 组补充：GUI 关于/诊断面板 + 检查更新按钮（R9 硬规则：未配置零联网）。

源码级验证（与 exe 同一份实现）；网络路径全部 mock，不会真联网。
同一进程反复创建/销毁 Tk 根会触发 Tcl 状态错误，因此 root 用模块级共享，
每个用例在其上新建 App 实例。
"""
import json
import time
from unittest import mock

import pytest

tkinter = pytest.importorskip("tkinter")

import sketchfab_gui as gui  # noqa: E402


@pytest.fixture(scope="module")
def root(tk_root):
    yield tk_root


@pytest.fixture
def app(root, monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "CFG_WRITE", tmp_path / "cfg.json")
    monkeypatch.setattr(gui, "CFG_READ_FALLBACK", tmp_path / "fb.json")
    monkeypatch.setattr(gui, "APPDATA_ROOT", tmp_path / "appdata")
    inst = gui.App(root)
    root.update()
    yield inst
    if inst.about is not None:
        inst._close_about()


def test_about_panel_opens(app):
    inst = app
    inst.show_about()
    inst.root.update()
    assert inst.about is not None and inst.about.winfo_exists()
    assert inst.about.title().endswith("v" + gui.__version__)
    assert "repo_var" in inst.about_state
    assert "json_var" in inst.about_state
    assert inst.diag_items, "关于面板应加载自检结果"
    inst._close_about()
    assert inst.about is None


def test_about_panel_reopen_raises_existing(app):
    inst = app
    inst.show_about()
    inst.root.update()
    first = inst.about
    inst.show_about()
    inst.root.update()
    assert inst.about is first, "重复打开应复用同一窗口"
    inst._close_about()


def test_check_update_unconfigured_no_network_no_dialog(app):
    """未配置更新源：点「检查更新」零网络请求，只提示未配置。"""
    inst = app
    inst.show_about()
    inst.root.update()
    with mock.patch("urllib.request.urlopen") as m_open:
        inst.check_update_clicked()
        inst.root.update()
    m_open.assert_not_called()
    assert "未配置" in inst.about_state["upd_var"].get()


def test_check_update_configured_goes_async_mocked(app):
    inst = app
    inst.show_about()
    inst.root.update()
    inst.save_patch({"update_repo": "o/n"})
    payload = json.dumps({"tag_name": "v99.0.0",
                          "html_url": "https://x/rel"}).encode()
    fake_resp = type("R", (), {
        "read": lambda s: payload,
        "__enter__": lambda s: s,
        "__exit__": lambda s, *a: False,
    })()
    with mock.patch("urllib.request.urlopen", return_value=fake_resp):
        inst.check_update_clicked()
        deadline = time.time() + 5
        while time.time() < deadline:
            inst.root.update()
            if inst.update_result is not None:
                break
            time.sleep(0.05)
    assert inst.update_result is not None
    assert inst.update_result.is_newer is True
    assert inst.update_result.latest == "99.0.0"


def test_gui_dual_link_batch_smoke(app, tmp_path):
    """GUI 双链接并行冒烟：不真下载，mock 引擎与网络，验证线程/队列/进度管线。"""
    inst = app
    calls = {"download": []}

    def fake_download_task(url, out, full=True, log=None, progress=None):
        calls["download"].append(url)
        return ("模型A", "fake.glb（1.0 MB）", tmp_path)

    def fake_resolve(url, log=None):
        # v1.1.1 起 GUI 会把 log 传进来（短链改写的提示要打到日志面板）
        return "0" * 32, url

    def fake_info(uid):
        return {"name": "模型A", "faceCount": 1, "license": {"label": "CC"},
                "isDownloadable": True}

    with mock.patch.object(gui, "download_task", fake_download_task), \
            mock.patch.object(gui, "resolve_and_uid", fake_resolve), \
            mock.patch.object(gui, "fetch_info", fake_info):
        inst.url_text.insert("1.0", "https://skfb.ly/aaaaaa\nhttps://skfb.ly/bbbbbb\n")
        inst.conc_var.set(2)
        outdir = tmp_path / "out"
        outdir.mkdir()
        inst.out_var.set(str(outdir))
        inst.start()
        deadline = time.time() + 10
        while time.time() < deadline and inst.busy:
            inst.root.update()
            time.sleep(0.05)
        inst.root.update()
    assert sorted(calls["download"]) == ["https://skfb.ly/aaaaaa", "https://skfb.ly/bbbbbb"]
    assert inst.finished == 2 and inst.ok_count == 2 and inst.fail_count == 0
    assert not inst.busy
