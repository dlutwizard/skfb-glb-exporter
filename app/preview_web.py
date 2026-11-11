# -*- coding: utf-8 -*-
"""Local WebGL 3D preview launcher.

The Tk UI only starts a loopback HTTP server and opens the system browser.
All geometry parsing and rendering happens in the browser with the bundled
Three.js assets, so large meshes are rendered by the GPU instead of Tk Canvas.

No third-party Python package is required.
"""
from __future__ import annotations

import mimetypes
import os
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__all__ = ["PreviewWebError", "WebPreview", "LocalModelServer", "SUPPORTED_SUFFIXES"]

SUPPORTED_SUFFIXES = {".glb", ".stl", ".3mf"}
MAX_MODEL_BYTES = 2 * 1024 * 1024 * 1024
_CHUNK = 256 * 1024
_ASSET_PREFIX = "/assets/webgl/"
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".wasm": "application/wasm",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".stl": "model/stl",
    ".3mf": "application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
    ".bin": "application/octet-stream",
}


class PreviewWebError(Exception):
    """Raised when a model cannot be opened by the local WebGL viewer."""


def _asset_dir() -> Path:
    return Path(__file__).resolve().parent / "assets" / "webgl"


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


class _Handler(BaseHTTPRequestHandler):
    server_version = "SFDWebPreview/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        return

    def do_HEAD(self):
        self._serve(head=True)

    def do_GET(self):
        self._serve(head=False)

    def _serve(self, head: bool):
        parsed = urllib.parse.urlsplit(self.path)
        route = urllib.parse.unquote(parsed.path)
        if route in ("/", "/index.html", "/viewer.html"):
            self._send_file(self.server.viewer_html, head=head, cache="no-store")
            return
        if route == "/model":
            self._send_file(self.server.model_path, head=head, cache="no-store")
            return
        if route == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if route.startswith(_ASSET_PREFIX):
            rel = route[len(_ASSET_PREFIX):]
            root = _asset_dir().resolve()
            candidate = (root / rel).resolve()
            if candidate != root and root not in candidate.parents:
                self._not_found()
                return
            if candidate.is_file():
                self._send_file(candidate, head=head, cache="public, max-age=3600")
                return
        self._not_found()

    def _send_file(self, path: Path, *, head: bool, cache: str):
        try:
            fh = path.open("rb")
        except OSError:
            self._not_found()
            return
        try:
            size = os.fstat(fh.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", _content_type(path))
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if head:
                return
            try:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
        finally:
            fh.close()

    def _not_found(self):
        body = b"not found\n"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalModelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, model_path: Path):
        self.model_path = model_path
        self.viewer_html = _asset_dir() / "viewer.html"
        self.token = secrets.token_urlsafe(12)
        self._thread = None
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return "http://%s:%d/" % (host, port)

    def start(self) -> "LocalModelServer":
        if not self.viewer_html.is_file():
            raise PreviewWebError("WebGL 查看器资源缺失：%s" % self.viewer_html)
        self._thread = threading.Thread(
            target=self.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
            name="sfd-web-preview",
        )
        self._thread.start()
        return self

    def stop(self):
        try:
            self.shutdown()
        except Exception:
            pass
        try:
            self.server_close()
        except Exception:
            pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None


class WebPreview:
    """Owns at most one local preview server."""

    def __init__(self):
        self._server = None
        self.path = None
        self.url = None

    def open(self, path) -> str:
        model = self._validate(path)
        self.close()
        server = LocalModelServer(model)
        try:
            server.start()
            url = server.url + "?name=" + urllib.parse.quote(model.name)
            if not webbrowser.open(url, new=2):
                raise PreviewWebError("无法启动默认浏览器，请检查系统默认浏览器设置。")
        except Exception:
            server.stop()
            raise
        self._server = server
        self.path = model
        self.url = url
        return url

    def reopen(self) -> str:
        if not self.path or not self.path.is_file():
            raise PreviewWebError("上一次打开的模型已经不存在。")
        return self.open(self.path)

    def close(self):
        server = self._server
        self._server = None
        self.url = None
        if server is not None:
            server.stop()

    def _validate(self, path) -> Path:
        try:
            model = Path(path).expanduser().resolve()
        except OSError as exc:
            raise PreviewWebError("无法读取模型路径：%s" % exc) from exc
        if not model.is_file():
            raise PreviewWebError("模型文件不存在：%s" % model)
        if model.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise PreviewWebError("只支持 .glb / .stl / .3mf，当前文件：%s" % model.name)
        try:
            size = model.stat().st_size
        except OSError as exc:
            raise PreviewWebError("无法读取模型文件：%s" % exc) from exc
        if size <= 0:
            raise PreviewWebError("模型文件为空：%s" % model.name)
        if size > MAX_MODEL_BYTES:
            raise PreviewWebError("模型文件超过 2 GB，浏览器预览可能内存不足：%s" % model.name)
        return model