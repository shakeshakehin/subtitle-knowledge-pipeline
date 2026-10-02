from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from .benchmark_common import (
    BENCHMARK_ROOT,
    load_manifest,
    output_metrics,
    read_json,
    resolve_benchmark_path,
    utc_stamp,
    write_json,
)


def _rating_value(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("score")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed in {0.0, 0.5, 1.0} else None


def _average_ratings(values: dict) -> tuple[float | None, int]:
    ratings = [rating for value in values.values() if (rating := _rating_value(value)) is not None]
    return (mean(ratings), len(ratings)) if ratings else (None, 0)


def _reviewers(root: Path) -> list[str]:
    annotations = root / "annotations"
    if not annotations.is_dir():
        return []
    return sorted(path.name for path in annotations.iterdir() if path.is_dir())


def _validate_gold(gold: dict) -> list[str]:
    issues: list[str] = []
    core_count = len(gold.get("core_points") or [])
    conclusion_count = len(gold.get("conclusions") or [])
    if not 8 <= core_count <= 12:
        issues.append(f"核心知识点应为 8–12 条，当前 {core_count}")
    if not 3 <= conclusion_count <= 5:
        issues.append(f"最终结论应为 3–5 条，当前 {conclusion_count}")
    if gold.get("status") != "complete":
        issues.append("金标准尚未锁定")
    return issues


def _cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    categories = sorted({value for pair in pairs for value in pair})
    observed = sum(left == right for left, right in pairs) / len(pairs)
    left_counts = Counter(left for left, _ in pairs)
    right_counts = Counter(right for _, right in pairs)
    expected = sum(
        left_counts[category] / len(pairs) * right_counts[category] / len(pairs)
        for category in categories
    )
    if expected == 1:
        return 1.0
    return (observed - expected) / (1 - expected)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _mean_field(rows: list[dict], field: str) -> float | None:
    values = [row[field] for row in rows if isinstance(row.get(field), (int, float))]
    return mean(values) if values else None


def score_benchmark(root: Path = BENCHMARK_ROOT, *, primary_reviewer: str = "owner") -> dict:
    manifest = load_manifest(root)
    blind = read_json(root / "admin" / "blind-key.json", {}).get("samples") or {}
    reviewers = _reviewers(root)
    semantic_rows: list[dict] = []
    automatic_rows: list[dict] = []
    incomplete: list[dict] = []
    failures: list[dict] = []
    reviewer_payloads: dict[str, dict[str, dict]] = defaultdict(dict)

    for sample in manifest:
        sample_id = sample["id"]
        gold = read_json(resolve_benchmark_path(sample["gold_path"], root), {})
        gold_issues = _validate_gold(gold)
        mapping = blind.get(sample_id)
        for reviewer in reviewers:
            review = read_json(root / "annotations" / reviewer / f"{sample_id}.json", {})
            if review:
                reviewer_payloads[reviewer][sample_id] = review

        review = reviewer_payloads.get(primary_reviewer, {}).get(sample_id, {})
        sample_issues = list(gold_issues)
        if not mapping:
            sample_issues.append("缺少盲测输出")
        if review.get("status") != "complete":
            sample_issues.append(f"{primary_reviewer} 评审未完成")
        if sample_issues:
            incomplete.append({"id": sample_id, "issues": sample_issues})

        # 自动结构与成本指标不依赖人工标注，即使金标准未完成也要输出。
        for version in ("baseline", "fusion"):
            output_path = root / "outputs" / version / f"{sample_id}.json"
            if not output_path.is_file():
                continue
            metrics = output_metrics(read_json(output_path, {}))
            run = read_json(output_path.with_suffix(".run.json"), {})
            usage = run.get("usage") or read_json(output_path.with_suffix(".usage.json"), {})
            automatic_rows.append(
                {
                    "sample_id": sample_id,
                    "title": sample["title"],
                    "category": sample["category"],
                    "length_bucket": sample["length_bucket"],
                    "version": version,
                    "baseline_provenance": (
                        sample.get("baseline_provenance") if version == "baseline" else ""
                    ),
                    **metrics,
                    "elapsed_seconds": run.get("elapsed_seconds"),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                    "attempts": usage.get("attempts"),
                    "model": run.get("model") or usage.get("model"),
                    "strategy": usage.get("strategy") or run.get("version"),
                    "state": run.get("state", "frozen-existing"),
                }
            )

        if not mapping or gold_issues or review.get("status") != "complete":
            continue

        for label in ("A", "B"):
            version = mapping[label]
            claim_score, claim_count = _average_ratings(
                (review.get("claim_support") or {}).get(label, {})
            )
            recall_score, recall_count = _average_ratings(
                (review.get("key_point_recall") or {}).get(label, {})
            )
            hierarchy_score, hierarchy_count = _average_ratings(
                (review.get("hierarchy_validity") or {}).get(label, {})
            )
            semantic_rows.append(
                {
                    "sample_id": sample_id,
                    "title": sample["title"],
                    "category": sample["category"],
                    "length_bucket": sample["length_bucket"],
                    "reviewer": primary_reviewer,
                    "blind_label": label,
                    "version": version,
                    "claim_support": claim_score,
                    "claim_count": claim_count,
                    "key_point_recall": recall_score,
                    "key_point_count": recall_count,
                    "hierarchy_validity": hierarchy_score,
                    "edge_count": hierarchy_count,
                }
            )
            for metric_name in ("claim_support", "key_point_recall", "hierarchy_validity"):
                items = (review.get(metric_name) or {}).get(label, {})
                for item_id, item in items.items():
                    if _rating_value(item) == 0:
                        failures.append(
                            {
                                "sample_id": sample_id,
                                "version": version,
                                "metric": metric_name,
                                "item_id": item_id,
                                "note": item.get("note", "") if isinstance(item, dict) else "",
                            }
                        )

    by_version: dict[str, dict] = {}
    for version in ("baseline", "fusion"):
        rows = [row for row in semantic_rows if row["version"] == version]
        by_version[version] = {
            "sample_count": len(rows),
            "claim_support": mean(
                value for row in rows if (value := row["claim_support"]) is not None
            )
            if any(row["claim_support"] is not None for row in rows)
            else None,
            "key_point_recall": mean(
                value for row in rows if (value := row["key_point_recall"]) is not None
            )
            if any(row["key_point_recall"] is not None for row in rows)
            else None,
            "hierarchy_validity": mean(
                value for row in rows if (value := row["hierarchy_validity"]) is not None
            )
            if any(row["hierarchy_validity"] is not None for row in rows)
            else None,
        }

    pairwise_counts = {
        dimension: Counter() for dimension in ("coverage", "hierarchy", "repetition", "study_value")
    }
    for sample in manifest:
        sample_id = sample["id"]
        mapping = blind.get(sample_id)
        review = reviewer_payloads.get(primary_reviewer, {}).get(sample_id, {})
        if not mapping or review.get("status") != "complete":
            continue
        for dimension, choice in (review.get("pairwise") or {}).items():
            if dimension not in pairwise_counts or choice not in {"A", "B", "tie"}:
                continue
            winner = "tie" if choice == "tie" else mapping[choice]
            pairwise_counts[dimension][winner] += 1

    pairwise: dict[str, dict] = {}
    for dimension, counts in pairwise_counts.items():
        total = sum(counts.values())
        pairwise[dimension] = {
            "fusion_wins": counts["fusion"],
            "baseline_wins": counts["baseline"],
            "ties": counts["tie"],
            "decisions": total,
            "fusion_preference_rate": (
                (counts["fusion"] + 0.5 * counts["tie"]) / total if total else None
            ),
        }

    automatic: dict[str, dict] = {}
    for version in ("baseline", "fusion"):
        rows = [row for row in automatic_rows if row["version"] == version]
        automatic[version] = {
            "sample_count": len(rows),
            "average_branch_count": _mean_field(rows, "branch_count"),
            "average_point_count": _mean_field(rows, "point_count"),
            "average_visual_depth": _mean_field(rows, "visual_depth"),
            "average_empty_node_ratio": _mean_field(rows, "empty_node_ratio"),
            "average_duplicate_node_ratio": _mean_field(rows, "duplicate_node_ratio"),
            "average_elapsed_seconds": _mean_field(rows, "elapsed_seconds"),
            "elapsed_sample_count": sum(row.get("elapsed_seconds") is not None for row in rows),
            "average_total_tokens": _mean_field(rows, "total_tokens"),
            "token_sample_count": sum(row.get("total_tokens") is not None for row in rows),
            "average_attempts": _mean_field(rows, "attempts"),
        }

    agreement: dict[str, dict] = {}
    if primary_reviewer in reviewer_payloads:
        for reviewer in reviewers:
            if reviewer == primary_reviewer:
                continue
            pairs: list[tuple[str, str]] = []
            pairwise_pairs: list[tuple[str, str]] = []
            for sample_id, primary in reviewer_payloads[primary_reviewer].items():
                secondary = reviewer_payloads[reviewer].get(sample_id)
                if not secondary:
                    continue
                for metric in ("claim_support", "key_point_recall", "hierarchy_validity"):
                    for label in ("A", "B"):
                        left = (primary.get(metric) or {}).get(label, {})
                        right = (secondary.get(metric) or {}).get(label, {})
                        for item_id in set(left) & set(right):
                            left_value = _rating_value(left[item_id])
                            right_value = _rating_value(right[item_id])
                            if left_value is not None and right_value is not None:
                                pairs.append((str(left_value), str(right_value)))
                for dimension in pairwise_counts:
                    left = (primary.get("pairwise") or {}).get(dimension)
                    right = (secondary.get("pairwise") or {}).get(dimension)
                    if left in {"A", "B", "tie"} and right in {"A", "B", "tie"}:
                        pairwise_pairs.append((left, right))
            agreement[reviewer] = {
                "rating_pairs": len(pairs),
                "rating_kappa": _cohen_kappa(pairs),
                "pairwise_pairs": len(pairwise_pairs),
                "pairwise_kappa": _cohen_kappa(pairwise_pairs),
            }

    scored_count = max(value["sample_count"] for value in by_version.values())
    if scored_count == 0:
        evaluation_status = "not_started"
    elif scored_count <= 3:
        evaluation_status = "pilot"
    elif scored_count < len(manifest):
        evaluation_status = "partial"
    else:
        evaluation_status = "complete"
    results = {
        "generated_at": utc_stamp(),
        "primary_reviewer": primary_reviewer,
        "manifest_sample_count": len(manifest),
        "baseline_provenance": dict(
            Counter(row.get("baseline_provenance", "unspecified") for row in manifest)
        ),
        "scored_sample_count": scored_count,
        "evaluation_status": evaluation_status,
        "semantic": by_version,
        "automatic": automatic,
        "pairwise": pairwise,
        "agreement": agreement,
        "incomplete": incomplete,
        "failures": failures,
    }
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    write_json(reports / "results.json", results)
    _write_csv(reports / "semantic-scores.csv", semantic_rows)
    _write_csv(reports / "automatic-metrics.csv", automatic_rows)
    cost_fields = (
        "sample_id",
        "title",
        "version",
        "baseline_provenance",
        "elapsed_seconds",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "attempts",
        "model",
        "strategy",
        "state",
    )
    _write_csv(
        reports / "per-video-cost.csv",
        [{field: row.get(field) for field in cost_fields} for row in automatic_rows],
    )
    write_json(reports / "failure-cases.json", failures)

    lines = [
        "# 字幕知识管道评测报告",
        "",
        f"- 固定样本：{len(manifest)}",
        f"- 已完成人工评分：{results['scored_sample_count']}",
        f"- 评测状态：{evaluation_status}（未满 20 份时只能视为阶段性结果）",
        f"- 主评审者：{primary_reviewer}",
        "- 评分方式：人工金标准与盲测；没有使用 LLM Judge",
        "",
        "## 语义指标",
        "",
        "| 版本 | Claim Support | Key-Point Recall | Hierarchy Validity | 样本数 |",
        "|---|---:|---:|---:|---:|",
    ]
    for version, values in by_version.items():
        lines.append(
            f"| {version} | {_fmt(values['claim_support'])} | {_fmt(values['key_point_recall'])} | "
            f"{_fmt(values['hierarchy_validity'])} | {values['sample_count']} |"
        )
    lines.extend(
        [
            "",
            "## A/B 盲测",
            "",
            "| 维度 | Fusion 胜 | Baseline 胜 | 平局 | Fusion 偏好率 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for dimension, values in pairwise.items():
        lines.append(
            f"| {dimension} | {values['fusion_wins']} | {values['baseline_wins']} | "
            f"{values['ties']} | {_fmt(values['fusion_preference_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## 自动结构与成本指标",
            "",
            "| 版本 | 输出数 | 平均分支 | 平均节点 | 平均视觉深度 | 精确重复率 | 平均耗时 | 平均 token |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for version, values in automatic.items():
        elapsed = values["average_elapsed_seconds"]
        tokens = values["average_total_tokens"]
        lines.append(
            f"| {version} | {values['sample_count']} | {values['average_branch_count']:.1f} | "
            f"{values['average_point_count']:.1f} | {values['average_visual_depth']:.1f} | "
            f"{_fmt(values['average_duplicate_node_ratio'])} | "
            f"{'—' if elapsed is None else f'{elapsed:.1f}s'} ({values['elapsed_sample_count']} 份) | "
            f"{'—' if tokens is None else f'{tokens:.0f}'} ({values['token_sample_count']} 份) |"
        )
    lines.extend(
        [
            "",
            "## 逐片生成成本",
            "",
            "| 样本 | V7 耗时 | Fusion 耗时 | V7 token | Fusion token | Fusion 调用/重试 | V7 来源 |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    cost_lookup = {(row["sample_id"], row["version"]): row for row in automatic_rows}
    for sample in manifest:
        baseline = cost_lookup.get((sample["id"], "baseline"), {})
        fusion = cost_lookup.get((sample["id"], "fusion"), {})

        def shown(value: object, suffix: str = "") -> str:
            return "—" if value is None else f"{value}{suffix}"

        lines.append(
            f"| {sample['id']} | {shown(baseline.get('elapsed_seconds'), 's')} | "
            f"{shown(fusion.get('elapsed_seconds'), 's')} | {shown(baseline.get('total_tokens'))} | "
            f"{shown(fusion.get('total_tokens'))} | {shown(fusion.get('attempts'))} | "
            f"{sample.get('baseline_provenance', 'unspecified')} |"
        )
    lines.extend(["", "## 标注进度", ""])
    if incomplete:
        for item in incomplete:
            lines.append(f"- {item['id']}：{'；'.join(item['issues'])}")
    else:
        lines.append("- 全部完成")
    lines.extend(
        [
            "",
            "## 局限",
            "",
            "- 人工评分仍受评审者理解影响；建议由第二位评审者复核 20%–30% 样本。",
            "- 自动结构指标不能替代语义忠实度、覆盖率与父子关系判断。",
            "- 15 份使用历史 V7 冻结输出；S009 和 S017–S020 使用相同 V7 Prompt 和结构在当前 DeepSeek 上补齐。V7 成本平均仅来自后 5 份。",
            "- 所有数字只代表本次冻结数据、Prompt、模型和输出，不外推到所有视频。",
            "",
        ]
    )
    (reports / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return results
