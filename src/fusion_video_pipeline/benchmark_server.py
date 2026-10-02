from __future__ import annotations

import io
import json
import re
import threading
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .benchmark_common import (
    BENCHMARK_ROOT,
    load_manifest,
    read_json,
    resolve_benchmark_path,
    review_payload,
    review_template,
    utc_stamp,
    write_json,
)
from .benchmark_score import score_benchmark

SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SAFE_SAMPLE = re.compile(r"^S\d{3}$")


def _compact_manifest(root: Path, reviewer: str) -> list[dict]:
    blind = read_json(root / "admin" / "blind-key.json", {}).get("samples") or {}
    rows: list[dict] = []
    for row in load_manifest(root):
        gold = read_json(resolve_benchmark_path(row["gold_path"], root), {})
        review = read_json(root / "annotations" / reviewer / f"{row['id']}.json", {})
        rows.append(
            {
                **row,
                "gold_status": gold.get("status", "draft"),
                "review_status": review.get("status", "draft"),
                "blind_ready": row["id"] in blind,
                "core_point_count": len(gold.get("core_points") or []),
                "conclusion_count": len(gold.get("conclusions") or []),
            }
        )
    return rows


def _sample_payload(root: Path, sample_id: str, reviewer: str) -> dict:
    manifest = {row["id"]: row for row in load_manifest(root)}
    if sample_id not in manifest:
        raise KeyError(sample_id)
    sample = manifest[sample_id]
    transcript = resolve_benchmark_path(sample["subtitle_path"], root).read_text(
        encoding="utf-8-sig"
    )
    gold = read_json(resolve_benchmark_path(sample["gold_path"], root), {})
    review_path = root / "annotations" / reviewer / f"{sample_id}.json"
    review = read_json(review_path, review_template(sample_id, reviewer))
    versions: dict[str, dict] = {}
    for label in ("A", "B"):
        path = root / "outputs" / "blind" / sample_id / f"{label}.json"
        if path.is_file():
            versions[label] = review_payload(read_json(path, {}), sample_id, label)
    return {
        "sample": sample,
        "transcript_lines": [
            {"number": index, "text": line}
            for index, line in enumerate(transcript.splitlines(), 1)
            if line.strip()
        ],
        "gold": gold,
        "review": review,
        "versions": versions,
    }


class BenchmarkHandler(BaseHTTPRequestHandler):
    server_version = "FusionBenchmark/1.0"

    @property
    def root(self) -> Path:
        return self.server.benchmark_root  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(size).decode("utf-8")) if size else {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            html_path = Path(__file__).with_name("benchmark_ui.html")
            body = html_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/manifest":
            reviewer = (query.get("reviewer") or ["owner"])[0]
            if not SAFE_NAME.fullmatch(reviewer):
                self._error(HTTPStatus.BAD_REQUEST, "评审者名称无效")
                return
            self._json({"reviewer": reviewer, "samples": _compact_manifest(self.root, reviewer)})
            return
        if parsed.path == "/api/sample":
            sample_id = (query.get("id") or [""])[0]
            reviewer = (query.get("reviewer") or ["owner"])[0]
            if not SAFE_SAMPLE.fullmatch(sample_id) or not SAFE_NAME.fullmatch(reviewer):
                self._error(HTTPStatus.BAD_REQUEST, "样本或评审者名称无效")
                return
            try:
                self._json(_sample_payload(self.root, sample_id, reviewer))
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "样本不存在")
            return
        if parsed.path == "/api/export":
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for directory in ("gold", "annotations", "reports"):
                    base = self.root / directory
                    if not base.is_dir():
                        continue
                    for path in base.rglob("*"):
                        if path.is_file():
                            archive.write(path, path.relative_to(self.root).as_posix())
            body = buffer.getvalue()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition", 'attachment; filename="benchmark-annotations.zip"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._error(HTTPStatus.NOT_FOUND, "路径不存在")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._body()
            if parsed.path == "/api/gold":
                sample_id = str(payload.get("sample_id") or "")
                if not SAFE_SAMPLE.fullmatch(sample_id):
                    raise ValueError("样本 ID 无效")
                payload["updated_at"] = utc_stamp()
                write_json(self.root / "gold" / f"{sample_id}.json", payload)
                self._json({"saved": True, "updated_at": payload["updated_at"]})
                return
            if parsed.path == "/api/review":
                sample_id = str(payload.get("sample_id") or "")
                reviewer = str(payload.get("reviewer") or "owner")
                if not SAFE_SAMPLE.fullmatch(sample_id) or not SAFE_NAME.fullmatch(reviewer):
                    raise ValueError("样本或评审者名称无效")
                payload["updated_at"] = utc_stamp()
                write_json(self.root / "annotations" / reviewer / f"{sample_id}.json", payload)
                self._json({"saved": True, "updated_at": payload["updated_at"]})
                return
            if parsed.path == "/api/score":
                reviewer = str(payload.get("reviewer") or "owner")
                if not SAFE_NAME.fullmatch(reviewer):
                    raise ValueError("评审者名称无效")
                result = score_benchmark(self.root, primary_reviewer=reviewer)
                self._json(
                    {
                        "scored_sample_count": result["scored_sample_count"],
                        "report": str(self.root / "reports" / "report.md"),
                    }
                )
                return
            self._error(HTTPStatus.NOT_FOUND, "路径不存在")
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")


def serve_benchmark(
    root: Path = BENCHMARK_ROOT,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    server = ThreadingHTTPServer((host, port), BenchmarkHandler)
    server.benchmark_root = root  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/"
    print(f"人工评测界面：{url}")
    print("按 Ctrl+C 停止；标注会自动保存到 benchmark/gold 与 benchmark/annotations。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
