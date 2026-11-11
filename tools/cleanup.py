#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建后自动清理（tools/cleanup.py）。

目的：每次出版本后，把"过程中产生的、可以再生成或已在 GitHub 上有副本"的
中间物删掉，项目里只留：源码 + 文档 + 最新发布物。

清理范围（全部可再生成 / 有异地副本，删除零风险）：
  1. build/work_dir/            PyInstaller 中间产物，重建即得
  2. build/gen/                 构建期生成物
  3. **/__pycache__、*.pyc     Python 字节码缓存（跳过 build/venv）
  4. .pytest_cache/             测试缓存
  5. .sftmp-*、*_old 临时目录   下载/替换过程中的临时目录
  6. build/dist_dir/            未打包的 onedir 产物（zip 里有一模一样的）
  7. 发布/ 里的旧版本 zip        GitHub Release 均有存档，本地只留当前版本

保留：源码、文档、build/build.py 等构建脚本、build/venv（重建环境）、
      发布/当前版本 zip + SHA256SUMS.txt + 说明文本。

用法：
  python tools/cleanup.py                 # 正常清理
  python tools/cleanup.py --keep-dist     # 额外保留 build/dist_dir（如还要拷去别处）
  python tools/cleanup.py --dry-run       # 只列将要删除的内容，不删

退出码：0 = 清理完成（或无可清理）。
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASE = ROOT / "发布"
SKIP_DIRS = {"venv", ".git", "node_modules"}


def dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def mb(n: int) -> str:
    return "%.1f MB" % (n / 1048576.0)


def read_version() -> str:
    """从 app/version.py 里抠出 __version__（不 import，避免依赖）。"""
    text = (ROOT / "app" / "version.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else ""


class Cleaner:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.freed = 0
        self.items = 0

    def drop_dir(self, p: Path, reason: str) -> None:
        if not p.is_dir():
            return
        size = dir_size(p)
        self.freed += size
        self.items += 1
        print("  删目录  %-52s %9s  %s" % (p.relative_to(ROOT), mb(size), reason))
        if not self.dry_run:
            shutil.rmtree(p, ignore_errors=True)

    def drop_file(self, p: Path, reason: str) -> None:
        if not p.is_file():
            return
        try:
            size = p.stat().st_size
        except OSError:
            return
        self.freed += size
        self.items += 1
        print("  删文件  %-52s %9s  %s" % (p.relative_to(ROOT), mb(size), reason))
        if not self.dry_run:
            try:
                p.unlink()
            except OSError as e:
                print("          （删除失败：%s）" % e)

    def purge_caches(self) -> None:
        for cache in ROOT.rglob("__pycache__"):
            if any(part in SKIP_DIRS for part in cache.parts):
                continue
            self.drop_dir(cache, "字节码缓存")
        for cache in ROOT.rglob(".pytest_cache"):
            if any(part in SKIP_DIRS for part in cache.parts):
                continue
            self.drop_dir(cache, "测试缓存")
        for pyc in ROOT.rglob("*.pyc"):
            if any(part in SKIP_DIRS for part in pyc.parts):
                continue
            self.drop_file(pyc, "字节码")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sketchfab下载器 构建后清理")
    ap.add_argument("--keep-dist", action="store_true",
                    help="保留 build/dist_dir（默认删除：zip 里有同样的内容）")
    ap.add_argument("--dry-run", action="store_true", help="只显示，不删除")
    args = ap.parse_args(argv)

    c = Cleaner(dry_run=args.dry_run)
    version = read_version()
    print("清理 Sketchfab下载器 项目（当前版本 v%s%s）"
          % (version or "?", "，试运行" if args.dry_run else ""))

    print("[1/4] 构建中间产物")
    c.drop_dir(ROOT / "build" / "work_dir", "PyInstaller 中间产物")
    c.drop_dir(ROOT / "build" / "advanced_work", "内置 worker 中间产物")
    c.drop_dir(ROOT / "build" / "gen", "构建期生成物")
    if not args.keep_dist:
        c.drop_dir(ROOT / "build" / "dist_dir", "onedir 产物（zip 内含同一份）")

    print("[2/4] 缓存")
    c.purge_caches()

    print("[3/4] 临时目录")
    for pat in (".sftmp-*", "*.tmp"):
        for p in ROOT.glob(pat):
            if p.is_dir():
                c.drop_dir(p, "临时目录")
            else:
                c.drop_file(p, "临时文件")

    print("[4/4] 旧版本发布物（GitHub Release 有存档）")
    if RELEASE.is_dir():
        for z in sorted(RELEASE.glob("Sketchfab下载器_v*.zip")):
            m = re.search(r"_v(.+)\.zip$", z.name)
            if m and version and m.group(1) != version:
                c.drop_file(z, "旧版本 zip，线上有存档")
        # 旧版本的校验和也一并清掉，只留当前版本
        for s in sorted(RELEASE.glob("SHA256SUMS*.txt")):
            if s.name != "SHA256SUMS.txt":
                c.drop_file(s, "旧版本校验和")

    print("-" * 78)
    if c.items == 0:
        print("没有需要清理的内容，项目已经是干净状态。")
    else:
        print("共清理 %d 项，释放 %s%s" % (c.items, mb(c.freed),
                                          "（试运行，未实际删除）" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
