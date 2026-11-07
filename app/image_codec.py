# -*- coding: utf-8 -*-
"""JPEG → PNG 转码（Windows GDI+，纯标准库，零新增打包依赖）。

Tk 8.6 的 ``PhotoImage`` 只认 PNG / GIF / PPM，而 Sketchfab 的缩略图是
JPEG。往发布包里塞 Pillow 会多出几 MB 并新增一条打包依赖链，这里改为调用
系统自带的 ``gdiplus.dll`` 转一次码：发布包体积不变，也不动既有构建门禁。

设计约束
--------
* 顶层不 import tkinter、不 import 项目其它模块 —— 干净环境可直接 import 单测。
* 非 Windows / GDI+ 不可用时 :func:`is_supported` 返回 False，调用方退回占位图。
* 失败一律抛 :class:`ImageCodecError`，绝不静默返回半张图。
* 临时文件写在系统临时目录并立刻清理；单张图片有体积上限，防异常大图。
"""
import ctypes
import os
import shutil
import sys
import tempfile
import threading

MAX_IMAGE_BYTES = 4 * 1024 * 1024


class ImageCodecError(RuntimeError):
    """GDI+ 转码失败（平台不支持、解码失败或写出失败）。"""


class _StartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint32),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", ctypes.c_int),
        ("SuppressExternalCodecs", ctypes.c_int),
    ]


# PNG encoder CLSID {557CF406-1A04-11D3-9A73-0000F81EF32E}
_PNG_CLSID = (ctypes.c_ubyte * 16)(
    0x06, 0xF4, 0x7C, 0x55, 0x04, 0x1A, 0xD3, 0x11,
    0x9A, 0x73, 0x00, 0x00, 0xF8, 0x1E, 0xF3, 0x2E,
)

_lock = threading.Lock()
_gdiplus = None
_token = None


def _ensure():
    """懒加载 GDI+，进程内只初始化一次；返回已配置好签名的 WinDLL。"""
    global _gdiplus, _token
    if _gdiplus is not None:
        return _gdiplus
    if sys.platform != "win32":
        raise ImageCodecError("GDI+ 转码只在 Windows 上可用")
    with _lock:
        if _gdiplus is not None:
            return _gdiplus
        try:
            g = ctypes.WinDLL("gdiplus")
        except OSError as e:
            raise ImageCodecError("加载 gdiplus.dll 失败：%s" % e)
        g.GdiplusStartup.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(_StartupInput),
            ctypes.c_void_p,
        ]
        g.GdiplusStartup.restype = ctypes.c_int
        g.GdipLoadImageFromFile.argtypes = [
            ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
        g.GdipLoadImageFromFile.restype = ctypes.c_int
        g.GdipSaveImageToFile.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p]
        g.GdipSaveImageToFile.restype = ctypes.c_int
        g.GdipDisposeImage.argtypes = [ctypes.c_void_p]
        g.GdipDisposeImage.restype = ctypes.c_int

        si = _StartupInput()
        si.GdiplusVersion = 1
        tok = ctypes.c_void_p()
        status = g.GdiplusStartup(ctypes.byref(tok), ctypes.byref(si), None)
        if status != 0:
            raise ImageCodecError("GdiplusStartup 失败（status %d）" % status)
        _gdiplus, _token = g, tok
        return g


def is_supported():
    """当前平台能否转码；不抛异常，供 GUI 决定是否直接走占位图。"""
    try:
        _ensure()
        return True
    except Exception:
        return False


def jpeg_to_png(jpeg_bytes):
    """把 JPEG 字节转成 PNG 字节；失败抛 :class:`ImageCodecError`。"""
    if not jpeg_bytes:
        raise ImageCodecError("空的图片数据")
    if len(jpeg_bytes) > MAX_IMAGE_BYTES:
        raise ImageCodecError("图片超过 %d 字节上限" % MAX_IMAGE_BYTES)
    # 先做格式守卫再把数据交给 GDI+：畸形 / 被网络截断的 JPEG 会让
    # GDI+ 抛出 Windows 结构化异常（0xc0000002），Python 层接不住，
    # 在程序里就可能直接崩进程。缩略图来自网络，这一步不能省。
    if len(jpeg_bytes) < 128 or jpeg_bytes[:3] != b"\xff\xd8\xff":
        raise ImageCodecError("不是有效的 JPEG 数据")
    if jpeg_bytes[-2:] != b"\xff\xd9":
        raise ImageCodecError("JPEG 数据不完整（缺少结束标记）")
    g = _ensure()
    td = tempfile.mkdtemp(prefix="sfdthumb-")
    try:
        src = os.path.join(td, "in.jpg")
        dst = os.path.join(td, "out.png")
        with open(src, "wb") as f:
            f.write(jpeg_bytes)
        image = ctypes.c_void_p()
        status = g.GdipLoadImageFromFile(src, ctypes.byref(image))
        if status != 0 or not image:
            raise ImageCodecError("GDI+ 无法解码 JPEG（status %d）" % status)
        try:
            status = g.GdipSaveImageToFile(
                image, dst, ctypes.byref(_PNG_CLSID), None)
            if status != 0:
                raise ImageCodecError("GDI+ 无法写出 PNG（status %d）" % status)
        finally:
            g.GdipDisposeImage(image)
        with open(dst, "rb") as f:
            data = f.read()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ImageCodecError("GDI+ 写出的不是 PNG")
        return data
    except OSError as e:
        raise ImageCodecError("转码临时文件失败：%s" % e)
    finally:
        shutil.rmtree(td, ignore_errors=True)
