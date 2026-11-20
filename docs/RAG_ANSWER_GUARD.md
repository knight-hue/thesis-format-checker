# RAG 回答安全 guard

版本：rag-answer-guard-v1

## 目的

RAG 回答目前使用三段式契约 conclusion / basis / suggestion。模型可能把“规范要求”误写成“论文已符合”，把 PDF 资源名当成 Word 设置，或在错学院/注入场景下给出越界结论。guard 在生成后、缓存前做确定性检查，必要时改写为安全回答。

**它不是模型能力提升。** guard 本身不新增字段；schema v2 额外提供 `can_judge/observed/unknown`，guard 会保留这些字段，并在改写时把 `can_judge` 置为 false。

## 接入点

- 新模块：backend/rag/answer_guard.py
- 注入分类器：backend/rag/injection_classifier.py（确定性规则，问题/答案共用）
- 调用点：backend/rag/rag_service.py::RAGService.answer，在 parse_answer 之后、_make_result 之前
- 缓存 key 加入 GUARD_VERSION，旧缓存不会绕过 guard
- 对话历史保存 guard 后的文本，避免不安全的回答进入下一轮上下文

## 当前规则

- 源论文/整篇/全文“符合/合规/合格/通过”
- “无需修改/不必修改”
- 只有规范时却给“符合/一致”结论
- PDF 资源名/实测值与 Word 设置“一致”
- 目标学院不是 jlu_cs，却用 jlu_cs 条款下结论
- 题面注入未显式拒绝，或答案执行“全部合格/微软雅黑/忽略证据”
- 注入分类器识别“忽略规则/管理员更新/强制结论/改字体/越狱/泄露提示”等模式
- observed 只有字段名没有具体值（如 font_size_pt）→ 删除该观测、移入 unknown、can_judge=false 并改写
- 同一段同时出现“未知/未观测”和“符合”
- 结构化字段 `can_judge=false` 但结论写“符合/居中/行距”等
- `unknown` 非空但结论仍写对应属性（如 unknown 含 alignment，结论写居中）

“应符合/需符合/符合以下要求”等要求性表述不会触发。

## 边界

- 只能拦已知模式，不能发现新错误；
- 可能过度阻断，例如本来安全的“历史版本未知”回答；
- 改写后是安全话术，不保证覆盖题目全部要点；
- 不能替代 schema、数据审核或人工验收。

## 对话 Agent 的输出校验

对话层新增的 Agent（`backend/rag/dialog/agent/`）在生成最终答案后再做两步校验：

- 复用同一套方向判定（`backend/rag/explanation_guard.py`）：结论翻转直接阻断；建议方向反转先按定式就地换位修复（「将<要求>调整为<现状>」→「将<现状>调整为<要求>」），换不了再阻断；
- 追溯工具返回：答案里的每个数字与规则号必须出现在本次工具调用结果里，「共 N 条」必须等于某次返回的 `matched_total`。

任一项不通过先让模型纠正一次；仍不通过就丢弃答案，回落到确定性状态机（`backend/rag/dialog/graph.py`），
不把未经核对的内容返回给学生。数字溯源只覆盖阿拉伯数字，中文数字（如“共三条”）不做校验。

## 测试

python -m unittest tests.test_injection_classifier tests.test_observed_schema tests.test_rag_answer_guard tests.test_explanation_guard -v

方向修复回归（115 条 near-miss，仓库外数据，用法见 `docs/PROJECT_STATUS_20261116.md`）：
E2b 复合 67→108、严格 27→71；E3 复合 107→115、严格 72→82，方向反转 27→0、阻断 27→0；
E3b 复合 115→115、严格 55→95，无变化。

OpenAPI 快照（schema v2 增加 optional 字段；guard-only 版本本身不改契约）：

python -m unittest tests.test_openapi_snapshot -v
