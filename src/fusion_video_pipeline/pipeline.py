from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .bili import extract_bvid, extract_page_number, fetch_bilibili
from .canonical import SourceUnit, source_fingerprint, units_from_plain_text, write_canonical
from .compact_tree import render_compact_tree_html
from .config import Settings
from .editorial_report import generate_editorial_report
from .efficient_report import generate_efficient_report
from .interactive import render_tree_html
from .mindmap import inspect_svg_layout, render_overview_svg, render_png, render_svg
from .notes import (
    ModelStageError,
    generate_outline,
    outline_metrics,
    render_markdown,
    validate_outline,
    validate_skeleton,
)
from .observability import PipelineTelemetry
from .obsidian import publish_to_obsidian, safe_name
from .report import generate_report
from .trace import RunTrace


def _read_units(path: Path) -> list[SourceUnit]:
    return [
        SourceUnit(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


ProgressCallback = Callable[[dict], None]


def _notify(callback: ProgressCallback | None, **payload) -> None:
    if callback is not None:
        callback(payload)


class FusionPipeline:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _load_source(
        self,
        source: str,
        *,
        title: str | None,
        uploader: str | None,
        source_url: str | None,
        force: bool,
    ) -> tuple[list[SourceUnit], dict]:
        local = Path(source).expanduser()
        if local.is_file():
            raw = local.read_text(encoding="utf-8-sig")
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            cache = self.settings.project_root / "cache" / f"local-{digest}"
            units = units_from_plain_text(raw, str(local.resolve()))
            local_bvid = ""
            if source_url:
                try:
                    local_bvid = extract_bvid(source_url)
                except ValueError:
                    pass
            metadata = {
                "source_type": "local_transcript",
                "source_path": str(local.resolve()),
                "url": source_url or "",
                "title": title or local.stem,
                "uploader": uploader or "",
                "description": "",
                "bvid": local_bvid,
            }
            cache_ready = (cache / "canonical-transcript.jsonl").is_file() and (
                cache / "source.info.json"
            ).is_file()
            if force or not cache_ready:
                write_canonical(units, cache)
                (cache / "source.info.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            metadata["transcript_cache"] = {
                "status": "refreshed" if force else "hit" if cache_ready else "stored",
                "key": cache.name,
            }
            return units, metadata

        bvid = extract_bvid(source)
        page = extract_page_number(source)
        cache = self.settings.project_root / "cache" / f"{bvid}-p{page}"
        if not force:
            try:
                if (cache / "canonical-transcript.jsonl").is_file() and (
                    cache / "source.info.json"
                ).is_file():
                    units = _read_units(cache / "canonical-transcript.jsonl")
                    metadata = json.loads(
                        (cache / "source.info.json").read_text(encoding="utf-8")
                    )
                    if not units or not isinstance(metadata, dict):
                        raise ValueError("字幕缓存为空或元数据无效")
                    metadata["transcript_cache"] = {"status": "hit", "key": cache.name}
                    return units, metadata
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                # A partial or damaged cache is never trusted. Fetch it again and
                # replace the cache before any LLM work starts.
                pass
        fetched = asyncio.run(fetch_bilibili(source, self.settings.credential_path))
        cache.mkdir(parents=True, exist_ok=True)
        write_canonical(fetched.units, cache)
        (cache / "source.info.json").write_text(
            json.dumps(fetched.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        metadata = dict(fetched.metadata)
        metadata["transcript_cache"] = {
            "status": "refreshed" if force else "stored",
            "key": cache.name,
        }
        return fetched.units, metadata

    def _reuse_outline(
        self, current_run: Path, fingerprint: str, units: list[SourceUnit]
    ) -> tuple[dict, dict, dict] | None:
        runs_root = self.settings.project_root / "runs"
        if not runs_root.is_dir():
            return None
        for candidate in sorted(
            runs_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True
        ):
            if candidate == current_run:
                continue
            metadata_path = candidate / "source.info.json"
            skeleton_path = candidate / "skeleton.json"
            outline_path = candidate / "outline.json"
            usage_path = candidate / "usage.json"
            if not (
                metadata_path.is_file()
                and skeleton_path.is_file()
                and outline_path.is_file()
                and usage_path.is_file()
            ):
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("source_fingerprint") != fingerprint:
                    continue
                skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
                outline = json.loads(outline_path.read_text(encoding="utf-8"))
                valid_ids = {unit.unit_id for unit in units}
                validate_skeleton(skeleton, valid_ids)
                validate_outline(outline, valid_ids, skeleton)
                usage = json.loads(usage_path.read_text(encoding="utf-8")).get("notes") or {}
                if usage.get("strategy") != "axis-first-v2":
                    continue
                generation_usage = usage
                if usage.get("total_tokens") == 0 and usage.get("attempts") == 0:
                    nested = usage.get("reused_generation_usage")
                    if not isinstance(nested, dict) or not (
                        nested.get("total_tokens") or nested.get("attempts")
                    ):
                        # This reuse artifact has already lost its provenance; keep
                        # looking for an older run that records the real generation.
                        continue
                    generation_usage = nested
                return outline, skeleton, {
                    **usage,
                    "original_generation_usage": generation_usage,
                    "reused_from": str(candidate),
                }
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return None

    def _reuse_skeleton(
        self, current_run: Path, fingerprint: str, units: list[SourceUnit]
    ) -> tuple[dict, dict] | None:
        runs_root = self.settings.project_root / "runs"
        if not runs_root.is_dir():
            return None
        for candidate in sorted(
            runs_root.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True
        ):
            if candidate == current_run:
                continue
            metadata_path = candidate / "source.info.json"
            skeleton_path = candidate / "skeleton.json"
            usage_path = candidate / "usage.json"
            if not (metadata_path.is_file() and skeleton_path.is_file()):
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if metadata.get("source_fingerprint") != fingerprint:
                    continue
                skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
                validate_skeleton(skeleton, {unit.unit_id for unit in units})
                usage = (
                    json.loads(usage_path.read_text(encoding="utf-8")).get("notes") or {}
                    if usage_path.is_file()
                    else {}
                )
                if usage and usage.get("strategy") != "axis-first-v2":
                    continue
                original = usage.get("skeleton") if isinstance(usage.get("skeleton"), dict) else {}
                reused_usage = {
                    "model": original.get("model", self.settings.note_model),
                    "attempts": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "reused_from": str(candidate),
                    "reused_generation_usage": {
                        key: original.get(key)
                        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "attempts")
                        if original.get(key) is not None
                    },
                }
                return skeleton, reused_usage
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return None

    def run(
        self,
        source: str,
        *,
        title: str | None = None,
        uploader: str | None = None,
        source_url: str | None = None,
        report_mode: str = "standard",
        report_engine: str = "legacy",
        force: bool = False,
        publish: bool = True,
        outputs: set[str] | tuple[str, ...] | list[str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict:
        requested_outputs = set(outputs or ("tree", "report"))
        unknown_outputs = requested_outputs - {"tree", "report"}
        if unknown_outputs or not requested_outputs:
            raise ValueError(f"outputs 必须从 tree/report 中选择：{sorted(unknown_outputs)}")
        if report_engine not in {"legacy", "efficient", "editorial"}:
            raise ValueError("report_engine 必须是 legacy、efficient 或 editorial")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:19]
        source_hint = Path(source).stem if Path(source).is_file() else extract_bvid(source)
        run_dir = self.settings.project_root / "runs" / f"{timestamp}-{safe_name(source_hint, 30)}"
        run_dir.mkdir(parents=True, exist_ok=False)
        telemetry = PipelineTelemetry.from_settings(self.settings, run_dir)
        telemetry.start_run(
            input_data={
                "source": source,
                "source_url": source_url or "",
                "requested_outputs": sorted(requested_outputs),
                "report_mode": report_mode,
                "report_engine": report_engine,
                "publish_to_obsidian": publish,
            },
            metadata={"run_id": run_dir.name, "pipeline": "fusion-video-pipeline"},
        )
        trace = RunTrace(run_dir, telemetry=telemetry)
        notes_started_at: float | None = None
        trace.status("RUNNING", "source")
        trace.add("source", "start", source=source)
        _notify(
            progress,
            phase="source",
            event="start",
            message="读取并规范化字幕",
            progress=3,
        )
        try:
            units, metadata = self._load_source(
                source, title=title, uploader=uploader, source_url=source_url, force=force
            )
            if title:
                metadata["title"] = title
            if uploader:
                metadata["uploader"] = uploader
            metadata["source_fingerprint"] = source_fingerprint(units)
            metadata["canonical_unit_count"] = len(units)
            metadata["report_mode"] = report_mode
            metadata["report_engine"] = report_engine
            metadata["outputs"] = sorted(requested_outputs)
            write_canonical(units, run_dir)
            (run_dir / "source.info.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            download = run_dir / "download"
            download.mkdir()
            shutil.copy2(run_dir / "source.info.json", download / "source.info.json")
            (run_dir / "input.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            trace.add(
                "source",
                "complete",
                units=len(units),
                fingerprint=metadata["source_fingerprint"],
                source_type=metadata.get("source_type"),
                title=metadata.get("title"),
                transcript_cache=metadata.get("transcript_cache"),
            )
            cache_status = (metadata.get("transcript_cache") or {}).get("status")
            source_message = (
                f"已复用本地字幕缓存：{len(units)} 个内容单元"
                if cache_status == "hit"
                else f"字幕已保存到本地缓存：{len(units)} 个内容单元"
            )
            _notify(
                progress,
                phase="source",
                event="complete",
                message=source_message,
                progress=8,
                units=len(units),
                cache_status=cache_status,
            )

            outline = None
            report = None
            usage_data: dict[str, dict] = {}
            mindmap_inspection = None
            report_started_at: float | None = None
            if "tree" in requested_outputs:
                trace.status("RUNNING", "notes")
                trace.add("notes", "start", model=self.settings.note_model)
                stage_started = time.perf_counter()
                notes_started_at = stage_started
                reused = self._reuse_outline(run_dir, metadata["source_fingerprint"], units)
                if reused:
                    outline, skeleton, previous_usage = reused
                    previous_run = previous_usage["reused_from"]
                    original_generation = previous_usage["original_generation_usage"]
                    notes_usage = {
                        "strategy": "axis-first-v2",
                        "model": previous_usage.get("model", self.settings.note_model),
                        "reused_from": previous_run,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "attempts": 0,
                        "reused_generation_usage": {
                            key: original_generation.get(key)
                            for key in (
                                "prompt_tokens",
                                "completion_tokens",
                                "total_tokens",
                                "attempts",
                            )
                            if previous_usage.get(key) is not None
                        },
                    }
                    trace.add("notes", "reused", previous_run=previous_run)
                    _notify(
                        progress,
                        phase="notes",
                        event="reused",
                        message="复用相同字幕的既有知识树",
                        progress=72,
                    )
                else:
                    reusable_skeleton = self._reuse_skeleton(
                        run_dir, metadata["source_fingerprint"], units
                    )
                    existing_skeleton, existing_skeleton_usage = (
                        reusable_skeleton if reusable_skeleton else (None, None)
                    )
                    outline, skeleton, notes_usage = generate_outline(
                        units,
                        base_url=self.settings.note_base_url,
                        model=self.settings.note_model,
                        api_key=self.settings.note_api_key,
                        skeleton_path=run_dir / "skeleton.json",
                        progress=progress,
                        existing_skeleton=existing_skeleton,
                        existing_skeleton_usage=existing_skeleton_usage,
                        telemetry=telemetry,
                    )
                notes_usage = {
                    **notes_usage,
                    "elapsed_seconds": round(time.perf_counter() - stage_started, 3),
                }
                usage_data["notes"] = notes_usage
                (run_dir / "skeleton.json").write_text(
                    json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                (run_dir / "outline.json").write_text(
                    json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                # Persist model usage before rendering so a layout failure does not
                # erase the cost and latency of already completed LLM calls.
                (run_dir / "usage.json").write_text(
                    json.dumps(usage_data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                trace.add(
                    "notes",
                    "complete",
                    **outline_metrics(outline),
                    usage=notes_usage,
                    artifacts={"skeleton": "skeleton.json", "outline": "outline.json"},
                )
                trace.add(
                    "render_tree",
                    "start",
                    outline="outline.json",
                    outputs=["notes.md", "mindmap.svg", "mindmap.png"],
                )
                _notify(
                    progress,
                    phase="render_tree",
                    event="start",
                    message="排版导图并检查文字边界",
                    progress=76,
                )
                render_markdown(outline, metadata, run_dir / "notes.md")
                render_svg(outline, run_dir / "mindmap.svg")
                render_png(run_dir / "mindmap.svg", run_dir / "mindmap.png")
                render_overview_svg(outline, run_dir / "mindmap-overview.svg")
                render_png(
                    run_dir / "mindmap-overview.svg", run_dir / "mindmap-overview.png"
                )
                mindmap_inspection = inspect_svg_layout(run_dir / "mindmap.svg")
                overview_inspection = inspect_svg_layout(run_dir / "mindmap-overview.svg")
                mindmap_inspection["overview"] = overview_inspection
                (run_dir / "mindmap-inspection.json").write_text(
                    json.dumps(mindmap_inspection, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if mindmap_inspection["findings"] or overview_inspection["findings"]:
                    raise RuntimeError("导图存在文字越界，已停止发布")
                trace.add(
                    "render_tree",
                    "complete",
                    inspection=mindmap_inspection,
                    artifacts=[
                        "notes.md",
                        "mindmap.svg",
                        "mindmap.png",
                        "mindmap-overview.svg",
                        "mindmap-overview.png",
                    ],
                )
                _notify(
                    progress,
                    phase="render_tree",
                    event="complete",
                    message="知识树与导图检查完成",
                    progress=82 if "report" in requested_outputs else 93,
                )

            if "report" in requested_outputs:
                trace.status("RUNNING", "report")
                trace.add(
                    "report",
                    "start",
                    provider=self.settings.report_provider,
                    model=self.settings.report_model,
                    engine=report_engine,
                )
                stage_started = time.perf_counter()
                report_started_at = stage_started
                _notify(
                    progress,
                    phase="report",
                    event="start",
                    message={
                        "editorial": "生成编辑型报告：编辑策划、整篇写作、终稿复核",
                        "efficient": "生成高效结构化报告：先规划覆盖账本，再写完整内容",
                        "legacy": "生成现有 Agent 精读报告并进行版面检查",
                    }[report_engine],
                    progress=84 if "tree" in requested_outputs else 20,
                )
                if report_engine == "editorial":
                    report = generate_editorial_report(
                        run_dir,
                        units,
                        metadata,
                        base_url=self.settings.note_base_url,
                        model=self.settings.report_model,
                        api_key=self.settings.report_api_key,
                        report_mode=report_mode,
                        outline=outline,
                        progress=progress,
                        telemetry=telemetry,
                    )
                elif report_engine == "efficient":
                    report = generate_efficient_report(
                        run_dir,
                        units,
                        metadata,
                        base_url=self.settings.note_base_url,
                        model=self.settings.report_model,
                        api_key=self.settings.report_api_key,
                        report_mode=report_mode,
                        outline=outline,
                        progress=progress,
                        telemetry=telemetry,
                    )
                else:
                    report = generate_report(
                        run_dir,
                        provider=self.settings.report_provider,
                        model=self.settings.report_model,
                        api_key=self.settings.report_api_key,
                        report_mode=report_mode,
                        telemetry=telemetry,
                    )
                usage_data["report"] = {
                    **report["usage"],
                    "provider": self.settings.report_provider,
                    "model": self.settings.report_model,
                    "engine": report_engine,
                    "elapsed_seconds": round(time.perf_counter() - stage_started, 3),
                }
                (run_dir / "usage.json").write_text(
                    json.dumps(usage_data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                trace.add(
                    "report",
                    "complete",
                    findings=len(report["inspection"].get("findings", [])),
                    source_id_count=report["inspection"].get("source_id_count"),
                    usage=usage_data["report"],
                    agent_workflow={
                        key: value
                        for key, value in report.get("agent_summary", {}).items()
                        if key != "steps"
                    },
                    engine=report_engine,
                    artifacts=report.get(
                        "artifacts",
                        [
                            "report.html",
                            "report.png",
                            "report-inspection.json",
                            "report-agent-summary.json",
                        ],
                    ),
                )
                _notify(
                    progress,
                    phase="report",
                    event="complete",
                    message="详细报告生成完成",
                    progress=94,
                )

            if outline is not None:
                trace.add(
                    "link",
                    "start",
                    tree="outline.json",
                    report="report.html" if report is not None else None,
                )
                _notify(
                    progress,
                    phase="link",
                    event="start",
                    message="生成树与报告的定位联动",
                    progress=95,
                )
                render_tree_html(
                    outline,
                    units,
                    metadata,
                    run_dir / "tree-classic.html",
                    report_filename="report.html" if report is not None else None,
                )
                render_compact_tree_html(
                    outline,
                    units,
                    metadata,
                    run_dir / "tree.html",
                    report_filename="report.html" if report is not None else None,
                )
                trace.add(
                    "link", "complete", artifacts=["tree.html", "tree-classic.html"]
                )
            (run_dir / "usage.json").write_text(
                json.dumps(usage_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            obsidian = None
            if publish:
                trace.status("RUNNING", "obsidian")
                trace.add("obsidian", "start", root=str(self.settings.obsidian_root))
                _notify(
                    progress,
                    phase="obsidian",
                    event="start",
                    message="保存到 Obsidian",
                    progress=97,
                )
                obsidian = publish_to_obsidian(run_dir, metadata, self.settings.obsidian_root)
                trace.add("obsidian", "complete", directory=obsidian["directory"])
            result = {
                "state": "COMPLETE",
                "run_dir": str(run_dir),
                "title": metadata["title"],
                "outputs": sorted(requested_outputs),
                "source_fingerprint": metadata["source_fingerprint"],
                "canonical_unit_count": len(units),
                "report_engine": report_engine if report is not None else None,
                "transcript_cache": metadata.get("transcript_cache"),
                "subtitle": str(run_dir / "subtitle.txt"),
                "transcript": str(run_dir / "transcript.md"),
                "canonical_transcript": str(run_dir / "canonical-transcript.jsonl"),
                "usage": usage_data,
                "obsidian": obsidian,
            }
            if outline is not None:
                result.update(
                    notes=str(run_dir / "notes.md"),
                    mindmap=str(run_dir / "mindmap.png"),
                    mindmap_svg=str(run_dir / "mindmap.svg"),
                    mindmap_overview=str(run_dir / "mindmap-overview.png"),
                    mindmap_overview_svg=str(run_dir / "mindmap-overview.svg"),
                    tree_html=str(run_dir / "tree.html"),
                    tree_classic_html=str(run_dir / "tree-classic.html"),
                    tree_metrics=outline_metrics(outline),
                    mindmap_inspection=mindmap_inspection,
                )
            if report is not None:
                result.update(
                    report_html=report["html"],
                    report_png=report["png"],
                    inspection=report["inspection"],
                )
                for source_key, result_key in (
                    ("plan", "report_plan"),
                    ("document", "report_document"),
                    ("review", "report_review"),
                    ("summary", "report_summary"),
                ):
                    if report.get(source_key):
                        result[result_key] = report[source_key]
            trace.status("COMPLETE", "done", result=result)
            _notify(
                progress,
                phase="done",
                event="complete",
                message="全部成果已保存",
                progress=100,
            )
            if obsidian:
                shutil.copy2(
                    run_dir / "status.json", Path(obsidian["directory"]) / "_素材" / "status.json"
                )
            return result
        except Exception as exc:
            _notify(
                progress,
                phase="failed",
                event="failed",
                message=f"生成失败：{type(exc).__name__}: {exc}",
            )
            if isinstance(exc, ModelStageError):
                failed_usage = dict(exc.usage)
                if str(exc.stage).startswith(("report-efficient", "report-editorial")):
                    if report_started_at is not None:
                        failed_usage["elapsed_seconds"] = round(
                            time.perf_counter() - report_started_at, 3
                        )
                    usage_data["report"] = {
                        **failed_usage,
                        "provider": self.settings.report_provider,
                        "model": self.settings.report_model,
                        "engine": report_engine,
                    }
                else:
                    if notes_started_at is not None:
                        failed_usage["elapsed_seconds"] = round(
                            time.perf_counter() - notes_started_at, 3
                        )
                    usage_data["notes"] = failed_usage
                (run_dir / "usage.json").write_text(
                    json.dumps(usage_data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            trace.add("pipeline", "failed", error_type=type(exc).__name__, error=str(exc))
            trace.status("FAILED", "error", error_type=type(exc).__name__, error=str(exc))
            raise

    def rebuild_tree(self, run_dir: Path, *, publish: bool = True) -> dict:
        run_dir = run_dir.resolve()
        runs_root = (self.settings.project_root / "runs").resolve()
        if not run_dir.is_relative_to(runs_root):
            raise ValueError(f"只能重建当前工作区 runs 下的结果：{runs_root}")
        required = [
            run_dir / "canonical-transcript.jsonl",
            run_dir / "source.info.json",
            run_dir / "report.html",
            run_dir / "report.png",
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"运行目录缺少必要文件：{missing}")
        trace = RunTrace(run_dir)
        trace.add("tree-rebuild", "start", model=self.settings.note_model)
        units = _read_units(run_dir / "canonical-transcript.jsonl")
        metadata = json.loads((run_dir / "source.info.json").read_text(encoding="utf-8"))
        outline, skeleton, usage = generate_outline(
            units,
            base_url=self.settings.note_base_url,
            model=self.settings.note_model,
            api_key=self.settings.note_api_key,
            skeleton_path=run_dir / "skeleton.json",
        )
        (run_dir / "skeleton.json").write_text(
            json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "outline.json").write_text(
            json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        usage_path = run_dir / "usage.json"
        previous_usage = (
            json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.is_file() else {}
        )
        previous_usage["notes"] = usage
        usage_path.write_text(
            json.dumps(previous_usage, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        render_markdown(outline, metadata, run_dir / "notes.md")
        render_svg(outline, run_dir / "mindmap.svg")
        render_png(run_dir / "mindmap.svg", run_dir / "mindmap.png")
        render_overview_svg(outline, run_dir / "mindmap-overview.svg")
        render_png(run_dir / "mindmap-overview.svg", run_dir / "mindmap-overview.png")
        mindmap_inspection = inspect_svg_layout(run_dir / "mindmap.svg")
        overview_inspection = inspect_svg_layout(run_dir / "mindmap-overview.svg")
        mindmap_inspection["overview"] = overview_inspection
        (run_dir / "mindmap-inspection.json").write_text(
            json.dumps(mindmap_inspection, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if mindmap_inspection["findings"] or overview_inspection["findings"]:
            raise RuntimeError("导图存在文字越界，已停止发布")
        render_tree_html(
            outline,
            units,
            metadata,
            run_dir / "tree-classic.html",
            report_filename="report.html" if (run_dir / "report.html").is_file() else None,
        )
        render_compact_tree_html(
            outline,
            units,
            metadata,
            run_dir / "tree.html",
            report_filename="report.html" if (run_dir / "report.html").is_file() else None,
        )
        obsidian = (
            publish_to_obsidian(run_dir, metadata, self.settings.obsidian_root) if publish else None
        )
        metrics = outline_metrics(outline)
        trace.add("tree-rebuild", "complete", **metrics, obsidian=obsidian)
        return {
            "state": "COMPLETE",
            "run_dir": str(run_dir),
            **metrics,
            "summary_count": len(outline["summary"]),
            "mindmap_inspection": mindmap_inspection,
            "notes": str(run_dir / "notes.md"),
            "mindmap": str(run_dir / "mindmap.png"),
            "mindmap_overview": str(run_dir / "mindmap-overview.png"),
            "tree_html": str(run_dir / "tree.html"),
            "tree_classic_html": str(run_dir / "tree-classic.html"),
            "obsidian": obsidian,
        }
