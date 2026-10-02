from fusion_video_pipeline.canonical import SourceUnit
from fusion_video_pipeline.interactive import render_tree_html


def test_tree_html_binds_nodes_to_report_ranges_without_video_seek(tmp_path):
    units = [
        SourceUnit("unit-000001", "第一段", "test", start_ms=10_000, end_ms=20_000),
        SourceUnit("unit-000002", "第二段", "test", start_ms=20_000, end_ms=35_000),
    ]
    outline = {
        "type": "逻辑型",
        "title": "测试知识树",
        "thesis": "先建立父子关系。",
        "outline": [
            {
                "heading": "主分支",
                "source_units": ["unit-000001", "unit-000002"],
                "points": [
                    {
                        "id": "p1",
                        "parent_id": None,
                        "title": "父节点",
                        "detail": "父节点说明",
                        "source_units": ["unit-000001"],
                    },
                    {
                        "id": "p2",
                        "parent_id": "p1",
                        "title": "子节点",
                        "source_units": ["unit-000002"],
                    },
                ],
            }
        ],
        "summary": [{"text": "结论", "source_units": ["unit-000002"]}],
    }
    (tmp_path / "report.html").write_text(
        '<html><body><p data-source-units="unit-000001">报告段落</p></body></html>',
        encoding="utf-8",
    )
    output = render_tree_html(
        outline,
        units,
        {"title": "测试"},
        tmp_path / "tree.html",
        report_filename="report.html",
    )
    document = output.read_text(encoding="utf-8")
    assert 'src="report.html"' in document
    assert 'data-source-units="unit-000001"' in document
    assert 'data-start-ms="10000"' in document
    assert "00:10–00:20" in document
    assert "best.el.scrollIntoView" in document
    assert "不跳转原视频" in document
    assert "bilibili.com/video" not in document
    report = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert 'id="fusion-report-link-handler"' in report
    assert "fusion-located" in report


def test_tree_only_html_has_no_report_frame(tmp_path):
    outline = {
        "type": "主题型",
        "title": "只有树",
        "thesis": "无需报告。",
        "outline": [],
        "summary": [],
    }
    output = render_tree_html(
        outline,
        [SourceUnit("unit-000001", "字幕", "test", line_start=1, line_end=1)],
        {"title": "只有树"},
        tmp_path / "tree.html",
    )
    document = output.read_text(encoding="utf-8")
    assert 'id="report-frame"' not in document
    assert 'data-line-start="1"' in document
