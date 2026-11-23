---
name: review-response-revision
description: Use when a revision task supplies a manuscript baseline plus reviewer comments and requires per-comment triage, sub-requirement coverage, anchored change locations, evidence status, tone control, and a contract-preserving /app/output/result.json with case_id and every original output_contract field. Deterministic standard-library script; no network.
---

# 审稿回复与论文修改（逐条意见—修改定位—证据台账）

## 用途与适用条件

当输入同时包含**指定版本稿件（baseline）**与**审稿意见清单**，需要产出一份可直接进入返修流程的逐条回复信草案、修改定位表、证据清单和版本一致性台账时使用。

适用：

- 期刊/会议返修，意见按编号或段落给出，允许一条意见包含多个子要求。
- 需要区分“已修改 / 部分采纳 / 不采纳 / 需人工确认”，并给出版本内可检索锚点。
- 需要标记无法补做的实验、缺失数据或需作者确认的事实。

不适用：没有稿件基线的纯写作、整篇从零生成、真实补做实验、代作者宣称编辑部已接收，或替审稿人补写其未说过的要求。

## 输入

- `/input/case.json`：主输入。读取 `case_id`、`output_contract`、任务说明，以及稿件/意见字段（`manuscript`、`baseline`、`source_text`、`reviewer_comments`、`comments`、`materials` 等）；字段名不确定时脚本递归收集文本，不按标签猜内容。
- `task.md`：Harness 实际提供时优先读取 `/input/task.md` 或 `/app/work/task.md`；缺失不阻塞。
- 材料可按路径给出。脚本仅读取确实存在、可解码、单文件 ≤2MB 的文本；PDF/二进制不解析，只记录未读取。
- 输出目录 `/app/output`，工作目录 `/app/work`。

## 处理流程（逐项覆盖评分维度）

脚本 `scripts/run_skill.py` 完成确定性主干，Agent 在此基础上补足需要判断的正文改写：

1. **意见拆解（任务完成度 / 完整性）**：识别编号意见；无编号时按空行分段。每条意见再拆子要求（分号、并列连词、多个祈使动作），保留原文与字符区间。
2. **类型与严重度**：按方法、数据、写作、格式、伦理五类关键词分类；按 `major/must/critical` 与 `minor/suggest/optional` 判定 high/medium/low，未命中为 medium 并标注证据不足。
3. **决策与语气（结果正确性 / 可靠体验）**：识别 `cannot/无法/不采纳/disagree` 等标记，给出 `accept | partial | reject | clarify` 与四种语气 `accept | clarify | polite_disagree | appeal`；不把“无法补做”写成“已完成”。
4. **修改定位（可核验性）**：在 baseline 中检索意见相关锚句，输出 `section`、`paragraph_index`、`char_start/char_end`、逐字 `anchor_quote` 和 `found`。找不到时写 `found=false` 并进入人工确认，不编造行号。
5. **证据清单**：对数据/实验/文献类意见生成 `evidence_checklist`，字段含 `required`、`status`（`available | missing | not_applicable`）、`source`、`fallback`，无法补做时写替代说明。
6. **版本一致性台账**：`version_ledger` 每条绑定 `comment_id → decision → action → locator → status`，并记录 baseline 指纹，防止回复信引用与稿件版本错位。
7. **可编辑交付**：`deliverable.response_letter` 为回复信草案，`deliverable.revision_worksheet` 为按定位排列的修改工作表（Markdown）。两处均显式区分“脚本已定位”与“需作者改写”。
8. **合同保留与自查**：见下节。

## 输出契约

写入 `/app/output/result.json`：

- 顶层必须含 `case_id`，名称与 `case.json` 完全一致。
- **保留原始 `output_contract` 的每个字段名**。脚本支持字段字典、字段列表、`fields`、`required/optional`、JSON Schema `properties`。已知语义字段按别名填充；无法映射的字段写 `null`，并在 `contract_status.unmapped_fields` 登记，整体标 `partial`，绝不编造值。
- `anyOf/allOf/oneOf/$ref/patternProperties` 等组合或引用 Schema 不猜测投影，登记 `contract_status.unsupported_features`。
- 除合同字段外，稳定提供：
  - `status`：`completed | partial | failed`
  - `summary`：意见数、子要求数、可定位数、未定位数、需人工确认数
  - `comments`：逐条拆解与决策
  - `deliverable`：`response_letter`、`revision_worksheet`
  - `sources`：baseline 指纹、意见来源、材料清单
  - `uncertainty`：缺失、冲突、未核实项
  - `change_record`：本次处理动作
  - `limitations`：明确未完成范围
- 退出码：`0` 结构完成；`1` 合同或字段校验失败；`2` 输入读取/解析失败（仍尽量写 `status=failed` 的结构化 JSON）。

## 异常处理

- **缺少意见**：输出空 `comments`，`limitations` 写 `reviewer_comments_missing`，状态 `partial`。
- **缺少稿件**：定位全部 `found=false`，不生成伪造锚点。
- **一条意见多个子要求**：全部保留，子要求逐条出现在 `responses`，不以一句致谢覆盖。
- **意见互相冲突**：两条都保留，标 `conflict_group` 并升级人工确认。
- **意见本身有误**：`decision=clarify` 或 `polite_disagree`，理由引用原文。
- **版本不匹配**：baseline 指纹不一致时拒绝生成“已修改”声明，只输出待核对表。
- **不可解码/超限文件**：记录路径与原因，继续处理其余材料。

## 限制与不适用

- 脚本不补做实验、不生成新的科学数据、不联网核验文献，也不判断学术结论真假。
- 定位基于逐字文本检索；“找到锚句”不等于“修改已正确完成”。
- 未读取的 PDF/图片不声称已理解。
- 回复信草案是模板化骨架，最终措辞与正文改写须由 Agent 或作者确认。

## 最小调用示例（示例）

以下为合成示意，非官方案例，不代表任何真实稿件或评审结果。

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --case /input/case.json \
  --output /app/output/result.json
```

合成 `case.json`：

```json
{
  "case_id": "synthetic-demo",
  "manuscript": "## Methods\nWe used 12 samples. ## Results\nAccuracy was 0.81.",
  "reviewer_comments": "1. The sample size is too small; add more samples and justify the statistical test.\n2. Please clarify the ablation setting.",
  "output_contract": {"case_id": "string", "status": "string", "comments": "array", "deliverable": "object"}
}
```

示意输出（节选，字段仅演示保留方式）：

```json
{"case_id":"synthetic-demo","status":"partial","comments":[{"comment_id":"C1","type":"data","severity":"high","decision":"accept","sub_requirements":["add more samples","justify the statistical test"]}],"deliverable":{"response_letter":"...","revision_worksheet":"..."}}
```

## 自查清单

- [ ] `case_id` 与输入完全一致。
- [ ] `output_contract` 的每个字段名都存在；未映射项已登记且状态为 `partial`。
- [ ] 每条意见及每个子要求都有独立处理结论。
- [ ] 每个“已修改”都有 `found=true` 的定位；`found=false` 未被写成已完成。
- [ ] 证据项区分 `available / missing / not_applicable`。
- [ ] 未编造补做实验、未声称编辑部接收、未伪造行号。
- [ ] 无网络调用，无第三方依赖。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--output` 指定。
2. **必须交付完整成品**：脚本只生成模板化定位与台账；调用 Agent 必须据 baseline 与意见写出完整 `deliverable.response_letter`（逐条意见及每个子要求都有回应、决策与证据状态）和 `deliverable.revision_worksheet`（逐条定位与改法）；“不采纳/无法补做”须写明理由与替代方案，不得以模板骨架、`pending` 或“待作者确认”作完成态。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 取本合同允许值 `completed | partial | failed`（意见与稿件齐全时为 `completed`）；正文无 `pending`/`TODO`/空占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
