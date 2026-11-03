# -*- coding: utf-8 -*-
"""
Sketchfab 模型下载器（桌面应用）· 支持批量并行

粘贴多行链接 → 并行下载 → 每个模型解密贴图 → 导出带材质的 .glb

三条命令行路径必须保持可用（回归底线）：
  --selftest   只建界面不显示，成功输出 SELFTEST_OK
  --env        打印依赖自检结果（纯 ASCII）
  --run        批量下载（无界面）

v1.1 新增：
  * 版本号可见（标题 / 副标题 / 关于面板 / --version / exe 属性，同源于 version.py）
  * 「关于 / 诊断」面板（依赖自检 + 更新日志 + 数据目录 + 手动检查更新）
  * 配置与程序分离（默认 %APPDATA%，程序目录内有 config.json 则为便携模式）
  * 资源目录前置进 PATH，让引擎的 shutil.which("node") 能找到随包 node.exe
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------- 路径 ----------------
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    RES = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    BASE = Path(sys.executable).parent
else:
    RES = Path(__file__).resolve().parent
    BASE = RES

# 同目录模块（version / diagnostics / update_checker）必须可 import：
# 源码直接运行时 sys.path[0] 已经是本目录，PyInstaller 打包后在 PYZ 里，
# 这里再兜一次，保证 `python app\sketchfab_gui.py` 与 exe 都能工作。
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import diagnostics as diag                                     # noqa: E402
import update_checker                                          # noqa: E402
from version import (APP_ID, APP_NAME, CHANGELOG_HEAD, SINGLE_FILE_EXPERIMENTAL,  # noqa: E402
                     VERSION_TUPLE, __version__)

CONVERT = RES / "convert.mjs"
EXTLIB = RES / "extlib"
ICON = RES / "icon.ico"
# 打赏收款码目录：打包后随包在 RES/assets/donate，源码运行时退回 app/assets/donate
DONATE_DIR = RES / "assets" / "donate"
if not DONATE_DIR.is_dir():
    DONATE_DIR = _APP_DIR / "assets" / "donate"


def _first_existing(paths, want_dir=False):
    paths = [p for p in paths if p]
    for p in paths:
        p = Path(p)
        if (p.is_dir() if want_dir else p.is_file()):
            return p
    return Path(paths[0])


def _env_path(name):
    """环境变量指向的路径；未设置返回 None（供 _first_existing 过滤）。"""
    value = os.environ.get(name)
    return Path(value) if value else None


def _which_node():
    for name in ("node", "node.exe", "nodejs"):
        hit = shutil.which(name)
        if hit:
            return Path(hit)
    return None


# 内核资源优先用随程序附带的（打包后解压在 RES 里），
# 找不到才退回仓库内的引擎副本 —— 这样别人拿到 exe 也能直接用。
# 开发机的私有路径一律不放进来，改为环境变量 SFD_CLI_DIR / SFD_NODE_EXE 覆盖。
CLI_DIR = _first_existing([
    RES / "engine",
    BASE / "engine",
    BASE.parent / "dist_engine",
    _env_path("SFD_CLI_DIR"),
    BASE.parent / "sketchfab-cli-main",
], want_dir=True)

NODE_EXE = _first_existing([
    RES / "node.exe",
    BASE / "node.exe",
    _env_path("SFD_NODE_EXE"),
    _which_node(),
    "node",
])


def prepend_to_path(directory) -> None:
    """
    把 ``directory`` 放到 ``PATH`` 最前面（幂等）。

    为什么必须做（v1.1 关键修复）：引擎用 ``shutil.which("node")`` 判断
    Node 是否可用，而打包后 ``node.exe`` 在 ``_internal\\`` 里、不在 PATH 上，
    PyInstaller 也不会把 ``_internal`` 加进 PATH，所以引擎历史上一直找不到
    Node，实际走的是 legacy ``binzDecrypt.exe``。把 RES 前置后引擎自己能找到，
    生产路径切到 WASM —— 这是行为变更，由 M6 六模型回归背书。
    """
    try:
        target = str(directory)
        if not target:
            return
        cur = os.environ.get("PATH", "")
        parts = [p for p in cur.split(os.pathsep) if p]
        if parts and os.path.normcase(parts[0]) == os.path.normcase(target):
            return
        parts = [p for p in parts if os.path.normcase(p) != os.path.normcase(target)]
        os.environ["PATH"] = target + (os.pathsep + os.pathsep.join(parts) if parts else "")
    except Exception:
        pass


def remove_from_path(directory) -> None:
    """把 ``directory`` 从 ``PATH`` 移除（回滚到 legacy 后端时用）。"""
    try:
        target = os.path.normcase(str(directory))
        cur = os.environ.get("PATH", "")
        parts = [p for p in cur.split(os.pathsep) if p]
        parts = [p for p in parts if os.path.normcase(p) != target]
        os.environ["PATH"] = os.pathsep.join(parts)
    except Exception:
        pass


# 模块级先前置一次：--env / 无界面路径也能得到修复后的 PATH
prepend_to_path(RES)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# 需要改写到 sketchfab.com/s/<code> 的短链域名（v1.1.1 修 TLS 握手失败）
SHORT_HOSTS = ("skfb.ly", "www.skfb.ly")

# 短链解析用的请求头与重试策略。
# 只加「像浏览器」的少量头：实测塞满 Sec-Fetch-*/sec-ch-ua/br 反而更容易被
# CloudFront WAF 挡成 202 challenge；Accept / Accept-Language 足够拿 301。
RESOLVE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}
RESOLVE_TIMEOUT = 20
RESOLVE_ATTEMPTS = 3
RETRY_DELAYS = (0.5, 1.2)


def default_out_dir():
    """
    挑一个「任何人电脑上都存在、而且一定可写」的目录：
    系统的下载文件夹，退而求其次用家目录。
    这样别人第一次打开就能直接下载，不用先去改路径。
    """
    cands = []
    home = Path.home()
    for name in ("Downloads", "下载", "下载文件"):
        d = home / name
        if d.is_dir():
            cands.append(d / "Sketchfab模型")
            break
    cands.append(home / "Sketchfab模型")
    return str(cands[0])


# ---------------- 配置（R7：配置与程序分离） ----------------
PORTABLE = "--portable" in sys.argv          # 便携模式：强制写程序目录
APPDATA_ROOT = diag.appdata_root()
CFG_WRITE, CFG_READ_FALLBACK = diag.resolve_cfg_paths(BASE, APPDATA_ROOT, PORTABLE)

DECRYPT_MODES = ("auto", "wasm", "legacy")

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

BG = "#f5f6f8"
CARD = "#ffffff"
ACCENT = "#2f6fed"
TEXT = "#1f2328"
MUTED = "#6b7280"
OK = "#15803d"
WARN = "#b45309"
ERR = "#b91c1c"

# 线程局部：给每个并行任务的日志打上标签
_tls = threading.local()
_cli_lock = threading.Lock()
_cli_mod = None


def _install_ssl_fix():
    """
    打包后的 OpenSSL（3.5.x）在读取 Windows 系统证书存储时，遇到个别无法解析
    的证书条目会整体抛 ASN1: NOT_ENOUGH_DATA，导致所有 https 请求失败。
    统一改用 certifi 自带的证书文件，既绕开这个问题，也不依赖对方的系统证书。
    """
    try:
        import ssl
        import certifi

        cacert = certifi.where()
        orig = ssl.create_default_context

        def patched(*a, **k):
            if a or k:                      # 调用方自己给了参数就尊重原样
                return orig(*a, **k)
            return orig(cafile=cacert)

        ssl.create_default_context = patched
        ssl._create_default_https_context = patched
        os.environ.setdefault("SSL_CERT_FILE", cacert)
    except Exception:
        pass


_install_ssl_fix()


def cfg_paths():
    """配置候选位置：(写目标, 读兜底)。保留函数名以兼容旧调用。"""
    return [CFG_WRITE, CFG_READ_FALLBACK]


def load_cfg():
    """
    读取配置：先读写目标，再兜底另一个位置（老版本遗留在程序目录的配置仍生效）。
    """
    for p in cfg_paths():
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def save_cfg(patch, base_cfg=None):
    """
    **合并式**保存：``save_cfg({"out_dir": ...})`` 只更新这一项。

    v1.0.0 的实现是整份覆盖 dict，会把 ``update_repo`` / ``decrypt_backend``
    等新键抹掉（R9 的前置缺陷），这里改为「读回 → merge → 只写一处」。
    返回合并后的完整配置，方便调用方同步内存副本。
    """
    merged = dict(base_cfg if isinstance(base_cfg, dict) else load_cfg())
    if isinstance(patch, dict):
        merged.update(patch)
    text = json.dumps(merged, ensure_ascii=False, indent=2)
    for p in [CFG_WRITE]:
        try:
            p = Path(p)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            return merged
        except Exception:
            continue
    return merged


def load_cli():
    """把下载引擎当模块导入（只导一次）"""
    global _cli_mod
    with _cli_lock:
        if _cli_mod is None:
            if str(CLI_DIR) not in sys.path:
                sys.path.insert(0, str(CLI_DIR))
            import main as cli  # noqa: E402
            _cli_mod = cli
        return _cli_mod


def normalize_decrypt_mode(value) -> str:
    """把配置值收敛到 auto / wasm / legacy 三者之一。"""
    mode = str(value or "auto").strip().lower()
    return mode if mode in DECRYPT_MODES else "auto"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self.last_dir = None
        self.total = 0
        self.finished = 0
        self.ok_count = 0
        self.fail_count = 0
        self.cfg = load_cfg()
        self.cfg_path = CFG_WRITE
        self.diag_items = []
        self.about = None                 # 关于/诊断 窗口（Toplevel）
        self.donate = None                # 打赏窗口（Toplevel）
        self._donate_imgs = []            # 图片引用，防止被 GC 回收
        self.about_state = {}             # 面板内的 Tk 变量与控件引用
        self.update_result = None

        root.title("Sketchfab 模型下载器 v" + __version__)
        root.geometry("860x700")
        root.minsize(760, 620)
        root.configure(bg=BG)
        try:
            if ICON.exists():
                root.iconbitmap(default=str(ICON))
        except Exception:
            pass

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT,
                        font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background=CARD, foreground=TEXT,
                        font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=TEXT,
                        font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Ver.TLabel", background=BG, foreground=ACCENT,
                        font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(14, 7))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 11, "bold"),
                        padding=(18, 10), background=ACCENT, foreground="white")
        style.map("Accent.TButton",
                  background=[("active", "#2559c9"), ("disabled", "#a9bdf0")],
                  foreground=[("disabled", "#eef2ff")])
        style.configure("TCheckbutton", background=CARD, foreground=TEXT,
                        font=("Microsoft YaHei UI", 10))
        style.configure("TEntry", padding=6)
        style.configure("Treeview", font=("Consolas", 9), rowheight=22)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

        head = ttk.Frame(root, style="TFrame")
        head.pack(fill="x", padx=20, pady=(16, 6))
        ttk.Label(head, text="Sketchfab 模型下载器", style="Title.TLabel").pack(anchor="w")
        subrow = ttk.Frame(head, style="TFrame")
        subrow.pack(fill="x", pady=(2, 0))
        ttk.Label(subrow,
                  text="支持一次粘贴多个链接，并行下载；每个模型自动解密贴图并导出 .glb",
                  style="Sub.TLabel").pack(side="left")
        # 副标题行右侧的版本号（M5 的第 2 处视觉位置）
        ttk.Label(subrow, text="v" + __version__, style="Ver.TLabel").pack(side="right")

        card = ttk.Frame(root, style="Card.TFrame")
        card.pack(fill="x", padx=20, pady=(6, 8))
        inner = ttk.Frame(card, style="Card.TFrame")
        inner.pack(fill="x", padx=16, pady=12)

        ttk.Label(inner, text="模型链接（一行一个，可粘贴多个）",
                  style="Card.TLabel").grid(row=0, column=0, columnspan=2,
                                            sticky="w", pady=(0, 4))
        self.url_text = tk.Text(inner, height=4, font=("Consolas", 9),
                                relief="solid", borderwidth=1, wrap="none",
                                bg="white", fg=TEXT, insertbackground=TEXT,
                                padx=8, pady=6)
        self.url_text.grid(row=1, column=0, columnspan=2, sticky="ew")

        ttk.Label(inner, text="保存到", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=(10, 4))
        # 上次的目录如果已经不在了（比如拔了 U 盘），自动换成可用的默认目录
        saved = self.cfg.get("out_dir") or ""
        if not saved or not Path(saved).parent.exists():
            saved = default_out_dir()
        self.out_var = tk.StringVar(value=saved)
        ttk.Entry(inner, textvariable=self.out_var, font=("Consolas", 10)).grid(
            row=3, column=0, sticky="ew", ipady=3)
        ttk.Button(inner, text="浏览…", command=self.pick_dir).grid(row=3, column=1, padx=(8, 0))

        opts = ttk.Frame(inner, style="Card.TFrame")
        opts.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.full_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="导出完整材质贴图的 .glb（推荐）",
                        variable=self.full_var).pack(side="left")
        ttk.Label(opts, text="    同时下载数", style="Card.TLabel").pack(side="left")
        self.conc_var = tk.IntVar(value=int(self.cfg.get("concurrency", 3)))
        ttk.Spinbox(opts, from_=1, to=5, width=4, textvariable=self.conc_var,
                    font=("Microsoft YaHei UI", 9)).pack(side="left", padx=6)
        ttk.Label(opts, text="（建议 2-3，太多会抢带宽）",
                  style="Card.TLabel", foreground=MUTED).pack(side="left")

        inner.columnconfigure(0, weight=1)

        bar = ttk.Frame(root, style="TFrame")
        bar.pack(fill="x", padx=20, pady=(2, 6))
        self.btn = ttk.Button(bar, text="开始下载", style="Accent.TButton", command=self.start)
        self.btn.pack(side="left")
        self.open_btn = ttk.Button(bar, text="打开输出目录", command=self.open_out, state="disabled")
        self.open_btn.pack(side="left", padx=8)
        ttk.Button(bar, text="清空日志", command=self.clear_log).pack(side="right")
        ttk.Button(bar, text="清空链接", command=self.clear_urls).pack(side="right", padx=8)
        self.about_btn = ttk.Button(bar, text="关于 / 诊断", command=self.show_about)
        self.about_btn.pack(side="right", padx=8)
        ttk.Button(bar, text="♡ 打赏", command=self.show_donate).pack(side="right")

        self.pbar = ttk.Progressbar(root, mode="determinate", maximum=100)
        self.pbar.pack(fill="x", padx=20, pady=(0, 6))

        logwrap = ttk.Frame(root, style="Card.TFrame")
        logwrap.pack(fill="both", expand=True, padx=20, pady=(0, 6))
        self.log = tk.Text(logwrap, wrap="word", bg="#0f172a", fg="#d7e0ef",
                           insertbackground="#d7e0ef", relief="flat",
                           font=("Consolas", 9), padx=12, pady=10, state="disabled")
        sb = ttk.Scrollbar(logwrap, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.status = ttk.Label(root, text="就绪", style="Sub.TLabel")
        self.status.pack(fill="x", padx=22, pady=(0, 10))

        # 按配置决定引擎走哪条解密后端（legacy = 把 RES 从 PATH 剔除）
        self.decrypt_mode = self.apply_node_visibility(self.cfg.get("decrypt_backend"))

        root.after(120, self.drain)
        self.url_text.focus_set()
        self.precheck()

    # ---------------- 基础 ----------------
    def logline(self, text, color=None):
        self.log.configure(state="normal")
        if color:
            tag = "c" + color.replace("#", "")
            self.log.tag_configure(tag, foreground=color)
            self.log.insert("end", text + "\n", tag)
        else:
            self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def clear_urls(self):
        self.url_text.delete("1.0", "end")

    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_var.get() or default_out_dir())
        if d:
            self.out_var.set(d.replace("/", "\\"))

    def open_out(self):
        p = self.last_dir or self.out_var.get()
        if p and Path(p).exists():
            webbrowser.open(Path(p).as_uri())
        else:
            messagebox.showinfo("提示", "目录还不存在")

    def save_patch(self, patch):
        """合并式保存并同步内存副本。"""
        self.cfg = save_cfg(patch, self.cfg)
        return self.cfg

    def apply_node_visibility(self, mode=None):
        """
        按 ``decrypt_backend`` 调整 PATH，返回实际生效的模式。

        * ``auto`` / ``wasm``：RES 在 PATH 最前 → 引擎 ``which("node")`` 成功 → WASM
        * ``legacy``：把 RES 从 PATH 剔除 → 引擎找不到 node → 回落 binzDecrypt.exe

        不改引擎一行代码就实现了「一键回滚」。
        """
        mode = normalize_decrypt_mode(mode if mode is not None
                                      else self.cfg.get("decrypt_backend"))
        if mode == "legacy":
            remove_from_path(RES)
        else:
            prepend_to_path(RES)
        return mode

    def precheck(self):
        """启动自检：复用 diagnostics.collect()，缺依赖时给人话指引（R11）。"""
        try:
            self.diag_items = diag.collect(RES, BASE, CLI_DIR, NODE_EXE)
        except Exception as e:
            self.logline("⚠ 自检失败: %s" % e, WARN)
            return

        bad = diag.problems(self.diag_items)
        if bad:
            self.logline("⚠ 启动自检发现问题：", WARN)
            for it in bad:
                self.logline("   - %s：%s" % (it.label, it.hint or it.path), WARN)
            self.logline("   点工具栏「关于 / 诊断」查看明细与解决办法。", WARN)
            self.status.configure(text="⚠ 发现 %d 项问题，点「关于 / 诊断」查看" % len(bad))
        else:
            backend = diag.decrypt_backend(RES, CLI_DIR)
            self.logline("✓ 环境自检通过，可以开始下载（解密后端 %s）" % backend, OK)
            self.status.configure(text="就绪 · v%s · 解密后端 %s" % (__version__, backend))

    # ---------------- 关于 / 诊断（R2/R3/R11，T03 接口） ----------------
    # ---------------- 打赏 ----------------
    def show_donate(self):
        """打开打赏窗口：并排显示微信 / 支付宝收款码。"""
        if self.donate is not None:
            try:
                if self.donate.winfo_exists():
                    self.donate.deiconify()
                    self.donate.lift()
                    self.donate.focus_force()
                    return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        self.donate = win
        win.title("打赏 · %s" % APP_NAME)
        win.configure(background=BG)
        win.resizable(False, False)
        win.transient(self.root)

        head = ttk.Frame(win, style="TFrame")
        head.pack(fill="x", padx=24, pady=(18, 4))
        ttk.Label(head, text="请作者喝杯咖啡 ☕", style="Title.TLabel").pack(anchor="w")
        ttk.Label(head, text="这个工具完全免费。如果它帮你省了时间，欢迎打赏支持，金额随意。",
                  style="Sub.TLabel").pack(anchor="w", pady=(4, 0))

        row = ttk.Frame(win, style="TFrame")
        row.pack(fill="x", padx=24, pady=(10, 4))

        self._donate_imgs = []
        rendered = 0
        for title, fname, color in (("微信打赏", "wechat.png", "#07c160"),
                                    ("支付宝", "alipay.png", "#1677ff")):
            img_path = DONATE_DIR / fname
            if not img_path.is_file():
                # 没有配这个渠道的收款码就整栏跳过，不显示占位/缺图提示
                continue
            try:
                photo = tk.PhotoImage(file=str(img_path))
            except Exception:
                continue
            # 收款码统一缩到 240px 宽展示（整数倍缩小，保证不模糊）
            if photo.width() > 240:
                factor = max(1, photo.width() // 240)
                photo = photo.subsample(factor, factor)
            self._donate_imgs.append(photo)

            card = ttk.Frame(row, style="Card.TFrame", padding=12)
            card.pack(side="left", padx=(0, 14), fill="both", expand=True)
            ttk.Label(card, text=title, style="Card.TLabel",
                      font=("Microsoft YaHei UI", 11, "bold"),
                      foreground=color).pack()
            tk.Label(card, image=photo, background=CARD).pack(pady=(8, 4))
            ttk.Label(card, text="扫码即可，感谢支持 ♥", style="Card.TLabel",
                      foreground=MUTED).pack(pady=(2, 4))
            rendered += 1

        if not rendered:
            ttk.Label(row, text="暂未配置收款码。", style="Sub.TLabel").pack(pady=20)

        foot = ttk.Frame(win, style="TFrame")
        foot.pack(fill="x", padx=24, pady=(2, 14))
        ttk.Label(foot, text="打赏完全自愿，不影响任何功能。", style="Sub.TLabel").pack(side="left")
        ttk.Button(foot, text="关闭", command=win.destroy).pack(side="right")

        # 居中于主窗口
        win.update_idletasks()
        try:
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            w, h = win.winfo_width(), win.winfo_height()
            win.geometry("+%d+%d" % (rx + (rw - w) // 2, ry + max(20, (rh - h) // 3)))
        except Exception:
            pass

    def show_about(self):
        """打开关于/诊断面板（已打开则提到最前）。"""
        if self.about is not None:
            try:
                if self.about.winfo_exists():
                    self.about.deiconify()
                    self.about.lift()
                    self.about.focus_force()
                    return
            except Exception:
                pass
            self.about = None

        win = tk.Toplevel(self.root)
        self.about = win
        win.title("关于 / 诊断 · %s v%s" % (APP_NAME, __version__))
        win.configure(bg=BG)
        win.geometry("760x680")
        win.minsize(680, 560)
        try:
            if ICON.exists():
                win.iconbitmap(default=str(ICON))
        except Exception:
            pass

        info = diag.load_build_info(RES)
        changelog_path = RES / "CHANGELOG.md"
        head_items = diag.read_changelog_head(changelog_path, CHANGELOG_HEAD)

        # --- 头部：版本 / 构建日期 -------------------------------------
        top = ttk.Frame(win, style="TFrame")
        top.pack(fill="x", padx=18, pady=(14, 6))
        ttk.Label(top, text="%s v%s" % (APP_NAME, __version__),
                  style="Title.TLabel").pack(anchor="w")
        ttk.Label(top, text="构建日期：%s" % info.get("build_date", "开发版"),
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 0))

        # --- 数据目录 / 配置路径 / 解密后端 -----------------------------
        paths = ttk.Frame(win, style="Card.TFrame")
        paths.pack(fill="x", padx=18, pady=(4, 8))
        pinner = ttk.Frame(paths, style="Card.TFrame")
        pinner.pack(fill="x", padx=12, pady=10)

        def _row(r, label, value):
            ttk.Label(pinner, text=label, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", pady=1)
            lbl = ttk.Label(pinner, text=value, style="Card.TLabel", foreground=MUTED)
            lbl.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=1)
            return lbl

        _row(0, "程序目录", str(BASE))
        _row(1, "数据目录", str(APPDATA_ROOT / APP_NAME))
        _row(2, "配置文件", "%s%s" % (self.cfg_path,
                                      "（便携模式）" if Path(self.cfg_path) == Path(CFG_WRITE)
                                      and CFG_WRITE == BASE / "config.json" else ""))
        _row(3, "解密后端", "%s（配置值：%s）" % (
            diag.decrypt_backend(RES, CLI_DIR),
            normalize_decrypt_mode(self.cfg.get("decrypt_backend"))))
        _row(4, "发布形态", "文件夹版（onedir）" if not SINGLE_FILE_EXPERIMENTAL
             else "单文件版（实验性）")

        # --- 更新日志 ---------------------------------------------------
        ttk.Label(win, text="更新日志（前 %d 条，离线读取）" % CHANGELOG_HEAD,
                  style="Sub.TLabel").pack(anchor="w", padx=20)
        clogwrap = ttk.Frame(win, style="Card.TFrame")
        clogwrap.pack(fill="x", padx=18, pady=(2, 8))
        clog = tk.Text(clogwrap, height=7, wrap="word", bg="#0f172a", fg="#d7e0ef",
                       relief="flat", font=("Microsoft YaHei UI", 9),
                       padx=10, pady=8, state="normal")
        csb = ttk.Scrollbar(clogwrap, command=clog.yview)
        clog.configure(yscrollcommand=csb.set)
        clog.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        if head_items:
            clog.insert("end", "\n".join("· " + x for x in head_items))
        else:
            clog.insert("end", "（未找到内置 CHANGELOG.md，可能是开发版运行）")
        clog.configure(state="disabled")

        # --- 依赖自检表格 -----------------------------------------------
        ttk.Label(win, text="依赖自检（与 --env 同一份实现）",
                  style="Sub.TLabel").pack(anchor="w", padx=20)
        treewrap = ttk.Frame(win, style="TFrame")
        treewrap.pack(fill="both", expand=True, padx=18, pady=(2, 6))
        tree = ttk.Treeview(treewrap, columns=("item", "state", "path"),
                            show="headings", height=8)
        tree.heading("item", text="项")
        tree.heading("state", text="状态")
        tree.heading("path", text="路径 / 值")
        tree.column("item", width=190, anchor="w", stretch=False)
        tree.column("state", width=80, anchor="center", stretch=False)
        tree.column("path", width=420, anchor="w", stretch=True)
        tsb = ttk.Scrollbar(treewrap, command=tree.yview)
        tree.configure(yscrollcommand=tsb.set)
        tree.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        tree.tag_configure("ok", foreground=OK)
        tree.tag_configure("bad", foreground=ERR)

        items = self.diag_items or diag.collect(RES, BASE, CLI_DIR, NODE_EXE)
        self.diag_items = items
        for it in items:
            hint = it.hint if not it.ok and it.hint else ""
            tree.insert("", "end",
                        values=(it.label, it.status_text(), hint or it.path),
                        tags=("ok" if it.ok else "bad",))

        # --- 更新源设置 + 检查更新 --------------------------------------
        updframe = ttk.Frame(win, style="Card.TFrame")
        updframe.pack(fill="x", padx=18, pady=(0, 6))
        uinner = ttk.Frame(updframe, style="Card.TFrame")
        uinner.pack(fill="x", padx=12, pady=10)

        ttk.Label(uinner, text="更新源（GitHub 仓库 owner/name，留空则不联网）",
                  style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.about_state["repo_var"] = tk.StringVar(
            value=str(self.cfg.get("update_repo") or ""))
        ttk.Entry(uinner, textvariable=self.about_state["repo_var"],
                  font=("Consolas", 9)).grid(row=1, column=0, sticky="ew", pady=(2, 6))

        self.about_state["adv_var"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(uinner, text="私有仓库才需要：填写访问令牌（PAT）",
                        variable=self.about_state["adv_var"],
                        command=lambda: self._toggle_token_row(uinner)
                        ).grid(row=2, column=0, sticky="w")
        self.about_state["token_row"] = ttk.Frame(uinner, style="Card.TFrame")
        self.about_state["token_row"].grid(row=3, column=0, sticky="ew", pady=(2, 0))
        self.about_state["token_var"] = tk.StringVar(
            value=str(self.cfg.get("github_token") or ""))
        ttk.Entry(self.about_state["token_row"],
                  textvariable=self.about_state["token_var"],
                  font=("Consolas", 9), show="•").pack(side="left", fill="x", expand=True)
        ttk.Label(self.about_state["token_row"],
                  text=" 只勾 Contents: Read-only", style="Card.TLabel",
                  foreground=MUTED).pack(side="left")
        self.about_state["token_row"].grid_remove()

        self.about_state["json_var"] = tk.StringVar(
            value=str(self.cfg.get("update_json_url") or ""))
        ttk.Label(uinner, text="或：静态 version.json 直链（网盘/任意 HTTP）",
                  style="Card.TLabel").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(uinner, textvariable=self.about_state["json_var"],
                  font=("Consolas", 9)).grid(row=5, column=0, sticky="ew", pady=(2, 0))
        uinner.columnconfigure(0, weight=1)

        self.about_state["upd_var"] = tk.StringVar(
            value="当前版本 v%s" % __version__)
        ttk.Label(win, textvariable=self.about_state["upd_var"],
                  style="Sub.TLabel").pack(anchor="w", padx=20, pady=(0, 4))

        # --- 底部按钮 ---------------------------------------------------
        btns = ttk.Frame(win, style="TFrame")
        btns.pack(fill="x", padx=18, pady=(0, 12))
        ttk.Button(btns, text="保存更新源", command=self._save_update_source).pack(side="left")
        ttk.Button(btns, text="检查更新", command=self.check_update_clicked).pack(
            side="left", padx=8)
        self.about_state["dl_btn"] = ttk.Button(
            btns, text="打开发布页", command=self._open_release_page, state="disabled")
        self.about_state["dl_btn"].pack(side="left")
        ttk.Button(btns, text="关闭", command=self._close_about).pack(side="right")
        ttk.Button(btns, text="打开数据目录", command=self._open_data_dir).pack(
            side="right", padx=8)

        # 若已配置更新源，先把上次结果清空（每次打开都从「当前版本」开始）
        if self.update_result is not None and self.update_result.ok():
            self._render_update_result(self.update_result)

    def _toggle_token_row(self, parent):
        """展开/收起 PAT 输入行（默认折叠，避免误导普通用户）。"""
        row = self.about_state.get("token_row")
        if row is None:
            return
        try:
            if self.about_state["adv_var"].get():
                row.grid()
            else:
                row.grid_remove()
        except Exception:
            pass

    def _close_about(self):
        if self.about is not None:
            try:
                self.about.destroy()
            except Exception:
                pass
        self.about = None
        self.about_state = {}

    def _open_data_dir(self):
        d = APPDATA_ROOT / APP_NAME
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            d = BASE
        try:
            webbrowser.open(Path(d).as_uri())
        except Exception:
            messagebox.showinfo("数据目录", str(d))

    def _open_release_page(self):
        url = ""
        if self.update_result is not None:
            url = self.update_result.url or ""
        if url:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    def _save_update_source(self):
        repo = str(self.about_state.get("repo_var").get() if self.about_state else "").strip()
        token = str(self.about_state.get("token_var").get() if self.about_state else "").strip()
        json_url = str(self.about_state.get("json_var").get() if self.about_state else "").strip()
        if repo and not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo):
            messagebox.showwarning("格式不对", "更新源请填成 owner/name 的形式，例如 yourname/SketchfabDownloader")
            return
        self.save_patch({"update_repo": repo, "github_token": token,
                         "update_json_url": json_url})
        msg = "已保存。当前不会自动联网，只有点「检查更新」才发一次请求。"
        if not repo and not json_url:
            msg = "已保存：更新源为空 = 不联网（默认状态）。"
        if self.about_state.get("upd_var") is not None:
            self.about_state["upd_var"].set(msg)
        self.logline("· 更新源已保存（repo=%r, json_url=%r）" % (repo, json_url), MUTED)

    def check_update_clicked(self):
        """
        手动检查更新（R9）。未配置时**不发任何网络请求**，只提示未配置。
        """
        cfg = dict(self.cfg)
        if not str(cfg.get("update_repo") or "").strip() and \
                not str(cfg.get("update_json_url") or "").strip():
            text = "未配置更新源（不影响使用）"
            if self.about_state.get("upd_var") is not None:
                self.about_state["upd_var"].set(text)
            self.logline("· " + text, MUTED)
            return

        if self.about_state.get("upd_var") is not None:
            self.about_state["upd_var"].set("检查中…（最多 5 秒，失败不影响使用）")

        def on_done(result):
            # 工作线程：只往队列里丢，绝不碰 Tk
            self.q.put(("update_done", result))

        try:
            update_checker.check_async(cfg, on_done, timeout=5.0)
        except Exception as e:            # check_async 自己不该抛，兜一层
            self.q.put(("update_done",
                        update_checker.UpdateResult(current=__version__,
                                                    error="%s: %s" % (type(e).__name__, e))))

    def _render_update_result(self, result):
        """把 UpdateResult 渲染到面板结果行（主线程调用）。"""
        self.update_result = result
        if not result.ok():
            text = "暂无法检查（离线可忽略）：%s" % result.error
            if result.error.startswith("未配置"):
                text = "未配置更新源（不影响使用）"
            if self.about_state.get("upd_var") is not None:
                self.about_state["upd_var"].set(text)
            self.logline("· " + text, MUTED)
            return

        if result.is_newer:
            text = "发现新版本 v%s（当前 v%s）" % (result.latest, result.current)
            btn = self.about_state.get("dl_btn")
            if btn is not None and result.url:
                try:
                    btn.configure(state="normal")
                except Exception:
                    pass
        else:
            text = "已是最新 v%s" % result.current
        if result.published_at:
            text += "｜发布于 %s" % result.published_at
        if self.about_state.get("upd_var") is not None:
            self.about_state["upd_var"].set(text)
        self.logline("· " + text, OK if not result.is_newer else WARN)

    # ---------------- 启动批量 ----------------
    def start(self):
        if self.busy:
            return
        raw = self.url_text.get("1.0", "end").strip()
        links = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not links:
            messagebox.showwarning("提示", "请先粘贴模型链接（一行一个）")
            return

        # 去重（按 32 位 ID 或整行）
        seen, uniq = set(), []
        for ln in links:
            key = (re.search(r"([0-9a-f]{32})", ln) or [None, ln])[1]
            if key in seen:
                continue
            seen.add(key)
            uniq.append(ln)
        if len(uniq) < len(links):
            self.logline(f"⚠ 已自动去掉 {len(links) - len(uniq)} 个重复链接", WARN)

        out = self.out_var.get().strip().strip('"')
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            fallback = default_out_dir()
            if not messagebox.askyesno(
                    "目录不可用",
                    f"没法写入这个位置：\n{out}\n（{e}）\n\n"
                    f"改用默认目录吗？\n{fallback}"):
                return
            out = fallback
            try:
                Path(out).mkdir(parents=True, exist_ok=True)
            except Exception as e2:
                messagebox.showerror("错误", f"默认目录也无法创建：{e2}")
                return
            self.out_var.set(out)

        conc = max(1, min(5, int(self.conc_var.get() or 1)))
        full = bool(self.full_var.get())          # 主线程读一次，避免工作线程碰 Tk 变量
        self.save_patch({"out_dir": out, "concurrency": conc})
        # 每次开跑前按当前配置重置一次 PATH 可见性（用户可能在面板里改过后端）
        self.decrypt_mode = self.apply_node_visibility(self.cfg.get("decrypt_backend"))

        self.busy = True
        self.total = len(uniq)
        self.finished = 0
        self.ok_count = 0
        self.fail_count = 0
        self.pbar.configure(value=0, maximum=100)
        self.btn.configure(state="disabled", text="下载中…")
        self.open_btn.configure(state="disabled")
        self.status.configure(text=f"0 / {self.total} 完成（并发 {conc}）")
        self.logline("")
        self.logline("─" * 66, "#4b5563")
        self.logline(f"开始批量下载：{self.total} 个模型，并发 {conc}", None)

        threading.Thread(target=self.run_batch, args=(uniq, out, conc, full),
                         daemon=True).start()

    def run_batch(self, links, out, conc, full=True):
        # 进度回调是全局的，用线程局部变量区分是哪个任务的输出
        try:
            cli = load_cli()
            cli.set_progress_callback(
                lambda m: self.q.put(("log", (f"{getattr(_tls, 'tag', '?')} {m}", None)))
            )
        except Exception as e:
            self.q.put(("log", (f"⚠ 下载引擎加载失败: {e}", ERR)))

        def task(idx, url):
            _tls.tag = f"[{idx}]"

            def log(text, color=None):
                self.q.put(("log", (f"{_tls.tag} {text}", color)))

            try:
                uid, _ = resolve_and_uid(url, log)
                try:
                    nm = fetch_info(uid).get("name", "")
                    _tls.tag = f"[{idx} {nm[:14]}]"
                except Exception:
                    pass
                name, summary, folder = download_task(url, out, full, log)
                self.q.put(("task_done", (idx, True, name, summary, str(folder))))
            except Exception as e:
                self.q.put(("task_done", (idx, False, url, str(e), None)))

        with ThreadPoolExecutor(max_workers=conc) as pool:
            list(pool.map(lambda a: task(a[0], a[1]), list(enumerate(links, start=1))))

        self.q.put(("batch_done", None))

    # ---------------- 队列 ----------------
    def drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    text, color = payload
                    if color is None and ("失败" in text or text.strip().startswith("⚠")):
                        color = ERR
                    self.logline(text, color)
                elif kind == "task_done":
                    idx, ok, name, detail, folder = payload
                    self.finished += 1
                    if ok:
                        self.ok_count += 1
                        self.last_dir = folder
                        self.logline(f"✅ [{idx}] {name} → {detail}", OK)
                    else:
                        self.fail_count += 1
                        self.logline(f"❌ [{idx}] {name} → {detail}", ERR)
                    pct = self.finished / max(1, self.total) * 100
                    self.pbar.configure(value=pct)
                    self.status.configure(
                        text=f"{self.finished} / {self.total} 完成（成功 {self.ok_count}，失败 {self.fail_count}）")
                elif kind == "update_done":
                    self._render_update_result(payload)
                elif kind == "batch_done":
                    self.finish()
        except queue.Empty:
            pass
        self.root.after(120, self.drain)

    def finish(self):
        self.busy = False
        self.btn.configure(state="normal", text="开始下载")
        self.open_btn.configure(state="normal" if self.ok_count else "disabled")
        self.status.configure(
            text=f"全部完成：成功 {self.ok_count}，失败 {self.fail_count}")
        self.logline("")
        self.logline("─" * 66, "#4b5563")
        self.logline(f"批量结束：成功 {self.ok_count} 个，失败 {self.fail_count} 个", OK if not self.fail_count else WARN)


# ---------------- 网络 ----------------
def run_stream_cmd(cmd, cwd, log):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", env=env, creationflags=NO_WINDOW)
    for line in p.stdout:
        line = line.rstrip()
        if line:
            log(line, None)
    p.wait()
    return p.returncode


def download_task(url, out, full=True, log=lambda t, c=None: None):
    """下载单个模型并导出 GLB。界面与命令行模式共用，返回 (名称, 摘要, 目录)"""
    uid, final_url = resolve_and_uid(url, log)
    info = fetch_info(uid)
    name = info.get("name", "未知模型")
    faces = info.get("faceCount") or 0
    lic = (info.get("license") or {}).get("label", "未知")
    open_dl = bool(info.get("isDownloadable"))
    log(f"面数 {faces:,}｜{lic}｜官方下载 {'是' if open_dl else '否'}", None)
    if not open_dl:
        log("注意：作者未开放下载，仅供个人学习参考", WARN)

    cli = load_cli()
    res = cli.download_one(final_url, out_root=Path(out))
    folder = Path(res["dir"])

    summary = "（仅几何体）"
    if full:
        out_glb = folder / f"{folder.name.rsplit('-', 1)[0]}_full.glb"
        cmd = [str(NODE_EXE), str(CONVERT), uid, str(folder),
               str(out_glb), str(EXTLIB)]
        rc = run_stream_cmd(cmd, RES, log)
        if rc == 0 and out_glb.exists():
            summary = f"{out_glb.name}（{out_glb.stat().st_size / 1024 / 1024:.1f} MB）"
        else:
            summary = "GLB 未生成，可用 .gltf"
    return name, summary, folder


def normalize_url(url: str) -> str:
    """补全 scheme / 放行裸 32 位 ID。返回可直接请求的绝对 URL。"""
    if not re.match(r"^https?://", url):
        return "https://" + url
    return url


def rewrite_short_link(url: str) -> str:
    """
    把 ``skfb.ly/<code>`` 改写成 ``sketchfab.com/s/<code>``（v1.1.1）。

    为什么必须改：打包后的 OpenSSL 访问 ``skfb.ly`` 时 TLS 握手稳定失败
    （``SSL: UNEXPECTED_EOF_WHILE_READING``，QA 实测 5/5 必现），而同一个 exe
    访问 ``sketchfab.com`` 完全正常。``/s/<code>`` 是官方短链落地页，会 302 到
    ``/3d-models/<slug>-<uid>``，语义等价且省掉一次跨域握手。

    实现约束（别改）：
      * **只替换域名与路径前缀**，``code`` 原样保留 —— 短 code 区分大小写，
        转成小写会 404
      * 查询串原样带过去（有些分享链接带 ``?utm_*``）
      * 不是短链就原样返回，绝不改动其它网址
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if host not in SHORT_HOSTS:
        return url
    code = parts.path.lstrip("/")
    if not code:
        return url
    return urllib.parse.urlunsplit(("https", "sketchfab.com", "/s/" + code,
                                    parts.query, ""))


def _fetch_final_url(url: str):
    """跟随跳转，返回 ``(HTTP 状态码, 最终 URL)``。"""
    req = urllib.request.Request(url, headers=RESOLVE_HEADERS)
    with urllib.request.urlopen(req, timeout=RESOLVE_TIMEOUT) as r:
        return int(getattr(r, "status", 200) or 200), r.geturl()


def _unshorten_via_service(short_url: str, log=None):
    """第三方短链还原（unshorten.me 免费接口）→ 成功返回目标 URL，失败返回 None。

    背景：2026-11 起 Sketchfab 对 skfb.ly 与 sketchfab.com/s/ 全量上了 AWS WAF
    JS 挑战（HTTP 202 + x-amzn-waf-action: challenge），纯 HTTP 客户端无法通过。
    第三方还原服务在自己侧用真实浏览器解开跳转，可绕开这一限制。
    """
    try:
        import json as _json
        req = urllib.request.Request(
            "https://unshorten.me/json/" + urllib.parse.quote(short_url, safe=""),
            headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=RESOLVE_TIMEOUT) as r:
            data = _json.loads(r.read().decode("utf-8", "replace"))
        if data.get("success") and data.get("resolved_url"):
            if log:
                log("第三方还原成功 → %s" % data["resolved_url"], None)
            return data["resolved_url"]
    except Exception as e:
        if log:
            log("第三方还原不可用：%s" % e, WARN)
    return None


def resolve_and_uid(url: str, log=None):
    """
    链接 → (32 位 UID, 最终 URL)。

    v1.1.1 起短链走「先改写、失败再回退」两条路：
      1. ``skfb.ly/<code>`` → ``sketchfab.com/s/<code>``（绕开 skfb.ly 的 TLS 问题）
      2. 改写后拿不到 UID（非 2xx / 跳转没 ID / 网络异常）→ 再用原始 skfb.ly 试一次
      3. 两条都失败才报错，并明确告诉用户「改粘完整链接」

    另外加了**同路径重试**：Sketchfab 前置的 CloudFront WAF 偶尔会回 202
    ``x-amzn-waf-action: challenge``（确认不了就不是跳转），实测重试一两次即可通过，
    所以每条候选路径最多试 :data:`RESOLVE_ATTEMPTS` 次。
    """
    raw = (url or "").strip()
    if not raw:
        raise RuntimeError("链接为空")
    if re.fullmatch(r"[0-9a-f]{32}", raw):
        return raw, f"https://sketchfab.com/3d-models/{raw}"

    original = normalize_url(raw)
    primary = rewrite_short_link(original)
    is_short = primary != original
    if is_short and log:
        log("短链 %s → %s（避开 skfb.ly 的 TLS 问题）" % (original, primary), None)

    candidates = [primary] if not is_short else [primary, original]
    problems = []
    for cand in candidates:
        for attempt in range(1, RESOLVE_ATTEMPTS + 1):
            try:
                status, final = _fetch_final_url(cand)
            except Exception as e:
                problems.append("%s → %s: %s" % (cand, type(e).__name__, e))
                if attempt < RESOLVE_ATTEMPTS:
                    time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])
                continue

            m = re.search(r"([0-9a-f]{32})", final)
            if m:
                return m.group(1), final

            # 没有 UID：202 = WAF 挑战（非跳转），其它状态码多半是被拦/落地页改版
            problems.append("%s → HTTP %s，未跳到 3d-models（无 32 位 ID）" % (cand, status))
            if attempt < RESOLVE_ATTEMPTS:
                if log:
                    log("解析被站点防护拦下（HTTP %s），%.1f 秒后重试 %d/%d" %
                        (status, RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)],
                         attempt + 1, RESOLVE_ATTEMPTS), WARN)
                time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

    detail = "；".join(problems[-2:])[:300]
    if is_short:
        # Sketchfab 已对短链启用全量 WAF 挑战（202），直连两条路都过不去，
        # 最后试一次第三方还原服务；成功则从目标 URL 里取 UID。
        resolved = _unshorten_via_service(original, log=log)
        if resolved:
            m = re.search(r"([0-9a-f]{32})", resolved)
            if m:
                return m.group(1), resolved
        raise RuntimeError(
            "短链解析失败：Sketchfab 的反爬防护（WAF）目前拦截了 skfb.ly 短链的自动解析。"
            "请改粘浏览器地址栏里的完整链接（sketchfab.com/3d-models/...）。%s" % detail)
    raise RuntimeError("链接里没有找到模型 ID：%s" % detail)


def fetch_info(uid: str):
    req = urllib.request.Request(f"https://api.sketchfab.com/v3/models/{uid}",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def cli_mode(argv):
    """无界面模式：--run <链接>[,<链接>] [--out 目录] [--conc N] [--no-full]"""
    try:
        sys.stdout.reconfigure(errors="replace")   # 兼容 GBK 控制台，别崩
    except Exception:
        pass

    links, out, full, conc = [], default_out_dir(), True, 2
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--run", "--links") and i + 1 < len(argv):
            links = [x for x in re.split(r"[,;\s]+", argv[i + 1]) if x]
            i += 1
        elif a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
            i += 1
        elif a == "--conc" and i + 1 < len(argv):
            try:
                conc = max(1, min(5, int(argv[i + 1])))
            except ValueError:
                pass
            i += 1
        elif a == "--no-full":
            full = False
        i += 1

    if not links:
        print("用法: Sketchfab下载器.exe --run <链接>[,<链接>...] "
              "[--out 目录] [--conc N] [--no-full]")
        return 2

    Path(out).mkdir(parents=True, exist_ok=True)
    print(f"输出目录: {out}")
    print(f"并发: {conc}  模型数: {len(links)}  导出GLB: {'是' if full else '否'}", flush=True)

    try:
        cli = load_cli()
        cli.set_progress_callback(
            lambda m: print(f"{getattr(_tls, 'tag', '?')} {m}", flush=True))
    except Exception as e:
        print(f"引擎加载失败: {e}", flush=True)
        return 1

    fails = [0]

    def task(idx, url):
        tag = f"[{idx}]"
        _tls.tag = tag

        def log(t, c=None):
            print(f"{tag} {t}", flush=True)

        try:
            _tls.tag = f"[{idx} {url[-8:]}]"   # 拿到模型名之前先显示链接尾巴
            name, summary, folder = download_task(url, out, full, log)
            print(f"{tag} ✅ {name} → {summary}", flush=True)
            print(f"{tag}    目录: {folder}", flush=True)
        except Exception as e:
            fails[0] += 1
            print(f"{tag} ❌ {type(e).__name__}: {e}", flush=True)

    with ThreadPoolExecutor(max_workers=conc) as pool:
        list(pool.map(lambda a: task(a[0], a[1]), list(enumerate(links, start=1))))

    ok = len(links) - fails[0]
    print(f"全部结束：成功 {ok}，失败 {fails[0]}", flush=True)
    return 0 if fails[0] == 0 else 1


def print_env() -> int:
    """``--env``：打印依赖自检（纯 ASCII，GBK 控制台安全）。"""
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    items = diag.collect(RES, BASE, CLI_DIR, NODE_EXE)
    print(diag.format_ascii(items))
    return 0


def print_version(as_json: bool = False) -> int:
    """``--version`` / ``--version --json``（M5 的第 4 处输出）。"""
    info = diag.load_build_info(RES)
    if as_json:
        payload = {
            "app": APP_ID,
            "name": APP_NAME,
            "version": __version__,
            "version_tuple": list(VERSION_TUPLE),
            "build_date": info.get("build_date", "开发版"),
            "python": info.get("python", ""),
            "pyinstaller": info.get("pyinstaller", ""),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("%s v%s (build %s)" % (APP_NAME, __version__,
                                     info.get("build_date", "开发版")))
    return 0


def main():
    try:
        if "--version" in sys.argv:
            raise SystemExit(print_version("--json" in sys.argv))
        if "--env" in sys.argv:
            raise SystemExit(print_env())
        if "--run" in sys.argv or "--links" in sys.argv:
            raise SystemExit(cli_mode(sys.argv[1:]))
        selftest = "--selftest" in sys.argv
        root = tk.Tk()
        App(root)
        if selftest:
            root.update()
            print("SELFTEST_OK")
            root.destroy()
            return
        root.mainloop()
    except Exception:
        import traceback
        err = traceback.format_exc()
        try:
            (Path(BASE) / "error.log").write_text(err, encoding="utf-8")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
