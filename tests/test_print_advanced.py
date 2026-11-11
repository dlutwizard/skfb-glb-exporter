# -*- coding: utf-8 -*-
"""高级打印转换调用层（纯标准库部分 + 真实端到端冒烟）。"""
import sys
from pathlib import Path

import pytest

import print_advanced as pa

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """探测结果有模块级缓存；每个用例必须从干净缓存开始。"""
    pa._CACHE.clear()
    yield
    pa._CACHE.clear()


def test_resolve_target_length_defaults_and_clamps():
    assert pa.resolve_target_length(244) == 244.0
    assert pa.resolve_target_length("180") == 180.0
    assert pa.resolve_target_length(9999) == 312.0          # 320 - 8
    assert pa.resolve_target_length(-5) == 5.0
    assert pa.resolve_target_length("oops") == 244.0
    assert pa.resolve_target_length(244, auto_fit=True) == 312.0


def test_resolve_target_length_follows_build_volume():
    assert pa.resolve_target_length(244, build_volume=(250, 260, 270)) == 242.0


def test_output_paths_never_overwrite_source(tmp_path):
    glb = tmp_path / "car.glb"
    glb.write_bytes(b"x")
    stl, mf3 = pa.output_paths(glb)
    assert stl.name == "car.stl"
    assert mf3.name == "car.3mf"

    stl_src = tmp_path / "part.stl"
    stl_src.write_bytes(b"x")
    stl, mf3 = pa.output_paths(stl_src)
    assert stl.name == "part_print.stl"
    assert mf3.name == "part_print.3mf"
    assert stl.resolve() != stl_src.resolve()


def test_output_paths_uses_given_outdir(tmp_path):
    src = tmp_path / "a.glb"
    src.write_bytes(b"x")
    out = tmp_path / "exports"
    stl, mf3 = pa.output_paths(src, out)
    assert stl.parent == out and mf3.parent == out


def test_candidate_pythons_puts_explicit_first_and_dedups():
    items = pa.candidate_pythons(r"D:\Anaconda\python.exe")
    assert items[0] == r"D:\Anaconda\python.exe"
    lowered = [str(x).lower() for x in items]
    assert len(lowered) == len(set(lowered))


def test_find_advanced_python_uses_first_probe_hit(monkeypatch):
    calls = []

    def fake_probe(interpreter):
        calls.append(interpreter)
        return (interpreter == "second", "3.11")

    monkeypatch.setattr(pa, "_probe", fake_probe)
    monkeypatch.setattr(pa, "candidate_pythons",
                        lambda explicit=None: ["first", "second", "third"])
    pa._CACHE.clear()
    assert pa.find_advanced_python(refresh=True) == "second"
    assert calls == ["first", "second"]


def test_find_advanced_python_negative_cache(monkeypatch):
    calls = []

    def fake_probe(interpreter):
        calls.append(interpreter)
        return False, "missing"

    monkeypatch.setattr(pa, "_probe", fake_probe)
    monkeypatch.setattr(pa, "candidate_pythons", lambda explicit=None: ["only"])
    pa._CACHE.clear()
    assert pa.find_advanced_python(refresh=True) is None
    assert pa.find_advanced_python() is None
    assert calls == ["only"]


def test_bundled_worker_wins_over_external_python(monkeypatch):
    sentinel = Path(r"C:\bundle\advanced\worker\advanced_print_worker.exe")
    monkeypatch.setattr(pa, "bundled_worker", lambda: sentinel)
    monkeypatch.setattr(pa, "find_advanced_python",
                        lambda *a, **k: pytest.fail("不应再探测外部 Python"))
    env = pa.check_environment()
    assert env["ok"] is True and env["bundled"] is True
    assert env["python"] == str(sentinel)


def test_worker_script_is_next_to_module():
    worker = pa.worker_script()
    assert worker.name == "advanced_print_worker.py"
    assert worker.is_file()


def test_describe_for_log_reports_watertight():
    lines = pa.describe_for_log({
        "facets": 1200, "components": 1, "bbox_mm": [10, 20, 30],
        "watertight": True, "boundary_edges": 0, "nonmanifold_edges": 0,
        "volume_cm3": 2.5, "grams_estimate_pla": 3.1, "pitch_mm": 0.6,
        "dropped_components": 4, "source_faces": 900, "elapsed_sec": 12,
    })
    text = "\n".join(lines)
    assert "水密校验：通过" in text
    assert "10.00 x 20.00 x 30.00 mm" in text
    assert "过滤 4" in text


def test_convert_rejects_missing_and_unsupported(tmp_path):
    with pytest.raises(pa.AdvancedPrintError, match="不存在"):
        pa.convert(tmp_path / "nope.glb")
    bad = tmp_path / "model.txt"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(pa.AdvancedPrintError, match="不支持"):
        pa.convert(bad)


def test_convert_reports_missing_environment(monkeypatch, tmp_path):
    src = tmp_path / "a.glb"
    src.write_bytes(b"glTF")
    monkeypatch.setattr(pa, "bundled_worker", lambda: None)
    monkeypatch.setattr(pa, "find_advanced_python", lambda *a, **k: None)
    with pytest.raises(pa.AdvancedPrintError) as info:
        pa.convert(src)
    assert "未找到可用的高级打印环境" in str(info.value)
    assert "pip install" in info.value.hint


@pytest.mark.skipif(not pa.check_environment()["ok"],
                    reason="本机没有 numpy/scipy/scikit-image/trimesh 环境")
def test_advanced_pipeline_end_to_end(tmp_path):
    from test_print_export import _cube_glb

    src = _cube_glb(tmp_path)
    out = tmp_path / "out"
    report = pa.convert(src, outdir=out, target_length_mm=40,
                        quality="standard")
    assert report["watertight"] is True
    assert report["boundary_edges"] == 0
    assert report["nonmanifold_edges"] == 0
    assert report["components"] >= 1
    assert abs(max(report["bbox_mm"]) - 40.0) < 2.0
    for path in report["outputs"]:
        assert Path(path).is_file() and Path(path).stat().st_size > 0
    assert (out / "advanced_report.json").is_file()