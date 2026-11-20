"""规则引擎（兼容入口）。

实现已按职责拆分到 ``detection/rules/`` 子包，本模块只保留转发，
避免既有 ``from detection.rule_engine import RuleEngine`` 调用点失效。

输入：
    document: 论文解析契约 dict（blocks/sections）
    rules:    学院结构化规则 dict（见 config/rules/<学校>/<学院>/*_rules.json）
输出：
    {"schema_version", "college_id", "passed", "total_issues", "issues"}
"""
from .rules import RuleEngine

__all__ = ["RuleEngine"]
