# -*- coding: utf-8 -*-
"""
引擎下载流水线回归测试（离线，全部 mock 掉网络与 Node）。

覆盖审查项：
  * SFD-003 进度回调必须在工作线程可达（含并发不串台）
  * SFD-001 失败路径不得删除旧目录、不得残留 .sftmp-*/.old
  * SFD-007 info.json.output 必须指向最终真实文件
  * SFD-010 默认不保留 key.txt；textures 只统计图片
"""
import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "dist_engine"

# 引擎内部有 `from textures import ...`，需要引擎目录在 sys.path 上
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import textures as tex_mod  # noqa: E402


def _load_engine():
    spec = importlib.util.spec_from_file_location("skfb_engine", ENGINE_DIR / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def engine():
    return _load_engine()


UID = "3984349b2dbc4cb287e998164700fa60"
URL = "https://sketchfab.com/3d-models/test-model-" + UID


@pytest.fixture
def fake_network(monkeypatch, engine):
    """把引擎的全部网络/转换步骤换成离线桩。"""
    viewer = {
        "name": "AtomicModel",
        "description": "<p>desc</p>",
        "user": {"username": "tester"},
        "animationCount": 0,
        "files": [{"p": [{"b": "KEY1"}]}],
    }

    monkeypatch.setattr(engine, "fetch_viewer_info", lambda uid: (viewer, "<html>KEY2</html>"))
    monkeypatch.setattr(engine, "resolve_model_name", lambda uid, vi: "AtomicModel")
    monkeypatch.setattr(engine, "_extract_author", lambda vi: "tester")
    monkeypatch.setattr(engine, "is_new_binz_format", lambda vi: True)
    monkeypatch.setattr(engine, "extract_key2", lambda html: "STATICKEY")

    def fake_download_model_files(vi, out_dir: Path, new_format):
        (out_dir / "file.osgjs").write_text("{}", encoding="utf-8")
        (out_dir / "model_file.bin").write_bytes(b"mesh")

    monkeypatch.setattr(engine, "download_model_files", fake_download_model_files)

    def fake_decrypt(work_dir: Path, key1, key2, *, embed_html=None):
        # 真实实现会写 key.txt，用来验证下载结束后是否被清掉
        (work_dir / "key.txt").write_text("KEY1", encoding="utf-8")

    monkeypatch.setattr(engine, "decrypt_binz", fake_decrypt)

    def fake_textures(uid, out_dir, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        mapping = {}
        for name in ("a.png", "b.png"):
            p = out_dir / name
            p.write_bytes(b"\x89PNG\r\n\x1a\n")
            mapping[name] = p
        (out_dir / "textures_manifest.json").write_text(
            json.dumps({"uid": uid, "count": 2, "images": 2, "files": ["a.png", "b.png"],
                        "textures": [], "failures": []}), encoding="utf-8")
        return mapping

    monkeypatch.setitem(sys.modules, "textures", tex_mod)
    monkeypatch.setattr(tex_mod, "download_textures", fake_textures)

    def fake_convert(out_dir: Path, model_name: str, texture_map=None, prefer="gltf"):
        p = out_dir / (model_name + ".gltf")
        p.write_text("{}", encoding="utf-8")
        return p

    monkeypatch.setattr(engine, "convert_to_model", fake_convert)
    return viewer


def test_success_writes_info_json_with_real_final_path(engine, fake_network, tmp_path):
    """SFD-007：output / deliverables 必须指向最终目录里真实存在的文件。"""
    res = engine.download_one(URL, out_root=tmp_path)
    folder = Path(res["dir"])

    assert folder.is_dir()
    assert not list(tmp_path.glob(".sftmp-*")), "临时目录未清理"

    meta = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    out = Path(meta["output"])
    assert out.exists(), "info.json.output 指向不存在的路径：%s" % out
    assert out.parent == folder, "output 应位于最终目录内：%s" % out
    assert ".sftmp-" not in str(out)
    assert meta["primary"] == out.name
    assert "AtomicModel.gltf" in meta["deliverables"]


def test_textures_counted_without_manifest(engine, fake_network, tmp_path):
    """SFD-010：textures 只数图片，textures_manifest.json 不算。"""
    res = engine.download_one(URL, out_root=tmp_path)
    meta = json.loads((Path(res["dir"]) / "info.json").read_text(encoding="utf-8"))
    assert meta["textures"] == 2, meta.get("textures")
    assert meta["texture_files"] == ["a.png", "b.png"]


def test_key_txt_removed_by_default(engine, fake_network, tmp_path):
    """SFD-010：解密材料不留输出目录。"""
    res = engine.download_one(URL, out_root=tmp_path)
    folder = Path(res["dir"])
    assert not (folder / "key.txt").exists()
    assert not (folder / "key2.txt").exists()


def test_key_txt_kept_when_debug_flag(engine, fake_network, tmp_path, monkeypatch):
    """SFD-010：SKFB_KEEP_INTERMEDIATES=1 时可保留（排障用）。"""
    monkeypatch.setattr(engine, "KEEP_INTERMEDIATES", True)
    res = engine.download_one(URL, out_root=tmp_path)
    assert (Path(res["dir"]) / "key.txt").exists()


def test_failure_keeps_old_model_and_no_leftovers(engine, monkeypatch, tmp_path):
    """SFD-001：下载中途失败不得动旧目录，也不得残留临时目录。"""
    old = tmp_path / ("AtomicModel-" + UID[:8])
    old.mkdir()
    (old / "OLD_SENTINEL.txt").write_text("old-good", encoding="utf-8")

    viewer = {"name": "AtomicModel", "files": [{"p": [{"b": "K"}]}], "animationCount": 0}
    monkeypatch.setattr(engine, "fetch_viewer_info", lambda uid: (viewer, ""))
    monkeypatch.setattr(engine, "resolve_model_name", lambda uid, vi: "AtomicModel")
    monkeypatch.setattr(engine, "_extract_author", lambda vi: "t")
    monkeypatch.setattr(engine, "is_new_binz_format", lambda vi: True)
    monkeypatch.setattr(engine, "extract_key2", lambda html: "K2")

    def boom(*a, **kw):
        raise OSError("network down")

    monkeypatch.setattr(engine, "download_model_files", boom)

    with pytest.raises(OSError):
        engine.download_one(URL, out_root=tmp_path)

    assert (old / "OLD_SENTINEL.txt").read_text(encoding="utf-8") == "old-good"
    assert not list(tmp_path.glob(".sftmp-*"))
    assert not list(tmp_path.glob("*.old"))


def test_success_replaces_old_dir(engine, fake_network, tmp_path):
    """SFD-001：成功时原子替换掉旧目录。"""
    old = tmp_path / ("AtomicModel-" + UID[:8])
    old.mkdir()
    (old / "OLD_SENTINEL.txt").write_text("old-good", encoding="utf-8")

    engine.download_one(URL, out_root=tmp_path)

    assert not (old / "OLD_SENTINEL.txt").exists(), "旧目录内容应被替换"
    assert (old / "info.json").is_file()
    assert not list(tmp_path.glob("*.old"))


# ---------------------------------------------------------------------------
# SFD-003：进度回调必须真的到达工作线程
# ---------------------------------------------------------------------------

def test_progress_callback_reaches_worker_thread(engine, fake_network, tmp_path):
    """主线程设置的回调不该被误认为自动对所有线程生效；显式传入才可靠。"""
    seen = []

    def cb(msg):
        seen.append(msg)

    t = threading.Thread(target=lambda: engine.download_one(
        URL, out_root=tmp_path / "w1", progress=cb))
    t.start()
    t.join(60)

    assert any(m.startswith("step:viewer") for m in seen), seen
    assert "step:done" in seen, seen


def test_concurrent_tasks_do_not_mix_callbacks(engine, fake_network, tmp_path):
    """两个并发任务各自收到自己的进度，互不串台。"""
    seen = {"A": [], "B": []}
    lock = threading.Lock()

    def make_cb(tag):
        def cb(msg):
            with lock:
                seen[tag].append(msg)
        return cb

    threads = [
        threading.Thread(target=lambda tag=tag: engine.download_one(
            URL, out_root=tmp_path / tag, progress=make_cb(tag)))
        for tag in ("A", "B")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)

    for tag in ("A", "B"):
        assert any(m.startswith("step:viewer") for m in seen[tag]), (tag, seen[tag])
        assert "step:done" in seen[tag], (tag, seen[tag])
    # 每边都只应看到自己那一轮（每轮 viewer/done 各一次）
    assert seen["A"].count("step:done") == 1
    assert seen["B"].count("step:done") == 1


def test_main_thread_callback_is_not_global(engine, fake_network, tmp_path):
    """反向守卫：仅 set_progress_callback 的线程收得到，别的线程收不到（这正是原 bug）。"""
    main_seen = []
    engine.set_progress_callback(main_seen.append)
    try:
        worker_seen = []

        t = threading.Thread(target=lambda: engine.download_one(
            URL, out_root=tmp_path / "w", progress=worker_seen.append))
        t.start()
        t.join(60)
    finally:
        engine.set_progress_callback(None)

    assert main_seen == [], "工作线程的进度不应灌进主线程的回调"
    assert worker_seen, "显式传入的回调必须收到事件"


# ---------------------------------------------------------------------------
# SFD-007：GLB 生成后回写 info.json
# ---------------------------------------------------------------------------

def test_update_info_json_promotes_glb(engine, fake_network, tmp_path):
    res = engine.download_one(URL, out_root=tmp_path)
    folder = Path(res["dir"])
    glb = folder / "AtomicModel.glb"
    glb.write_bytes(b"glTF" + b"\0" * 64)

    meta = engine.update_info_json(folder, primary=glb.name)
    assert meta is not None
    assert Path(meta["output"]) == glb
    assert Path(meta["output"]).exists()
    assert meta["format"] == "glb"
    assert "AtomicModel.glb" in meta["deliverables"]


def test_update_info_json_missing_file_is_noop(engine, tmp_path):
    assert engine.update_info_json(tmp_path, primary="x.glb") is None


# ---------------------------------------------------------------------------
# SFD-005：贴图选择必须优先「能解密」的 2 的幂变体
# ---------------------------------------------------------------------------

def test_pick_best_image_prefers_power_of_two():
    images = [
        {"width": 800, "height": 100, "size": 999999, "url": "x.jpg"},   # 更大但解不出来
        {"width": 1024, "height": 1024, "size": 500000, "url": "y.png"},
    ]
    best = tex_mod.pick_best_image(images, pk=12345)
    assert best["width"] == 1024, "带 pk 时必须优先 2 的幂变体"


def test_pick_best_image_without_pk_takes_largest():
    images = [
        {"width": 800, "height": 100, "size": 10, "url": "x.jpg"},
        {"width": 2048, "height": 2048, "size": 999999, "url": "y.png"},
    ]
    assert tex_mod.pick_best_image(images, pk=None)["width"] == 2048


def test_is_descramblable():
    assert tex_mod.is_descramblable({"width": 1024, "height": 512}, 7) is True
    assert tex_mod.is_descramblable({"width": 800, "height": 100}, 7) is False
    assert tex_mod.is_descramblable({"width": 800, "height": 100}, None) is True


# ---------------------------------------------------------------------------
# SFD-005（ROUND-02 复验补丁）：管线接壤处的两个"看起来对、实际没接上"的坑
#   1) 引擎用不带参数的 /textures 端点 → 下 36 张，转换器只要 14 张
#   2) 清单写在 textures/ 子目录，转换器在模型根目录找 → 永远走联网兜底
# ---------------------------------------------------------------------------

def test_fetch_texture_manifest_prefers_optimized_endpoint():
    seen = []

    def fake_get_json(url):
        seen.append(url)
        if "optimized=1" in url:
            return {"results": [{"uid": "u1"}]}
        return {"results": [{"uid": "u1"}, {"uid": "u2"}, {"uid": "u3"}]}

    results = tex_mod.fetch_texture_manifest("abc", fake_get_json)
    assert [r["uid"] for r in results] == ["u1"], "必须优先 optimized（与查看器同源）"
    assert seen == ["https://sketchfab.com/i/models/abc/textures?optimized=1"]


def test_fetch_texture_manifest_falls_back_to_plain():
    def fake_get_json(url):
        if "optimized=1" in url:
            raise RuntimeError("404")
        return {"results": [{"uid": "u1"}, {"uid": "u2"}]}

    assert len(tex_mod.fetch_texture_manifest("abc", fake_get_json)) == 2


def test_fetch_texture_manifest_empty_when_nothing_available():
    def fake_get_json(url):
        raise RuntimeError("offline")

    assert tex_mod.fetch_texture_manifest("abc", fake_get_json) == []


def test_converter_reads_manifest_from_textures_subdir():
    """静态守卫：清单路径必须覆盖引擎实际写的位置（textures/ 子目录）。"""
    src = (ROOT / "app" / "convert.mjs").read_text(encoding="utf-8")
    assert "path.join(dir, 'textures', 'textures_manifest.json')" in src, (
        "convert.mjs 未在 textures/ 子目录查找清单，会退化成联网重下（SFD-005 回归）"
    )
