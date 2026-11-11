# -*- mode: python ; coding: utf-8 -*-
"""
生产 spec：onedir 绿色版（v1.1 唯一发布形态）。

约定：
  * 一切路径相对 ``SPECPATH``，**不得出现绝对路径**（换机器/换盘也能构建）
  * ``node.exe`` 的位置由 build/build.py 通过环境变量 ``SFD_NODE_EXE`` 传入
  * ``version_info.txt`` / ``build_info.json`` 是构建产物，位于 build/gen/
    （版本号唯一真源是 app/version.py，这里只消费不定义）
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
        (os.path.join(APP, "advanced_print_worker.py"), "advanced"),
        (os.path.join(ROOT, "dist_engine"), "engine"),
        (os.path.join(ROOT, "CHANGELOG.md"), "."),
        (BUILD_INFO, "."),
        (NODE_EXE, "."),
    ],
    hiddenimports=[
        "requests", "urllib3", "certifi", "idna", "charset_normalizer",
        "version", "diagnostics", "update_checker", "sketchfab_search", "ui_theme",
        "image_codec", "print_export", "print_advanced", "preview_web",
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
    [],
    exclude_binaries=True,
    name='Sketchfab下载器',
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Sketchfab下载器',
)
