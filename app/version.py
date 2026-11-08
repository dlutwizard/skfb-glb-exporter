# -*- coding: utf-8 -*-
"""
版本唯一真源（Single Source of Truth）。

本模块**不得 import 任何东西**——构建脚本 `build/build.py` 会在没有 tkinter、
没有 PyInstaller 环境的解释器里直接读取它（`exec` / `runpy` 均可），
一旦这里 import 了 GUI 依赖，构建就会在不该失败的地方失败。

改版本号：只改 `__version__` 一行，同时把 `VERSION_TUPLE` 对齐
（模块尾有断言，不一致直接 ImportError 级别的 AssertionError）。
"""

APP_NAME = "Sketchfab下载器"
APP_ID = "SketchfabDownloader"       # exe 的 InternalName
__version__ = "1.3.1"                # ← 改版本号只改这一行
VERSION_TUPLE = (1, 3, 1)            # 必须与 __version__ 一致（模块尾断言）

# ---- 更新检查（默认全关：未配置时零网络请求）----------------------------
GITHUB_REPO = ""                     # "owner/name"；空 = 更新检查整体关闭
UPDATE_CHECK_DEFAULT = False         # 仅当 GITHUB_REPO 非空时有意义；仍只允许手动点
CHANGELOG_HEAD = 5                   # 关于面板展示的更新日志条数

# ---- 构建开关 -----------------------------------------------------------
SINGLE_FILE_EXPERIMENTAL = False     # v1.1 默认不产出单文件 exe（启动 40s vs onedir 1.7s）

# ---- 其它元信息 ---------------------------------------------------------
APP_DESCRIPTION = "Sketchfab 模型下载器"
COPYRIGHT = "仅限个人学习使用"
COMPANY = "个人工具"

# 版本解析允许的分隔符（"1.2.3-beta" / "v1.2.3" / "1.2.3+build" 都能吃）
_SEPARATORS = ".-_+~ "


def version_string() -> str:
    """返回形如 ``"1.2.3"`` 的版本字符串。"""
    return __version__


def parse_version(s) -> tuple:
    """
    把版本字符串解析成整数元组。

    ``"1.2.3-beta"`` → ``(1, 2, 3)``；``"v1.1"`` → ``(1, 1)``；
    非法输入（含没有数字、非字符串）→ ``()``。
    """
    if not isinstance(s, str):
        return ()
    raw = s.strip()
    if not raw:
        return ()
    if raw[0] in ("v", "V"):
        raw = raw[1:]
    parts = []
    buf = ""
    for ch in raw:
        if ch.isdigit():
            buf += ch
        elif ch in _SEPARATORS:
            if buf:
                parts.append(int(buf))
                buf = ""
            # 连续分隔符或前导分隔符：忽略
        else:
            # 遇到版本号之外的内容（如 "-beta" 的字母）就停止
            break
    if buf:
        parts.append(int(buf))
    return tuple(parts)


def dotted(t) -> str:
    """把整数元组还原成 ``"1.2.3"`` 形式的点分字符串。"""
    if not t:
        return ""
    try:
        return ".".join(str(int(x)) for x in t)
    except (TypeError, ValueError):
        return ""


def is_newer(cur: str, latest: str) -> bool:
    """
    判断 ``latest`` 是否比 ``cur`` 新（逐段整数比较，缺位补 0）。

    任一侧无法解析时返回 ``False``——更新检查宁可"不提示"也不要误报。
    """
    a = parse_version(cur)
    b = parse_version(latest)
    if not a or not b:
        return False
    width = max(len(a), len(b))
    left = tuple(a) + (0,) * (width - len(a))
    right = tuple(b) + (0,) * (width - len(b))
    return right > left


def version_info_dict() -> dict:
    """给 ``--version --json`` 与 build_info 用的一小块结构化元信息。"""
    return {
        "app": APP_ID,
        "name": APP_NAME,
        "version": __version__,
        "version_tuple": list(VERSION_TUPLE),
        "github_repo": GITHUB_REPO,
        "single_file_experimental": SINGLE_FILE_EXPERIMENTAL,
    }


# 一致性断言：放在模块尾部，任何 import 本模块的代码都会立刻得到反馈
assert VERSION_TUPLE == tuple(int(x) for x in __version__.split(".")), (
    "version.py 内部不一致：VERSION_TUPLE=%r 与 __version__=%r 不匹配"
    % (VERSION_TUPLE, __version__)
)


if __name__ == "__main__":
    # 验收用：`python app/version.py` 必须输出当前版本号（与 __version__ 一致）
    print(__version__)
