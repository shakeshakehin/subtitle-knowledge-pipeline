from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class RunTrace:
    def __init__(self, run_dir: Path, telemetry: Any | None = None):
        self.run_dir = run_dir
        self.path = run_dir / "run.trace.jsonl"
        self.telemetry = telemetry

    def add(self, stage: str, event: str, **detail) -> None:
        record = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "stage": stage,
            "event": event,
            **detail,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        if self.telemetry is not None:
            if event == "start":
                self.telemetry.start_stage(stage, detail)
            elif event == "complete":
                self.telemetry.complete_stage(stage, detail)
            else:
                self.telemetry.stage_event(stage, event, detail)

    def status(self, state: str, stage: str, **detail) -> None:
        payload = {
            "state": state,
            "stage": stage,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            **detail,
        }
        (self.run_dir / "status.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if self.telemetry is not None:
            if state == "COMPLETE":
                self.telemetry.complete_run(detail)
            elif state == "FAILED":
                self.telemetry.fail_run(
                    error_type=str(detail.get("error_type") or "PipelineError"),
                    error=str(detail.get("error") or "Pipeline failed"),
                )
