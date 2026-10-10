import json

import pytest

from fusion_video_pipeline import editorial_report
from fusion_video_pipeline.canonical import SourceUnit


def units_fixture():
    return [
        SourceUnit("unit-000001", "先定义主题。", "test", start_ms=0, end_ms=1200),
        SourceUnit("unit-000002", "方法分成两步。", "test", start_ms=1200, end_ms=3200),
        SourceUnit("unit-000003", "结论存在边界。", "test", start_ms=3200, end_ms=5100),
    ]


def plan_fixture():
    return {
        "schema_version": 1,
        "title": "编辑型报告",
        "subtitle": "从方法到边界",
        "lead_angle": "先解释主题，再展示方法与边界。",
        "thesis": "完整理解必须同时保留方法与边界。",
        "terminology": [],
        "content_ledger": [
            {
                "id": "k1",
                "importance": "core",
                "statement": "主题需要定义",
                "detail_scope": "保留主题定义",
                "source_units": ["unit-000001"],
            },
            {
                "id": "k2",
                "importance": "supporting",
                "statement": "方法包含两个步骤",
                "detail_scope": "展开步骤",
                "source_units": ["unit-000002"],
            },
            {
                "id": "k3",
                "importance": "core",
                "statement": "结论存在边界",
                "detail_scope": "保留适用限制",
                "source_units": ["unit-000003"],
            },
        ],
        "block_coverage": [
            {
                "block_id": "block-001",
                "decision": "include",
                "content_ids": ["k1", "k2", "k3"],
                "reason": "包含主题、方法和边界",
            }
        ],
        "sections": [
            {
                "id": "s1",
                "heading": "主题与方法",
                "purpose": "建立主题并解释步骤",
                "content_ids": ["k1", "k2"],
                "source_units": ["unit-000001", "unit-000002"],
                "components": [
                    {
                        "id": "c1",
                        "type": "prose",
                        "purpose": "综合解释主题",
                        "content_ids": ["k1"],
                    },
                    {
                        "id": "c2",
                        "type": "steps",
                        "purpose": "展示真实步骤",
                        "content_ids": ["k2"],
                    },
                ],
            },
            {
                "id": "s2",
                "heading": "适用边界",
                "purpose": "说明结论限制",
                "content_ids": ["k3"],
                "source_units": ["unit-000003"],
                "components": [
                    {
                        "id": "c3",
                        "type": "callout",
                        "purpose": "突出必要限定",
                        "content_ids": ["k3"],
                    }
                ],
            },
        ],
        "closing_strategy": "short_conclusion",
    }


def document_fixture():
    return {
        "schema_version": 1,
        "title": "编辑型报告",
        "subtitle": "从方法到边界",
        "lead": {
            "text": "这份报告解释主题、方法和适用边界。",
            "source_units": ["unit-000001", "unit-000003"],
        },
        "sections": [
            {
                "id": "s1",
                "heading": "主题与方法",
                "source_units": ["unit-000001", "unit-000002"],
                "components": [
                    {
                        "id": "c1",
                        "type": "prose",
                        "heading": "先建立主题",
                        "paragraphs": [
                            {
                                "text": "理解方法前，需要先定义主题。",
                                "source_units": ["unit-000001"],
                            }
                        ],
                        "source_units": ["unit-000001"],
                    },
                    {
                        "id": "c2",
                        "type": "steps",
                        "heading": "两步方法",
                        "items": [
                            {
                                "title": "第一步",
                                "text": "先识别方法起点。",
                                "source_units": ["unit-000002"],
                            },
                            {
                                "title": "第二步",
                                "text": "再完成后续处理。",
                                "source_units": ["unit-000002"],
                            },
                        ],
                        "source_units": ["unit-000002"],
                    },
                ],
            },
            {
                "id": "s2",
                "heading": "适用边界",
                "source_units": ["unit-000003"],
                "components": [
                    {
                        "id": "c3",
                        "type": "callout",
                        "heading": "不能省略的限制",
                        "text": "结论必须和它的适用边界一起理解。",
                        "source_units": ["unit-000003"],
                    }
                ],
            },
        ],
        "closing": {
            "kind": "short_conclusion",
            "heading": "总结",
            "text": "先理解方法，再保留边界。",
            "items": [],
            "source_units": ["unit-000002", "unit-000003"],
        },
    }


def review_fixture(status="pass"):
    return {
        "schema_version": 1,
        "status": status,
        "summary": "正文忠实、完整且结构清楚。" if status == "pass" else "需要压缩正文。",
        "issues": []
        if status == "pass"
        else [
            {
                "kind": "density",
                "severity": "medium",
                "target_id": "c1",
                "problem": "解释重复。",
                "instruction": "合并重复句。",
            }
        ],
    }


def test_editorial_plan_locks_content_to_one_section_and_component():
    plan = plan_fixture()
    valid_ids = {unit.unit_id for unit in units_fixture()}
    editorial_report.validate_editorial_plan(plan, valid_ids, {"block-001"})
    editorial_report.validate_editorial_document(document_fixture(), plan, valid_ids)

    broken = json.loads(json.dumps(plan))
    broken["sections"][1]["content_ids"].append("k2")
    with pytest.raises(ValueError, match="恰好归入一个 section"):
        editorial_report.validate_editorial_plan(broken, valid_ids, {"block-001"})


def test_editorial_document_derives_component_sources_from_children():
    document = document_fixture()
    document["sections"][0]["components"][0]["source_units"] = []

    normalized = editorial_report.normalize_editorial_document(document)

    assert normalized["sections"][0]["components"][0]["source_units"] == ["unit-000001"]


def test_editorial_plan_assigns_global_mechanical_ids_without_retry():
    plan = plan_fixture()
    for section in plan["sections"]:
        section["id"] = "s1"
        for component in section["components"]:
            component["id"] = "c1"

    normalized = editorial_report.normalize_editorial_plan(plan)

    assert [section["id"] for section in normalized["sections"]] == ["s1", "s2"]
    assert [
        component["id"] for section in normalized["sections"] for component in section["components"]
    ] == ["c1", "c2", "c3"]


def test_editorial_plan_rejects_ledger_shaped_component_sprawl():
    plan = plan_fixture()
    plan["sections"][0]["components"].extend(
        [
            {
                "id": "c4",
                "type": "prose",
                "purpose": "不应逐项拆组件",
            },
            {
                "id": "c5",
                "type": "prose",
                "purpose": "不应逐项拆组件",
            },
        ]
    )

    with pytest.raises(ValueError, match="不能超过 3 个"):
        editorial_report.validate_editorial_plan(
            plan, {unit.unit_id for unit in units_fixture()}, {"block-001"}
        )


def test_writing_plan_removes_repeated_mechanical_sources():
    compact = editorial_report._editorial_plan_for_writing(plan_fixture())

    assert "block_coverage" not in compact
    assert "source_units" not in json.dumps(compact, ensure_ascii=False)


def test_editorial_renderer_uses_report_template_and_varied_components(tmp_path):
    output = editorial_report.render_editorial_report(
        document_fixture(),
        plan_fixture(),
        {"title": "原视频", "uploader": "作者", "url": "https://example.com/video"},
        units_fixture(),
        tmp_path / "report.html",
        report_mode="standard",
    )
    content = output.read_text(encoding="utf-8")

    assert 'class="report-nav"' in content
    assert 'class="steps"' in content
    assert 'class="callout"' in content
    assert "00:00–00:04" in content
    assert 'data-source-units="unit-000001 unit-000002"' in content
    assert "{{BODY}}" not in content


@pytest.mark.parametrize(("review_status", "expected_calls"), [("pass", 3), ("revise", 4)])
def test_editorial_generation_uses_three_calls_and_only_revises_when_needed(
    tmp_path, monkeypatch, review_status, expected_calls
):
    plan = plan_fixture()
    document = document_fixture()
    review = review_fixture(review_status)
    calls = []
    (tmp_path / "download").mkdir()
    (tmp_path / "download" / "source.info.json").write_text(
        json.dumps({"description": "原始视频简介"}), encoding="utf-8"
    )

    def fake_request_json(**kwargs):
        calls.append(kwargs["stage"])
        revision_patch = {
            "schema_version": 1,
            "lead": None,
            "component_replacements": [document["sections"][0]["components"][0]],
            "closing": None,
        }
        values = {
            "编辑策划": plan,
            "整篇编辑": document,
            "终稿复核": review,
            "局部修订": revision_patch,
        }
        value = values[kwargs["stage"]]
        kwargs["validator"](value)
        return value, {
            "attempts": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    monkeypatch.setattr(editorial_report, "_request_json", fake_request_json)
    monkeypatch.setattr(
        editorial_report,
        "inspect_report",
        lambda *args, **kwargs: {
            "status": "checked",
            "findings": [],
            "source_id_count": 3,
            "reference_elements": 8,
            "height": 1200,
        },
    )

    def fake_render_image(run_dir, **kwargs):
        path = run_dir / "report.png"
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr(editorial_report, "render_report_image", fake_render_image)
    result = editorial_report.generate_editorial_report(
        tmp_path,
        units_fixture(),
        {"title": "测试", "url": ""},
        base_url="https://example.invalid",
        model="model",
        api_key="key",
        report_mode="standard",
    )

    assert len(calls) == expected_calls
    assert calls[:3] == ["编辑策划", "整篇编辑", "终稿复核"]
    assert result["engine"] == "editorial"
    assert result["usage"]["llm_calls"] == expected_calls
    assert result["usage"]["tokens"] == 15 * expected_calls
    assert (tmp_path / "report-editorial-review.json").is_file()
    assert (tmp_path / "report-editorial-revised.json").is_file() is (review_status == "revise")
    assert "原始视频简介" in (tmp_path / "report.html").read_text(encoding="utf-8")
