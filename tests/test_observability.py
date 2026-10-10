import json

from fusion_video_pipeline.observability import PipelineTelemetry


class FakeObservation:
    def __init__(self, name, **payload):
        self.name = name
        self.payload = payload
        self.children = []
        self.updates = []
        self.ended = False
        self.trace_id = "0123456789abcdef0123456789abcdef"

    def start_observation(self, *, name, **payload):
        child = FakeObservation(name, **payload)
        self.children.append(child)
        return child

    def update(self, **payload):
        self.updates.append(payload)
        return self

    def end(self):
        self.ended = True
        return self


class FakeClient:
    def __init__(self):
        self.root = None
        self.flushed = False

    def start_observation(self, *, name, **payload):
        self.root = FakeObservation(name, **payload)
        return self.root

    def get_trace_url(self, *, trace_id):
        return f"http://localhost:3000/trace/{trace_id}"

    def flush(self):
        self.flushed = True


def test_telemetry_builds_stage_and_generation_tree_without_raw_content(tmp_path):
    client = FakeClient()
    telemetry = PipelineTelemetry(client, tmp_path, capture_content=False)

    telemetry.start_run(
        input_data={"source": "private-subtitle.txt"},
        metadata={"run_id": "run-1"},
    )
    telemetry.start_stage("notes", {"model": "deepseek-chat"})
    generation = telemetry.start_generation(
        stage="主干生成",
        attempt=1,
        model="deepseek-chat",
        messages=[{"role": "user", "content": "private transcript"}],
        model_parameters={"temperature": 0},
    )
    telemetry.complete_generation(
        generation,
        output={"title": "private output"},
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        finish_reason="stop",
    )
    telemetry.complete_stage("notes", {"nodes": 8})
    telemetry.complete_run({"state": "COMPLETE"})

    assert client.root.ended is True
    assert client.flushed is True
    assert client.root.payload["input"]["source"]["characters"] == 20
    assert client.root.payload["input"]["source"] != "private-subtitle.txt"
    notes = client.root.children[0]
    assert notes.name == "notes"
    assert notes.ended is True
    assert notes.children[0].payload["model"] == "deepseek-chat"
    assert notes.children[0].updates[0]["usage_details"] == {
        "input": 10,
        "output": 5,
        "total": 15,
    }
    state = json.loads((tmp_path / "langfuse.json").read_text(encoding="utf-8"))
    assert state["status"] == "complete"
    assert state["trace_id"] == client.root.trace_id


def test_telemetry_can_capture_full_content_for_local_self_hosting(tmp_path):
    client = FakeClient()
    telemetry = PipelineTelemetry(client, tmp_path, capture_content=True)

    telemetry.start_run(
        input_data={"source": "subtitle.txt"},
        metadata={"run_id": "run-2"},
    )

    assert client.root.payload["input"] == {"source": "subtitle.txt"}


def test_full_content_capture_redacts_binary_image_payloads(tmp_path):
    telemetry = PipelineTelemetry(FakeClient(), tmp_path, capture_content=True)

    captured = telemetry._capture(
        {
            "content": [
                {"type": "text", "text": "visible result"},
                {"type": "image", "mimeType": "image/png", "data": "aGVsbG8="},
            ]
        }
    )

    assert captured["content"][0]["text"] == "visible result"
    assert captured["content"][1]["mimeType"] == "image/png"
    assert captured["content"][1]["data"] == {
        "characters": 8,
        "sha256": "333d6b3a3c1f5db6c9bdda5939b136986d170f4649172a68368d54ecb44c2ff2",
        "redacted": "binary image payload",
    }


def test_telemetry_expands_report_agent_turns_and_tools(tmp_path):
    client = FakeClient()
    telemetry = PipelineTelemetry(client, tmp_path, capture_content=True)
    telemetry.start_run(input_data={"source": "subtitle.txt"}, metadata={"run_id": "run-3"})
    telemetry.start_stage("report", {"provider": "deepseek", "model": "deepseek-flash"})

    telemetry.report_agent_event(
        {
            "type": "message_end",
            "message": {"role": "user", "content": "生成报告"},
        }
    )
    telemetry.report_agent_event(
        {
            "type": "request_start",
            "request_id": 1,
            "provider": "deepseek",
            "model": "deepseek-flash",
        }
    )
    telemetry.report_agent_event(
        {
            "type": "request_first_response",
            "request_id": 1,
            "timestamp": 1_700_000_000.5,
            "latency_ms": 500,
        }
    )
    telemetry.report_agent_event(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "toolCall", "id": "call-1", "name": "read"}],
                "provider": "deepseek",
                "model": "deepseek-flash",
                "usage": {
                    "input": 100,
                    "output": 20,
                    "cacheRead": 50,
                    "cacheWrite": 0,
                    "reasoning": 5,
                    "totalTokens": 170,
                },
                "stopReason": "toolUse",
                "rawStopReason": "tool_calls",
            },
        }
    )
    telemetry.report_agent_event(
        {
            "type": "tool_execution_start",
            "toolCallId": "call-1",
            "toolName": "read",
            "args": {"path": "transcript.md"},
        }
    )
    telemetry.report_agent_event(
        {
            "type": "tool_execution_end",
            "toolCallId": "call-1",
            "toolName": "read",
            "result": "subtitle content",
            "isError": False,
        }
    )
    telemetry.report_agent_event(
        {
            "type": "request_start",
            "request_id": 2,
            "provider": "deepseek",
            "model": "deepseek-flash",
        }
    )
    telemetry.report_agent_event(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "完成"}],
                "usage": {"input": 150, "output": 10, "totalTokens": 160},
                "stopReason": "stop",
            },
        }
    )
    telemetry.finish_report_agent()

    report = client.root.children[0]
    assert report.name == "report"
    assert [child.name for child in report.children] == ["阶段 1 · 读取与规划"]
    phase = report.children[0]
    assert [child.name for child in phase.children] == [
        "步骤 01 · 等待模型决策",
        "步骤 02 · 等待模型决策",
    ]
    first_step, second_step = phase.children
    first_generation, tool = first_step.children
    second_generation = second_step.children[0]
    assert first_generation.ended is True
    assert first_generation.updates[-1]["usage_details"] == {
        "input": 100,
        "output": 20,
        "total": 170,
        "cache_read_input_tokens": 50,
        "cache_write_input_tokens": 0,
        "reasoning_tokens": 5,
    }
    assert first_step.updates[0]["name"] == "步骤 01 · 读取完整字幕"
    assert tool.payload["input"] == {"path": "transcript.md"}
    assert tool.updates[-1]["output"] == "subtitle content"
    second_input = second_generation.payload["input"]["new_since_previous_request"]
    assert second_input[-2]["content"][0]["arguments"] == {
        "path": "transcript.md"
    }
    assert second_input[-1] == {
        "role": "tool",
        "name": "read",
        "tool_call_id": "call-1",
        "content": "subtitle content",
    }
    assert second_step.updates[0]["name"] == "步骤 02 · 返回最终报告"
    assert telemetry.report_agent_summary() == {
        "llm_calls": 2,
        "tool_calls": 1,
        "phases": [
            {
                "phase": "读取与规划",
                "llm_calls": 2,
                "tool_calls": 1,
                "input_tokens": 250,
                "output_tokens": 30,
                "cache_read_tokens": 50,
                "reasoning_tokens": 5,
                "elapsed_seconds": 0,
            }
        ],
        "steps": [
            {
                "turn": 1,
                "phase": "读取与规划",
                "activities": ["读取完整字幕"],
                "tools": ["read"],
                "usage": {
                    "input": 100,
                    "output": 20,
                    "cacheRead": 50,
                    "cacheWrite": 0,
                    "reasoning": 5,
                    "totalTokens": 170,
                },
                "elapsed_seconds": None,
            },
            {
                "turn": 2,
                "phase": "读取与规划",
                "activities": ["返回最终报告"],
                "tools": [],
                "usage": {"input": 150, "output": 10, "totalTokens": 160},
                "elapsed_seconds": None,
            },
        ],
    }
