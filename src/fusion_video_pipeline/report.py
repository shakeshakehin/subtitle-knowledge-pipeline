from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

import video_report_agent
from video_report_agent.inspect_report import inspect_report
from video_report_agent.pi import PiRunner
from video_report_agent.report_image import render_report_image
from video_report_agent.usage import call_costs


def _read_pi_events(
    path: Path, offset: int, pending: bytes
) -> tuple[int, bytes, list[dict[str, Any]]]:
    """Read only complete newly appended JSONL records from Pi's event log."""
    if not path.is_file():
        return offset, pending, []
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
            offset = stream.tell()
    except OSError:
        return offset, pending, []
    if not chunk:
        return offset, pending, []
    records: list[dict[str, Any]] = []
    pieces = (pending + chunk).split(b"\n")
    pending = pieces.pop()
    for line in pieces:
        if not line.strip():
            continue
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            records.append(event)
    return offset, pending, records


async def _run_report_agent(runner: PiRunner, run_dir: Path, telemetry: Any | None) -> Path:
    """Run Pi while forwarding its durable event stream to optional telemetry."""
    if telemetry is None or not telemetry.can_trace_report_agent():
        return await runner.run(run_dir)

    events_path = run_dir / "pi.events.jsonl"
    offset = 0
    pending = b""
    task = asyncio.create_task(runner.run(run_dir))
    failure: Exception | None = None
    try:
        while not task.done():
            offset, pending, events = _read_pi_events(events_path, offset, pending)
            for event in events:
                telemetry.report_agent_event(event)
            await asyncio.sleep(0.05)

        offset, pending, events = _read_pi_events(events_path, offset, pending)
        for event in events:
            telemetry.report_agent_event(event)
        if pending.strip():
            try:
                event = json.loads(pending.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                event = None
            if isinstance(event, dict):
                telemetry.report_agent_event(event)
        return task.result()
    except Exception as exc:
        failure = exc
        if not task.done():
            task.cancel()
        raise
    finally:
        telemetry.finish_report_agent(failure)


def _ensure_report_runtime(report_agent_root: Path | None = None) -> str | None:
    """Expose the sibling project's local Pi/Node runtime to PiRunner.

    ``run-fusion.ps1`` already prepares PATH for the normal local startup path,
    but the web server can also be launched through an IDE, a service wrapper,
    or the console entry point.  The editable ``video-report-agent`` dependency
    gives us a stable way to discover its project-local runtime without copying
    or modifying that third-party project.
    """
    executable = shutil.which("pi")
    if executable is not None:
        return executable

    root = (
        report_agent_root.resolve()
        if report_agent_root is not None
        else Path(video_report_agent.__file__).resolve().parents[2]
    )
    runtime_paths: list[Path] = []
    pi_root = root / ".local" / "pi"
    if pi_root.is_dir():
        runtime_paths.append(pi_root)

    node_root = root / ".local" / "node"
    if node_root.is_dir():
        node_runtime = next(
            (
                candidate
                for candidate in sorted(node_root.iterdir())
                if candidate.is_dir() and (candidate / "node.exe").is_file()
            ),
            None,
        )
        if node_runtime is not None:
            runtime_paths.append(node_runtime)

    if runtime_paths:
        current_path = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(
            [*(str(path) for path in runtime_paths), current_path]
        )
    return shutil.which("pi")


def generate_report(
    run_dir: Path,
    *,
    provider: str,
    model: str,
    api_key: str,
    report_mode: str,
    telemetry: Any | None = None,
) -> dict:
    if not api_key:
        raise RuntimeError("REPORT_API_KEY 未配置")
    _ensure_report_runtime()
    metadata_path = run_dir / "input.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["report_mode"] = report_mode
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    runner = PiRunner(provider=provider, model=model, api_key=api_key, review=False)
    report_path = asyncio.run(_run_report_agent(runner, run_dir, telemetry))
    agent_summary = telemetry.report_agent_summary() if telemetry is not None else {}
    (run_dir / "report-agent-summary.json").write_text(
        json.dumps(agent_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    inspection = inspect_report(run_dir, label="final")
    (run_dir / "report-inspection.json").write_text(
        json.dumps(inspection, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    image_path = render_report_image(run_dir, refresh=True)
    measured = call_costs(run_dir, {}, None)
    usage = {
        key: measured[key]
        for key in (
            "llm_calls",
            "tokens",
            "llm_usd_estimate",
            "llm_cny_estimate",
            "llm_unpriced_calls",
        )
    }
    return {
        "html": str(report_path),
        "png": str(image_path),
        "inspection": inspection,
        "usage": usage,
        "agent_summary": agent_summary,
    }
