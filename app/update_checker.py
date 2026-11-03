# -*- coding: utf-8 -*-
"""
检查更新（R9：无服务器、离线优先、默认不联网）。

三条硬规则，写在代码里而不是靠自觉：

1. **未配置 = 零网络请求**：``update_repo`` 与 ``update_json_url`` 都为空时，
   ``check()`` 立刻返回 ``error="未配置更新源"``，连 socket 都不建。
2. **只在用户手动点击时联网**：没有「启动自动检查」的代码路径。
3. **失败静默**：任何异常（超时 / DNS / 404 / 限流 / JSON 错 / 证书）都变成
   ``UpdateResult.error``，永不抛出、永不弹窗。

支持两种源：
  * GitHub Release：``cfg["update_repo"] = "owner/name"``（私有仓库可配 ``github_token``）
  * 静态 version.json：``cfg["update_json_url"] = "https://.../version.json"``
"""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Optional

try:
    from version import APP_NAME, __version__, is_newer, parse_version, version_string
except Exception:  # pragma: no cover
    APP_NAME = "Sketchfab下载器"
    __version__ = "0.0.0"

    def version_string() -> str:  # type: ignore[misc]
        return __version__

    def is_newer(cur, latest):  # type: ignore[misc]
        return False

    def parse_version(s):  # type: ignore[misc]
        return ()

DEFAULT_TIMEOUT = 5.0
NOTES_LIMIT = 800
GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"


@dataclass
class UpdateResult:
    """一次更新检查的结果。``error`` 非空即失败（UI 静默处理）。"""

    current: str = ""
    latest: str = ""
    is_newer: bool = False
    url: str = ""
    notes: str = ""
    published_at: str = ""
    source: str = ""
    error: str = ""

    def ok(self) -> bool:
        """是否成功拿到远端版本信息。"""
        return not self.error


def _ua() -> str:
    return "{0}/{1} (+update-check)".format(APP_NAME, version_string())


def _clean_tag(tag: str) -> str:
    """``v1.2.0`` → ``1.2.0``；解析不出数字就原样返回。"""
    raw = (tag or "").strip()
    if not raw:
        return ""
    if parse_version(raw):
        return raw.lstrip("vV")
    return raw


def _github_latest(repo: str, token: str = "", timeout: float = DEFAULT_TIMEOUT) -> dict:
    """
    GET ``api.github.com/repos/{repo}/releases/latest``。

    返回规范化后的 dict：``{version, url, notes, published_at, source}``。
    网络/协议错误直接抛出，由 :func:`check` 统一兜住。
    """
    req = urllib.request.Request(
        GITHUB_API.format(repo=repo.strip()),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _ua(),
        },
    )
    tok = (token or "").strip()
    if tok:
        # 私有仓库必须带 PAT（fine-grained，权限只需 Contents: Read-only）
        req.add_header("Authorization", "Bearer " + tok)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub 返回结构异常")

    tag = payload.get("tag_name") or payload.get("name") or ""
    assets = payload.get("assets") or []
    download = payload.get("html_url") or ""
    for asset in assets:
        if isinstance(asset, dict) and asset.get("browser_download_url"):
            download = asset["browser_download_url"]
            break
    return {
        "version": _clean_tag(str(tag)),
        "url": str(download),
        "notes": str(payload.get("body") or "")[:NOTES_LIMIT],
        "published_at": str(payload.get("published_at") or ""),
        "source": "github",
    }


def _json_latest(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """
    GET 一个静态 ``version.json``，期望
    ``{"version","url","notes","published_at"}``。
    """
    req = urllib.request.Request(url.strip(), headers={"User-Agent": _ua()})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("version.json 结构异常")
    return {
        "version": _clean_tag(str(payload.get("version") or "")),
        "url": str(payload.get("url") or ""),
        "notes": str(payload.get("notes") or "")[:NOTES_LIMIT],
        "published_at": str(payload.get("published_at") or payload.get("build_date") or ""),
        "source": "json",
    }


def check(cfg: Optional[Dict], *, timeout: float = DEFAULT_TIMEOUT) -> UpdateResult:
    """
    执行一次更新检查。**永不抛出异常**，也永不因「未配置」而联网。
    """
    cfg = cfg or {}
    current = version_string()
    repo = str(cfg.get("update_repo") or "").strip()
    json_url = str(cfg.get("update_json_url") or "").strip()
    token = str(cfg.get("github_token") or "")

    if not repo and not json_url:
        return UpdateResult(current=current, error="未配置更新源")

    try:
        if repo:
            data = _github_latest(repo, token, timeout)
        else:
            data = _json_latest(json_url, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return UpdateResult(current=current, error="更新源不存在或无权访问(404)")
        if e.code in (403, 429):
            return UpdateResult(current=current, error="请求过于频繁(限流)，稍后再试")
        return UpdateResult(current=current, error="HTTP %s" % e.code)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return UpdateResult(current=current, error="网络不可用: %s" % reason)
    except socket.timeout:
        return UpdateResult(current=current, error="连接超时(>%ss)" % timeout)
    except json.JSONDecodeError:
        return UpdateResult(current=current, error="更新源返回内容不是合法 JSON")
    except Exception as e:  # 兜底：任何情况都不许冒泡到 UI
        return UpdateResult(current=current, error="%s: %s" % (type(e).__name__, e))

    latest = str(data.get("version") or "")
    if not latest:
        return UpdateResult(current=current, error="更新源没有给出有效版本号")

    return UpdateResult(
        current=current,
        latest=latest,
        is_newer=is_newer(current, latest),
        url=str(data.get("url") or ""),
        notes=str(data.get("notes") or ""),
        published_at=str(data.get("published_at") or ""),
        source=str(data.get("source") or ""),
    )


def check_async(cfg: Optional[Dict],
                on_done: Callable[[UpdateResult], None],
                timeout: float = DEFAULT_TIMEOUT) -> threading.Thread:
    """
    在 daemon 线程里跑 :func:`check`，结果通过 ``on_done`` 回调交回调用方。

    回调在**工作线程**里执行 —— 调用方（Tk）必须自己把它转成
    ``queue.put(...)`` 再在主线程消费，绝不能直接在回调里碰 Tk 变量。
    """
    def worker() -> None:
        result = check(cfg, timeout=timeout)
        try:
            on_done(result)
        except Exception:
            pass

    t = threading.Thread(target=worker, name="update-check", daemon=True)
    t.start()
    return t
