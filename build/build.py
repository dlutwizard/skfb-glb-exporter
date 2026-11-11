# -*- coding: utf-8 -*-
"""
一键构建（R4 / M1 / M2 / M5 / M7 / R13）。

    python build/build.py                 # 按 app/version.py 的版本出绿色版 zip
    python build/build.py --version 1.1.0 # 显式指定（必须与 version.py 一致）
    python build/build.py --keep-legacy-decrypt # 保留 binzDecrypt.exe（S6，默认删）
    python build/build.py --single-file   # 只构建调试用的单文件版（不写 发布/）

流水线：
    校验版本(semver + 单调) → 生成 version_info.txt → PyInstaller onedir →
    pefile 读 exe 版本 → 瘦身(slim.py，带守卫) → 写包内元文件 →
    起 exe 自检(--version/--env/--selftest×3) → 体积门禁 → 渲染使用说明 →
    打包 zip → 追加体积账本 → 写构建报告

退出码语义（脚本化会依赖，别随意改）：
    0  成功
    2  版本号非法 / 回退（不是严格大于账本与 CHANGELOG 里的最高版本）
    3  构建失败（PyInstaller 报错 / 产物缺失）
    4  体积超预算（onedir > 420 MiB 或 zip > 280 MB）
    5  版本一致性校验失败（exe 属性 / --version 输出与 version.py 不符）
    6  包内出现禁止物（config.json / psutil / 旧手写使用说明）
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 路径常量
BUILD = Path(__file__).resolve().parent
ROOT = BUILD.parent
APP = ROOT / "app"
DOCS = ROOT / "docs"
RELEASE = ROOT / "发布"
ENGINE = ROOT / "dist_engine"
GEN = BUILD / "gen"
DIST_DIR = BUILD / "dist_dir"
WORK_DIR = BUILD / "work_dir"
ONEFILE_DIST = BUILD / "dist_onefile"
SPEC_DIR = BUILD / "Sketchfab下载器.spec"
SPEC_ONE = BUILD / "single_file.spec"
SPEC_WORKER = BUILD / "advanced_worker.spec"
ADVANCED_DIST = BUILD / "advanced_dist"
ADVANCED_WORK = BUILD / "advanced_work"
WORKER_NAME = "advanced_print_worker"
LEDGER = BUILD / "sizes.json"
TEMPLATE = APP / "使用说明.template.txt"
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_PY = APP / "version.py"
VENV_PY = BUILD / "venv" / "Scripts" / "python.exe"

ONEDIR_NAME = "Sketchfab下载器"
EXE_NAME = ONEDIR_NAME + ".exe"

# 体积门禁（M1/M2）。这里的 "MB" 一律按 MiB 计。
# v1.5.0 起内置高级转换 worker（numpy/scipy/scikit-image/trimesh），
# 体积预算相应放宽；worker 只在用户点「高级 3D 打印转换」时启动。
ONEDIR_LIMIT = 420 * 1024 * 1024
ZIP_LIMIT = 280 * 1024 * 1024
COMPONENT_WARN = 2 * 1024 * 1024      # R13：单组件相对上一版 +2 MiB 告警

PLACEHOLDERS = ("{{VERSION}}", "{{BUILD_DATE}}", "{{SIZE_ONEDIR_MB}}",
                "{{STARTUP_SEC}}", "{{CHANGELOG_TOP5}}", "{{PYTHON_VER}}")


class BuildError(RuntimeError):
    """带退出码的构建失败。"""

    def __init__(self, message: str, code: int = 3):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- 小工具
def log(msg: str = "") -> None:
    print(msg, flush=True)


def step(title: str) -> None:
    log("")
    log("=" * 74)
    log("== " + title)
    log("=" * 74)


def mb(nbytes: int) -> float:
    return nbytes / 1024 / 1024


def dir_bytes(path: Path) -> int:
    p = Path(path)
    if p.is_file():
        return p.stat().st_size
    total = 0
    for cur, _dirs, files in os.walk(p):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(cur, name))
            except OSError:
                continue
    return total


def load_version_namespace() -> dict:
    """独立 exec app/version.py（不允许 import GUI 依赖）。"""
    source = VERSION_PY.read_text(encoding="utf-8")
    ns: dict = {"__name__": "sfd_version", "__file__": str(VERSION_PY)}
    exec(compile(source, str(VERSION_PY), "exec"), ns)     # noqa: S102 - 版本真源就该这么读
    return ns


def ensure_build_python(argv: Sequence[str]) -> None:
    """
    构建需要 `pefile`（在 build/venv 里，不在系统 Python 里）。

    `python build/build.py` 用哪个解释器都行：缺依赖时自动用 build/venv 的
    python 重跑本脚本（用环境变量防重入）。
    """
    try:
        import pefile  # noqa: F401
        return
    except Exception:
        pass
    if os.environ.get("SFD_BUILD_REEXEC") == "1" or not VENV_PY.is_file():
        raise BuildError("缺少 pefile，且找不到 %s；请先 pip install pefile" % VENV_PY, 3)
    log("· 当前解释器缺少 pefile，改用构建 venv 重跑：%s" % VENV_PY)
    env = dict(os.environ)
    env["SFD_BUILD_REEXEC"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    rc = subprocess.call([str(VENV_PY), str(Path(__file__).resolve())] + list(argv), env=env)
    raise SystemExit(rc)


def resolve_node_exe() -> Path:
    """node.exe 位置：环境变量 → 仓库根 → PATH 上的 node。"""
    env = os.environ.get("SFD_NODE_EXE")
    if env and Path(env).is_file():
        return Path(env)
    cands = [ROOT / "node.exe"]
    for name in ("node", "node.exe", "nodejs"):
        hit = shutil.which(name)
        if hit:
            cands.append(Path(hit))
            break
    for cand in cands:
        if cand.is_file():
            return cand
    raise BuildError(
        "找不到 node.exe。把 node.exe 放到仓库根目录，或设置 SFD_NODE_EXE 环境变量。", 3)


# ---------------------------------------------------------------- 体积账本
def load_ledger() -> List[dict]:
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        return []


def prev_record(ledger: List[dict], version: str) -> Optional[dict]:
    """上一版 = 账本里版本号小于当前的最大那一条。"""
    cur = tuple(int(x) for x in version.split("."))
    best = None
    for rec in ledger:
        try:
            v = tuple(int(x) for x in str(rec.get("version", "")).split("."))
        except ValueError:
            continue
        if v < cur and (best is None or v > best[0]):
            best = (v, rec)
    return best[1] if best else None


def write_ledger(ledger: List[dict], record: dict) -> None:
    """同一版本重跑时覆盖旧条目（保证连续跑两次结果一致）。"""
    out = [d for d in ledger if str(d.get("version")) != str(record.get("version"))]
    out.append(record)
    out.sort(key=lambda d: [int(x) if str(x).isdigit() else 0
                            for x in str(d.get("version", "0")).split(".")])
    LEDGER.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 版本校验
def _parse3(text: str) -> Tuple[int, int, int]:
    parts = str(text).strip().lstrip("vV").split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise BuildError("版本号必须是 MAJOR.MINOR.PATCH 三段数字：%r" % text, 2)
    return int(parts[0]), int(parts[1]), int(parts[2])


def changelog_head_version() -> Optional[str]:
    """CHANGELOG 里最高的版本号（作为单调性下限的一部分）。"""
    try:
        text = CHANGELOG.read_text(encoding="utf-8")
    except OSError:
        return None
    versions: List[Tuple[int, int, int]] = []
    for line in text.splitlines():
        m = re.match(r"^##\s*\[([0-9]+\.[0-9]+\.[0-9]+)\]", line.strip())
        if m:
            try:
                versions.append(_parse3(m.group(1)))
            except BuildError:
                continue
    if not versions:
        return None
    top = max(versions)
    return "%d.%d.%d" % top


def check_version_monotonic(version: str, ledger: List[dict]) -> Tuple[dict, str]:
    """
    返回 (上一版账本记录, 说明)。版本非法或回退时抛 :class:`BuildError`（exit 2）。
    """
    cur = _parse3(version)

    floors: List[Tuple[Tuple[int, int, int], str]] = []
    for rec in ledger:
        try:
            floors.append((_parse3(str(rec.get("version"))), "build/sizes.json"))
        except BuildError:
            continue
    head = changelog_head_version()
    if head:
        floors.append((_parse3(head), "CHANGELOG.md 首条"))

    if not floors:
        return {}, "账本与 CHANGELOG 均为空（首个版本）"

    top_v, top_src = max(floors, key=lambda x: x[0])
    if cur < top_v:
        raise BuildError(
            "版本号回退：当前 %s < 已知最高 %s（来源 %s）。"
            "要发补丁版请同时更新 CHANGELOG.md 与 sizes.json" %
            (version, "%d.%d.%d" % top_v, top_src), 2)

    prev = prev_record(ledger, version)
    if cur == top_v:
        return prev or {}, "同版本重跑（账本条目将被覆盖）"
    return prev or {}, "正常递增（上一版 %s）" % ("%d.%d.%d" % top_v)


# ---------------------------------------------------------------- 生成物
def gen_version_info(version: str, out_path: Path) -> Path:
    """从 version.py 生成 build/version_info.txt（**不再手改这个文件**）。"""
    vt = _parse3(version)
    quad = vt + (0,)
    text = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad},
    prodvers={quad},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '080404B0',
        [StringStruct('CompanyName', '{company}'),
         StringStruct('FileDescription', '{desc}'),
         StringStruct('FileVersion', '{dotted}.0'),
         StringStruct('InternalName', '{app_id}'),
         StringStruct('LegalCopyright', '{copy}'),
         StringStruct('OriginalFilename', '{exe}'),
         StringStruct('ProductName', '{desc}'),
         StringStruct('ProductVersion', '{dotted}.0')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""".format(quad=quad, company="个人工具", desc="Sketchfab 模型下载器",
           dotted=version, app_id="SketchfabDownloader",
           copy="仅限个人学习使用", exe=EXE_NAME)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def changelog_top_lines(n: int = 5) -> List[str]:
    """取 CHANGELOG 前 n 条（复用 app/diagnostics.py 的解析，避免两份逻辑）。"""
    try:
        if str(APP) not in sys.path:
            sys.path.insert(0, str(APP))
        import diagnostics as diag
        return diag.read_changelog_head(CHANGELOG, n)
    except Exception:
        return []


def render_readme(version: str, build_date: str,
                  onedir_mb: Optional[float] = None,
                  startup_sec: Optional[float] = None) -> str:
    """渲染 app/使用说明.template.txt → 使用说明.txt（只注入 6 个占位符）。"""
    text = TEMPLATE.read_text(encoding="utf-8")
    tops = changelog_top_lines(5)
    top_text = "\n".join("  · " + line for line in tops) if tops else "  · 见 CHANGELOG.md"
    mapping = {
        "{{VERSION}}": version,
        "{{BUILD_DATE}}": build_date,
        "{{SIZE_ONEDIR_MB}}": ("%.1f" % onedir_mb) if onedir_mb else "—",
        "{{STARTUP_SEC}}": ("%.2f" % startup_sec) if startup_sec else "—",
        "{{CHANGELOG_TOP5}}": top_text,
        "{{PYTHON_VER}}": platform.python_version(),
    }
    for key, value in mapping.items():
        text = text.replace(key, value)
    leftover = [p for p in PLACEHOLDERS if p in text]
    if leftover:
        raise BuildError("模板仍有未替换占位符：%s" % ", ".join(leftover), 3)
    return text


def build_info_payload(version: str, build_date: str,
                       slim_rules: Sequence[str],
                       onedir_mb: Optional[float] = None,
                       build_ts: str = "") -> dict:
    """`_internal/build_info.json` 的内容。"""
    try:
        import PyInstaller
        pyi_ver = PyInstaller.__version__
    except Exception:
        pyi_ver = ""
    engine_sha = ""
    try:
        import hashlib
        engine_sha = hashlib.sha1((ENGINE / "main.py").read_bytes()).hexdigest()[:8]
    except OSError:
        engine_sha = ""
    payload = {
        "app": "sketchfab_downloader",
        "version": version,
        "build_date": build_date,
        "build_ts": build_ts or build_date,
        "python": platform.python_version(),
        "pyinstaller": pyi_ver,
        "engine_sha": engine_sha,
        "slim_rules": list(slim_rules),
        "note": "zip_mb 见 build/sizes.json 与 docs/build-report；此处不写入以避免两次压缩",
    }
    if onedir_mb is not None:
        payload["onedir_mb"] = round(onedir_mb, 1)
    return payload


def write_version_json(target: Path, version: str, build_date: str,
                       notes: str, commit: str = "") -> Path:
    """包根 version.json（给排障与「检查更新」参照）。

    ``commit`` 让发布物能反查到具体源码版本（SFD-007/ROUND-01 建议）：
    以前只能靠人工比对文件哈希，现在 version.json 里直接写着。
    """
    payload = {
        "schema": 1,
        "app": "sketchfab_downloader",
        "version": version,
        "build_date": build_date,
        "channel": "stable",
        "commit": commit or "unknown",
        "url": "",
        "notes": notes,
        "min_version": "1.0.0",
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return target


def git_commit() -> str:
    """当前 HEAD 的短哈希；非 git 环境（源码压缩包）返回空串。"""
    try:
        out = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def changelog_headline(version: str) -> str:
    """取 CHANGELOG 中当前版本条目的第一句，作为 version.json 的 notes。"""
    try:
        lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "v%s" % version
    inside = False
    for line in lines:
        if line.startswith("## "):
            inside = version in line
            continue
        if not inside:
            continue
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        return text.lstrip("-").strip()
    return "v%s" % version


# ---------------------------------------------------------------- 构建步骤
def spec_check_no_absolute_paths() -> None:
    """`build/` 下所有 spec 不得残留绝对路径（保证可复现、可换机器构建）。"""
    bad: List[str] = []
    for spec in sorted(BUILD.rglob("*.spec")):
        try:
            text = spec.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if re.search(r"[A-Za-z]:[\\/]{1,2}(?:Users|Anaconda|workbuddy|Program)", line):
                bad.append("%s:%d: %s" % (spec.relative_to(ROOT), lineno, line.strip()[:90]))
    if bad:
        raise BuildError("spec 里仍有绝对路径：\n" + "\n".join(bad), 5)


def _preclean(target: Path) -> None:
    """
    构建前先清掉上一次的产物目录。

    为什么要自己删：PyInstaller 的 ``COLLECT`` 阶段会 ``rm -rf`` 同名目录，
    在受限环境下这次批量删除可能被安全策略拦下（构建直接失败）。自己先删，
    失败也不致命（PyInstaller 还会再试一次），但日志要留下痕迹便于排查。
    """
    if not target.exists():
        return
    try:
        shutil.rmtree(target)
        log("· 已清理旧产物 %s" % target.relative_to(ROOT))
    except Exception as e:
        log("· ⚠ 清理旧产物失败（%s），交给 PyInstaller 处理" % e)


def check_pyinstaller_version() -> None:
    """构建器版本闸门。

    PyInstaller 6.20+ 的引导器会被 Microsoft Defender 的机器学习模型判成
    Trojan:Win32/Bearfoos.B!ml（实测 6.22.3 打包的 exe 一落地就被隔离，
    6.10.0 打包的同一份代码扫描干净、可正常下载）。这不是代码问题，
    但会让用户拿到文件就跑不起来，因此在此硬拦。
    """
    if os.environ.get("SFD_ALLOW_PYI") == "1":
        return
    try:
        import PyInstaller as _pyi
        got = _pyi.__version__
    except Exception:
        return
    if not got.startswith("6.10"):
        raise BuildError(
            "PyInstaller %s 不在允许列表（只用 6.10.x）：6.20+ 的引导器会被 Defender "
            "误判为 Trojan:Win32/Bearfoos.B!ml，用户下载后文件会被隔离。"
            "修法：pip install pyinstaller==6.10.0；"
            "确需用当前版本请设 SFD_ALLOW_PYI=1。" % got, 6)


def run_pyinstaller(version: str, node_exe: Path, single_file: bool = False) -> Path:
    """跑 PyInstaller，返回产物路径（onedir 目录或单文件 exe）。"""
    check_pyinstaller_version()
    if single_file:
        cmd_py = [str(VENV_PY), "-m", "PyInstaller"] if VENV_PY.is_file() \
            else [sys.executable, "-m", "PyInstaller"]
        target = ONEFILE_DIST
        spec = SPEC_ONE
    else:
        cmd_py = [str(VENV_PY), "-m", "PyInstaller"] if VENV_PY.is_file() \
            else [sys.executable, "-m", "PyInstaller"]
        target = DIST_DIR
        spec = SPEC_DIR

    if not spec.is_file():
        raise BuildError("找不到 spec：%s" % spec, 3)

    _preclean(target / (EXE_NAME if single_file else ONEDIR_NAME))

    env = dict(os.environ)
    env["SFD_NODE_EXE"] = str(node_exe)
    env["SFD_ROOT"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    cmd = cmd_py + [str(spec), "--noconfirm",
                    "--distpath", str(target),
                    "--workpath", str(WORK_DIR)]
    log("$ " + " ".join(cmd))
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    if proc.returncode != 0:
        raise BuildError("PyInstaller 失败（exit %d）" % proc.returncode, 3)
    log("· PyInstaller 用时 %.1f s" % (time.time() - started))

    out = target / (EXE_NAME if single_file else ONEDIR_NAME)
    if not out.exists():
        raise BuildError("PyInstaller 产物缺失：%s" % out, 3)
    return out


def build_advanced_worker(force: bool = False) -> Path:
    """构建内置高级转换 worker（onedir），返回 exe 路径。

    依赖已固定在 build/venv：numpy 1.26 / scipy 1.11 / scikit-image 0.22 /
    trimesh 4.4。发布包把整个目录放进 _internal/advanced/worker/。
    """
    exe = ADVANCED_DIST / WORKER_NAME / (WORKER_NAME + ".exe")
    sources = [SPEC_WORKER, APP / "advanced_print_worker.py"]
    if (not force and exe.is_file()
            and all(exe.stat().st_mtime >= s.stat().st_mtime
                    for s in sources if s.is_file())):
        log("· 复用已有内置 worker（源码未变）")
        return exe
    if not SPEC_WORKER.is_file():
        raise BuildError("找不到 worker spec：%s" % SPEC_WORKER, 3)
    cmd_py = [str(VENV_PY), "-m", "PyInstaller"] if VENV_PY.is_file() \
        else [sys.executable, "-m", "PyInstaller"]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cmd = cmd_py + [str(SPEC_WORKER), "--noconfirm",
                    "--distpath", str(ADVANCED_DIST),
                    "--workpath", str(ADVANCED_WORK)]
    log("$ " + " ".join(cmd))
    started = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    if proc.returncode != 0:
        raise BuildError("内置 worker 构建失败（exit %d）" % proc.returncode, 3)
    if not exe.is_file():
        raise BuildError("内置 worker 产物缺失：%s" % exe, 3)
    log("· 内置 worker 用时 %.1f s（%.1f MiB）"
        % (time.time() - started, mb(dir_bytes(exe.parent))))
    return exe


def _as_text(value) -> str:
    """bytes / str → str（PE 资源里的字符串可能是任一种）。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value if value is not None else "")


def _iter_version_strings(table):
    """遍历一个 StringTable 的 (Key, value)。

    pefile 各版本里 ``StringTable.entries`` 可能是 dict（Key→value），
    也可能是一串 StringStruct 对象，两种都要吃得下。
    """
    entries = getattr(table, "entries", None)
    if isinstance(entries, dict):
        for key, value in entries.items():
            yield _as_text(key), _as_text(value)
        return
    for struct in entries or []:
        yield _as_text(getattr(struct, "Key", b"")), _as_text(getattr(struct, "entry", b""))


def read_exe_version(exe: Path) -> Tuple[int, int, int, int]:
    """用 pefile 读 exe 的 FileVersion 资源（M5 的第 5 处校验点）。"""
    import pefile

    pe = pefile.PE(str(exe), fast_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    found = ""
    for fileinfo in getattr(pe, "FileInfo", []) or []:
        for entry in fileinfo:
            if _as_text(getattr(entry, "Key", b"")) != "StringFileInfo":
                continue
            for table in getattr(entry, "StringTable", []) or []:
                for key, value in _iter_version_strings(table):
                    if key == "FileVersion":
                        found = value
    pe.close()
    if not found:
        raise BuildError("读不到 exe 的 FileVersion 资源", 5)
    parts = [p for p in re.split(r"[^0-9]+", found) if p != ""]
    nums = [int(p) for p in parts[:4]]
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums[:4])          # type: ignore[return-value]


def _decode_output(raw: bytes) -> str:
    """
    PyInstaller 的 windowed 程序把 stdout 包成自己的管道，编码跟**系统代码页**
    走（中文 Windows = cp936），完全不理 ``PYTHONIOENCODING``。所以先按 utf-8
    严格解，失败再退 cp936 —— 纯 ASCII 输出两种情况都正确。
    """
    if not raw:
        return ""
    for enc in ("utf-8", "cp936"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def run_exe(exe: Path, args: Sequence[str], timeout: int = 180) -> Tuple[int, str]:
    """
    起打包后的 exe 并抓 stdout。

    windowed exe 没有自己的控制台，必须由父进程给管道才拿得到输出；
    编码问题见 :func:`_decode_output`。
    """
    cmd = [str(exe)] + list(args)
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              cwd=str(exe.parent))
    except subprocess.TimeoutExpired:
        raise BuildError("exe 超时未退出：%s" % " ".join(args), 3)
    return proc.returncode, _decode_output((proc.stdout or b"") + (proc.stderr or b""))


def probe_exe(exe: Path, version: str) -> dict:
    """
    起 exe 做三项自证：`--version` / `--env` / `--selftest`×3。

    返回 ``{"version_out", "env", "startup": [...], "startup_median"}``。
    """
    rc, out = run_exe(exe, ["--version"])
    if version not in out:
        raise BuildError("exe --version 输出里没有 %s：%r" % (version, out.strip()[:200]), 5)
    log("· exe --version → " + out.strip())

    rc, env_out = run_exe(exe, ["--env"])
    env_map: Dict[str, str] = {}
    for line in env_out.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            env_map[key.strip()] = val.strip()
    for required in ("RES", "BASE", "ENGINE", "NODE", "CONVERT", "SHARP", "WASM",
                     "EXTLIB", "NODE_LOOKUP", "DECRYPT_BACKEND", "BUILD"):
        if required not in env_map:
            raise BuildError("exe --env 缺少 %s 行：\n%s" % (required, env_out), 5)
    backend = env_map.get("DECRYPT_BACKEND", "")
    if backend not in ("wasm", "legacy"):
        raise BuildError("exe --env 的 DECRYPT_BACKEND 异常：%r" % backend, 5)
    log("· exe --env → DECRYPT_BACKEND=%s, NODE_LOOKUP=%s" %
        (backend, env_map.get("NODE_LOOKUP", "")))

    times: List[float] = []
    # 预热一次：刚打包出来的 exe 第一次启动会被杀软实时扫描，实测能慢 2~3 倍
    # （2.48 s vs 0.90 s）。不预热的话每次构建都会误报 M3 不达标。
    run_exe(exe, ["--selftest"])
    for _ in range(3):
        started = time.perf_counter()
        rc, st_out = run_exe(exe, ["--selftest"])
        elapsed = time.perf_counter() - started
        if "SELFTEST_OK" not in st_out:
            raise BuildError("exe --selftest 未输出 SELFTEST_OK：%r" % st_out[:300], 3)
        times.append(elapsed)
    times_sorted = sorted(times)
    median = times_sorted[len(times_sorted) // 2]
    log("· exe --selftest ×3（已预热）→ 中位 %.2f s（%.2f / %.2f / %.2f）" %
        (median, times[0], times[1], times[2]))

    return {"version_out": out.strip(), "env": env_map, "backend": backend,
            "startup": times, "startup_median": median}


def verify_package(onedir: Path, version: str) -> None:
    """包内禁止物断言（R7 / R8 / 6.2）。"""
    problems: List[str] = []

    for cfg in onedir.rglob("config.json"):
        problems.append("包内出现 config.json（会破坏「覆盖解压不丢设置」）：%s" %
                        cfg.relative_to(onedir))

    internal = onedir / "_internal"
    if not (internal / "CHANGELOG.md").is_file():
        problems.append("缺少 _internal/CHANGELOG.md（关于面板需要离线读它）")
    if not (internal / "build_info.json").is_file():
        problems.append("缺少 _internal/build_info.json")

    readme = onedir / "使用说明.txt"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8", errors="replace")
        if version not in text:
            problems.append("包根 使用说明.txt 不是本次渲染的版本（疑似旧手写版残留）")
        for leftover in ("{{", "}}"):
            if leftover in text:
                problems.append("使用说明.txt 仍有未替换的占位符")
                break

    for psutil_hit in list(internal.glob("psutil*")):
        problems.append("包内出现 psutil（dev-only 依赖混入）：%s" % psutil_hit.name)

    # SFD-008：MIT 要求随副本分发许可声明；顶层没有就等于没给用户看
    for name in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
        if not (onedir / name).is_file():
            problems.append("发布包顶层缺少 %s（MIT 合规要求）" % name)

    # SFD-008：version.json 必须能反查源码版本，且与 version.py 一致
    vj = onedir / "version.json"
    if not vj.is_file():
        problems.append("缺少 version.json")
    else:
        try:
            payload = json.loads(vj.read_text(encoding="utf-8"))
        except Exception as exc:                       # noqa: BLE001
            problems.append("version.json 无法解析：%s" % exc)
            payload = {}
        if payload.get("version") != version:
            problems.append("version.json 的 version=%r 与 %s 不一致"
                            % (payload.get("version"), version))
        if not str(payload.get("commit") or "").strip():
            problems.append("version.json 缺 commit 字段（无法反查源码版本）")

    if problems:
        raise BuildError("包内检查未通过：\n  - " + "\n  - ".join(problems), 6)


def measure_components(onedir: Path) -> Dict[str, int]:
    """体积分项（与 sizes.json 的 key 对齐，缺的记 0 以便对比）。"""
    internal = onedir / "_internal"
    mods = internal / "node_modules"
    return {
        "node_exe": dir_bytes(internal / "node.exe"),
        "libvips_dup": (dir_bytes(internal / "libvips-42.dll")
                        + dir_bytes(internal / "libvips-cpp-8.18.7.dll")),
        "binzDecrypt_exe": dir_bytes(internal / "engine" / "tools" / "binz" / "binzDecrypt.exe"),
        "sharp_wasm32": dir_bytes(mods / "@img" / "sharp-wasm32"),
        "emnapi": dir_bytes(mods / "@emnapi"),
        "tslib": dir_bytes(mods / "tslib"),
        "sharp_win32_x64": dir_bytes(mods / "@img" / "sharp-win32-x64"),
        "engine": dir_bytes(internal / "engine"),
        "node_modules": dir_bytes(mods),
    }


def check_sizes(onedir_bytes: int, components: Dict[str, int],
                prev: Optional[dict]) -> List[str]:
    """M1 + R13。超预算抛 exit 4；单组件增长只告警。"""
    warnings: List[str] = []
    if onedir_bytes > ONEDIR_LIMIT:
        raise BuildError("onedir 体积超预算：%.1f MiB > %d MiB（M1）" %
                         (mb(onedir_bytes), ONEDIR_LIMIT // 1024 // 1024), 4)

    if prev and isinstance(prev.get("components"), dict):
        for key, value in components.items():
            old = int(prev["components"].get(key, 0) or 0)
            if old and value - old > COMPONENT_WARN:
                warnings.append("%s 增长 %.1f MiB（%.1f → %.1f）" %
                                (key, mb(value - old), mb(old), mb(value)))
    return warnings


def make_zip(onedir: Path, zip_path: Path) -> int:
    """打包成 zip，顶层目录固定为 ``Sketchfab下载器/``（保证覆盖解压升级）。"""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    files: List[Path] = []
    for cur, _dirs, names in os.walk(onedir):
        for name in names:
            files.append(Path(cur) / name)
    files.sort(key=lambda p: str(p).lower())

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for f in files:
            arc = Path(ONEDIR_NAME) / f.relative_to(onedir)
            zf.write(f, arcname=str(arc).replace(os.sep, "/"))
    size = zip_path.stat().st_size
    if size > ZIP_LIMIT:
        raise BuildError("zip 体积超预算：%.1f MB > %d MB（M2）" %
                         (mb(size), ZIP_LIMIT // 1024 // 1024), 4)
    log("· zip 写入 %d 个文件，%.1f MB" % (len(files), mb(size)))
    return size


# ---------------------------------------------------------------- 报告
def write_report(version: str, build_date: str, onedir_bytes: int, zip_bytes: int,
                 components: Dict[str, int], prev: Optional[dict],
                 removed: Sequence, probe: dict, warnings: Sequence[str],
                 zip_files: int, elapsed: float) -> Path:
    prev_comps = (prev or {}).get("components", {}) if prev else {}
    prev_onedir = int((prev or {}).get("onedir_bytes", 0) or 0)
    prev_zip = int((prev or {}).get("zip_bytes", 0) or 0)

    lines: List[str] = []
    lines.append("# 构建报告 · v%s" % version)
    lines.append("")
    lines.append("> 由 `build/build.py` 自动生成，请勿手改。构建日期：%s" % build_date)
    lines.append("")
    lines.append("## 1. 结论")
    lines.append("")
    lines.append("| 项 | 值 | 门禁 | 结果 |")
    lines.append("|---|---|---|---|")
    lines.append("| onedir 体积 | %.1f MiB（%d B） | ≤ %d MiB（M1） | %s |" %
                 (mb(onedir_bytes), onedir_bytes,
                  ONEDIR_LIMIT // 1024 // 1024,
                  "PASS" if onedir_bytes <= ONEDIR_LIMIT else "FAIL"))
    lines.append("| zip 体积 | %.1f MB（%d B） | ≤ %d MB（M2） | %s |" %
                 (mb(zip_bytes), zip_bytes, ZIP_LIMIT // 1024 // 1024,
                  "PASS" if zip_bytes <= ZIP_LIMIT else "FAIL"))
    lines.append("| 启动耗时（--selftest 中位） | %.2f s | ≤ 1.7 s（M3） | %s |" %
                 (probe["startup_median"],
                  "PASS" if probe["startup_median"] <= 1.7 else "WARN"))
    lines.append("| 版本一致性（exe 属性/--version/version.py） | %s | 一致（M5） | PASS |" %
                 version)
    lines.append("| exe 自检 | SELFTEST_OK ×3 | 必须（R4） | PASS |")
    lines.append("| 实际解密后端 | %s | 必须可观测 | PASS |" % probe["backend"])
    lines.append("| 包内禁止物（config.json / psutil / 旧使用说明） | 无 | 必须（R7） | PASS |")
    lines.append("| zip 内文件数 | %d | 连续两次一致 | 记录 |" % zip_files)
    lines.append("| 构建总耗时 | %.1f s | — | — |" % elapsed)
    lines.append("")
    lines.append("## 2. 体积分项与增量")
    lines.append("")
    lines.append("| 组件 | v%s | 上一版 v%s | 增量 | 告警（+2 MiB） |" %
                 (version, (prev or {}).get("version", "-")))
    lines.append("|---|---|---|---|---|")
    for key in ("node_exe", "libvips_dup", "binzDecrypt_exe", "sharp_wasm32",
                "emnapi", "tslib", "sharp_win32_x64", "engine", "node_modules"):
        cur = components.get(key, 0)
        old = int(prev_comps.get(key, 0) or 0)
        delta = cur - old if old else 0
        flag = "是" if (old and delta > COMPONENT_WARN) else ""
        lines.append("| %s | %.1f MiB | %s | %+.1f MiB | %s |" %
                     (key, mb(cur), ("%.1f MiB" % mb(old)) if old else "-",
                      mb(delta), flag))
    lines.append("| **onedir 合计** | **%.1f MiB** | **%s** | **%+.1f MiB** | |" %
                 (mb(onedir_bytes), ("%.1f MiB" % mb(prev_onedir)) if prev_onedir else "-",
                  mb(onedir_bytes - prev_onedir) if prev_onedir else 0))
    lines.append("| **zip** | **%.1f MB** | **%s** | **%+.1f MB** | |" %
                 (mb(zip_bytes), ("%.1f MB" % mb(prev_zip)) if prev_zip else "-",
                  mb(zip_bytes - prev_zip) if prev_zip else 0))
    lines.append("")
    if warnings:
        lines.append("### 单组件告警")
        lines.append("")
        for w in warnings:
            lines.append("- " + w)
        lines.append("")
    lines.append("## 3. 瘦身明细（build/slim.py，每条都过守卫）")
    lines.append("")
    if removed:
        lines.append("| 规则 | 路径 | 释放 |")
        lines.append("|---|---|---|")
        for r in removed:
            lines.append("| %s | `%s` | %.2f MiB |" % (r.rule_id, r.rel_path, mb(r.bytes)))
        lines.append("| **合计** | | **%.2f MiB** |" % mb(sum(r.bytes for r in removed)))
    else:
        lines.append("（本次无可删项：目标不存在，说明上一轮已瘦身或规则已失效）")
    lines.append("")
    lines.append("明确不删（PRD 曾误列，实测是 sharp 真依赖）：`@img/colour`、"
                 "`node_modules/{sharp,detect-libc,semver}`、`_internal/node.exe`、"
                 "`engine/tools/wasm/*`。")
    lines.append("")
    lines.append("## 4. exe 自证输出")
    lines.append("")
    lines.append("```")
    lines.append("$ Sketchfab下载器.exe --version")
    lines.append(probe["version_out"])
    lines.append("")
    lines.append("$ Sketchfab下载器.exe --env")
    for key in ("RES", "BASE", "ENGINE", "NODE", "CONVERT", "SHARP", "WASM",
                "EXTLIB", "NODE_LOOKUP", "DECRYPT_BACKEND", "BUILD"):
        if key in probe["env"]:
            # 与 app/diagnostics.py 的输出形状保持一致：
            # 前三组旧行是 "NAME = value"，新增三项是紧凑的 "NAME=value"
            if key in ("NODE_LOOKUP", "DECRYPT_BACKEND", "BUILD"):
                lines.append("%s=%s" % (key, probe["env"][key]))
            else:
                lines.append("%s = %s" % (key, probe["env"][key]))
    lines.append("")
    lines.append("$ Sketchfab下载器.exe --selftest  (×3)")
    lines.append("SELFTEST_OK  " + "  ".join("%.2fs" % t for t in probe["startup"]))
    lines.append("```")
    lines.append("")
    lines.append("## 5. 未纳入本报告的门禁")
    lines.append("")
    lines.append("- **M4 峰值内存 / M6 六模型回归**：需要真实下载，由 `tools/bench.py` "
                 "产出 `docs/benchmark-v1.1.md`（QA 执行，不在构建期做）")
    lines.append("- **zip_mb 未写入 build_info.json**：zip 体积在打包后才确定，"
                 "写进包内会导致两次压缩，故记录在 `build/sizes.json` 与本报告")
    lines.append("")
    out = DOCS / ("build-report-v%s.md" % version)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ---------------------------------------------------------------- 主流程
def _python_with_pytest() -> Optional[str]:
    """找一个能 import pytest 的解释器（优先显式指定，其次当前，再次构建 venv）。"""
    for py in (os.environ.get("SFD_TEST_PY"), sys.executable, str(VENV_PY)):
        if not py or not Path(py).is_file():
            continue
        try:
            probe = subprocess.run([py, "-c", "import pytest"], capture_output=True,
                                   timeout=120)
        except Exception:
            continue
        if probe.returncode == 0:
            return py
    return None


def run_test_gate() -> None:
    """
    发布门禁：**测试不过就不许打包**（SFD-006）。

    以前 build.py 只做 exe 自检，测试套件和发布流程完全脱钩，别人克隆下来
    pytest 是红的也照样能出包。这里把它变成硬前置。
    """
    step("0/9 测试门禁（pytest）")
    py = _python_with_pytest()
    if py is None:
        log("· 当前解释器与构建 venv 都没有 pytest，尝试装到 venv…")
        try:
            subprocess.call([str(VENV_PY), "-m", "pip", "install", "-q", "pytest"])
        except Exception:
            pass
        py = _python_with_pytest()
    if py is None:
        raise BuildError(
            "找不到带 pytest 的解释器，无法执行发布门禁。\n"
            "请先 pip install -r requirements-dev.txt（或加 --no-tests 跳过）", 3)
    log("· 用 %s 运行 tests/" % py)
    # 显式指定 basetemp：pytest 默认会在系统临时目录里滚动清理旧的 pytest-N 目录，
    # 在受限/加固环境（安全软件、沙箱）下这次批量删除可能被拦截，进而污染退出码。
    basetemp = Path(tempfile.gettempdir()) / ("sfd-pytest-%d" % os.getpid())
    rc = subprocess.call([py, "-m", "pytest", "-q", "--basetemp=%s" % basetemp],
                         cwd=str(ROOT))
    if rc != 0:
        raise BuildError("测试未通过（pytest 退出码 %d），已中止构建" % rc, 3)
    log("· 测试通过，继续打包")


def run(args: argparse.Namespace) -> int:
    started = time.time()
    if not getattr(args, "no_tests", False):
        run_test_gate()
    step("1/9 读取版本真源")
    ns = load_version_namespace()
    version = str(ns["__version__"])
    if args.version and args.version != version:
        raise BuildError("传入 --version %s 与 app/version.py 的 %s 不一致；"
                         "版本号只允许在 version.py 里改" % (args.version, version), 2)
    build_date = datetime.now().strftime("%Y-%m-%d")
    build_ts = datetime.now().astimezone().isoformat(timespec="seconds")
    log("· 版本 %s（VERSION_TUPLE=%s）" % (version, ns["VERSION_TUPLE"]))
    log("· 构建日期 %s" % build_date)

    ledger = load_ledger()
    prev, reason = check_version_monotonic(version, ledger)
    log("· 单调性检查通过：%s" % reason)

    node_exe = resolve_node_exe()
    log("· node.exe = %s（%.1f MiB）" % (node_exe, mb(node_exe.stat().st_size)))

    step("2/9 生成构建期文件（version_info.txt / build_info.json / 使用说明）")
    GEN.mkdir(parents=True, exist_ok=True)
    info_path = gen_version_info(version, GEN / "version_info.txt")
    log("· 已生成 %s" % info_path.relative_to(ROOT))
    (GEN / "build_info.json").write_text(
        json.dumps(build_info_payload(version, build_date, [], build_ts=build_ts),
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readme_pass1 = render_readme(version, build_date)
    (GEN / "使用说明.txt").write_text(readme_pass1, encoding="utf-8")
    log("· 已渲染 使用说明（体积/启动耗时待回填）")
    spec_check_no_absolute_paths()
    log("· spec 绝对路径检查通过")

    if args.single_file:
        step("3/9 构建单文件 exe（调试用，不写 发布/）")
        exe = run_pyinstaller(version, node_exe, single_file=True)
        log("· 单文件产物：%s（%.1f MiB，实验性，不进入发布流程）" % (exe, mb(dir_bytes(exe))))
        return 0

    step("3/9 PyInstaller 构建 onedir")
    onedir = run_pyinstaller(version, node_exe)
    log("· 产物目录：%s（%.1f MiB）" % (onedir, mb(dir_bytes(onedir))))
    exe = onedir / EXE_NAME

    step("3.5/9 构建内置高级转换 worker")
    worker_exe = build_advanced_worker()
    worker_dest = onedir / "_internal" / "advanced" / "worker"
    if worker_dest.exists():
        shutil.rmtree(worker_dest)
    shutil.copytree(worker_exe.parent, worker_dest)
    log("· 已内置 worker：%s（%.1f MiB）"
        % (worker_dest, mb(dir_bytes(worker_dest))))

    step("4/9 版本一致性（pefile 读 exe 属性）")
    file_ver = read_exe_version(exe)
    want = tuple(_parse3(version)) + (0,)
    if file_ver[:3] != want[:3]:
        raise BuildError("exe FileVersion=%s 与 version.py 的 %s 不一致" %
                         (file_ver, want), 5)
    log("· exe FileVersion = %s（与 version.py 一致）" % ".".join(str(x) for x in file_ver))

    step("5/9 瘦身（build/slim.py，守卫不通过就不删）")
    sys.path.insert(0, str(BUILD))
    import slim as slim_mod
    slimmer = slim_mod.Slimmer()
    removed = slimmer.apply(onedir, keep_legacy=args.keep_legacy_decrypt, dry_run=False)
    if removed:
        for r in removed:
            log("· 删除 [%s] %s  %.2f MiB" % (r.rule_id, r.rel_path, mb(r.bytes)))
        log("· 合计释放 %.2f MiB" % mb(sum(r.bytes for r in removed)))
    else:
        log("· 无可删项")
    log("· 瘦身后 onedir = %.1f MiB" % mb(dir_bytes(onedir)))

    step("6/9 写入包内元文件")
    shutil.copy2(CHANGELOG, onedir / "_internal" / "CHANGELOG.md")
    slim_ids = [r.rule_id for r in removed]
    onedir_mb = mb(dir_bytes(onedir))
    (onedir / "_internal" / "build_info.json").write_text(
        json.dumps(build_info_payload(version, build_date, slim_ids, onedir_mb,
                                      build_ts=build_ts),
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_version_json(onedir / "version.json", version, build_date,
                       "v%s：%s" % (version, changelog_headline(version)),
                       commit=git_commit())
    # MIT 要求随副本分发许可声明：放到发布包顶层，用户解压就能看到（SFD-008）
    for extra in (ROOT / "LICENSE", ROOT / "THIRD-PARTY-NOTICES.md"):
        if extra.is_file():
            shutil.copy2(extra, onedir / extra.name)
    log("· 已写入 _internal/CHANGELOG.md、_internal/build_info.json、version.json、"
        "LICENSE、THIRD-PARTY-NOTICES.md")

    readme_final = render_readme(version, build_date, onedir_mb=onedir_mb,
                                 startup_sec=None)
    (onedir / "使用说明.txt").write_text(readme_final, encoding="utf-8")

    step("7/9 exe 自证（--version / --env / --selftest×3）")
    probe = probe_exe(exe, version)
    # 用实测启动耗时回填使用说明
    readme_final = render_readme(version, build_date, onedir_mb=onedir_mb,
                                 startup_sec=probe["startup_median"])
    (onedir / "使用说明.txt").write_text(readme_final, encoding="utf-8")

    verify_package(onedir, version)
    log("· 包内检查通过（无 config.json / 无 psutil / CHANGELOG 在位）")

    step("8/9 体积门禁 + 打包")
    onedir_bytes = dir_bytes(onedir)
    components = measure_components(onedir)
    warnings = check_sizes(onedir_bytes, components, prev)
    for w in warnings:
        log("· ⚠ 组件增大告警：" + w)
    log("· onedir %.1f MiB / 上限 %d MiB" % (mb(onedir_bytes), ONEDIR_LIMIT // 1024 // 1024))

    zip_path = RELEASE / ("%s_v%s.zip" % (ONEDIR_NAME, version))
    zip_bytes = make_zip(onedir, zip_path)
    zip_files = len(zipfile.ZipFile(zip_path).namelist())
    log("· 发布物：%s（%.1f MB，%d 个条目）" % (zip_path, mb(zip_bytes), zip_files))

    step("9/9 记账 + 报告")
    elapsed = time.time() - started
    record = {
        "version": version,
        "build_date": build_date,
        "onedir_bytes": onedir_bytes,
        "zip_bytes": zip_bytes,
        "startup_sec": round(probe["startup_median"], 2),
        "zip_files": zip_files,
        "slim_rules": slim_ids,
        "decrypt_backend": probe["backend"],
        "components": components,
    }
    write_ledger(ledger, record)
    log("· 已追加 build/sizes.json（v%s）" % version)

    report = write_report(version, build_date, onedir_bytes, zip_bytes, components,
                          prev, removed, probe, warnings, zip_files, elapsed)
    log("· 已生成 %s" % report.relative_to(ROOT))

    log("")
    log("-" * 74)
    log("构建成功：v%s" % version)
    log("  发布物  %s（%.1f MB）" % (zip_path.name, mb(zip_bytes)))
    log("  onedir  %.1f MiB（上一版 %s）" %
        (mb(onedir_bytes), ("%.1f MiB" % mb(prev["onedir_bytes"]))
         if prev and prev.get("onedir_bytes") else "-"))
    log("  瘦身    %.2f MiB（%d 条规则）" %
        (mb(sum(r.bytes for r in removed)), len(removed)))
    log("  启动    %.2f s（--selftest 中位）｜解密后端 %s" %
        (probe["startup_median"], probe["backend"]))
    log("-" * 74)

    # 收尾：自动清理构建中间产物与旧版本发布物（tools/cleanup.py，零风险）
    if not getattr(args, "no_clean", False):
        step("9/9+ 构建后自动清理")
        rc = subprocess.call([sys.executable, str(ROOT / "tools" / "cleanup.py")])
        if rc != 0:
            warnings.append("构建后清理未完全成功（见 tools/cleanup.py 输出）")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Sketchfab 下载器一键构建")
    parser.add_argument("--version", help="显式指定版本（必须与 app/version.py 一致）")
    parser.add_argument("--single-file", action="store_true",
                        help="构建实验性单文件 exe（不写入 发布/）")
    parser.add_argument("--keep-legacy-decrypt", action="store_true",
                        help="保留 binzDecrypt.exe（S6，默认删除：该二进制来源无明确许可）")
    parser.add_argument("--no-clean", action="store_true",
                        help="跳过构建后的自动清理（默认构建结束即清理中间产物）")
    parser.add_argument("--no-tests", action="store_true",
                        help="跳过发布门禁（pytest）。正常发版不要用")
    args = parser.parse_args(argv)

    ensure_build_python(argv)
    try:
        return run(args)
    except BuildError as e:
        log("")
        log("构建失败：" + str(e))
        log("（退出码 %d）" % e.code)
        return e.code


if __name__ == "__main__":
    raise SystemExit(main())
