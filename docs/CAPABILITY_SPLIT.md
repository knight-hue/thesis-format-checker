# 能力拆分与固定评测

日期：2026-10-24

## 原则

1.7B 模型只负责“把结构化事实解释成自然语言”，不负责注入拦截、资源名映射、证据核验和同文对齐。

| 能力 | 负责模块 | 当前状态 | 指标 |
| --- | --- | --- | --- |
| 注入拦截 | injection_classifier + answer_guard | 已实现规则分类器 | 3/3 注入题可识别 |
| 资源名映射 | 确定性映射表/数据样本 | 已补 SimSun/SimHei/TimesNewRomanPS | 映射类输出改善 |
| PDF/Word 分层 | 解析器 + prompt | 已区分 | v6/v8 boundary 0/20 |
| 同文对照 | 文本对齐 + 模型总结 | 6 对、12 条 | v8 heldout 3/12 |
| schema/JSON | 模型 + 校验器 | v6/v8 20/20 | schema_valid |
| 历史/错学院/位图/无答案 | 规则 + prompt | 基本能说不能判断 | family checks |
| 语义正确性 | RAG/规则/人工复核 | 未解决 | 人工 rubric |

## 固定评测集

构建脚本：scripts/build_capability_eval.py

输出：local-llm-baseline/distillation-v1/materials/eval-suite-20261024/

共 147 条：

- 20 条 heldout20（唯一真正 heldout）
- 80 条 seen80（训练中见过，只作回归）
- 12 条 same_pair（v9 训练见过；v8 是 heldout）
- 35 条 real_observation（24 Word + 11 PDF，v8 训练见过）

family：

- grounded_qa 40
- historical_version 10
- missing_format 10
- wrong_college_evidence 10
- bitmap_unknown 10
- no_answer 10
- injected_text 10
- same_pair_observation 12
- real_word 24
- real_pdf 11

**注意：147 条不等于 147 条 heldout。真正 heldout 仍是 20 条；其余是固定回归集。**

## 使用方式

1. 每次训练前固定跑 20 heldout + 12 same_pair。
2. 每次结构变更跑 147 条回归。
3. 报告必须分开写 heldout 和 seen，不得合并成一个准确率。
4. 同文配对单独报告，不混入 20 题。

## 教师数据

脚本：scripts/generate_teacher_data.py

支持：

- OpenAI-compatible：DeepSeek/其他强模型
- Ollama：本地 Qwen3-8B GGUF

流程：

1. 读 eval_suite 的 prompt
2. 强模型生成 schema v2 JSON
3. validate_rag_answer_schema 校验
4. 注入题检查显式拒绝
5. 同文题检查 Word/PDF 分离
6. 通过才写入 teacher_accepted.jsonl

未通过的行写入 rejected，不静默丢弃。

## 当前阻塞

- 当前环境没有 THESIS_LLM_API_KEY
- 本地 Qwen3-8B GGUF 通过 Ollama 导入时 manifest 报错
- 因此教师生成脚本已实现，但尚未实际批量生成数据
