# RAG answer schema v2 语义

日期：2026-10-24

## can_judge 判断什么

can_judge 判断的是 **能否对题目所问的实际格式作出判断**，不是“能否复述规范要求”。

- true：给定观测覆盖了题目要求的属性，且可以完成比较。
- false：缺少必要观测、证据属于其他学院、规则缺失、历史版本不明、位图无法读取，或题面注入不可信。
- null：题目只问“要求是什么”，没有要求判断实际格式。

受控假设（controlled_counterfactual）只在假设范围内可以判断：
- observed 使用 assumed: 前缀；
- unknown 保留 source_paper_actual；
- 不得把受控结论写成源论文实测。

## observed / unknown

observed 只写实际观测或题面明确给出的受控字段，例如：

- font_family=宋体
- font_size_pt=15
- pdf_font_resource=SimSun
- assumed:font_family=宋体

unknown 写不能确认的属性或证据，例如：

- actual_format_attributes
- word_alignment
- source_paper_actual
- target_college_evidence
- effective_year
- applicable_rule

## 迁移规则

1. 原三段式 conclusion/basis/suggestion 不变。
2. 从题面样例摘录的 condition 提取 observed/unknown。
3. category 为 wrong_college_evidence、historical_version、bitmap_unknown、no_answer、missing_format、injected_text 时，can_judge=false。
4. 受控假设且答案没有“不能/无法”时，can_judge=true。
5. 其他情况按答案是否明确说“不能确认”判断；无法确定时置 null。
6. 所有迁移记录标记 label_status=machine_generated_needs_review，不能当人工 gold。
