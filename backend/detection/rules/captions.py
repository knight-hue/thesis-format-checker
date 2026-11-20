"""图表检查（§5.3~§5.5）：表题/图题的位置、随文排、编号连续、续页表头与续表标注。"""
import re

from ..numbering import caption_key, first_mention_index
from .base import CJK_RE, CM_PER_PT, section_text_width_pt

# 归一化后的题注编号（caption_key 的产物）：前缀 + 章号 + 序号。
_CAPTION_KEY_PARTS_RE = re.compile(r"^([图表])(\d+)-(\d+)$")

# 续表标注起点与表格左边线的允许偏差（磅）。两边取的分别是段落缩进与表格网格宽度，
# 网格宽度取整、缩进用字符单位都会带来 1~2 磅的零头；不到**半个汉字**（小四 12pt
# → 6pt ≈ 0.21cm）的差别人眼看上去就是"对齐"。超过才算没对齐。
# 值放在 rules 的 tables.continued_note.position_tolerance_pt，可按学院调。
_DEFAULT_CONTINUED_NOTE_TOLERANCE_PT = 6.0


def _is_continued_caption(block, prev_block, markers=("续表",)):
    """这条题注是不是“续表”的题注。

    模板要求续表用**独立的“续表”标注**（左上方与表格左边线对齐），不另起表题；
    续表重复的是同一张表的号，所以它既不是“新表”、也不参与依序编号的计数。
    两种写法都算：①题注文字里写了“续表”（学生常写成“（续表）”）；
    ②题注紧挨着一条“续表”标注段。
    """
    text = "".join((block.get("text") or "").split())
    if any(marker and marker in text for marker in markers):
        return True
    return bool(prev_block) and prev_block.get("kind") == "table_continued_note"


def _table_left_edge_pt(table, text_width_pt):
    """表格左边线距版心左边界的位置（磅）；量不准返回 None。

    左起表格就是它的左缩进；居中的表格左边线要按“（版心宽 − 表格宽）/2”右移，
    靠右的按“版心宽 − 表格宽”右移——不知道表格宽度就算不出左边线，不猜。
    """
    indent = table.get("indent_pt")
    if indent is None:
        return None
    width = table.get("width_pt")
    align = table.get("align") or "left"
    if align == "center":
        if width is None:
            return None
        return indent + max(0.0, (text_width_pt - width) / 2.0)
    if align == "right":
        if width is None:
            return None
        return indent + max(0.0, text_width_pt - width)
    return indent


def _paragraph_left_edge_pt(block):
    """段落起点距版心左边界的位置（磅）。"""
    left = block.get("left_indent_pt") or 0.0
    first = block.get("first_line_indent_pt") or 0.0
    return left + max(0.0, first)


class CaptionsMixin:
    """表格、图片及其题注。"""

    # ====================== 题注位置 ======================
    def _caption_position(self, section):
        """题注应在图/表的哪一侧，取自 rules 的 <section>.caption.position。

        缺省值与规范一致：表题在上方、图题在下方。
        """
        default = "above" if section == "tables" else "below"
        caption = self.rules.get(section, {}).get("caption") or {}
        value = caption.get("position")
        return value if value in ("above", "below") else default

    def _extra_caption_position(self, b, ctx, kind):
        section = "tables" if kind == "table" else "figures"
        label = "表" if kind == "table" else "图"
        side = self._caption_position(section)
        if kind == "figure" and side == "below":
            self._check_figure_caption_side(b, ctx, label)
            return
        neighbor_type = "table" if kind == "table" else "image"
        noun = "表格" if kind == "table" else "图片"
        where = "上方" if side == "above" else "下方"
        neighbor = ctx.nxt if side == "above" else ctx.prev
        if neighbor is None or neighbor.get("type") != neighbor_type:
            self._issue(f"{kind}.caption_position", label, "error", self._loc(b),
                        f"{label}题位置错误", f"{label}题应在{noun}{where}",
                        f"{label}题应位于{noun}{where}")

    # 图题方位判定的邻域（实测 437 条图注）：往前 3 块找"图在上方"的证据——
    # 437 条里 425 条图注的图紧贴其上一块、7 条隔着子标注行/空段（最远 3 块）；
    # 再往后只敢看 2 块找"题注被排到了图上方"的反证——往后第 3 块的图可能是
    # **邻居图题自己的图**（李培金 图4-7 下方第 3 块是图4-8 的图，图后紧跟
    # 图4-8 题注），窗口一放大会把邻居的图算到自己头上、报出假错；
    # 蒙博 图4-1 是真错例（图在其下一块，落入 2 块窗口）。
    _FIGURE_SIDE_BEFORE = 3
    _FIGURE_SIDE_AFTER = 2

    def _check_figure_caption_side(self, b, ctx, label):
        """§5.3「图号和图题名：排于图的下方」——判方向（结果），不判紧邻。

        题注与图之间常夹着图自己的块：子标注行（a)/b)）、图内文字块（图表的
        "ACC" 等）、空段、行内预览图；"图"也可能就是几张截图拼成的小表格。
        这些都不改变"题注在图下方"这个事实（实测 12 条样张里 11 条属于此类）。
        只有题注上方近处没有图、下方紧跟着图时，才是可证实的"题注排到了图
        上方"（蒙博 图4-1 题注在 image 之前）。两侧都没有图时不报：图可能在
        更远处或拼在表格里，无从证实方向。
        """
        blocks = ctx.blocks
        for block in blocks[max(0, ctx.index - self._FIGURE_SIDE_BEFORE):ctx.index]:
            if block.get("type") == "image":
                return
        end = min(len(blocks), ctx.index + 1 + self._FIGURE_SIDE_AFTER)
        for block in blocks[ctx.index + 1:end]:
            if block.get("type") == "image":
                self._issue("figure.caption_position", label, "error",
                            self._loc(b), f"{label}题位于图片上方",
                            f"{label}题应在图片下方",
                            f"{label}题应位于图片下方")
                return

    def _extra_table_caption_position(self, b, ctx):
        self._extra_caption_position(b, ctx, "table")
        self._check_continued_note_in_caption(b, ctx)
        self._check_caption_number_continuity(b, ctx, "table")

    def _extra_figure_caption_position(self, b, ctx):
        self._extra_caption_position(b, ctx, "figure")
        self._check_caption_number_continuity(b, ctx, "figure")

    def _check_caption_number_continuity(self, b, ctx, kind):
        """§5.1：图、表编号按论文章节划分、用阿拉伯数字依序连续编号。

        判的是“同一章号内依序连续”：序号 = 本文档里同章号已有题注数 + 1，所以
        表1-1 → 表1-3（跳号）、表1-1 → 表1-1（重号）、表1-2 → 表1-1（颠倒）都会被
        报出来。题注自己写的章号与它所在章是否一致不在这里判：附录里的表常按另一
        套章号排（表1-1 排在附录中），拿正文的章号去套会冤枉人。
        编号头认不出来（如写成“图1.1”）时不判——那种排版由题注编号格式那条报。
        规则编号复用 table.caption_number / figure.caption_number：说的都是“题注
        编号与应有的不一致”，自动修复也走同一条改写题注编号的通道。
        """
        if not self.rules.get("tables" if kind == "table" else "figures", {}).get(
                "check_continuity"):
            return
        actual = caption_key(b.get("text"), kind)
        if not actual:
            return
        if kind == "table" and _is_continued_caption(b, ctx.prev,
                                                     self._continued_markers()):
            # 续表重复的是同一张表的号，不是“新表”：不参与计数、不报重号。
            return
        expected = self._expected_caption_number(ctx, kind, actual)
        if expected is None or actual == expected:
            return
        label = "表" if kind == "table" else "图"
        head = _CAPTION_KEY_PARTS_RE.match(actual)
        first_break = (self._first_caption_break(ctx, kind, head.group(2))
                       if head is not None else None)
        if first_break is None:
            message = f"{label}题编号不连续"
            suggestion = (f"{label}题应按“章号-序号”依序编号，此处应为"
                          f"“{expected}”（并同步修改正文中的引用）")
        else:
            # 级联：前面已有一处断号，这一条只是跟着错位。逐条改成“应为…”会把
            # 根因掩盖成第 N 处错，所以指向第一处断号（这是模板要的那个结论）。
            message = f"{label}题编号不连续（受前一处断号影响）"
            suggestion = (f"先修正文第 {first_break + 1} 段附近的第一处断号，"
                          "后面的编号会随之归位；本条不必单独改")
        self._issue(f"{kind}.caption_number", label, "error", self._loc(b),
                    actual, expected, message, suggestion=suggestion)

    def _expected_caption_number(self, ctx, kind, actual):
        """同一章号内依序编号时该题注应有的编号，如 表1-3 -> 表1-2。"""
        head = _CAPTION_KEY_PARTS_RE.match(actual)
        if head is None:
            return None
        prefix, chapter = head.group(1), head.group(2)
        caption_kind = "table_caption" if kind == "table" else "figure_caption"
        markers = self._continued_markers()
        seq = 0
        for index, block in enumerate(ctx.blocks[:ctx.index]):
            if block.get("kind") != caption_kind:
                continue
            if kind == "table" and _is_continued_caption(
                    block, ctx.blocks[index - 1] if index else None, markers):
                continue
            other = _CAPTION_KEY_PARTS_RE.match(caption_key(block.get("text"), kind) or "")
            if other is not None and other.group(2) == chapter:
                seq += 1
        return f"{prefix}{chapter}-{seq + 1}"

    def _continued_markers(self):
        """“续表”标注的写法，取自 rules 的 tables.continued_note.prefix。"""
        note = self.rules.get("tables", {}).get("continued_note") or {}
        markers = tuple(m for m in (note.get("prefix") or ("续表",)) if m)
        return markers or ("续表",)

    def _first_caption_break(self, ctx, kind, chapter):
        """同一章内第一处断号所在块的下标；没有断号返回 None。"""
        caption_kind = "table_caption" if kind == "table" else "figure_caption"
        markers = self._continued_markers()
        seq = 0
        for index, block in enumerate(ctx.blocks[:ctx.index]):
            if block.get("kind") != caption_kind:
                continue
            if kind == "table" and _is_continued_caption(
                    block, ctx.blocks[index - 1] if index else None, markers):
                continue
            key = caption_key(block.get("text"), kind)
            head = _CAPTION_KEY_PARTS_RE.match(key or "")
            if head is None or head.group(2) != chapter:
                continue
            seq += 1
            if key != f"{head.group(1)}{chapter}-{seq}":
                return index
        return None

    def _check_continued_note_in_caption(self, b, ctx):
        """§5.4：续表标注要“在续表左上方与表格左边线对齐位置注明”。

        把“（续表）”写进表题里，标注就不在“左上方”单独成行了，还会让依序编号
        把它当成新的一张表。按原句指出来，改法是把标注从表题里移出来。
        """
        note = self.rules.get("tables", {}).get("continued_note") or {}
        if not note.get("check_caption_marker", True):
            return
        text = b.get("text") or ""
        marker = next((m for m in self._continued_markers() if m in text), None)
        if marker is None:
            return
        self._issue(
            "table.continued_note_in_caption", "表", "warning", self._loc(b),
            f"续表标注写进了表题：{text.strip()[:24]}",
            f"单独一行“{marker}”，置于续表左上方与表格左边线对齐",
            "续表标注应单独成行，不写在表题里",
            f"把“{marker}”从表题里删掉，另起一段只写“{marker}”，"
            "置于续表左上方并与表格左边线对齐",
        )

    # ====================== 表格 ======================
    def _check_table_block(self, b, ctx):
        prev, nxt = ctx.prev, ctx.nxt
        loc = {"type": "table", "index": b.get("index", 0)}
        # “表格上方应有表题”这一项已撤销：模板只写了“表号和表名**排于表的上方**”
        # （排在哪一侧），没有一句“每张表都必须有表题”。按“只认说明句”的尺子，
        # 缺表题的判断是推出来的，不是原句，故连同 fix 层的补题注通道一并删除。
        rule = self.rules.get("tables", {})
        cell_ea_font = rule.get("cell_east_asia_font") or rule.get("cell_font_name")
        cell_size = rule.get("cell_font_size_pt")
        for cell in b.get("cells", []):
            cloc = {"type": "table_cell", "index": b.get("index", 0),
                    "row": cell.get("row"), "col": cell.get("col")}
            # 按 **run** 判，不按“单元格代表值”判：一个单元格里常混着中文 run 与
            # 西文 run，而 font_name / east_asia_font / font_size_pt 三个字段取的是
            # 第一个有文字的 run。实测有单元格第一个 run 是中文（没有西文字体设置）、
            # 含 "id" 的那个 run 写明 Times New Roman，按代表值会报“表内西文字体是宋体”。
            # 这里逐 run 只看它自己渲染的那些字符；纯空白 run 跳过（不渲染任何字符）。
            runs = [r for r in (cell.get("runs") or [])
                    if (r.get("text") or "").strip()]
            wrong_font_ea = wrong_size = None
            for run in runs:
                run_text = run.get("text") or ""
                if cell_ea_font and CJK_RE.search(run_text) and wrong_font_ea is None:
                    if not self._font_eq(run.get("east_asia_font"), cell_ea_font):
                        wrong_font_ea = run.get("east_asia_font")
                size = run.get("font_size_pt")
                if cell_size is not None and size is not None and wrong_size is None \
                        and not self._close(size, cell_size, 0.5):
                    wrong_size = size
            # 一个单元格里同一种问题只报一条（改前是按单元格代表值报一条，
            # 逐 run 判之后若直接逐条报，同一个格会被报 3 次，条数虚增 288）。
            if wrong_font_ea is not None:
                self._issue("table.cell_east_asia_font", "表", "error", cloc,
                            wrong_font_ea, cell_ea_font, "表内中文字体不符合要求",
                            f"请将表内中文字体修改为 {cell_ea_font}")
            if wrong_size is not None:
                self._issue("table.cell_font_size", "表", "error", cloc,
                            f"{wrong_size} pt", f"{cell_size} pt", "表内字号不符合要求")
            # 解析结果里没有 run 明细时（老契约/构造的文档）退回单元格代表值
            if not runs:
                text = cell.get("text") or ""
                if cell_ea_font and CJK_RE.search(text):
                    actual = cell.get("east_asia_font")
                    if not self._font_eq(actual, cell_ea_font):
                        self._issue("table.cell_east_asia_font", "表", "error", cloc,
                                    actual, cell_ea_font, "表内中文字体不符合要求",
                                    f"请将表内中文字体修改为 {cell_ea_font}")
                if cell_size is not None and cell.get("font_size_pt") is not None \
                        and not self._close(cell["font_size_pt"], cell_size, 0.5):
                    self._issue("table.cell_font_size", "表", "error", cloc,
                                f'{cell["font_size_pt"]} pt', f"{cell_size} pt",
                                "表内字号不符合要求")

        if prev is not None and prev.get("kind") == "table_caption":
            self._check_placement(prev, "table", b.get("index", 0), loc)
        # §5.4：带“续表”标注的表格必须是重复了标题行的续表
        if prev is not None and prev.get("kind") == "table_continued_note" \
                and not b.get("header_repeat") \
                and not self._continued_header_repeated(b, ctx):
            self._issue("table.continued_note_header", "表", "error", loc,
                        "续表首行非表头行", "续表应重复排出表头",
                        "标注了“续表”的表格未重复排出表头",
                        suggestion="把上表的表头行抄进续表首行，或在续表上设置“重复标题行”")
        self._check_continued_header(b, loc)

    def _continued_header_repeated(self, table, ctx):
        """续表是否已把表头“重复排出”（§5.4 的字面结果，不限定实现手段）。

        ① 续表首行与上一张表的表头行逐格一致：学生手工把表头抄了进来，
           视觉上表头照常重复排出（实测张心宇、白皓天，末列为"是否允许为空"
           /"是否允许 为空"这类空格差异要归一）；
        ② 两列表是"字段｜值"键值式，版面上没有可重复的列表头带，续表从
           后续字段接着排没有视觉缺失（实测高绪哲的用例表）；
        ③ 其余（源表有 ≥3 列的列表头带、续表却从数据行开始）→ 未重复排出。

        已知边界：真正的两列数据表（带表头行）若未排表头、从数据行续排，也会
        被②放过——语料里所有两列续表都是键值式（4 张全属高绪哲用例表），
        尚无反例；方向是按"不冤枉"宽放，日后出现实例需要重新量。
        """
        first = self._row_text(table, 0)
        if not first:
            return False
        source = None
        for block in reversed(ctx.blocks[:ctx.index]):
            if block.get("type") == "table":
                source = block
                break
        if source is None:
            return False
        if first == self._row_text(source, 0):
            return True
        columns = source.get("n_cols") or 0
        return 0 < columns <= 2 and (table.get("n_cols") or 0) <= 2

    @staticmethod
    def _row_text(table, row):
        cells = [c for c in (table.get("cells") or []) if c.get("row") == row]
        cells.sort(key=lambda c: c.get("col") or 0)
        return "".join("".join((c.get("text") or "").split()) for c in cells)

    # ====================== 图片 ======================
    def _check_image_block(self, b, ctx):
        # 行内图形（与文字同段：公式/符号的预览图）不是插图，不查图题位置。
        if b.get("inline"):
            return
        # “图片下方应有图题”这一项已撤销：模板只写了“图号和图题名**排于图的下方**”
        # （排在哪一侧），没有一句“每张图都必须有图题”，故只保留位置检查。
        nxt = ctx.nxt
        if nxt is not None and nxt.get("kind") == "figure_caption":
            self._check_placement(nxt, "figure", b.get("index", 0),
                                  {"type": "image", "index": b.get("index", 0)})

    # ====================== 图表位置与续页 ======================
    def _check_placement(self, caption, kind, block_index, loc):
        """图/表应“随文排”：出现在正文首次提及之后。"""
        cfg = self.rules.get("tables" if kind == "table" else "figures", {})
        if not cfg.get("check_placement"):
            return
        key = caption_key(caption.get("text"), kind) if caption else None
        if not key:
            return
        mention = first_mention_index(self._blocks, key)
        if mention is None or block_index > mention:
            return
        label = "表" if kind == "table" else "图"
        self._issue(
            f"{kind}.placement", label, "error", loc,
            f"位于正文第 {mention + 1} 段之前",
            "应随文排在相关文字说明之后",
            f"{label}应随文排：先出现文字说明，再放置{label}",
            f"将{label}及其题注移动到首次提及该编号的正文段之后",
        )

    def _check_continued_header(self, b, loc):
        """§5.4：表格转页时表头应重复（<w:tblHeader/>）并注明“续表”。

        规范只规定“转页时”，没有行数阈值之类的附加条件，所以这里只按表格是否会
        超出版心判断：行高齐备时按实测量算（确知转页，报 error）；行高缺失时只能
        按每行 0.8 cm 估算（报 warning，不把估算值当确定违规）。
        """
        cfg = self.rules.get("tables", {})
        if b.get("header_repeat"):
            return
        cells = b.get("cells", [])
        n_rows = (max(c.get("row", 0) for c in cells) + 1) if cells else 0
        if not n_rows:
            return
        heights = [h for h in (b.get("row_heights_cm") or []) if h]
        measured = len(heights) == n_rows
        est_height = sum(heights) if measured else n_rows * 0.8
        usable = self._page_usable_height_cm()
        if est_height <= usable:
            return
        note = cfg.get("continued_note", {})
        prefix = (note.get("prefix") or ["续表"])[0]
        source = "表格行高合计" if measured else "按每行 0.8 cm 估算的表格高度"
        self._issue("table.continued_header", "表", "error" if measured else "warning", loc,
                    "未设置重复标题行", f"转页时应重复表头并注明“{prefix}”",
                    f"{source} {est_height:.1f} cm 超过版心可用高度 {usable:.1f} cm，"
                    f"转页时表头应重复并在续表注明“{prefix}”",
                    f"在表格首行开启“重复标题行”，并在续表左上方注明“{prefix}”")

    def _check_continued_note(self, b, ctx):
        """§5.4：续表标注要"在续表左上方与表格左边线对齐位置"（模板原句）。

        判据就是模板说的那件事：标注段落的起点横坐标 == 它所标的那张表的左边线横坐标。
        - 段落起点 = 左缩进 + 首行缩进（居中/靠右排版不可能与表格左边线对齐，直接报）；
        - 表格左边线 = 表格左缩进 + 居中/靠右时的平移量（表格宽度取 tblGrid 合计）；
        - 版心宽度 = 页面宽 − 左右页边距；这些量任何一个取不到就跳过（宁可漏检不猜）。
        "左上方"里的"上方"不在本函数：章节标题紧贴表格、重复表头由
        table.continued_note_header / table.continued_header 管。
        前缀也不在这里查：分类层只把已以"续表"开头的段落标成 table_continued_note，
        缺前缀的段落根本到不了这里，在此判前缀是永不成立的检查。
        """
        rule = self.rules.get("tables", {}).get("continued_note")
        if not rule:
            return
        table = self._annotated_table(ctx)
        if table is None:
            # 没有它标的表，就没有"表格左边线"可比，位置无从谈起
            return
        loc = self._loc(b)
        align = b.get("alignment")
        if align in ("center", "right"):
            self._issue("table.continued_note_alignment", "表", "warning", loc,
                        f"续表标注{align}排版", "续表标注从表格左边线起排",
                        "续表标注未与表格左边线对齐")
            return
        text_width = section_text_width_pt(self._sections)
        note_left = _paragraph_left_edge_pt(b)
        if text_width is None or note_left is None:
            return
        table_left = _table_left_edge_pt(table, text_width)
        if table_left is None:
            return
        expected = f"续表从表格左边线起排（距版心左边界 {table_left * CM_PER_PT:.2f} cm）"
        tolerance = rule.get("position_tolerance_pt",
                             _DEFAULT_CONTINUED_NOTE_TOLERANCE_PT)
        if abs(note_left - table_left) <= tolerance:
            return
        self._issue("table.continued_note_alignment", "表", "warning", loc,
                    f"续表距版心左边界 {note_left * CM_PER_PT:.2f} cm", expected,
                    "续表标注未与表格左边线对齐")

    @staticmethod
    def _annotated_table(ctx):
        """续表标注所标的那张表：紧跟其后 3 块内的第一张表。

        实测结构是"表格 → 续表标注 → 表格"（转页把一张表拆成两半），中间可能夹
        一个空段；中间出现别的文字就说明后面那张表不是它标的，不认。
        """
        for block in ctx.blocks[ctx.index + 1: ctx.index + 4]:
            if block.get("type") == "table":
                return block
            if (block.get("text") or "").strip():
                return None
        return None
