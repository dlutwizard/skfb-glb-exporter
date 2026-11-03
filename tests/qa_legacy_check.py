# -*- coding: utf-8 -*-
"""
D 组：回滚与降级路径执行器（非 pytest，需要真实网络）。

场景 1（引擎层回滚）：把包内 node.exe 临时改名 → 引擎 which("node") 失败 →
  --env 应显示 DECRYPT_BACKEND=legacy，且下载仍可用（binzDecrypt.exe 兜底）。
场景 2（F 盘缺失）：见 tests/test_config.py::test_default_out_dir_without_f_drive。

用法：
    build/venv/Scripts/python.exe tests/qa_legacy_check.py \
        --exe "F:/迅雷下载/_qa_v11/Sketchfab下载器/Sketchfab下载器.exe" \
        --model 6S7uo --out "F:/迅雷下载/_qa_v11/out_legacy"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def run_cmd(exe: Path, *args, timeout=600):
    return subprocess.run([str(exe), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def run_stream(exe: Path, *args, timeout=1200):
    proc = subprocess.Popen([str(exe), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1)
    lines = []
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                lines.append(line)
                print("    | " + line, flush=True)
        proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
    return proc.returncode, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", required=True)
    ap.add_argument("--model", default="6S7uo")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    exe = Path(args.exe)
    res = exe.parent / "_internal"
    node = res / "node.exe"
    node_hidden = res / "node.exe.qa_hidden"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"node_hidden": False, "env_backend": "", "download_ok": False,
              "backend_in_log": "", "glb": None, "restored": False}

    assert node.is_file(), "包内没有 node.exe：%s" % node
    print("· 隐藏包内 node.exe（模拟引擎找不到 Node）", flush=True)
    os.rename(node, node_hidden)
    report["node_hidden"] = True
    try:
        env_proc = run_cmd(exe, "--env")
        env_out = env_proc.stdout + env_proc.stderr
        print(env_out)
        m = re.search(r"DECRYPT_BACKEND=(\w+)", env_out)
        report["env_backend"] = m.group(1) if m else "missing"
        m = re.search(r"NODE_LOOKUP=(.*)", env_out)
        report["env_node_lookup"] = m.group(1).strip() if m else "missing"

        print("\n· legacy 后端下载模型 %s" % args.model, flush=True)
        t0 = time.perf_counter()
        rc, lines = run_stream(exe, "--run", args.model, "--out", str(out), "--conc", "1")
        report["seconds"] = round(time.perf_counter() - t0, 1)
        report["returncode"] = rc
        report["backend_in_log"] = next(
            (b for mk, b in (("decrypt: WASM", "wasm"),
                             ("decrypt: legacy binzDecrypt.exe", "legacy"))
             if any(mk in ln for ln in lines)), "unknown")
        glbs = list(out.rglob("*.glb"))
        if glbs:
            g = max(glbs, key=lambda p: p.stat().st_size)
            report["glb"] = {"path": str(g), "size_mb": round(g.stat().st_size / 1048576, 2)}
        report["download_ok"] = bool(
            glbs and glbs[0].stat().st_size > 1_000_000
            and report["backend_in_log"] == "legacy")
        report["log_tail"] = lines[-20:]
    finally:
        os.rename(node_hidden, node)
        report["restored"] = node.is_file()
        print("\n· node.exe 已恢复：%s" % report["restored"], flush=True)

    result_path = out.parent / "legacy_result.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print("\n· 结果已写 %s" % result_path)
    print("· env_backend=%s log_backend=%s download_ok=%s" % (
        report["env_backend"], report["backend_in_log"], report["download_ok"]))
    return 0 if (report["env_backend"] == "legacy" and report["download_ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
