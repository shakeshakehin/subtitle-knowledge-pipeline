from fusion_video_pipeline.canonical import units_from_plain_text, write_canonical


def test_plain_text_becomes_traceable_units(tmp_path):
    units = units_from_plain_text("第一行\n第二行\n\n第三行", "sample.txt", max_lines=2)
    assert [unit.unit_id for unit in units] == ["unit-000001", "unit-000002"]
    assert units[0].line_start == 1
    assert units[1].line_start == 4
    paths = write_canonical(units, tmp_path)
    assert "[unit-000001 | lines 1–2]" in paths["transcript"].read_text(encoding="utf-8")
