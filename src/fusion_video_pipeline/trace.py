from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class RunTrace:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.path = run_dir / "run.trace.jsonl"

    def add(self, stage: str, event: str, **detail) -> None:
        record = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "stage": stage,
            "event": event,
            **detail,
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

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
