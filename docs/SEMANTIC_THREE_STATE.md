# 语义检查三态契约（confirmed / suggested / pending_review）

> 适用版本：`schema_version = 1.0`，`docs/openapi.json` 为机器契约基线。
> 本文解释「为什么是三态」「每个字段什么含义」「统计口径怎么算」，不替代 OpenAPI。
> 最近更新：2026-11-16。

---

## 1. 为什么需要三态

早期实现把「规则命中」和「模型猜测」混在同一个 `issues` 列表里，最严重的问题不是漏检，而是**把不确定的东西说成结论**：

- 模型说「术语未解释」，其实只是词表没收录，模型在猜；
- 无逐段解析时无法验证「缩写首次出现」，却给出确定违规；
- 学生看到 `total_issues: 12`，其中若干条只是猜测，等于用假数据制造焦虑。

三态把「能确定的」「只是建议的」「机器判不了的」在**契约层**分开，让前端、统计、导出各自按状态处理，而不是靠文案暗示。

| 状态 | 含义 | 是否计入问题总数 | 是否影响 `passed` |
| --- | --- | --- | --- |
| `confirmed` | 确定性判定（规则引擎或零 LLM 的确定性语义条款） | 是 | 是 |
| `suggested` | 模型给出、未经人工确认的建议 | 否 | 否 |
| `pending_review` | 规则与模型都无法判定，需要人工核对 | 否 | 否 |

---

## 2. 字段契约

### 2.1 单条问题 `IssueResponse`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `decision` | `confirmed` \| `suggested` \| `pending_review` | 缺省 `confirmed`，兼容没有该字段的旧调用方 |
| `confidence` | `number \| null` | 0–1；**没有校准数据时为 `null`**，不得编造（规则引擎条目恒为 `null`） |
| `evidence` | `object \| null` | 判定依据；**只有语义条目会填**，规则引擎条目为 `null` |

`evidence` 结构：

| 字段 | 说明 |
| --- | --- |
| `scope` | 检查范围：`body` / `zh_abstract` / `en_abstract` / `acknowledgement` |
| `first_occurrence` | 本条是否该范围内首次出现；`null` 表示无法判断 |
| `earlier_occurrences` | 更早出现的位置，最多 3 条（`index` / `kind` / `excerpt`） |
| `rule_hits` | 命中的规则标识，如 `gloss_missing` |
| `reason_code` | 机器可读原因，见下表 |

`reason_code` 取值与含义：

| 值 | 含义 | 典型状态 |
| --- | --- | --- |
| `exempt_by_level` | 词表豁免（官方专名 / D 级术语 / 专名） | `not_applicable` 侧，通常不产生 issue |
| `not_first_in_scope` | 本范围内更早位置已出现，本条非首次 | 不产生 issue |
| `first_occurrence_no_gloss` | 首次出现且无释义 | `confirmed` |
| `context_unavailable` | 无逐段解析，无法回溯原文 | `pending_review` |
| `model_guess_unlisted_term` | 词表未收录，仅模型猜测 | `suggested` / `pending_review` |

### 2.2 响应体 `CheckResponse`

| 字段 | 说明 |
| --- | --- |
| `issues` | **只含 `confirmed`**，计入 `total_issues` |
| `advisory_issues` | **只含 `suggested`**，不计入总数 |
| `pending_review` | 待人工条目，不计入总数；超过 200 条时截断 |
| `unchecked` | 本次**没有执行**的条款及原因（能力缺口），必须展示，不能静默 |
| `breakdown.semantic.confirmed_count` | 语义层 `confirmed` 条数 |
| `breakdown.semantic.pending_review_count` | 实际返回的待人工条数（截断后）；超出部分在 `unchecked` 里说明数量 |

### 2.3 统计口径（D1）

```text
total_issues = len(issues) = 规则引擎 confirmed + 语义 confirmed
passed       = total_issues == 0
suggested    → advisory_issues，不计入 total_issues / passed
pending      → pending_review，不计入 total_issues / passed
unchecked    → 不计入任何计数，单独提示「未检查」
```

规则引擎产出的一律是 `confirmed`；`suggested` 只可能来自模型层。

---

## 3. 判定链

```text
规则引擎（确定性）
   ↓
语义层：确定性 gate（术语策略表 / 局部释义 / 参考文献角标）
   ↓ 残差
模型层（LoRA / 云端 LLM）→ suggested
   ↓ 仍无法判定
人工队列 → pending_review
```

术语三态的关键判定（`backend/detection/semantic/terminology.py`）：

1. 词表豁免 → 不判违规；
2. 范围内更早出现 → 非首次，不判；
3. 首次出现 + 有释义 → 合规；
4. 首次出现 + 无释义 + 核心/已登记术语 → `confirmed` 违规；
5. 首次出现 + 无释义 + **词表未收录** → 不猜，进 `suggested` / `pending_review`；
6. 无逐段解析 → `pending_review`（`context_unavailable`）。

---

## 4. 开关

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `include_semantic` | `true` | 关闭时只跑规则引擎，`breakdown.semantic.enabled=false` |
| `terminology_strict` | `false` | 严格口径：B 级条件术语也要求首次解释 |
| `terminology_b04_domain_exempt` | `false` | 医学等 B04 领域词按论文实际领域豁免 |

三个参数在 `/api/check`、`/api/check-file`、`/api/check-jobs/create` 上语义一致。

---

## 5. 前端约定

- `frontend/src/utils/issue.ts` 的 `issueDecision()` 在字段缺失时按 `confirmed` 处理，与后端缺省值一致；
- 结果页三态总览：确定 / AI 建议 / 待人工 / 未检查；6 张统计卡分别为结论、确定、建议、待人工、未检查、耗时；
- 详情页（`Detail`）只列 `confirmed`，并提示另有建议与待人工条目；
- 只有 `fixable` 的 `confirmed` 条目允许勾选修复；
- 复制/导出时，`suggested` 与 `pending_review` 必须带状态标签，不得呈现为结论。

---

## 6. 示例

```json
{
  "schema_version": "1.0",
  "passed": false,
  "total_issues": 1,
  "issues": [
    {
      "rule_id": "terminology.first_occurrence",
      "decision": "confirmed",
      "confidence": null,
      "evidence": {
        "scope": "body",
        "first_occurrence": true,
        "earlier_occurrences": [],
        "rule_hits": ["gloss_missing"],
        "reason_code": "first_occurrence_no_gloss"
      },
      "suggestion": "首次出现处补中文全称或英文全称"
    }
  ],
  "advisory_issues": [
    {
      "rule_id": "terminology.first_occurrence",
      "decision": "suggested",
      "confidence": null,
      "evidence": {
        "scope": "body",
        "first_occurrence": true,
        "rule_hits": [],
        "reason_code": "model_guess_unlisted_term"
      }
    }
  ],
  "pending_review": [
    {
      "rule_id": "terminology.first_occurrence",
      "decision": "pending_review",
      "confidence": null,
      "evidence": {
        "scope": null,
        "first_occurrence": null,
        "reason_code": "context_unavailable"
      }
    }
  ],
  "unchecked": [
    {"rule_id": "formula_axis", "reason": "坐标轴说明需要 AI 语义判断，当前未启用"}
  ]
}
```

---

## 7. 测试与验证

```powershell
python -m pytest tests/test_semantic_three_state_contract.py -q
python -m pytest tests/test_terminology_runtime.py tests/test_terminology_gate.py -q
python scripts/export_openapi.py --check      # 契约快照必须与实现一致
```

前端：`npm run typecheck`、`npm run build`；真实后端联调用 `python scripts/smoke_api.py`。

---

## 8. 已知限制

- `confidence` 目前恒为 `null`，没有校准过的概率就不写数字；
- 模型层 accuracy 约 33%（离线回放），因此只产出 `suggested`；
- `pending_review` 截断上限 200 条，超出部分只在 `unchecked` 里提示数量；
- `unchecked` 的 `reason` 是给人看的文案，不做机器解析。
