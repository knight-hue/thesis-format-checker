# RAG answer schema v2 校验

用途：在训练/评测前检查 JSONL 中的回答记录是否符合 schema v2，避免“字段缺失 + 结论越界”的数据进入训练。

## 文件

- 契约：docs/rag_answer_schema_v2.schema.json
- 校验器：scripts/validate_rag_answer_schema.py

## 用法

python scripts\validate_rag_answer_schema.py path\to\answers.jsonl

退出码：

- 0：全部通过
- 1：存在字段/语义错误
- 2：文件读取或 JSON 解析失败

## 检查项

- conclusion/basis/suggestion 必须是非空字符串
- answer_schema_version 只能是 1.0 或 2.0
- schema 2.0 必须显式给出 can_judge（可以为 null）
- can_judge 必须是 boolean 或 null
- observed/unknown 必须是非空字符串数组
- can_judge=false 时结论不能写“符合/无需修改”等
- unknown 非空时结论不能写“居中/对齐/行距”等对应属性

## 当前边界

- 校验器只查结构矛盾和明显越界，不判断 answer 是否真实正确。
- 它不能替代人工审核，也不能证明模型效果。
- 训练数据还需要单独做证据溯源和人工验收。
