from __future__ import annotations

import hashlib
import json
import mimetypes
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .config import Settings
from .obsidian import safe_name
from .pipeline import FusionPipeline

ARTIFACT_KEYS = (
    "tree_html",
    "report_html",
    "mindmap",
    "mindmap_svg",
    "report_png",
    "notes",
)

HISTORY_ARTIFACTS = {
    "tree.html": "tree_html",
    "report.html": "report_html",
    "mindmap.png": "mindmap",
    "mindmap.svg": "mindmap_svg",
    "report.png": "report_png",
    "notes.md": "notes",
}


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        # Model work is intentionally serialized to keep local cost and machine load predictable.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fusion-web")

    def submit(self, payload: dict) -> dict:
        job_id = uuid.uuid4().hex[:12]
        outputs = {
            name for name in ("tree", "report") if bool((payload.get("outputs") or {}).get(name))
        }
        if not outputs:
            raise ValueError("至少选择树状图或详细总结中的一项")
        source_type = str(payload.get("source_type") or "bilibili")
        if source_type not in {"bilibili", "subtitle"}:
            raise ValueError("来源类型无效")
        source = self._prepare_source(payload, source_type)
        title = str(payload.get("title") or "").strip() or None
        uploader = str(payload.get("uploader") or "").strip() or None
        source_url = str(payload.get("source_url") or "").strip() or None
        report_mode = str(payload.get("report_mode") or "standard")
        if report_mode not in {"standard", "brief"}:
            raise ValueError("报告模式无效")
        custom = payload.get("models") or {}
        settings = replace(
            self.settings,
            note_base_url=str(custom.get("note_base_url") or self.settings.note_base_url).rstrip(
                "/"
            ),
            note_model=str(custom.get("note_model") or self.settings.note_model),
            note_api_key=str(custom.get("note_api_key") or self.settings.note_api_key),
            report_provider=str(custom.get("report_provider") or self.settings.report_provider),
            report_model=str(custom.get("report_model") or self.settings.report_model),
            report_api_key=str(custom.get("report_api_key") or self.settings.report_api_key),
            obsidian_root=Path(
                str(payload.get("obsidian_root") or self.settings.obsidian_root)
            ).expanduser(),
        )
        if "tree" in outputs and not settings.note_api_key:
            raise ValueError("生成知识树需要填写 Tree API Key 或在 .env 配置 NOTE_API_KEY")
        if "report" in outputs and not settings.report_api_key:
            raise ValueError("生成详细报告需要填写 Report API Key 或在 .env 配置 REPORT_API_KEY")
        now = time.time()
        public_job = {
            "id": job_id,
            "state": "queued",
            "stage": "等待执行",
            "created_at": now,
            "outputs": sorted(outputs),
            "source_type": source_type,
            "title": title or (Path(source).stem if source_type == "subtitle" else source),
            "progress": 0,
            "events": [
                {
                    "at": now,
                    "phase": "queue",
                    "event": "queued",
                    "message": "任务已进入队列",
                    "progress": 0,
                }
            ],
        }
        with self.lock:
            self.jobs[job_id] = public_job
        self.executor.submit(
            self._run,
            job_id,
            settings,
            source,
            title,
            uploader,
            source_url,
            report_mode,
            outputs,
            bool(payload.get("save_to_obsidian")),
        )
        return dict(public_job)

    def _prepare_source(self, payload: dict, source_type: str) -> str:
        if source_type == "bilibili":
            source = str(payload.get("bilibili_url") or "").strip()
            if not source:
                raise ValueError("请填写 B 站链接或 BV 号")
            return source
        text = str(payload.get("subtitle_text") or "")
        if not text.strip():
            raise ValueError("请粘贴字幕或选择本地字幕文件")
        if len(text.encode("utf-8")) > 12 * 1024 * 1024:
            raise ValueError("字幕文件不能超过 12 MB")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        filename = safe_name(str(payload.get("subtitle_filename") or "subtitle.txt"), 60)
        suffix = Path(filename).suffix or ".txt"
        directory = self.settings.project_root / "cache" / "web-inputs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}{suffix}"
        if not path.is_file():
            path.write_text(text, encoding="utf-8")
        return str(path)

    def _run(
        self,
        job_id: str,
        settings: Settings,
        source: str,
        title: str | None,
        uploader: str | None,
        source_url: str | None,
        report_mode: str,
        outputs: set[str],
        publish: bool,
    ) -> None:
        started = time.time()
        self._update(job_id, state="running", stage="读取字幕并生成", started_at=started)

        def on_progress(event: dict) -> None:
            self._progress(job_id, event)

        try:
            result = FusionPipeline(settings).run(
                source,
                title=title,
                uploader=uploader,
                source_url=source_url,
                report_mode=report_mode,
                publish=publish,
                outputs=outputs,
                progress=on_progress,
            )
            run_dir = Path(result["run_dir"]).resolve()
            artifacts = {}
            for key in ARTIFACT_KEYS:
                value = result.get(key)
                if not value:
                    continue
                path = Path(value).resolve()
                if path.is_file() and path.is_relative_to(run_dir):
                    relative = path.relative_to(run_dir).as_posix()
                    artifacts[key] = f"/artifacts/{job_id}/{quote(relative)}"
            primary = artifacts.get("tree_html") or artifacts.get("report_html")
            self._update(
                job_id,
                state="complete",
                stage="完成",
                finished_at=time.time(),
                elapsed_seconds=round(time.time() - started, 2),
                result={
                    **result,
                    "artifacts": artifacts,
                    "primary_url": primary,
                },
            )
        except Exception as exc:
            self._update(
                job_id,
                state="failed",
                stage="失败",
                finished_at=time.time(),
                elapsed_seconds=round(time.time() - started, 2),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _progress(self, job_id: str, event: dict) -> None:
        now = time.time()
        public_event = {
            "at": now,
            "phase": str(event.get("phase") or "pipeline"),
            "event": str(event.get("event") or "update"),
            "message": str(event.get("message") or "处理中"),
        }
        for key in ("attempt", "total_tokens", "units", "progress"):
            if event.get(key) is not None:
                public_event[key] = event[key]
        with self.lock:
            job = self.jobs[job_id]
            job["stage"] = public_event["message"]
            if isinstance(public_event.get("progress"), (int, float)):
                job["progress"] = max(0, min(100, int(public_event["progress"])))
            events = job.setdefault("events", [])
            events.append(public_event)
            del events[:-40]

    def _update(self, job_id: str, **fields) -> None:
        with self.lock:
            self.jobs[job_id].update(fields)

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            value = self.jobs.get(job_id)
            return dict(value) if value else None

    def artifact(self, job_id: str, relative: str) -> Path | None:
        job = self.get(job_id)
        if not job or job.get("state") != "complete":
            return None
        run_dir = Path(job["result"]["run_dir"]).resolve()
        candidate = (run_dir / unquote(relative)).resolve()
        return candidate if candidate.is_file() and candidate.is_relative_to(run_dir) else None

    def history_artifact(self, run_name: str, relative: str) -> Path | None:
        runs_root = (self.settings.project_root / "runs").resolve()
        run_dir = (runs_root / unquote(run_name)).resolve()
        if not run_dir.is_dir() or not run_dir.is_relative_to(runs_root):
            return None
        decoded = unquote(relative)
        if decoded not in HISTORY_ARTIFACTS:
            return None
        candidate = (run_dir / decoded).resolve()
        return candidate if candidate.is_file() and candidate.is_relative_to(run_dir) else None

    def history(self, limit: int = 50) -> list[dict]:
        runs_root = self.settings.project_root / "runs"
        if not runs_root.is_dir():
            return []
        rows: list[dict] = []
        candidates = sorted(
            (path for path in runs_root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for run_dir in candidates[: max(1, min(limit, 200))]:
            metadata = _read_json(run_dir / "source.info.json")
            status = _read_json(run_dir / "status.json")
            usage = _read_json(run_dir / "usage.json")
            outline = _read_json(run_dir / "outline.json")
            notes_usage = usage.get("notes") if isinstance(usage.get("notes"), dict) else {}
            report_usage = usage.get("report") if isinstance(usage.get("report"), dict) else {}
            reused_generation = (
                notes_usage.get("reused_generation_usage")
                if isinstance(notes_usage.get("reused_generation_usage"), dict)
                else {}
            )
            outputs = metadata.get("outputs") if isinstance(metadata.get("outputs"), list) else []
            if not outputs:
                outputs = [
                    name
                    for name, filename in (("tree", "tree.html"), ("report", "report.html"))
                    if (run_dir / filename).is_file()
                ]
            artifacts = {
                key: f"/history-artifacts/{quote(run_dir.name)}/{quote(filename)}"
                for filename, key in HISTORY_ARTIFACTS.items()
                if (run_dir / filename).is_file()
            }
            summary = outline.get("summary") if isinstance(outline.get("summary"), list) else []
            summary_text = [
                str(item.get("text") or "")
                for item in summary[:3]
                if isinstance(item, dict) and item.get("text")
            ]
            state = str(status.get("state") or "UNKNOWN").lower()
            elapsed_values = [notes_usage.get("elapsed_seconds"), report_usage.get("elapsed_seconds")]
            elapsed_seconds = sum(
                float(value) for value in elapsed_values if isinstance(value, (int, float))
            )
            total_tokens = int(notes_usage.get("total_tokens") or 0) + int(
                report_usage.get("tokens") or 0
            )
            calls = int(notes_usage.get("attempts") or 0) + int(
                report_usage.get("llm_calls") or 0
            )
            rows.append(
                {
                    "id": run_dir.name,
                    "title": str(metadata.get("title") or outline.get("title") or run_dir.name),
                    "source_type": metadata.get("source_type"),
                    "outputs": outputs,
                    "state": state,
                    "stage": status.get("stage"),
                    "updated_at": status.get("updated_at"),
                    "created_at": run_dir.stat().st_ctime,
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "total_tokens": total_tokens,
                    "calls": calls,
                    "reused_generation_tokens": int(
                        reused_generation.get("total_tokens") or 0
                    ),
                    "reused_generation_calls": int(reused_generation.get("attempts") or 0),
                    "note_model": notes_usage.get("model"),
                    "report_model": report_usage.get("model"),
                    "thesis": outline.get("thesis"),
                    "summary": summary_text,
                    "error": status.get("error"),
                    "artifacts": artifacts,
                    "primary_url": artifacts.get("tree_html") or artifacts.get("report_html"),
                }
            )
        return rows


class WebHandler(BaseHTTPRequestHandler):
    server_version = "FusionVideoWeb/1.0"

    @property
    def manager(self) -> JobManager:
        return self.server.manager  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size > 14 * 1024 * 1024:
            raise ValueError("请求内容过大")
        value = json.loads(self.rfile.read(size).decode("utf-8")) if size else {}
        if not isinstance(value, dict):
            raise ValueError("请求必须是 JSON 对象")
        return value

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            body = Path(__file__).with_name("web_ui.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/config":
            settings = self.manager.settings
            self._json(
                {
                    "note_base_url": settings.note_base_url,
                    "note_model": settings.note_model,
                    "note_api_key_configured": bool(settings.note_api_key),
                    "report_provider": settings.report_provider,
                    "report_model": settings.report_model,
                    "report_api_key_configured": bool(settings.report_api_key),
                    "obsidian_root": str(settings.obsidian_root),
                }
            )
            return
        if path == "/api/history":
            try:
                limit = int((parse_qs(parsed.query).get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            self._json({"runs": self.manager.history(limit)})
            return
        if path.startswith("/api/jobs/"):
            job_id = path.removeprefix("/api/jobs/").strip("/")
            job = self.manager.get(job_id)
            if job:
                self._json(job)
            else:
                self._error(HTTPStatus.NOT_FOUND, "任务不存在")
            return
        if path.startswith("/artifacts/"):
            parts = path.split("/", 3)
            if len(parts) != 4:
                self._error(HTTPStatus.NOT_FOUND, "文件不存在")
                return
            artifact = self.manager.artifact(parts[2], parts[3])
            if not artifact:
                self._error(HTTPStatus.NOT_FOUND, "文件不存在")
                return
            body = artifact.read_bytes()
            mime = mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/history-artifacts/"):
            parts = path.split("/", 3)
            if len(parts) != 4:
                self._error(HTTPStatus.NOT_FOUND, "文件不存在")
                return
            artifact = self.manager.history_artifact(parts[2], parts[3])
            if not artifact:
                self._error(HTTPStatus.NOT_FOUND, "文件不存在")
                return
            body = artifact.read_bytes()
            mime = mimetypes.guess_type(artifact.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") else mime
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._error(HTTPStatus.NOT_FOUND, "路径不存在")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/jobs":
            self._error(HTTPStatus.NOT_FOUND, "路径不存在")
            return
        try:
            job = self.manager.submit(self._body())
            self._json(job, HTTPStatus.ACCEPTED)
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")


def serve_web(
    settings: Settings,
    *,
    host: str = "127.0.0.1",
    port: int = 8766,
    open_browser: bool = True,
) -> None:
    manager = JobManager(settings)
    server = ThreadingHTTPServer((host, port), WebHandler)
    server.manager = manager  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"字幕知识管道：{url}")
    print("API Key 只保留在本次进程内存，不写入运行目录。按 Ctrl+C 停止。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.executor.shutdown(wait=False, cancel_futures=True)
