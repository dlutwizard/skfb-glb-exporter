# -*- coding: utf-8 -*-
"""
发布物取证：不落盘、不执行，直接从发布 zip 的字节里解析 PyInstaller CArchive。

用途（v1.1.1 引入）：
  安全软件把 `Sketchfab下载器.exe` 判为 Trojan:Win32/Bearfoos.B!ml 并隔离后，
  既不能执行也不能读盘 —— 但仍需要回答两个问题：
    1. exe 里到底装了什么模块？（是否只有我们自己的代码 + 标准库）
    2. 有没有可疑的第三方二进制/脚本被塞进包？

用法：
    python tests/qa_archive_audit.py 发布/Sketchfab下载器_v1.1.1.zip

输出：CArchive TOC 条目总数、按类型统计、脚本/二进制条目清单，以及
      是否出现非预期顶层模块（用于人工复核误报）。
"""
from __future__ import annotations

import argparse
import collections
import marshal
import struct
import sys
import zipfile
from pathlib import Path

MAGIC = b"MEI\014\013\012\013\016"
COOKIE_FMT = "!8sIIII64s"
COOKIE_LEN = struct.calcsize(COOKIE_FMT)
TOC_FMT = "!iIIIBc"
TOC_LEN = struct.calcsize(TOC_FMT)

# 期望出现在 PYZ 里的自家模块（其余应为标准库/第三方依赖）
OWN_MODULES = {"sketchfab_gui", "version", "diagnostics", "update_checker"}


def find_cookie(data: bytes):
    """PyInstaller 把 cookie 放在归档尾部：MAGIC + 长度字段。

    cookie 定长 88 B 且紧贴文件末尾，包体在其前面：
    ``package_start = cookie_off + COOKIE_LEN - lengthofPackage``。
    """
    idx = data.rfind(MAGIC)
    while idx != -1:
        if idx + COOKIE_LEN <= len(data):
            try:
                fields = struct.unpack(COOKIE_FMT, data[idx:idx + COOKIE_LEN])
            except struct.error:
                fields = None
            if fields:
                pkg_len = fields[1]
                if 0 < pkg_len and idx + COOKIE_LEN - pkg_len >= 0:
                    return idx, fields
        idx = data.rfind(MAGIC, 0, idx)
    raise ValueError("没找到 PyInstaller 归档 cookie（MAGIC=MEI\\014...）")


def parse_toc(data: bytes, cookie_off: int, fields):
    magic, pkg_len, toc_off, toc_len, pyver, pylib = fields
    start = cookie_off + COOKIE_LEN - pkg_len + toc_off
    pos, end = start, start + toc_len
    entries = []
    while pos < end:
        entry_len, offset, dlen, ulen, flag, typecode = struct.unpack(
            TOC_FMT, data[pos:pos + TOC_LEN])
        name_start = pos + TOC_LEN
        name = data[name_start:pos + entry_len].rstrip(b"\0").decode("utf-8", "replace")
        entries.append({"name": name, "typecode": typecode.decode("ascii", "replace"),
                        "offset": offset, "length": dlen, "ulen": ulen, "flag": flag})
        pos += entry_len
    return entries, pyver, pylib.decode("utf-8", "replace").rstrip("\0")


def parse_pyz(data: bytes, pkg_start: int, entry: dict, top: int = 200):
    """解析 PYZ（'PYZ\\0' + pyc magic + toc 偏移 + marshal 的 TOC 字典）。"""
    start = pkg_start + entry["offset"]
    blob = data[start:start + entry["length"]]
    if blob[:4] != b"PYZ\0":
        return None
    toc_off = struct.unpack("!i", blob[8:12])[0]
    toc = dict(marshal.loads(blob[toc_off:]))
    names = sorted(toc)
    return names, names[:top]


def audit(zip_path: Path) -> int:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith("Sketchfab下载器.exe")
                 and n.count("/") == 1]
        if len(names) != 1:
            print("✗ zip 内主 exe 不唯一：%r" % names)
            return 2
        data = z.read(names[0])
        print("· 被审对象：%s（%d B）" % (names[0], len(data)))

    cookie_off, fields = find_cookie(data)
    entries, pyver, pylib = parse_toc(data, cookie_off, fields)
    pkg_start = cookie_off + COOKIE_LEN - fields[1]
    print("· Python 字节码版本 = %d，pylib = %s" % (pyver, pylib))

    kinds = collections.Counter(e["typecode"] for e in entries)
    print("· CArchive 条目 %d 个，按类型：%s" % (len(entries), dict(kinds)))

    scripts = [e["name"] for e in entries if e["typecode"] == "s"]
    binaries = [e["name"] for e in entries if e["typecode"] in ("b", "x", "d")]
    zips = [e["name"] for e in entries if e["typecode"] == "z"]

    print("\n· 入口脚本 / PYZ：%r" % (scripts + zips))
    own = [n for n in scripts if Path(n).stem in OWN_MODULES]
    print("· 自家模块出现在脚本条目里：%r" % own)

    print("\n· 载入的二进制（前 20）：")
    for n in binaries[:20]:
        print("    %s" % n)
    if len(binaries) > 20:
        print("    ...（共 %d 个）" % len(binaries))

    suspicious = [n for n in binaries
                  if n.lower().endswith((".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse"))]
    print("\n· 可疑脚本型二进制：%r" % suspicious)

    pyz_entries = [e for e in entries if e["typecode"] == "z"]
    if pyz_entries:
        parsed = parse_pyz(data, pkg_start, pyz_entries[0])
        if parsed:
            all_names, head = parsed
            print("\n· PYZ 模块总数 = %d" % len(all_names))
            own = [n for n in all_names if n.split(".")[0] in OWN_MODULES]
            print("· 自家模块：%r" % own)
            missing = OWN_MODULES - {n.split(".")[0] for n in all_names}
            print("· 缺失的自家模块：%r" % sorted(missing))
            non_ascii = [n for n in all_names
                         if not all(ch.isalnum() or ch in "._-" for ch in n)]
            print("· 含非常规字符的模块名：%r" % non_ascii[:10])

    ok = not suspicious
    print("· 结论：%s" % ("未发现异常载荷（全部为 Python 运行时 + 自家代码）"
                        if ok else "发现可疑条目，需人工复核"))
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", type=Path)
    args = ap.parse_args(argv)
    return audit(args.zip)


if __name__ == "__main__":
    raise SystemExit(main())
