# -*- coding: utf-8 -*-
"""
模型搜索（Sketchfab 官方公开 v3 接口）的离线单元测试。

全部用注入的 ``http_get_json``，不访问网络、不需要 tkinter / numpy / 发布包，
在干净环境里也能真跑（而不是 skip）。
"""
import re
import urllib.parse
from pathlib import Path

import pytest

import sketchfab_search as s

ROOT = Path(__file__).resolve().parent.parent


def _raw_item(**overrides):
    item = {
        "uid": "d07a66ea95a14b018329e73dc31d8a25",
        "name": "Nissan Tiida",
        "viewerUrl": "https://sketchfab.com/3d-models/nissan-tiida-d07a66ea95a14b018329e73dc31d8a25",
        "isDownloadable": True,
        "faceCount": 1201924,
        "likeCount": 7290,
        "viewCount": 42000,
        "animationCount": 0,
        "license": {"label": "CC Attribution"},
        "user": {"displayName": "Alice", "username": "alice"},
        "thumbnails": {"images": [
            {"width": 1920, "url": "https://media.sketchfab.com/big.jpeg"},
            {"width": 720, "url": "https://media.sketchfab.com/mid.jpeg"},
            {"width": 256, "url": "https://media.sketchfab.com/small.jpeg"},
        ]},
    }
    item.update(overrides)
    return item


def test_build_search_url_encodes_query_and_filters():
    url = s.build_search_url("丰田 86 & GT", downloadable=True)
    parts = urllib.parse.urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "api.sketchfab.com"
    q = dict(urllib.parse.parse_qsl(parts.query))
    assert q == {"type": "models", "q": "丰田 86 & GT", "count": "24",
                 "downloadable": "true"}
    assert "%26" in url and ("+" in url or "%20" in url)   # 特殊字符确实被 urlencode 了


def test_build_search_url_rejects_empty_query():
    with pytest.raises(s.SearchError):
        s.build_search_url("   ")


def test_build_search_url_cursor_and_sort():
    url = s.build_search_url("car", downloadable=False, sort="-likeCount",
                             cursor="24", count=12)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert q["cursor"] == "24"
    assert q["sort_by"] == "-likeCount"
    assert q["count"] == "12"
    assert "downloadable" not in q


def test_count_is_clamped():
    q = dict(urllib.parse.parse_qsl(
        urllib.parse.urlsplit(s.build_search_url("car", count=9999)).query))
    assert q["count"] == str(s.MAX_COUNT)


def test_parse_response_normalizes_fields():
    page = s.parse_search_response({
        "cursors": {"next": "24", "previous": None},
        "results": [_raw_item(), _raw_item(uid="abc", isDownloadable=False,
                                           name="不可下载")],
    })
    assert page["next_cursor"] == "24"
    assert page["previous_cursor"] is None
    assert len(page["items"]) == 2
    first = page["items"][0]
    assert first["uid"] == "d07a66ea95a14b018329e73dc31d8a25"
    assert first["name"] == "Nissan Tiida"
    assert first["author"] == "Alice"
    assert first["faces"] == 1201924
    assert first["downloadable"] is True
    assert first["license"] == "CC Attribution"
    # 缩略图挑 200~800 宽里最窄的，避免拉 1920 大图
    assert first["thumbnail"].endswith("small.jpeg")
    assert page["items"][1]["downloadable"] is False


def test_parse_response_tolerates_missing_fields():
    page = s.parse_search_response({"cursors": {}, "results": [{"uid": "abcdef"}]})
    it = page["items"][0]
    assert it["name"] == "(未命名模型)"
    assert it["author"] == "—"
    assert it["faces"] == 0
    assert it["downloadable"] is False
    assert it["thumbnail"] == ""
    assert it["url"].endswith("abcdef")
    assert page["next_cursor"] is None


def test_parse_response_detects_api_change():
    with pytest.raises(s.SearchError):
        s.parse_search_response({"error": "oops"})


def test_search_models_uses_injected_http():
    seen = []

    def fake(url):
        seen.append(url)
        return {"cursors": {"next": "24"}, "results": [_raw_item()]}

    page = s.search_models("nissan", downloadable=True, sort="-likeCount",
                           http_get_json=fake)
    assert len(seen) == 1
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(seen[0]).query))
    assert q["q"] == "nissan" and q["downloadable"] == "true"
    assert q["sort_by"] == "-likeCount"
    assert page["items"][0]["name"] == "Nissan Tiida"


def test_search_models_wraps_network_failure():
    def fake(_url):
        raise OSError("offline")

    with pytest.raises(s.SearchError, match="搜索失败"):
        s.search_models("car", http_get_json=fake)


def test_api_error_message_is_human_readable(monkeypatch):
    import urllib.error

    def boom(_req, timeout=None):
        raise urllib.error.HTTPError("https://x", 429, "Too Many", {}, None)

    monkeypatch.setattr(s.urllib.request, "urlopen", boom)
    with pytest.raises(s.SearchError, match="限流"):
        s._http_get_json("https://api.sketchfab.com/v3/search?q=x")


def test_module_has_no_insecure_tls_patterns():
    src = (ROOT / "app" / "sketchfab_search.py").read_text(encoding="utf-8")
    assert not re.search(r"CERT_NONE|check_hostname\s*=\s*False|verify\s*=\s*False", src)


def test_module_does_not_import_gui_dependencies():
    src = (ROOT / "app" / "sketchfab_search.py").read_text(encoding="utf-8")
    assert "import tkinter" not in src
    assert "from tkinter" not in src


def test_gui_wires_search_tab_and_download_button():
    """静态守卫：页签、队列事件、一键下载三处接线不能掉。"""
    src = (ROOT / "app" / "sketchfab_gui.py").read_text(encoding="utf-8")
    assert "self.nb.add(tab_search" in src
    assert "import sketchfab_search as sfsearch" in src
    assert '("search_done"' in src and '("search_error"' in src
    assert "def _download_selected" in src
    assert "sketchfab_search" in (ROOT / "build" / "Sketchfab下载器.spec").read_text(encoding="utf-8")
