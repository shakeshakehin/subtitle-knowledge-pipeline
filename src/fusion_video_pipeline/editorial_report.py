from __future__ import annotations

import html
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import video_report_agent
from video_report_agent.inspect_report import inspect_report
from video_report_agent.report_content import fill_video_description
from video_report_agent.report_image import render_report_image

from .canonical import SourceUnit
from .efficient_report import (
    _blocks_prompt,
    _outline_hint,
    _require_text,
    _source_ids,
    _time_label,
    build_source_blocks,
)
from .notes import ModelStageError, _request_json

if TYPE_CHECKING:
    from .observability import PipelineTelemetry


COMPONENT_TYPES = {
    "prose",
    "table",
    "comparison",
    "callout",
    "cards",
    "steps",
    "cycle",
    "evidence",
}
COVERAGE_DECISIONS = {"include", "merge", "omit"}
IMPORTANCE = {"core", "supporting", "optional"}
REVIEW_KINDS = {"faithfulness", "coverage", "hierarchy", "density", "layout", "style"}
REVIEW_SEVERITIES = {"high", "medium", "low"}


PLAN_SYSTEM_PROMPT = """你是视频阅读报告的总编辑。先通读全部字幕，再设计一份可直接阅读、层次清晰、
视觉结构有变化的报告计划；此阶段不要写最终正文。只使用字幕信息，不补充外部事实。

只输出合法 JSON，不要 Markdown 围栏：
{
  "schema_version": 1,
  "title": "准确而有辨识度的标题",
  "subtitle": "补充阅读角度，可为空字符串",
  "lead_angle": "开篇怎样帮助读者建立全局理解",
  "thesis": "一句话核心命题",
  "terminology": [{"raw":"字幕写法","normalized":"规范写法","reason":"上下文依据","source_units":["unit-000001"]}],
  "content_ledger": [{
    "id":"k1","importance":"core","statement":"独立成立的知识点",
    "detail_scope":"必须保留的解释、条件、例子或限制","source_units":["unit-000001"]
  }],
  "block_coverage": [{"block_id":"block-001","decision":"include","content_ids":["k1"],"reason":"为何保留、合并或省略"}],
  "sections": [{
    "id":"s1","heading":"章节标题","purpose":"本章回答的问题",
    "content_ids":["k1"],"source_units":["unit-000001"],
    "components":[{"id":"c1","type":"prose","purpose":"这一组件如何组织本章相关内容"}]
  }],
  "closing_strategy":"short_conclusion"
}

编辑规则：
1. content_ledger 是覆盖账本，不是逐句目录；同义重复合并，importance 只能是 core、supporting、optional。
2. 必须逐一处理全部 block_id。decision 只能是 include、merge、omit；include/merge 必须关联 content_ids；
   只有广告、寒暄、口头过渡或无新增信息的重复才可 omit。
3. 每个 content_id 恰好归入一个 section。components 是本章的阅读与视觉结构，不再逐项映射账本；
   每章只设计 1–3 个综合组件，组件总数必须显著少于 content_ledger 数量。prose 可以承载多个段落，
   不要把每个知识点做成一个组件或一张卡片；每章超过 3 个组件会被拒绝。
4. 先决定阅读主线和章节，再选择真正适合内容关系的组件。type 只能是 prose、table、comparison、
   callout、cards、steps、cycle、evidence。默认 prose；只有确有比较关系才用 table/comparison，确有顺序才用
   steps，确有循环才用 cycle，不能为了好看滥用组件。相邻组件尽量不要连续同型。
5. 章节数量和篇幅由内容决定，不凑固定数量。标准模式保留关键解释与必要例子；精简模式压缩 optional，
   但不能删除决定结论成立的条件。
6. source_units 只引用输入存在且直接支持当前内容的 ID。术语只在字幕写法明显需要规范时列出。
7. closing_strategy 只能是 none、keyline、short_conclusion；没有新信息时选 none，不写空洞收尾。"""


DRAFT_SYSTEM_PROMPT = """你是视频阅读报告的资深编辑。用户会提供完整字幕和已锁定的编辑计划。
请一次性完成整篇报告正文：保持计划的 section/component id、顺序、类型和内容归属，不重新规划。
只使用字幕信息，不补充外部知识，不把猜测、个人经验或类比升级为事实。

只输出合法 JSON，不要 Markdown 围栏：
{
  "schema_version":1,
  "title":"与计划一致",
  "subtitle":"与计划一致",
  "lead":{"text":"开篇正文","source_units":["unit-000001"]},
  "sections":[{
    "id":"s1","heading":"与计划一致","source_units":["unit-000001"],
    "components":[
      {"id":"c1","type":"prose","heading":"可为空","paragraphs":[{"text":"正文段落","source_units":["unit-000001"]}],"source_units":["unit-000001"]}
    ]
  }],
  "closing":{"kind":"short_conclusion","heading":"总结","text":"简短结论","items":[],"source_units":["unit-000001"]}
}

各组件字段：
- prose：heading（可空），paragraphs[{text,source_units}]。
- callout：heading，text，source_units。
- table：heading（可空），columns[字符串]，rows[{cells[字符串],source_units}]；列数保持 2–4。
- comparison/cards：heading（可空），items[{title,text,source_units}]。
- steps：heading（可空），items[{title,text,source_units}]，只写有真实顺序的步骤。
- cycle：heading（可空），nodes[{title,text,source_units}]（3–6 个），caption（可空）。几何位置由程序生成。
- evidence：heading（可空），items[{topic,judgment,detail,source_units}]。

写作规则：
1. 一个组件把相关内容综合成读者可理解的段落、比较、步骤或图，不逐条复述 ledger。
2. 保留判断成立的对象、条件、因果强度和限制；省略口头重复、过场和无信息修辞。
3. 直接表达知识，不反复写“视频中提到”。多人观点、预测和个人经历必须保留归属。
4. 组件之间要有节奏变化，但形式必须服从内容。避免整篇都是相同卡片，也避免表格塞入长段文字。
5. closing.kind 与计划 closing_strategy 一致；none 时 heading/text 为空且 items 为空。
6. 这是编辑后的详细报告，不是字幕转写。prose 每段通常 90–220 字，同一 prose 可有 2–4 段；卡片、
   步骤和表格单元使用短句。删除口头语和重复，但保留关键机制、例子与限定。
7. 每段或条目只给 1–6 个最直接、最有代表性的 source_units；章节和组件的聚合来源由程序补齐。"""


REVIEW_SYSTEM_PROMPT = """你是同一份视频报告的终稿编辑。根据完整字幕、锁定计划、已生成正文和
程序提供的版面检查结果做一次严格复核。你不是第二个独立评估模型，也不要重写全文；只判断是否存在
必须修订的问题。不要因为个人文风偏好要求改写，不补充外部事实。

只输出合法 JSON，不要 Markdown 围栏：
{
  "schema_version":1,
  "status":"pass",
  "summary":"终稿判断",
  "issues":[{
    "kind":"coverage","severity":"medium","target_id":"c1",
    "problem":"具体问题","instruction":"可执行的局部修订要求"
  }]
}

检查范围：
- faithfulness：是否把字幕没有说的内容写成事实，是否改变归属、条件或因果强度；
- coverage：是否遗漏计划中的核心内容或关键限定；
- hierarchy：章节与组件是否体现父子/并列/步骤/比较关系；
- density：是否逐条堆砌、重复、过密或过度压缩；
- layout：结合程序结果判断溢出、表格过宽、同型组件连续过多等；
- style：是否存在妨碍阅读的口号、机械转述或不自然表达。

只有确实影响正确性、完整性、结构或阅读效果时才 status=revise。issues 可以为空；target_id 只能使用
lead、closing、section id 或 component id。"""


REVISION_SYSTEM_PROMPT = """你是视频报告的终稿修订编辑。根据完整字幕、锁定计划、当前正文和复核
问题清单，只返回需要替换的局部内容，不重写整篇报告，不扩大修改范围，不补充外部事实。

只输出合法 JSON，不要 Markdown 围栏：
{
  "schema_version":1,
  "lead": null,
  "component_replacements": [{"id":"c1","type":"prose","heading":"标题","paragraphs":[...],"source_units":[...]}],
  "closing": null
}

规则：
1. lead 只在 lead 被点名时返回完整替换对象，否则为 null。
2. component_replacements 只包含被点名或为解决被点名 section 问题而必须修改的完整组件；保持 component id，
   必要时可以更换 type 与对应字段。不要返回未修改组件。
3. closing 只在 closing 被点名时返回完整替换对象，否则为 null。
4. section id/heading/顺序、component id/顺序和内容归属保持不变。"""


def _write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _component_map(plan: dict) -> dict[str, dict]:
    return {
        component["id"]: component
        for section in plan["sections"]
        for component in section["components"]
    }


def normalize_editorial_plan(data: dict) -> dict:
    """Assign mechanical section/component identifiers without changing editorial choices."""
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        return data
    component_number = 1
    for section_number, section in enumerate(data["sections"], 1):
        if not isinstance(section, dict):
            continue
        section["id"] = f"s{section_number}"
        components = section.get("components")
        if not isinstance(components, list):
            continue
        for component in components:
            if isinstance(component, dict):
                component["id"] = f"c{component_number}"
                component_number += 1
    return data


def _editorial_plan_for_writing(plan: dict) -> dict:
    """Remove block audit and repeated source lists after planning."""
    compact = deepcopy(
        {key: value for key, value in plan.items() if key not in {"block_coverage", "lead_angle"}}
    )

    def strip_mechanical(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("source_units", None)
            if "type" in value and "purpose" in value:
                value.pop("content_ids", None)
            for nested in value.values():
                strip_mechanical(nested)
        elif isinstance(value, list):
            for nested in value:
                strip_mechanical(nested)

    strip_mechanical(compact)
    return compact


def normalize_editorial_document(data: dict) -> dict:
    """Fill aggregate source bindings from already cited child elements."""
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        return data
    for section in data["sections"]:
        if not isinstance(section, dict) or not isinstance(section.get("components"), list):
            continue
        section_sources: list[str] = []
        for component in section["components"]:
            if not isinstance(component, dict):
                continue
            nested_sources: list[str] = []
            for key in ("paragraphs", "items", "rows", "nodes"):
                values = component.get(key)
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, dict) and isinstance(value.get("source_units"), list):
                        nested_sources.extend(str(item) for item in value["source_units"] if item)
            if not component.get("source_units") and nested_sources:
                component["source_units"] = list(dict.fromkeys(nested_sources))
            section_sources.extend(
                str(item) for item in component.get("source_units") or [] if item
            )
        if not section.get("source_units") and section_sources:
            section["source_units"] = list(dict.fromkeys(section_sources))
    closing = data.get("closing")
    if isinstance(closing, dict) and not closing.get("source_units"):
        closing_sources = [
            str(source)
            for item in closing.get("items") or []
            if isinstance(item, dict)
            for source in item.get("source_units") or []
            if source
        ]
        if closing_sources:
            closing["source_units"] = list(dict.fromkeys(closing_sources))
    return data


def validate_editorial_plan(data: dict, valid_ids: set[str], block_ids: set[str]) -> None:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("编辑计划 schema_version 必须为 1")
    for field in ("title", "lead_angle", "thesis"):
        _require_text(data.get(field), field)
    _require_text(data.get("subtitle"), "subtitle", allow_empty=True)
    if data.get("closing_strategy") not in {"none", "keyline", "short_conclusion"}:
        raise ValueError("closing_strategy 无效")

    terminology = data.get("terminology")
    if not isinstance(terminology, list):
        raise ValueError("terminology 必须是数组")
    for index, term in enumerate(terminology, 1):
        if not isinstance(term, dict):
            raise ValueError(f"terminology[{index}] 必须是对象")
        for field in ("raw", "normalized", "reason"):
            _require_text(term.get(field), f"terminology[{index}].{field}")
        _source_ids(term.get("source_units"), valid_ids, f"terminology[{index}]")

    ledger = data.get("content_ledger")
    if not isinstance(ledger, list) or not ledger:
        raise ValueError("content_ledger 必须是非空数组")
    ledger_by_id: dict[str, dict] = {}
    for index, item in enumerate(ledger, 1):
        if not isinstance(item, dict):
            raise ValueError(f"content_ledger[{index}] 必须是对象")
        item_id = _require_text(item.get("id"), f"content_ledger[{index}].id")
        if item_id in ledger_by_id:
            raise ValueError(f"content_ledger id 重复：{item_id}")
        if item.get("importance") not in IMPORTANCE:
            raise ValueError(f"content_ledger[{index}] importance 无效")
        _require_text(item.get("statement"), f"content_ledger[{index}].statement")
        _require_text(item.get("detail_scope"), f"content_ledger[{index}].detail_scope")
        _source_ids(item.get("source_units"), valid_ids, f"content_ledger[{index}]")
        ledger_by_id[item_id] = item

    coverage = data.get("block_coverage")
    if not isinstance(coverage, list):
        raise ValueError("block_coverage 必须是数组")
    seen_blocks: set[str] = set()
    coverage_ids: set[str] = set()
    for index, item in enumerate(coverage, 1):
        if not isinstance(item, dict):
            raise ValueError(f"block_coverage[{index}] 必须是对象")
        block_id = str(item.get("block_id") or "")
        if block_id not in block_ids or block_id in seen_blocks:
            raise ValueError(f"block_coverage[{index}] block_id 未知或重复")
        decision = item.get("decision")
        content_ids = item.get("content_ids")
        if decision not in COVERAGE_DECISIONS or not isinstance(content_ids, list):
            raise ValueError(f"block_coverage[{index}] decision/content_ids 无效")
        if len(content_ids) != len(set(content_ids)) or set(content_ids) - set(ledger_by_id):
            raise ValueError(f"block_coverage[{index}] content_ids 未知或重复")
        if decision in {"include", "merge"} and not content_ids:
            raise ValueError(f"block_coverage[{index}] 保留内容必须关联 content_ids")
        if decision == "omit" and content_ids:
            raise ValueError(f"block_coverage[{index}] omit 不应关联 content_ids")
        _require_text(item.get("reason"), f"block_coverage[{index}].reason")
        seen_blocks.add(block_id)
        coverage_ids.update(content_ids)
    if seen_blocks != block_ids:
        raise ValueError(f"block_coverage 未完整处理字幕块：{sorted(block_ids - seen_blocks)}")
    if coverage_ids != set(ledger_by_id):
        raise ValueError("block_coverage 与 content_ledger 不一致")

    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("sections 必须是非空数组")
    seen_sections: set[str] = set()
    seen_components: set[str] = set()
    section_assignments: list[str] = []
    for index, section in enumerate(sections, 1):
        if not isinstance(section, dict):
            raise ValueError(f"sections[{index}] 必须是对象")
        section_id = _require_text(section.get("id"), f"sections[{index}].id")
        if section_id in seen_sections:
            raise ValueError(f"section id 重复：{section_id}")
        seen_sections.add(section_id)
        _require_text(section.get("heading"), f"sections[{index}].heading")
        _require_text(section.get("purpose"), f"sections[{index}].purpose")
        section_content = section.get("content_ids")
        if (
            not isinstance(section_content, list)
            or not section_content
            or len(section_content) != len(set(section_content))
            or set(section_content) - set(ledger_by_id)
        ):
            raise ValueError(f"sections[{index}].content_ids 无效")
        section_assignments.extend(section_content)
        _source_ids(section.get("source_units"), valid_ids, f"sections[{index}]")
        components = section.get("components")
        if not isinstance(components, list) or not components:
            raise ValueError(f"sections[{index}].components 必须是非空数组")
        if len(components) > 3:
            raise ValueError(f"sections[{index}].components 不能超过 3 个综合组件")
        for component_index, component in enumerate(components, 1):
            label = f"sections[{index}].components[{component_index}]"
            if not isinstance(component, dict):
                raise ValueError(f"{label} 必须是对象")
            component_id = _require_text(component.get("id"), f"{label}.id")
            if component_id in seen_components:
                raise ValueError(f"component id 重复：{component_id}")
            seen_components.add(component_id)
            if component.get("type") not in COMPONENT_TYPES:
                raise ValueError(f"{label}.type 无效")
            _require_text(component.get("purpose"), f"{label}.purpose")
    ledger_ids = set(ledger_by_id)
    if (
        len(section_assignments) != len(set(section_assignments))
        or set(section_assignments) != ledger_ids
    ):
        raise ValueError("每个 content_id 必须恰好归入一个 section")


def _validate_component(
    component: dict, planned: dict, valid_ids: set[str], label: str, *, allow_type_change: bool
) -> None:
    if component.get("id") != planned["id"]:
        raise ValueError(f"{label}.id 必须与计划一致")
    kind = component.get("type")
    if kind not in COMPONENT_TYPES or (not allow_type_change and kind != planned["type"]):
        raise ValueError(f"{label}.type 与计划不一致或无效")
    _require_text(component.get("heading"), f"{label}.heading", allow_empty=True)
    _source_ids(component.get("source_units"), valid_ids, label)
    if kind == "prose":
        paragraphs = component.get("paragraphs")
        if not isinstance(paragraphs, list) or not paragraphs:
            raise ValueError(f"{label}.paragraphs 必须是非空数组")
        for index, item in enumerate(paragraphs, 1):
            _validate_text_source(item, valid_ids, f"{label}.paragraphs[{index}]")
    elif kind == "callout":
        _require_text(component.get("heading"), f"{label}.heading")
        _require_text(component.get("text"), f"{label}.text")
    elif kind == "table":
        columns = component.get("columns")
        rows = component.get("rows")
        if not isinstance(columns, list) or not 2 <= len(columns) <= 4:
            raise ValueError(f"{label}.columns 必须有 2–4 列")
        for index, value in enumerate(columns, 1):
            _require_text(value, f"{label}.columns[{index}]")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{label}.rows 必须是非空数组")
        for index, row in enumerate(rows, 1):
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("cells"), list)
                or len(row["cells"]) != len(columns)
            ):
                raise ValueError(f"{label}.rows[{index}].cells 列数无效")
            for cell_index, value in enumerate(row["cells"], 1):
                _require_text(value, f"{label}.rows[{index}].cells[{cell_index}]")
            _source_ids(row.get("source_units"), valid_ids, f"{label}.rows[{index}]")
    elif kind in {"comparison", "cards", "steps"}:
        items = component.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{label}.items 必须是非空数组")
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise ValueError(f"{label}.items[{index}] 必须是对象")
            _require_text(item.get("title"), f"{label}.items[{index}].title")
            _require_text(item.get("text"), f"{label}.items[{index}].text")
            _source_ids(item.get("source_units"), valid_ids, f"{label}.items[{index}]")
    elif kind == "cycle":
        nodes = component.get("nodes")
        if not isinstance(nodes, list) or not 3 <= len(nodes) <= 6:
            raise ValueError(f"{label}.nodes 必须有 3–6 个节点")
        for index, node in enumerate(nodes, 1):
            if not isinstance(node, dict):
                raise ValueError(f"{label}.nodes[{index}] 必须是对象")
            _require_text(node.get("title"), f"{label}.nodes[{index}].title")
            _require_text(node.get("text"), f"{label}.nodes[{index}].text")
            _source_ids(node.get("source_units"), valid_ids, f"{label}.nodes[{index}]")
        _require_text(component.get("caption"), f"{label}.caption", allow_empty=True)
    elif kind == "evidence":
        items = component.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{label}.items 必须是非空数组")
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise ValueError(f"{label}.items[{index}] 必须是对象")
            for field in ("topic", "judgment", "detail"):
                _require_text(item.get(field), f"{label}.items[{index}].{field}")
            _source_ids(item.get("source_units"), valid_ids, f"{label}.items[{index}]")


def _validate_text_source(item: Any, valid_ids: set[str], label: str) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"{label} 必须是对象")
    _require_text(item.get("text"), f"{label}.text")
    _source_ids(item.get("source_units"), valid_ids, label)


def validate_editorial_document(
    data: dict, plan: dict, valid_ids: set[str], *, allow_type_change: bool = False
) -> None:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("编辑正文 schema_version 必须为 1")
    if _require_text(data.get("title"), "title") != str(plan["title"]).strip():
        raise ValueError("正文 title 必须与计划一致")
    if (
        _require_text(data.get("subtitle"), "subtitle", allow_empty=True)
        != str(plan["subtitle"]).strip()
    ):
        raise ValueError("正文 subtitle 必须与计划一致")
    lead = data.get("lead")
    _validate_text_source(lead, valid_ids, "lead")
    sections = data.get("sections")
    planned_sections = plan["sections"]
    if not isinstance(sections, list) or len(sections) != len(planned_sections):
        raise ValueError("正文 sections 必须完整对应计划")
    for index, (section, planned) in enumerate(zip(sections, planned_sections, strict=True), 1):
        label = f"sections[{index}]"
        if (
            not isinstance(section, dict)
            or section.get("id") != planned["id"]
            or section.get("heading") != planned["heading"]
        ):
            raise ValueError(f"{label} id/heading 必须与计划一致")
        _source_ids(section.get("source_units"), valid_ids, label)
        components = section.get("components")
        planned_components = planned["components"]
        if not isinstance(components, list) or len(components) != len(planned_components):
            raise ValueError(f"{label}.components 必须完整对应计划")
        for component_index, (component, planned_component) in enumerate(
            zip(components, planned_components, strict=True), 1
        ):
            if not isinstance(component, dict):
                raise ValueError(f"{label}.components[{component_index}] 必须是对象")
            _validate_component(
                component,
                planned_component,
                valid_ids,
                f"{label}.components[{component_index}]",
                allow_type_change=allow_type_change,
            )
    closing = data.get("closing")
    if not isinstance(closing, dict):
        raise ValueError("closing 必须是对象")
    expected = plan["closing_strategy"]
    if closing.get("kind") != expected:
        raise ValueError("closing.kind 必须与计划一致")
    heading = _require_text(closing.get("heading"), "closing.heading", allow_empty=True)
    text = _require_text(closing.get("text"), "closing.text", allow_empty=True)
    items = closing.get("items")
    if not isinstance(items, list):
        raise ValueError("closing.items 必须是数组")
    if expected == "none":
        if heading or text or items or closing.get("source_units") not in ([], None):
            raise ValueError("none closing 必须为空")
    else:
        if not heading or (not text and not items):
            raise ValueError("非空 closing 需要 heading 和正文")
        _source_ids(closing.get("source_units"), valid_ids, "closing")
        for index, item in enumerate(items, 1):
            _validate_text_source(item, valid_ids, f"closing.items[{index}]")


def validate_editorial_review(data: dict, target_ids: set[str]) -> None:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("终稿复核 schema_version 必须为 1")
    if data.get("status") not in {"pass", "revise"}:
        raise ValueError("review.status 无效")
    _require_text(data.get("summary"), "review.summary")
    issues = data.get("issues")
    if not isinstance(issues, list):
        raise ValueError("review.issues 必须是数组")
    for index, issue in enumerate(issues, 1):
        if not isinstance(issue, dict):
            raise ValueError(f"review.issues[{index}] 必须是对象")
        if issue.get("kind") not in REVIEW_KINDS or issue.get("severity") not in REVIEW_SEVERITIES:
            raise ValueError(f"review.issues[{index}] kind/severity 无效")
        if issue.get("target_id") not in target_ids:
            raise ValueError(f"review.issues[{index}] target_id 无效")
        _require_text(issue.get("problem"), f"review.issues[{index}].problem")
        _require_text(issue.get("instruction"), f"review.issues[{index}].instruction")
    if data["status"] == "pass" and issues:
        raise ValueError("pass 时 review.issues 必须为空")
    if data["status"] == "revise" and not issues:
        raise ValueError("revise 时 review.issues 不能为空")


def apply_editorial_revision(patch: dict, document: dict, plan: dict, valid_ids: set[str]) -> dict:
    if not isinstance(patch, dict) or patch.get("schema_version") != 1:
        raise ValueError("局部修订 schema_version 必须为 1")
    allowed = {"schema_version", "lead", "component_replacements", "closing"}
    if set(patch) - allowed:
        raise ValueError("局部修订包含未知字段")
    replacements = patch.get("component_replacements")
    if not isinstance(replacements, list):
        raise ValueError("component_replacements 必须是数组")
    revised = deepcopy(document)
    changed = False
    if patch.get("lead") is not None:
        _validate_text_source(patch["lead"], valid_ids, "revision.lead")
        revised["lead"] = patch["lead"]
        changed = True
    planned_components = _component_map(plan)
    document_components = {
        component["id"]: (section, index)
        for section in revised["sections"]
        for index, component in enumerate(section["components"])
    }
    seen: set[str] = set()
    for index, component in enumerate(replacements, 1):
        if not isinstance(component, dict):
            raise ValueError(f"component_replacements[{index}] 必须是对象")
        component_id = str(component.get("id") or "")
        if component_id in seen or component_id not in document_components:
            raise ValueError(f"component_replacements[{index}].id 未知或重复")
        _validate_component(
            component,
            planned_components[component_id],
            valid_ids,
            f"component_replacements[{index}]",
            allow_type_change=True,
        )
        section, component_index = document_components[component_id]
        section["components"][component_index] = component
        seen.add(component_id)
        changed = True
    if patch.get("closing") is not None:
        revised["closing"] = patch["closing"]
        changed = True
    if not changed:
        raise ValueError("局部修订没有返回任何修改")
    validate_editorial_document(revised, plan, valid_ids, allow_type_change=True)
    return revised


def _template_path(report_mode: str) -> Path:
    assets = (
        Path(video_report_agent.__file__).resolve().parent / "skills" / "video-report" / "assets"
    )
    return assets / (
        "brief-report-template.html" if report_mode == "brief" else "report-template.html"
    )


def _refs(value: dict) -> list[str]:
    return [str(item) for item in value.get("source_units") or [] if item]


def _attrs(value: dict) -> str:
    return html.escape(" ".join(_refs(value)), quote=True)


def _heading(value: str) -> str:
    return f"<h3>{html.escape(value)}</h3>" if value else ""


def _cycle_svg(component: dict, *, brief: bool) -> str:
    nodes = component["nodes"]
    count = len(nodes)
    width, height = 720, 330
    center_x, center_y = width / 2, height / 2
    radius_x, radius_y = 245, 110
    positions = [
        (
            center_x + radius_x * math.sin(2 * math.pi * index / count),
            center_y - radius_y * math.cos(2 * math.pi * index / count),
        )
        for index in range(count)
    ]
    marker = f"editorial-arrow-{html.escape(component['id'], quote=True)}"
    edges: list[str] = []
    for index, (x1, y1) in enumerate(positions):
        x2, y2 = positions[(index + 1) % count]
        dx, dy = x2 - x1, y2 - y1
        distance = math.hypot(dx, dy) or 1
        inset = 64
        edges.append(
            f'<path d="M {x1 + dx / distance * inset:.1f} {y1 + dy / distance * inset:.1f} '
            f'L {x2 - dx / distance * inset:.1f} {y2 - dy / distance * inset:.1f}"/>'
        )
    node_markup: list[str] = []
    for node, (x, y) in zip(nodes, positions, strict=True):
        title = html.escape(node["title"][:18])
        note = html.escape(node["text"][:30])
        node_markup.append(
            f'<g data-source-units="{_attrs(node)}"><rect class="cycle-node" x="{x - 82:.1f}" y="{y - 34:.1f}" width="164" height="68" rx="10"/>'
            f'<text class="cycle-title" text-anchor="middle" x="{x:.1f}" y="{y - 4:.1f}">{title}</text>'
            f'<text class="cycle-note" text-anchor="middle" x="{x:.1f}" y="{y + 18:.1f}">{note}</text></g>'
        )
    figure_class = "brief-map" if brief else "cycle-map"
    edge_class = "" if brief else ' class="cycle-edge"'
    node_style = (
        ""
        if not brief
        else "<style>.cycle-node{fill:#edf7fc;stroke:#b9d6e5}.cycle-title{font-weight:700}.cycle-note{font-size:14px;fill:#40577a}path{stroke:#6997ad;stroke-width:2;fill:none}</style>"
    )
    caption = html.escape(component.get("caption") or "")
    return (
        f'<figure class="{figure_class}" data-source-units="{_attrs(component)}"><svg viewBox="0 0 {width} {height}" role="img">{node_style}'
        f'<defs><marker id="{marker}" viewBox="0 0 8 8" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M1 1 L7 4 L1 7"{edge_class}/></marker></defs>'
        f'<g{edge_class} marker-end="url(#{marker})">{"".join(edges)}</g>{"".join(node_markup)}</svg>'
        f"{f'<figcaption>{caption}</figcaption>' if caption else ''}</figure>"
    )


def _render_component(component: dict, *, brief: bool) -> str:
    kind = component["type"]
    heading = _heading(component.get("heading") or "")
    attrs = _attrs(component)
    if kind == "prose":
        paragraphs = "".join(
            f'<p data-source-units="{_attrs(item)}">{html.escape(item["text"])}</p>'
            for item in component["paragraphs"]
        )
        return (
            f'<div data-component="prose" data-source-units="{attrs}">{heading}{paragraphs}</div>'
        )
    if kind == "callout":
        css = "brief-feature" if brief else "callout"
        return f'<aside class="{css}" data-source-units="{attrs}">{heading}<p>{html.escape(component["text"])}</p></aside>'
    if kind == "table":
        css = "brief-table" if brief else "table-details"
        headers = "".join(f"<th>{html.escape(value)}</th>" for value in component["columns"])
        rows = "".join(
            f'<tr data-source-units="{_attrs(row)}">'
            + "".join(f"<td>{html.escape(value)}</td>" for value in row["cells"])
            + "</tr>"
            for row in component["rows"]
        )
        table = f'<table class="{css}" data-source-units="{attrs}"><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table>'
        return f'{heading}<div class="brief-table-wrap">{table}</div>' if brief else heading + table
    if kind in {"comparison", "cards"}:
        wrapper = "brief-pair" if brief else ("comparison" if kind == "comparison" else "cards")
        card = "brief-card" if brief else "card"
        items = "".join(
            f'<article class="{card}" data-source-units="{_attrs(item)}"><h3>{html.escape(item["title"])}</h3><p>{html.escape(item["text"])}</p></article>'
            for item in component["items"]
        )
        return f'{heading}<div class="{wrapper}" data-source-units="{attrs}">{items}</div>'
    if kind == "steps":
        wrapper = "brief-flow" if brief else "steps"
        item_css = ' class="brief-step"' if brief else ""
        items = "".join(
            f'<li{item_css} data-source-units="{_attrs(item)}"><h3>{html.escape(item["title"])}</h3><p>{html.escape(item["text"])}</p></li>'
            for item in component["items"]
        )
        return f'{heading}<ol class="{wrapper}" data-source-units="{attrs}">{items}</ol>'
    if kind == "cycle":
        return heading + _cycle_svg(component, brief=brief)
    if kind == "evidence":
        if brief:
            items = "".join(
                f'<article class="brief-watch-item" data-source-units="{_attrs(item)}"><h3>{html.escape(item["topic"])}</h3><p><strong>{html.escape(item["judgment"])}</strong></p><p>{html.escape(item["detail"])}</p></article>'
                for item in component["items"]
            )
            return f'{heading}<div class="brief-watch" data-source-units="{attrs}">{items}</div>'
        items = "".join(
            f'<li data-source-units="{_attrs(item)}"><div class="evidence-topic"><h3>{html.escape(item["topic"])}</h3></div><div class="evidence-body"><p class="evidence-judgment">{html.escape(item["judgment"])}</p><p class="evidence-detail">{html.escape(item["detail"])}</p></div></li>'
            for item in component["items"]
        )
        return f'{heading}<ol class="evidence-rows" data-source-units="{attrs}">{items}</ol>'
    raise ValueError(f"未知组件类型：{kind}")


def render_editorial_report(
    document: dict,
    plan: dict,
    metadata: dict,
    units: list[SourceUnit],
    output: Path,
    *,
    report_mode: str,
) -> Path:
    brief = report_mode == "brief"
    template = _template_path(report_mode).read_text(encoding="utf-8")
    unit_index = {unit.unit_id: unit for unit in units}
    nav = ""
    if not brief:
        links = "".join(
            f'<a href="#{html.escape(section["id"], quote=True)}">{html.escape(section["heading"])}</a>'
            for section in document["sections"]
        )
        nav = f'<nav class="report-nav" aria-label="章节导航"><span class="report-nav-title">目录</span>{links}</nav>'
    sections: list[str] = []
    for number, section in enumerate(document["sections"], 1):
        label = _time_label(_refs(section), unit_index)
        if brief:
            heading = f"<h2>{html.escape(section['heading'])}</h2>"
            sections.append(
                f'<section class="brief-section" id="{html.escape(section["id"], quote=True)}" data-source-units="{_attrs(section)}">{heading}'
                + "".join(
                    _render_component(component, brief=True) for component in section["components"]
                )
                + "</section>"
            )
        else:
            time_markup = f'<span class="section-time">{html.escape(label)}</span>' if label else ""
            heading = f'<h2><span class="num">{number}</span><span class="section-title">{html.escape(section["heading"])}</span>{time_markup}</h2>'
            sections.append(
                f'<section id="{html.escape(section["id"], quote=True)}" data-source-units="{_attrs(section)}">{heading}'
                + "".join(
                    _render_component(component, brief=False) for component in section["components"]
                )
                + "</section>"
            )
    closing = document["closing"]
    closing_markup = ""
    if closing["kind"] != "none":
        paragraphs = f"<p>{html.escape(closing['text'])}</p>" if closing["text"] else ""
        items = ""
        if closing["items"]:
            items = (
                "<ul>"
                + "".join(
                    f'<li data-source-units="{_attrs(item)}">{html.escape(item["text"])}</li>'
                    for item in closing["items"]
                )
                + "</ul>"
            )
        css = "brief-feature" if brief else "keyline"
        closing_markup = f'<aside id="closing" class="{css}" data-source-units="{_attrs(closing)}"><h3>{html.escape(closing["heading"])}</h3>{paragraphs}{items}</aside>'
    source_url = str(metadata.get("url") or metadata.get("source_url") or "")
    source_title = str(metadata.get("title") or document["title"])
    uploader = str(metadata.get("uploader") or metadata.get("owner") or "")
    platform = "Bilibili" if metadata.get("bvid") else "本地字幕"
    source_link = (
        f'<a href="{html.escape(source_url, quote=True)}">原视频</a>' if source_url else ""
    )
    attribution = " · ".join(
        part for part in (platform, uploader, f"《{source_title}》", source_link) if part
    )
    terminology = ""
    if plan.get("terminology"):
        terminology = (
            "<p><strong>术语规范：</strong>"
            + "；".join(
                f"{html.escape(term['raw'])} → {html.escape(term['normalized'])}（{html.escape(term['reason'])}）"
                for term in plan["terminology"]
            )
            + "</p>"
        )
    sources = (
        f"<p>本报告基于 {len(units)} 个 canonical 字幕单元生成。每个正文模块的 "
        "<code>data-source-units</code> 用于回查和报告内定位；它不是独立的语义正确性证明。</p>"
        + terminology
    )
    replacements = {
        "{{TITLE}}": html.escape(document["title"]),
        "{{SUBTITLE}}": html.escape(document["subtitle"]),
        "{{LEAD}}": f'<span data-source-units="{_attrs(document["lead"])}">{html.escape(document["lead"]["text"])}</span>',
        "{{ATTRIBUTION}}": attribution,
        "{{BODY}}": nav + "".join(sections) + closing_markup,
        "{{SOURCES}}": sources,
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    output.write_text(template, encoding="utf-8")
    return output


def _layout_manifest(inspection: dict, document: dict) -> dict:
    component_types = [
        component["type"] for section in document["sections"] for component in section["components"]
    ]
    longest_repeat = 0
    current = 0
    previous = None
    for kind in component_types:
        current = current + 1 if kind == previous else 1
        previous = kind
        longest_repeat = max(longest_repeat, current)
    paragraph_lengths = [
        len(item["text"])
        for section in document["sections"]
        for component in section["components"]
        if component["type"] == "prose"
        for item in component["paragraphs"]
    ]
    return {
        "inspection_status": inspection.get("status"),
        "findings": inspection.get("findings") or [],
        "height": inspection.get("height"),
        "reference_elements": inspection.get("reference_elements"),
        "section_count": len(document["sections"]),
        "component_count": len(component_types),
        "component_types": component_types,
        "longest_same_type_run": longest_repeat,
        "longest_paragraph_characters": max(paragraph_lengths, default=0),
        "table_columns": [
            len(component["columns"])
            for section in document["sections"]
            for component in section["components"]
            if component["type"] == "table"
        ],
    }


def _usage_total(model: str, **stage_usage: dict | None) -> dict:
    stages = [value for value in stage_usage.values() if value is not None]
    result: dict[str, Any] = {
        "strategy": "editorial-report-v1",
        "model": model,
        **stage_usage,
        "llm_calls": sum(int(value.get("attempts") or 0) for value in stages),
        "llm_usd_estimate": None,
        "llm_cny_estimate": None,
        "llm_unpriced_calls": sum(int(value.get("attempts") or 0) for value in stages),
    }
    for source_key, target_key in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "tokens"),
    ):
        values = [value.get(source_key) for value in stages]
        if values and all(isinstance(value, int) for value in values):
            result[target_key] = sum(values)
    return result


def generate_editorial_report(
    run_dir: Path,
    units: list[SourceUnit],
    metadata: dict,
    *,
    base_url: str,
    model: str,
    api_key: str,
    report_mode: str,
    outline: dict | None = None,
    progress=None,
    telemetry: PipelineTelemetry | None = None,
) -> dict:
    """Generate an editorial report with three normal LLM calls and one conditional revision."""
    if not api_key:
        raise RuntimeError("REPORT_API_KEY 未配置")
    if report_mode not in {"standard", "brief"}:
        raise ValueError("报告模式无效")
    blocks = build_source_blocks(units)
    valid_ids = {unit.unit_id for unit in units}
    block_ids = {block["block_id"] for block in blocks}
    source = _blocks_prompt(blocks)
    usage_by_stage: dict[str, dict | None] = {
        "plan": None,
        "draft": None,
        "review": None,
        "revision": None,
    }

    try:
        plan, usage_by_stage["plan"] = _request_json(
            base_url=base_url,
            model=model,
            api_key=api_key,
            system_prompt=PLAN_SYSTEM_PROMPT,
            user_prompt=(
                f"本次阅读模式：{report_mode}。\n\n【已有知识树参考】\n{_outline_hint(outline)}\n\n【完整字幕块】\n{source}"
            ),
            validator=lambda data: validate_editorial_plan(data, valid_ids, block_ids),
            normalizer=normalize_editorial_plan,
            stage="编辑策划",
            max_tokens=32768,
            timeout=360,
            progress=progress,
            progress_percent=86,
            telemetry=telemetry,
            allow_thinking=False,
        )
    except ModelStageError as exc:
        raise ModelStageError(
            str(exc), stage="report-editorial-plan", usage=_usage_total(model, plan=exc.usage)
        ) from exc
    plan_path = _write_json(run_dir / "report-editorial-plan.json", plan)
    writing_plan = _editorial_plan_for_writing(plan)

    try:
        document, usage_by_stage["draft"] = _request_json(
            base_url=base_url,
            model=model,
            api_key=api_key,
            system_prompt=DRAFT_SYSTEM_PROMPT,
            user_prompt=(
                f"本次阅读模式：{report_mode}。\n\n【锁定的编辑计划】\n{json.dumps(writing_plan, ensure_ascii=False)}\n\n【完整字幕块】\n{source}"
            ),
            validator=lambda data: validate_editorial_document(data, plan, valid_ids),
            normalizer=normalize_editorial_document,
            stage="整篇编辑",
            max_tokens=32768,
            timeout=360,
            progress=progress,
            progress_percent=91,
            telemetry=telemetry,
            allow_thinking=False,
        )
    except ModelStageError as exc:
        raise ModelStageError(
            str(exc),
            stage="report-editorial-draft",
            usage=_usage_total(model, plan=usage_by_stage["plan"], draft=exc.usage),
        ) from exc
    document_path = _write_json(run_dir / "report-editorial-document.json", document)
    report_path = render_editorial_report(
        document, plan, metadata, units, run_dir / "report.html", report_mode=report_mode
    )
    inspection = inspect_report(run_dir, label="editorial-review")
    manifest = _layout_manifest(inspection, document)

    target_ids = {"lead", "closing"}
    target_ids.update(section["id"] for section in plan["sections"])
    target_ids.update(_component_map(plan))
    try:
        review, usage_by_stage["review"] = _request_json(
            base_url=base_url,
            model=model,
            api_key=api_key,
            system_prompt=REVIEW_SYSTEM_PROMPT,
            user_prompt=(
                f"【完整字幕块】\n{source}\n\n【锁定计划】\n{json.dumps(writing_plan, ensure_ascii=False)}\n\n"
                f"【当前正文】\n{json.dumps(document, ensure_ascii=False)}\n\n【程序版面清单】\n{json.dumps(manifest, ensure_ascii=False)}"
            ),
            validator=lambda data: validate_editorial_review(data, target_ids),
            stage="终稿复核",
            max_tokens=4096,
            timeout=360,
            progress=progress,
            progress_percent=95,
            telemetry=telemetry,
            allow_thinking=False,
        )
    except ModelStageError as exc:
        raise ModelStageError(
            str(exc),
            stage="report-editorial-review",
            usage=_usage_total(
                model, plan=usage_by_stage["plan"], draft=usage_by_stage["draft"], review=exc.usage
            ),
        ) from exc
    review["layout_manifest"] = manifest
    review_path = _write_json(run_dir / "report-editorial-review.json", review)

    revision_needed = bool(manifest["findings"]) or review["status"] == "revise"
    revised_path: Path | None = None
    revision_patch_path: Path | None = None
    if revision_needed:
        try:
            revision_patch, usage_by_stage["revision"] = _request_json(
                base_url=base_url,
                model=model,
                api_key=api_key,
                system_prompt=REVISION_SYSTEM_PROMPT,
                user_prompt=(
                    f"【完整字幕块】\n{source}\n\n【锁定计划】\n{json.dumps(writing_plan, ensure_ascii=False)}\n\n"
                    f"【当前正文】\n{json.dumps(document, ensure_ascii=False)}\n\n【必须处理的问题】\n{json.dumps(review, ensure_ascii=False)}"
                ),
                validator=lambda data: apply_editorial_revision(data, document, plan, valid_ids),
                stage="局部修订",
                max_tokens=16384,
                timeout=360,
                progress=progress,
                progress_percent=97,
                telemetry=telemetry,
                allow_thinking=False,
            )
        except ModelStageError as exc:
            raise ModelStageError(
                str(exc),
                stage="report-editorial-revision",
                usage=_usage_total(model, **{**usage_by_stage, "revision": exc.usage}),
            ) from exc
        revision_patch_path = _write_json(
            run_dir / "report-editorial-revision.json", revision_patch
        )
        document = apply_editorial_revision(revision_patch, document, plan, valid_ids)
        revised_path = _write_json(run_dir / "report-editorial-revised.json", document)
        report_path = render_editorial_report(
            document, plan, metadata, units, report_path, report_mode=report_mode
        )
        inspection = inspect_report(run_dir, label="editorial-final")

    _write_json(run_dir / "report-inspection.json", inspection)
    if inspection.get("status") != "checked":
        raise RuntimeError(f"编辑型报告版面检查失败：{inspection.get('error') or 'unknown error'}")
    if inspection.get("findings"):
        raise RuntimeError(f"编辑型报告版面或来源检查未通过：{inspection['findings'][:3]}")
    fill_video_description(report_path, run_dir)
    image_path = render_report_image(run_dir, refresh=True)
    usage = _usage_total(model, **usage_by_stage)
    summary = {
        "engine": "editorial",
        "strategy": "editorial-report-v1",
        "source_blocks": len(blocks),
        "ledger_items": len(plan["content_ledger"]),
        "sections": len(plan["sections"]),
        "components": sum(len(section["components"]) for section in plan["sections"]),
        "component_types": manifest["component_types"],
        "review_status": review["status"],
        "review_issues": len(review["issues"]),
        "revision_performed": revision_needed,
        "llm_calls": usage["llm_calls"],
    }
    summary_path = _write_json(run_dir / "report-editorial-summary.json", summary)
    artifacts = [
        "report.html",
        "report.png",
        "report-inspection.json",
        plan_path.name,
        document_path.name,
        review_path.name,
        summary_path.name,
    ]
    if revised_path:
        artifacts.append(revised_path.name)
    if revision_patch_path:
        artifacts.append(revision_patch_path.name)
    return {
        "engine": "editorial",
        "html": str(report_path),
        "png": str(image_path),
        "plan": str(plan_path),
        "document": str(revised_path or document_path),
        "review": str(review_path),
        "summary": str(summary_path),
        "inspection": inspection,
        "usage": usage,
        "agent_summary": summary,
        "artifacts": artifacts,
    }
