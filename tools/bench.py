# -*- coding: utf-8 -*-
"""
基线测量工具（M3 / M4 / R5）——**仅开发用，绝不进发布包**。

为什么不会进包：PyInstaller 从入口 ``app/sketchfab_gui.py`` 出发做静态依赖分析，
``tools/bench.py`` 既不在 ``pathex``、也不在 ``Analysis.scripts``、更不被 GUI import，
所以 ``psutil`` 不可能被收进包里；``build/build.py`` 还会断言 ``_internal`` 下没有
``psutil*``。

用法：

    python tools/bench.py --startup                  # 只测启动（exe --selftest ×3）
    python tools/bench.py --model p9HSv              # 测单个模型
    python tools/bench.py --all                      # 按 docs/regression-models.md 全跑
    python tools/bench.py --all --mem-tool ctypes    # 没装 psutil 时的纯标准库方案

产物：
    docs/benchmark-v1.1.md        汇总表（启动表 + 6 行模型表 + 并发建议）
    docs/bench-raw/<uid>.json     原始采样序列（可复查）
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
RAW = DOCS / "bench-raw"
MODELS_MD = DOCS / "regression-models.md"
BENCH_MD = DOCS / "benchmark-v1.1.md"

DEFAULT_EXE = ROOT / "build" / "dist_dir" / "Sketchfab下载器" / "Sketchfab下载器.exe"
SAMPLE_INTERVAL = 0.1          # 100 ms
UID_RE = re.compile(r"[0-9a-f]{32}")
SHORT_RE = re.compile(r"skfb\.ly/([A-Za-z0-9]+)")

# 日志里的后端标记（与 dist_engine/main.py 的实际输出一致）
BACKEND_MARKERS = (
    ("decrypt: WASM", "wasm"),
    ("decrypt: legacy binzDecrypt.exe", "legacy"),
)


# ---------------------------------------------------------------------------
# 内存采样：两种实现
# ---------------------------------------------------------------------------
class PsutilSampler:
    """首选实现：psutil 遍历进程树累加 RSS。"""

    name = "psutil"

    def __init__(self) -> None:
        import psutil  # 延迟导入，保证没装时能给出清晰提示
        self.psutil = psutil

    def tree_rss(self, pid: int) -> Tuple[int, int]:
        """返回 (进程树总 RSS 字节, node.exe 的 RSS 字节)。"""
        psutil = self.psutil
        try:
            root = psutil.Process(pid)
            procs = [root] + root.children(recursive=True)
        except Exception:
            return 0, 0
        total = 0
        node = 0
        for p in procs:
            try:
                rss = p.memory_info().rss
            except Exception:
                continue
            total += rss
            try:
                if (p.name() or "").lower() == "node.exe":
                    node += rss
            except Exception:
                pass
        return total, node


class CtypesSampler:
    """
    兜底实现：纯标准库（Toolhelp32Snapshot + GetProcessMemoryInfo）。

    沙箱/离线环境装不了 psutil 时用 ``--mem-tool ctypes`` 切到这条路。
    """

    name = "ctypes"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        self.PMC = PROCESS_MEMORY_COUNTERS
        self.k32 = k32
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE

    def _snapshot(self) -> List[Tuple[int, int, str]]:
        """枚举所有进程：[(pid, ppid, exe_name)]。"""
        TH32CS_SNAPPROCESS = 0x00000002
        INVALID = self.ctypes.c_void_p(-1).value
        wintypes = self.wintypes

        class PROCESSENTRY32(self.ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.CHAR * 260),
            ]

        snap = self.k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID:
            return []
        out: List[Tuple[int, int, str]] = []
        entry = PROCESSENTRY32()
        entry.dwSize = self.ctypes.sizeof(PROCESSENTRY32)
        ok = self.k32.Process32First(snap, self.ctypes.byref(entry))
        while ok:
            name = entry.szExeFile.decode("mbcs", "replace")
            out.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), name))
            ok = self.k32.Process32Next(snap, self.ctypes.byref(entry))
        self.k32.CloseHandle(snap)
        return out

    def _rss(self, pid: int) -> int:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = self.k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return 0
        try:
            counters = self.PMC()
            counters.cb = self.ctypes.sizeof(self.PMC)
            if self.k32.GetProcessMemoryInfo(handle, self.ctypes.byref(counters),
                                             counters.cb):
                return int(counters.WorkingSetSize)
            return 0
        finally:
            self.k32.CloseHandle(handle)

    def tree_rss(self, pid: int) -> Tuple[int, int]:
        procs = self._snapshot()
        children: Dict[int, List[int]] = {}
        names: Dict[int, str] = {}
        for ppid, parent, name in procs:
            names[ppid] = name
            children.setdefault(parent, []).append(ppid)

        stack = [pid]
        seen = set()
        total = 0
        node = 0
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            rss = self._rss(cur)
            total += rss
            if names.get(cur, "").lower() == "node.exe":
                node += rss
            stack.extend(children.get(cur, []))
        return total, node


def make_sampler(kind: str):
    """按 ``--mem-tool`` 造采样器（psutil 缺失时自动降级并提示）。"""
    if kind in ("auto", "psutil"):
        try:
            return PsutilSampler()
        except Exception as e:
            if kind == "psutil":
                raise SystemExit("psutil 不可用：%s\n（可改用 --mem-tool ctypes）" % e)
            print("· psutil 不可用（%s），自动降级到 ctypes 实现" % e)
    return CtypesSampler()


# ---------------------------------------------------------------------------
# 测量
# ---------------------------------------------------------------------------
def find_exe(explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit("找不到 exe：%s" % p)
        return p
    if DEFAULT_EXE.is_file():
        return DEFAULT_EXE
    raise SystemExit("找不到默认 exe，请用 --exe 指定：%s" % DEFAULT_EXE)


def run_startup(exe: Path, times: int = 3) -> dict:
    """``exe --selftest`` ×N，取中位/最小/最大（与 v1.0.0 同口径）。"""
    samples: List[float] = []
    for i in range(times):
        started = time.perf_counter()
        proc = subprocess.run([str(exe), "--selftest"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=300)
        elapsed = time.perf_counter() - started
        ok = "SELFTEST_OK" in (proc.stdout or "")
        print("  · 第 %d 次：%.2f s %s" % (i + 1, elapsed, "" if ok else "（未输出 SELFTEST_OK）"))
        if not ok:
            raise SystemExit("--selftest 失败，先修好再谈基线")
        samples.append(elapsed)
    return {
        "samples": samples,
        "min": min(samples),
        "median": statistics.median(samples),
        "max": max(samples),
    }


def sample_process(proc: subprocess.Popen, sampler) -> dict:
    """在子进程运行期间每 100 ms 采样一次进程树 RSS。"""
    samples: List[dict] = []
    stop = threading.Event()

    def loop() -> None:
        t0 = time.perf_counter()
        while not stop.is_set():
            total, node = sampler.tree_rss(proc.pid)
            if total > 0:
                samples.append({"t": round(time.perf_counter() - t0, 3),
                                "rss": total, "node": node})
            stop.wait(SAMPLE_INTERVAL)

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    lines: List[str] = []
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
                print("    " + line.rstrip())
    finally:
        proc.wait()
        stop.set()
        th.join(timeout=2)

    peak = max((s["rss"] for s in samples), default=0)
    peak_node = max((s["node"] for s in samples), default=0)
    return {"samples": samples, "peak_rss": peak, "peak_node": peak_node,
            "returncode": proc.returncode, "lines": lines}


def parse_backend(lines: Sequence[str]) -> str:
    """从日志里认出实际走的解密后端（这一列是 v1.2 决策的证据）。"""
    for line in lines:
        for marker, name in BACKEND_MARKERS:
            if marker in line:
                return name
    return "unknown"


def parse_counts(lines: Sequence[str]) -> Tuple[Optional[int], Optional[int]]:
    """从 `DONE ... meshes=N images=M` 里抓结构计数。"""
    meshes = images = None
    for line in lines:
        m = re.search(r"meshes=(\d+)", line)
        if m:
            meshes = int(m.group(1))
        m = re.search(r"images=(\d+)", line)
        if m:
            images = int(m.group(1))
    return meshes, images


def run_model(exe: Path, target: str, out_dir: Path, sampler) -> dict:
    """跑一个模型的完整下载 + 转换，返回测量记录。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p for p in out_dir.rglob("*.glb")}
    started = time.perf_counter()
    env_note = ""
    proc = subprocess.Popen([str(exe), "--run", target, "--out", str(out_dir), "--conc", "1"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    result = sample_process(proc, sampler)
    total_sec = time.perf_counter() - started

    glbs = [p for p in out_dir.rglob("*.glb") if p not in before]
    glb = max(glbs, key=lambda p: p.stat().st_size) if glbs else None
    meshes, images = parse_counts(result["lines"])
    backend = parse_backend(result["lines"])
    return {
        "target": target,
        "peak_rss_mb": result["peak_rss"] / 1024 / 1024,
        "peak_node_mb": result["peak_node"] / 1024 / 1024,
        "seconds": total_sec,
        "glb_mb": (glb.stat().st_size / 1024 / 1024) if glb else 0.0,
        "glb_path": str(glb) if glb else "",
        "glb_ok": bool(glb and glb.stat().st_size > 1024 * 1024),
        "meshes": meshes if meshes is not None else "-",
        "images": images if images is not None else "-",
        "backend": backend,
        "returncode": result["returncode"],
        "env_note": env_note,
        "samples": result["samples"],
        "tail": result["lines"][-40:],
    }


def read_models(path: Path = MODELS_MD) -> List[dict]:
    """解析 docs/regression-models.md 的表格行（``#`` 列 / 类别 / UID 或链接 / 关注点）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: List[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        if cells[0] in ("#", "") or set(cells[0]) <= set("-: "):
            continue
        raw = cells[2]
        uid = ""
        m = UID_RE.search(raw)
        if m:
            uid = m.group(0)
        else:
            m = SHORT_RE.search(raw)
            if m:
                uid = m.group(1)
        rows.append({
            "idx": cells[0],
            "category": cells[1],
            "target": raw,
            "uid": uid,
            "focus": cells[3],
            "pending": (not uid) or ("待" in raw),
        })
    return rows


def write_markdown(records: List[dict], startup: Optional[dict],
                   sampler_name: str, path: Path = BENCH_MD) -> Path:
    """产出 docs/benchmark-v1.1.md（6 行模型表 + 启动表 + 并发建议）。"""
    lines: List[str] = []
    lines.append("# 基线测量 · v1.1")
    lines.append("")
    lines.append("> 工具：`tools/bench.py`（dev-only，不进包）｜采样：每 100 ms 累加进程树 RSS")
    lines.append("> 测量时间：%s｜内存工具：%s｜并发：1（排除相互干扰）" %
                 (datetime.now().strftime("%Y-%m-%d %H:%M"), sampler_name))
    lines.append("")
    if startup:
        lines.append("## 1. 冷启动（`--selftest` 代理指标，与 v1.0.0 同口径）")
        lines.append("")
        lines.append("| 次数 | 耗时 (s) |")
        lines.append("|---|---|")
        for i, t in enumerate(startup["samples"], start=1):
            lines.append("| %d | %.2f |" % (i, t))
        lines.append("| **中位** | **%.2f** |" % startup["median"])
        lines.append("| 最小 / 最大 | %.2f / %.2f |" % (startup["min"], startup["max"]))
        lines.append("")
        lines.append("M3 目标 ≤ 1.7 s：%s" % ("PASS" if startup["median"] <= 1.7 else "未达标"))
        lines.append("")
    lines.append("## 2. 模型转换基线")
    lines.append("")
    lines.append("| # | 类别 | UID | 峰值 RSS (MB) | 其中 node.exe (MB) | 总耗时 (s) "
                 "| GLB (MB) | meshes | images | 解密后端 | GLB 成功 |")
    lines.append("|---|------|-----|--------------|-------------------|-----------|"
                 "---------|--------|--------|----------|----------|")
    for r in records:
        lines.append("| %s | %s | %s | %.0f | %.0f | %.1f | %.2f | %s | %s | %s | %s |" % (
            r["idx"], r["category"], r["uid"],
            r["peak_rss_mb"], r["peak_node_mb"], r["seconds"], r["glb_mb"],
            r["meshes"], r["images"], r["backend"],
            "是" if r["glb_ok"] else "否"))
    lines.append("")
    ok = [r for r in records if r["glb_ok"]]
    lines.append("GLB 成功率：%d/%d（M6 要求 100%%）" % (len(ok), len(records)))
    lines.append("")
    if records:
        worst = max(records, key=lambda r: r["peak_rss_mb"])
        lines.append("## 3. 并发建议（R12：有条件才改）")
        lines.append("")
        lines.append("当前默认并发 = 3。峰值最高的样本是 `%s`（%.0f MB）。" % (
            worst["uid"], worst["peak_rss_mb"]))
        lines.append("")
        lines.append("改默认值的门槛（架构设计第 8 节）：conc=1 比 conc=3 峰值低 >25% 且耗时"
                     "增加 <20% 才改，否则保持 3。**本次仅记录 conc=1 的单模型数据，"
                     "如需决策请补测 conc=3 的对照样本。**")
        lines.append("")
    lines.append("## 4. 原始数据")
    lines.append("")
    lines.append("每个模型的逐 100 ms 采样序列在 `docs/bench-raw/<uid>.json`。")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sketchfab 下载器基线测量（dev-only）")
    parser.add_argument("--exe", help="目标 exe（默认 build/dist_dir/.../Sketchfab下载器.exe）")
    parser.add_argument("--startup", action="store_true", help="只测启动耗时")
    parser.add_argument("--model", help="测单个模型（32 位 UID 或 skfb.ly 短码）")
    parser.add_argument("--all", action="store_true", help="跑 docs/regression-models.md 全表")
    parser.add_argument("--out", help="模型输出目录（默认临时目录）")
    parser.add_argument("--mem-tool", default="auto", choices=("auto", "psutil", "ctypes"),
                        help="内存采样实现（默认 auto）")
    parser.add_argument("--times", type=int, default=3, help="启动测量次数（默认 3）")
    args = parser.parse_args(argv)

    exe = find_exe(args.exe)
    print("· 目标 exe：%s" % exe)
    sampler = make_sampler(args.mem_tool)
    print("· 采样实现：%s" % sampler.name)

    startup = None
    if args.startup or args.all or not args.model:
        print("· 测启动（--selftest ×%d）" % args.times)
        startup = run_startup(exe, args.times)
        print("· 启动中位：%.2f s（最小 %.2f）" % (startup["median"], startup["min"]))
        if args.startup and not args.all and not args.model:
            out = write_markdown([], startup, sampler.name)
            print("· 已写 %s" % out)
            return 0

    targets: List[dict] = []
    if args.model:
        targets = [{"idx": "1", "category": "手动指定", "target": args.model,
                    "uid": args.model, "pending": False}]
    elif args.all:
        rows = read_models()
        if not rows:
            raise SystemExit("读不到 %s 的表格行" % MODELS_MD)
        targets = rows
    else:
        out = write_markdown([], startup, sampler.name)
        print("· 已写 %s（只测了启动；加 --all 或 --model 才跑模型）" % out)
        return 0

    records: List[dict] = []
    RAW.mkdir(parents=True, exist_ok=True)
    for row in targets:
        if row.get("pending"):
            print("· 跳过第 %s 行（%s）：UID 未填 —— 待所有者补全" %
                  (row["idx"], row["category"]))
            continue
        target = row["uid"] or row["target"]
        print("")
        print("· [%s] %s → %s" % (row["idx"], row["category"], target))
        out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="sfbench_"))
        rec = run_model(exe, target, out_dir / str(row["idx"]), sampler)
        rec.update({"idx": row["idx"], "category": row["category"],
                    "uid": row["uid"] or target})
        records.append(rec)
        raw_path = RAW / ("%s.json" % (rec["uid"]))
        raw_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  → 峰值 %.0f MB（node %.0f MB）｜%.1f s｜GLB %s｜后端 %s" % (
            rec["peak_rss_mb"], rec["peak_node_mb"], rec["seconds"],
            ("%.2f MB" % rec["glb_mb"]) if rec["glb_ok"] else "失败", rec["backend"]))

    out = write_markdown(records, startup, sampler.name)
    print("")
    print("· 已写 %s" % out)
    if records:
        bad = [r for r in records if not r["glb_ok"]]
        if bad:
            print("· ⚠ %d 个样本 GLB 未成功：%s" %
                  (len(bad), ", ".join(r["uid"] for r in bad)))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
