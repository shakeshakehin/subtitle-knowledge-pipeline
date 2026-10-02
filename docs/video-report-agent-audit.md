# `video-report-agent` 生成与质量保障审计

审计对象：同级目录 `../video-report-agent` 的当前本地版本。这里区分三件容易混淆的事：提示词要求、确定性程序检查、真正的语义质量验证。

## 结论

上游不是“单次聊天补全直接吐出 HTML”，也不是“多个 LLM 分工审稿”。它启动一个 Pi Agent 会话，由同一个 Agent 读取完整字幕、选择内容结构、调用文件工具写入 `report.html`，必要时在同一会话内继续修改。因此一次报告可以包含多次模型消息和工具调用，但没有独立的第二个评审模型。

| 问题 | 代码里实际存在的机制 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| 忠实度 / 完整度 / 遗漏 | Skill 要求读到字幕末尾、保留条件和依据，并在交付前由同一 Agent 自查。见 `video-report-agent/src/video_report_agent/skills/video-report/SKILL.md:8,15,137`。 | 模型生成时收到明确编辑规范。 | 没有程序逐条判断主张是否被字幕支持，也没有独立 LLM 或人工自动判定遗漏。 |
| Token 消耗 | `usage.py:85-119` 逐条读取 `pi.events.jsonl` 的 assistant `message_end`，累计 `totalTokens`、调用数和可取得的价格。 | 可统计已完成的模型消息；有定价时可估算费用。 | 自定义 provider 没有价格时会标为未定价，不能假装成本为 0。 |
| 来源定位 | 报告元素使用 `data-source-units`；Standard 还允许章节时间。见 `SKILL.md:126-128`。`inspect_report.py:59-102` 检查 ID 是否真实存在。 | 能从报告元素回到对应字幕单元，也能发现自造/失效 ID。 | 上游自己明确写着“绑定仅便于定位，不证明解释正确”；合法 ID 也可能绑错语义。 |
| 最终质量检查 | `PiRunner` 先检查 HTML 文件和 `<html>/<body>` 是否完整（`pi.py:426-441`）；`inspect_report.py` 检查溢出、裁切候选、坏图片/脚本、来源 ID；随后截图渲染。 | 能拦住文件缺失、HTML 不完整、明显布局与引用完整性问题。 | 检查结果声明 `semantic_review=not_performed`、`visual_quality=not_scored`（`inspect_report.py:69-71`），所以不等于内容正确或审美达标。 |

## 可选 review 模式

上游支持 `REPORT_REVIEW=1`。开启后，同一个 Agent 写完报告会调用 `inspect_report`，最多做一次集中修订，再检查一次（`pi.py:338-346`）。这能把浏览器检查结果反馈给生成者，但仍不是独立审稿角色，也不新增语义证据。

融合管道此前在 `fusion_video_pipeline/report.py` 中显式使用 `review=False`。它仍会在 Agent 结束后由 Python 运行一次 `inspect_report` 和截图，但不会让 LLM 根据检查结果再改一轮。本次网页化没有改变这个策略，也没有引入第二个 LLM。

## 对四个问题的直接回答

1. **忠诚度 / 完整度 / 遗漏检查：**有提示词内的自查要求，没有独立、可验证的语义检查。
2. **Token 消耗：**上游有事件级统计代码；融合版现已把报告 token、调用数、已知费用和树生成 token、尝试次数、各阶段耗时合并写入 `usage.json` 并展示在网页。
3. **来源定位：**有，依赖 `data-source-units` 和 canonical 字幕 ID；融合版进一步用这些 ID 对应的时间范围完成“树节点 → 详细报告段落”的滚动定位。时间不用于跳转原视频。
4. **最终质量检查：**有 HTML 完整性、渲染、布局和来源 ID 完整性检查；没有自动语义质量结论。

## 调用链

```text
canonical 字幕
  → 同一个 Pi Agent 会话：理解、组织、写 report.html
  → Python 检查 HTML 文件完整性
  → 浏览器检查布局 / 资源 / data-source-units
  → 截图 report.png

可选 REPORT_REVIEW=1：
  同一个 Pi Agent → inspect_report → 最多一次局部修订 → 再 inspect_report
```

这个边界很重要：现有系统有“来源可回查”和“页面可用性检查”，但不能据此声称已经解决幻觉、遗漏或语义忠实度评估。
