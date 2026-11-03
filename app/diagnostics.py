# -*- coding: utf-8 -*-
"""
依赖自检 / 构建信息 / 配置文件路径 —— 单一实现，多处复用。

复用点（R11：自检前置化）：
  * ``--env`` 命令行输出（纯 ASCII，GBK 控制台安全）
  * 启动时的 ``precheck()`` 日志
  * 「关于 / 诊断」面板（人话指引）
  * 构建脚本 ``build/build.py`` 的包内断言

本模块只依赖标准库，且不 import tkinter —— 构建期也需要它。
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

try:  # 同目录 import（源码运行 / PyInstaller PYZ 都成立）
    from version import APP_ID, APP_NAME, CHANGELOG_HEAD, version_string
except Exception:  # pragma: no cover - 极端情况下按目录名兜底
    APP_ID = "SketchfabDownloader"
    APP_NAME = "Sketchfab下载器"
    CHANGELOG_HEAD = 5

    def version_string() -> str:  # type: ignore[misc]
        return "0.0.0"


# ``--env`` 里不打印、但面板要展示的项。
# 目的：保证 --env 输出严格 = 8 旧行（RES/BASE/ENGINE/NODE/CONVERT/SHARP/WASM/EXTLIB）
# + 3 新行（NODE_LOOKUP/DECRYPT_BACKEND/BUILD），不因新增自检项而破坏既有排障习惯。
# changelog / build_info 的结论已并入 BUILD 行与构建脚本断言，面板里仍逐项展示。
_ENV_HIDDEN_KEYS = ("libvips_dup", "changelog", "build_info")

# 这三项用 ``NAME=value`` 的紧凑格式，便于 grep 与脚本化判断
_COMPACT_KEYS = ("node_lookup", "decrypt_backend", "build")

# 只关心「状态」不关心「路径」的旧行（沿用 v1.0.0 的 --env 输出形状）
_STATUS_ONLY_KEYS = ("sharp", "wasm", "extlib")


@dataclass
class CheckItem:
    """一条自检结果。"""

    key: str            # engine / node / convert / sharp / wasm / changelog / ...
    label: str          # 中文人话，给关于面板与日志用
    path: str           # 被检查的路径或值
    ok: bool            # 是否通过
    hint: str = ""      # 缺了怎么办（R11 要求「人话指引」）
    kind: str = "file"  # file / dir / value，仅用于展示

    def status_text(self) -> str:
        return "OK" if self.ok else "MISSING"


@dataclass
class Environment:
    """自检所需的路径集合（一次性算好，避免各处重复推导）。"""

    res: Path
    base: Path
    cli_dir: Path
    node_exe: Optional[Path] = None
    extra: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 路径与后端判定
# ---------------------------------------------------------------------------
def node_lookup(res: Optional[Path] = None) -> str:
    """
    引擎判断 Node 是否可用的**同一套逻辑**：``shutil.which("node")``。

    ``res`` 参数保留是为了签名稳定与未来「找不到时提示 RES 里有 node.exe」的增强；
    当前不做额外兜底 —— 这里必须与引擎真实行为一致，否则诊断会撒谎。

    唯一加工：``shutil.which`` 在 Windows 上按 PATHEXT 拼候选名，磁盘上是
    ``node.exe`` 也会返回 ``node.EXE``。这里按目录里的真实文件名回正，
    免得出诊断输出和文档对不上（不影响引擎行为）。
    """
    del res  # 占位：保持签名兼容
    try:
        found = shutil.which("node") or shutil.which("nodejs") or ""
    except Exception:
        return ""
    if not found:
        return ""
    try:
        folder, base = os.path.split(found)
        for name in os.listdir(folder or "."):
            if name.lower() == base.lower():
                return os.path.join(folder, name)
    except OSError:
        pass
    return found


def _wasm_ready(cli_dir: Optional[Path]) -> bool:
    """复刻引擎 ``_wasm_decrypt_paths()`` 的判定（含 >10KB 体积门槛）。"""
    if not cli_dir:
        return False
    worker = Path(cli_dir) / "tools" / "wasm" / "decrypt_worker.mjs"
    wasm = Path(cli_dir) / "tools" / "wasm" / "decrypt.wasm"
    try:
        return worker.is_file() and wasm.is_file() and wasm.stat().st_size > 10_000
    except OSError:
        return False


def decrypt_backend(res: Path, cli_dir: Optional[Path] = None) -> str:
    """
    当前**实际会走**的解密后端：``"wasm"`` / ``"legacy"`` / ``"none"``。

    严格照抄 ``dist_engine/main.py::decrypt_binz`` 的分支条件：

        if worker and node_ok:      → wasm   (Node + tools/wasm 齐全)
        elif BINZ_DECRYPT.is_file() → legacy (tools/binz/binzDecrypt.exe)
        else                        → none

    这是 v1.1 的关键可观测点：PATH 修复后到底走哪条路，靠这一行说清。
    """
    cli = Path(cli_dir) if cli_dir else Path(res) / "engine"
    if node_lookup(res) and _wasm_ready(cli):
        return "wasm"
    if (Path(cli) / "tools" / "binz" / "binzDecrypt.exe").is_file():
        return "legacy"
    return "none"


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def _libvips_dups(res: Path) -> List[str]:
    """返回 ``_internal`` 下残留的顶层 libvips 副本（瘦身是否生效的标志）。"""
    found: List[str] = []
    for p in sorted(Path(res).glob("libvips*.dll")):
        found.append(p.name)
    return found


def collect(res: Path, base: Path, cli_dir: Path,
            node_exe: Optional[Path] = None) -> List[CheckItem]:
    """
    逐项检查运行时依赖，返回有序的 :class:`CheckItem` 列表。

    顺序即 ``--env`` 的输出顺序：前 8 项与 v1.0.0 完全一致（兼容既有排障习惯），
    之后是 ``NODE_LOOKUP`` / ``DECRYPT_BACKEND`` / ``BUILD`` 三行新增项。
    """
    res = Path(res)
    base = Path(base)
    cli_dir = Path(cli_dir)
    node_exe = Path(node_exe) if node_exe else res / "node.exe"

    items: List[CheckItem] = []

    # --- 与 v1.0.0 一致的 8 行 -------------------------------------------
    items.append(CheckItem("res", "资源目录（_internal）", str(res), res.is_dir(),
                           "包不完整，请重新解压整个 zip", "dir"))
    items.append(CheckItem("base", "程序目录", str(base), base.is_dir(),
                           "程序目录不可访问，请换一个可写位置解压", "dir"))

    engine_main = cli_dir / "main.py"
    items.append(CheckItem("engine", "下载引擎", str(cli_dir), engine_main.is_file(),
                           "缺少 engine\\main.py：包不完整，请重新解压", "dir"))

    items.append(CheckItem("node", "Node.js", str(node_exe), node_exe.is_file(),
                           "缺少 node.exe：贴图还原会失败，请重新解压整包（不要只拷 exe）"))

    convert = res / "convert.mjs"
    items.append(CheckItem("convert", "转换脚本 convert.mjs", str(convert), convert.is_file(),
                           "缺少 convert.mjs：无法导出带材质的 .glb，请重新解压"))

    sharp = res / "node_modules" / "sharp"
    items.append(CheckItem("sharp", "图像库 sharp", str(sharp), sharp.is_dir(),
                           "缺少 node_modules\\sharp：贴图编码会失败，请重新解压", "dir"))

    wasm = cli_dir / "tools" / "wasm" / "decrypt.wasm"
    items.append(CheckItem("wasm", "WASM 解密模块", str(wasm), _wasm_ready(cli_dir),
                           "缺少 tools\\wasm\\：新格式模型可能解不开，请重新解压"))

    extlib = res / "extlib"
    items.append(CheckItem("extlib", "转换扩展库 extlib", str(extlib), extlib.is_dir(),
                           "缺少 extlib\\：GLB 转换会失败，请重新解压", "dir"))

    # --- 新增：构建与瘦身相关 --------------------------------------------
    changelog = res / "CHANGELOG.md"
    items.append(CheckItem("changelog", "内置更新日志", str(changelog), changelog.is_file(),
                           "缺少 CHANGELOG.md：关于面板只能显示版本号"))

    binfo_path = res / "build_info.json"
    items.append(CheckItem("build_info", "构建信息 build_info.json", str(binfo_path),
                           binfo_path.is_file(),
                           "缺少 build_info.json：按「开发版」显示，不影响使用"))

    dups = _libvips_dups(res)
    items.append(CheckItem(
        "libvips_dup", "重复 libvips 副本", ",".join(dups) if dups else "(无)",
        True,                                          # 不影响可用性，所以 ok=True
        ("发现顶层 libvips 副本（瘦身未生效，仅占体积）：" + ",".join(dups)) if dups else "",
        "value"))

    # --- 新增三行（--env 里以 NAME=value 输出） ---------------------------
    lookup = node_lookup(res)
    items.append(CheckItem("node_lookup", "引擎查找 Node 的结果", lookup or "(not found)",
                           bool(lookup),
                           "引擎用 PATH 找 node，找不到会回落到 binzDecrypt.exe",
                           "value"))

    backend = decrypt_backend(res, cli_dir)
    items.append(CheckItem("decrypt_backend", "实际解密后端", backend, backend != "none",
                           "两条后端都不可用：新格式模型会下载失败", "value"))

    info = load_build_info(res)
    build_summary = "{ver} ({date})".format(
        ver=info.get("version", version_string()),
        date=info.get("build_date", "开发版"))
    extra_bits = []
    if info.get("python"):
        extra_bits.append("py" + str(info["python"]))
    if info.get("pyinstaller"):
        extra_bits.append("pyinstaller " + str(info["pyinstaller"]))
    if extra_bits:
        build_summary += ", " + ", ".join(extra_bits)
    items.append(CheckItem("build", "构建信息", build_summary, True,
                           "开发版构建信息来自 version.py", "value"))

    return items


def problems(items: Sequence[CheckItem]) -> List[CheckItem]:
    """筛出不通过的项（``precheck`` / 构建断言用）。"""
    return [it for it in items if not it.ok]


def format_ascii(items: Sequence[CheckItem]) -> str:
    """
    把自检结果渲染成 ``--env`` 的纯文本输出。

    * 前 8 行沿用 v1.0.0 的 ``NAME = value [OK|MISSING]`` 形状
    * ``NODE_LOOKUP`` / ``DECRYPT_BACKEND`` / ``BUILD`` 用 ``NAME=value``
      （紧凑格式，方便 grep / 脚本判断）
    * ``libvips_dup`` 等纯展示项不进 ``--env``（见 ``_ENV_HIDDEN_KEYS``）
    """
    lines: List[str] = []
    for it in items:
        if it.key in _ENV_HIDDEN_KEYS:
            continue
        name = it.key.upper()
        if it.key in _COMPACT_KEYS:
            lines.append("{0}={1}".format(name, it.path))
        elif it.key in _STATUS_ONLY_KEYS:
            lines.append("{0:<8} = [{1}]".format(name, it.status_text()))
        else:
            lines.append("{0:<8} = {1} [{2}]".format(name, it.path, it.status_text()))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CHANGELOG / build_info
# ---------------------------------------------------------------------------
def read_changelog_head(path: Path, n: int = CHANGELOG_HEAD) -> List[str]:
    """
    读取更新日志前 ``n`` 条，每条形如 ``"1.1.0　版本号成为一等公民…"``。

    「一条」= 一个 ``## [x.y.z]`` 版本段的首条描述（优先取该段第一条 ``- `` 列表项）。
    文件不存在或格式不符时返回空列表，绝不抛异常（关于面板要能降级显示）。
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    entries: List[str] = []
    cur_ver = ""
    cur_desc = ""
    in_section = False

    def flush() -> None:
        if cur_ver:
            entries.append((cur_ver + "　" + cur_desc) if cur_desc else cur_ver)

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            flush()
            cur_ver, cur_desc = "", ""
            body = line[3:].strip()
            if body.startswith("["):
                end = body.find("]")
                if end > 0:
                    cur_ver = body[1:end].strip()
                    in_section = bool(cur_ver)
                    continue
            in_section = False
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not cur_desc and stripped.startswith("- "):
            cur_desc = stripped[2:].strip()
        if len(entries) >= n and cur_desc:
            break
    flush()
    return entries[:max(0, n)]


def load_build_info(res: Path) -> dict:
    """
    读 ``_internal/build_info.json``；读不到返回开发版信息（降级，不报错）。
    """
    p = Path(res) / "build_info.json"
    data = None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = None
    except Exception:
        data = None

    if not data:
        return {"version": version_string(), "build_date": "开发版",
                "python": "", "pyinstaller": "", "app": APP_ID}

    out = {
        "version": str(data.get("version") or version_string()),
        "build_date": str(data.get("build_date") or "开发版"),
        "python": str(data.get("python") or ""),
        "pyinstaller": str(data.get("pyinstaller") or ""),
        "app": str(data.get("app") or APP_ID),
    }
    for key in ("engine_sha", "slim_rules", "onedir_mb", "zip_mb"):
        if key in data:
            out[key] = data[key]
    return out


# ---------------------------------------------------------------------------
# 配置路径（R7：配置与程序分离）
# ---------------------------------------------------------------------------
def resolve_cfg_paths(base: Path, appdata: Path,
                     portable: bool = False) -> Tuple[Path, Path]:
    """
    返回 ``(写目标, 读兜底)``。

    规则（架构设计 3.6）：

    * 程序目录里有 ``config.json``（或显式 ``--portable``）→ 便携模式，写程序目录
    * 否则写 ``%APPDATA%\\Sketchfab下载器\\config.json``，程序目录那份只作为读兜底
    * 写只在**一处**发生（单真源），读才两处都试（老版本遗留配置仍生效）
    """
    base = Path(base)
    appdata = Path(appdata)
    base_cfg = base / "config.json"
    app_cfg = appdata / APP_NAME / "config.json"

    try:
        has_portable_cfg = base_cfg.exists()
    except OSError:
        has_portable_cfg = False

    if portable or has_portable_cfg:
        return base_cfg, app_cfg
    return app_cfg, base_cfg


def appdata_root() -> Path:
    """``%APPDATA%``，取不到时用 ``~/AppData/Roaming``。"""
    env = os.environ.get("APPDATA")
    if env:
        return Path(env)
    return Path.home() / "AppData" / "Roaming"
