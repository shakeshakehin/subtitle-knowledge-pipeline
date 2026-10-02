from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .pipeline import FusionPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fusion-video",
        description="同一份 canonical 字幕，同时生成 v7 树形笔记和视觉阅读报告并归档到 Obsidian。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="处理 B 站 BV/URL 或本地字幕文本")
    run.add_argument("source", help="BV 号、B 站 URL 或本地字幕路径")
    run.add_argument("--title", help="本地字幕的显示标题；B 站输入可覆盖原标题")
    run.add_argument("--uploader", help="UP 主/作者")
    run.add_argument("--source-url", help="本地字幕对应的原视频 URL")
    run.add_argument("--report-mode", choices=("standard", "brief"), default="standard")
    run.add_argument(
        "--output",
        choices=("tree", "report", "both"),
        default="both",
        help="生成树、详细报告或两者（默认 both）",
    )
    run.add_argument("--force", action="store_true", help="忽略已有字幕缓存")
    run.add_argument("--no-publish", action="store_true", help="不发布到 Obsidian")
    rebuild = subparsers.add_parser("rebuild-tree", help="重生成某次运行的树形笔记，不重跑阅读报告")
    rebuild.add_argument("run_dir", type=Path, help="fusion-video-pipeline/runs 下的运行目录")
    rebuild.add_argument("--no-publish", action="store_true", help="不更新 Obsidian")
    serve = subparsers.add_parser("serve", help="启动本地网页")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument(
        "--public-mode",
        action="store_true",
        help="启用访问认证并锁定可被远程请求修改的服务端设置",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    project_root = Path(__file__).resolve().parents[2]
    settings = Settings.load(project_root)
    if args.command == "run":
        result = FusionPipeline(settings).run(
            args.source,
            title=args.title,
            uploader=args.uploader,
            source_url=args.source_url,
            report_mode=args.report_mode,
            force=args.force,
            publish=not args.no_publish,
            outputs={"tree", "report"} if args.output == "both" else {args.output},
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "rebuild-tree":
        result = FusionPipeline(settings).rebuild_tree(args.run_dir, publish=not args.no_publish)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "serve":
        from .web_server import serve_web

        serve_web(
            settings,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
            public_mode=args.public_mode,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
