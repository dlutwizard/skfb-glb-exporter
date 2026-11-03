# -*- coding: utf-8 -*-
"""
B 组：真实下载回归（M6）执行器 —— 非pytest 脚本，长任务，后台运行。

用法（用 build/venv 的 python，有 psutil）：
    build/venv/Scripts/python.exe tests/qa_download_regress.py \
        --exe "F:/迅雷下载/_qa_v11/Sketchfab下载器/Sketchfab下载器.exe" \
        --out  "F:/迅雷下载/_qa_v11/out" \
        --conc 2 \
        --targets p9HSv,6S7uo,1053729ad9d24d71a578622251f76deb,... \
        --result "F:/迅雷下载/_qa_v11/regress_result.json"

产出：每个模型 → GLB 校验（头/JSON chunk 的 meshes/images/materials/skins）、
日志里的解密后端、峰值 RSS（进程树）、耗时、GLB 大小 → JSON 落盘。
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

BACKEND_MARKERS = (
    ("decrypt: WASM", "wasm"),
    ("decrypt: legacy binzDecrypt.exe", "legacy"),
)


# ---------------------------------------------------------------------------
# 内存采样（psutil 优先，ctypes 兜底）
# ---------------------------------------------------------------------------
def make_sampler():
    try:
        import psutil

        class P:
            name = "psutil"

            def tree_rss(self, pid):
                try:
                    root = psutil.Process(pid)
                    procs = [root] + root.children(recursive=True)
                except Exception:
                    return 0, 0
                total = node = 0
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

        return P()
    except ImportError:
        pass

    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    class C:
        name = "ctypes"

        def _rss(self, pid):
            h = k32.OpenProcess(0x1000, False, pid)
            if not h:
                return 0
            try:
                c = PMC()
                c.cb = ctypes.sizeof(PMC)
                if k32.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                    return int(c.WorkingSetSize)
                return 0
            finally:
                k32.CloseHandle(h)

        def tree_rss(self, pid):
            # 简化：只采样根进程 + 一次性快照父子匹配（引擎子进程均为其子孙）
            TH32CS_SNAPPROCESS = 0x2
            class PE(ctypes.Structure):
                _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                            ("th32ProcessID", wintypes.DWORD),
                            ("th32DefaultHeapID", ctypes.c_void_p),
                            ("th32ModuleID", wintypes.DWORD),
                            ("th32ParentProcessID", wintypes.DWORD),
                            ("dwFlags", wintypes.DWORD),
                            ("szExeFile", wintypes.CHAR * 260)]
            snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            procs = []
            e = PE()
            e.dwSize = ctypes.sizeof(PE)
            if snap and k32.Process32First(snap, ctypes.byref(e)):
                while True:
                    procs.append((int(e.th32ProcessID), int(e.th32ParentProcessID),
                                  e.szExeFile.decode("mbcs", "replace")))
                    if not k32.Process32Next(snap, ctypes.byref(e)):
                        break
                k32.CloseHandle(snap)
            children = {}
            names = {}
            for p, pp, n in procs:
                children.setdefault(pp, []).append(p)
                names[p] = n
            total = node = 0
            stack, seen = [pid], set()
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                total += self._rss(cur)
                if names.get(cur, "").lower() == "node.exe":
                    node += self._rss(cur)
                stack.extend(children.get(cur, []))
            return total, node

    return C()


def parse_glb(path: Path) -> dict:
    """解析 GLB 二进制容器：magic/版本/JSON chunk 里的 meshes/images/materials/skins。"""
    out = {"valid": False, "version": None, "meshes": None, "images": None,
           "materials": None, "skins": None, "animations": None, "error": ""}
    try:
        with open(path, "rb") as fh:
            header = fh.read(20)
            if len(header) < 20:
                out["error"] = "文件过小，不是 GLB"
                return out
            magic, ver, length = struct.unpack("<3I", header[:12])
            if magic != 0x46546C67:               # 'glTF'
                out["error"] = "magic 不合法（非 GLB）"
                return out
            out["version"] = ver
            clen, ctype = struct.unpack("<2I", header[12:20])
            if ctype != 0x4E4F534A:               # 'JSON'
                out["error"] = "第一个 chunk 不是 JSON"
                return out
            js = json.loads(fh.read(clen).decode("utf-8", "replace"))
        out["meshes"] = len(js.get("meshes", []))
        out["images"] = len(js.get("images", []))
        out["materials"] = len(js.get("materials", []))
        out["skins"] = len(js.get("skins", []))
        out["animations"] = len(js.get("animations", []))
        out["valid"] = True
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    return out


def _slug(target: str) -> str:
    """把 target（可能是完整 URL / 短链）压成合法目录名。

    例：``https://skfb.ly/p9HSv`` → ``skfb.ly_p9HSv``
    """
    s = re.sub(r"^https?://", "", target.strip(), flags=re.I)
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", s).strip("._-")
    return (s or "target")[:60]


def run_one(exe: Path, target: str, out_root: Path, sampler, conc: int,
            timeout: float, launcher=None) -> dict:
    out_dir = out_root / _slug(target)
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p for p in out_dir.rglob("*.glb")}
    lines = []
    stop = threading.Event()
    samples = []

    def loop():
        while not stop.is_set():
            total, node = sampler.tree_rss(proc.pid)
            if total > 0:
                samples.append({"t": round(time.perf_counter() - t0, 2),
                                "rss": total, "node": node})
            stop.wait(0.1)

    t0 = time.perf_counter()
    cmd = ([*launcher] if launcher else []) + [
        str(exe), "--run", target, "--out", str(out_dir), "--conc", str(conc)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", bufsize=1)
    th = threading.Thread(target=loop, daemon=True)
    th.start()
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                lines.append(line)
                print("    | " + line, flush=True)
        proc.wait(timeout=timeout)
    finally:
        stop.set()
        th.join(timeout=2)
    seconds = time.perf_counter() - t0

    glbs = [p for p in out_dir.rglob("*.glb") if p not in before]
    rec = {
        "target": target,
        "returncode": proc.returncode,
        "seconds": round(seconds, 1),
        "peak_rss_mb": round(max((s["rss"] for s in samples), default=0) / 1048576),
        "peak_node_mb": round(max((s["node"] for s in samples), default=0) / 1048576),
        "backend": next((b for m, b in BACKEND_MARKERS
                         if any(m in ln for ln in lines)), "unknown"),
        "glb_files": [str(p) for p in glbs],
        "glb": None,
        "log_meshes": None, "log_images": None,
        "log_tail": lines[-25:],
    }
    for ln in lines:
        m = re.search(r"meshes=(\d+)", ln)
        if m:
            rec["log_meshes"] = int(m.group(1))
        m = re.search(r"images=(\d+)", ln)
        if m:
            rec["log_images"] = int(m.group(1))
    if glbs:
        biggest = max(glbs, key=lambda p: p.stat().st_size)
        g = parse_glb(biggest)
        g["path"] = str(biggest)
        g["size_mb"] = round(biggest.stat().st_size / 1048576, 2)
        rec["glb"] = g
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--conc", type=int, default=2)
    ap.add_argument("--targets", required=True, help="逗号分隔 UID/短码")
    ap.add_argument("--result", required=True)
    ap.add_argument("--timeout-per-model", type=float, default=1800)
    ap.add_argument("--launcher", default="",
                    help="用解释器启动被测脚本时填（如 D:/Anaconda/python.exe）；"
                         "留空 = 直接执行 --exe（打包 exe 的正常用法）")
    args = ap.parse_args(argv)

    exe = Path(args.exe)
    launcher = [args.launcher] if args.launcher else None
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    sampler = make_sampler()
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    print("· exe=%s launcher=%r conc=%d sampler=%s"
          % (exe, args.launcher or None, args.conc, sampler.name), flush=True)
    print("· targets=%r" % targets, flush=True)

    records = []
    for t in targets:
        print("\n=== [%s] 开始 %s ===" % (time.strftime("%H:%M:%S"), t), flush=True)
        rec = run_one(exe, t, out_root, sampler, args.conc,
                      args.timeout_per_model, launcher=launcher)
        ok = bool(rec["glb"] and rec["glb"]["valid"] and rec["glb"]["size_mb"] > 0.1)
        rec["glb_ok"] = ok
        if ok and rec["log_images"] is not None:
            rec["images_match_log"] = (rec["glb"]["images"] == rec["log_images"])
        records.append(rec)
        print("=== [%s] 完成 %s：GLB=%s 后端=%s 峰值=%dMB 耗时=%.1fs ===" % (
            time.strftime("%H:%M:%S"), t,
            ("%.2fMB meshes=%s images=%s skins=%s materials=%s" % (
                rec["glb"]["size_mb"], rec["glb"]["meshes"], rec["glb"]["images"],
                rec["glb"]["skins"], rec["glb"]["materials"])) if ok else "失败",
            rec["backend"], rec["peak_rss_mb"], rec["seconds"]), flush=True)

    result_path = Path(args.result)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    ok_n = sum(1 for r in records if r["glb_ok"])
    print("\n· 结果已写 %s（%d/%d 成功）" % (result_path, ok_n, len(records)))
    return 0 if ok_n == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
