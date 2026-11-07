# -*- coding: utf-8 -*-
"""
v1.2.1 界面改版回归：设计令牌、对比度、样式落地与 GUI 接线。

大部分用例是纯静态/纯计算，干净环境（无 tkinter、无发布包）也能跑；
唯一需要真实 Tk 的用例用 importorskip 降级为 skip 而不是 error。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

import ui_theme as theme  # noqa: E402  (模块本身不依赖 tkinter)


def _luminance(color):
    rgb = [int(color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]

    def linear(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = [linear(c) for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_all_tokens_are_hex_colors():
    for key, value in theme.TOKENS.items():
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), "令牌 %s 不是 6 位十六进制色" % key


def test_body_and_accent_text_meet_wcag_aa():
    """正文对比度 >= 4.5:1，主按钮上的文字同样要够清楚。"""
    assert _contrast(theme.TEXT, theme.BG) >= 4.5
    assert _contrast(theme.MUTED, theme.BG) >= 4.5
    assert _contrast(theme.ON_ACCENT, theme.ACCENT) >= 4.5
    assert _contrast(theme.LOG_FG, theme.LOG_BG) >= 4.5


def test_font_helpers_return_font_tuples():
    for fn in (theme.display, theme.body, theme.mono):
        f = fn(11)
        assert isinstance(f, tuple) and len(f) >= 2
        assert isinstance(f[0], str) and isinstance(f[1], int)
    assert theme.display(11)[2] == "bold"
    assert theme.mono(9, "bold")[2] == "bold"


def test_gui_wires_theme_and_new_widgets():
    src = (ROOT / "app" / "sketchfab_gui.py").read_text(encoding="utf-8")
    assert "import ui_theme as theme" in src
    assert "theme.apply(root, style)" in src
    assert "theme.hide_native_notebook_tabs(style)" in src
    assert "def set_status(" in src
    assert "def _select_tab(" in src
    assert "self.search_empty" in src
    # v1.2.0 的接线不能因为换皮掉线
    assert "self.nb.add(tab_search" in src
    assert '("search_done"' in src and '("search_error"' in src


def test_specs_include_ui_theme_hiddenimport():
    for name in ("Sketchfab下载器.spec", "single_file.spec"):
        src = (ROOT / "build" / name).read_text(encoding="utf-8")
        assert '"ui_theme"' in src, "%s 没把 ui_theme 加进 hiddenimports" % name


def test_no_hardcoded_legacy_palette_in_gui():
    """换皮后不许把旧版蓝/白卡片色值散落回 GUI 源码。"""
    src = (ROOT / "app" / "sketchfab_gui.py").read_text(encoding="utf-8")
    for old in ("#f5f6f8", "#ffffff", "#2f6fed", "#0f172a", "#d7e0ef", "#9aa1ad"):
        assert old not in src, "残留旧配色 %s" % old


def test_apply_styles_and_hide_tabs_on_real_tk():
    pytest.importorskip("tkinter")
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.withdraw()
    try:
        style = ttk.Style()
        fonts = theme.apply(root, style)
        assert set(fonts) == {"display", "body", "mono"}
        assert all(isinstance(v, str) and v for v in fonts.values())
        assert style.lookup("Accent.TButton", "background") == theme.ACCENT
        assert style.lookup("TFrame", "background") == theme.BG
        assert style.lookup("Treeview", "background") == theme.SURFACE
        assert theme.hide_native_notebook_tabs(style) is True
        # Tk 会把空 layout 规范化成单个 'null' 元素，所以按「没有实际绘制元素」判断
        assert all(name == "null" for name, _ in style.layout("TNotebook.Tab"))

        # 真正的验收：原生页签栏消失（client 顶部间距 <= 3px），且 select 仍可用
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)
        f1, f2 = ttk.Frame(nb), ttk.Frame(nb)
        nb.add(f1, text="A")
        nb.add(f2, text="B")
        root.update()
        assert f1.winfo_rooty() - nb.winfo_rooty() <= 3
        nb.select(1)
        root.update()
        assert nb.index(nb.select()) == 1
    finally:
        root.destroy()
