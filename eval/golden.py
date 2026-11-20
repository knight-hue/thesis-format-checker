# -*- coding: utf-8 -*-
"""金标准数据结构：注入器声明的"应有问题"。

合成样张的做法：从合规基线论文出发，注入**恰好一个**已知错误，并在注入时
登记金标准条目（rule_id + 定位）。评测时用检测结果与金标准比对算 P/R/F1。

定位方式（两种互补）：
- 规则引擎类 issue 带 index/run/row/col：金标准用 ``text_find``（该注入点所在
  段落/表格的唯一文本片段）+ ``kind``，评测时在解析后的 document 里解析成 index；
- 语义类 issue（location.type == "document"）没有 index：金标准用
  ``text_anchor``（违规片段文本），命中时检测 issue 的可见文本须包含该片段。
"""
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class GoldenIssue:
    """一条金标准：注入器保证文档里存在 rule_id 对应的违规。"""

    rule_id: str
    location: dict = field(default_factory=dict)
    # 注入点所在块/单元格的唯一文本片段（解析成 index 用）；None 表示已直接给出 index
    text_find: Optional[str] = None
    # 期望该块的 kind（如 "body"），限定解析定位时的块类型
    kind: Optional[str] = None
    # 语义类 issue 的命中锚：检测 issue 的可见文本须包含该片段
    text_anchor: Optional[str] = None
    # 语义 issue 的 blocks[0] 也带 index；提供时匹配可用它
    block_index: Optional[int] = None
    severity: str = "error"
    note: str = ""  # 注入说明（人工可读）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SyntheticCase:
    """一个合成样张：源 DOCX 路径 + 注入问题清单（通常 1 条）+ 元信息。"""

    name: str            # 样张名（= 注入规则名或 clean）
    docx_path: str
    golden: list = field(default_factory=list)   # list[GoldenIssue]
    college_id: str = "jlu_cs"
    is_clean: bool = False   # True 表示合规基线样张（期望 0 问题）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "docx_path": self.docx_path,
            "golden": [g.to_dict() for g in self.golden],
            "college_id": self.college_id,
            "is_clean": self.is_clean,
        }


def visible_text_of(issue: dict) -> list:
    """收集一条检测 issue 的全部可见文本片段（用于 text_anchor 匹配）。"""
    texts = []
    for field_name in ("actual", "message", "conclusion", "text_preview"):
        value = issue.get(field_name)
        if isinstance(value, str) and value:
            texts.append(value)
    loc = issue.get("location") or {}
    if isinstance(loc.get("text_preview"), str) and loc["text_preview"]:
        texts.append(loc["text_preview"])
    for block in issue.get("blocks") or []:
        if isinstance(block.get("text"), str) and block["text"]:
            texts.append(block["text"])
    return texts
