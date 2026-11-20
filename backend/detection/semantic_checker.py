# -*- coding: utf-8 -*-
"""语义类规则检查器（兼容门面）。

实现已拆分到 detection/semantic/ 子包（rules / patterns / deterministic / checker）；
本模块保留原导入路径，供 checker_service / text_rewrite_service / fix.fixability
无感使用：

    from backend.detection.semantic_checker import SemanticChecker, prefilter_semantic_rule
"""
from .semantic.checker import SemanticChecker
from .semantic.patterns import prefilter_semantic_rule
from .semantic.rules import (
    DETERMINISTIC_SEMANTIC_RULES,
    LOCATED_SEMANTIC_RULES,
    MANUAL_ONLY_SEMANTIC_RULES,
    REWRITABLE_SEMANTIC_RULES,
    SEMANTIC_RULES,
)

__all__ = [
    "SemanticChecker",
    "prefilter_semantic_rule",
    "SEMANTIC_RULES",
    "DETERMINISTIC_SEMANTIC_RULES",
    "LOCATED_SEMANTIC_RULES",
    "REWRITABLE_SEMANTIC_RULES",
    "MANUAL_ONLY_SEMANTIC_RULES",
]
