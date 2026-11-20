# -*- coding: utf-8 -*-
"""图内文字检查（模板 §5.3「图内字体字号：宋体五号字」）。

只判**矢量图形里的文字**（形状/文本框，即 `w:txbxContent` 内的段落）
—— 那些字在 docx 里是真字符，字体名与字号都明确写着，判定与正文同级可信，
写回也能直接改（`set_run_font` 对任意 `w:r` 有效）。

**位图**（截图/照片/导出的图）里的字是像素：字体名无处可读，字号也无法从像素可靠
反推（同一墨迹高对应的字体不同、字号可差约 10%；且实测行投影找出的“文字带”是网格线/
方框边框/界面行，不是文字行）。按像素估出的数字报给学生会大面积“报错与事实不符”，
因此**不判**，改由 `figure_image.service.summarize` 产出一段“请人工核对”的说明
（只陈述事实，不给估计值，理由见该模块）。

坐标轴的“说明/单位”（§5.7）不在这里：那是内容判断，由现有 AI 语义规则
`formula_axis` 负责。
"""
from .base import CJK_RE


class FigureContentMixin:
    """图内文字（仅矢量图形）。"""

    def _check_figure_text(self, b, ctx):
        rule = self.rules.get("figures", {}).get("inner_content") or {}
        if not rule.get("enabled", True):
            return
        loc = self._loc(b)
        expected_size = rule.get("font_size_pt")
        expected_ea = rule.get("east_asia_font")
        size_tol = rule.get("font_size_tolerance_pt", 0.5)
        for index, run in enumerate(b.get("runs", [])):
            text = run.get("text") or ""
            # 纯空白 run 不渲染可见字符，其字体字号不影响版式（与正文口径一致）。
            if not text.strip():
                continue
            rloc = {**loc, "run": index}
            if expected_ea and CJK_RE.search(text):
                actual = run.get("east_asia_font")
                if not self._font_eq(actual, expected_ea):
                    # 规则号用 inner_**east_asia**_font：fix 层按规则号后缀判可修复性，
                    # 收尾是 "font" 的名字会命中 `run_both` 分支、把西文/数字也一起
                    # 改成宋体，而模板对图内西文数字没有规定（改动会与事实不符）。
                    self._issue("figure.inner_east_asia_font", "图", "error", rloc,
                                actual, expected_ea, "图内中文字体不符合要求",
                                f"请把图内文字的中文字体改为 {expected_ea}")
            size = run.get("font_size_pt")
            if expected_size is not None and size is not None \
                    and not self._close(size, expected_size, size_tol):
                self._issue("figure.inner_font_size", "图", "error", rloc,
                            f"{size} pt", f"{expected_size} pt", "图内字号不符合要求",
                            f"请把图内文字的字号改为 {expected_size} 磅")
