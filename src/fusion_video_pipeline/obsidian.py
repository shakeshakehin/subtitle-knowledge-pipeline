from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path


def safe_name(value: str, limit: int = 70) -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n]+', "_", value).strip(" .")
    return (value[:limit] or "未命名视频").rstrip(" .")


def _copy(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def _copy_if_present(source: Path, target: Path) -> Path | None:
    return _copy(source, target) if source.is_file() else None


def _source_id(metadata: dict, run_dir: Path) -> str:
    if metadata.get("bvid"):
        return str(metadata["bvid"])
    match = re.search(r"BV[0-9A-Za-z]+", str(metadata.get("url") or ""), flags=re.I)
    if match:
        return match.group(0)
    source_path = str(metadata.get("source_path") or "")
    if source_path:
        return Path(source_path).stem
    return run_dir.name


def publish_to_obsidian(run_dir: Path, metadata: dict, root: Path) -> dict[str, str]:
    title = safe_name(metadata.get("title") or "未命名视频")
    source_id = _source_id(metadata, run_dir)
    destination = root / date.today().isoformat() / safe_name(f"{source_id}-{title}", 90)
    destination.mkdir(parents=True, exist_ok=True)
    assets = destination / "_素材"
    assets.mkdir(exist_ok=True)
    candidates = {
        "mindmap_svg": (run_dir / "mindmap.svg", destination / f"{title}_導圖.svg"),
        "mindmap_png": (run_dir / "mindmap.png", destination / f"{title}_導圖.png"),
        "tree_html": (run_dir / "tree.html", destination / f"{title}_交互樹.html"),
        "report_html": (run_dir / "report.html", destination / f"{title}_閱讀報告.html"),
        "report_png": (run_dir / "report.png", destination / f"{title}_閱讀報告.png"),
    }
    files = {
        key: copied
        for key, (source, target) in candidates.items()
        if (copied := _copy_if_present(source, target)) is not None
    }
    if "tree_html" in files and "report_html" in files:
        tree_html = files["tree_html"].read_text(encoding="utf-8")
        tree_html = tree_html.replace('src="report.html"', f'src="{files["report_html"].name}"')
        files["tree_html"].write_text(tree_html, encoding="utf-8")
    for name in (
        "canonical-transcript.jsonl",
        "transcript.md",
        "subtitle.txt",
        "skeleton.json",
        "outline.json",
        "usage.json",
        "source.info.json",
        "report-inspection.json",
        "mindmap-inspection.json",
        "run.trace.jsonl",
        "status.json",
    ):
        source = run_dir / name
        if source.is_file():
            _copy(source, assets / name)
    notes_path = run_dir / "notes.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else f"# {title}\n"
    links = []
    if "tree_html" in files:
        links.append(f"- [打开树与详细报告联动页]({files['tree_html'].name})")
    elif "report_html" in files:
        links.append(f"- [打开 HTML 阅读报告]({files['report_html'].name})")
    if "mindmap_svg" in files:
        links.append(f"- [[{files['mindmap_svg'].name}|打开 SVG 导图]]")
    previews = []
    if "mindmap_png" in files:
        previews.append(f"![[{files['mindmap_png'].name}|900]]")
    if "report_png" in files:
        previews.extend(("## 完整閱讀報告", f"![[{files['report_png'].name}|720]]"))
    integration = "\n## 成果入口\n\n" + "\n".join(links)
    if previews:
        integration += "\n\n" + "\n\n".join(previews)
    integration += "\n"
    note_path = destination / f"{title}.md"
    note_path.write_text(notes.rstrip() + integration, encoding="utf-8")
    index = root / "_首頁.md"
    root.mkdir(parents=True, exist_ok=True)
    link = f"- [[{date.today().isoformat()}/{destination.name}/{note_path.stem}|{metadata.get('title', title)}]]"
    existing = index.read_text(encoding="utf-8") if index.is_file() else "# 视频融合成果\n\n"
    if link not in existing:
        index.write_text(existing.rstrip() + "\n" + link + "\n", encoding="utf-8")
    return {
        "directory": str(destination),
        "note": str(note_path),
        **{key: str(value) for key, value in files.items()},
    }
