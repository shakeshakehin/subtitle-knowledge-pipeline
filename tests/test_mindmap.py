from fusion_video_pipeline.mindmap import (
    PAD_X,
    TEXT_WIDTH_SAFETY,
    _layout,
    _style,
    _text_width,
    render_svg,
)


def tree_data():
    return {
        "type": "逻辑型",
        "title": "根主题包含中文与 Agent Reflection",
        "thesis": "核心论点也必须在卡片内部安全换行",
        "outline": [{
            "heading": "章节",
            "points": [
                {
                    "id": "p1",
                    "parent_id": None,
                    "level": 1,
                    "title": "主论点",
                    "detail": "完整内容与导图短标题分开保存，并在卡片内自动换行。",
                },
                {"id": "p2", "parent_id": "p1", "level": 2, "title": "支撑论据"},
                {"id": "p3", "parent_id": "p2", "level": 3, "text": "具体细节"},
                {"id": "p4", "parent_id": None, "level": 1, "text": "第二主论点"},
            ],
        }],
        "summary": [{"text": "总结结论"}],
    }


def test_mindmap_renders_explicit_parent_child_depth_and_summary(tmp_path):
    output = render_svg(tree_data(), tmp_path / "tree.svg")
    svg = output.read_text(encoding="utf-8")
    assert "主论点" in svg
    assert "支撑论据" in svg
    assert "具体细节" in svg
    assert "总结结论" in svg
    assert "深度 5" in svg


def test_every_wrapped_line_fits_its_measured_card():
    _, nodes = _layout(tree_data())
    for node in nodes:
        font_size, _, _, _ = _style(node)
        available = node.width - 2 * PAD_X
        assert all(_text_width(line, font_size) <= available for line in node.lines)
        assert all(_text_width(line, 15) <= available for line in node.subtitle_lines)


def test_measured_cards_keep_browser_font_fallback_safety_space():
    _, nodes = _layout(tree_data())
    for node in nodes:
        font_size, _, min_width, max_width = _style(node)
        widest = max(
            [_text_width(line, font_size) for line in node.lines]
            + [_text_width(line, 15) for line in node.subtitle_lines]
        )
        if min_width < widest + 2 * PAD_X + TEXT_WIDTH_SAFETY < max_width:
            assert node.width - 2 * PAD_X - widest >= TEXT_WIDTH_SAFETY
