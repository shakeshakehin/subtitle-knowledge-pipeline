from __future__ import annotations

from fusion_video_pipeline.benchmark_common import (
    normalized_tree,
    rebuild_lock,
    review_payload,
    save_manifest,
    verify_lock,
    write_json,
)
from fusion_video_pipeline.benchmark_score import score_benchmark
from fusion_video_pipeline.benchmark_server import _sample_payload


def _outline(prefix: str) -> dict:
    return {
        "title": f"{prefix} 标题",
        "thesis": "中心论点",
        "outline": [
            {
                "id": "branch-1",
                "heading": "主要分支",
                "points": [
                    {
                        "id": "p1",
                        "parent_id": None,
                        "level": 1,
                        "title": "主论点",
                        "detail": "主论点详情",
                    },
                    {
                        "id": "p2",
                        "parent_id": "p1",
                        "level": 2,
                        "title": "证据",
                    },
                ],
            }
        ],
        "summary": [{"text": "最终结论"}],
    }


def test_normalized_tree_supports_explicit_and_legacy_parents():
    explicit = normalized_tree(_outline("fusion"))
    assert explicit["nodes"][1]["parent_id"] == "p1"
    assert explicit["edges"][1]["parent"] == "主论点：主论点详情"

    legacy = _outline("v7")
    del legacy["outline"][0]["points"][1]["parent_id"]
    normalized = normalized_tree(legacy)
    assert normalized["nodes"][1]["parent_id"] == "p1"


def test_review_payload_sampling_is_deterministic():
    first = review_payload(_outline("x"), "S001", "A")
    second = review_payload(_outline("x"), "S001", "A")
    assert first == second
    assert [item["kind"] for item in first["claims"][:2]] == ["thesis", "summary"]


def test_scoring_and_lock_work_without_llm_judge(tmp_path):
    root = tmp_path / "benchmark"
    subtitle = root / "subtitles" / "S001.txt"
    subtitle.parent.mkdir(parents=True)
    subtitle.write_text("第一行\n第二行\n", encoding="utf-8")
    row = {
        "id": "S001",
        "source_id": "local",
        "title": "测试",
        "category": "tutorial",
        "length_bucket": "short",
        "char_count": 8,
        "subtitle_path": "subtitles/S001.txt",
        "baseline_output": "outputs/baseline/S001.json",
        "fusion_output": "outputs/fusion/S001.json",
        "skeleton_output": "outputs/fusion/S001.skeleton.json",
        "gold_path": "gold/S001.json",
        "baseline_status": "frozen",
        "fusion_status": "frozen",
    }
    save_manifest([row], root)
    write_json(root / "outputs/baseline/S001.json", _outline("baseline"))
    write_json(root / "outputs/fusion/S001.json", _outline("fusion"))
    write_json(root / "outputs/fusion/S001.skeleton.json", {"branches": []})
    write_json(
        root / "gold/S001.json",
        {
            "sample_id": "S001",
            "status": "complete",
            "core_points": [{"id": f"K{i}", "text": f"知识点 {i}"} for i in range(1, 9)],
            "conclusions": [{"id": f"C{i}", "text": f"结论 {i}"} for i in range(1, 4)],
        },
    )
    write_json(
        root / "admin/blind-key.json",
        {"samples": {"S001": {"A": "fusion", "B": "baseline"}}},
    )
    for label, version in {"A": "fusion", "B": "baseline"}.items():
        write_json(root / f"outputs/blind/S001/{label}.json", _outline(version))
    review = {
        "sample_id": "S001",
        "reviewer": "owner",
        "status": "complete",
        "claim_support": {
            "A": {"claim": {"score": "1"}},
            "B": {"claim": {"score": "0.5"}},
        },
        "key_point_recall": {
            "A": {f"K{i}": {"score": "1"} for i in range(1, 9)},
            "B": {f"K{i}": {"score": "0.5"} for i in range(1, 9)},
        },
        "hierarchy_validity": {
            "A": {"edge": {"score": "1"}},
            "B": {"edge": {"score": "0"}},
        },
        "pairwise": {
            "coverage": "A",
            "hierarchy": "A",
            "repetition": "tie",
            "study_value": "A",
        },
    }
    write_json(root / "annotations/owner/S001.json", review)

    rebuild_lock(root)
    assert verify_lock(root) == []
    payload = _sample_payload(root, "S001", "owner")
    assert set(payload["versions"]) == {"A", "B"}
    assert "mapping" not in payload

    results = score_benchmark(root)
    assert results["scored_sample_count"] == 1
    assert results["semantic"]["fusion"]["claim_support"] == 1
    assert results["semantic"]["baseline"]["hierarchy_validity"] == 0
    assert results["pairwise"]["coverage"]["fusion_wins"] == 1
    assert results["automatic"]["fusion"]["sample_count"] == 1
    assert (root / "reports/report.md").is_file()
    assert (root / "reports/per-video-cost.csv").is_file()
    assert (
        len((root / "reports/automatic-metrics.csv").read_text(encoding="utf-8-sig").splitlines())
        == 3
    )
