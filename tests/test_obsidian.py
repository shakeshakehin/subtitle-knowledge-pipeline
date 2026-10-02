import json

from fusion_video_pipeline.obsidian import _source_id, publish_to_obsidian


def test_publish_integrates_both_outputs(tmp_path):
    run = tmp_path / "run-123"
    run.mkdir()
    for name in ("mindmap.svg", "mindmap.png", "report.html", "report.png"):
        (run / name).write_text(name, encoding="utf-8")
    (run / "notes.md").write_text("# 笔记\n", encoding="utf-8")
    (run / "source.info.json").write_text(json.dumps({"title": "测试"}), encoding="utf-8")
    result = publish_to_obsidian(run, {"title": "测试", "bvid": "BV123"}, tmp_path / "vault")
    note = (tmp_path / "vault" / "_首頁.md").read_text(encoding="utf-8")
    assert "测试" in note
    assert "完整閱讀報告" in __import__("pathlib").Path(result["note"]).read_text(encoding="utf-8")


def test_source_id_falls_back_to_bvid_in_url(tmp_path):
    metadata = {"url": "https://www.bilibili.com/video/BV1qVbs6rEqs"}
    assert _source_id(metadata, tmp_path / "unrelated-run-name") == "BV1qVbs6rEqs"


def test_publish_supports_report_only(tmp_path):
    run = tmp_path / "run-report"
    run.mkdir()
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    result = publish_to_obsidian(run, {"title": "只有报告"}, tmp_path / "vault")
    assert "report_html" in result
    assert "mindmap_png" not in result
    assert "打开 HTML 阅读报告" in __import__("pathlib").Path(result["note"]).read_text(
        encoding="utf-8"
    )


def test_publish_rewrites_linked_tree_report_filename(tmp_path):
    run = tmp_path / "run-linked"
    run.mkdir()
    (run / "tree.html").write_text('<iframe src="report.html"></iframe>', encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    result = publish_to_obsidian(run, {"title": "联动"}, tmp_path / "vault")
    tree = __import__("pathlib").Path(result["tree_html"]).read_text(encoding="utf-8")
    assert 'src="联动_閱讀報告.html"' in tree
