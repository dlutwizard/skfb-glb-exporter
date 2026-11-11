# -*- mode: python ; coding: utf-8 -*-
"""高级打印 worker 的独立打包 spec（不依赖用户安装 Python）。

产物：build/advanced_dist/advanced_print_worker/advanced_print_worker.exe
build/build.py 会把它复制到主包 _internal/advanced/worker/。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
APP = os.path.join(ROOT, "app")

a = Analysis(
    [os.path.join(APP, "advanced_print_worker.py")],
    pathex=[APP],
    binaries=[],
    datas=[],
    hiddenimports=[
        "numpy", "numpy.linalg", "numpy.random",
        "scipy", "scipy.ndimage", "scipy.spatial", "scipy.special",
        "skimage", "skimage.measure", "skimage.transform",
        "trimesh", "trimesh.exchange.gltf", "trimesh.exchange.stl",
        "trimesh.exchange.obj", "trimesh.exchange.ply",
        "trimesh.exchange.off", "trimesh.exchange.load",
        "trimesh.visual", "trimesh.transformations",
        "trimesh.ray", "trimesh.ray.ray_triangle",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "pandas", "PyQt5", "PySide2",
        "IPython", "pytest", "lxml", "sphinx", "docutils",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="advanced_print_worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="advanced_print_worker",
)
