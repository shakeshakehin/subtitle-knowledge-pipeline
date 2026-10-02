from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from http.cookies import SimpleCookie
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


@dataclass
class WebAccess:
    enabled: bool = False
    username: str = ""
    password: str = ""
    admin_token: str = ""
    session_seconds: int = 12 * 60 * 60
    admin_session_seconds: int = 30 * 24 * 60 * 60
    sessions: dict[str, float] = field(default_factory=dict, repr=False)
    failed_logins: dict[str, list[float]] = field(default_factory=dict, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_environment(cls, *, enabled: bool) -> "WebAccess":
        if not enabled:
            return cls()
        username = os.getenv("FUSION_ACCESS_USERNAME", "").strip()
        password = os.getenv("FUSION_ACCESS_PASSWORD", "")
        admin_token = os.getenv("FUSION_ADMIN_TOKEN", "")
        if not username or len(password) < 16:
            raise RuntimeError(
                "外网模式需要 FUSION_ACCESS_USERNAME，以及至少 16 个字符的 "
                "FUSION_ACCESS_PASSWORD"
            )
        if admin_token and len(admin_token) < 32:
            raise RuntimeError("FUSION_ADMIN_TOKEN 至少需要 32 个字符")
        return cls(
            enabled=True,
            username=username,
            password=password,
            admin_token=admin_token,
        )

    def issue_session(self, username: str, password: str, client_key: str) -> str | None:
        now = time.time()
        with self.lock:
            recent = [stamp for stamp in self.failed_logins.get(client_key, []) if now - stamp < 600]
            if len(recent) >= 10:
                self.failed_logins[client_key] = recent
                return None
            valid = hmac.compare_digest(username, self.username) and hmac.compare_digest(
                password, self.password
            )
            if not valid:
                recent.append(now)
                self.failed_logins[client_key] = recent
                return None
            self.failed_logins.pop(client_key, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + self.session_seconds
            return token

    def issue_admin_session(self, presented_token: str) -> str | None:
        if not self.enabled or not self.admin_token or not hmac.compare_digest(
            presented_token, self.admin_token
        ):
            return None
        expires = int(time.time()) + self.admin_session_seconds
        payload = f"admin.{expires}.{secrets.token_urlsafe(18)}"
        signature = hmac.new(
            self.admin_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"{payload}.{signature}"

    def session_role(self, token: str) -> str | None:
        if self.admin_token and token.startswith("admin."):
            try:
                prefix, expires_text, nonce, signature = token.split(".", 3)
                payload = f"{prefix}.{expires_text}.{nonce}"
                expected = hmac.new(
                    self.admin_token.encode("utf-8"),
                    payload.encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                if int(expires_text) > int(time.time()) and hmac.compare_digest(
                    signature, expected
                ):
                    return "admin"
            except (TypeError, ValueError):
                return None
            return None
        return "user" if self.session_is_valid(token) else None

    def session_is_valid(self, token: str) -> bool:
        now = time.time()
        with self.lock:
            expired = [key for key, expires in self.sessions.items() if expires <= now]
            for key in expired:
                self.sessions.pop(key, None)
            expires = self.sessions.get(token)
            return bool(expires and expires > now)

    def revoke_session(self, token: str) -> None:
        with self.lock:
            self.sessions.pop(token, None)


def basic_auth_matches(header: str | None, access: WebAccess) -> bool:
    if not access.enabled:
        return True
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(":")
    return bool(separator) and hmac.compare_digest(
        username, access.username
    ) and hmac.compare_digest(password, access.password)


def _session_token(cookie_header: str | None) -> str:
    if not cookie_header:
        return ""
    cookie = SimpleCookie()
    try:
        cookie.load(cookie_header)
    except Exception:
        return ""
    value = cookie.get("fusion_session")
    return value.value if value else ""


def session_cookie_matches(cookie_header: str | None, access: WebAccess) -> bool:
    return access.enabled and access.session_role(_session_token(cookie_header)) is not None


def session_cookie_role(cookie_header: str | None, access: WebAccess) -> str | None:
    if not access.enabled:
        return "admin"
    return access.session_role(_session_token(cookie_header))


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class JobManager:
    def __init__(self, settings: Settings, *, public_mode: bool = False):
        self.settings = settings
        self.public_mode = public_mode
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        # Model work is intentionally serialized to keep local cost and machine load predictable.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fusion-web")

    def submit(self, payload: dict) -> dict:
        with self.lock:
            active_jobs = sum(
                job.get("state") in {"queued", "running"} for job in self.jobs.values()
            )
        if active_jobs >= 3:
            raise ValueError("当前已有 3 个任务等待或运行，请稍后再试")
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
        settings = self.request_settings(payload)
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

    def request_settings(self, payload: dict) -> Settings:
        custom = payload.get("models") if isinstance(payload.get("models"), dict) else {}
        # In public mode, endpoint/provider and filesystem roots are server policy.
        # A remote request may still choose a model and provide its own in-memory key.
        note_base_url = (
            self.settings.note_base_url
            if self.public_mode
            else str(custom.get("note_base_url") or self.settings.note_base_url).rstrip("/")
        )
        report_provider = (
            self.settings.report_provider
            if self.public_mode
            else str(custom.get("report_provider") or self.settings.report_provider)
        )
        obsidian_root = (
            self.settings.obsidian_root
            if self.public_mode
            else Path(str(payload.get("obsidian_root") or self.settings.obsidian_root)).expanduser()
        )
        requested_note_key = str(custom.get("note_api_key") or "").strip()
        requested_report_key = str(custom.get("report_api_key") or "").strip()
        shared_official_deepseek = (
            report_provider.casefold() == "deepseek"
            and note_base_url.rstrip("/")
            in {"https://api.deepseek.com", "https://api.deepseek.com/v1"}
        )
        if shared_official_deepseek:
            note_api_key = (
                requested_note_key or requested_report_key or self.settings.note_api_key
            )
            report_api_key = (
                requested_report_key or requested_note_key or self.settings.report_api_key
            )
        else:
            note_api_key = requested_note_key or self.settings.note_api_key
            report_api_key = requested_report_key or self.settings.report_api_key
        settings = replace(
            self.settings,
            note_base_url=note_base_url,
            note_model=str(custom.get("note_model") or self.settings.note_model),
            note_api_key=note_api_key,
            report_provider=report_provider,
            report_model=str(custom.get("report_model") or self.settings.report_model),
            report_api_key=report_api_key,
            obsidian_root=obsidian_root,
        )
        return settings

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

    @property
    def access(self) -> WebAccess:
        return self.server.web_access  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        return

    def _security_headers(self, *, allow_embedding: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if not allow_embedding:
            self.send_header("X-Frame-Options", "SAMEORIGIN")

    def _access_role(self) -> str | None:
        if not self.access.enabled:
            return "admin"
        if basic_auth_matches(self.headers.get("Authorization"), self.access):
            return "user"
        return session_cookie_role(self.headers.get("Cookie"), self.access)

    def _is_authenticated(self) -> bool:
        return self._access_role() is not None

    def _authenticate(self, *, api: bool = False) -> bool:
        if self._is_authenticated():
            return True
        if api:
            self._json({"error": "登录已失效，请刷新页面重新登录"}, HTTPStatus.UNAUTHORIZED)
        else:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/login")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
        return False

    def _json(
        self,
        payload: object,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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
        if path == "/login":
            if self._is_authenticated():
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = Path(__file__).with_name("login_ui.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; img-src 'self' data:",
            )
            self._security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self._authenticate(api=path.startswith("/api/")):
            return
        if path == "/":
            body = Path(__file__).with_name("web_ui.html").read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "img-src 'self' data: blob:; frame-src 'self'; connect-src 'self'",
            )
            self._security_headers()
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
                    "obsidian_root": "" if self.manager.public_mode else str(settings.obsidian_root),
                    "public_mode": self.manager.public_mode,
                    "access_role": self._access_role(),
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
            self.send_header("Cache-Control", "private, no-store")
            if mime == "text/html":
                self.send_header(
                    "Content-Security-Policy",
                    "sandbox allow-scripts allow-downloads; default-src 'self' data: blob: https:; "
                    "style-src 'unsafe-inline' 'self' https:; script-src 'unsafe-inline' 'self'; "
                    "img-src 'self' data: blob: https:; connect-src 'none'; frame-src * data: blob:",
                )
            self._security_headers(allow_embedding=True)
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
            self.send_header("Cache-Control", "private, no-store")
            if mime == "text/html":
                self.send_header(
                    "Content-Security-Policy",
                    "sandbox allow-scripts allow-downloads; default-src 'self' data: blob: https:; "
                    "style-src 'unsafe-inline' 'self' https:; script-src 'unsafe-inline' 'self'; "
                    "img-src 'self' data: blob: https:; connect-src 'none'; frame-src * data: blob:",
                )
            self._security_headers(allow_embedding=True)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._error(HTTPStatus.NOT_FOUND, "路径不存在")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/login":
            try:
                if int(self.headers.get("Content-Length", "0")) > 8192:
                    raise ValueError("请求内容过大")
                payload = self._body()
                client_key = str(
                    self.headers.get("CF-Connecting-IP") or self.client_address[0] or "unknown"
                )
                token = self.access.issue_session(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                    client_key,
                )
                if not token:
                    self._error(HTTPStatus.UNAUTHORIZED, "用户名或密码错误；连续失败过多时请稍后再试")
                    return
                cookie = (
                    f"fusion_session={token}; Path=/; Max-Age={self.access.session_seconds}; "
                    "HttpOnly; Secure; SameSite=Strict"
                )
                self._json({"ok": True}, headers={"Set-Cookie": cookie})
            except (ValueError, json.JSONDecodeError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if path == "/api/admin-login":
            try:
                if int(self.headers.get("Content-Length", "0")) > 8192:
                    raise ValueError("请求内容过大")
                payload = self._body()
                token = self.access.issue_admin_session(str(payload.get("token") or ""))
                if not token:
                    self._error(HTTPStatus.UNAUTHORIZED, "管理员直达链接无效")
                    return
                cookie = (
                    f"fusion_session={token}; Path=/; "
                    f"Max-Age={self.access.admin_session_seconds}; "
                    "HttpOnly; Secure; SameSite=Strict"
                )
                self._json(
                    {"ok": True, "role": "admin"},
                    headers={"Set-Cookie": cookie},
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not self._authenticate(api=True):
            return
        if path == "/api/logout":
            self.access.revoke_session(_session_token(self.headers.get("Cookie")))
            self._json(
                {"ok": True},
                headers={
                    "Set-Cookie": (
                        "fusion_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"
                    )
                },
            )
            return
        if path != "/api/jobs":
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
    public_mode: bool = False,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"} and not public_mode:
        raise RuntimeError("拒绝在未启用 --public-mode 时监听非本机地址")
    access = WebAccess.from_environment(enabled=public_mode)
    manager = JobManager(settings, public_mode=public_mode)
    server = ThreadingHTTPServer((host, port), WebHandler)
    server.manager = manager  # type: ignore[attr-defined]
    server.web_access = access  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"字幕知识管道：{url}")
    print("API Key 只保留在本次进程内存，不写入运行目录。按 Ctrl+C 停止。")
    if public_mode:
        print("外网安全模式：已启用访问认证，并锁定模型端点、Provider 与 Obsidian 路径。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.executor.shutdown(wait=False, cancel_futures=True)
