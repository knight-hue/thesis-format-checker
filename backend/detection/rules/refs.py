"""参考文献与致谢检查（§7：标题、条目编号/字体/顶格、顺序编码制；§8：致谢）。"""
import re

# 正文引用标记（含组合区间）：
# - 单引用：“［1］”“[1]”（全角/半角方括号都认，规范里条目序号就写作全角）；
# - 区间引用：“［1-2］”“[18-19]”——一次引用多篇。实测 5 份样张用了区间引用
#   （张权辉 [18-19]/[28-30]、崔镭明 [1-2]/[3-4]、王福全 [1-2] 等）：漏解析会
#   当选不到号，报出“第 18 处引用的序号是［20］”这类假错。
_CITATION_RE = re.compile(r"[［\[]\s*(\d{1,3})\s*[］\]]")
_CITATION_RANGE_RE = re.compile(r"[［\[]\s*(\d{1,3})\s*[-–—]\s*(\d{1,3})\s*[］\]]")

# 区间端点校验：文献表一般几十条；超过 99 或倒序的“区间”不是引用，不认。
_CITATION_RANGE_MAX = 99

# 引用顺序扫描范围：除“参考文献表”自身（条目/续行/标题）之外的所有文字块。
# 实测教训：钟浩林 [20] 首次出现在图注块（figure_caption）、程昊男 [23] 在公式
# 段落（formula）的说明文字里——只扫正文类块会把这两种当“缺号”报假错；表格
# 单元格里的引用（“方法[3]”）同属正文内容。文献清单自身 [n] 是清单编号，
# 不参与引用顺序比对（见 tests/test_rules_wiring 的守护用例）。
_CITATION_SKIP_KINDS = frozenset({
    "reference_entry", "reference_continuation", "reference_title",
})


def _iter_citation_numbers(text):
    """按出现位置产出引用号；区间引用展开为区间内每个号（[18-19] → 18、19）。

    边界说明：逗号列举式（[3, 768, 768]、[0，1]）不解析——正文里的图片尺寸、
    区间记号与引用无法从字符上区分，宁可少认，也不把 768 当成文献引用。
    实测 26 份样张没有用逗号式引用。
    """
    hits = []
    for match in _CITATION_RANGE_RE.finditer(text):
        start, end = int(match.group(1)), int(match.group(2))
        if 1 <= start <= end <= _CITATION_RANGE_MAX:
            hits.extend((match.start(), number) for number in range(start, end + 1))
    for match in _CITATION_RE.finditer(text):
        hits.append((match.start(), int(match.group(1))))
    hits.sort()
    return [number for _, number in hits]


class RefsMixin:
    """参考文献与致谢。"""

    # ====================== 参考文献 ======================
    def _check_reference_citation_order(self, document):
        """§7.6：顺序编码制——按正文中引用的文献出现的先后顺序连续编码。

        判据是“正文里首次出现的引用序号”必须是从 1 开始的连续递增序列：正文先引
        ［2］再引［1］、或［1］［3］［2］这类没按引用顺序编码的写法都会被报出来。
        引用标记含区间引用（[1-2] 展开为 1、2，见 _iter_citation_numbers）；扫描
        范围是除参考文献表自身外的全部文字块（图注/表题/公式段/表格单元格都算
        正文，见 _CITATION_SKIP_KINDS）。正文一处引用都没有时不判。
        """
        # 模板「参考文献采用顺序编码制」（CS 第149段）。软院模板的参考文献部分
        # 只有“正文用宋体五号字”一句，没有这句说明，config 里就没有这个键 → 不查。
        if (self.rules.get("references") or {}).get("numbering_scheme") != "sequential":
            return
        if not any(b.get("kind") == "reference_entry" for b in self._blocks):
            return
        seen = []
        for block in self._blocks:
            if block.get("kind") in _CITATION_SKIP_KINDS:
                continue
            texts = [block.get("text") or ""]
            if block.get("type") == "table":
                texts.extend(cell.get("text") or ""
                             for cell in block.get("cells", []))
            for text in texts:
                for number in _iter_citation_numbers(text):
                    if number not in seen:
                        seen.append(number)
        if len(seen) < 2:
            return
        expected = list(range(1, len(seen) + 1))
        if seen == expected:
            return
        pos = next(i for i, (actual, want) in enumerate(zip(seen, expected))
                   if actual != want)
        self._issue(
            "reference.citation_order", "参考文献", "warning",
            {"type": "document", "index": 0},
            f"第 {pos + 1} 处引用的序号是［{seen[pos]}］",
            f"［{expected[pos]}］",
            "正文引用顺序与参考文献编号顺序不一致",
            suggestion="顺序编码制要求按正文中引用出现的先后顺序连续编码："
                       "调整正文的引用顺序，或按引用顺序重排参考文献编号",
        )

    def _check_reference_title(self, b, ctx):
        """§7.1：标题居中排作为标识（字体字号见 rules 的 references.title）。"""
        rule = self.rules.get("references", {}).get("title", {})
        self._check_style_block(b, rule, "reference.title", "参考文献标题")

    def _check_reference_entry_fonts(self, b, loc):
        rule = self.rules.get("references", {}).get("entry", {})
        self._check_run_fonts(b, "参考文献条目", cjk_font=rule.get("font_name"),
                              size_pt=rule.get("font_size_pt"), size_tol=0.5,
                              loc=loc, font_rule_id="reference.entry_font",
                              size_rule_id="reference.entry_font_size")

    def _check_reference_entry(self, b, ctx):
        rule = self.rules.get("references", {}).get("entry", {})
        loc = self._loc(b)
        text = (b.get("text") or "").strip()
        pattern = rule.get("number_prefix_pattern")
        if pattern and text:
            # 自动编号生成的序号不在文本里：用渲染结果补出来（[%1] + 第 3 条 → [3]）。
            rendered = (b.get("auto_number_text") or "").strip()
            if b.get("auto_number") and not rendered:
                # 编号定义还原不出来：不判“缺序号”，免得把 Word 自动编号报成没写序号
                rendered = None
            else:
                rendered = rendered or ""
            if rendered is not None:
                visible = (rendered + text).strip()
                if not re.match(pattern, visible):
                    # 提示用的「应为」示例取自 rules，但序号按当前条目实际编号展示；
                    # 不能把第 16 条也写成“［1］”。
                    expected = rule.get("number_prefix_expected")
                    number = re.match(r"^\s*(\d{1,3})(?=\s|[.．、,，:：])", visible)
                    if expected and number:
                        expected = re.sub(r"\d+", number.group(1), expected, count=1)
                    self._issue("reference.entry_number", "参考文献", "warning", loc,
                                visible[:20], expected,
                                "参考文献条目应以方括号序号开头")
        if rule.get("end_with_period") and text:
            # 模板原句“参考文献条目结尾：以“．”结束”——全角句点才算；半角“.”不算。
            period = rule.get("period_char", "．")
            if not text.endswith(period):
                self._issue("reference.entry_period", "参考文献", "error", loc,
                            text[-5:], period,
                            f"参考文献条目应以全角句点“{period}”结束")
        self._check_reference_entry_fonts(b, loc)
        # “序号左顶格”只在 rules 配了 indent_tolerance_pt 时才查（软院模板没有
        # “序号左顶格”这句说明，config 里没有该项 → 不查）。
        # 判据是**首行起点**（左缩进 + 首行缩进），不是首行缩进本身：
        # 参考文献常用悬挂缩进（w:left=420 + w:hanging=420），此时首行正好落在
        # 左边线上、序号确实顶格，只比首行缩进会报出 -21 pt 的假错（实测 26 条）。
        indent_tol = rule.get("indent_tolerance_pt")
        start = (b.get("left_indent_pt") or 0.0) + (b.get("first_line_indent_pt") or 0.0)
        if indent_tol is not None and not self._close(start, 0, indent_tol):
            self._issue("reference.entry_indent", "参考文献", "warning", loc,
                        f"{round(start, 2)} pt", "0 pt",
                        "参考文献条目应左顶格（首行起点应在左边线）")

    def _check_reference_continuation(self, b, ctx):
        """§7.1：同一条目换行续写的部分只查字体字号，不查编号与顶格。"""
        self._check_reference_entry_fonts(b, self._loc(b))

    # ====================== 致谢 ======================
    def _check_ack_body(self, b, ctx):
        rule = self.rules.get("acknowledgement", {}).get("body", {})
        loc = self._loc(b)
        self._check_run_fonts(b, "致谢正文", cjk_font=rule.get("font_name"),
                              size_pt=rule.get("font_size_pt"), size_tol=0.5,
                              loc=loc, font_rule_id="ack.body_font",
                              size_rule_id="ack.body_font_size")
