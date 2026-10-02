import base64
import json
from pathlib import Path

from fusion_video_pipeline.config import Settings
from fusion_video_pipeline.web_server import (
    JobManager,
    WebAccess,
    basic_auth_matches,
    session_cookie_matches,
    session_cookie_role,
)


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
    assert 'sandbox="allow-scripts allow-downloads"' in html


def test_public_mode_locks_server_controlled_endpoints_and_paths(tmp_path):
    manager = JobManager(settings(tmp_path), public_mode=True)
    try:
        requested = manager.request_settings(
            {
                "obsidian_root": tmp_path / "attacker-selected",
                "models": {
                    "note_base_url": "https://attacker.invalid",
                    "note_model": "allowed-custom-tree-model",
                    "note_api_key": "request-tree-key",
                    "report_provider": "attacker-provider",
                    "report_model": "allowed-custom-report-model",
                    "report_api_key": "request-report-key",
                },
            }
        )
        assert requested.note_base_url == "https://example.invalid"
        assert requested.report_provider == "provider"
        assert requested.obsidian_root == tmp_path / "vault"
        assert requested.note_model == "allowed-custom-tree-model"
        assert requested.report_model == "allowed-custom-report-model"
        assert requested.note_api_key == "request-tree-key"
        assert requested.report_api_key == "request-report-key"
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_official_deepseek_request_key_is_shared_between_outputs(tmp_path):
    configured = Settings(
        project_root=tmp_path,
        note_base_url="https://api.deepseek.com",
        note_model="deepseek-flash",
        note_api_key="stale-tree-key",
        report_provider="deepseek",
        report_model="deepseek-flash",
        report_api_key="stale-report-key",
        obsidian_root=tmp_path / "vault",
        credential_path=tmp_path / "credential.json",
    )
    manager = JobManager(configured, public_mode=True)
    try:
        from_tree_field = manager.request_settings(
            {"models": {"note_api_key": "fresh-official-key"}}
        )
        assert from_tree_field.note_api_key == "fresh-official-key"
        assert from_tree_field.report_api_key == "fresh-official-key"

        from_report_field = manager.request_settings(
            {"models": {"report_api_key": "another-fresh-key"}}
        )
        assert from_report_field.note_api_key == "another-fresh-key"
        assert from_report_field.report_api_key == "another-fresh-key"
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_basic_auth_uses_exact_username_and_password():
    access = WebAccess(enabled=True, username="owner", password="a-strong-test-password")
    encoded = base64.b64encode(b"owner:a-strong-test-password").decode("ascii")
    assert basic_auth_matches(f"Basic {encoded}", access)
    assert not basic_auth_matches(None, access)
    assert not basic_auth_matches("Basic not-base64", access)
    wrong = base64.b64encode(b"owner:wrong-password").decode("ascii")
    assert not basic_auth_matches(f"Basic {wrong}", access)


def test_web_access_issues_and_revokes_browser_session():
    access = WebAccess(enabled=True, username="owner", password="a-strong-test-password")
    assert access.issue_session("owner", "wrong-password", "127.0.0.1") is None
    token = access.issue_session("owner", "a-strong-test-password", "127.0.0.1")
    assert token
    cookie = f"theme=light; fusion_session={token}"
    assert session_cookie_matches(cookie, access)
    access.revoke_session(token)
    assert not session_cookie_matches(cookie, access)


def test_admin_link_issues_signed_persistent_session():
    access = WebAccess(
        enabled=True,
        username="owner",
        password="a-strong-test-password",
        admin_token="a-long-random-administrator-token-123456",
    )
    assert access.issue_admin_session("wrong-token") is None
    token = access.issue_admin_session("a-long-random-administrator-token-123456")
    assert token
    cookie = f"fusion_session={token}"
    assert session_cookie_matches(cookie, access)
    assert session_cookie_role(cookie, access) == "admin"

    token_parts = token.split(".")
    token_parts[-1] = "0" * len(token_parts[-1])
    assert access.session_role(".".join(token_parts)) is None
