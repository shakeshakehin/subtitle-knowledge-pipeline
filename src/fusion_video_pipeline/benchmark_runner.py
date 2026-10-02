from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .benchmark_common import (
    BENCHMARK_ROOT,
    PROJECT_ROOT,
    load_manifest,
    read_json,
    rebuild_lock,
    resolve_benchmark_path,
    save_manifest,
    utc_stamp,
    write_json,
)
from .canonical import source_fingerprint, units_from_plain_text
from .config import Settings
from .notes import generate_outline, outline_metrics

_legacy_script_value = os.getenv("V7_GENERATOR_PATH", "").strip()
LEGACY_SCRIPT = Path(_legacy_script_value).expanduser() if _legacy_script_value else None


def _selected(rows: list[dict], only: set[str] | None, limit: int | None) -> list[dict]:
    selected = [row for row in rows if not only or row["id"] in only]
    return selected[:limit] if limit else selected


def _run_baseline(row: dict, root: Path, settings: Settings, *, force: bool) -> dict:
    output = resolve_benchmark_path(row["baseline_output"], root)
    run_meta = output.with_suffix(".run.json")
    if output.is_file() and not force:
        return {"state": "FROZEN", "output": str(output)}
    if LEGACY_SCRIPT is None or not LEGACY_SCRIPT.is_file():
        raise FileNotFoundError(
            "旧版生成器不存在；请把 V7_GENERATOR_PATH 设为 notes_structurer.py 的路径"
        )
    if not settings.note_api_key:
        raise RuntimeError("未找到 NOTE_API_KEY，无法生成缺少的 V7 基线")
    subtitle = resolve_benchmark_path(row["subtitle_path"], root)
    work = root / "work" / "baseline" / row["id"]
    work.mkdir(parents=True, exist_ok=True)
    for path in work.glob(f"{row['id']}.*"):
        if path.is_file():
            path.unlink()
    # 在独立目录运行原始 V7 脚本，避免其目录中的 harness.env 覆盖
    # 本次命令行参数。Prompt 与结构逻辑不变，只指向当前 DeepSeek 端点。
    generator_dir = root / "work" / "baseline" / "_generator"
    generator_dir.mkdir(parents=True, exist_ok=True)
    generator = generator_dir / "notes_structurer.py"
    generator_source = LEGACY_SCRIPT.read_text(encoding="utf-8")
    api_argument = 'ap.add_argument("--api-key", default=None)'
    if api_argument not in generator_source:
        raise RuntimeError("V7 脚本的 --api-key 参数定义已变更，停止生成以免泄漏密钥")
    generator_source = generator_source.replace(
        api_argument,
        'ap.add_argument("--api-key", default=os.getenv("NOTE_API_KEY"))',
        1,
    )
    generator.write_text(generator_source, encoding="utf-8")
    started = time.perf_counter()
    command = [
        sys.executable,
        str(generator),
        str(subtitle),
        "-o",
        str(work),
        "--base-url",
        settings.note_base_url,
        "--model",
        settings.note_model,
    ]
    completed = subprocess.run(
        command,
        cwd=generator_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    elapsed = round(time.perf_counter() - started, 3)
    generated = work / f"{subtitle.stem}.outline.json"
    metadata = {
        "state": "COMPLETE" if completed.returncode == 0 and generated.is_file() else "FAILED",
        "sample_id": row["id"],
        "version": "baseline-v7-prompt",
        "elapsed_seconds": elapsed,
        "return_code": completed.returncode,
        "generated_at": utc_stamp(),
        "generator": str(LEGACY_SCRIPT),
        "generator_sha256": hashlib.sha256(LEGACY_SCRIPT.read_bytes()).hexdigest(),
        "endpoint": settings.note_base_url,
        "model": settings.note_model,
        "stdout_tail": (completed.stdout or "")[-1000:],
        "stderr_tail": (completed.stderr or "")[-1000:],
    }
    if metadata["state"] == "COMPLETE":
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(generated, output)
        usage = work / f"{subtitle.stem}.usage.json"
        if usage.is_file():
            shutil.copy2(usage, output.with_suffix(".usage.json"))
    write_json(run_meta, metadata)
    if metadata["state"] != "COMPLETE":
        raise RuntimeError(
            f"{row['id']} 旧版生成失败：{metadata['stderr_tail'] or metadata['stdout_tail']}"
        )
    return metadata


def _run_fusion(row: dict, root: Path, settings: Settings, *, force: bool) -> dict:
    output = resolve_benchmark_path(row["fusion_output"], root)
    skeleton_path = resolve_benchmark_path(row["skeleton_output"], root)
    usage_path = output.with_suffix(".usage.json")
    run_meta = output.with_suffix(".run.json")
    if output.is_file() and skeleton_path.is_file() and not force:
        return {"state": "FROZEN", "output": str(output)}
    subtitle = resolve_benchmark_path(row["subtitle_path"], root)
    raw = subtitle.read_text(encoding="utf-8-sig")
    units = units_from_plain_text(raw, str(subtitle.resolve()))
    started = time.perf_counter()
    outline, skeleton, usage = generate_outline(
        units,
        base_url=settings.note_base_url,
        model=settings.note_model,
        api_key=settings.note_api_key,
        skeleton_path=skeleton_path,
    )
    elapsed = round(time.perf_counter() - started, 3)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, outline)
    write_json(skeleton_path, skeleton)
    write_json(usage_path, usage)
    metadata = {
        "state": "COMPLETE",
        "sample_id": row["id"],
        "version": "fusion-axis-first-v2",
        "elapsed_seconds": elapsed,
        "generated_at": utc_stamp(),
        "model": settings.note_model,
        "source_fingerprint": source_fingerprint(units),
        "canonical_unit_count": len(units),
        "metrics": outline_metrics(outline),
        "usage": usage,
    }
    write_json(run_meta, metadata)
    return metadata


def refresh_blind(root: Path = BENCHMARK_ROOT) -> dict:
    rows = load_manifest(root)
    key: dict[str, dict[str, str]] = {}
    blind_root = root / "outputs" / "blind"
    blind_root.mkdir(parents=True, exist_ok=True)
    for row in rows:
        baseline = resolve_benchmark_path(row["baseline_output"], root)
        fusion = resolve_benchmark_path(row["fusion_output"], root)
        if not (baseline.is_file() and fusion.is_file()):
            continue
        sample_root = blind_root / row["id"]
        sample_root.mkdir(parents=True, exist_ok=True)
        fusion_is_a = int(hashlib.sha256(f"blind-v1:{row['id']}".encode()).hexdigest(), 16) % 2 == 0
        mapping = (
            {"A": "fusion", "B": "baseline"} if fusion_is_a else {"A": "baseline", "B": "fusion"}
        )
        for label, version in mapping.items():
            source = fusion if version == "fusion" else baseline
            shutil.copy2(source, sample_root / f"{label}.json")
        key[row["id"]] = mapping
    payload = {"format_version": 1, "generated_at": utc_stamp(), "samples": key}
    write_json(root / "admin" / "blind-key.json", payload)
    return payload


def run_benchmark(
    version: str,
    *,
    root: Path = BENCHMARK_ROOT,
    only: set[str] | None = None,
    limit: int | None = None,
    force: bool = False,
) -> dict:
    rows = load_manifest(root)
    if not rows:
        raise RuntimeError("benchmark 尚未 setup")
    selected = _selected(rows, only, limit)
    settings = Settings.load(PROJECT_ROOT)
    results: dict[str, dict] = {}
    versions = ("baseline", "fusion") if version == "all" else (version,)
    for selected_version in versions:
        for index, row in enumerate(selected, 1):
            print(
                f"[{selected_version} {index}/{len(selected)}] {row['id']} {row['title']}",
                flush=True,
            )
            try:
                if selected_version == "baseline":
                    result = _run_baseline(row, root, settings, force=force)
                elif selected_version == "fusion":
                    result = _run_fusion(row, root, settings, force=force)
                else:
                    raise ValueError(f"未知版本：{selected_version}")
                results[f"{selected_version}:{row['id']}"] = result
                print(f"  -> {result['state']}", flush=True)
            except Exception as exc:
                results[f"{selected_version}:{row['id']}"] = {
                    "state": "FAILED",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                print(f"  -> FAILED {type(exc).__name__}: {exc}", flush=True)

    for row in rows:
        baseline = resolve_benchmark_path(row["baseline_output"], root)
        fusion = resolve_benchmark_path(row["fusion_output"], root)
        row["baseline_status"] = "frozen" if baseline.is_file() else "pending"
        row["fusion_status"] = "frozen" if fusion.is_file() else "pending"
    save_manifest(rows, root)
    blind = refresh_blind(root)
    lock = rebuild_lock(root)
    summary = {
        "generated_at": utc_stamp(),
        "requested_version": version,
        "selected_count": len(selected),
        "complete": sum(
            1 for value in results.values() if value["state"] in {"COMPLETE", "FROZEN"}
        ),
        "failed": sum(1 for value in results.values() if value["state"] == "FAILED"),
        "blind_sample_count": len(blind["samples"]),
        "lock_file_count": len(lock["files"]),
        "results": results,
    }
    write_json(root / "reports" / "last-run.json", summary)
    return summary


def load_run_metadata(root: Path, version: str, sample_id: str) -> dict:
    path = root / "outputs" / version / f"{sample_id}.run.json"
    return read_json(path, {})
