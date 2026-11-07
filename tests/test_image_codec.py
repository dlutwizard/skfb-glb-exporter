# -*- coding: utf-8 -*-
"""image_codec（GDI+ JPEG→PNG）离线回归：不联网、不依赖 Pillow / tkinter。"""
import base64
import sys
from pathlib import Path

import pytest

import image_codec

ROOT = Path(__file__).resolve().parent.parent

# 2x2 像素 JPEG（base64 内联），GDI+ 能真实解码；测试不依赖图像库也不联网
_TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSopGR8tMC0oMCUoKSj/2wBDAQYHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCj/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDuaKKK/Kz7k//Z"
)

_WIN_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="GDI+ 转码仅 Windows 可用")


def test_module_is_dependency_clean():
    """干净环境也要能 import：顶层不碰 GUI、不碰项目其它模块、不降级 TLS。"""
    import re
    src = (ROOT / "app" / "image_codec.py").read_text(encoding="utf-8")
    # 只看真正的 import 语句，别把 docstring 里的说明文字算进去
    assert not re.search(r"^\s*import tkinter\b", src, re.M)
    assert not re.search(r"^\s*import ui_theme\b", src, re.M)
    assert not re.search(r"^\s*import sketchfab_gui\b", src, re.M)
    assert "CERT_NONE" not in src
    assert "verify=False" not in src
    assert "check_hostname" not in src


@_WIN_ONLY
def test_is_supported_on_windows():
    assert image_codec.is_supported() is True


@_WIN_ONLY
def test_jpeg_to_png_produces_real_png():
    png = image_codec.jpeg_to_png(_TINY_JPEG)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 32


@_WIN_ONLY
def test_jpeg_to_png_rejects_empty_and_oversize():
    with pytest.raises(image_codec.ImageCodecError):
        image_codec.jpeg_to_png(b"")
    with pytest.raises(image_codec.ImageCodecError):
        image_codec.jpeg_to_png(b"x" * (image_codec.MAX_IMAGE_BYTES + 1))


@_WIN_ONLY
def test_jpeg_to_png_rejects_garbage():
    with pytest.raises(image_codec.ImageCodecError):
        image_codec.jpeg_to_png(b"\xff\xd8\xff\xe0" + b"not-a-jpeg" * 20)


@_WIN_ONLY
def test_jpeg_to_png_rejects_truncated_jpeg():
    """有头无尾的截断数据必须在格式守卫处被拒，不能喂给 GDI+。"""
    with pytest.raises(image_codec.ImageCodecError):
        image_codec.jpeg_to_png(_TINY_JPEG[:200])


def test_format_guard_runs_before_gdiplus():
    """格式守卫在 GDI+ 初始化之前执行，非 Windows 也能安全拒绝垃圾数据。"""
    with pytest.raises(image_codec.ImageCodecError):
        image_codec.jpeg_to_png(b"\x00\x01\x02" * 100)
