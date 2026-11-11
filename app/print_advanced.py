# -*- coding: utf-8 -*-
"""
高级 3D 打印转换调用层（纯标准库）。

职责边界：
  * 只负责找外部 Python 环境、启动 worker、转发进度、解析结果
  * 真正吃 numpy/scipy/scikit-image/trimesh 的代码在 advanced_print_worker.py
  * 不 import tkinter，可在干净环境里单测

为什么不在发布包里塞 numpy/scipy/trimesh：
  它们加起来 300 MB 以上，而多数用户只需要下载 / 预览。
  高级重建要求本机已有 Anaconda 一类环境；找不到时明确报错，
  并提示改用「基础转换（仅几何）」。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SUPPORTED_INPUTS = (".glb", ".gltf", ".stl", ".obj", ".ply", ".3mf", ".off")
QUALITY_KEYS = ("standard", "fine", "ultra")
QUALITY_LABELS = {"standard": "标准", "fine": "精细", "ultra": "超精细"}

# 拓竹 H2D Pro：350 x 320 x 325 mm
BUILD_VOLUME_MM = (350.0, 320.0, 325.0)
SAFE_MARGIN_MM = 8.0

_PROBE_TIMEOUT = 25
_PROBE_CODE = (
    "import numpy,scipy,skimage,trimesh,sys;"
    "sys.stdout.write(sys.version.split()[0])"
)
_CACHE = {}
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class AdvancedPrintError(Exception):
    """高级打印转换失败。``hint`` 是给用户看的下一步建议。"""

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def bundled_worker():
    """发布包内置的 worker 可执行文件；存在时完全不需要外部 Python。"""
    roots = []
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        roots.append(meipass / "advanced" / "worker")
    else:
        roots.append(Path(__file__).resolve().parent.parent / "build" /
                     "advanced_dist" / "advanced_print_worker")
    for root in roots:
        exe = root / ("advanced_print_worker.exe" if os.name == "nt"
                      else "advanced_print_worker")
        if exe.is_file():
            return exe
    return None


def candidate_pythons(explicit=None):
    """按优先级返回候选解释器路径（不做可用性探测）。"""
    seen = []
    out = []

    def add(value):
        if not value:
            return
        text = str(value).strip().strip('"')
        if not text:
            return
        key = _norm(text) if os.path.isabs(text) else text.lower()
        if key in seen:
            return
        seen.append(key)
        out.append(text)

    add(explicit)
    add(os.environ.get("SFD_ADVANCED_PYTHON"))
    if not getattr(sys, "frozen", False):
        add(sys.executable)

    prefix = os.environ.get("CONDA_PREFIX")
    if prefix:
        add(Path(prefix) / "python.exe")
        add(Path(prefix) / "bin" / "python")

    for path in (r"D:\Anaconda\python.exe", r"D:\Anaconda3\python.exe",
                 r"C:\ProgramData\Anaconda3\python.exe",
                 r"C:\ProgramData\miniconda3\python.exe"):
        add(path)

    home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
    for name in ("anaconda3", "miniconda3", "Anaconda3", "Miniconda3"):
        add(home / name / "python.exe")

    for name in ("python", "python3"):
        add(shutil.which(name))
    return out


def _probe(interpreter):
    try:
        proc = subprocess.run(
            [interpreter, "-c", _PROBE_CODE],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            timeout=_PROBE_TIMEOUT,
            creationflags=_CREATE_NO_WINDOW)
    except FileNotFoundError:
        return False, "解释器不存在"
    except subprocess.TimeoutExpired:
        return False, "探测超时"
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    if proc.returncode == 0:
        return True, (proc.stdout or "").strip()
    lines = [ln.strip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
    return False, lines[-1] if lines else "退出码 %d" % proc.returncode


def find_advanced_python(explicit=None, refresh=False):
    """返回第一个装着全部依赖的解释器；找不到返回 ``None``。"""
    key = explicit or "<auto>"
    if not refresh and key in _CACHE:
        return _CACHE[key] or None
    for interpreter in candidate_pythons(explicit):
        ok, _detail = _probe(interpreter)
        if ok:
            _CACHE[key] = interpreter
            return interpreter
    _CACHE[key] = ""
    return None


def check_environment(explicit=None, refresh=False):
    """给诊断面板 / GUI 用：返回可用性与说明，不抛异常。"""
    bundled = bundled_worker()
    if bundled:
        return {"ok": True, "python": str(bundled), "version": "bundled",
                "detail": "使用包内 worker", "bundled": True}
    interpreter = find_advanced_python(explicit, refresh=refresh)
    if interpreter:
        ok, detail = _probe(interpreter)
        return {"ok": bool(ok), "python": interpreter,
                "version": detail if ok else "", "detail": detail,
                "bundled": False}
    candidates = candidate_pythons(explicit)
    return {"ok": False, "python": None, "version": "", "bundled": False,
            "detail": "已检查 %d 个候选解释器，均缺少依赖" % len(candidates)}


def install_hint():
    return (
        "发布包应自带 worker.exe；若你正在源码环境运行，需要一个带科学计算库的"
        " Python 环境。任选一种：\n"
        "  1) 构建内置 worker：python build/build.py；\n"
        "  2) 安装依赖：pip install numpy scipy scikit-image trimesh；\n"
        "  3) 指定解释器：set SFD_ADVANCED_PYTHON=D:\\Anaconda\\python.exe\n"
        "完成后重试；也可以先用「基础转换（仅几何）」输出 STL / 3MF。"
    )


def worker_script():
    names = ("advanced_print_worker.py",)
    roots = []
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        roots.extend([meipass / "advanced", meipass, Path(sys.executable).parent / "advanced"])
    else:
        roots.append(Path(__file__).resolve().parent)
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                return candidate
    raise AdvancedPrintError(
        "找不到高级转换 worker（advanced_print_worker.py）",
        hint="发布包可能不完整，请重新下载完整压缩包。")


def resolve_target_length(target_length_mm, auto_fit=False,
                          build_volume=BUILD_VOLUME_MM):
    """把用户输入换算成可以真正放进打印机的成品最长边。"""
    limit = max(10.0, min(build_volume) - SAFE_MARGIN_MM)
    if auto_fit:
        return round(limit, 3)
    try:
        value = float(target_length_mm)
    except (TypeError, ValueError):
        value = 244.0
    return round(max(5.0, min(value, limit)), 3)


def output_paths(source, outdir=None):
    """返回 (stl, 3mf)。绝不覆盖源文件本身。"""
    src = Path(source)
    out = Path(outdir) if outdir else src.parent
    stem = src.stem
    if src.suffix.lower() in (".stl", ".3mf"):
        stem += "_print"
    stl = out / (stem + ".stl")
    mf3 = out / (stem + ".3mf")
    if stl.resolve() == src.resolve():
        stl = out / (stem + "_print.stl")
    if mf3.resolve() == src.resolve():
        mf3 = out / (stem + "_print.3mf")
    return stl, mf3


def _format_progress(message, progress):
    if progress is None:
        return message
    try:
        pct = max(0, min(100, int(round(float(progress) * 100))))
    except (TypeError, ValueError):
        return message
    return "[%3d%%] %s" % (pct, message)


def convert(source, outdir=None, target_length_mm=244.0, auto_fit=False,
            quality="fine", python_exe=None, log=None, timeout=1800,
            want_3mf=True):
    """
    把一个模型重建为水密打印件。

    返回 worker 的报告字典（见 advanced_print_worker.py）。
    失败统一抛 :class:`AdvancedPrintError`，``hint`` 里带排查建议。
    """
    src = Path(source)
    if not src.is_file():
        raise AdvancedPrintError("模型文件不存在：%s" % src)
    if src.suffix.lower() not in SUPPORTED_INPUTS:
        raise AdvancedPrintError(
            "不支持的输入格式：%s" % src.suffix,
            hint="支持：%s" % " / ".join(SUPPORTED_INPUTS))
    if quality not in QUALITY_KEYS:
        quality = "fine"

    bundled = bundled_worker()
    interpreter = None
    worker = None
    if not bundled:
        interpreter = find_advanced_python(python_exe)
        if not interpreter:
            raise AdvancedPrintError("未找到可用的高级打印环境",
                                     hint=install_hint())
        worker = worker_script()

    out = Path(outdir) if outdir else src.parent
    out.mkdir(parents=True, exist_ok=True)
    stl_path, _mf3_path = output_paths(src, out)
    target = resolve_target_length(target_length_mm, auto_fit=auto_fit)

    args = ([str(bundled)] if bundled else [interpreter, str(worker)])
    args += ["--source", str(src),
             "--outdir", str(out),
             "--target-length", "%.4f" % target,
             "--quality", quality,
             "--name", stl_path.stem]
    if not want_3mf:
        args.append("--no-3mf")

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_CREATE_NO_WINDOW, env=env)
    except Exception as exc:
        raise AdvancedPrintError(
            "启动高级转换进程失败：%s: %s" % (type(exc).__name__, exc),
            hint=install_hint())

    if log:
        log("使用高级环境：%s" % (bundled or interpreter))
        log("体素重建目标：最长边 %.1f mm · 质量 %s"
            % (target, QUALITY_LABELS.get(quality, quality)))

    started = time.time()
    report = {}
    error = None
    try:
        for raw in proc.stdout:
            line = (raw or "").strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                if log:
                    log(line)
                continue
            stage = item.get("stage")
            if stage == "log":
                if log:
                    log(_format_progress(item.get("message", ""),
                                         item.get("progress")))
            elif stage == "done":
                report = item.get("report") or {}
            elif stage == "error":
                error = str(item.get("message") or "未知错误")
            if timeout and (time.time() - started) > timeout:
                proc.kill()
                raise AdvancedPrintError(
                    "高级转换超时（%d 秒）" % timeout,
                    hint="可用「标准」质量重试，或换一台更快的机器。")
    finally:
        try:
            code = proc.wait(timeout=30)
        except Exception:
            proc.kill()
            code = -1
        try:
            proc.stdout.close()
        except Exception:
            pass

    if error:
        raise AdvancedPrintError(error, hint=install_hint())
    if code != 0:
        raise AdvancedPrintError("高级转换进程异常退出（代码 %d）" % code)
    if not report:
        raise AdvancedPrintError("高级转换没有返回结果报告")
    return report


def describe_for_log(report):
    """把报告转成适合日志面板的多行文本（纯函数，便于单测）。"""
    lines = []
    lines.append("水密重建完成：%s 面 / %s 个封闭实体"
                 % (report.get("facets", "?"), report.get("components", "?")))
    bbox = report.get("bbox_mm") or []
    if len(bbox) == 3:
        lines.append("成品尺寸：%.2f x %.2f x %.2f mm" % tuple(bbox))
    if report.get("watertight"):
        lines.append("水密校验：通过（边界边 %d / 非流形边 %d）"
                     % (report.get("boundary_edges", 0),
                        report.get("nonmanifold_edges", 0)))
    else:
        lines.append("水密校验：未通过")
    lines.append("体积 %.1f cm3 · PLA 实心约 %.0f g · 体素 %.2f mm"
                 % (report.get("volume_cm3", 0.0),
                    report.get("grams_estimate_pla", 0.0),
                    report.get("pitch_mm", 0.0)))
    if report.get("dropped_components"):
        lines.append("已过滤 %d 个过小/非封闭碎件" % report["dropped_components"])
    lines.append("源模型 %d 面 -> 成品 %d 面，用时 %.0f s"
                 % (report.get("source_faces", 0), report.get("facets", 0),
                    report.get("elapsed_sec", 0)))
    return lines