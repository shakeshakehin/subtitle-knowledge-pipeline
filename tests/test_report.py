import asyncio
import json
import os
from pathlib import Path

from fusion_video_pipeline import report


def test_ensure_report_runtime_discovers_sibling_local_pi(tmp_path, monkeypatch):
    report_agent_root = tmp_path / "video-report-agent"
    pi_root = report_agent_root / ".local" / "pi"
    node_runtime = report_agent_root / ".local" / "node" / "node-v22"
    pi_root.mkdir(parents=True)
    node_runtime.mkdir(parents=True)
    (pi_root / "pi.CMD").write_text("@echo off\n", encoding="utf-8")
    (node_runtime / "node.exe").write_bytes(b"")
    monkeypatch.setenv("PATH", "existing-path")

    def fake_which(command: str) -> str | None:
        assert command == "pi"
        path_entries = os.environ["PATH"].split(os.pathsep)
        return str(pi_root / "pi.CMD") if str(pi_root) in path_entries else None

    monkeypatch.setattr(report.shutil, "which", fake_which)

    executable = report._ensure_report_runtime(report_agent_root)

    assert executable == str(pi_root / "pi.CMD")
    assert os.environ["PATH"].split(os.pathsep)[:2] == [str(pi_root), str(node_runtime)]


def test_ensure_report_runtime_preserves_existing_pi(monkeypatch):
    existing = Path("C:/tools/pi.CMD")
    monkeypatch.setattr(report.shutil, "which", lambda command: str(existing))
    monkeypatch.setenv("PATH", "unchanged")

    assert report._ensure_report_runtime() == str(existing)
    assert os.environ["PATH"] == "unchanged"


def test_read_pi_events_keeps_partial_jsonl_record(tmp_path):
    path = tmp_path / "pi.events.jsonl"
    first = json.dumps({"type": "request_start", "request_id": 1}).encode()
    second = json.dumps({"type": "message_end", "message": {"role": "assistant"}}).encode()
    path.write_bytes(first + b"\n" + second[:12])

    offset, pending, events = report._read_pi_events(path, 0, b"")

    assert events == [{"type": "request_start", "request_id": 1}]
    assert pending == second[:12]

    with path.open("ab") as stream:
        stream.write(second[12:] + b"\n")
    offset, pending, events = report._read_pi_events(path, offset, pending)

    assert pending == b""
    assert events == [{"type": "message_end", "message": {"role": "assistant"}}]


def test_run_report_agent_forwards_live_events(tmp_path):
    class Telemetry:
        def __init__(self):
            self.events = []
            self.finished_with = "not-finished"

        def can_trace_report_agent(self):
            return True

        def report_agent_event(self, event):
            self.events.append(event)

        def finish_report_agent(self, error):
            self.finished_with = error

    class Runner:
        async def run(self, run_dir):
            path = run_dir / "pi.events.jsonl"
            path.write_text(
                json.dumps({"type": "request_start", "request_id": 1}) + "\n",
                encoding="utf-8",
            )
            await asyncio.sleep(0.08)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {"type": "message_end", "message": {"role": "assistant"}}
                    )
                    + "\n"
                )
            return run_dir / "report.html"

    telemetry = Telemetry()
    result = asyncio.run(report._run_report_agent(Runner(), tmp_path, telemetry))

    assert result == tmp_path / "report.html"
    assert [event["type"] for event in telemetry.events] == ["request_start", "message_end"]
    assert telemetry.finished_with is None
