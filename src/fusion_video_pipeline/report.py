from __future__ import annotations

import asyncio
import json
from pathlib import Path

from video_report_agent.inspect_report import inspect_report
from video_report_agent.pi import PiRunner
from video_report_agent.report_image import render_report_image
from video_report_agent.usage import call_costs


def generate_report(
    run_dir: Path, *, provider: str, model: str, api_key: str, report_mode: str
) -> dict:
    if not api_key:
        raise RuntimeError("REPORT_API_KEY 未配置")
    metadata_path = run_dir / "input.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["report_mode"] = report_mode
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    runner = PiRunner(provider=provider, model=model, api_key=api_key, review=False)
    report_path = asyncio.run(runner.run(run_dir))
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
    }
