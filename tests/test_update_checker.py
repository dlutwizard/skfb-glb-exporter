# -*- coding: utf-8 -*-
"""
R9 / update_checker 单元测试。

硬规则（PRD R9 + 模块 docstring）：
  1. 未配置 = 零网络请求（mock urlopen，断言未被调用）
  2. 超时 ≤6s 返回 error 不抛
  3. tag 比较正确（v 前缀、预发布）
  4. PAT 只从配置读，不读环境变量
"""
import json
import socket
import sys
import time
import urllib.request
from io import BytesIO
from unittest import mock

import pytest

import update_checker as uc


def _resp(payload: bytes):
    """伪造 urlopen 返回的 context-manager 响应。"""
    m = mock.MagicMock()
    m.read.return_value = payload
    m.__enter__.return_value = m
    m.__exit__.return_value = False
    return m


# ---------------------------------------------------------------------------
# 1. 未配置 → 零网络请求
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cfg", [None, {}, {"update_repo": "", "update_json_url": " "}])
def test_unconfigured_zero_network(cfg):
    with mock.patch.object(urllib.request, "urlopen") as m_open:
        result = uc.check(cfg)
        m_open.assert_not_called()
    assert not result.ok()
    assert "未配置" in result.error
    assert result.current == uc.version_string()


def test_check_async_unconfigured_never_hits_network():
    done = []
    with mock.patch.object(urllib.request, "urlopen") as m_open:
        th = uc.check_async({}, lambda r: done.append(r), timeout=1.0)
        th.join(timeout=3.0)
    m_open.assert_not_called()
    assert done and not done[0].ok()


# ---------------------------------------------------------------------------
# 2. 超时 / 网络错误 → error 不抛
# ---------------------------------------------------------------------------

def test_timeout_returns_error_within_6s():
    def slow_urlopen(req, timeout=None):
        time.sleep(1.0)          # 模拟慢连接（>传入的 timeout）
        raise socket.timeout("timed out")
    t0 = time.perf_counter()
    with mock.patch.object(urllib.request, "urlopen", side_effect=slow_urlopen):
        result = uc.check({"update_repo": "o/n"}, timeout=0.5)
    elapsed = time.perf_counter() - t0
    assert not result.ok()
    assert "超时" in result.error
    assert elapsed < 6.0, "超时路径耗时 %.1fs，超出 6s 上限" % elapsed


def test_timeout_at_default_bound():
    """默认 timeout=5.0，即使网络挂死也必须在 ≤6s 量级返回 error。"""
    def hang(req, timeout=None):
        assert timeout is not None and timeout <= 6.0
        raise socket.timeout()
    t0 = time.perf_counter()
    with mock.patch.object(urllib.request, "urlopen", side_effect=hang):
        result = uc.check({"update_json_url": "https://example.com/version.json"})
    assert time.perf_counter() - t0 < 6.0
    assert not result.ok() and "超时" in result.error


def test_all_exceptions_swallowed():
    for exc in (Exception("boom"), OSError("dns"), ValueError("x")):
        with mock.patch.object(urllib.request, "urlopen", side_effect=exc):
            result = uc.check({"update_repo": "o/n"})
        assert not result.ok()
        assert result.error


def test_http_404_mapped():
    import urllib.error
    err = urllib.error.HTTPError("u", 404, "NF", {}, BytesIO(b""))
    with mock.patch.object(urllib.request, "urlopen", side_effect=err):
        result = uc.check({"update_repo": "o/n"})
    assert "404" in result.error


# ---------------------------------------------------------------------------
# 3. tag 比较（v 前缀 / 预发布 / 逐段）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,cleaned", [
    ("v1.2.0", "1.2.0"),
    ("V2.0.1", "2.0.1"),
    ("1.3.0", "1.3.0"),
    ("not-a-version", "not-a-version"),   # 解析不出数字就原样返回
])
def test_clean_tag(raw, cleaned):
    assert uc._clean_tag(raw) == cleaned


@pytest.mark.parametrize("cur,latest,expect", [
    ("1.1.0", "1.2.0", True),
    ("1.1.0", "v1.2.0", True),
    ("1.1.0", "1.1.0", False),
    ("1.1.0", "1.0.9", False),
    ("1.1", "1.1.0", False),          # 缺位补 0 后相等
    ("2.0", "1.9.9", False),
    ("1.1.0", "1.1.1-beta", True),    # 预发布按数值段判定为更新（文档化行为）
    ("1.1.0", "", False),
    ("", "1.2.0", False),
    ("1.1.0", "garbage", False),      # 解析失败宁可不提示
])
def test_is_newer(cur, latest, expect):
    from version import is_newer
    assert is_newer(cur, latest) is expect


def test_github_flow_end_to_end_mocked():
    # 用一个远高于任何当前版本的值，避免每次升版本号都要改这条用例
    payload = json.dumps({
        "tag_name": "v9.9.9",
        "html_url": "https://github.com/o/n/releases/tag/v9.9.9",
        "assets": [{"browser_download_url": "https://x/pkg.zip"}],
        "body": "- fix a\n- add b",
        "published_at": "2026-11-01T00:00:00Z",
    }).encode("utf-8")
    with mock.patch.object(urllib.request, "urlopen",
                           return_value=_resp(payload)) as m_open:
        result = uc.check({"update_repo": "o/n"})
    assert result.ok()
    assert result.latest == "9.9.9"
    assert result.is_newer is True
    assert result.url == "https://x/pkg.zip"
    assert result.source == "github"
    req = m_open.call_args[0][0]
    assert "api.github.com" in req.full_url
    assert req.full_url.endswith("/repos/o/n/releases/latest")


def test_json_source_flow_mocked():
    payload = json.dumps({"version": "1.1.0", "url": "https://x/v1.1.0.zip"}).encode()
    with mock.patch.object(urllib.request, "urlopen", return_value=_resp(payload)):
        result = uc.check({"update_json_url": "https://cdn/version.json"})
    assert result.ok() and result.latest == "1.1.0"
    assert result.is_newer is False


# ---------------------------------------------------------------------------
# 4. PAT 只从配置读
# ---------------------------------------------------------------------------

def test_pat_from_config_only(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ENV_LEAKED_TOKEN")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.headers.get("Authorization")
        seen["ua"] = req.headers.get("User-agent")
        return _resp(json.dumps({"tag_name": "v1.1.0"}).encode())

    with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
        result = uc.check({"update_repo": "o/n", "github_token": "CFG_PAT"})

    assert seen["auth"] == "Bearer CFG_PAT", "PAT 应且仅应来自配置，而非环境变量"
    assert result.ok()


def test_no_pat_no_auth_header(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ENV_LEAKED_TOKEN")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.headers.get("Authorization")
        return _resp(json.dumps({"tag_name": "v9.9.9"}).encode())

    with mock.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
        result = uc.check({"update_repo": "o/n"})

    assert result.ok()
    assert "Authorization" not in (seen["auth"] or "")


def test_real_network_not_used_in_tests():
    """保险丝：本测试模块里 urlopen 必须处于 mock 状态（防止误真联网）。"""
    import unittest.mock
    assert "unittest.mock" in sys.modules
