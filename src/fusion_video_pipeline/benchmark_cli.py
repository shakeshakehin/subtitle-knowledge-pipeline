from __future__ import annotations

import argparse
import json
from pathlib import Path

from .benchmark_common import BENCHMARK_ROOT, load_manifest, setup_benchmark, verify_lock
from .benchmark_runner import refresh_blind, run_benchmark
from .benchmark_score import score_benchmark
from .benchmark_server import serve_benchmark


def _only(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {item.strip().upper() for item in value.split(",") if item.strip()}


def _status(root: Path) -> dict:
    rows = load_manifest(root)
    return {
        "sample_count": len(rows),
        "baseline_ready": sum(row.get("baseline_status") == "frozen" for row in rows),
        "fusion_ready": sum(row.get("fusion_status") == "frozen" for row in rows),
        "gold_complete": sum(
            json.loads((root / row["gold_path"]).read_text(encoding="utf-8")).get("status")
            == "complete"
            for row in rows
        ),
        "lock_issues": verify_lock(root),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fusion-benchmark",
        description="V7 与 Skeleton First 的固定数据集、人工金标准和盲测工具",
    )
    parser.add_argument("--root", type=Path, default=BENCHMARK_ROOT, help="benchmark 目录")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="复制固定字幕、已有 V7 输出并建立标注模板")
    setup.add_argument("--force", action="store_true", help="重新复制冻结输入和已有基线")

    run = subparsers.add_parser("run", help="生成缺少的基线或融合版输出")
    run.add_argument("version", choices=("baseline", "fusion", "all"))
    run.add_argument("--only", help="只处理指定样本，例如 S001,S002")
    run.add_argument("--limit", type=int, help="最多处理前 N 个样本")
    run.add_argument("--force", action="store_true", help="覆盖已有冻结输出")

    serve = subparsers.add_parser("serve", help="打开人工金标准与 A/B 盲测界面")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    score = subparsers.add_parser("score", help="汇总人工与自动指标")
    score.add_argument("--reviewer", default="owner", help="主评审者名称")

    subparsers.add_parser("blind", help="按固定规则重建 A/B 盲测副本")
    subparsers.add_parser("verify", help="校验冻结数据集哈希")
    subparsers.add_parser("status", help="显示生成与标注进度")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    if args.command == "setup":
        rows = setup_benchmark(root, force=args.force)
        result = {"sample_count": len(rows), "root": str(root)}
    elif args.command == "run":
        result = run_benchmark(
            args.version,
            root=root,
            only=_only(args.only),
            limit=args.limit,
            force=args.force,
        )
    elif args.command == "serve":
        serve_benchmark(root, host=args.host, port=args.port, open_browser=not args.no_open)
        return 0
    elif args.command == "score":
        result = score_benchmark(root, primary_reviewer=args.reviewer)
    elif args.command == "blind":
        result = refresh_blind(root)
    elif args.command == "verify":
        issues = verify_lock(root)
        result = {"ok": not issues, "issues": issues}
    elif args.command == "status":
        result = _status(root)
    else:
        raise AssertionError(args.command)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
