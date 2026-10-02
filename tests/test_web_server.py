import json
from pathlib import Path

from fusion_video_pipeline.config import Settings
from fusion_video_pipeline.web_server import JobManager


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


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_history_is_rebuilt_from_persisted_run_directories(tmp_path):
    run = tmp_path / "runs" / "20261001-120000-000-sample"
    run.mkdir(parents=True)
    write_json(
        run / "source.info.json",
        {"title": "历史总结", "outputs": ["tree", "report"], "source_type": "local_transcript"},
    )
    write_json(
        run / "status.json",
        {"state": "COMPLETE", "stage": "done", "updated_at": "2026-10-01T12:03:00+08:00"},
    )
    write_json(
        run / "usage.json",
        {
            "notes": {"model": "tree-model", "total_tokens": 120, "attempts": 2, "elapsed_seconds": 12},
            "report": {"model": "report-model", "tokens": 80, "llm_calls": 1, "elapsed_seconds": 20},
        },
    )
    write_json(
        run / "outline.json",
        {"title": "历史总结", "thesis": "历史主旨", "summary": [{"text": "结论一"}]},
    )
    (run / "tree.html").write_text("<html></html>", encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")

    manager = JobManager(settings(tmp_path))
    try:
        rows = manager.history()
        assert len(rows) == 1
        row = rows[0]
        assert row["title"] == "历史总结"
        assert row["state"] == "complete"
        assert row["total_tokens"] == 200
        assert row["calls"] == 3
        assert row["elapsed_seconds"] == 32
        assert row["thesis"] == "历史主旨"
        assert row["summary"] == ["结论一"]
        assert row["primary_url"].endswith("/tree.html")
        assert manager.history_artifact(run.name, "tree.html") == run / "tree.html"
        assert manager.history_artifact(run.name, "../source.info.json") is None
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_job_progress_keeps_a_bounded_public_event_timeline(tmp_path):
    manager = JobManager(settings(tmp_path))
    try:
        manager.jobs["job"] = {"id": "job", "stage": "等待", "events": []}
        for index in range(45):
            manager._progress(
                "job",
                {
                    "phase": "population",
                    "event": "attempt_start",
                    "message": f"步骤 {index}",
                    "progress": index,
                },
            )
        job = manager.get("job")
        assert job is not None
        assert job["progress"] == 44
        assert job["stage"] == "步骤 44"
        assert len(job["events"]) == 40
        assert job["events"][0]["message"] == "步骤 5"
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_web_ui_contains_live_progress_and_persisted_history_controls():
    html = (
        Path(__file__).parents[1]
        / "src"
        / "fusion_video_pipeline"
        / "web_ui.html"
    ).read_text(encoding="utf-8")
    assert 'id="history-list"' in html
    assert 'id="history-filter"' in html
    assert 'class="progress-track"' in html
    assert "/api/history?limit=60" in html
