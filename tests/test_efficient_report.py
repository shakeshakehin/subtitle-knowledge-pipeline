import json

import pytest

from fusion_video_pipeline import efficient_report
from fusion_video_pipeline.canonical import SourceUnit


def units_fixture():
    return [
        SourceUnit("unit-000001", "先解释主题。", "test", start_ms=0, end_ms=1200),
        SourceUnit("unit-000002", "再给出条件与方法。", "test", start_ms=1200, end_ms=3200),
        SourceUnit("unit-000003", "最后说明限制。", "test", start_ms=3200, end_ms=5100),
    ]


def plan_fixture():
    return {
        "schema_version": 1,
        "title": "结构化报告",
        "thesis": "主题、方法与限制共同构成完整结论。",
        "terminology": [],
        "inventory": [
            {
                "id": "k1",
                "kind": "concept",
                "importance": "core",
                "statement": "主题需要先被定义",
                "detail_scope": "解释主题与方法",
                "source_units": ["unit-000001", "unit-000002"],
            },
            {
                "id": "k2",
                "kind": "caveat",
                "importance": "supporting",
                "statement": "结论存在限制",
                "detail_scope": "保留限制条件",
                "source_units": ["unit-000003"],
            },
        ],
        "block_coverage": [
            {
                "block_id": "block-001",
                "decision": "include",
                "inventory_ids": ["k1", "k2"],
                "reason": "字幕块包含主题、方法与限制",
            }
        ],
        "sections": [
            {
                "id": "s1",
                "heading": "从主题到限制",
                "purpose": "解释完整推理链",
                "inventory_ids": ["k1", "k2"],
                "source_units": ["unit-000001", "unit-000003"],
            }
        ],
        "summary": [
            {
                "text": "理解主题时必须同时保留方法和限制。",
                "source_units": ["unit-000001", "unit-000003"],
            }
        ],
    }


def document_fixture():
    return {
        "schema_version": 1,
        "title": "结构化报告",
        "subtitle": "从主题到适用边界",
        "lead": {
            "text": "这份报告解释主题、方法与限制之间的关系。",
            "source_units": ["unit-000001", "unit-000003"],
        },
        "quick_summary": [
            {
                "text": "主题必须和适用条件一起理解。",
                "source_units": ["unit-000001", "unit-000003"],
            }
        ],
        "sections": [
            {
                "id": "s1",
                "heading": "从主题到限制",
                "takeaway": "定义、方法和限制构成完整认识。",
                "source_units": ["unit-000001", "unit-000003"],
                "subsections": [
                    {
                        "heading": "核心关系",
                        "relation": "explanation",
                        "inventory_ids": ["k1", "k2"],
                        "source_units": ["unit-000001", "unit-000003"],
                        "paragraphs": [
                            {
                                "text": "先定义主题，再说明方法，最后保留限制。",
                                "source_units": ["unit-000001", "unit-000003"],
                            }
                        ],
                        "points": [
                            {
                                "label": "限制",
                                "text": "限制不能从最终总结中删除。",
                                "source_units": ["unit-000003"],
                            }
                        ],
                    }
                ],
            }
        ],
        "conclusion": [
            {
                "text": "完整总结必须同时呈现核心判断与成立边界。",
                "source_units": ["unit-000001", "unit-000003"],
            }
        ],
    }


def test_plan_and_document_require_complete_coverage():
    units = units_fixture()
    valid_ids = {unit.unit_id for unit in units}
    plan = plan_fixture()
    efficient_report.validate_report_plan(plan, valid_ids, {"block-001"})
    efficient_report.validate_report_document(document_fixture(), plan, valid_ids)

    broken = json.loads(json.dumps(plan))
    broken["block_coverage"] = []
    with pytest.raises(ValueError, match="未完整处理字幕块"):
        efficient_report.validate_report_plan(broken, valid_ids, {"block-001"})


def test_source_blocks_keep_all_canonical_ids_once():
    blocks = efficient_report.build_source_blocks(units_fixture(), max_chars=18, max_units=2)

    assert [block["block_id"] for block in blocks] == ["block-001", "block-002"]
    assert [item for block in blocks for item in block["source_units"]] == [
        "unit-000001",
        "unit-000002",
        "unit-000003",
    ]


def test_renderer_produces_linkable_self_contained_report(tmp_path):
    output = efficient_report.render_efficient_report(
        document_fixture(),
        plan_fixture(),
        {"title": "原视频 <标题>", "uploader": "作者", "url": "https://example.com/video"},
        units_fixture(),
        tmp_path / "report.html",
    )
    content = output.read_text(encoding="utf-8")

    assert 'data-source-units="unit-000001 unit-000003"' in content
    assert "00:00–00:06" in content
    assert "原视频 &lt;标题&gt;" in content
    assert "STRUCTURED VIDEO REPORT · V2" in content
    assert "https://example.com/video" in content


def test_generate_efficient_report_uses_two_model_stages(tmp_path, monkeypatch):
    units = units_fixture()
    plan = plan_fixture()
    document = document_fixture()
    calls = []
    (tmp_path / "download").mkdir()
    (tmp_path / "download" / "source.info.json").write_text(
        json.dumps({"description": "原始视频简介"}), encoding="utf-8"
    )

    def fake_request_json(**kwargs):
        calls.append(kwargs["stage"])
        value = plan if kwargs["stage"] == "报告规划" else document
        kwargs["validator"](value)
        return value, {
            "attempts": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    monkeypatch.setattr(efficient_report, "_request_json", fake_request_json)
    monkeypatch.setattr(
        efficient_report,
        "inspect_report",
        lambda *args, **kwargs: {"status": "checked", "findings": [], "source_id_count": 3},
    )

    def fake_render_image(run_dir, **kwargs):
        path = run_dir / "report.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr(efficient_report, "render_report_image", fake_render_image)
    result = efficient_report.generate_efficient_report(
        tmp_path,
        units,
        {"title": "测试", "url": ""},
        base_url="https://example.invalid",
        model="model",
        api_key="key",
        report_mode="standard",
    )

    assert calls == ["报告规划", "报告写作"]
    assert result["engine"] == "efficient"
    assert result["usage"]["llm_calls"] == 2
    assert result["usage"]["tokens"] == 30
    assert (tmp_path / "report-efficient-plan.json").is_file()
    assert (tmp_path / "report-efficient-document.json").is_file()
    assert (tmp_path / "report.html").is_file()
    assert (tmp_path / "report.png").is_file()
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "原始视频简介" in html
    assert "{{VIDEO_DESCRIPTION}}" not in html
