# RAG 回答 schema v2（兼容加法）

日期：2026-10-24

## 目标

在保持旧三字段可用的前提下，为 RAG 回答增加结构化字段，便于前端、Apifox 和后续训练/评估区分“规范要求 / 实际观测 / 未知 / 是否可判”。

## 字段

| 字段 | 类型 | 默认 | 含义 |
| --- | --- | --- | --- |
| conclusion | string | 必填 | 旧字段，回答结论；保持兼容 |
| basis | string | 必填 | 旧字段，规范依据 |
| suggestion | string | 必填 | 旧字段，修改建议 |
| can_judge | bool \| null | null | true=可判断；false=不能判断；null=模型未提供 |
| observed | string[] | [] | 已实际观测到的信息，例如 PDF 资源名、实测字号、检测结果字段 |
| unknown | string[] | [] | 不能确认的字段/对象，例如 Word 对齐、行距、历史版本 |
| answer_schema_version | string | "1.0" | 1.0=旧三段式；2.0=结构化 JSON |

## 兼容规则

- 旧客户端只读 conclusion/basis/suggestion，不受影响。
- 旧缓存/旧 session 缺少新字段时由 _make_result 默认补齐。
- parse_answer 同时支持：
  - 三段式：返回旧字段 + null/[] + version 1.0
  - 整段 JSON：解析 can_judge/observed/unknown + version 2.0
- guard 改写时保留 observed/unknown，并把 can_judge 置为 false；`can_judge=false` 或 `unknown` 与结论属性冲突时会触发改写。
- RAG 缓存 key 同时带 ANSWER_SCHEMA_VERSION，旧缓存不会缺新字段。
- 新字段全部 optional；OpenAPI 中不是 required。

## 当前边界

- 模型 prompt 保留三段式，并追加可选【结构化字段】JSON；如果模型不输出该行，新字段保持 null/[]。
- 因此本轮是“契约先行”，不是模型效果提升。
- 模型侧要真正产出 can_judge/observed/unknown，还需要下一轮 prompt/训练数据迁移。
- Apifox 已于 2026-10-24 同步：AI 分支 ai/20261024-from-main-rag-answer-schema-v2 合并到 main，endpoint 517301291，含 legacy/structured 两个响应示例。

## 示例

旧三段式：

~~~json
{"conclusion":"一级标题宋体三号。","basis":"一级标题条款。","suggestion":"在 Word 中设置。","can_judge":null,"observed":[],"unknown":[],"answer_schema_version":"1.0"}
~~~

结构化：

~~~json
{
  "can_judge": false,
  "observed": ["PDF资源名=SimSun", "实测=15.02pt"],
  "unknown": ["word_font", "alignment"],
  "conclusion": "只能陈述 PDF 层信息，不能确认原 Word 设置。",
  "basis": "PDF 资源名不是 Word 设置。",
  "suggestion": "补 DOCX 的 rFonts/sz/b/jc。",
  "answer_schema_version": "2.0"
}
~~~
