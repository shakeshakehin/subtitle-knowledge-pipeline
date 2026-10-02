from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceUnit:
    unit_id: str
    text: str
    source: str
    start_ms: int | None = None
    end_ms: int | None = None
    line_start: int | None = None
    line_end: int | None = None


def _new_unit(number: int, text: str, source: str, **fields) -> SourceUnit:
    return SourceUnit(unit_id=f"unit-{number:06d}", text=text.strip(), source=source, **fields)


def units_from_plain_text(text: str, source: str, max_chars: int = 380, max_lines: int = 6) -> list[SourceUnit]:
    numbered = [(index, line.strip()) for index, line in enumerate(text.splitlines(), 1) if line.strip()]
    units: list[SourceUnit] = []
    pending: list[tuple[int, str]] = []
    size = 0
    for line_no, line in numbered:
        added = len(line) + (1 if pending else 0)
        if pending and (len(pending) >= max_lines or size + added > max_chars):
            units.append(_new_unit(len(units) + 1, "\n".join(value for _, value in pending), source,
                                   line_start=pending[0][0], line_end=pending[-1][0]))
            pending, size = [], 0
        pending.append((line_no, line))
        size += added
    if pending:
        units.append(_new_unit(len(units) + 1, "\n".join(value for _, value in pending), source,
                               line_start=pending[0][0], line_end=pending[-1][0]))
    if not units:
        raise ValueError("字幕文件没有可用文本")
    return units


def units_from_bilibili_body(body: list[dict], source: str) -> list[SourceUnit]:
    units: list[SourceUnit] = []
    previous = None
    for item in body:
        text = str(item.get("content", "")).strip()
        if not text or text == previous:
            continue
        previous = text
        units.append(_new_unit(
            len(units) + 1,
            text,
            source,
            start_ms=round(float(item.get("from", 0)) * 1000),
            end_ms=round(float(item.get("to", item.get("from", 0))) * 1000),
        ))
    if not units:
        raise ValueError("B 站字幕内容为空")
    return units


def _stamp(unit: SourceUnit) -> str:
    if unit.start_ms is not None:
        return f"{unit.start_ms / 1000:.3f}–{(unit.end_ms or unit.start_ms) / 1000:.3f}s"
    if unit.line_start is not None:
        return f"lines {unit.line_start}–{unit.line_end}"
    return "no timestamp"


def write_canonical(units: list[SourceUnit], directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    jsonl = directory / "canonical-transcript.jsonl"
    transcript = directory / "transcript.md"
    plain = directory / "subtitle.txt"
    jsonl.write_text("".join(json.dumps(asdict(unit), ensure_ascii=False) + "\n" for unit in units),
                     encoding="utf-8")
    transcript.write_text("\n\n".join(
        f"[{unit.unit_id} | {_stamp(unit)}]\n{unit.text}" for unit in units
    ) + "\n", encoding="utf-8")
    plain.write_text("\n".join(unit.text for unit in units) + "\n", encoding="utf-8")
    return {"jsonl": jsonl, "transcript": transcript, "plain": plain}


def source_fingerprint(units: list[SourceUnit]) -> str:
    material = "\n".join(f"{unit.unit_id}\t{unit.text}" for unit in units).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
