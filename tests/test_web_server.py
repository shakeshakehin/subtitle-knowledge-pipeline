import base64
import json
from pathlib import Path

import pytest

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
        {
            "title": "历史总结",
            "outputs": ["tree", "report"],
            "report_engine": "efficient",
            "source_type": "local_transcript",
            "transcript_cache": {"status": "hit", "key": "local-test"},
        },
    )
    write_json(
        run / "status.json",
        {"state": "COMPLETE", "stage": "done", "updated_at": "2026-10-01T12:03:00+08:00"},
    )
    write_json(
        run / "usage.json",
        {
            "notes": {
                "model": "tree-model",
                "prompt_tokens": 90,
                "completion_tokens": 30,
                "total_tokens": 120,
                "attempts": 2,
                "elapsed_seconds": 12,
            },
            "report": {
                "model": "report-model",
                "prompt_tokens": 60,
                "completion_tokens": 20,
                "tokens": 80,
                "llm_calls": 1,
                "elapsed_seconds": 20,
                "llm_cny_estimate": 0.0123,
            },
        },
    )
    write_json(
        run / "outline.json",
        {"title": "历史总结", "thesis": "历史主旨", "summary": [{"text": "结论一"}]},
    )
    (run / "tree.html").write_text("<html></html>", encoding="utf-8")
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "subtitle.txt").write_text("字幕", encoding="utf-8")
    (run / "transcript.md").write_text("带定位字幕", encoding="utf-8")
    (run / "canonical-transcript.jsonl").write_text("{}\n", encoding="utf-8")

    manager = JobManager(settings(tmp_path))
    try:
        rows = manager.history()
        assert len(rows) == 1
        row = rows[0]
        assert row["title"] == "历史总结"
        assert row["state"] == "complete"
        assert row["total_tokens"] == 200
        assert row["prompt_tokens"] == 150
        assert row["completion_tokens"] == 50
        assert row["calls"] == 3
        assert row["cost"] == 0.0123
        assert row["cost_currency"] == "CNY"
        assert row["elapsed_seconds"] == 32
        assert row["thesis"] == "历史主旨"
        assert row["summary"] == ["结论一"]
        assert row["report_engine"] == "efficient"
        assert row["transcript_cache"] == {"status": "hit", "key": "local-test"}
        assert set(row["artifacts"]) == {"tree_html", "report_html"}
        assert row["primary_url"].endswith("/tree.html")
        assert manager.history_artifact(run.name, "tree.html") == run / "tree.html"
        assert manager.history_artifact(run.name, "subtitle.txt") is None
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
    assert 'id="fusion-language-toggle"' in html
    assert 'id="history-filter"' in html
    assert 'class="progress-track"' in html
    assert "/api/history?limit=60" in html
    assert 'sandbox="allow-scripts allow-downloads"' in html
    assert "公开访问模式 · 必须自带 Key" in html
    assert "双击记录查看完整知识树与详细报告" in html
    assert "data-history-open=" in html
    assert "card.addEventListener('dblclick'" in html
    assert "输入 Tokens" in html
    assert "输出 Tokens" in html
    assert "总 Tokens" in html
    assert "模型请求" in html
    assert "总耗时" in html
    assert "已知成本" in html
    assert "字幕 TXT" not in html
    assert "旧版完整树" not in html
    assert "Canonical JSONL" not in html
    assert 'id="api-key-mode"' in html
    assert 'id="shared-api-key"' in html
    assert 'id="new-api-key-field"' in html
    assert 'type="submit" disabled>正在读取配置' in html
    assert "configReady=false" in html
    assert "API：${esc(job.api_key_source" in html
    assert "note_api_key:sharedApiKey" in html
    assert "report_api_key:sharedApiKey" in html
    assert 'id="note-api-key"' not in html
    assert 'id="report-api-key"' not in html
    assert 'name="report-engine"' in html
    assert 'value="editorial" checked' in html
    assert "编辑型总结（推荐）" in html
    assert "report_engine:reportEngine" in html
    assert "原始 Agent 精读（回退）" in html


def test_job_rejects_unknown_report_engine(tmp_path):
    manager = JobManager(settings(tmp_path))
    try:
        with pytest.raises(ValueError, match="报告引擎无效"):
            manager.submit(
                {
                    "source_type": "subtitle",
                    "subtitle_text": "测试字幕",
                    "outputs": {"report": True},
                    "report_engine": "unknown",
                }
            )
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_public_mode_can_disable_login_without_disabling_server_locks(tmp_path):
    access = WebAccess.from_environment(enabled=False)
    manager = JobManager(settings(tmp_path), public_mode=True)
    try:
        assert access.enabled is False
        assert basic_auth_matches(None, access)
        requested = manager.request_settings(
            {
                "obsidian_root": tmp_path / "attacker-selected",
                "models": {
                    "note_base_url": "https://attacker.invalid",
                    "report_provider": "attacker-provider",
                },
            }
        )
        assert requested.note_base_url == "https://example.invalid"
        assert requested.report_provider == "provider"
        assert requested.obsidian_root == tmp_path / "vault"
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


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


def test_new_official_deepseek_key_can_replace_configured_key_and_be_saved(tmp_path):
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
    manager = JobManager(configured)
    try:
        requested, credential_to_save = manager._request_settings(
            {
                "models": {
                    "api_key_mode": "new",
                    "note_api_key": "fresh-shared-key",
                    "report_api_key": "fresh-shared-key",
                    "save_api_key": True,
                }
            },
            allow_saved_api_key=True,
        )
        assert requested.note_api_key == "fresh-shared-key"
        assert requested.report_api_key == "fresh-shared-key"
        assert credential_to_save == "fresh-shared-key"
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_local_legacy_request_prefers_saved_key_over_stale_config(tmp_path):
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
    manager = JobManager(configured)

    class SavedCredential:
        @staticmethod
        def available():
            return True

        @staticmethod
        def load():
            return "known-working-saved-key"

    manager.credential_store = SavedCredential()
    try:
        requested = manager.request_settings(
            {
                "models": {
                    "note_api_key": "obsolete-request-key",
                    "report_api_key": "obsolete-request-key",
                }
            }
        )
        assert requested.note_api_key == "known-working-saved-key"
        assert requested.report_api_key == "known-working-saved-key"
        assert (
            manager._api_key_source({}, allow_saved_api_key=True)
            == "本机已保存 API（兼容模式）"
        )
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_saved_api_key_is_not_available_to_public_unauthenticated_request(tmp_path):
    manager = JobManager(settings(tmp_path), public_mode=True)
    try:
        with pytest.raises(ValueError, match="不允许使用本机保存"):
            manager.request_settings(
                {"models": {"api_key_mode": "saved"}},
                allow_saved_api_key=False,
            )
    finally:
        manager.executor.shutdown(wait=False, cancel_futures=True)


def test_public_new_key_still_works_when_save_checkbox_was_stale(tmp_path):
    configured = Settings(
        project_root=tmp_path,
        note_base_url="https://api.deepseek.com",
        note_model="deepseek-flash",
        note_api_key="",
        report_provider="deepseek",
        report_model="deepseek-flash",
        report_api_key="",
        obsidian_root=tmp_path / "vault",
        credential_path=tmp_path / "credential.json",
    )
    manager = JobManager(configured, public_mode=True)
    try:
        requested, credential_to_save = manager._request_settings(
            {
                "models": {
                    "api_key_mode": "new",
                    "note_api_key": "request-only-key",
                    "report_api_key": "request-only-key",
                    "save_api_key": True,
                }
            },
            allow_saved_api_key=False,
        )
        assert requested.note_api_key == "request-only-key"
        assert requested.report_api_key == "request-only-key"
        assert credential_to_save is None
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
