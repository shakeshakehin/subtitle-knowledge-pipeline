from __future__ import annotations

import html
import json
from pathlib import Path

from .canonical import SourceUnit

REPORT_LINK_MARKER = "fusion-report-link-handler"


def _references(value: dict) -> list[str]:
    return [str(item) for item in value.get("source_units") or [] if str(item)]


def _range_attributes(source_ids: list[str], unit_index: dict[str, SourceUnit]) -> str:
    units = [unit_index[item] for item in source_ids if item in unit_index]
    starts = [unit.start_ms for unit in units if unit.start_ms is not None]
    ends = [unit.end_ms for unit in units if unit.end_ms is not None]
    line_starts = [unit.line_start for unit in units if unit.line_start is not None]
    line_ends = [unit.line_end for unit in units if unit.line_end is not None]
    fields = [f'data-source-units="{html.escape(" ".join(source_ids))}"']
    if starts:
        fields.extend((f'data-start-ms="{min(starts)}"', f'data-end-ms="{max(ends or starts)}"'))
    if line_starts:
        fields.extend(
            (
                f'data-line-start="{min(line_starts)}"',
                f'data-line-end="{max(line_ends or line_starts)}"',
            )
        )
    return " ".join(fields)


def _time_label(source_ids: list[str], unit_index: dict[str, SourceUnit]) -> str:
    units = [unit_index[item] for item in source_ids if item in unit_index]
    starts = [unit.start_ms for unit in units if unit.start_ms is not None]
    ends = [unit.end_ms for unit in units if unit.end_ms is not None]
    if starts:

        def stamp(value: int) -> str:
            seconds = value // 1000
            hours, seconds = divmod(seconds, 3600)
            minutes, seconds = divmod(seconds, 60)
            return (
                f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                if hours
                else f"{minutes:02d}:{seconds:02d}"
            )

        return f"{stamp(min(starts))}–{stamp(max(ends or starts))}"
    line_starts = [unit.line_start for unit in units if unit.line_start is not None]
    line_ends = [unit.line_end for unit in units if unit.line_end is not None]
    if line_starts:
        return f"字幕行 {min(line_starts)}–{max(line_ends or line_starts)}"
    return f"{len(source_ids)} 个来源单元" if source_ids else "无来源定位"


def _point_tree(points: list[dict]) -> list[tuple[dict, list]]:
    by_id = {str(point.get("id")): point for point in points if point.get("id")}
    children: dict[str, list[dict]] = {key: [] for key in by_id}
    roots: list[dict] = []
    for point in points:
        parent = point.get("parent_id")
        if parent and str(parent) in by_id:
            children[str(parent)].append(point)
        else:
            roots.append(point)

    def build(point: dict) -> tuple[dict, list]:
        return point, [build(child) for child in children.get(str(point.get("id")), [])]

    return [build(point) for point in roots]


def _node_html(
    point: dict,
    children: list[tuple[dict, list]],
    unit_index: dict[str, SourceUnit],
    *,
    linked: bool,
) -> str:
    source_ids = _references(point)
    title = str(point.get("title") or point.get("text") or "未命名节点")
    detail = str(point.get("detail") or "")
    kind = str(point.get("kind") or "point")
    attrs = _range_attributes(source_ids, unit_index)
    action = "button" if linked and source_ids else "div"
    action_attrs = ' type="button"' if action == "button" else ""
    child_markup = "".join(
        _node_html(child, descendants, unit_index, linked=linked) for child, descendants in children
    )
    children_block = f'<div class="children">{child_markup}</div>' if child_markup else ""
    return (
        '<div class="tree-item">'
        f'<{action} class="node-card" {attrs}{action_attrs}>'
        f'<span class="node-kind">{html.escape(kind)}</span>'
        f"<strong>{html.escape(title)}</strong>"
        f'<span class="node-time">{html.escape(_time_label(source_ids, unit_index))}</span>'
        f"{f'<small>{html.escape(detail)}</small>' if detail else ''}"
        f"</{action}>{children_block}</div>"
    )


def install_report_link_handler(report_path: Path, units: list[SourceUnit]) -> bool:
    """Make report-side locating work even when both HTML files are opened via file://."""
    if not report_path.is_file():
        return False
    document = report_path.read_text(encoding="utf-8")
    if f'id="{REPORT_LINK_MARKER}"' in document:
        return False
    unit_times = {
        unit.unit_id: {
            "start": unit.start_ms,
            "end": unit.end_ms,
            "lineStart": unit.line_start,
            "lineEnd": unit.line_end,
        }
        for unit in units
    }
    data_json = json.dumps(unit_times, ensure_ascii=False).replace("</", "<\\/")
    script = f"""<script id="{REPORT_LINK_MARKER}">
(() => {{
  const UNIT_TIMES={data_json};
  const splitIds=value=>(value||'').split(/[\\s,;]+/).filter(Boolean);
  const rangeFor=ids=>{{
    const rows=ids.map(id=>UNIT_TIMES[id]).filter(Boolean);
    const starts=rows.map(x=>x.start).filter(Number.isFinite), ends=rows.map(x=>x.end).filter(Number.isFinite);
    const lineStarts=rows.map(x=>x.lineStart).filter(Number.isFinite), lineEnds=rows.map(x=>x.lineEnd).filter(Number.isFinite);
    return {{start:starts.length?Math.min(...starts):null,end:ends.length?Math.max(...ends):null,lineStart:lineStarts.length?Math.min(...lineStarts):null,lineEnd:lineEnds.length?Math.max(...lineEnds):null}};
  }};
  const overlap=(a1,a2,b1,b2)=>[a1,a2,b1,b2].every(Number.isFinite)?Math.max(0,Math.min(a2,b2)-Math.max(a1,b1)):0;
  const stamp=ms=>{{const n=Math.floor(ms/1000),s=String(n%60).padStart(2,'0'),m=Math.floor(n/60)%60,h=Math.floor(n/3600);return h?`${{String(h).padStart(2,'0')}}:${{String(m).padStart(2,'0')}}:${{s}}`:`${{String(m).padStart(2,'0')}}:${{s}}`;}};
  const clear=()=>document.querySelectorAll('.fusion-report-match').forEach(el=>{{el.classList.remove('fusion-report-match');el.style.outline='';el.style.backgroundColor='';}});
  addEventListener('message', event=>{{
    if(event.data?.type==='fusion-clear'){{clear();return;}}
    if(event.data?.type!=='fusion-locate'||!Array.isArray(event.data.ids))return;
    clear();
    const wanted=event.data.ids, wantedSet=new Set(wanted), wantedRange=rangeFor(wanted);
    const candidates=[...document.querySelectorAll('[data-source-units]')].map(el=>{{
      const ids=splitIds(el.getAttribute('data-source-units')), exact=ids.filter(id=>wantedSet.has(id)).length, r=rangeFor(ids);
      const time=overlap(wantedRange.start,wantedRange.end,r.start,r.end), lines=overlap(wantedRange.lineStart,wantedRange.lineEnd,r.lineStart,r.lineEnd);
      const tagBonus=/^(P|LI|TR|FIGURE|BLOCKQUOTE)$/.test(el.tagName)?250:0;
      return {{el,ids,exact,time,lines,score:exact*100000+time+lines*1000+tagBonus-Math.min(ids.length,200)}};
    }}).filter(x=>x.exact||x.time||x.lines).sort((a,b)=>b.score-a.score);
    if(!candidates.length){{event.source?.postMessage({{type:'fusion-located',found:false}},'*');return;}}
    const best=candidates[0];
    candidates.filter(x=>x.exact&&x.exact===best.exact).slice(0,8).forEach(x=>{{x.el.classList.add('fusion-report-match');x.el.style.outline='3px solid #f06b4f';x.el.style.backgroundColor='#fff3cf';}});
    best.el.scrollIntoView({{behavior:'smooth',block:'center'}});
    const label=Number.isFinite(wantedRange.start)?`${{stamp(wantedRange.start)}}–${{stamp(wantedRange.end)}}`:Number.isFinite(wantedRange.lineStart)?`字幕行 ${{wantedRange.lineStart}}–${{wantedRange.lineEnd}}`:`${{wanted.length}} 个来源单元`;
    event.source?.postMessage({{type:'fusion-located',found:true,label,matchCount:best.exact||1}},'*');
  }});
}})();
</script>"""
    lowered = document.lower()
    position = lowered.rfind("</body>")
    updated = (
        document[:position] + script + document[position:] if position >= 0 else document + script
    )
    report_path.write_text(updated, encoding="utf-8")
    return True


def render_tree_html(
    outline: dict,
    units: list[SourceUnit],
    metadata: dict,
    output: Path,
    *,
    report_filename: str | None = None,
) -> Path:
    """Render an interactive HTML tree, optionally linked to a sibling detailed report.

    The time range is used only to find report elements backed by the same transcript range.
    This page intentionally contains no link or script that seeks the original video.
    """
    unit_index = {unit.unit_id: unit for unit in units}
    linked = bool(report_filename)
    if report_filename:
        install_report_link_handler(output.parent / report_filename, units)
    all_ids = [unit.unit_id for unit in units]
    root_attrs = _range_attributes(all_ids, unit_index)
    structure_design = outline.get("structure_design") or {}
    selected_axis = structure_design.get("selected_axis") or {}
    axis_name = next(
        (
            str(axis.get("name") or "")
            for axis in structure_design.get("candidate_axes") or []
            if axis.get("id") == selected_axis.get("id")
        ),
        "",
    )
    root_note = (
        f"组织轴：{axis_name}。树节点的时间范围只用于定位右侧详细报告，不跳转原视频。"
        if axis_name
        else "树节点的时间范围只用于定位右侧详细报告，不跳转原视频。"
    )
    branch_markup: list[str] = []
    for branch in outline.get("outline") or []:
        source_ids = _references(branch)
        points = _point_tree(branch.get("points") or [])
        point_markup = "".join(
            _node_html(point, children, unit_index, linked=linked) for point, children in points
        )
        branch_markup.append(
            '<section class="branch">'
            f'<button class="branch-title" type="button" {_range_attributes(source_ids, unit_index)}>'
            f"<span>{html.escape(str(branch.get('heading') or '未命名分支'))}</span>"
            f"<em>{html.escape(_time_label(source_ids, unit_index))}</em>"
            "</button>"
            f'<div class="branch-body">{point_markup}</div></section>'
        )
    summaries = []
    for item in outline.get("summary") or []:
        text = str(item.get("text") if isinstance(item, dict) else item)
        source_ids = _references(item) if isinstance(item, dict) else []
        summaries.append(
            f"<li {_range_attributes(source_ids, unit_index)}>{html.escape(text)}"
            f"<span>{html.escape(_time_label(source_ids, unit_index))}</span></li>"
        )
    summary_markup = (
        '<section class="summary"><h2>总结结论</h2><ol>' + "".join(summaries) + "</ol></section>"
        if summaries
        else ""
    )
    unit_times = {
        unit.unit_id: {
            "start": unit.start_ms,
            "end": unit.end_ms,
            "lineStart": unit.line_start,
            "lineEnd": unit.line_end,
        }
        for unit in units
    }
    report_panel = (
        '<main class="report-pane"><div class="report-toolbar">'
        '<strong>详细报告</strong><span id="match-status">点击左侧节点定位到报告内容</span>'
        '<button id="clear-match" type="button">清除定位</button></div>'
        f'<iframe id="report-frame" title="详细报告" src="{html.escape(report_filename or "")}"></iframe></main>'
        if linked
        else ""
    )
    linked_class = " linked" if linked else ""
    data_json = json.dumps(unit_times, ensure_ascii=False).replace("</", "<\\/")
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(str(metadata.get("title") or outline.get("title") or "知识树"))}</title>
<style>
:root{{--ink:#18252f;--muted:#6b7780;--line:#cfdae1;--paper:#f5f3ee;--blue:#1d5f82;--blue-soft:#dcecf4;--accent:#f06b4f;--panel:#fff}}
*{{box-sizing:border-box}} html,body{{margin:0;min-height:100%;font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);background:var(--paper)}}
.app{{display:grid;grid-template-columns:minmax(440px,42vw) 1fr;height:100vh;overflow:hidden}} .app:not(.linked){{display:block;height:auto;min-height:100vh}}
.tree-pane{{overflow:auto;padding:24px 26px 60px;background:linear-gradient(150deg,#f7f4ee,#eef3f4)}} .app:not(.linked) .tree-pane{{max-width:1280px;margin:auto}}
.eyebrow{{font-size:12px;letter-spacing:.16em;color:var(--blue);font-weight:800}} h1{{font-size:28px;line-height:1.25;margin:8px 0}} .thesis{{margin:0 0 22px;color:#3c4a52;line-height:1.7}}
.root-card{{padding:18px 20px;border-radius:16px;background:#173b51;color:#fff;box-shadow:0 10px 26px #173b5122;margin-bottom:18px}} .root-card small{{display:block;margin-top:8px;color:#cfe1ea}}
.branch{{position:relative;margin:0 0 16px 18px;padding-left:22px;border-left:2px solid #9db7c6}} .branch:before{{content:"";position:absolute;left:-2px;top:25px;width:22px;border-top:2px solid #9db7c6}}
.branch-title,.node-card{{font:inherit;text-align:left;color:inherit;border:0;cursor:default}} button.branch-title,button.node-card{{cursor:pointer}}
.branch-title{{width:100%;display:flex;justify-content:space-between;gap:14px;align-items:center;padding:12px 14px;border-radius:12px;background:var(--blue);color:#fff;box-shadow:0 6px 16px #1d5f8220}} .branch-title span{{font-weight:800}} .branch-title em{{font-size:12px;font-style:normal;color:#d7edf7;white-space:nowrap}}
.branch-body{{padding-top:10px}} .tree-item{{position:relative;margin:8px 0 8px 15px;padding-left:20px;border-left:1px solid var(--line)}} .tree-item:before{{content:"";position:absolute;left:0;top:24px;width:20px;border-top:1px solid var(--line)}}
.node-card{{display:grid;grid-template-columns:auto 1fr auto;gap:5px 9px;width:100%;padding:11px 13px;border-radius:11px;background:var(--panel);box-shadow:0 3px 12px #23343e12;border:1px solid transparent}} button.node-card:hover,button.branch-title:hover{{outline:3px solid #f06b4f33}} .node-card:focus-visible,.branch-title:focus-visible{{outline:3px solid var(--accent)}}
.node-kind{{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--blue);background:var(--blue-soft);padding:3px 6px;border-radius:99px;align-self:start}} .node-card strong{{font-size:14px;line-height:1.5}} .node-time{{font-size:11px;color:var(--muted);white-space:nowrap}} .node-card small{{grid-column:2/4;color:#526069;line-height:1.55}}
.children{{margin-left:10px}} .summary{{margin-top:24px;padding:18px;border-radius:14px;background:#fff7e8;border:1px solid #edd9ae}} .summary h2{{margin:0 0 10px;font-size:18px}} .summary li{{margin:8px 0;line-height:1.6}} .summary li span{{display:block;font-size:11px;color:var(--muted)}}
.report-pane{{min-width:0;background:#d9dde0;display:grid;grid-template-rows:auto 1fr}} .report-toolbar{{display:flex;align-items:center;gap:14px;min-height:54px;padding:9px 16px;background:#fff;border-bottom:1px solid #ccd4d8}} .report-toolbar span{{flex:1;color:var(--muted);font-size:13px}} .report-toolbar button{{border:1px solid #bdc8ce;background:#fff;border-radius:8px;padding:7px 11px;cursor:pointer}} iframe{{width:100%;height:100%;border:0;background:white}}
@media(max-width:900px){{.app{{grid-template-columns:1fr;height:auto;overflow:visible}}.tree-pane{{max-height:none}}.report-pane{{height:82vh}}}}
</style>
</head>
<body>
<div class="app{linked_class}">
<aside class="tree-pane">
  <div class="eyebrow">FUSION KNOWLEDGE TREE</div>
  <h1>{html.escape(str(outline.get("title") or metadata.get("title") or "知识树"))}</h1>
  <p class="thesis">{html.escape(str(outline.get("thesis") or ""))}</p>
  <div class="root-card" {root_attrs}><strong>{html.escape(str(outline.get("type") or "知识结构"))}</strong><small>{html.escape(root_note)}</small></div>
  {"".join(branch_markup)}
  {summary_markup}
</aside>
{report_panel}
</div>
<script>
const UNIT_TIMES={data_json};
const frame=document.getElementById('report-frame');
const status=document.getElementById('match-status');
const splitIds=value=>(value||'').split(/[\\s,;]+/).filter(Boolean);
const rangeFor=ids=>{{
  const rows=ids.map(id=>UNIT_TIMES[id]).filter(Boolean);
  const starts=rows.map(x=>x.start).filter(Number.isFinite), ends=rows.map(x=>x.end).filter(Number.isFinite);
  const lineStarts=rows.map(x=>x.lineStart).filter(Number.isFinite), lineEnds=rows.map(x=>x.lineEnd).filter(Number.isFinite);
  return {{start:starts.length?Math.min(...starts):null,end:ends.length?Math.max(...ends):null,lineStart:lineStarts.length?Math.min(...lineStarts):null,lineEnd:lineEnds.length?Math.max(...lineEnds):null}};
}};
const overlap=(a1,a2,b1,b2)=>[a1,a2,b1,b2].every(Number.isFinite)?Math.max(0,Math.min(a2,b2)-Math.max(a1,b1)):0;
const stamp=ms=>{{const n=Math.floor(ms/1000),s=String(n%60).padStart(2,'0'),m=Math.floor(n/60)%60,h=Math.floor(n/3600);return h?`${{String(h).padStart(2,'0')}}:${{String(m).padStart(2,'0')}}:${{s}}`:`${{String(m).padStart(2,'0')}}:${{s}}`;}};
function clearMatches(){{if(!frame?.contentDocument)return;frame.contentDocument.querySelectorAll('.fusion-report-match').forEach(el=>{{el.classList.remove('fusion-report-match');el.style.outline='';el.style.backgroundColor='';}});}}
function locate(trigger){{
  if(!frame)return;
  const wanted=splitIds(trigger.dataset.sourceUnits);
  frame.contentWindow?.postMessage({{type:'fusion-locate',ids:wanted}},'*');
  if(!frame.contentDocument){{status.textContent='正在详细报告中定位…';return;}}
  clearMatches();
  const wantedSet=new Set(wanted), wantedRange=rangeFor(wanted);
  const candidates=[...frame.contentDocument.querySelectorAll('[data-source-units]')].map(el=>{{
    const ids=splitIds(el.getAttribute('data-source-units')), exact=ids.filter(id=>wantedSet.has(id)).length, r=rangeFor(ids);
    const time=overlap(wantedRange.start,wantedRange.end,r.start,r.end), lines=overlap(wantedRange.lineStart,wantedRange.lineEnd,r.lineStart,r.lineEnd);
    const tagBonus=/^(P|LI|TR|FIGURE|BLOCKQUOTE)$/.test(el.tagName)?250:0;
    return {{el,ids,exact,time,lines,score:exact*100000+time+lines*1000+tagBonus-Math.min(ids.length,200)}};
  }}).filter(x=>x.exact||x.time||x.lines).sort((a,b)=>b.score-a.score);
  if(!candidates.length){{status.textContent='详细报告中没有找到对应来源绑定';return;}}
  const best=candidates[0];
  candidates.filter(x=>x.exact&&x.exact===best.exact).slice(0,8).forEach(x=>{{x.el.classList.add('fusion-report-match');x.el.style.outline='3px solid #f06b4f';x.el.style.backgroundColor='#fff3cf';}});
  best.el.scrollIntoView({{behavior:'smooth',block:'center'}});
  const label=Number.isFinite(wantedRange.start)?`${{stamp(wantedRange.start)}}–${{stamp(wantedRange.end)}}`:Number.isFinite(wantedRange.lineStart)?`字幕行 ${{wantedRange.lineStart}}–${{wantedRange.lineEnd}}`:`${{wanted.length}} 个来源单元`;
  status.textContent=`已定位：${{label}} · 命中 ${{best.exact||1}} 个来源单元`;
}}
document.querySelectorAll('button[data-source-units]').forEach(el=>el.addEventListener('click',()=>locate(el)));
addEventListener('message',event=>{{if(event.data?.type!=='fusion-located')return;status.textContent=event.data.found?`已定位：${{event.data.label}} · 命中 ${{event.data.matchCount}} 个来源单元`:'详细报告中没有找到对应来源绑定';}});
document.getElementById('clear-match')?.addEventListener('click',()=>{{clearMatches();frame?.contentWindow?.postMessage({{type:'fusion-clear'}},'*');status.textContent='点击左侧节点定位到报告内容';}});
</script>
</body>
</html>"""
    output.write_text(document, encoding="utf-8")
    return output
