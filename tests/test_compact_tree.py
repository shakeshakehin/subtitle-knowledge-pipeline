from fusion_video_pipeline.canonical import SourceUnit
from fusion_video_pipeline.compact_tree import render_compact_tree_html


def test_compact_tree_starts_as_overview_and_keeps_details_on_demand(tmp_path):
    units = [
        SourceUnit("unit-000001", "第一段", "test", start_ms=10_000, end_ms=20_000),
        SourceUnit("unit-000002", "第二段", "test", start_ms=20_000, end_ms=30_000),
    ]
    outline = {
        "type": "逻辑型",
        "title": "简洁知识树",
        "thesis": "先看结构，再按需阅读细节。",
        "structure_design": {
            "candidate_axes": [{"id": "axis-1", "name": "按问题组织"}],
            "selected_axis": {"id": "axis-1"},
        },
        "outline": [
            {
                "heading": "主要问题",
                "source_units": ["unit-000001", "unit-000002"],
                "points": [
                    {
                        "id": "p1",
                        "parent_id": None,
                        "title": "父节点",
                        "detail": "完整解释只在选中节点后出现。",
                        "kind": "claim",
                        "relation_to_parent": "branch_claim",
                        "source_units": ["unit-000001"],
                    },
                    {
                        "id": "p2",
                        "parent_id": "p1",
                        "title": "子节点",
                        "detail": "子节点细节",
                        "kind": "example",
                        "relation_to_parent": "example",
                        "source_units": ["unit-000002"],
                    },
                ],
            }
        ],
        "summary": [{"text": "总结", "source_units": ["unit-000002"]}],
    }
    (tmp_path / "report.html").write_text(
        '<html><body><p data-source-units="unit-000001">报告</p></body></html>',
        encoding="utf-8",
    )

    output = render_compact_tree_html(
        outline,
        units,
        {"title": "测试"},
        tmp_path / "tree.html",
        report_filename="report.html",
    )
    document = output.read_text(encoding="utf-8")

    assert "OVERVIEW FIRST" in document
    assert 'data-tree-view="overview"' in document
    assert 'data-tree-view="next"' in document
    assert 'data-tree-view="all"' in document
    assert 'class="tree-item is-collapsed"' in document
    assert 'data-child-count="1"' in document
    assert 'data-detail="完整解释只在选中节点后出现。"' in document
    assert 'id="detail-panel"' in document
    assert "focusBranch" in document
    assert 'src="report.html"' in document
    assert "不跳转原视频" not in document
    assert "bilibili.com/video" not in document
