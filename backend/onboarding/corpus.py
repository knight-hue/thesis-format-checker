# -*- coding: utf-8 -*-
"""规范文本（corpus）的解析与组装——学院上架流水线的共同底座。

规范文本的现役格式（实证 ``config/rules/jlu/cs/jlu_cs.txt``）::

    【第1章 页面设置 / 1.1 页边距】
    左页边距：3厘米
    右页边距：3厘米

即 ``【章 / 节】`` 标题行 + 一行一个"条目：要求"。围绕这个格式的现役依赖：

- 切块：``kb_manager._smart_split`` 的正则就吃 ``【第X章 … / …】``；
- 依据：``<id>_basis.json`` 的"原句"是这里的行（多行用"；"拼接）；
- 上架：新学院的 corpus.txt 必须生成同构文本，覆盖率对账以此为基。

本模块只做文本层结构化（不读磁盘、不查注册表）：解析成条目台账（带行号与
所在章 / 节），或把分组后的条目组装回文本。原文保存在 ``CorpusDocument.text``
里，未改动的文档原样输出即原文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# 标题行：【章 / 节】；斜杠两侧允许空白（与 kb_manager._smart_split 同源的写法）。
HEADER_RE = re.compile(r"^【\s*(?P<chapter>[^/【】]+?)\s*/\s*(?P<section>[^/【】]+?)\s*】$")

# 匹配用归一化：去掉全部空白（含全角空格、制表符）与尾部句点——两边同样处理，
# 消除"用户可见的排版噪音"对原句对位的影响。
_WS_RE = re.compile(r"[\s\u3000\u00a0]+")
_TAIL_PUNCT = "。．."


def normalize(text: str) -> str:
    """匹配用归一化：去全部空白 + 去尾部句点。"""
    cleaned = _WS_RE.sub("", text or "")
    return cleaned.rstrip(_TAIL_PUNCT)


@dataclass
class CorpusItem:
    """一条"条目：要求"。"""

    index: int      # 条目序号（0 起，按文档顺序，不含标题行）
    line_no: int    # 1 起的原文行号
    chapter: str    # 所在章（标题行第一部分）；第一个标题行之前为空串
    section: str    # 所在节；第一个标题行之前为空串
    text: str       # 条目原文（未归一化）

    @property
    def location(self) -> str:
        """给人看的定位：``第1章 页面设置 / 1.1 页边距``（缺章/节时降级）。"""
        return " / ".join(part for part in (self.chapter, self.section) if part) \
            or f"第 {self.line_no} 行"


@dataclass
class CorpusDocument:
    """一份规范文本：原文 + 解析出的条目台账。"""

    text: str
    items: List[CorpusItem] = field(default_factory=list)
    headers: List[str] = field(default_factory=list)  # 原标题行（原样保留）

    def render(self) -> str:
        """原样输出原文（解析不动内容，roundtrip 恒等）。"""
        return self.text


def parse_corpus(text: str) -> CorpusDocument:
    """解析规范文本为条目台账。空行跳过；标题行更新当前章 / 节。"""
    items: List[CorpusItem] = []
    headers: List[str] = []
    chapter = section = ""
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        header = HEADER_RE.match(line)
        if header:
            chapter = header.group("chapter").strip()
            section = header.group("section").strip()
            headers.append(line)
            continue
        items.append(CorpusItem(index=len(items), line_no=line_no,
                                chapter=chapter, section=section, text=line))
    return CorpusDocument(text=text, items=items, headers=headers)


def split_fragments(value: str) -> List[str]:
    """把依据表的一句值拆成原句片段。

    实证：``basis.json`` 的 value 是一句或多句原句用"；"拼接（例："左页边距：
    3厘米；右页边距：3厘米；…"），对位时逐段核对。
    """
    parts = re.split(r"[；;]", value or "")
    return [frag for frag in (normalize(part) for part in parts) if frag]


def build_corpus_text(sections: List[dict]) -> str:
    """组装 corpus 文本（抽取器落草稿用）。

    sections: ``[{"chapter": str, "section": str, "items": [str, ...]}, ...]``；
    ``【章 / 节】`` 标题只在章或节非空时输出，组与组之间空一行。
    """
    blocks: List[str] = []
    for group in sections:
        chapter = str(group.get("chapter") or "").strip()
        title = str(group.get("section") or "").strip()
        lines: List[str] = []
        if chapter and title:
            lines.append(f"【{chapter} / {title}】")
        elif chapter or title:
            # 章/节不完整时以单栏标题呈现（草稿缺陷可见），提取器会同时给警告。
            lines.append(f"【{chapter or title}】")
        lines.extend(str(item).strip() for item in group.get("items", [])
                     if str(item).strip())
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""
