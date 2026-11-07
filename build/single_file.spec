# -*- mode: python ; coding: utf-8 -*-
"""
实验性单文件 spec（`build/build.py --single-file` 专用，产物不写入 发布/）。

为什么默认不发：单文件版每次启动都要把全部内部文件解压到临时目录，
实测启动 40 s 左右（onedir 1.7 s），只有调试价值。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
APP = os.path.join(ROOT, "app")
GEN = os.path.join(ROOT, "build", "gen")

NODE_EXE = os.environ.get("SFD_NODE_EXE") or os.path.join(ROOT, "node.exe")
VERSION_FILE = os.path.join(GEN, "version_info.txt")
BUILD_INFO = os.path.join(GEN, "build_info.json")

a = Analysis(
    [os.path.join(APP, "sketchfab_gui.py")],
    pathex=[APP],
    binaries=[],
    datas=[
        (os.path.join(APP, "convert.mjs"), "."),
        (os.path.join(APP, "extlib"), "extlib"),
        (os.path.join(APP, "node_modules"), "node_modules"),
        (os.path.join(APP, "icon.ico"), "."),
        (os.path.join(APP, "assets"), "assets"),
        (os.path.join(ROOT, "dist_engine"), "engine"),
        (os.path.join(ROOT, "CHANGELOG.md"), "."),
        (BUILD_INFO, "."),
        (NODE_EXE, "."),
    ],
    hiddenimports=[
        "requests", "urllib3", "certifi", "idna", "charset_normalizer",
        "version", "diagnostics", "update_checker", "sketchfab_search", "ui_theme",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Sketchfab下载器_单文件版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=VERSION_FILE,
    icon=[os.path.join(APP, "icon.ico")],
)
