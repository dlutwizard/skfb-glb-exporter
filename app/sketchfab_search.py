# -*- coding: utf-8 -*-
"""
Sketchfab 模型搜索（官方公开 API，无需登录/API key）。

设计约束
--------
* **纯标准库、零 GUI 依赖**：不使用任何图形界面工具包，方便在干净环境里单测；
  也不引用项目里的其它模块，避免打包时牵出一整条依赖链。
* **只走 HTTPS 默认证书校验**：不降级证书校验、不关闭主机名校验，
  一律使用 urllib 的默认安全上下文。GUI 启动时安装的 certifi 修复对这里同样生效。
* **不做任何抓取**：只调用 ``api.sketchfab.com/v3/search``。其它站点没有
  等价的免鉴权公开接口，抓取页面既不稳定也可能违反站点条款，所以 v1.2.0
  的搜索范围明确限定为 Sketchfab 站内。
* 网络层可注入（``http_get_json``），便于离线测试，也便于未来换 HTTP 客户端。

接口返回的关键字段（2026-11 实测）::

    {
      "cursors": {"next": "24", "previous": null},
      "next": "https://api.sketchfab.com/v3/search?...&cursor=24",
      "results": [
        {"uid", "name", "viewerUrl", "isDownloadable", "faceCount",
         "likeCount", "viewCount", "animationCount", "publishedAt",
         "license": {"label"}, "user": {"displayName", "username"},
         "thumbnails": {"images": [{"width", "url"}]}}
      ]
    }
"""
import json
import urllib.error
import urllib.parse
import urllib.request

SEARCH_ENDPOINT = "https://api.sketchfab.com/v3/search"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
DEFAULT_COUNT = 24
MAX_COUNT = 100
TIMEOUT = 20

# 组合框显示文案 → API 的 sort_by 值；None = 不传，即官方相关度排序
SORT_OPTIONS = {
    "相关度": None,
    "最多喜欢": "-likeCount",
    "最新发布": "-publishedAt",
    "最多浏览": "-viewCount",
}


class SearchError(RuntimeError):
    """搜索失败（网络、限流、接口改版），消息可直接展示给用户。"""


def _pick_thumbnail(item):
    """挑一张尺寸适中的缩略图 URL；字段缺失返回空串，绝不抛异常。"""
    images = ((item.get("thumbnails") or {}).get("images") or [])
    candidates = []
    for im in images:
        if not isinstance(im, dict) or not im.get("url"):
            continue
        try:
            width = int(im.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        candidates.append((width, im["url"]))
    if not candidates:
        return ""
    # 优先 200~800 宽的小图；没有就取最窄的一张，避免拉 1920 大图
    mid = [c for c in candidates if 200 <= c[0] <= 800]
    pool = sorted(mid) or sorted(candidates)
    return pool[0][1]


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_item(item):
    """把一条 API 结果收敛成 GUI 用的扁平字典（缺字段一律给安全默认值）。"""
    item = item or {}
    uid = str(item.get("uid") or "")
    user = item.get("user") or {}
    lic = item.get("license") or {}
    url = str(item.get("viewerUrl") or "").strip()
    if not url and uid:
        url = "https://sketchfab.com/3d-models/%s" % uid
    return {
        "uid": uid,
        "name": str(item.get("name") or "(未命名模型)"),
        "url": url,
        "author": str(user.get("displayName") or user.get("username") or "—"),
        "faces": _as_int(item.get("faceCount")),
        "likes": _as_int(item.get("likeCount")),
        "views": _as_int(item.get("viewCount")),
        "animations": _as_int(item.get("animationCount")),
        "license": str(lic.get("label") or "—"),
        "downloadable": bool(item.get("isDownloadable")),
        "thumbnail": _pick_thumbnail(item),
    }


def build_search_url(query, downloadable=True, sort=None, cursor=None, count=DEFAULT_COUNT):
    """构造搜索 URL。纯函数，便于断言参数编码与分页游标。"""
    q = str(query or "").strip()
    if not q:
        raise SearchError("搜索关键词不能为空")
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = DEFAULT_COUNT
    count = max(1, min(MAX_COUNT, count))
    params = [("type", "models"), ("q", q), ("count", str(count))]
    if downloadable:
        params.append(("downloadable", "true"))
    if sort:
        params.append(("sort_by", str(sort)))
    if cursor:
        params.append(("cursor", str(cursor)))
    return SEARCH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def parse_search_response(data):
    """解析官方响应 → ``{"items", "next_cursor", "previous_cursor"}``。"""
    if not isinstance(data, dict):
        raise SearchError("搜索接口返回格式异常（不是 JSON 对象）")
    raw = data.get("results")
    if raw is None and "results" not in data:
        raise SearchError("搜索接口改版：缺少 results 字段")
    items = [normalize_item(r) for r in (raw or []) if isinstance(r, dict)]
    cursors = data.get("cursors") or {}
    return {
        "items": items,
        "next_cursor": str(cursors.get("next") or "") or None,
        "previous_cursor": str(cursors.get("previous") or "") or None,
    }


def _http_get_json(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            payload = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise SearchError("搜索太频繁，Sketchfab 暂时限流了，稍等几秒再试")
        if e.code in (401, 403):
            raise SearchError("搜索接口拒绝了请求（HTTP %d）" % e.code)
        raise SearchError("搜索服务返回 HTTP %d" % e.code)
    except urllib.error.URLError as e:
        raise SearchError("连不上 Sketchfab 搜索服务：%s" % (e.reason or e))
    except OSError as e:
        raise SearchError("网络错误：%s" % e)
    try:
        return json.loads(payload)
    except (ValueError, TypeError) as e:
        raise SearchError("搜索结果解析失败：%s" % e)


def search_models(query, downloadable=True, sort=None, cursor=None,
                  count=DEFAULT_COUNT, http_get_json=None):
    """执行一次搜索，返回 :func:`parse_search_response` 形状的结果。

    ``http_get_json`` 可注入 ``f(url) -> dict``；不传则走标准库 HTTPS。
    """
    url = build_search_url(query, downloadable=downloadable, sort=sort,
                           cursor=cursor, count=count)
    getter = http_get_json or _http_get_json
    try:
        data = getter(url)
    except SearchError:
        raise
    except Exception as e:
        raise SearchError("搜索失败：%s" % e)
    return parse_search_response(data)
