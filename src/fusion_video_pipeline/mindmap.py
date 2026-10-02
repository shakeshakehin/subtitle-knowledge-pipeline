from __future__ import annotations

import html
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import sync_playwright

FONT = "Microsoft YaHei, PingFang SC, sans-serif"
COLORS = ["#e87352", "#2e8b83", "#b47b26", "#6d63a8", "#39749b", "#8c5b68"]
MARGIN_X = 42
MARGIN_Y = 78
H_GAP = 46
V_GAP = 13
PAD_X = 15
PAD_Y = 12
# The wrapping heuristic is intentionally conservative. Browser font fallback can
# render mixed Chinese/Latin labels a few pixels wider than our character model.
TEXT_WIDTH_SAFETY = 20


@dataclass
class TreeNode:
    label: str
    depth: int
    color: str
    kind: str = "point"
    node_id: str = ""
    subtitle: str = ""
    children: list["TreeNode"] = field(default_factory=list)
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    subtree_height: float = 0
    lines: list[str] = field(default_factory=list)
    subtitle_lines: list[str] = field(default_factory=list)


def _char_width(character: str, font_size: float) -> float:
    return font_size if unicodedata.east_asian_width(character) in "WF" else font_size * 0.56


def _text_width(text: str, font_size: float) -> float:
    return sum(_char_width(character, font_size) for character in text)


def _wrap_px(text: str, font_size: float, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    current_width = 0.0
    for character in str(text):
        width = _char_width(character, font_size)
        if current and current_width + width > max_width:
            lines.append(current)
            current, current_width = character, width
        else:
            current += character
            current_width += width
    if current:
        lines.append(current)
    return lines or [""]


def _build_points(section: dict, chapter: TreeNode, color: str) -> None:
    points = section.get("points") or []
    if points and all(point.get("id") for point in points):
        nodes: dict[str, TreeNode] = {}
        for point in points:
            node = TreeNode(
                label=str(point.get("title") or point.get("text") or ""),
                subtitle=str(point.get("detail") or ""),
                depth=chapter.depth + int(point.get("level", 1)),
                color=color,
                node_id=str(point["id"]),
            )
            parent_id = point.get("parent_id")
            (nodes[parent_id].children if parent_id else chapter.children).append(node)
            nodes[node.node_id] = node
        return
    stack: dict[int, TreeNode] = {0: chapter}
    for index, point in enumerate(points, 1):
        level = max(1, min(3, int(point.get("level", 1))))
        while level > 1 and level - 1 not in stack:
            level -= 1
        node = TreeNode(
            label=str(point.get("title") or point.get("text") or ""),
            subtitle=str(point.get("detail") or ""),
            depth=chapter.depth + level,
            color=color,
            node_id=f"legacy-{index}",
        )
        stack[level - 1].children.append(node)
        stack[level] = node
        for stale in [value for value in stack if value > level]:
            del stack[stale]


def _build_tree(data: dict) -> TreeNode:
    root = TreeNode(
        label=str(data.get("title", "视频笔记")),
        subtitle=str(data.get("thesis", "")),
        depth=0,
        color="#17324d",
        kind="root",
        node_id="root",
    )
    for index, section in enumerate(data.get("outline") or []):
        color = COLORS[index % len(COLORS)]
        chapter = TreeNode(
            label=str(section.get("heading", "未命名章节")),
            depth=1,
            color=color,
            kind="chapter",
            node_id=f"section-{index + 1}",
        )
        root.children.append(chapter)
        _build_points(section, chapter, color)
    if data.get("summary"):
        color = "#455a64"
        summary = TreeNode(
            label="总结",
            depth=1,
            color=color,
            kind="summary",
            node_id="summary",
        )
        root.children.append(summary)
        for index, item in enumerate(data["summary"], 1):
            summary.children.append(
                TreeNode(
                    label=str(item.get("text", "")),
                    depth=2,
                    color=color,
                    node_id=f"summary-{index}",
                )
            )
    return root


def _style(node: TreeNode) -> tuple[float, float, float, float]:
    if node.kind == "root":
        return 24, 31, 310, 420
    if node.kind in {"chapter", "summary"}:
        return 18, 25, 185, 360
    return 15, 21, 220, 380


def _measure_node(node: TreeNode) -> None:
    font_size, line_height, min_width, max_width = _style(node)
    inner_max = max_width - 2 * PAD_X - TEXT_WIDTH_SAFETY
    node.lines = _wrap_px(node.label, font_size, inner_max)
    widest = max(_text_width(line, font_size) for line in node.lines)
    node.subtitle_lines = []
    subtitle_height = 0
    if node.subtitle:
        node.subtitle_lines = _wrap_px(node.subtitle, 15, inner_max)
        widest = max(widest, max(_text_width(line, 15) for line in node.subtitle_lines))
        subtitle_height = 10 + len(node.subtitle_lines) * 22
    node.width = min(max_width, max(min_width, widest + 2 * PAD_X + TEXT_WIDTH_SAFETY))
    node.height = 2 * PAD_Y + len(node.lines) * line_height + subtitle_height
    node.height = max(node.height, 62 if node.kind == "point" else 72)
    for child in node.children:
        _measure_node(child)


def _walk(node: TreeNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _assign_columns(root: TreeNode) -> None:
    nodes = list(_walk(root))
    maximum_depth = max(node.depth for node in nodes)
    depth_widths = {
        depth: max(node.width for node in nodes if node.depth == depth)
        for depth in range(maximum_depth + 1)
    }
    positions = {0: MARGIN_X}
    for depth in range(1, maximum_depth + 1):
        positions[depth] = positions[depth - 1] + depth_widths[depth - 1] + H_GAP
    for node in nodes:
        node.x = positions[node.depth]


def _measure_subtree(node: TreeNode) -> float:
    children_height = sum(_measure_subtree(child) for child in node.children)
    if node.children:
        children_height += V_GAP * (len(node.children) - 1)
    node.subtree_height = max(node.height, children_height)
    return node.subtree_height


def _place(node: TreeNode, top: float) -> None:
    node.y = top + node.subtree_height / 2
    if not node.children:
        return
    children_height = sum(child.subtree_height for child in node.children)
    children_height += V_GAP * (len(node.children) - 1)
    cursor = top + (node.subtree_height - children_height) / 2
    for child in node.children:
        _place(child, cursor)
        cursor += child.subtree_height + V_GAP


def _layout(data: dict) -> tuple[TreeNode, list[TreeNode]]:
    root = _build_tree(data)
    _measure_node(root)
    _assign_columns(root)
    _measure_subtree(root)
    _place(root, MARGIN_Y)
    return root, list(_walk(root))


def _node_svg(node: TreeNode) -> list[str]:
    top = node.y - node.height / 2
    font_size, line_height, _, _ = _style(node)
    if node.kind == "root":
        fill, stroke, text_color = "#17324d", "#17324d", "#ffffff"
    elif node.kind in {"chapter", "summary"}:
        fill, stroke, text_color = node.color, node.color, "#ffffff"
    else:
        fill, stroke, text_color = "#ffffff", node.color, "#263944"
    parts = [
        f'<g data-node-id="{html.escape(node.node_id)}">',
        f'<rect x="{node.x:.1f}" y="{top:.1f}" width="{node.width:.1f}" '
        f'height="{node.height:.1f}" rx="12" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{2 if node.kind == "point" else 1.4}"/>',
    ]
    text_y = top + PAD_Y + font_size * 0.92
    for line in node.lines:
        parts.append(
            f'<text x="{node.x + PAD_X:.1f}" y="{text_y:.1f}" font-family="{FONT}" '
            f'font-size="{font_size}" font-weight="600" fill="{text_color}">'
            f'{html.escape(line)}</text>'
        )
        text_y += line_height
    if node.subtitle_lines:
        text_y += 5
        subtitle_color = "#d9e7f2" if node.kind == "root" else "#52636d"
        for line in node.subtitle_lines:
            parts.append(
                f'<text x="{node.x + PAD_X:.1f}" y="{text_y:.1f}" font-family="{FONT}" '
                f'font-size="15" fill="{subtitle_color}">{html.escape(line)}</text>'
            )
            text_y += 22
    parts.append("</g>")
    return parts


def render_svg(data: dict, output: Path) -> Path:
    root, nodes = _layout(data)
    width = max(node.x + node.width for node in nodes) + MARGIN_X
    height = root.subtree_height + MARGIN_Y * 2
    maximum_depth = max(node.depth for node in nodes)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">',
        '<rect width="100%" height="100%" fill="#f7f8f6"/>',
        f'<text x="{MARGIN_X}" y="38" font-family="{FONT}" font-size="16" fill="#66737c">'
        'FUSION TREE · 像素宽度排版 · 显式父子关系</text>',
    ]
    for parent in nodes:
        for child in parent.children:
            start_x = parent.x + parent.width
            end_x = child.x
            bend = (start_x + end_x) / 2
            svg.append(
                f'<path d="M {start_x:.1f} {parent.y:.1f} C {bend:.1f} {parent.y:.1f}, '
                f'{bend:.1f} {child.y:.1f}, {end_x:.1f} {child.y:.1f}" fill="none" '
                f'stroke="{child.color}" stroke-width="{3 if child.depth == 1 else 1.8}" '
                'opacity=".66"/>'
            )
    for node in nodes:
        svg.extend(_node_svg(node))
    svg.append(
        f'<text x="{width - MARGIN_X:.0f}" y="{height - 22:.0f}" text-anchor="end" '
        f'font-family="{FONT}" font-size="13" fill="#7e898f">'
        f'{html.escape(str(data.get("type", "")))} · 深度 {maximum_depth + 1}</text>'
    )
    svg.append("</svg>")
    output.write_text("\n".join(svg), encoding="utf-8")
    return output


def inspect_svg_layout(svg_path: Path) -> dict:
    """Use the browser's actual font metrics to verify that text stays inside each card."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 900})
            page.goto(svg_path.resolve().as_uri(), wait_until="load")
            result = page.evaluate("""() => {
                const findings = [];
                const groups = [...document.querySelectorAll('g[data-node-id]')];
                for (const group of groups) {
                    const card = group.querySelector('rect');
                    if (!card) continue;
                    const box = card.getBoundingClientRect();
                    for (const text of group.querySelectorAll('text')) {
                        const t = text.getBoundingClientRect();
                        if (t.left < box.left - 1 || t.right > box.right + 1 ||
                            t.top < box.top - 1 || t.bottom > box.bottom + 1) {
                            findings.push({
                                node_id: group.getAttribute('data-node-id'),
                                text: (text.textContent || '').slice(0, 100),
                                card: {left: box.left, right: box.right,
                                       top: box.top, bottom: box.bottom},
                                text_box: {left: t.left, right: t.right,
                                           top: t.top, bottom: t.bottom},
                            });
                        }
                    }
                }
                return {status: 'checked', node_count: groups.length, findings};
            }""")
        finally:
            browser.close()
    return result


def render_png(svg_path: Path, output: Path) -> Path:
    header = svg_path.read_text(encoding="utf-8")[:500]
    match = re.search(r'width="(\d+)" height="(\d+)"', header)
    width, height = (int(match.group(1)), int(match.group(2))) if match else (1580, 2000)
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if chrome.is_file():
        with tempfile.TemporaryDirectory(prefix="fusion-mindmap-") as profile:
            completed = subprocess.run(
                [
                    str(chrome),
                    "--headless=new",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--force-device-scale-factor=1.25",
                    f"--user-data-dir={profile}",
                    f"--window-size={width},{height}",
                    f"--screenshot={output.resolve()}",
                    svg_path.resolve().as_uri(),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        if completed.returncode == 0 and output.is_file():
            return output
        raise RuntimeError(f"Chrome 导图截图失败：{completed.stderr[-500:]}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": width, "height": min(height, 900)}, device_scale_factor=1
            )
            page.set_content(svg_path.read_text(encoding="utf-8"), wait_until="load")
            page.locator("svg").screenshot(
                path=str(output), animations="disabled", timeout=60_000
            )
        finally:
            browser.close()
    return output
