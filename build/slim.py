# -*- coding: utf-8 -*-
"""
零风险瘦身规则引擎（R6）。

设计原则：**每条删除规则都必须带一个可判定的守卫**，守卫不通过就不删 ——
瘦身是唯一可能把「能用的包」变成「砖头」的操作，所以这里宁可不省字节。

    python build/slim.py <onedir> --dry-run          # 只预演，打印将删清单与守卫结果
    python build/slim.py <onedir>                    # 真删（含 S1~S5）
    python build/slim.py <onedir> --keep-legacy-decrypt  # 保留 S6（默认删除）

退出码：0 = 全部守卫通过；1 = 有守卫失败（此时**一个文件都没删**）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

INTERNAL = "_internal"
MODULES = INTERNAL + "/node_modules"

# ---------------------------------------------------------------------------
# 明确不删清单（PRD 曾把 @img/colour 误列为可删，这里做硬保护）
# ---------------------------------------------------------------------------
FORBIDDEN_PATTERNS: Tuple[str, ...] = (
    MODULES + "/@img/colour",          # sharp/dist/colour.cjs:6 有 require('@img/colour')
    MODULES + "/sharp",                # sharp 运行时依赖链
    MODULES + "/detect-libc",
    MODULES + "/semver",
    MODULES + "/@img/sharp-win32-x64",  # 真正的 native 绑定
    INTERNAL + "/node.exe",            # v1.1 不碰（v1.2/v1.3 目标）
    INTERNAL + "/engine/tools/wasm",   # 删了 WASM 解密就没兜底了
)


def md5_of(path: Path, chunk: int = 1 << 20) -> str:
    """流式计算 MD5（libvips 有 19 MB，别一次读进内存）。"""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def dir_size(path: Path) -> int:
    """目录/文件的字节数；不存在返回 0。"""
    p = Path(path)
    try:
        if p.is_file():
            return p.stat().st_size
        if not p.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(p):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


@dataclass
class SlimRule:
    """
    一条瘦身规则。

    ``guard_md5_of``：非空时，目标文件与它必须 MD5 相等才允许删（重复副本场景）。
    ``require_glob``：非空时，必须存在匹配的文件（说明替代实现到位）。
    ``scan_token``：非空时，在 ``node_modules`` 里做引用扫描，
                    除 ``scan_exclude`` 下的目录外不得有命中（否则说明真有依赖）。
    """

    rule_id: str
    rel_path: str
    kind: str                    # "file" | "dir"
    expect_bytes: int = 0
    default_on: bool = True
    guard_md5_of: str = ""
    require_glob: str = ""
    scan_token: str = ""
    scan_exclude: Sequence[str] = field(default_factory=tuple)
    desc: str = ""

    def target(self, onedir: Path) -> Path:
        return Path(onedir) / self.rel_path

    def applies(self, onedir: Path) -> bool:
        """目标当前是否存在（存在 = 这条规则有活干）。"""
        p = self.target(onedir)
        return p.is_dir() if self.kind == "dir" else p.is_file()


@dataclass
class Removed:
    """一条已删除（或将要删除）的记录。"""

    rule_id: str
    rel_path: str
    bytes: int
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {"rule": self.rule_id, "path": self.rel_path,
                "bytes": self.bytes, "dry_run": self.dry_run}


# ---------------------------------------------------------------------------
# 规则表（对应架构设计第 7 节 S1~S6）
# ---------------------------------------------------------------------------
RULES: List[SlimRule] = [
    SlimRule(
        rule_id="S1",
        rel_path=INTERNAL + "/libvips-42.dll",
        kind="file",
        expect_bytes=19018240,
        guard_md5_of=MODULES + "/@img/sharp-win32-x64/lib/libvips-42.dll",
        desc="顶层 libvips-42.dll 与 @img/sharp-win32-x64/lib 下的副本完全重复",
    ),
    SlimRule(
        rule_id="S2",
        rel_path=INTERNAL + "/libvips-cpp-8.18.7.dll",
        kind="file",
        expect_bytes=333312,
        guard_md5_of=MODULES + "/@img/sharp-win32-x64/lib/libvips-cpp-8.18.7.dll",
        desc="顶层 libvips-cpp dll 与 native 绑定目录下的副本完全重复",
    ),
    SlimRule(
        rule_id="S3",
        rel_path=MODULES + "/@img/sharp-wasm32",
        kind="dir",
        expect_bytes=9325870,
        require_glob=MODULES + "/@img/sharp-win32-x64/lib/sharp-win32-x64-*.node",
        desc="WASM 版 sharp 仅在原生绑定加载失败时才会被 require，win32-x64 在位即永不加载",
    ),
    SlimRule(
        rule_id="S4",
        rel_path=MODULES + "/@emnapi",
        kind="dir",
        expect_bytes=436901,
        scan_token="emnapi",
        scan_exclude=(MODULES + "/@emnapi", MODULES + "/@img/sharp-wasm32"),
        desc="@img/sharp-wasm32 的唯一依赖，删 wasm32 后无人引用",
    ),
    SlimRule(
        rule_id="S5",
        rel_path=MODULES + "/tslib",
        kind="dir",
        expect_bytes=92088,
        scan_token="tslib",
        scan_exclude=(MODULES + "/@emnapi", MODULES + "/@img/sharp-wasm32"),
        desc="只被 @emnapi/runtime 引用",
    ),
    SlimRule(
        rule_id="S6",
        rel_path=INTERNAL + "/engine/tools/binz/binzDecrypt.exe",
        kind="file",
        expect_bytes=18766080,
        default_on=True,         # v1.1.3 起默认删除：该二进制来源无明确许可，不可再分发
        require_glob=INTERNAL + "/engine/tools/wasm/decrypt.wasm",
        desc="legacy 解密后端；来源无许可证，删除后只走 WASM（随包 node.exe 保证可用）",
    ),
]

# 引用扫描时只看这些后缀。**故意不含 .json**：package.json / package-lock.json 里的
# devDependencies 提到 @emnapi 只表示「开发依赖」，不构成运行时 require
# （已核实：sharp/dist/colour.cjs 与 sharp.cjs 都不 require @emnapi 或 tslib）。
_SCAN_SUFFIXES = (".js", ".cjs", ".mjs")


def reference_scan(onedir: Path, token: str,
                   exclude_dirs: Sequence[str] = ()) -> List[str]:
    """
    在 ``node_modules`` 里搜 ``token`` 的文本出现位置（行级）。

    ``exclude_dirs`` 是「允许出现」的目录（相对 onedir），例如 ``@emnapi/**``
    本身就是 tslib 的引用方，不能算命中。
    返回 ``["path:行号", ...]``。
    """
    root = Path(onedir) / MODULES
    if not root.is_dir():
        return []
    excl = {os.path.normcase(os.path.join(str(onedir), e.replace("/", os.sep)))
            for e in exclude_dirs}
    hits: List[str] = []
    needle = token.lower()
    for cur_root, dirs, files in os.walk(root):
        norm_cur = os.path.normcase(cur_root)
        if any(norm_cur == e or norm_cur.startswith(e + os.sep) for e in excl):
            dirs[:] = []
            continue
        for name in files:
            if not name.lower().endswith(_SCAN_SUFFIXES):
                continue
            full = os.path.join(cur_root, name)
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        if needle in line.lower():
                            rel = os.path.relpath(full, str(onedir)).replace(os.sep, "/")
                            hits.append("%s:%d" % (rel, lineno))
                            break
            except OSError:
                continue
    return hits


class Slimmer:
    """规则执行器：先整体校验守卫，再统一删除。"""

    def __init__(self, rules: Optional[Sequence[SlimRule]] = None):
        self.rules = list(rules if rules is not None else RULES)

    # -- 守卫 ---------------------------------------------------------------
    def guard_results(self, onedir: Path, keep_legacy: bool = False) -> List[Tuple[SlimRule, bool, str]]:
        """
        逐条评估守卫，返回 ``[(rule, passed, message)]``。

        只评估「会生效」的规则（目标存在、且被开关启用）。
        """
        onedir = Path(onedir)
        results: List[Tuple[SlimRule, bool, str]] = []
        for rule in self.rules:
            enabled = rule.default_on and not (rule.rule_id == "S6" and keep_legacy)
            if not enabled:
                continue
            if not rule.applies(onedir):
                continue

            # 保护清单：绝不允许规则删掉「明确不删」的东西
            norm = rule.rel_path.replace("\\", "/")
            for guarded in FORBIDDEN_PATTERNS:
                if norm == guarded or norm.startswith(guarded + "/"):
                    results.append((rule, False, "规则命中保护清单：" + guarded))
                    break
            else:
                ok, msg = self._check_one(rule, onedir)
                results.append((rule, ok, msg))
        return results

    def _check_one(self, rule: SlimRule, onedir: Path) -> Tuple[bool, str]:
        target = rule.target(onedir)
        notes: List[str] = []

        # 1) MD5 相等守卫（重复副本）
        if rule.guard_md5_of:
            twin = Path(onedir) / rule.guard_md5_of
            if not twin.is_file():
                return False, "对照副本不存在：" + rule.guard_md5_of
            if rule.kind != "file":
                return False, "MD5 守卫只能用于 file 规则"
            try:
                a = md5_of(target)
                b = md5_of(twin)
            except OSError as e:
                return False, "计算 MD5 失败：%s" % e
            if a != b:
                return False, "MD5 不等（%s vs %s）—— 不是重复副本，拒绝删除" % (a[:8], b[:8])
            notes.append("md5=%s 与副本一致" % a[:8])

        # 2) 替代实现到位守卫
        if rule.require_glob:
            pattern = Path(onedir) / rule.require_glob
            matches = sorted(Path(onedir).glob(rule.require_glob))
            if not matches:
                return False, "替代实现未到位（缺少 %s）" % rule.require_glob
            if rule.rel_path.endswith("binzDecrypt.exe"):
                wasm = Path(onedir) / INTERNAL / "engine" / "tools" / "wasm" / "decrypt.wasm"
                if not wasm.is_file() or wasm.stat().st_size <= 10_000:
                    return False, "WASM 模块不可用（decrypt.wasm 缺失或 <10KB）"
                notes.append("WASM 模块在位（%d B）" % wasm.stat().st_size)
            del pattern
            notes.append("替代实现存在：%d 个匹配" % len(matches))

        # 3) 引用扫描守卫（目标自身不算命中：要么它被整个删掉，要么它引用自己）
        if rule.scan_token:
            exclude = tuple(rule.scan_exclude) + (rule.rel_path,)
            hits = reference_scan(onedir, rule.scan_token, exclude)
            if hits:
                preview = ", ".join(hits[:3])
                return False, "仍有 %d 处引用（%s…）" % (len(hits), preview)
            notes.append("引用扫描 0 命中")

        # 4) 体积参考（不一致只提示，不阻断 —— 不同 PyInstaller 版本可能略有差异）
        size = dir_size(target)
        if rule.expect_bytes and abs(size - rule.expect_bytes) > 64 * 1024:
            notes.append("注意：实测 %d B，与参考 %d B 有差异" % (size, rule.expect_bytes))
        else:
            notes.append("体积 %d B" % size)

        return True, "；".join(notes)

    def assert_guards(self, onedir: Path, keep_legacy: bool = False) -> List[Tuple[SlimRule, str]]:
        """返回失败的 ``[(rule, message)]``；空列表 = 全部通过。"""
        failed = []
        for rule, ok, msg in self.guard_results(onedir, keep_legacy):
            if not ok:
                failed.append((rule, msg))
        return failed

    # -- 执行 ---------------------------------------------------------------
    def apply(self, onedir: Path, keep_legacy: bool = False,
              dry_run: bool = False) -> List[Removed]:
        """
        校验守卫 → 通过后统一删除。**任何一条守卫失败就整体放弃**（一个文件都不删）。
        """
        onedir = Path(onedir)
        failed = self.assert_guards(onedir, keep_legacy)
        if failed:
            detail = "\n".join("  [%s] %s → %s" % (r.rule_id, r.rel_path, m) for r, m in failed)
            raise RuntimeError("瘦身守卫失败，已放弃全部删除：\n" + detail)

        removed: List[Removed] = []
        for rule in self.rules:
            enabled = rule.default_on and not (rule.rule_id == "S6" and keep_legacy)
            if not enabled or not rule.applies(onedir):
                continue
            target = rule.target(onedir)
            size = dir_size(target)
            if not dry_run:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            removed.append(Removed(rule.rule_id, rule.rel_path, size, dry_run))
        return removed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_guard_table(slimmer: Slimmer, onedir: Path, keep_legacy: bool) -> None:
    print("守卫检查（不通过就不删）")
    print("-" * 78)
    for rule, ok, msg in slimmer.guard_results(onedir, keep_legacy):
        flag = "PASS" if ok else "FAIL"
        print("[%s] %-46s %s" % (flag, rule.rel_path, msg))


def main(argv: Optional[Sequence[str]] = None) -> int:
    # 输出里有中文：被 subprocess 捕获时 Windows 默认按 GBK 编码，
    # 调用方按 UTF-8 解码就会看到乱码（SFD-006 的根因之一）。统一强制 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Sketchfab 下载器零风险瘦身")
    parser.add_argument("onedir", help="包根目录（含 Sketchfab下载器.exe 的那个文件夹）")
    parser.add_argument("--dry-run", action="store_true", help="只预演，不真删")
    parser.add_argument("--keep-legacy-decrypt", action="store_true",
                        help="保留 binzDecrypt.exe（S6，默认删除：该二进制来源无明确许可）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出删除清单")
    args = parser.parse_args(argv)

    onedir = Path(args.onedir).resolve()
    if not onedir.is_dir():
        print("目录不存在：%s" % onedir, file=sys.stderr)
        return 2

    slimmer = Slimmer()
    if not args.json:
        _print_guard_table(slimmer, onedir, args.keep_legacy_decrypt)
        print("-" * 78)

    try:
        removed = slimmer.apply(onedir, keep_legacy=args.keep_legacy_decrypt,
                               dry_run=args.dry_run)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    total = sum(r.bytes for r in removed)
    if args.json:
        print(json.dumps({"dry_run": args.dry_run,
                          "removed": [r.to_dict() for r in removed],
                          "total_bytes": total}, ensure_ascii=False, indent=2))
    else:
        verb = "将删除" if args.dry_run else "已删除"
        for r in removed:
            print("%s [%s] %-46s %8.2f MiB" % (verb, r.rule_id, r.rel_path,
                                               r.bytes / 1024 / 1024))
        print("-" * 78)
        print("%s %d 项，合计 %.2f MiB（%.2f MB）" %
              (verb, len(removed), total / 1024 / 1024, total / 1000 / 1000))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
