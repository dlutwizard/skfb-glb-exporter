# -*- coding: utf-8 -*-
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import preview_web


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read(), response.headers


@pytest.fixture
def running_server(tmp_path):
    model = tmp_path / "part.stl"
    model.write_bytes(b"solid part\nendsolid part\n")
    server = preview_web.LocalModelServer(model).start()
    try:
        yield server
    finally:
        server.stop()


def test_viewer_html_and_three_assets_are_served(running_server):
    status, body, headers = _get(running_server.url)
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"importmap" in body
    assert b"/assets/webgl/viewer.js" in body

    status, body, headers = _get(running_server.url + "assets/webgl/viewer.js")
    assert status == 200
    assert headers["Content-Type"].startswith("text/javascript")
    assert b"OrbitControls" in body

    status, body, headers = _get(running_server.url + "assets/webgl/three.module.js")
    assert status == 200
    assert len(body) > 1_000_000


def test_model_is_streamed_with_its_real_mime_type(running_server):
    status, body, headers = _get(running_server.url + "model")
    assert status == 200
    assert body == b"solid part\nendsolid part\n"
    assert headers["Content-Type"].startswith("model/stl")


def test_assets_cannot_escape_the_viewer_directory(running_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(running_server.url + "assets/webgl/../preview_web.py")
    assert exc.value.code == 404


def test_web_preview_opens_browser_and_owns_one_server(tmp_path, monkeypatch):
    model = tmp_path / "part.3mf"
    model.write_bytes(b"PK\x03\x04fake")
    opened = {}

    def fake_open(url, new=0):
        opened["url"] = url
        return True

    monkeypatch.setattr(preview_web.webbrowser, "open", fake_open)
    preview = preview_web.WebPreview()
    try:
        url = preview.open(model)
        assert opened["url"] == url
        assert preview.url == url
        assert preview.path == model.resolve()
        assert url.startswith("http://127.0.0.1:")
    finally:
        preview.close()


def test_unsupported_and_empty_models_are_rejected(tmp_path):
    preview = preview_web.WebPreview()
    bad = tmp_path / "part.obj"
    bad.write_text("v 0 0 0\n", encoding="utf-8")
    with pytest.raises(preview_web.PreviewWebError):
        preview.open(bad)

    empty = tmp_path / "empty.glb"
    empty.write_bytes(b"")
    with pytest.raises(preview_web.PreviewWebError):
        preview.open(empty)


def test_bundled_viewer_assets_exist():
    root = Path(preview_web.__file__).resolve().parent / "assets" / "webgl"
    for name in ("viewer.html", "viewer.js", "viewer.css", "three.module.js"):
        assert (root / name).is_file()
    assert (root / "addons" / "loaders" / "GLTFLoader.js").is_file()
    assert (root / "addons" / "loaders" / "STLLoader.js").is_file()
    assert (root / "addons" / "loaders" / "3MFLoader.js").is_file()
