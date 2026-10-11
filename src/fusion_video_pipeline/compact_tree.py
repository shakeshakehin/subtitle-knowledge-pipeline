from __future__ import annotations

import html
import json
from pathlib import Path

from .canonical import SourceUnit
from .interactive import (
    _point_tree,
    _range_attributes,
    _references,
    _time_label,
    install_report_link_handler,
)

BRANCH_THEMES = (
    ("#d96845", "#fbede8"),
    ("#27827a", "#e8f4f2"),
    ("#a56d1b", "#f8f0df"),
    ("#665da0", "#efedf8"),
    ("#347397", "#e8f1f6"),
    ("#8a5967", "#f5eaee"),
)


def _descendant_count(children: list[tuple[dict, list]]) -> int:
    return sum(1 + _descendant_count(descendants) for _, descendants in children)


def _attribute(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _compact_node_html(
    point: dict,
    children: list[tuple[dict, list]],
    unit_index: dict[str, SourceUnit],
    *,
    linked: bool,
    level: int,
) -> str:
    source_ids = _references(point)
    title = str(point.get("title") or point.get("text") or "未命名节点")
    child_markup = "".join(
        _compact_node_html(
            child,
            descendants,
            unit_index,
            linked=linked,
            level=level + 1,
        )
        for child, descendants in children
    )
    descendants = _descendant_count(children)
    fold = (
        '<button class="node-fold" type="button" data-node-fold '
        f'aria-expanded="false" title="展开 {descendants} 个下级节点">'
        f'<span aria-hidden="true">＋</span><em>{descendants}</em></button>'
        if children
        else ""
    )
    children_block = f'<div class="children">{child_markup}</div>' if child_markup else ""
    collapsed = " is-collapsed" if children else ""
    return (
        f'<div class="tree-item{collapsed}" data-level="{level}" '
        f'data-node-id="{_attribute(point.get("id"))}">'
        '<div class="node-row">'
        f'<button class="node-main" type="button" data-node-select '
        f'{_range_attributes(source_ids, unit_index)} '
        f'data-can-locate="{str(linked and bool(source_ids)).lower()}">'
        '<span class="node-dot" aria-hidden="true"></span>'
        f'<span class="node-title">{html.escape(title)}</span>'
        "</button>"
        f"{fold}</div>{children_block}</div>"
    )


def render_compact_tree_html(
    outline: dict,
    units: list[SourceUnit],
    metadata: dict,
    output: Path,
    *,
    report_filename: str | None = None,
) -> Path:
    """Render a progressive-disclosure tree that stays compact at first glance."""
    unit_index = {unit.unit_id: unit for unit in units}
    linked = bool(report_filename)
    if report_filename:
        install_report_link_handler(output.parent / report_filename, units)

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
    branch_markup: list[str] = []
    for index, branch in enumerate(outline.get("outline") or []):
        color, soft = BRANCH_THEMES[index % len(BRANCH_THEMES)]
        source_ids = _references(branch)
        points = _point_tree(branch.get("points") or [])
        point_markup = "".join(
            _compact_node_html(point, children, unit_index, linked=linked, level=1)
            for point, children in points
        )
        point_count = len(branch.get("points") or [])
        branch_markup.append(
            f'<section class="branch" data-branch="branch-{index + 1}" '
            f'style="--branch:{color};--branch-soft:{soft}">'
            '<div class="branch-head">'
            f'<button class="branch-main" type="button" data-node-select '
            f'{_range_attributes(source_ids, unit_index)} '
            f'data-can-locate="{str(linked and bool(source_ids)).lower()}">'
            f'<span class="branch-index">{index + 1:02d}</span>'
            f'<span><strong>{html.escape(str(branch.get("heading") or "未命名分支"))}</strong>'
            f'<small>{point_count} 个知识节点</small></span></button>'
            '<button class="branch-focus" type="button" data-branch-focus title="只查看这个分支">聚焦</button>'
            '<button class="branch-fold" type="button" data-branch-fold aria-expanded="true" title="折叠分支">−</button>'
            "</div>"
            f'<div class="branch-body">{point_markup}</div></section>'
        )

    summaries: list[str] = []
    for item in outline.get("summary") or []:
        text = str(item.get("text") if isinstance(item, dict) else item)
        source_ids = _references(item) if isinstance(item, dict) else []
        summaries.append(
            f'<li {_range_attributes(source_ids, unit_index)}><span>{html.escape(text)}</span>'
            f'<small>{html.escape(_time_label(source_ids, unit_index))}</small></li>'
        )
    summary_markup = (
        '<details class="summary"><summary>总结结论'
        f'<span>{len(summaries)}</span></summary><ol>{"".join(summaries)}</ol></details>'
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
        '<div id="splitter" class="splitter" role="separator" tabindex="0" '
        'aria-label="调整知识树与详细报告宽度" aria-orientation="vertical" '
        'aria-valuemin="22" aria-valuemax="78" aria-valuenow="44" '
        'title="拖动调整两侧宽度；双击恢复默认比例"><span aria-hidden="true"></span></div>'
        '<main class="report-pane"><div class="report-toolbar"><strong>详细报告</strong>'
        '<span id="match-status">选择左侧节点可定位到报告内容</span>'
        '<button id="clear-match" type="button">清除定位</button></div>'
        f'<iframe id="report-frame" title="详细报告" src="{_attribute(report_filename)}"></iframe></main>'
        if linked
        else ""
    )
    linked_class = " linked" if linked else ""
    root_note = f"组织轴 · {axis_name}" if axis_name else "知识结构概览"
    title = html.escape(str(outline.get("title") or metadata.get("title") or "知识树"))
    thesis = html.escape(str(outline.get("thesis") or ""))
    data_json = json.dumps(unit_times, ensure_ascii=False).replace("</", "<\\/")

    document = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--ink:#17262f;--muted:#718089;--line:#d8dfdf;--paper:#f4f5f2;--navy:#17384b;--accent:#e86d50;--tree-width:44%}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;color:var(--ink);background:var(--paper)}button{font:inherit}.app{display:grid;grid-template-columns:var(--tree-width) 10px minmax(0,1fr);height:100vh;overflow:hidden}.app:not(.linked){grid-template-columns:minmax(0,1fr)}
.tree-pane{min-width:0;height:100vh;display:grid;grid-template-rows:auto minmax(0,1fr);overflow:hidden;background:#f8f8f5}.tree-header{padding:22px 25px 15px;border-bottom:1px solid #e1e5e2;background:#fbfbf9}.eyebrow{font-size:10px;font-weight:900;letter-spacing:.18em;color:#517284}.title-row{display:flex;align-items:flex-start;gap:14px;margin-top:8px}.title-copy{min-width:0;flex:1}.title-copy h1{font-size:clamp(22px,2.1vw,31px);line-height:1.25;letter-spacing:-.025em;margin:0}.title-copy p{font-size:13px;line-height:1.65;color:#52626b;margin:8px 0 0}.axis{margin-top:11px;color:#69808b;font-size:11px}.tree-tools{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:15px}.tool-group{display:flex;padding:3px;border-radius:9px;background:#edf0ed}.tree-tools button{border:0;border-radius:7px;padding:7px 9px;background:transparent;color:#52626b;font-size:11px;font-weight:750;cursor:pointer}.tree-tools button:hover{color:var(--navy)}.tree-tools button.active{background:#fff;color:var(--navy);box-shadow:0 2px 9px #1e364512}.exit-focus{margin-left:auto!important;border:1px solid #cad4d5!important;background:#fff!important;color:#2d6076!important}.exit-focus[hidden]{display:none}
.tree-scroll{overflow:auto;padding:18px 22px 55px;scrollbar-gutter:stable}.branch{--branch:#347397;--branch-soft:#e8f1f6;margin-bottom:13px}.branch[hidden]{display:none}.branch-head{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:6px;align-items:stretch}.branch-main{min-width:0;display:flex;align-items:center;gap:11px;border:0;border-left:4px solid var(--branch);border-radius:9px;padding:10px 12px;background:#fff;color:var(--ink);text-align:left;cursor:pointer;box-shadow:0 2px 9px #2237430b}.branch-main:hover,.branch-main.active{background:var(--branch-soft)}.branch-index{color:var(--branch);font-size:11px;font-weight:900;letter-spacing:.04em}.branch-main>span:last-child{min-width:0}.branch-main strong{display:block;font-size:14px;line-height:1.45}.branch-main small{display:block;margin-top:2px;color:var(--muted);font-size:10px}.branch-focus,.branch-fold{border:1px solid #d7dddd;border-radius:8px;background:#fff;color:#6b7b82;font-size:10px;font-weight:800;cursor:pointer}.branch-focus{padding:0 9px}.branch-fold{width:33px;font-size:17px}.branch-focus:hover,.branch-fold:hover{border-color:var(--branch);color:var(--branch)}.branch.is-collapsed .branch-body{display:none}.branch.is-collapsed .branch-fold{font-size:0}.branch.is-collapsed .branch-fold:after{content:'＋';font-size:16px}
.branch-body{padding:8px 0 1px 13px}.tree-item{position:relative;margin-left:9px;padding-left:16px;border-left:1px solid color-mix(in srgb,var(--branch) 30%,#d9dfdf)}.tree-item:before{content:"";position:absolute;left:-1px;top:18px;width:13px;border-top:1px solid color-mix(in srgb,var(--branch) 38%,#d9dfdf)}.node-row{display:flex;align-items:center;gap:5px;min-height:35px}.node-main{min-width:0;flex:1;display:flex;align-items:center;gap:9px;border:0;border-radius:8px;padding:7px 8px;background:transparent;color:var(--ink);text-align:left;cursor:pointer}.node-main:hover,.node-main.active{background:var(--branch-soft)}.node-dot{flex:none;width:7px;height:7px;border:2px solid var(--branch);border-radius:50%;background:#fff}.node-title{font-size:12px;line-height:1.5}.tree-item[data-level="1"]>.node-row .node-title{font-size:13px;font-weight:750}.tree-item[data-level="1"]>.node-row .node-dot{width:8px;height:8px;background:var(--branch)}.tree-item[data-level="3"] .node-title,.tree-item[data-level="4"] .node-title{color:#53636b}.node-fold{flex:none;display:flex;align-items:center;gap:2px;border:0;border-radius:7px;padding:4px 6px;background:transparent;color:var(--branch);font-size:12px;cursor:pointer}.node-fold:hover{background:var(--branch-soft)}.node-fold em{font-size:9px;font-style:normal}.children{margin-left:6px}.tree-item.is-collapsed>.children{display:none}.tree-item.is-collapsed>.node-row .node-fold span{font-size:0}.tree-item.is-collapsed>.node-row .node-fold span:after{content:'＋';font-size:12px}
.summary{margin:17px 0 0 4px;border-top:1px solid #dfe3df;padding-top:12px}.summary summary{display:flex;align-items:center;gap:8px;color:#4c5e67;font-size:12px;font-weight:800;cursor:pointer}.summary summary span{display:inline-grid;place-items:center;min-width:19px;height:19px;border-radius:99px;background:#ecefeb;color:#66767d;font-size:9px}.summary ol{margin:12px 0 0;padding:0 0 0 23px}.summary li{padding:4px 0 7px;line-height:1.55;font-size:12px}.summary li small{display:block;color:var(--muted);font-size:9px}
.splitter{position:relative;z-index:5;display:grid;place-items:center;cursor:col-resize;touch-action:none;background:#d7ddde;border-left:1px solid #c8d0d2;border-right:1px solid #c8d0d2;outline:none}.splitter span{width:3px;height:48px;border-radius:99px;background:#91a1a8;box-shadow:0 0 0 4px #ffffff80}.splitter:hover,.splitter:focus-visible,.splitter.dragging{background:#c3dce5}.splitter:hover span,.splitter:focus-visible span,.splitter.dragging span{background:#2f6f89}body.resizing{cursor:col-resize;user-select:none}.report-pane{min-width:0;background:#d9dddf;display:grid;grid-template-rows:auto 1fr}.report-toolbar{display:flex;align-items:center;gap:14px;min-height:52px;padding:9px 15px;background:#fff;border-bottom:1px solid #ccd4d8}.report-toolbar span{flex:1;color:var(--muted);font-size:11px}.report-toolbar button{border:1px solid #bdc8ce;background:#fff;border-radius:8px;padding:7px 10px;cursor:pointer}iframe{width:100%;height:100%;border:0;background:white}
@media(max-width:900px){.app{display:block;height:auto;overflow:visible}.tree-pane{height:100vh}.splitter{display:none}.report-pane{height:82vh}.title-copy h1{font-size:23px}.tree-header{padding:18px 17px 13px}.tree-scroll{padding:14px 14px 45px}.branch-focus{display:none}}
</style>
</head>
<body>
<div class="app__LINKED_CLASS__">
<aside class="tree-pane">
  <header class="tree-header">
    <div class="eyebrow">FUSION KNOWLEDGE TREE · OVERVIEW FIRST</div>
    <div class="title-row"><div class="title-copy"><h1>__TITLE__</h1><p>__THESIS__</p></div></div>
    <div class="axis">__ROOT_NOTE__</div>
    <nav class="tree-tools" aria-label="知识树展开方式">
      <div class="tool-group"><button class="active" type="button" data-tree-view="overview">概览</button><button type="button" data-tree-view="next">展开一层</button><button type="button" data-tree-view="all">全部展开</button></div>
      <button id="exit-focus" class="exit-focus" type="button" hidden>显示全部分支</button>
    </nav>
  </header>
  <main class="tree-scroll">__BRANCHES____SUMMARY__</main>
</aside>
__REPORT_PANEL__
</div>
<script>
const UNIT_TIMES=__UNIT_TIMES__;
const app=document.querySelector('.app'),splitter=document.getElementById('splitter'),frame=document.getElementById('report-frame'),status=document.getElementById('match-status');
const clampSplit=value=>Math.max(22,Math.min(78,value));
function setSplit(value){if(!app||!splitter)return;const percent=clampSplit(Number(value)||44);app.style.setProperty('--tree-width',`${percent}%`);splitter.setAttribute('aria-valuenow',String(Math.round(percent)));}
function splitFromPointer(clientX){if(!app||matchMedia('(max-width:900px)').matches)return;const bounds=app.getBoundingClientRect();setSplit((clientX-bounds.left)/bounds.width*100);}
let resizing=false;
splitter?.addEventListener('pointerdown',event=>{resizing=true;splitter.classList.add('dragging');document.body.classList.add('resizing');splitter.setPointerCapture(event.pointerId);splitFromPointer(event.clientX);event.preventDefault();});
splitter?.addEventListener('pointermove',event=>{if(resizing)splitFromPointer(event.clientX);});
function stopResizing(event){if(!resizing)return;resizing=false;splitter?.classList.remove('dragging');document.body.classList.remove('resizing');if(event?.pointerId!=null&&splitter?.hasPointerCapture(event.pointerId))splitter.releasePointerCapture(event.pointerId);}
splitter?.addEventListener('pointerup',stopResizing);splitter?.addEventListener('pointercancel',stopResizing);splitter?.addEventListener('dblclick',()=>setSplit(44));
splitter?.addEventListener('keydown',event=>{const current=Number(splitter.getAttribute('aria-valuenow'))||44;const next=event.key==='ArrowLeft'?current-3:event.key==='ArrowRight'?current+3:event.key==='Home'?22:event.key==='End'?78:null;if(next!=null){setSplit(next);event.preventDefault();}});
const splitIds=value=>(value||'').split(/[\s,;]+/).filter(Boolean);
const rangeFor=ids=>{const rows=ids.map(id=>UNIT_TIMES[id]).filter(Boolean),starts=rows.map(x=>x.start).filter(Number.isFinite),ends=rows.map(x=>x.end).filter(Number.isFinite),lineStarts=rows.map(x=>x.lineStart).filter(Number.isFinite),lineEnds=rows.map(x=>x.lineEnd).filter(Number.isFinite);return{start:starts.length?Math.min(...starts):null,end:ends.length?Math.max(...ends):null,lineStart:lineStarts.length?Math.min(...lineStarts):null,lineEnd:lineEnds.length?Math.max(...lineEnds):null};};
const overlap=(a1,a2,b1,b2)=>[a1,a2,b1,b2].every(Number.isFinite)?Math.max(0,Math.min(a2,b2)-Math.max(a1,b1)):0;
const stamp=ms=>{const n=Math.floor(ms/1000),s=String(n%60).padStart(2,'0'),m=Math.floor(n/60)%60,h=Math.floor(n/3600);return h?`${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${s}`:`${String(m).padStart(2,'0')}:${s}`;};
function clearMatches(){if(!frame?.contentDocument)return;frame.contentDocument.querySelectorAll('.fusion-report-match').forEach(el=>{el.classList.remove('fusion-report-match');el.style.outline='';el.style.backgroundColor='';});}
function locate(trigger){if(!frame)return;const wanted=splitIds(trigger.dataset.sourceUnits);frame.contentWindow?.postMessage({type:'fusion-locate',ids:wanted},'*');if(!frame.contentDocument){status.textContent='正在详细报告中定位…';return;}clearMatches();const wantedSet=new Set(wanted),wantedRange=rangeFor(wanted);const candidates=[...frame.contentDocument.querySelectorAll('[data-source-units]')].map(el=>{const ids=splitIds(el.getAttribute('data-source-units')),exact=ids.filter(id=>wantedSet.has(id)).length,r=rangeFor(ids),time=overlap(wantedRange.start,wantedRange.end,r.start,r.end),lines=overlap(wantedRange.lineStart,wantedRange.lineEnd,r.lineStart,r.lineEnd),tagBonus=/^(P|LI|TR|FIGURE|BLOCKQUOTE)$/.test(el.tagName)?250:0;return{el,ids,exact,time,lines,score:exact*100000+time+lines*1000+tagBonus-Math.min(ids.length,200)};}).filter(x=>x.exact||x.time||x.lines).sort((a,b)=>b.score-a.score);if(!candidates.length){status.textContent='详细报告中没有找到对应来源绑定';return;}const best=candidates[0];candidates.filter(x=>x.exact&&x.exact===best.exact).slice(0,8).forEach(x=>{x.el.classList.add('fusion-report-match');x.el.style.outline='3px solid #e86d50';x.el.style.backgroundColor='#fff3cf';});best.el.scrollIntoView({behavior:'smooth',block:'center'});const label=Number.isFinite(wantedRange.start)?`${stamp(wantedRange.start)}–${stamp(wantedRange.end)}`:Number.isFinite(wantedRange.lineStart)?`字幕行 ${wantedRange.lineStart}–${wantedRange.lineEnd}`:`${wanted.length} 个来源单元`;status.textContent=`已定位：${label} · 命中 ${best.exact||1} 个来源单元`;}
document.querySelectorAll('[data-node-select]').forEach(el=>el.addEventListener('click',()=>{if(el.dataset.canLocate==='true')locate(el);}));
function setNodeCollapsed(item,collapsed){item.classList.toggle('is-collapsed',collapsed);const button=item.querySelector(':scope>.node-row>[data-node-fold]');button?.setAttribute('aria-expanded',String(!collapsed));button?.setAttribute('title',collapsed?'展开下级节点':'折叠下级节点');}
document.querySelectorAll('[data-node-fold]').forEach(button=>button.addEventListener('click',event=>{event.stopPropagation();const item=button.closest('.tree-item');setNodeCollapsed(item,!item.classList.contains('is-collapsed'));}));
function setView(mode){document.querySelectorAll('[data-tree-view]').forEach(button=>button.classList.toggle('active',button.dataset.treeView===mode));document.querySelectorAll('.tree-item:has(>.children)').forEach(item=>{const level=Number(item.dataset.level)||1;setNodeCollapsed(item,mode==='overview'||(mode==='next'&&level>=2));});}
document.querySelectorAll('[data-tree-view]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.treeView)));
document.querySelectorAll('[data-branch-fold]').forEach(button=>button.addEventListener('click',()=>{const branch=button.closest('.branch'),collapsed=branch.classList.toggle('is-collapsed');button.setAttribute('aria-expanded',String(!collapsed));button.title=collapsed?'展开分支':'折叠分支';}));
const exitFocus=document.getElementById('exit-focus');function focusBranch(target){document.querySelectorAll('.branch').forEach(branch=>branch.hidden=Boolean(target)&&branch!==target);exitFocus.hidden=!target;if(target)target.scrollIntoView({behavior:'smooth',block:'start'});}document.querySelectorAll('[data-branch-focus]').forEach(button=>button.addEventListener('click',()=>focusBranch(button.closest('.branch'))));exitFocus?.addEventListener('click',()=>focusBranch(null));
addEventListener('message',event=>{if(event.data?.type!=='fusion-located')return;status.textContent=event.data.found?`已定位：${event.data.label} · 命中 ${event.data.matchCount} 个来源单元`:'详细报告中没有找到对应来源绑定';});
document.getElementById('clear-match')?.addEventListener('click',()=>{clearMatches();frame?.contentWindow?.postMessage({type:'fusion-clear'},'*');status.textContent='选择左侧节点可定位到报告内容';});
</script>
</body>
</html>"""
    replacements = {
        "__TITLE__": title,
        "__THESIS__": thesis,
        "__ROOT_NOTE__": html.escape(root_note),
        "__LINKED_CLASS__": linked_class,
        "__BRANCHES__": "".join(branch_markup),
        "__SUMMARY__": summary_markup,
        "__REPORT_PANEL__": report_panel,
        "__UNIT_TIMES__": data_json,
    }
    for marker, value in replacements.items():
        document = document.replace(marker, value)
    output.write_text(document, encoding="utf-8")
    return output
