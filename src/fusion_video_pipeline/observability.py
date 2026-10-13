from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CLIENTS: dict[tuple[str, str, str, str], Any] = {}


def _text_fingerprint(value: str) -> dict[str, Any]:
    return {
        "characters": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


_REPORT_PHASES = {
    "prepare": (1, "读取与规划"),
    "assets": (2, "数据与图形准备"),
    "draft": (3, "报告初稿"),
    "verify": (4, "检查与修订"),
    "finalize": (5, "完成交付"),
}


def _report_tool_semantics(tool_name: str, args: Any) -> tuple[str, str]:
    """Assign a readable purpose to one observable Pi tool action."""
    payload = args if isinstance(args, dict) else {}
    path = str(payload.get("path") or "")
    filename = Path(path.replace("\\", "/")).name
    command = str(payload.get("command") or "")
    lowered_path = path.casefold()
    lowered_command = command.casefold()

    if tool_name == "read":
        if filename == "SKILL.md":
            return "prepare", "读取生成规范"
        if filename == "input.json":
            return "prepare", "读取视频元数据"
        if lowered_path.startswith("modes/"):
            return "prepare", "读取报告模式"
        if lowered_path.startswith("assets/"):
            return "prepare", "读取页面模板"
        if filename == "transcript.md":
            return "prepare", "读取完整字幕"
        if filename in {"notes.md", "skeleton.json", "status.json", "mindmap-inspection.json"}:
            return "prepare", "读取已有管道产物"
        if lowered_path.startswith("references/"):
            return "prepare", "读取写作参考"
        if Path(filename).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}:
            return "verify", f"检查截图 {filename}"
        if Path(filename).suffix.casefold() == ".svg":
            return "assets", f"检查图形 {filename}"
        return "prepare", f"读取 {filename or '文件'}"

    if tool_name == "write":
        if filename == "_body.html":
            return "draft", "生成报告正文初稿"
        if filename == "_sources.html":
            return "draft", "生成来源说明"
        return "draft", f"写入 {filename or '报告文件'}"

    if tool_name == "edit":
        return "verify", f"修订 {filename or '报告内容'}"

    if tool_name == "bash":
        if any(
            term in lowered_command
            for term in (
                "ls -la assets",
                "ls -la;",
                "ls references",
                "cat status.json",
                "cat mindmap-inspection.json",
            )
        ):
            return "prepare", "盘点报告输入文件"
        if any(term in lowered_command for term in ("which python", "node -v", "browsers")):
            return "prepare", "检查报告运行环境"
        if "transcript.md" in lowered_command and any(
            term in lowered_command for term in ("timestamp", "unit-", "fmt(", "secs=")
        ):
            return "assets", "计算章节时间范围"
        if any(term in lowered_command for term in ("_cycle.svg", "rect_edge_point", "cycle-node")):
            return "assets", "生成报告图形"
        if any(term in lowered_command for term in ("_assemble.py", "report-template", "tpl.replace")):
            return "draft", "组装报告 HTML"
        if any(
            term in lowered_command
            for term in ("playwright", "screenshot", "shot-", "_shot", "_crop", "viewport")
        ):
            return "verify", "渲染并检查页面"
        if "report.html" in lowered_command and "rm -f" in lowered_command:
            return "finalize", "清理并确认最终产物"
        if any(
            term in lowered_command
            for term in (
                "banned",
                "placeholder",
                "overflow",
                "external refs",
                "section-time",
                "grep -",
            )
        ):
            return "verify", "执行内容与版面检查"
        return "verify", "执行报告辅助脚本"

    return "verify", f"执行 {tool_name or '工具'}"


def _report_phase_after_tool(tool_name: str, args: Any, activity_phase: str) -> str:
    """Select the next model turn's phase after a completed kind of work."""
    payload = args if isinstance(args, dict) else {}
    path = str(payload.get("path") or "").casefold()
    command = str(payload.get("command") or "").casefold()
    if tool_name == "read" and Path(path).suffix == ".svg":
        return "draft"
    if tool_name == "bash" and any(
        term in command for term in ("_assemble.py", "report-template", "tpl.replace")
    ):
        return "verify"
    return activity_phase


def _compact_report_value(value: Any, *, text_limit: int = 2000) -> Any:
    """Keep report telemetry readable while the full JSONL remains local."""
    if isinstance(value, str):
        if len(value) <= text_limit:
            return value
        return {
            **_text_fingerprint(value),
            "preview": value[:text_limit],
            "truncated": True,
        }
    if isinstance(value, list):
        return [_compact_report_value(item, text_limit=text_limit) for item in value]
    if isinstance(value, dict):
        compacted = {
            key: _compact_report_value(item, text_limit=text_limit)
            for key, item in value.items()
        }
        if value.get("type") == "image" and isinstance(value.get("data"), str):
            compacted["data"] = {
                **_text_fingerprint(value["data"]),
                "redacted": "binary image payload",
            }
        return compacted
    return value


class PipelineTelemetry:
    """Optional Langfuse mirror for the local JSONL run trace.

    Observability must never become a new failure mode for the video pipeline.
    All SDK operations are best-effort and the local run artifacts remain the
    source of truth.
    """

    def __init__(self, client: Any | None, run_dir: Path, *, capture_content: bool = False):
        self.client = client
        self.run_dir = run_dir
        self.capture_content = capture_content
        self.root: Any | None = None
        self.stages: dict[str, Any] = {}
        self._state_path = run_dir / "langfuse.json"
        self._state: dict[str, Any] = {
            "enabled": client is not None,
            "capture_content": capture_content,
        }
        self._report_messages: list[dict[str, Any]] = []
        self._report_pending_messages: list[dict[str, Any]] = []
        self._report_request_index = 0
        self._report_tool_index = 0
        self._report_active_step: dict[str, Any] | None = None
        self._report_tools: dict[str, dict[str, Any]] = {}
        self._report_phase: dict[str, Any] | None = None
        self._report_next_phase = "prepare"
        self._report_steps: list[dict[str, Any]] = []
        self._report_summary: dict[str, Any] = {}

    @classmethod
    def from_settings(cls, settings: Any, run_dir: Path) -> "PipelineTelemetry":
        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            return cls(None, run_dir)
        try:
            from langfuse import Langfuse

            cache_key = (
                settings.langfuse_public_key,
                settings.langfuse_secret_key,
                settings.langfuse_base_url,
                settings.langfuse_environment,
            )
            client = _CLIENTS.get(cache_key)
            if client is None:
                client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    base_url=settings.langfuse_base_url,
                    environment=settings.langfuse_environment,
                    release="10.0.0",
                    timeout=10,
                )
                _CLIENTS[cache_key] = client
            return cls(
                client,
                run_dir,
                capture_content=settings.langfuse_capture_content,
            )
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            telemetry = cls(None, run_dir)
            telemetry._record_error(exc)
            return telemetry

    def _write_state(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            # A secondary observability artifact must not break the main run.
            pass

    def _record_error(self, exc: Exception) -> None:
        self._state["last_error_type"] = type(exc).__name__
        self._write_state()

    def _capture(self, value: Any) -> Any:
        if self.capture_content:
            if isinstance(value, list):
                return [self._capture(item) for item in value]
            if isinstance(value, dict):
                captured = {key: self._capture(item) for key, item in value.items()}
                if value.get("type") == "image" and isinstance(value.get("data"), str):
                    captured["data"] = {
                        **_text_fingerprint(value["data"]),
                        "redacted": "binary image payload",
                    }
                return captured
            return value
        if isinstance(value, str):
            return _text_fingerprint(value)
        if isinstance(value, list):
            return {
                "items": len(value),
                "sha256": hashlib.sha256(
                    json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            }
        if isinstance(value, dict):
            return {key: self._capture(item) for key, item in value.items()}
        return value

    def start_run(self, *, input_data: dict[str, Any], metadata: dict[str, Any]) -> None:
        if self.client is None:
            return
        try:
            self.root = self.client.start_observation(
                name="subtitle-video-pipeline",
                as_type="chain",
                input=self._capture(input_data),
                metadata=metadata,
                version="axis-first-v2",
            )
            self._state.update(
                {
                    "trace_id": self.root.trace_id,
                    "trace_url": self.client.get_trace_url(trace_id=self.root.trace_id),
                }
            )
            self._write_state()
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)
    def start_stage(self, stage: str, detail: dict[str, Any]) -> None:
        if self.root is None or stage in self.stages:
            return
        try:
            stage_types = {
                "notes": "chain",
                "report": "agent",
                "render_tree": "tool",
                "link": "tool",
                "obsidian": "tool",
                "tree-rebuild": "chain",
            }
            self.stages[stage] = self.root.start_observation(
                name=stage,
                as_type=stage_types.get(stage, "span"),
                input=self._capture(detail),
            )
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)

    def stage_event(self, stage: str, event: str, detail: dict[str, Any]) -> None:
        observation = self.stages.get(stage)
        if observation is None:
            return
        try:
            observation.update(metadata={"last_event": event, **self._capture(detail)})
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)

    def complete_stage(self, stage: str, detail: dict[str, Any]) -> None:
        observation = self.stages.pop(stage, None)
        if observation is None:
            return
        try:
            observation.update(output=self._capture(detail))
            observation.end()
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)

    def start_generation(
        self,
        *,
        stage: str,
        attempt: int,
        model: str,
        messages: list[dict[str, str]],
        model_parameters: dict[str, Any],
    ) -> Any | None:
        parent = (
            self.stages.get("notes")
            or self.stages.get("tree-rebuild")
            or self.stages.get("report")
            or self.root
        )
        if parent is None:
            return None
        try:
            return parent.start_observation(
                name=f"{stage}-attempt-{attempt}",
                as_type="generation",
                model=model,
                input=self._capture(messages),
                model_parameters=model_parameters,
                metadata={"stage": stage, "attempt": attempt},
            )
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)
            return None

    def complete_generation(
        self,
        observation: Any | None,
        *,
        output: Any,
        usage: dict[str, Any],
        finish_reason: str | None,
    ) -> None:
        if observation is None:
            return
        usage_details = {
            key: value
            for key, value in {
                "input": usage.get("prompt_tokens"),
                "output": usage.get("completion_tokens"),
                "total": usage.get("total_tokens"),
                "cache_read_input_tokens": usage.get("prompt_cache_hit_tokens"),
                "cache_write_input_tokens": usage.get("prompt_cache_miss_tokens"),
            }.items()
            if isinstance(value, int)
        }
        try:
            observation.update(
                output=self._capture(output),
                usage_details=usage_details,
                metadata={"finish_reason": finish_reason, "provider_usage": usage},
            )
            observation.end()
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)

    def fail_generation(
        self,
        observation: Any | None,
        *,
        error: Exception | str,
        output: Any = None,
        usage: dict[str, Any] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        if observation is None:
            return
        try:
            observation.update(
                output=self._capture(output),
                level="ERROR",
                status_message=str(error),
                metadata={
                    "finish_reason": finish_reason,
                    "provider_usage": usage or {},
                },
            )
            observation.end()
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)

    def can_trace_report_agent(self) -> bool:
        """Return whether live Pi events can be attached to the report stage."""
        return self.client is not None and (self.stages.get("report") or self.root) is not None

    @staticmethod
    def _report_usage(usage: Any) -> dict[str, int]:
        if not isinstance(usage, dict):
            return {}
        return {
            key: value
            for key, value in {
                "input": usage.get("input"),
                "output": usage.get("output"),
                "total": usage.get("totalTokens"),
                "cache_read_input_tokens": usage.get("cacheRead"),
                "cache_write_input_tokens": usage.get("cacheWrite"),
                "reasoning_tokens": usage.get("reasoning"),
            }.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }

    def _capture_report(self, value: Any) -> Any:
        return self._capture(_compact_report_value(value))

    @staticmethod
    def _phase_rank(key: str) -> int:
        return _REPORT_PHASES.get(key, _REPORT_PHASES["verify"])[0]

    def _phase_summary(self, phase_key: str) -> dict[str, Any]:
        phase_steps = [step for step in self._report_steps if step["phase"] == phase_key]
        return {
            "phase": _REPORT_PHASES[phase_key][1],
            "llm_calls": len(phase_steps),
            "tool_calls": sum(len(step["tools"]) for step in phase_steps),
            "input_tokens": sum(step["usage"].get("input", 0) for step in phase_steps),
            "output_tokens": sum(step["usage"].get("output", 0) for step in phase_steps),
            "cache_read_tokens": sum(
                step["usage"].get("cacheRead", 0) for step in phase_steps
            ),
            "reasoning_tokens": sum(
                step["usage"].get("reasoning", 0) for step in phase_steps
            ),
            "elapsed_seconds": round(
                sum(step.get("elapsed_seconds", 0) for step in phase_steps), 3
            ),
        }

    def _open_report_phase(self, parent: Any, phase_key: str) -> None:
        order, label = _REPORT_PHASES[phase_key]
        self._report_phase = {
            "key": phase_key,
            "observation": parent.start_observation(
                name=f"阶段 {order} · {label}",
                as_type="chain",
                metadata={"phase": phase_key, "phase_label": label, "order": order},
            ),
        }

    def _close_report_phase(self, *, error: Exception | None = None) -> None:
        current = self._report_phase
        if current is None:
            return
        summary = self._phase_summary(current["key"])
        update: dict[str, Any] = {"output": summary}
        if error is not None:
            update.update(level="ERROR", status_message=str(error))
        current["observation"].update(**update)
        current["observation"].end()
        self._report_phase = None

    def _switch_report_phase(self, parent: Any) -> None:
        desired = self._report_next_phase
        current_key = self._report_phase["key"] if self._report_phase else None
        if current_key is None:
            self._open_report_phase(parent, desired)
            return
        if self._phase_rank(desired) <= self._phase_rank(current_key):
            return
        self._close_report_phase()
        self._open_report_phase(parent, desired)

    def _report_input_payload(self) -> dict[str, Any]:
        try:
            context_characters = len(
                json.dumps(self._report_messages, ensure_ascii=False, separators=(",", ":"))
            )
        except (TypeError, ValueError):
            context_characters = None
        return {
            "new_since_previous_request": deepcopy(self._report_pending_messages),
            "context": {
                "message_count": len(self._report_messages),
                "characters": context_characters,
                "display_policy": (
                    "Only incremental messages are shown here. The full compact event stream "
                    "is retained in pi.events.jsonl. Pi's provider system prompt is not recorded."
                ),
            },
        }

    def _update_report_step_label(self) -> None:
        current = self._report_active_step
        if current is None:
            return
        record = current["record"]
        activities = record["activities"]
        purpose = " / ".join(activities[:2]) if activities else "等待模型决策"
        current["observation"].update(
            name=f"步骤 {record['turn']:02d} · {purpose}",
            metadata={
                "turn": record["turn"],
                "phase": record["phase"],
                "phase_label": _REPORT_PHASES[record["phase"]][1],
                "activities": activities,
                "tool_count": len(record["tools"]),
            },
        )

    def _close_report_step(
        self, *, error: Exception | None = None, ended_at: float | None = None
    ) -> None:
        current = self._report_active_step
        if current is None:
            return
        message = str(error) if error is not None else "Pi 事件流在步骤结束前中断"
        generation = current.get("generation")
        if generation is not None:
            generation.update(level="ERROR", status_message=message)
            generation.end()
            current["generation"] = None
        for tool_call_id, tool in list(self._report_tools.items()):
            if tool.get("step") is current:
                tool["observation"].update(level="ERROR", status_message=message)
                tool["observation"].end()
                self._report_tools.pop(tool_call_id, None)
        record = current["record"]
        if isinstance(ended_at, (int, float)) and isinstance(record.get("started_at"), (int, float)):
            record["elapsed_seconds"] = round(max(0, ended_at - record["started_at"]), 3)
        output = {
            "phase": _REPORT_PHASES[record["phase"]][1],
            "activities": record["activities"],
            "tools": record["tools"],
            "usage": record["usage"],
            "elapsed_seconds": record.get("elapsed_seconds"),
        }
        update: dict[str, Any] = {"output": output}
        if error is not None:
            update.update(level="ERROR", status_message=str(error))
        current["observation"].update(**update)
        current["observation"].end()
        self._report_active_step = None

    def _inject_report_tool_args(self, tool_call_id: str, args: Any) -> None:
        for message in reversed(self._report_messages):
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                return
            for part in content:
                if isinstance(part, dict) and part.get("id") == tool_call_id:
                    part["arguments"] = deepcopy(args)
                    return
            return

    def report_agent_event(self, event: dict[str, Any]) -> None:
        """Mirror one compact Pi event into child report observations.

        Pi owns the agent loop and persists ``pi.events.jsonl``.  This method
        reconstructs the conversation visible in that event stream; Pi's
        provider-level system prompt is intentionally not present in the local
        compact trace and is therefore not claimed as captured here.
        """
        if not self.can_trace_report_agent():
            return
        try:
            event_type = event.get("type")
            parent = self.stages.get("report") or self.root
            if parent is None:
                return

            if event_type == "message_end":
                message = event.get("message")
                if not isinstance(message, dict):
                    return
                role = message.get("role")
                if role == "user":
                    stored = deepcopy(message)
                    self._report_messages.append(stored)
                    self._report_pending_messages.append(stored)
                    return
                if role != "assistant":
                    return

                current = self._report_active_step
                if current is not None and current.get("generation") is not None:
                    usage = message.get("usage")
                    tool_names = [
                        str(part.get("name"))
                        for part in message.get("content", [])
                        if isinstance(part, dict)
                        and part.get("type") == "toolCall"
                        and part.get("name")
                    ]
                    stop_reason = message.get("stopReason")
                    update: dict[str, Any] = {
                        "output": self._capture_report(message),
                        "usage_details": self._report_usage(usage),
                        "metadata": {
                            "request_id": current.get("request_id"),
                            "turn": current["record"]["turn"],
                            "stop_reason": stop_reason,
                            "raw_stop_reason": message.get("rawStopReason"),
                            "response_id": message.get("responseId"),
                            "first_response_latency_ms": current.get(
                                "first_response_latency_ms"
                            ),
                            "tool_calls": tool_names,
                            "provider_usage": usage if isinstance(usage, dict) else {},
                        },
                    }
                    if stop_reason in {"error", "aborted"}:
                        update.update(
                            level="ERROR",
                            status_message=str(
                                message.get("errorMessage") or f"stopReason={stop_reason}"
                            ),
                        )
                    current["generation"].update(**update)
                    current["generation"].end()
                    current["generation"] = None
                    current["record"]["usage"] = (
                        deepcopy(usage) if isinstance(usage, dict) else {}
                    )
                    current["record"]["llm_ended_at"] = event.get("_trace_received_at")
                    if stop_reason == "stop" and not tool_names:
                        current["record"]["activities"].append("返回最终报告")
                        self._report_next_phase = "finalize"
                        self._update_report_step_label()
                stored = deepcopy(message)
                self._report_messages.append(stored)
                self._report_pending_messages.append(stored)
                return

            if event_type == "request_start":
                self._close_report_step(ended_at=event.get("timestamp"))
                self._report_request_index += 1
                turn = self._report_request_index
                self._switch_report_phase(parent)
                phase = self._report_phase
                if phase is None:
                    return
                input_payload = self._report_input_payload()
                step = phase["observation"].start_observation(
                    name=f"步骤 {turn:02d} · 等待模型决策",
                    as_type="chain",
                    input=self._capture_report(input_payload),
                    metadata={
                        "turn": turn,
                        "phase": phase["key"],
                        "phase_label": _REPORT_PHASES[phase["key"]][1],
                    },
                )
                generation = step.start_observation(
                    name="LLM 调用",
                    as_type="generation",
                    model=event.get("model"),
                    input=self._capture_report(input_payload),
                    metadata={
                        "request_id": event.get("request_id"),
                        "turn": turn,
                        "provider": event.get("provider"),
                        "input_scope": "incremental reconstructed context",
                    },
                )
                record = {
                    "turn": turn,
                    "phase": phase["key"],
                    "activities": [],
                    "tools": [],
                    "usage": {},
                    "started_at": event.get("timestamp"),
                }
                self._report_steps.append(record)
                self._report_active_step = {
                    "observation": step,
                    "generation": generation,
                    "request_id": event.get("request_id"),
                    "record": record,
                    "first_response_latency_ms": None,
                }
                self._report_pending_messages.clear()
                return

            if event_type == "request_first_response":
                current = self._report_active_step
                if current is None or current.get("request_id") != event.get("request_id"):
                    return
                current["first_response_latency_ms"] = event.get("latency_ms")
                timestamp = event.get("timestamp")
                if isinstance(timestamp, (int, float)) and current.get("generation") is not None:
                    current["generation"].update(
                        completion_start_time=datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    )
                return

            if event_type == "tool_execution_start":
                step = self._report_active_step
                if step is None:
                    return
                tool_call_id = str(event.get("toolCallId") or "")
                self._report_tool_index += 1
                tool_number = self._report_tool_index
                tool_name = str(event.get("toolName") or "tool")
                args = event.get("args")
                phase_key, activity = _report_tool_semantics(tool_name, args)
                next_phase = _report_phase_after_tool(tool_name, args, phase_key)
                if self._phase_rank(next_phase) > self._phase_rank(self._report_next_phase):
                    self._report_next_phase = next_phase
                record = step["record"]
                if activity not in record["activities"]:
                    record["activities"].append(activity)
                record["tools"].append(tool_name)
                self._update_report_step_label()
                self._inject_report_tool_args(tool_call_id, args)
                tool_message = {
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": None,
                }
                self._report_messages.append(tool_message)
                self._report_pending_messages.append(tool_message)
                observation = step["observation"].start_observation(
                    name=f"工具 {tool_number:02d} · {activity}",
                    as_type="tool",
                    input=self._capture_report(args),
                    metadata={
                        "tool_call_id": tool_call_id,
                        "tool": tool_name,
                        "activity": activity,
                        "phase": phase_key,
                    },
                )
                self._report_tools[tool_call_id] = {
                    "observation": observation,
                    "message": tool_message,
                    "name": tool_name,
                    "step": step,
                }
                return

            if event_type == "tool_execution_end":
                tool_call_id = str(event.get("toolCallId") or "")
                current = self._report_tools.pop(tool_call_id, None)
                if current is None:
                    return
                result = event.get("result")
                current["message"]["content"] = deepcopy(result)
                update = {"output": self._capture_report(result)}
                if event.get("isError"):
                    update.update(level="ERROR", status_message="Pi tool execution failed")
                current["observation"].update(**update)
                current["observation"].end()
                return

            if event_type == "agent_settled":
                self._close_report_step(ended_at=event.get("_trace_received_at"))
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)

    def finish_report_agent(self, error: Exception | None = None) -> None:
        """Close partially emitted child observations after the Pi process exits."""
        if self.client is None:
            return
        try:
            self._close_report_step(error=error)
            self._close_report_phase(error=error)
            self._report_summary = self.report_agent_summary()
            report = self.stages.get("report") or self.root
            if report is not None:
                report.update(
                    metadata={
                        "agent_summary": {
                            key: value
                            for key, value in self._report_summary.items()
                            if key != "steps"
                        }
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)

    def report_agent_summary(self) -> dict[str, Any]:
        phases = [
            self._phase_summary(key)
            for key, _ in sorted(_REPORT_PHASES.items(), key=lambda item: item[1][0])
            if any(step["phase"] == key for step in self._report_steps)
        ]
        steps = [
            {
                "turn": step["turn"],
                "phase": _REPORT_PHASES[step["phase"]][1],
                "activities": step["activities"],
                "tools": step["tools"],
                "usage": step["usage"],
                "elapsed_seconds": step.get("elapsed_seconds"),
            }
            for step in self._report_steps
        ]
        return {
            "llm_calls": len(steps),
            "tool_calls": sum(len(step["tools"]) for step in self._report_steps),
            "phases": phases,
            "steps": steps,
        }

    def complete_run(self, detail: dict[str, Any]) -> None:
        if self.root is None:
            return
        try:
            for stage in list(self.stages):
                self.complete_stage(stage, {"state": "complete"})
            self.root.update(output=self._capture(detail))
            self.root.end()
            self.client.flush()
            self._state["status"] = "complete"
            self._write_state()
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)

    def fail_run(self, *, error_type: str, error: str) -> None:
        if self.root is None:
            return
        try:
            for stage, observation in list(self.stages.items()):
                observation.update(
                    level="ERROR",
                    status_message=error,
                    output={"state": "failed", "error_type": error_type},
                )
                observation.end()
                self.stages.pop(stage, None)
            self.root.update(
                level="ERROR",
                status_message=error,
                output={"state": "failed", "error_type": error_type},
            )
            self.root.end()
            self.client.flush()
            self._state["status"] = "failed"
            self._write_state()
        except Exception as exc:  # pragma: no cover - defensive SDK boundary
            self._record_error(exc)


__all__ = ["PipelineTelemetry"]
