# -*- coding: utf-8 -*-
"""v1.2.4 搜索缩略图 / 大图预览：接线守卫 + 真实 Tk 回归。"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_search_module_exposes_injectable_thumbnail_fetch():
    import sketchfab_search as s

    src = _src("app/sketchfab_search.py")
    assert "def fetch_thumbnail(" in src
    assert "THUMB_TIMEOUT" in src and "MAX_THUMB_BYTES" in src
    assert "CERT_NONE" not in src and "verify=False" not in src

    seen = []

    def fake(url):
        seen.append(url)
        return b"\xff\xd8\xff\xe0JPEG"

    data = s.fetch_thumbnail(
        "https://media.sketchfab.com/t.jpeg", http_get=fake)
    assert data == b"\xff\xd8\xff\xe0JPEG"
    assert seen == ["https://media.sketchfab.com/t.jpeg"]

    with pytest.raises(s.SearchError):
        s.fetch_thumbnail("  ", http_get=fake)
    with pytest.raises(s.SearchError):
        s.fetch_thumbnail("https://x", http_get=lambda u: "not bytes")

    def boom(u):
        raise OSError("offline")

    with pytest.raises(s.SearchError):
        s.fetch_thumbnail("https://x", http_get=boom)


def test_gui_wires_thumbnails_preview_and_queue():
    src = _src("app/sketchfab_gui.py")
    markers = (
        'show="tree headings"',
        'style="Preview.Treeview"',
        'self.search_tree.heading("#0", text="预览")',
        "self.preview_panel",
        "self.preview_img",
        "self.preview_name",
        "self.preview_meta",
        "self.preview_flag",
        "def _queue_thumbnails",
        "def _thumb_worker",
        "def _apply_thumb",
        "def _on_search_select",
        "def _show_preview",
        "def _clear_preview",
        '("search_thumb"',
        "<<TreeviewSelect>>",
        "image_codec.jpeg_to_png",
        "base64.b64encode",
        "self.thumb_cache",
        "self.search_thumbs",
    )
    for marker in markers:
        assert marker in src, "缺少缩略图接线：%s" % marker


def test_theme_defines_preview_treeview():
    src = _src("app/ui_theme.py")
    assert '"Preview.Treeview"' in src
    assert "rowheight=46" in src


def test_specs_include_image_codec():
    for rel in ("build/Sketchfab下载器.spec", "build/single_file.spec"):
        assert '"image_codec"' in _src(rel), "%s 缺少 image_codec hiddenimport" % rel


def test_real_tk_thumbnail_column_and_preview_panel():
    tk = pytest.importorskip("tkinter")
    from tkinter import ttk

    import sketchfab_gui as gui
    import ui_theme as theme

    root = tk.Tk()
    try:
        style = ttk.Style()
        theme.apply(root, style)
        app = gui.App(root)
        root.update()

        assert app._ph_small.width() == 64 and app._ph_small.height() == 36
        assert app._ph_big.width() == 192 and app._ph_big.height() == 108
        assert "tree" in str(app.search_tree.cget("show"))
        rowheight = style.lookup("Preview.Treeview", "rowheight")
        assert rowheight == "" or int(rowheight) >= 40

        page = {"items": [{
            "name": "Future Car", "author": "Alice", "faces": 1201924,
            "downloadable": True, "license": "CC Attribution", "animations": 0,
            "url": "https://sketchfab.com/3d-models/x", "thumbnail": "",
        }], "next_cursor": None}
        app.search_gen = 1
        app._render_search_page(1, "car", page, None, True)
        root.update()
        assert len(app.search_tree.get_children()) == 1
        assert app.search_tree.item("0", "image"), "行内缺少占位缩略图"

        app.search_tree.selection_set("0")
        app._on_search_select()
        assert "Future Car" in app.preview_name.cget("text")
        assert "可下载" in app.preview_flag.cget("text")

        app.search_tree.selection_remove("0")
        app._on_search_select()
        assert app.preview_name.cget("text") == "选中一条结果查看预览"
    finally:
        root.destroy()
