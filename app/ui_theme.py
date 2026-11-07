# -*- coding: utf-8 -*-
"""
ui_theme.py — 界面设计令牌（design tokens）与 ttk 样式。

设计方向：Editorial / Industrial「暗房工作台」
------------------------------------------------------------------
面向的是「批量抓取 3D 资产」的技术型桌面工具，不是消费级 App，所以放弃
浅灰底 + 白卡片 + 蓝按钮那套默认工具外观，改用：

  * 暖调石墨底（非纯黑、非蓝黑），单一琥珀色作为唯一强调色
  * 编辑式规则线与留白分区，而不是给每一块内容都套一张卡片
  * 思源黑体做展示字 / 正文，等宽字体承载数字、路径与日志
  * 自定义页签栏与几何线框标记（等距立方体），呼应 3D 模型

配色比例约 60% 深底 / 30% 面板与线 / 10% 强调色；强调色只用于主按钮、
进度、选中态、版本号与品牌标记，不撒得到处都是。

所有颜色都是模块级常量，控件只引用常量，避免界面里散落十六进制字面量。
``apply()`` 会探测本机字体并逐项降级配置样式：不同 Tk 版本对个别
ttk 选项（bordercolor / arrowcolor / indicatorcolor …）支持不一致，
因此用 ``_configure()`` / ``_map()`` 逐项兜底，绝不因为一个选项抛异常
就让整个界面启动失败。
"""
from __future__ import annotations

# tk 只在真正创建小组件的函数里导入：这样无图形界面的干净环境也能
# import 本模块做令牌/对比度自检（发布物里的 Python 不需要 tkinter 才能检查）。

# ---------------- 设计令牌 ----------------
BG = "#1e222a"          # 主背景：暖调石墨，不用纯黑（v1.2.3 整体提亮一档）
SURFACE = "#262b35"     # 输入 / 面板
SURFACE2 = "#2e3541"    # 更高一层：表头、进度槽、hover
LINE = "#38404d"        # 1px 规则线
TEXT = "#ece9e2"        # 暖白主文字
MUTED = "#8d96a3"       # 次文字（对 BG 约 5.3:1）
DIM = "#6b7582"         # 更弱：禁用、仅浏览（随底色同步上移）
ACCENT = "#e8a23b"      # 唯一强调色：琥珀
ACCENT_HI = "#f2b45c"   # hover
ACCENT_DK = "#c9862a"   # pressed
ON_ACCENT = "#15181d"   # 强调色块上的文字
OK = "#86bd93"
WARN = "#e3b45c"
ERR = "#e28076"
LOG_BG = "#1a1e25"      # 控制台再压暗一档
LOG_FG = "#c8d0dc"

CARD = SURFACE          # 兼容 GUI 里沿用已久的名字

TOKENS = {
    "bg": BG, "surface": SURFACE, "surface2": SURFACE2, "line": LINE,
    "text": TEXT, "muted": MUTED, "dim": DIM, "accent": ACCENT,
    "accent_hi": ACCENT_HI, "accent_dk": ACCENT_DK, "on_accent": ON_ACCENT,
    "ok": OK, "warn": WARN, "err": ERR, "log_bg": LOG_BG, "log_fg": LOG_FG,
}

# ---------------- 字体 ----------------
# 优先用有辨识度的字族；别的电脑上没有时逐级降级，最后落到 Tk 默认字。
FONT_DISPLAY = "Noto Sans SC"
FONT_BODY = "Noto Sans SC"
FONT_MONO = "Cascadia Mono"

_DISPLAY_CANDIDATES = ("Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei UI",
                       "Microsoft YaHei", "TkDefaultFont")
_BODY_CANDIDATES = ("Noto Sans SC", "Source Han Sans SC", "DengXian",
                    "Microsoft YaHei UI", "TkDefaultFont")
_MONO_CANDIDATES = ("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New")


def _pick_font(root, candidates):
    try:
        from tkinter import font as tkfont
        available = set(tkfont.families(root))
        for name in candidates:
            if name in available:
                return name
    except Exception:
        pass
    return candidates[-1]


def display(size=11, weight="bold"):
    """展示字：页签、标题、主按钮。"""
    return (FONT_DISPLAY, size, weight) if weight else (FONT_DISPLAY, size)


def body(size=10, bold=False):
    return (FONT_BODY, size, "bold") if bold else (FONT_BODY, size)


def mono(size=9, bold=False):
    """等宽字：数字、路径、链接、日志、眉题。"""
    return (FONT_MONO, size, "bold") if bold else (FONT_MONO, size)


# ---------------- 兼容不同 Tk 版本的样式写入 ----------------
def _configure(style, name, **opts):
    """一次写入全部选项；某个选项不被支持时逐项重试，忽略不支持的。"""
    try:
        style.configure(name, **opts)
        return
    except Exception:
        pass
    for key, value in opts.items():
        try:
            style.configure(name, **{key: value})
        except Exception:
            pass


def _map(style, name, **opts):
    try:
        style.map(name, **opts)
        return
    except Exception:
        pass
    for key, value in opts.items():
        try:
            style.map(name, **{key: value})
        except Exception:
            pass


def apply(root, style) -> dict:
    """把整套设计令牌装到 ``style`` 上，返回实际生效的字体族。"""
    global FONT_DISPLAY, FONT_BODY, FONT_MONO
    FONT_DISPLAY = _pick_font(root, _DISPLAY_CANDIDATES)
    FONT_BODY = _pick_font(root, _BODY_CANDIDATES)
    FONT_MONO = _pick_font(root, _MONO_CANDIDATES)

    try:
        style.theme_use("clam")          # clam 才能自由改颜色
    except Exception:
        pass

    _configure(style, ".", background=BG, foreground=TEXT, font=body(10),
               bordercolor=LINE, lightcolor=SURFACE2, darkcolor=SURFACE2)

    # --- 容器 ---------------------------------------------------------
    _configure(style, "TFrame", background=BG)
    _configure(style, "Card.TFrame", background=SURFACE)
    _configure(style, "Rule.TFrame", background=LINE)

    # --- 文字层级 -----------------------------------------------------
    _configure(style, "TLabel", background=BG, foreground=TEXT, font=body(10))
    _configure(style, "Card.TLabel", background=SURFACE, foreground=TEXT,
               font=body(10))
    _configure(style, "CardMuted.TLabel", background=SURFACE, foreground=MUTED,
               font=body(9))
    _configure(style, "Title.TLabel", background=BG, foreground=TEXT,
               font=display(22, "bold"))
    _configure(style, "Section.TLabel", background=BG, foreground=TEXT,
               font=display(13, "bold"))
    _configure(style, "Eyebrow.TLabel", background=BG, foreground=ACCENT,
               font=mono(9, "bold"))
    _configure(style, "Kicker.TLabel", background=BG, foreground=MUTED,
               font=mono(9))
    _configure(style, "Sub.TLabel", background=BG, foreground=MUTED,
               font=body(9))
    _configure(style, "Ver.TLabel", background=SURFACE2, foreground=ACCENT,
               font=mono(9, "bold"), padding=(10, 4))
    _configure(style, "Num.TLabel", background=BG, foreground=ACCENT,
               font=mono(20, "bold"))
    _configure(style, "Status.TLabel", background=SURFACE, foreground=MUTED,
               font=body(9))

    # --- 按钮：主按钮实心琥珀 / 次要按钮描边 / 弱按钮纯文字 ------------
    _configure(style, "TButton", font=body(10), padding=(13, 7),
               background=SURFACE, foreground=TEXT, borderwidth=1,
               relief="solid", bordercolor=LINE, lightcolor=LINE,
               darkcolor=LINE)
    _map(style, "TButton",
         background=[("active", SURFACE2), ("pressed", LINE), ("disabled", BG)],
         foreground=[("disabled", DIM), ("active", TEXT)],
         bordercolor=[("active", ACCENT), ("disabled", LINE)])

    _configure(style, "Accent.TButton", font=display(11, "bold"),
               padding=(22, 10), background=ACCENT, foreground=ON_ACCENT,
               borderwidth=0, relief="flat", bordercolor=ACCENT,
               lightcolor=ACCENT, darkcolor=ACCENT)
    _map(style, "Accent.TButton",
         background=[("active", ACCENT_HI), ("pressed", ACCENT_DK),
                     ("disabled", SURFACE2)],
         foreground=[("disabled", DIM)],
         bordercolor=[("active", ACCENT_HI), ("disabled", SURFACE2)])

    # 顶栏打赏入口：实心琥珀、比主按钮小一号。位置在界面最上方，
    # 用同一个强调色保持克制，但足够跳，不会被当成普通工具栏文字按钮。
    _configure(style, "Donate.TButton", font=display(10, "bold"),
               padding=(16, 8), background=ACCENT, foreground=ON_ACCENT,
               borderwidth=0, relief="flat", bordercolor=ACCENT,
               lightcolor=ACCENT, darkcolor=ACCENT)
    _map(style, "Donate.TButton",
         background=[("active", ACCENT_HI), ("pressed", ACCENT_DK),
                     ("disabled", SURFACE2)],
         foreground=[("disabled", DIM)],
         bordercolor=[("active", ACCENT_HI)])

    _configure(style, "Quiet.TButton", font=body(9), padding=(10, 6),
               background=BG, foreground=MUTED, borderwidth=0,
               relief="flat", bordercolor=BG)
    _map(style, "Quiet.TButton",
         background=[("active", BG)],
         foreground=[("active", TEXT), ("disabled", DIM)])

    # --- 输入 ---------------------------------------------------------
    _entry = dict(fieldbackground=SURFACE, foreground=TEXT,
                  insertcolor=ACCENT, borderwidth=1, relief="solid",
                  bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                  padding=6)
    _configure(style, "TEntry", font=mono(10), **_entry)
    _configure(style, "TSpinbox", font=mono(10), arrowcolor=MUTED, **_entry)
    _configure(style, "TCombobox", font=body(9), arrowcolor=ACCENT, **_entry)
    for _name in ("TEntry", "TSpinbox", "TCombobox"):
        _map(style, _name,
             bordercolor=[("focus", ACCENT), ("disabled", LINE)],
             lightcolor=[("focus", ACCENT)],
             darkcolor=[("focus", ACCENT)],
             fieldbackground=[("disabled", BG)])

    _configure(style, "TCheckbutton", background=SURFACE, foreground=TEXT,
               font=body(10), indicatorcolor=SURFACE2, borderwidth=0)
    _map(style, "TCheckbutton",
         background=[("active", SURFACE)],
         foreground=[("disabled", DIM)],
         indicatorcolor=[("selected", ACCENT), ("active", SURFACE2)])

    # --- 页签：隐藏原生 tab，改由自绘栏呈现 ----------------------------
    _configure(style, "TNotebook", background=BG, borderwidth=0,
               tabmargins=(0, 0, 0, 0))
    _configure(style, "TNotebook.Tab", background=BG, foreground=MUTED,
               font=display(11, "bold"), padding=(24, 12), borderwidth=0)
    _map(style, "TNotebook.Tab",
         background=[("selected", SURFACE), ("active", SURFACE2)],
         foreground=[("selected", ACCENT), ("active", TEXT)])

    # --- 表格 ---------------------------------------------------------
    _configure(style, "Treeview", background=SURFACE, fieldbackground=SURFACE,
               foreground=TEXT, borderwidth=0, bordercolor=LINE,
               font=body(10), rowheight=29)
    _map(style, "Treeview",
         background=[("selected", ACCENT)],
         foreground=[("selected", ON_ACCENT)])
    _configure(style, "Treeview.Heading", background=SURFACE2,
               foreground=MUTED, font=mono(9, "bold"), relief="flat",
               borderwidth=0, padding=(8, 7))
    _map(style, "Treeview.Heading",
         background=[("active", LINE)],
         foreground=[("active", TEXT)])

    # --- 进度条 / 滚动条 -----------------------------------------------
    _configure(style, "TProgressbar", troughcolor=SURFACE2,
               background=ACCENT, bordercolor=SURFACE2,
               lightcolor=ACCENT, darkcolor=ACCENT,
               thickness=6, borderwidth=0)
    _configure(style, "Bar.Horizontal.TProgressbar",
               troughcolor=SURFACE2, background=ACCENT,
               bordercolor=SURFACE2, lightcolor=ACCENT,
               darkcolor=ACCENT, thickness=6, borderwidth=0)
    _configure(style, "TScrollbar", background=SURFACE2, troughcolor=BG,
               bordercolor=BG, arrowcolor=MUTED, lightcolor=SURFACE2,
               darkcolor=SURFACE2, borderwidth=0)
    _map(style, "TScrollbar",
         background=[("active", LINE)],
         arrowcolor=[("active", ACCENT)])

    return {"display": FONT_DISPLAY, "body": FONT_BODY, "mono": FONT_MONO}


def hide_native_notebook_tabs(style) -> bool:
    """让原生页签栏彻底消失，改由自绘栏呈现。

    注意：只改 ``TNotebook`` 的 layout 不会隐藏 tab 栏——tab 是控件内部
    用 ``TNotebook.Tab`` 样式画的。必须把 ``TNotebook.Tab`` 的 layout 清空，
    实测 client 与 notebook 顶部的间距才会从 39px 降到 2px。
    ``nb.select()`` / ``<<NotebookTabChanged>>`` 仍然照常工作。
    """
    try:
        style.layout("TNotebook.Tab", [])
        try:
            style.layout("TNotebook", [("Notebook.client", {"sticky": "nswe"})])
        except Exception:
            pass
        return True
    except Exception:
        return False


# ---------------- 小组件 ----------------
def rule(parent, bg=None):
    """1px 编辑式规则线。"""
    import tkinter as tk
    f = tk.Frame(parent, height=1, bg=bg or LINE, highlightthickness=0)
    return f


def draw_logo(canvas, color=None, size=34, bg=None):
    """等距立方体线框：这个工具处理 3D 模型，标记也用 3D 的语言。"""
    color = color or ACCENT
    canvas.configure(width=size, height=size, bg=bg or BG,
                     highlightthickness=0)
    canvas.delete("all")
    s = size * 0.30
    w = s * 0.866
    cx = cy = size / 2.0
    p0 = (cx, cy - s)
    p1 = (cx + w, cy - s / 2)
    p2 = (cx + w, cy + s / 2)
    p3 = (cx, cy + s)
    p4 = (cx - w, cy + s / 2)
    p5 = (cx - w, cy - s / 2)
    outline = [p0, p1, p2, p3, p4, p5, p0]
    canvas.create_line(outline, fill=color, width=1.5)
    for p in (p0, p2, p4):
        canvas.create_line(cx, cy, p[0], p[1], fill=color, width=1.2)
    canvas.create_oval(cx - 1.6, cy - 1.6, cx + 1.6, cy + 1.6,
                       fill=color, outline="")


def make_status_dot(parent):
    """状态条左侧的小圆点，返回 (canvas, item_id)。"""
    import tkinter as tk
    c = tk.Canvas(parent, width=9, height=9, bg=SURFACE,
                  highlightthickness=0)
    dot = c.create_oval(1, 1, 8, 8, fill=OK, outline="")
    return c, dot
