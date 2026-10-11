from fusion_video_pipeline.localization import (
    SIMPLIFIED,
    TRADITIONAL,
    convert_html_document,
    convert_payload,
    convert_text,
    language_from_cookie,
)


def test_opencc_converts_words_in_both_directions():
    simplified = "开发视频总结，头发和后台。"
    traditional = "開發視頻總結，頭髮和後臺。"
    assert convert_text(simplified, TRADITIONAL) == traditional
    assert convert_text(traditional, SIMPLIFIED) == simplified


def test_payload_values_are_converted_without_changing_api_keys():
    converted = convert_payload(
        {"stage": "生成内容", "nested": ["历史记录"], "count": 2},
        TRADITIONAL,
    )
    assert converted == {"stage": "生成內容", "nested": ["歷史記錄"], "count": 2}


def test_language_cookie_is_allowlisted():
    assert language_from_cookie(None) == SIMPLIFIED
    assert language_from_cookie("fusion_language=zh-Hant") == TRADITIONAL
    assert language_from_cookie("fusion_language=unexpected") == SIMPLIFIED


def test_html_is_localized_and_gets_a_top_level_switch():
    document = (
        '<!doctype html><html lang="zh-CN"><body><h1>视频总结</h1>'
        '<iframe src="report.html"></iframe></body></html>'
    )
    converted = convert_html_document(document, TRADITIONAL)
    assert '<html lang="zh-Hant">' in converted
    assert "視頻總結" in converted
    assert 'id="fusion-language-toggle"' in converted
    assert "data-fusion-language-bootstrap" in converted
    assert "window.top!==window" in converted
    assert "destination.searchParams.set('lang',target)" in converted
    assert 'src="report.html?lang=zh-Hant"' in converted


def test_retired_tree_type_badges_are_removed_from_saved_html_copies():
    compact = '<html><body><span class="root-mark">逻辑型</span></body></html>'
    classic = (
        '<html><body><div class="root-card" data-source-units="u1">'
        "<strong>逻辑型</strong><small>组织轴：按问题</small></div></body></html>"
    )
    assert "逻辑型" not in convert_html_document(compact, SIMPLIFIED)
    converted = convert_html_document(classic, TRADITIONAL)
    assert "邏輯型" not in converted
    assert "組織軸：按問題" in converted


def test_saved_compact_trees_disable_detail_popup_but_keep_report_location():
    document = (
        '<html><body><button data-node-select data-can-locate="true">节点</button>'
        "<span>选择左侧节点可查看详情并定位报告</span>"
        '<section id="detail-panel" hidden>无用详情</section>'
        "<script>function locate(node){return node}</script></body></html>"
    )
    converted = convert_html_document(document, SIMPLIFIED)
    assert "data-fusion-disable-tree-detail" in converted
    assert "#detail-panel{display:none!important}" in converted
    assert "event.stopImmediatePropagation()" in converted
    assert "window.locate(node)" in converted
    assert "可查看详情" not in converted
    assert "选择左侧节点可定位到报告内容" in converted
