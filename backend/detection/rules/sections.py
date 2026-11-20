# -*- coding: utf-8 -*-
"""页面设置检查：只保留模板里有说明句的那一项——页边距。

模板原文（两份模板都有）：“页面设置：左、右：3 厘米；上、下：2 厘米；页眉、页脚：1.5 厘米。”

**纸张尺寸、页面方向、页眉文字与格式、页脚页码、前置部分不设页眉**这五项已撤销：
模板里只有它们**排出来的样子**（它自己是 A4 纵向、页眉写着“吉林大学 …学院 毕业论文”），
没有任何一句“应该怎么排”的说明。按“只认说明句”的尺子它们不是标准，判定代码一并删除，
不留随时能被误启用的假标准。
"""

# 页边距字段 → 中文标签。
_MARGIN_LABELS = {
    "left_cm": "左", "right_cm": "右",
    "top_cm": "上", "bottom_cm": "下",
    "header_cm": "页眉", "footer_cm": "页脚",
}


class SectionsMixin:
    """页面设置：逐个 section 与 rules["page"]["margins"] 比较。"""

    def _check_sections(self, sections):
        page_rule = self.rules.get("page", {})
        margins = page_rule.get("margins", {})
        tolerance = page_rule.get("tolerance_cm", 0.1)
        for section in sections:
            loc = {"type": "section", "index": section.get("index", 0)}
            for key, label in _MARGIN_LABELS.items():
                expected = margins.get(key)
                actual = section.get(key)
                if expected is None or actual is None:
                    continue
                if not self._close(actual, expected, tolerance):
                    self._issue(f"page.{key}", "页面设置", "error", loc,
                                f"{actual} cm", f"{expected} cm",
                                f"{label}边距不符合要求")
