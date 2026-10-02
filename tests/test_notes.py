import json

import pytest

from fusion_video_pipeline.notes import (
    _request_json,
    normalize_outline_response,
    normalize_skeleton_response,
    outline_metrics,
    render_markdown,
    validate_outline,
    validate_skeleton,
)


def sample_skeleton(source="unit-000001"):
    inventory = [
        {
            "id": f"k{index}",
            "importance": "core" if index == 1 else "supporting",
            "kind": "claim",
            "statement": f"核心内容 {index}",
            "source_units": [source],
        }
        for index in range(1, 4)
    ]
    return {
        "schema_version": 2,
        "type": "逻辑型",
        "title": "标题",
        "thesis": "论点",
        "term_normalizations": [
            {
                "raw": "a two a",
                "normalized": "Agent-to-Agent",
                "confidence": "high",
                "reason": "上下文讨论 Agent 之间的连接",
                "source_units": [source],
            }
        ],
        "content_inventory": inventory,
        "candidate_axes": [
            {
                "id": "axis-1",
                "name": "按功能",
                "rationale": "各分支回答不同功能问题",
                "branch_preview": ["章节 1", "章节 2", "章节 3"],
            },
            {
                "id": "axis-2",
                "name": "按讲述顺序",
                "rationale": "保持原有顺序但分类能力较弱",
                "branch_preview": ["前段", "后段"],
            },
        ],
        "selected_axis": {"id": "axis-1", "reason": "覆盖核心内容且分支同层"},
        "branches": [
            {
                "id": f"b{index}",
                "heading": f"章节 {index}",
                "purpose": f"回答问题 {index}",
                "inventory_ids": [f"k{index}"],
                "source_units": [source],
            }
            for index in range(1, 4)
        ],
    }


def sample_outline(source="unit-000001"):
    sections = []
    coverage = []
    for index in range(1, 4):
        parent_id = f"s{index}-p1"
        child_id = f"s{index}-p2"
        sections.append(
            {
                "id": f"b{index}",
                "level": 1,
                "heading": f"章节 {index}",
                "source_units": [source],
                "points": [
                    {
                        "id": parent_id,
                        "parent_id": None,
                        "level": 1,
                        "kind": "claim",
                        "relation_to_parent": "branch_claim",
                        "inventory_ids": [f"k{index}"],
                        "title": f"主论点 {index}",
                        "detail": "这是能够独立理解的主要观点说明。",
                        "source_units": [source],
                    },
                    {
                        "id": child_id,
                        "parent_id": parent_id,
                        "level": 2,
                        "kind": "evidence",
                        "relation_to_parent": "evidence",
                        "inventory_ids": [f"k{index}"],
                        "title": f"支撑论据 {index}",
                        "detail": "这是解释父节点为什么成立的支撑信息。",
                        "source_units": [source],
                    },
                ],
            }
        )
        coverage.append(
            {
                "inventory_id": f"k{index}",
                "status": "included",
                "node_ids": [parent_id, child_id],
                "reason": "作为分支论点和支撑展开",
            }
        )
    return {
        "schema_version": 2,
        "type": "逻辑型",
        "title": "标题",
        "thesis": "论点",
        "outline": sections,
        "coverage": coverage,
        "summary": [
            {"text": "结论一", "source_units": [source]},
            {"text": "结论二", "source_units": [source]},
            {"text": "结论三", "source_units": [source]},
        ],
        "concepts": [],
        "key_chain": ["开始", "过程", "结束"],
    }


def test_source_ids_and_axis_first_schema_are_validated():
    skeleton = sample_skeleton()
    validate_skeleton(skeleton, {"unit-000001"})
    validate_outline(sample_outline(), {"unit-000001"}, skeleton)
    with pytest.raises(ValueError, match="无效 source_units"):
        validate_skeleton(sample_skeleton("unit-999999"), {"unit-000001"})


def test_parent_must_be_explicit_and_precede_child():
    data = sample_outline()
    data["outline"][0]["points"][1]["parent_id"] = "missing"
    with pytest.raises(ValueError, match="前置节点"):
        validate_outline(data, {"unit-000001"}, sample_skeleton())


def test_parent_relation_is_required_and_typed():
    data = sample_outline()
    data["outline"][0]["points"][1]["relation_to_parent"] = "related"
    with pytest.raises(ValueError, match="relation_to_parent"):
        validate_outline(data, {"unit-000001"}, sample_skeleton())


def test_population_must_keep_selected_skeleton_branches_locked():
    data = sample_outline()
    data["outline"][0]["heading"] = "擅自改名"
    with pytest.raises(ValueError, match="保持 skeleton"):
        validate_outline(data, {"unit-000001"}, sample_skeleton())


def test_selected_axis_must_reference_a_candidate():
    skeleton = sample_skeleton()
    skeleton["selected_axis"]["id"] = "missing"
    with pytest.raises(ValueError, match="candidate_axes"):
        validate_skeleton(skeleton, {"unit-000001"})


def test_core_inventory_must_enter_exactly_one_branch():
    skeleton = sample_skeleton()
    skeleton["branches"] = skeleton["branches"][1:]
    with pytest.raises(ValueError, match="core content_inventory"):
        validate_skeleton(skeleton, {"unit-000001"})
    skeleton = sample_skeleton()
    skeleton["branches"][1]["inventory_ids"].append("k1")
    with pytest.raises(ValueError, match="重复认领"):
        validate_skeleton(skeleton, {"unit-000001"})


def test_coverage_cannot_omit_core_content():
    data = sample_outline()
    data["coverage"][0] = {
        "inventory_id": "k1",
        "status": "omitted",
        "node_ids": [],
        "reason": "尝试省略核心内容",
    }
    with pytest.raises(ValueError, match="core content_inventory"):
        validate_outline(data, {"unit-000001"}, sample_skeleton())


def test_coverage_node_ids_must_match_node_inventory_ids():
    data = sample_outline()
    data["coverage"][0]["node_ids"] = ["s1-p1"]
    with pytest.raises(ValueError, match="不一致"):
        validate_outline(data, {"unit-000001"}, sample_skeleton())


def test_tree_size_and_depth_follow_content_instead_of_fixed_limits():
    skeleton = sample_skeleton()
    skeleton["branches"] = [
        {
            "id": "b1",
            "heading": "唯一主线",
            "purpose": "内容本身只有一个连续推导",
            "inventory_ids": ["k1", "k2", "k3"],
            "source_units": ["unit-000001"],
        }
    ]
    data = sample_outline()
    points = []
    relations = ["branch_claim", "explanation", "cause", "limitation"]
    for level in range(1, 5):
        points.append(
            {
                "id": f"p{level}",
                "parent_id": None if level == 1 else f"p{level - 1}",
                "level": level,
                "kind": "claim" if level == 1 else "explanation",
                "relation_to_parent": relations[level - 1],
                "inventory_ids": ["k1"],
                "title": f"第 {level} 层",
                "detail": "该层具有独立且明确的语义关系。",
                "source_units": ["unit-000001"],
            }
        )
    data["outline"] = [
        {
            "id": "b1",
            "level": 1,
            "heading": "唯一主线",
            "source_units": ["unit-000001"],
            "points": points,
        }
    ]
    data["coverage"] = [
        {
            "inventory_id": "k1",
            "status": "included",
            "node_ids": [f"p{level}" for level in range(1, 5)],
            "reason": "形成连续推导",
        },
        {"inventory_id": "k2", "status": "omitted", "node_ids": [], "reason": "重复支撑"},
        {"inventory_id": "k3", "status": "omitted", "node_ids": [], "reason": "非必要案例"},
    ]
    data["summary"] = [{"text": "一个结论", "source_units": ["unit-000001"]}]
    data["key_chain"] = ["一条主线"]
    validate_outline(data, {"unit-000001"}, skeleton)
    assert outline_metrics(data)["maximum_point_level"] == 4


def test_outline_metrics_expose_real_depth():
    metrics = outline_metrics(sample_outline())
    assert metrics["level_counts"] == {1: 3, 2: 3}
    assert metrics["nested_sections"] == 3
    assert metrics["visual_depth"] == 4


def test_request_usage_sums_all_validation_attempts(monkeypatch):
    payloads = iter(
        [
            {
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"ok": True})},
                    }
                ],
                "usage": {"prompt_tokens": 14, "completion_tokens": 3, "total_tokens": 17},
            },
        ]
    )

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    requests = []

    def fake_post(*args, **kwargs):
        requests.append(kwargs["json"])
        return Response(next(payloads))

    monkeypatch.setattr("fusion_video_pipeline.notes.requests.post", fake_post)

    def validator(data):
        if not data.get("ok"):
            raise ValueError("not ready")

    data, usage = _request_json(
        base_url="https://example.invalid",
        model="model",
        api_key="key",
        system_prompt="system",
        user_prompt="user",
        validator=validator,
        stage="test",
        max_tokens=100,
        timeout=5,
    )
    assert data == {"ok": True}
    assert usage["attempts"] == 2
    assert usage["prompt_tokens"] == 24
    assert usage["completion_tokens"] == 5
    assert usage["total_tokens"] == 29
    assert len(usage["attempt_usages"]) == 2
    assert "thinking" not in requests[0]
    assert requests[1]["thinking"] == {"type": "disabled"}


def test_exact_duplicate_term_normalizations_are_merged_without_an_llm_retry():
    data = sample_skeleton()
    duplicate = {
        **data["term_normalizations"][0],
        "source_units": ["unit-000002"],
    }
    data["term_normalizations"].append(duplicate)
    normalized = normalize_skeleton_response(data)
    assert len(normalized["term_normalizations"]) == 1
    assert normalized["term_normalizations"][0]["source_units"] == [
        "unit-000001",
        "unit-000002",
    ]


def test_conflicting_term_normalizations_still_fail_validation():
    data = sample_skeleton()
    data["term_normalizations"].append(
        {
            **data["term_normalizations"][0],
            "normalized": "Another Meaning",
        }
    )
    normalized = normalize_skeleton_response(data)
    with pytest.raises(ValueError, match="raw 重复"):
        validate_skeleton(normalized, {"unit-000001"})


def test_markdown_keeps_source_ids_internal(tmp_path):
    data = sample_outline()
    output = render_markdown(
        data,
        {"source_path": "subtitle.txt", "bvid": ""},
        tmp_path / "notes.md",
    )
    markdown = output.read_text(encoding="utf-8")
    assert data["outline"][0]["points"][0]["title"] in markdown
    assert "unit-000001" not in markdown


def test_outline_schema_synonyms_are_normalized_before_validation():
    data = sample_outline()
    point = data["outline"][0]["points"][1]
    point["kind"] = "concept"
    point["relation_to_parent"] = "definition"
    normalize_outline_response(data)
    assert point["kind"] == "explanation"
    assert point["relation_to_parent"] == "definition"
    validate_outline(data, {"unit-000001"}, sample_skeleton())


def test_unknown_outline_semantics_still_fail_after_normalization():
    data = sample_outline()
    point = data["outline"][0]["points"][1]
    point["kind"] = "mystery"
    point["relation_to_parent"] = "mystery"
    normalize_outline_response(data)
    with pytest.raises(ValueError, match="kind 无效"):
        validate_outline(data, {"unit-000001"}, sample_skeleton())
