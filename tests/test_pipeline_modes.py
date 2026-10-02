import json

from fusion_video_pipeline.canonical import SourceUnit
from fusion_video_pipeline.config import Settings
from fusion_video_pipeline.pipeline import FusionPipeline


def settings(tmp_path):
    return Settings(
        project_root=tmp_path,
        note_base_url="https://example.invalid",
        note_model="tree-model",
        note_api_key="tree-key",
        report_provider="provider",
        report_model="report-model",
        report_api_key="report-key",
        obsidian_root=tmp_path / "vault",
        credential_path=tmp_path / "credential.json",
    )


def source_fixture(tmp_path):
    source = tmp_path / "subtitle.txt"
    source.write_text("字幕", encoding="utf-8")
    units = [SourceUnit("unit-000001", "字幕", str(source), line_start=1, line_end=1)]
    metadata = {"title": "测试", "uploader": "", "url": "", "bvid": ""}
    return source, units, metadata


def outline_fixture():
    return {
        "type": "逻辑型",
        "title": "测试",
        "thesis": "测试论点",
        "outline": [
            {
                "id": "b1",
                "heading": "分支",
                "source_units": ["unit-000001"],
                "points": [
                    {
                        "id": "p1",
                        "parent_id": None,
                        "level": 1,
                        "title": "节点",
                        "source_units": ["unit-000001"],
                    }
                ],
            }
        ],
        "summary": [{"text": "结论", "source_units": ["unit-000001"]}],
    }


def test_tree_only_does_not_generate_report(tmp_path, monkeypatch):
    source, units, metadata = source_fixture(tmp_path)
    pipeline = FusionPipeline(settings(tmp_path))
    monkeypatch.setattr(pipeline, "_load_source", lambda *args, **kwargs: (units, metadata))
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.generate_outline",
        lambda *args, **kwargs: (
            outline_fixture(),
            {"type": "逻辑型", "title": "测试", "thesis": "测试", "branches": []},
            {"strategy": "axis-first-v2", "total_tokens": 12, "attempts": 2},
        ),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.generate_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("report should not run")),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_markdown",
        lambda outline, metadata, path: path.write_text("# 笔记", encoding="utf-8"),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_svg",
        lambda outline, path: path.write_text("<svg></svg>", encoding="utf-8"),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_png",
        lambda source, path: path.write_bytes(b"png"),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.inspect_svg_layout",
        lambda path: {"status": "checked", "findings": []},
    )
    result = pipeline.run(str(source), outputs={"tree"}, publish=False)
    assert result["outputs"] == ["tree"]
    assert "tree_html" in result
    assert "report_html" not in result
    assert result["usage"]["notes"]["total_tokens"] == 12


def test_report_only_does_not_generate_tree(tmp_path, monkeypatch):
    source, units, metadata = source_fixture(tmp_path)
    pipeline = FusionPipeline(settings(tmp_path))
    monkeypatch.setattr(pipeline, "_load_source", lambda *args, **kwargs: (units, metadata))
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.generate_outline",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tree should not run")),
    )

    def fake_report(run_dir, **kwargs):
        html = run_dir / "report.html"
        png = run_dir / "report.png"
        html.write_text("<html><body>报告</body></html>", encoding="utf-8")
        png.write_bytes(b"png")
        return {
            "html": str(html),
            "png": str(png),
            "inspection": {"findings": [], "source_id_count": 1},
            "usage": {"llm_calls": 1, "tokens": 34},
        }

    monkeypatch.setattr("fusion_video_pipeline.pipeline.generate_report", fake_report)
    result = pipeline.run(str(source), outputs={"report"}, publish=False)
    assert result["outputs"] == ["report"]
    assert "tree_html" not in result
    assert result["report_html"].endswith("report.html")
    usage = json.loads((__import__("pathlib").Path(result["run_dir"]) / "usage.json").read_text())
    assert usage["report"]["tokens"] == 34


def test_reused_tree_reports_zero_current_tokens(tmp_path, monkeypatch):
    source, units, metadata = source_fixture(tmp_path)
    pipeline = FusionPipeline(settings(tmp_path))
    monkeypatch.setattr(pipeline, "_load_source", lambda *args, **kwargs: (units, metadata))
    monkeypatch.setattr(
        pipeline,
        "_reuse_outline",
        lambda *args, **kwargs: (
            outline_fixture(),
            {"type": "逻辑型", "title": "测试", "thesis": "测试", "branches": []},
            {
                "strategy": "axis-first-v2",
                "model": "tree-model",
                "total_tokens": 500,
                "attempts": 2,
                "original_generation_usage": {"total_tokens": 500, "attempts": 2},
                "reused_from": "previous-run",
            },
        ),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.generate_outline",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model should not run")),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_markdown",
        lambda outline, metadata, path: path.write_text("# 笔记", encoding="utf-8"),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_svg",
        lambda outline, path: path.write_text("<svg></svg>", encoding="utf-8"),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_png",
        lambda source, path: path.write_bytes(b"png"),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.inspect_svg_layout",
        lambda path: {"status": "checked", "findings": []},
    )
    result = pipeline.run(str(source), outputs={"tree"}, publish=False)
    assert result["usage"]["notes"]["total_tokens"] == 0
    assert result["usage"]["notes"]["attempts"] == 0
    assert result["usage"]["notes"]["reused_generation_usage"]["total_tokens"] == 500


def test_reuse_skips_zero_cost_artifact_without_generation_provenance(tmp_path, monkeypatch):
    source, units, metadata = source_fixture(tmp_path)
    pipeline = FusionPipeline(settings(tmp_path))
    fingerprint = "fingerprint"
    runs = tmp_path / "runs"
    unusable = runs / "newer-reuse"
    original = runs / "older-generation"
    monkeypatch.setattr("fusion_video_pipeline.pipeline.validate_skeleton", lambda *args: None)
    monkeypatch.setattr("fusion_video_pipeline.pipeline.validate_outline", lambda *args: None)
    for path, usage in (
        (
            original,
            {"strategy": "axis-first-v2", "total_tokens": 500, "attempts": 2},
        ),
        (
            unusable,
            {
                "strategy": "axis-first-v2",
                "total_tokens": 0,
                "attempts": 0,
                "reused_generation_usage": {"total_tokens": 0, "attempts": 0},
            },
        ),
    ):
        path.mkdir(parents=True)
        (path / "source.info.json").write_text(
            json.dumps({"source_fingerprint": fingerprint}), encoding="utf-8"
        )
        (path / "skeleton.json").write_text(
            json.dumps({"branches": []}), encoding="utf-8"
        )
        (path / "outline.json").write_text(
            json.dumps(outline_fixture()), encoding="utf-8"
        )
        (path / "usage.json").write_text(
            json.dumps({"notes": usage}), encoding="utf-8"
        )
    reused = pipeline._reuse_outline(tmp_path / "current", fingerprint, units)
    assert reused is not None
    assert reused[2]["reused_from"].endswith("older-generation")
    assert reused[2]["original_generation_usage"]["total_tokens"] == 500


def test_failed_population_can_reuse_its_valid_skeleton(tmp_path, monkeypatch):
    source, units, metadata = source_fixture(tmp_path)
    pipeline = FusionPipeline(settings(tmp_path))
    fingerprint = "fingerprint"
    run = tmp_path / "runs" / "failed-population"
    run.mkdir(parents=True)
    monkeypatch.setattr("fusion_video_pipeline.pipeline.validate_skeleton", lambda *args: None)
    (run / "source.info.json").write_text(
        json.dumps({"source_fingerprint": fingerprint}), encoding="utf-8"
    )
    (run / "skeleton.json").write_text(json.dumps({"branches": []}), encoding="utf-8")
    (run / "usage.json").write_text(
        json.dumps(
            {
                "notes": {
                    "strategy": "axis-first-v2",
                    "skeleton": {
                        "model": "tree-model",
                        "prompt_tokens": 100,
                        "completion_tokens": 200,
                        "total_tokens": 300,
                        "attempts": 1,
                    },
                    "failed_stage": "population",
                }
            }
        ),
        encoding="utf-8",
    )
    reused = pipeline._reuse_skeleton(tmp_path / "current", fingerprint, units)
    assert reused is not None
    assert reused[0] == {"branches": []}
    assert reused[1]["total_tokens"] == 0
    assert reused[1]["reused_generation_usage"]["total_tokens"] == 300


def test_tree_usage_survives_layout_failure(tmp_path, monkeypatch):
    source, units, metadata = source_fixture(tmp_path)
    pipeline = FusionPipeline(settings(tmp_path))
    monkeypatch.setattr(pipeline, "_load_source", lambda *args, **kwargs: (units, metadata))
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.generate_outline",
        lambda *args, **kwargs: (
            outline_fixture(),
            {"type": "逻辑型", "title": "测试", "thesis": "测试", "branches": []},
            {"strategy": "axis-first-v2", "total_tokens": 42, "attempts": 2},
        ),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_markdown",
        lambda outline, metadata, path: path.write_text("# 笔记", encoding="utf-8"),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_svg",
        lambda outline, path: path.write_text("<svg></svg>", encoding="utf-8"),
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.render_png", lambda source, path: path.write_bytes(b"png")
    )
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.inspect_svg_layout",
        lambda path: {"status": "checked", "findings": [{"text": "overflow"}]},
    )

    import pytest

    with pytest.raises(RuntimeError, match="文字越界"):
        pipeline.run(str(source), outputs={"tree"}, publish=False)

    run_dir = next((tmp_path / "runs").iterdir())
    usage = json.loads((run_dir / "usage.json").read_text(encoding="utf-8"))
    assert usage["notes"]["total_tokens"] == 42
