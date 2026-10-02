from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = PROJECT_ROOT / "benchmark"


def utc_stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(root: Path = BENCHMARK_ROOT) -> list[dict]:
    path = root / "manifest.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def save_manifest(rows: list[dict], root: Path = BENCHMARK_ROOT) -> Path:
    path = root / "manifest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def resolve_benchmark_path(value: str, root: Path = BENCHMARK_ROOT) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def length_bucket(char_count: int) -> str:
    if char_count < 2500:
        return "short"
    if char_count < 6000:
        return "medium"
    return "long"


def gold_template(sample: dict) -> dict:
    return {
        "sample_id": sample["id"],
        "title": sample["title"],
        "status": "draft",
        "core_points": [],
        "conclusions": [],
        "facts_and_constraints": [],
        "main_branches": [],
        "notes": "",
        "updated_at": None,
    }


def review_template(sample_id: str, reviewer: str) -> dict:
    return {
        "sample_id": sample_id,
        "reviewer": reviewer,
        "status": "draft",
        "claim_support": {"A": {}, "B": {}},
        "key_point_recall": {"A": {}, "B": {}},
        "hierarchy_validity": {"A": {}, "B": {}},
        "pairwise": {
            "coverage": "",
            "hierarchy": "",
            "repetition": "",
            "study_value": "",
        },
        "notes": "",
        "updated_at": None,
    }


def setup_benchmark(root: Path = BENCHMARK_ROOT, *, force: bool = False) -> list[dict]:
    seed_path = root / "seed-sources.json"
    seeds = read_json(seed_path)
    if not isinstance(seeds, list) or len(seeds) < 20:
        raise ValueError(f"seed-sources.json 必须至少包含 20 条样本：{seed_path}")
    for relative in (
        "subtitles",
        "gold",
        "outputs/baseline",
        "outputs/fusion",
        "outputs/blind",
        "annotations/owner",
        "admin",
        "reports",
        "work/baseline",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    for seed in seeds:
        sample_id = str(seed["id"])
        source = Path(seed["subtitle_source"])
        if not source.is_file():
            raise FileNotFoundError(f"{sample_id} 字幕不存在：{source}")
        subtitle = root / "subtitles" / f"{sample_id}.txt"
        if force or not subtitle.exists():
            shutil.copy2(source, subtitle)
        baseline = root / "outputs" / "baseline" / f"{sample_id}.json"
        baseline_source = seed.get("baseline_source")
        if baseline_source and (force or not baseline.exists()):
            candidate = Path(baseline_source)
            if not candidate.is_file():
                raise FileNotFoundError(f"{sample_id} 基线不存在：{candidate}")
            shutil.copy2(candidate, baseline)
        char_count = len(subtitle.read_text(encoding="utf-8-sig"))
        row = {
            "id": sample_id,
            "source_id": seed["source_id"],
            "title": seed["title"],
            "category": seed["category"],
            "length_bucket": length_bucket(char_count),
            "char_count": char_count,
            "subtitle_path": f"subtitles/{sample_id}.txt",
            "subtitle_sha256": file_sha256(subtitle),
            "baseline_output": f"outputs/baseline/{sample_id}.json",
            "baseline_provenance": (
                "historical-v7-frozen" if baseline_source else "generated-v7-prompt-current-model"
            ),
            "fusion_output": f"outputs/fusion/{sample_id}.json",
            "skeleton_output": f"outputs/fusion/{sample_id}.skeleton.json",
            "gold_path": f"gold/{sample_id}.json",
            "baseline_status": "frozen" if baseline.is_file() else "pending",
            "fusion_status": (
                "frozen"
                if (root / "outputs" / "fusion" / f"{sample_id}.json").is_file()
                else "pending"
            ),
        }
        manifest.append(row)
        gold_path = root / row["gold_path"]
        if not gold_path.exists():
            write_json(gold_path, gold_template(row))
        review_path = root / "annotations" / "owner" / f"{sample_id}.json"
        if not review_path.exists():
            write_json(review_path, review_template(sample_id, "owner"))

    save_manifest(manifest, root)
    rebuild_lock(root)
    return manifest


def rebuild_lock(root: Path = BENCHMARK_ROOT) -> dict:
    manifest = load_manifest(root)
    files: dict[str, str] = {}
    for row in manifest:
        for key in ("subtitle_path", "baseline_output", "fusion_output", "skeleton_output"):
            path = resolve_benchmark_path(row[key], root)
            if path.is_file():
                files[path.relative_to(root).as_posix()] = file_sha256(path)
    lock = {
        "format_version": 1,
        "created_at": utc_stamp(),
        "sample_count": len(manifest),
        "files": dict(sorted(files.items())),
    }
    write_json(root / "dataset.lock.json", lock)
    return lock


def verify_lock(root: Path = BENCHMARK_ROOT) -> list[str]:
    lock = read_json(root / "dataset.lock.json", {})
    issues: list[str] = []
    for relative, expected in (lock.get("files") or {}).items():
        path = root / relative
        if not path.is_file():
            issues.append(f"缺少：{relative}")
        elif file_sha256(path) != expected:
            issues.append(f"内容变化：{relative}")
    return issues


def point_text(point: dict) -> str:
    title = str(point.get("title") or point.get("text") or "").strip()
    detail = str(point.get("detail") or "").strip()
    return f"{title}：{detail}" if detail else title


def normalized_tree(data: dict) -> dict:
    sections: list[dict] = []
    nodes: list[dict] = []
    edges: list[dict] = []
    for section_index, section in enumerate(data.get("outline") or [], 1):
        section_id = str(section.get("id") or f"section-{section_index}")
        heading = str(section.get("heading") or f"章节 {section_index}")
        normalized_section = {"id": section_id, "heading": heading, "points": []}
        stack: dict[int, dict] = {}
        known: dict[str, dict] = {}
        for point_index, point in enumerate(section.get("points") or [], 1):
            node_id = str(point.get("id") or f"{section_id}-p{point_index}")
            level = max(1, int(point.get("level", 1)))
            node = {
                "id": node_id,
                "section_id": section_id,
                "section": heading,
                "level": level,
                "text": point_text(point),
                "kind": str(point.get("kind") or "point"),
                "relation_to_parent": str(point.get("relation_to_parent") or ""),
                "source_units": list(point.get("source_units") or []),
            }
            parent_id = point.get("parent_id")
            if parent_id is not None and str(parent_id) in known:
                parent = known[str(parent_id)]
            elif level > 1 and level - 1 in stack:
                parent = stack[level - 1]
            else:
                parent = {"id": section_id, "text": heading, "level": 0}
            node["parent_id"] = parent["id"]
            node["parent_text"] = parent["text"]
            edges.append(
                {
                    "id": f"{parent['id']}->{node_id}",
                    "parent": parent["text"],
                    "child": node["text"],
                    "parent_id": parent["id"],
                    "child_id": node_id,
                }
            )
            normalized_section["points"].append(node)
            nodes.append(node)
            known[node_id] = node
            stack[level] = node
            for stale_level in [value for value in stack if value > level]:
                del stack[stale_level]
        sections.append(normalized_section)
    raw_summaries = data.get("summary") or data.get("key_chain") or []
    summaries = [
        {
            "id": f"summary-{index}",
            "text": str(item.get("text") if isinstance(item, dict) else item),
            "source_units": list(item.get("source_units") or []) if isinstance(item, dict) else [],
        }
        for index, item in enumerate(raw_summaries, 1)
    ]
    return {
        "title": str(data.get("title") or ""),
        "thesis": str(data.get("thesis") or ""),
        "sections": sections,
        "nodes": nodes,
        "edges": edges,
        "summaries": summaries,
    }


def _stable_sample(values: list[dict], count: int, seed: str) -> list[dict]:
    if len(values) <= count:
        return values
    randomizer = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    indexes = sorted(randomizer.sample(range(len(values)), count))
    return [values[index] for index in indexes]


def review_payload(data: dict, sample_id: str, label: str) -> dict:
    tree = normalized_tree(data)
    node_claims = [
        {"id": f"node:{node['id']}", "kind": "node", "text": node["text"]} for node in tree["nodes"]
    ]
    sampled_nodes = _stable_sample(node_claims, 10, f"{sample_id}:{label}:claims")
    thesis_claims = (
        [{"id": "thesis", "kind": "thesis", "text": tree["thesis"]}] if tree["thesis"] else []
    )
    summary_claims = [
        {"id": f"summary:{item['id']}", "kind": "summary", "text": item["text"]}
        for item in tree["summaries"]
    ]
    edges = _stable_sample(tree["edges"], 10, f"{sample_id}:{label}:edges")
    return {
        "title": tree["title"],
        "thesis": tree["thesis"],
        "sections": tree["sections"],
        "claims": thesis_claims + summary_claims + sampled_nodes,
        "edges": edges,
    }


def output_metrics(data: dict) -> dict:
    tree = normalized_tree(data)
    level_counts: dict[int, int] = {}
    duplicate_keys: set[str] = set()
    duplicates = 0
    empty_nodes = 0
    for node in tree["nodes"]:
        level_counts[node["level"]] = level_counts.get(node["level"], 0) + 1
        normalized = "".join(
            character for character in node["text"].casefold() if character.isalnum()
        )
        if not normalized:
            empty_nodes += 1
        elif normalized in duplicate_keys:
            duplicates += 1
        duplicate_keys.add(normalized)
    point_count = len(tree["nodes"])
    return {
        "branch_count": len(tree["sections"]),
        "point_count": point_count,
        "summary_count": len(tree["summaries"]),
        "level_counts": level_counts,
        "maximum_point_level": max(level_counts, default=0),
        "visual_depth": max(level_counts, default=0) + 2 if point_count else 1,
        "parent_child_edges": len(tree["edges"]),
        "empty_node_ratio": empty_nodes / point_count if point_count else 0.0,
        "duplicate_node_ratio": duplicates / point_count if point_count else 0.0,
    }
