import json

import pytest

from fusion_video_pipeline.bili import BiliResult
from fusion_video_pipeline.canonical import SourceUnit
from fusion_video_pipeline.config import Settings
from fusion_video_pipeline.pipeline import FusionPipeline


def settings(tmp_path):
    return Settings(
        project_root=tmp_path,
        note_base_url="https://example.invalid",
        note_model="tree-model",
        note_api_key="tree-key",
        report_provider="provider",
        report_model="report-model",
        report_api_key="report-key",
        obsidian_root=tmp_path / "vault",
        credential_path=tmp_path / "credential.json",
    )


def test_failed_generation_keeps_bilibili_subtitle_and_next_run_reuses_it(
    tmp_path, monkeypatch
):
    source = "https://www.bilibili.com/video/BV1MDah6HEGb/?spm=test&p=2"
    fetch_calls = 0

    async def fake_fetch(value, credential_path):
        nonlocal fetch_calls
        fetch_calls += 1
        return BiliResult(
            units=[
                SourceUnit(
                    "unit-000001",
                    "已经抓取的字幕",
                    "https://www.bilibili.com/video/BV1MDah6HEGb?p=2",
                    start_ms=0,
                    end_ms=1000,
                )
            ],
            metadata={
                "source_type": "bilibili_cc",
                "url": "https://www.bilibili.com/video/BV1MDah6HEGb?p=2",
                "bvid": "BV1MDah6HEGb",
                "page": 2,
                "title": "缓存测试",
                "uploader": "",
            },
        )

    monkeypatch.setattr("fusion_video_pipeline.pipeline.fetch_bilibili", fake_fetch)
    monkeypatch.setattr(
        "fusion_video_pipeline.pipeline.generate_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("下游生成失败")),
    )
    pipeline = FusionPipeline(settings(tmp_path))

    with pytest.raises(RuntimeError, match="下游生成失败"):
        pipeline.run(source, outputs={"report"}, publish=False)

    cache = tmp_path / "cache" / "BV1MDah6HEGb-p2"
    assert (cache / "subtitle.txt").read_text(encoding="utf-8").strip() == "已经抓取的字幕"
    assert (cache / "canonical-transcript.jsonl").is_file()
    failed_run = next((tmp_path / "runs").iterdir())
    assert (failed_run / "subtitle.txt").is_file()
    assert json.loads((failed_run / "source.info.json").read_text(encoding="utf-8"))[
        "transcript_cache"
    ]["status"] == "stored"

    async def should_not_fetch(value, credential_path):
        raise AssertionError("命中字幕缓存后不应再次请求 B 站")

    monkeypatch.setattr("fusion_video_pipeline.pipeline.fetch_bilibili", should_not_fetch)
    units, metadata = pipeline._load_source(
        source,
        title=None,
        uploader=None,
        source_url=None,
        force=False,
    )

    assert fetch_calls == 1
    assert units[0].text == "已经抓取的字幕"
    assert metadata["transcript_cache"] == {
        "status": "hit",
        "key": "BV1MDah6HEGb-p2",
    }
