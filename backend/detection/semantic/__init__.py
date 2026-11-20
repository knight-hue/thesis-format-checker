# -*- coding: utf-8 -*-
"""语义类规则检查子包。

模块划分：
- rules.py        规则元数据、分组与 issue 构造（纯数据 + 纯构造）
- patterns.py     模式表、候选定位与送审前预筛（正则启发式）
- deterministic.py 确定性检查（零 LLM，全文覆盖）
- checker.py      SemanticChecker 编排（确定性 + 定位规则 RAG 批量判定）

外部统一从 detection.semantic_checker 导入：
    from backend.detection.semantic_checker import SemanticChecker, prefilter_semantic_rule
"""
