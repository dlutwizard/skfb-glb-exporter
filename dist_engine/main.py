#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sketchfab Model Downloader (CLI)

Download public Sketchfab 3D models without an account or API keys.
Works for models that have no official Download button (uses the same
mesh data the public 3D viewer loads in the browser).

Output: glTF (+ textures when available) in the downloads/ folder.

Local key.txt / key2.txt written during decrypt are NOT API keys —
they are one-time mesh decryption material taken from the public page.
Since v1.1.8 they are deleted after a successful decrypt; set
SKFB_KEEP_INTERMEDIATES=1 to keep them for debugging.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
BINZ_DECRYPT = ROOT / "tools" / "binz" / "binzDecrypt.exe"
OSGCONV = ROOT / "tools" / "OsgConv" / "osgconv.exe"

# 贴图扩展名白名单：统计 info.json.textures 时只算图片，不把 manifest 算进去
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 排障开关：默认丢弃 key.txt / key2.txt 等中间材料（SFD-010）
KEEP_INTERMEDIATES = os.environ.get("SKFB_KEEP_INTERMEDIATES", "").strip().lower() not in (
    "", "0", "false", "no", "off",
)

# Runtime options (CLI / library helpers)
#
# 用 threading.local 而不是模块级全局：并发下载时每个工作线程有独立的代理与
# 进度回调，避免 A 任务的进度被 B 任务覆盖（早期版本用全局变量会串台）。
_LOCAL = threading.local()


def _get_proxy() -> Optional[str]:
    return getattr(_LOCAL, "proxy", None)


def _get_cb() -> Optional[Callable[[str], None]]:
    return getattr(_LOCAL, "cb", None)


def set_proxy(proxy: Optional[str]) -> None:
    """http(s)://user:pass@host:port or socks5://… — None = direct（仅作用于当前线程）。"""
    _LOCAL.proxy = (proxy or "").strip() or None


def set_progress_callback(cb: Optional[Callable[[str], None]]) -> None:
    """设置当前线程的进度回调（并发任务互不影响）。"""
    _LOCAL.cb = cb


def _progress(msg: str) -> None:
    log(msg)
    cb = _get_cb()
    if cb:
        try:
            cb(msg)
        except Exception:
            pass

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

UID_RE = re.compile(r"([a-f0-9]{32})(?:/)?(?:\?.*)?$", re.I)
URL_RE = re.compile(
    r"https?://(?:www\.)?sketchfab\.com/(?:3d-models|models)/[^\s]+",
    re.I,
)

MAX_RETRIES = 6
RETRY_BASE_SEC = 1.2


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

def _fix_console() -> None:
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def log(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Robust HTTP (SSL-safe, multi-backend, retries)
# ---------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    """TLS context that works better with AV/proxies on Windows."""
    try:
        import certifi  # type: ignore

        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    # Soften some middlebox quirks without fully disabling verification.
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except Exception:
        pass
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return ctx


def _is_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    names = type(exc).__name__.lower()
    needles = (
        "decrypt",
        "bad record mac",
        "ssl",
        "eof",
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "broken pipe",
        "10054",
        "10053",
        "10060",
        "wrong version number",
        "unexpected_eof",
        "protocol",
        "remote end closed",
    )
    if any(n in text or n in names for n in needles):
        return True
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 500, 502, 503, 504):
        return True
    return False


def _proxy_dict() -> Optional[dict]:
    proxy = _get_proxy()
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _http_via_requests(url: str, timeout: int) -> bytes:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "close",  # avoid broken keep-alive after AV MITM
    }
    r = session.get(
        url,
        headers=headers,
        timeout=timeout,
        stream=True,
        proxies=_proxy_dict(),
    )
    r.raise_for_status()
    chunks: list[bytes] = []
    for chunk in r.iter_content(chunk_size=256 * 1024):
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def _http_via_urllib(url: str, timeout: int, *, insecure: bool = False) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Connection": "close",
    }
    req = urllib.request.Request(url, headers=headers)
    if insecure:
        ctx = ssl._create_unverified_context()
    else:
        ctx = _ssl_context()
    handlers: list[Any] = [urllib.request.HTTPSHandler(context=ctx)]
    if _get_proxy():
        handlers.insert(
            0,
            urllib.request.ProxyHandler(
                _proxy_dict() or {}
            ),
        )
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def _http_via_curl(url: str, timeout: int, dest: Optional[Path] = None) -> bytes:
    """Use real curl.exe (not PowerShell alias). Very resilient to flaky TLS."""
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl not found")
    # Prefer writing to a temp file for large binaries.
    tmp = dest
    own_tmp = False
    if tmp is None:
        tmp = ROOT / f".tmp_dl_{os.getpid()}_{random.randint(0, 1_000_000)}.bin"
        own_tmp = True
    try:
        cmd = [
            curl,
            "-L",
            "--fail",
            "--silent",
            "--show-error",
            "--http1.1",  # HTTP/2 multiplex often triggers BAD_RECORD_MAC behind AV
            "--retry",
            "3",
            "--retry-delay",
            "1",
            "--retry-all-errors",
            "--connect-timeout",
            "20",
            "--max-time",
            str(max(timeout, 30)),
            "-A",
            USER_AGENT,
            "-H",
            "Connection: close",
            "-o",
            str(tmp),
        ]
        if _get_proxy():
            cmd.extend(["-x", _get_proxy()])
        cmd.append(url)
        proc = subprocess.run(cmd, capture_output=True, text=True, **_hidden_kwargs())
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"curl exit {proc.returncode}: {err[:400]}")
        data = tmp.read_bytes()
        if not data:
            raise RuntimeError("curl downloaded empty file")
        return data
    finally:
        if own_tmp and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def http_get_bytes(url: str, *, timeout: int = 180, label: str = "") -> bytes:
    """
    Download URL with multiple backends and retries.
    Order: requests → urllib → curl.exe.

    所有后端都**强制校验 TLS 证书**。早期版本还有一个"最后兜底：不校验证书"的分支，
    在代理/杀软/中间人环境下会静默降级，用户完全无感地接受被篡改的流量，已移除。
    证书有问题时应该报错让人看见，而不是偷偷关掉校验。
    """
    last_err: Optional[BaseException] = None
    backends = (
        ("requests", lambda: _http_via_requests(url, timeout)),
        ("urllib", lambda: _http_via_urllib(url, timeout, insecure=False)),
        ("curl", lambda: _http_via_curl(url, timeout)),
    )

    for attempt in range(1, MAX_RETRIES + 1):
        for name, fn in backends:
            try:
                data = fn()
                if attempt > 1 or name != "requests":
                    tag = f" ({name}, attempt {attempt})" if label else f" via {name}"
                    if label:
                        log(f"  ✓ {label}{tag}")
                return data
            except Exception as e:
                last_err = e
                if not _is_retryable(e):
                    # Non-network HTTP errors (404 etc.) — try next backend only if useful
                    if isinstance(e, urllib.error.HTTPError) and e.code == 404:
                        raise
                    if "404" in str(e):
                        raise
                # continue to next backend
                continue

        wait = RETRY_BASE_SEC * (1.5 ** (attempt - 1)) + random.uniform(0, 0.4)
        log(
            f"  ⚠ network/SSL failure (attempt {attempt}/{MAX_RETRIES}): "
            f"{type(last_err).__name__}: {last_err}"
        )
        log(f"    retry in {wait:.1f}s…")
        time.sleep(wait)

    raise RuntimeError(
        "Failed to download data due to a network/SSL error.\n"
        f"URL: {url}\n"
        f"Last error: {last_err}\n\n"
        "Try:\n"
        "  • disable VPN briefly\n"
        "  • temporarily turn off antivirus HTTPS scanning\n"
        "  • run the command again (often works on 2nd–3rd try)\n"
        "  • ensure curl is available in PATH"
    )


def http_get_text(url: str, *, timeout: int = 120) -> str:
    return http_get_bytes(url, timeout=timeout).decode("utf-8", "replace")


def http_get_json(url: str, *, timeout: int = 60) -> dict:
    return json.loads(http_get_text(url, timeout=timeout))


def download_file(url: str, dest: Path) -> None:
    log(f"  ↓ {dest.name}")
    # Prefer curl straight-to-disk for large binz.
    try:
        data = _http_via_curl(url, timeout=300, dest=dest)
        # curl already wrote dest; ensure size
        if dest.is_file() and dest.stat().st_size > 0:
            return
        dest.write_bytes(data)
        return
    except Exception as e:
        if not _is_retryable(e) and "curl" not in str(e).lower():
            pass
        log(f"    curl failed ({e}); trying Python HTTP…")

    data = http_get_bytes(url, timeout=300, label=dest.name)
    dest.write_bytes(data)


# ---------------------------------------------------------------------------
# URL / names
# ---------------------------------------------------------------------------

def extract_uid(text: str) -> str:
    text = text.strip()
    m = UID_RE.search(text)
    if not m:
        raise ValueError(
            "Model UID (32 hex chars) not found in the link.\n"
            "Example: https://sketchfab.com/3d-models/name-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )
    return m.group(1).lower()


def sanitize_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name.strip())
    name = name.strip(" ._")
    return name or "model"


# ---------------------------------------------------------------------------
# Sketchfab viewer data (public, no login)
# ---------------------------------------------------------------------------

def fetch_viewer_info(uid: str) -> tuple[dict, str]:
    embed_url = f"https://sketchfab.com/models/{uid}/embed"
    log(f"[1/5] Reading public viewer (no login): {embed_url}")
    embed_html = http_get_text(embed_url, timeout=90)

    m = re.search(
        r'id="js-dom-data-prefetched-data"><!--(.*?)-->',
        embed_html,
        re.S,
    )
    if not m:
        # Alternate marker (Sketchfab sometimes changes whitespace)
        m = re.search(
            r'js-dom-data-prefetched-data[^>]*>\s*<!--(.*?)-->',
            embed_html,
            re.S,
        )
    if not m:
        raise RuntimeError(
            "Failed to read model data from the embed page.\n"
            "Possible causes: model deleted, private, or region-blocked."
        )

    raw = m.group(1).replace("&#34;", '"')
    data = json.loads(raw)
    key = f"/i/models/{uid}"
    if key not in data:
        # sometimes only /i/models/{uid}?… variants
        for k in data:
            if k.startswith(f"/i/models/{uid}"):
                key = k
                break
        else:
            raise RuntimeError(f"Model {uid} not found in prefetched data")
    return data[key], embed_html


# Built-in fallback if live extraction fails (Sketchfab rotates this periodically).
_DEFAULT_STATIC_KEY = "7d61ef7c7530c12cf080fafd05e603d1aa3a92c6"
_STATIC_KEY_CACHE = ROOT / "tools" / "wasm" / "static_key.txt"
_WASM_DIR = ROOT / "tools" / "wasm"
_WASM_FILE = _WASM_DIR / "decrypt.wasm"
_WASM_WORKER = _WASM_DIR / "decrypt_worker.mjs"


def _viewer_script_urls(embed_html: str) -> list[str]:
    script_urls: list[str] = []
    for m in re.finditer(r'<script[^>]+src="([^"]+)"', embed_html):
        src = m.group(1)
        if src not in script_urls:
            script_urls.append(src)
    script_urls.sort(
        key=lambda u: (0 if "/static/builds/web/dist/" in u else 1, u)
    )
    return script_urls


def _load_cached_static_key() -> Optional[str]:
    try:
        if _STATIC_KEY_CACHE.is_file():
            val = _STATIC_KEY_CACHE.read_text(encoding="utf-8").strip().lower()
            if re.fullmatch(r"[0-9a-f]{40}", val):
                return val
    except Exception:
        pass
    return None


def _save_cached_static_key(key: str) -> None:
    try:
        _WASM_DIR.mkdir(parents=True, exist_ok=True)
        _STATIC_KEY_CACHE.write_text(key.strip().lower() + "\n", encoding="utf-8")
    except Exception:
        pass


def ensure_wasm_module(embed_html: str, *, force: bool = False) -> bool:
    """
    Ensure tools/wasm/decrypt.wasm exists.
    If missing (or force=True), extract it from current viewer JS bundles.
    Returns True if wasm file is available afterwards.
    """
    if _WASM_FILE.is_file() and _WASM_FILE.stat().st_size > 10_000 and not force:
        return True

    log("  extracting decrypt.wasm from viewer bundles…")
    _WASM_DIR.mkdir(parents=True, exist_ok=True)
    import base64

    for js_url in _viewer_script_urls(embed_html):
        name = js_url.rstrip("/").split("/")[-1]
        try:
            js = http_get_text(js_url, timeout=120)
        except Exception as e:
            log(f"  (JS {name}: {e})")
            continue
        start = 0
        while True:
            idx = js.find("AGFzbQ", start)
            if idx < 0:
                break
            q0 = js.rfind('"', 0, idx) + 1
            end = idx
            while end < len(js):
                if js[end] == '"' and js[end - 1] != "\\":
                    break
                end += 1
            b64 = js[q0:end].replace("\\n", "")
            try:
                raw = base64.b64decode(b64, validate=False)
            except Exception:
                start = idx + 1
                continue
            if raw[:4] == b"\x00asm" and len(raw) > 50_000:
                _WASM_FILE.write_bytes(raw)
                log(f"  decrypt.wasm saved from {name} ({len(raw)} bytes)")
                return True
            start = idx + 1
    log("  WARNING: could not extract decrypt.wasm from viewer JS")
    return _WASM_FILE.is_file()


def extract_key2(embed_html: str) -> Optional[str]:
    """
    Static decrypt key from public viewer JS (auto-updated each run).

    This is NOT a user API key. It is a 40-char hex string the browser viewer
    uses together with per-model diter.b for WASM .binz decryption.

    On success the key is cached to tools/wasm/static_key.txt so the next run
    still works if Sketchfab temporarily changes JS packaging.
    """
    # Also refresh wasm if missing (self-healing install)
    ensure_wasm_module(embed_html, force=False)

    patterns = [
        re.compile(
            r'exports\s*\.\s*k\s*:\s*\(\)\s*=>\s*\w+\}\s*;\s*const\s+\w+\s*=\s*"([0-9a-f]{40})(?:\\n)?"'
        ),
        re.compile(
            r'\{k:\s*\(\)\s*=>\s*\w+\}[^;]*;\s*const\s+\w+\s*=\s*"([0-9a-f]{40})(?:\\n)?"'
        ),
        re.compile(
            r'a\.d\(t,\{\s*k:\(\)=>\w+\s*\}\)[^"]{0,40}const\s+\w+\s*=\s*"([0-9a-f]{40})(?:\\n)?"'
        ),
        re.compile(
            r'pXZ0:\(e,t,a\)=>\{"use strict";a\.d\(t,\{k:\(\)=>n\}\);'
            r'const n="([^"]+)"'
        ),
        re.compile(r'pXZ0:.*?const n="([^"]+)"', re.S),
        re.compile(r'const\s+\w+\s*=\s*"([0-9a-f]{40})\\n"'),
        re.compile(r'const\s+\w+\s*=\s*"([0-9a-f]{40})"'),
    ]

    for js_url in _viewer_script_urls(embed_html):
        name = js_url.rstrip("/").split("/")[-1]
        try:
            js = http_get_text(js_url, timeout=90)
        except Exception as e:
            log(f"  (JS {name}: {e})")
            continue

        for pat in patterns:
            mm = pat.search(js)
            if not mm:
                continue
            raw = mm.group(1)
            key2 = raw.replace("\\n", "\n").strip()
            if re.fullmatch(r"[0-9a-f]{40}", key2, re.I):
                key = key2.lower()
                log(f"  static key found in {name}")
                _save_cached_static_key(key)
                return key
            if len(key2) >= 16:
                log(f"  key2 found in {name} ({len(key2)} chars)")
                return key2

        if "pXZ0" in js:
            try:
                chunk = js.split("pXZ0:", 1)[1]
                if 'const n="' in chunk:
                    key2 = chunk.split('const n="', 1)[1].split('"', 1)[0]
                    key2 = key2.replace("\\n", "\n").strip()
                    if re.fullmatch(r"[0-9a-f]{40}", key2, re.I):
                        key = key2.lower()
                        log(f"  static key (pXZ0) in {name}")
                        _save_cached_static_key(key)
                        return key
                    if len(key2) >= 16:
                        log(f"  key2 (fallback) in {name} ({len(key2)} chars)")
                        return key2
            except Exception:
                pass

        # Unique 40-hex in a small module is almost always the static key
        if len(js) < 200_000:
            hexes = re.findall(r'["\']([0-9a-f]{40})(?:\\n)?["\']', js)
            uniq = list(dict.fromkeys(hexes))
            if len(uniq) == 1:
                key = uniq[0].lower()
                log(f"  static key (unique hex) in {name}")
                _save_cached_static_key(key)
                return key

    cached = _load_cached_static_key()
    if cached:
        log("  static key not in live JS — using cached tools/wasm/static_key.txt")
        return cached
    log("  static key not found — using built-in fallback")
    return _DEFAULT_STATIC_KEY


def resolve_model_name(uid: str, viewer_info: dict) -> str:
    name = viewer_info.get("name")
    if name:
        return sanitize_name(str(name))
    try:
        info = http_get_json(f"https://sketchfab.com/i/models/{uid}")
        if info.get("name"):
            return sanitize_name(str(info["name"]))
    except Exception:
        pass
    return uid


# ---------------------------------------------------------------------------
# Download + decrypt + convert
# ---------------------------------------------------------------------------

def _find_wine() -> Optional[str]:
    """Locate Wine loader (Debian puts it under /usr/lib/wine, not always in PATH)."""
    for name in ("wine64", "wine"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        "/usr/lib/wine/wine64",
        "/usr/bin/wine64",
        "/usr/bin/wine",
        "/usr/local/bin/wine64",
        "/usr/local/bin/wine",
        "/opt/wine-stable/bin/wine64",
        "/opt/wine-stable/bin/wine",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _hidden_kwargs() -> dict:
    """
    Windows 下隐藏子进程的控制台窗口。

    GUI 版是以无控制台方式启动的，但只要 spawn 一个控制台程序（node.exe / curl.exe），
    Windows 就会为它新建一个黑框一闪而过。CREATE_NO_WINDOW + STARTF_USESHOWWINDOW
    能彻底避免这个现象；非 Windows 平台返回空字典，行为不变。
    """
    if not sys.platform.startswith("win"):
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        "startupinfo": si,
    }


def run_tool(cmd: list[str], cwd: Path) -> None:
    import tempfile

    creationflags = 0
    run_cmd = list(cmd)
    env = {**os.environ}
    use_wine = False
    if sys.platform.startswith("win"):
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    else:
        # Windows PE tools (binzDecrypt/osgconv) via Wine on Linux/Docker
        exe = run_cmd[0] if run_cmd else ""
        if str(exe).lower().endswith(".exe"):
            wine = _find_wine()
            if not wine:
                raise RuntimeError(
                    f"Cannot run {Path(exe).name}: wine not found. "
                    "Install wine64 or use Docker image with Wine."
                )
            run_cmd = [wine, *run_cmd]
            use_wine = True
            env.setdefault("WINEDEBUG", "-all")
            env.setdefault("WINEDLLOVERRIDES", "mscoree,mshtml=")
            env.setdefault("WINEPREFIX", os.path.expanduser("~/.wine"))
            # binzDecrypt is a Node pkg binary; needs this under Wine
            env.setdefault("NODE_SKIP_PLATFORM_CHECK", "1")
    shown = " ".join(Path(c).name if i == 0 else c for i, c in enumerate(run_cmd))
    log(f"  $ {shown}")

    # Node-under-Wine crashes on pipe stdio (uv_pipe_open EINVAL).
    # Always capture via temp files when using Wine.
    if use_wine:
        out_path = err_path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="sftool_out_", delete=False
            ) as fo, tempfile.NamedTemporaryFile(
                prefix="sftool_err_", delete=False
            ) as fe:
                out_path, err_path = fo.name, fe.name
                proc = subprocess.run(
                    run_cmd,
                    cwd=str(cwd),
                    stdin=subprocess.DEVNULL,
                    stdout=fo,
                    stderr=fe,
                    creationflags=creationflags,
                    env=env,
                )
            out = Path(out_path).read_text(encoding="utf-8", errors="replace")
            err = Path(err_path).read_text(encoding="utf-8", errors="replace")
        finally:
            for p in (out_path, err_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
        if proc.returncode != 0:
            msg = (err or out or "").strip()
            raise RuntimeError(f"外部工具执行失败（退出码 {proc.returncode}）：\n{msg[:900]}")
        return

    proc = subprocess.run(
        run_cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        env=env,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        # 0xC0000005 = 3221225477 = ACCESS_VIOLATION (common osgconv crash)
        raise RuntimeError(f"外部工具执行失败（退出码 {proc.returncode}）：\n{err[:900]}")


def is_new_binz_format(viewer_info: dict) -> bool:
    files = viewer_info.get("files") or []
    if not files:
        return False
    try:
        _ = files[0]["p"][0]["b"]
        return True
    except (KeyError, IndexError, TypeError):
        return False


def download_model_files(viewer_info: dict, work_dir: Path, new_format: bool) -> None:
    files = viewer_info.get("files") or []
    if not files:
        raise RuntimeError("这个模型没有可下载的网格文件（可能是私有模型或数据损坏）")

    osgjs_url = files[0]["osgjsUrl"]
    if new_format:
        urls = [
            osgjs_url,
            osgjs_url.replace("file.binz", "model_file.binz"),
            osgjs_url.replace("file.binz", "model_file_wireframe.binz"),
        ]
    else:
        urls = [
            osgjs_url,
            osgjs_url.replace("file.osgjs.gz", "model_file.bin.gz"),
            osgjs_url.replace("file.osgjs.gz", "model_file_wireframe.bin.gz"),
        ]

    for url in urls:
        name = url.rstrip("/").split("/")[-1].split("?")[0]
        dest = work_dir / name
        try:
            download_file(url, dest)
        except Exception as e:
            if "wireframe" in name and ("404" in str(e) or "HTTP Error 404" in str(e)):
                log(f"  (skip {name}: not on CDN)")
                continue
            raise


def _wasm_decrypt_paths() -> tuple[Optional[Path], Optional[Path]]:
    """Return (node_script, wasm_file) if WASM decrypt tools are present."""
    if _WASM_WORKER.is_file() and _WASM_FILE.is_file() and _WASM_FILE.stat().st_size > 10_000:
        return _WASM_WORKER, _WASM_FILE
    return None, None


def _resolve_static_key(key2: Optional[str]) -> str:
    """Prefer live/extracted key, then cache, then built-in default."""
    if key2:
        k = key2.strip()
        if re.fullmatch(r"[0-9a-fA-F]{40}", k):
            return k.lower()
        if len(k) >= 16:
            return k
    cached = _load_cached_static_key()
    if cached:
        return cached
    return _DEFAULT_STATIC_KEY


def _decrypt_binz_wasm(work_dir: Path, key1: str, static_key: str) -> None:
    """Decrypt all .binz in work_dir via Node + Sketchfab WASM module."""
    worker, _wasm = _wasm_decrypt_paths()
    if not worker:
        raise FileNotFoundError("tools/wasm/decrypt_worker.mjs or decrypt.wasm missing")

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        raise RuntimeError(
            "Node.js is required for WASM .binz decrypt.\n"
            "Install: https://nodejs.org/  or  sudo apt install nodejs"
        )

    key_file = work_dir / "key.txt"
    key_file.write_text(key1, encoding="utf-8")
    static = _resolve_static_key(static_key)

    cmd = [node, str(worker), str(work_dir), f"@{key_file}", static]
    log(f"  $ node {worker.name} (WASM decrypt, key={static[:8]}…)")
    proc = subprocess.run(
        cmd,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "NODE_NO_WARNINGS": "1"},
        **_hidden_kwargs(),
    )
    if proc.stderr:
        for line in proc.stderr.strip().splitlines()[-20:]:
            log(f"  {line}")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"WASM decrypt failed (code {proc.returncode}):\n{err[:900]}")


def decrypt_binz(
    work_dir: Path,
    key1: str,
    key2: Optional[str],
    *,
    embed_html: Optional[str] = None,
) -> None:
    """
    Decrypt Sketchfab .binz mesh files in work_dir.

    Modern path: Node.js + WASM (tools/wasm/) using:
      - key1 = per-model diter.b from viewer JSON (always live)
      - static key = 40-hex from viewer JS (auto-extracted each run + cached)
    Legacy path: binzDecrypt.exe via Wine (often broken on current Sketchfab).
    """
    binz_files = sorted(work_dir.glob("*.binz"))
    if not binz_files:
        raise RuntimeError("No .binz files found after download")

    static_key = _resolve_static_key(key2)
    worker, _ = _wasm_decrypt_paths()
    node_ok = bool(shutil.which("node") or shutil.which("nodejs"))

    if worker and node_ok:
        log("  decrypt: WASM (auto static key + per-model diter.b)")
        try:
            _decrypt_binz_wasm(work_dir, key1, static_key)
        except Exception as e:
            if embed_html:
                log(f"  WASM decrypt failed ({e}); refreshing wasm + key…")
                ensure_wasm_module(embed_html, force=True)
                static_key = _resolve_static_key(extract_key2(embed_html))
                _decrypt_binz_wasm(work_dir, key1, static_key)
            else:
                raise
    elif BINZ_DECRYPT.is_file():
        log("  decrypt: legacy binzDecrypt.exe (may fail on current Sketchfab)")
        (work_dir / "key.txt").write_text(key1, encoding="utf-8")
        (work_dir / "key2.txt").write_text(static_key, encoding="utf-8")
        for path in binz_files:
            log(f"  decrypt {path.name} ({path.stat().st_size} bytes)")
            run_tool([str(BINZ_DECRYPT), "key.txt", path.name], cwd=work_dir)
    else:
        raise FileNotFoundError(
            "没有可用的解密后端。\n"
            "需要 tools/wasm/（decrypt_worker.mjs + decrypt.wasm）与 Node.js；"
            "传统后端 binzDecrypt.exe 自 v1.1.3 起已从发布包移除（该二进制来源无明确许可）。"
        )

    produced = [
        p.name
        for p in work_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".osgjs", ".bin", ".gz"}
    ]
    if produced:
        log(f"  after decrypt: {', '.join(sorted(produced))}")
    else:
        still = [p.name for p in work_dir.glob("*.binz")]
        raise RuntimeError(
            "Decrypt produced no .osgjs/.bin.\n"
            f"Still present: {', '.join(still)}\n"
            "Static key or WASM module may have changed; re-run after git pull."
        )

def prepare_legacy_gz(work_dir: Path) -> None:
    for path in list(work_dir.glob("*.gz")):
        out = work_dir / path.name[:-3]
        data = path.read_bytes()
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
        out.write_bytes(data)
        path.unlink(missing_ok=True)


def ensure_osgjs_present(work_dir: Path) -> Path:
    osgjs = work_dir / "file.osgjs"
    if osgjs.is_file() and osgjs.stat().st_size > 0:
        return osgjs

    gz = work_dir / "file.osgjs.gz"
    if gz.is_file():
        data = gz.read_bytes()
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
        osgjs.write_bytes(data)
        return osgjs

    for p in work_dir.iterdir():
        if p.is_file() and "osgjs" in p.name.lower():
            if p.suffix == ".gz":
                data = p.read_bytes()
                try:
                    data = gzip.decompress(data)
                except OSError:
                    data = p.read_bytes()
                osgjs.write_bytes(data)
                return osgjs
            return p

    listing = ", ".join(sorted(x.name for x in work_dir.iterdir()))
    raise RuntimeError(
        "No file.osgjs after decryption.\n"
        f"Files: {listing}\n"
        "binz format may have changed — refresh tools (setup_tools.py)."
    )


def _prepare_osgjs_refs(work_dir: Path, osgjs: Path) -> None:
    """Point .binz / .gz names in osgjs to decrypted local .bin files."""
    try:
        text = osgjs.read_text(encoding="utf-8", errors="replace")
        fixed = (
            text.replace(".binz", ".bin")
            .replace("model_file.bin.gz", "model_file.bin")
            .replace("model_file_wireframe.bin.gz", "model_file_wireframe.bin")
        )
        if fixed != text:
            osgjs.write_text(fixed, encoding="utf-8")
    except Exception:
        pass


def _run_osgconv(work_dir: Path, osgjs_name: str, out_name: str) -> None:
    """Run osgconv with the original Sketchfab-Ripper parameters (best quality)."""
    if not OSGCONV.is_file():
        raise FileNotFoundError(f"osgconv not found: {OSGCONV}")
    run_tool(
        [
            str(OSGCONV),
            osgjs_name,
            out_name,
            "-O",
            "XParam=939161269",
            "-O",
            "NoTextureLoad",
        ],
        cwd=work_dir,
    )


def _osgconv_via_ascii_temp(work_dir: Path, model_name: str) -> Path:
    """
    osgconv often crashes on non-ASCII paths (Cyrillic folders).
    Copy the mesh files to a short ASCII temp dir, convert, copy back.
    """
    import tempfile

    out_name = f"{model_name}.gltf"
    needed = ["file.osgjs", "model_file.bin", "model_file_wireframe.bin"]
    with tempfile.TemporaryDirectory(prefix="sfosg_") as tmp:
        tmp_path = Path(tmp)
        for name in needed:
            src = work_dir / name
            if src.is_file():
                shutil.copy2(src, tmp_path / name)
        # osgjs must exist
        if not (tmp_path / "file.osgjs").is_file():
            raise FileNotFoundError("file.osgjs missing for osgconv temp convert")
        _run_osgconv(tmp_path, "file.osgjs", out_name)
        produced = tmp_path / out_name
        if not produced.is_file() or produced.stat().st_size <= 0:
            # collect any gltf
            gltfs = list(tmp_path.glob("*.gltf"))
            if not gltfs:
                raise RuntimeError("osgconv produced no glTF in temp dir")
            produced = gltfs[0]
        dest = work_dir / produced.name
        shutil.copy2(produced, dest)
        # copy sidecar bins if any
        for side in tmp_path.glob("*.bin"):
            if side.name.startswith("model_file"):
                continue
            shutil.copy2(side, work_dir / side.name)
        return dest


def convert_to_model(
    work_dir: Path,
    model_name: str,
    *,
    texture_map: Optional[dict] = None,
    prefer: str = "gltf",
) -> Path:
    """
    Convert decrypted Sketchfab mesh to a Blender-friendly format.

    Primary path (original working pipeline):
      binzDecrypt → osgconv → glTF  (same as first successful version)

    Fallbacks if osgconv crashes:
      1) osgconv in short ASCII temp directory
      2) package raw decrypted files (file.osgjs + *.bin) for manual convert
    """
    osgjs = ensure_osgjs_present(work_dir)
    _prepare_osgjs_refs(work_dir, osgjs)

    out_gltf = work_dir / f"{model_name}.gltf"
    errors: list[str] = []

    # --- 1) Direct osgconv (original recipe) ---
    if OSGCONV.is_file():
        try:
            _progress("step:convert")
            _run_osgconv(work_dir, osgjs.name, out_gltf.name)
            if out_gltf.is_file() and out_gltf.stat().st_size > 0:
                _progress(f"step:export|{out_gltf.name}|{out_gltf.stat().st_size}")
                return out_gltf
            gltfs = list(work_dir.glob("*.gltf"))
            if gltfs:
                _progress(f"step:export|{gltfs[0].name}|{gltfs[0].stat().st_size}")
                return gltfs[0]
        except Exception as e:
            errors.append(f"osgconv: {e}")
            _progress(f"step:convert_fail|{e}")

        # --- 2) ASCII temp path (fixes many Windows path crashes) ---
        try:
            _progress("step:convert")
            result = _osgconv_via_ascii_temp(work_dir, model_name)
            if result.is_file() and result.stat().st_size > 0:
                _progress(f"step:export|{result.name}|{result.stat().st_size}")
                return result
        except Exception as e:
            errors.append(f"osgconv-temp: {e}")
            _progress(f"step:convert_fail|{e}")

    # --- 3) Pure-Python glTF (viewer-accurate verts/indices; no weld hacks) ---
    try:
        from osgjs_convert import convert_osgjs

        _progress("step:convert")
        result = convert_osgjs(
            work_dir,
            model_name,
            prefer="gltf",
            texture_map=texture_map,
            weld=False,
            double_sided=False,
        )
        if result.is_file() and result.stat().st_size > 0:
            _progress(f"step:export|{result.name}|{result.stat().st_size}")
            return result
    except Exception as e:
        errors.append(f"python-gltf: {e}")
        _progress(f"step:convert_fail|{e}")

    # --- 4) Raw decrypted files as last resort ---
    raw_note = work_dir / f"{model_name}.RAW_OSGJS.txt"
    raw_note.write_text(
        "Converters failed. Folder has decrypted viewer files:\n"
        "  file.osgjs, model_file.bin, textures/\n"
        "Errors:\n- " + "\n- ".join(errors) + "\n",
        encoding="utf-8",
    )
    if osgjs.is_file():
        _progress(f"step:export|{osgjs.name}|{osgjs.stat().st_size}")
        return osgjs

    raise RuntimeError("Conversion failed:\n" + "\n".join(errors))


def cleanup_work_dir(work_dir: Path, keep: set[str]) -> None:
    for p in list(work_dir.iterdir()):
        if p.name in keep:
            continue
        try:
            if p.is_file():
                p.unlink()
            else:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def _extract_author(viewer_info: dict) -> str:
    user = viewer_info.get("user") or {}
    if isinstance(user, dict):
        for k in ("displayName", "username", "name"):
            if user.get(k):
                return str(user[k])
    return "unknown"


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+\n", "\n", text)
    return text.strip()


def download_one(
    url: str,
    *,
    out_root: Optional[Path] = None,
    proxy: Optional[str] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Download a single Sketchfab model (geometry only).

    Returns dict:
      path, dir, uid, name, author, description, source_url
    """
    prev_proxy = _get_proxy()
    prev_cb = _get_cb()
    if proxy is not None:
        set_proxy(proxy)
    if progress is not None:
        set_progress_callback(progress)

    tmp_dir: Optional[Path] = None
    try:
        uid = extract_uid(url)
        # Progress tokens are machine-readable for bot i18n: "step:<key>" or "step:<key>|<args>"
        _progress("step:viewer")
        viewer_info, embed_html = fetch_viewer_info(uid)
        model_name = resolve_model_name(uid, viewer_info)
        # 模型名可能含 Windows 不允许的字符（? : / 等）或过长，会导致建目录失败
        model_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", model_name).strip(" .")
        model_name = (model_name or uid[:8])[:80]
        author = _extract_author(viewer_info)
        description = _strip_html(str(viewer_info.get("description") or ""))[:1500]
        display_name = str(viewer_info.get("name") or model_name)
        _progress(f"step:model|{display_name}|{author}")

        root = Path(out_root) if out_root else DOWNLOADS
        root.mkdir(parents=True, exist_ok=True)
        final_dir = root / f"{model_name}-{uid[:8]}"
        # 先下载到同盘的临时目录，**全部成功之后**才替换旧目录。
        # 早期版本是下载前直接 rmtree(final_dir)：一旦断网/中途退出，
        # 之前已经下好的模型也被删了，属于数据丢失级缺陷。
        out_dir = Path(tempfile.mkdtemp(prefix=".sftmp-", dir=str(root)))
        tmp_dir: Optional[Path] = out_dir

        new_format = is_new_binz_format(viewer_info)
        _progress(f"step:format|{'binz' if new_format else 'legacy'}")

        key1 = key2 = None
        if new_format:
            key1 = viewer_info["files"][0]["p"][0]["b"]
            _progress("step:keys")
            key2 = extract_key2(embed_html)
            if not key2:
                _progress("step:keys_partial")
        else:
            _progress("step:legacy")

        _progress("step:mesh")
        download_model_files(viewer_info, out_dir, new_format)

        if new_format:
            _progress("step:decrypt")
            decrypt_binz(out_dir, key1, key2, embed_html=embed_html)
        else:
            prepare_legacy_gz(out_dir)

        # Textures (public texture API — no Sketchfab login)
        # v1.1.8：这里是唯一的贴图下载点，Node 转换器改为读本地 textures/（SFD-005）
        tex_map: dict = {}
        try:
            from textures import download_textures

            _progress("step:textures")
            tex_dir = out_dir / "textures"
            tex_map = download_textures(
                uid,
                tex_dir,
                http_get_json=http_get_json,
                download_file=download_file,
                progress=_progress,
            )
        except Exception as e:
            _progress(f"step:textures_warn|{e}")

        model_path = convert_to_model(
            out_dir, model_name, texture_map=tex_map, prefer="gltf"
        )

        # 解密材料用完即弃：key.txt / key2.txt 对外发目录毫无意义，还是可复用的
        # 解密材料。需要排障时用 SKFB_KEEP_INTERMEDIATES=1 保留（SFD-010）。
        if not KEEP_INTERMEDIATES:
            for _k in ("key.txt", "key2.txt"):
                try:
                    (out_dir / _k).unlink()
                except OSError:
                    pass

        # Keep user-facing assets：白名单，不再用 *.txt 通配（SFD-010）
        final_keep = {
            model_path.name,
            "info.json",
            "textures",
            "file.osgjs",
            "model_file.bin",
            "model_file_wireframe.bin",
        }
        for pat in ("*.gltf", "*.glb", "*.bin", "*.obj", "*.mtl"):
            for p in out_dir.glob(pat):
                final_keep.add(p.name)
        if KEEP_INTERMEDIATES:
            final_keep |= {"key.txt", "key2.txt"}

        cleanup_work_dir(out_dir, final_keep)

        # 全部产物就绪 → 原子替换旧目录（旧目录先改名备份，替换失败自动回滚）
        backup = None
        if final_dir.exists():
            backup = final_dir.with_name(final_dir.name + ".old")
            shutil.rmtree(backup, ignore_errors=True)
            final_dir.rename(backup)
        try:
            out_dir.rename(final_dir)
        except Exception:
            if backup is not None and backup.exists() and not final_dir.exists():
                backup.rename(final_dir)
            raise
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        out_dir = final_dir

        # Prefer largest glTF/GLB as main deliverable if present
        gltfs = list(out_dir.glob("*.gltf")) + list(out_dir.glob("*.glb"))
        if gltfs:
            model_path = max(gltfs, key=lambda p: p.stat().st_size)
        else:
            model_path = final_dir / model_path.name

        # info.json 必须在 rename **之后**写：之前写在 .sftmp-* 里，
        # 目录被改名后 output 指向一个已经不存在的路径（SFD-007）。
        tex_files = sorted(
            p.name for p in (out_dir / "textures").glob("*")
            if p.suffix.lower() in IMAGE_EXTS
        ) if (out_dir / "textures").is_dir() else []
        anim_count = int(viewer_info.get("animationCount") or 0)
        meta = {
            "uid": uid,
            "name": display_name,
            "file_name": model_name,
            "author": author,
            "description": description,
            "source_url": url.strip(),
            "output": str(model_path),
            "primary": model_path.name,
            "format": model_path.suffix.lower().lstrip("."),
            "deliverables": sorted(
                p.name for p in out_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".glb", ".gltf", ".bin"}
            ),
            "textures": len(tex_files),
            "texture_files": tex_files,
            "texture_manifest": "textures/textures_manifest.json"
            if (out_dir / "textures" / "textures_manifest.json").is_file() else None,
            "animations": {
                "supported": False,
                "count": anim_count,
                "note": "本工具导出静态几何 + 材质贴图；动画/骨骼/形变不导出",
            },
            "note": "glTF 为主交付物；GLB 由 GUI 转换后追加到本文件",
        }
        (out_dir / "info.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if anim_count:
            _progress(f"step:anim_warn|{anim_count}")

        _progress("step:done")
        return {
            "path": model_path,
            "dir": out_dir,
            "uid": uid,
            "name": display_name,
            "file_name": model_name,
            "author": author,
            "description": description,
            "source_url": url.strip(),
        }
    finally:
        # 失败路径：临时目录还在说明没替换成功，直接清掉，旧目录毫发无伤
        try:
            if tmp_dir is not None and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        set_proxy(prev_proxy)
        set_progress_callback(prev_cb)


def update_info_json(folder: Path, **fields) -> Optional[dict]:
    """
    合并更新输出目录里的 info.json（GUI 生成 GLB 之后调用）。

    GLB 由 Node 转换器在 Python 侧返回**之后**才产生，所以 download_one 写的
    info.json 只知道 glTF。这里把主交付物改成实际的 GLB，并追加转换报告里的
    动画/贴图告警（SFD-007 / SFD-004 / SFD-011）。

    返回更新后的字典；info.json 不存在时返回 None（不新建，避免掩盖异常）。
    """
    folder = Path(folder)
    path = folder / "info.json"
    if not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None

    meta.update(fields)

    primary = meta.get("primary")
    if primary:
        target = folder / str(primary)
        if target.is_file():
            meta["output"] = str(target)
            meta["format"] = target.suffix.lower().lstrip(".")

    files = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in {".glb", ".gltf", ".bin"}:
            files.append(p.name)
    if files:
        meta["deliverables"] = files
    # 转换阶段会把解密的 .jpg 改写成 .png，所以贴图清单要在此时重算一次
    if (folder / "textures").is_dir():
        img_files = sorted(
            p.name for p in (folder / "textures").glob("*")
            if p.suffix.lower() in IMAGE_EXTS
        )
        meta["textures"] = len(img_files)
        meta["texture_files"] = img_files
    else:
        meta["textures"] = 0

    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def check_tools(*, quiet: bool = False) -> bool:
    ok = True
    node = shutil.which("node") or shutil.which("nodejs")
    worker, wasm = _wasm_decrypt_paths()
    if not worker or not wasm:
        if not quiet:
            log(f"WARNING: missing WASM decrypt tools under {_WASM_DIR}")
            log("  Expected: tools/wasm/decrypt_worker.mjs + decrypt.wasm")
        ok = False
    if not node:
        if not quiet:
            log("WARNING: Node.js not found (required for .binz decrypt)")
            log("  Install: https://nodejs.org/  or  sudo apt install nodejs")
        ok = False
    if not OSGCONV.is_file():
        if not quiet:
            log(f"WARNING: missing {OSGCONV} (optional if Python glTF fallback is OK)")
            log("  Run once:  python setup_tools.py")
    if not quiet:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        log(f"node: {node or 'not found'}")
        log(f"wasm: {'ok' if worker and wasm else 'missing'}")
        if curl:
            log(f"curl: {curl}")
        else:
            log("curl: not found (Python HTTP will be used)")
        if not sys.platform.startswith("win"):
            wine = _find_wine()
            log(f"wine: {wine or 'not found'} (optional, for osgconv.exe)")
        cached = _load_cached_static_key()
        if cached:
            log(f"cached static key: {cached[:8]}…")
    # Node+WASM is the critical path today
    return bool(ok and node and worker and wasm)


def _normalize_url(raw: str) -> Optional[str]:
    raw = (raw or "").strip().strip("\"'")
    if not raw:
        return None
    if raw.startswith("http"):
        m = URL_RE.search(raw)
        return m.group(0) if m else raw.rstrip(").,]}>'\"")
    if re.fullmatch(r"[a-f0-9]{32}", raw, re.I):
        return f"https://sketchfab.com/3d-models/{raw}"
    m = URL_RE.search(raw)
    return m.group(0) if m else None


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Download public Sketchfab 3D models (no account / API keys). "
            "Works for models without an official Download button."
        ),
        epilog=(
            "Examples:\n"
            "  python main.py https://sketchfab.com/3d-models/...\n"
            "  python main.py MODEL_UID\n"
            "  python main.py -o ./out URL1 URL2\n"
            "  python main.py --proxy http://127.0.0.1:8080 URL\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "urls",
        nargs="*",
        help="Sketchfab model URL(s) or 32-char model UID(s)",
    )
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help=f"Output directory (default: {DOWNLOADS})",
    )
    p.add_argument(
        "--proxy",
        default="",
        help="HTTP(S)/SOCKS proxy, e.g. http://user:pass@host:port",
    )
    p.add_argument(
        "--check-tools",
        action="store_true",
        help="Only verify tools/Wine/curl and exit",
    )
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Less banner text",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    _fix_console()
    args = _parse_args(argv)

    if not args.quiet:
        log("=" * 58)
        log("  Sketchfab Model Downloader (CLI)")
        log("  • no account / no API keys")
        log("  • public models, even without Download button")
        log("  • output: glTF (+ textures when available)")
        log("=" * 58)

    tools_ok = check_tools(quiet=args.quiet)
    if args.check_tools:
        return 0 if tools_ok else 2

    out_root = Path(args.output) if args.output else DOWNLOADS
    out_root.mkdir(parents=True, exist_ok=True)
    if not args.quiet:
        log(f"Output folder: {out_root.resolve()}")
        log()

    raw_urls = list(args.urls)
    if not raw_urls:
        try:
            typed = input("Sketchfab model URL (or UID):\n> ").strip()
        except EOFError:
            typed = ""
        if typed:
            raw_urls = [typed]

    if not raw_urls:
        log("No URL given. Example:")
        log("  python main.py https://sketchfab.com/3d-models/your-model-uid")
        return 1

    urls: list[str] = []
    for raw in raw_urls:
        u = _normalize_url(raw)
        if not u:
            log(f"Skip (not a Sketchfab link): {raw}")
            continue
        urls.append(u)

    if not urls:
        log("No valid Sketchfab URLs.")
        return 1

    if args.proxy:
        set_proxy(args.proxy)
        if not args.quiet:
            log(f"Proxy: {args.proxy}")

    ok_n = fail_n = 0
    for i, url in enumerate(urls, start=1):
        if len(urls) > 1:
            log()
            log(f"--- [{i}/{len(urls)}] {url}")
        try:
            result = download_one(url, out_root=out_root)
        except KeyboardInterrupt:
            log("\nCancelled.")
            return 130
        except Exception as e:
            log(f"\nERROR: {e}")
            fail_n += 1
            continue

        path = Path(result["path"])
        log()
        log("Done!")
        log(f"  Model:  {result.get('name') or path.stem}")
        log(f"  Author: {result.get('author') or 'unknown'}")
        log(f"  File:   {path}")
        log(f"  Folder: {path.parent}")
        ok_n += 1

    if len(urls) > 1:
        log()
        log(f"Summary: {ok_n} ok, {fail_n} failed (of {len(urls)})")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
