"""当前 DOCX 解析契约（1.0）的入参校验。未知扩展字段原样保留。"""
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)


class FieldMark(ContractModel):
    """域代码信息（parser.fields.field_run_map）。

    注意：类名不能叫 ``Field``，否则会遮蔽 ``pydantic.Field``，
    让后续所有 ``Field(...)`` 默认值/约束静默失效（P0 契约回归）。
    """

    type: Optional[str] = None
    code: Optional[str] = None


class Run(ContractModel):
    text: str
    font_name: Optional[str] = None
    east_asia_font: Optional[str] = None
    font_size_pt: Optional[float] = Field(default=None, ge=0)
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    char_spacing_pt: Optional[float] = None
    color_hex: Optional[str] = None
    # 域信息（如 PAGE / REF / SEQ），由 parser.fields 标注。
    field: Optional[FieldMark] = None
    # 所属超链接在段落 hyperlinks 列表中的下标；None 表示不属于超链接。
    hyperlink: Optional[int] = Field(default=None, ge=0)


# P10：alignment 需覆盖 OOXML 的 distribute（分散对齐），
# 否则解析出该值时校验直接失败。
ALIGNMENT = Literal["left", "center", "right", "justify", "distribute"]


class FormulaAsset(ContractModel):
    """公式引用的部件（OLE 预览图 / OLE 二进制），落盘后的索引信息。"""

    old_rid: str
    content_type: Optional[str] = None
    path: str


class Hyperlink(ContractModel):
    text: str
    target: Optional[str] = None
    # 该超链接首个 run 在 runs 中的下标与包含的 run 个数。
    run_index: int = Field(ge=0)
    run_count: int = Field(default=0, ge=0)


class ParagraphBlock(ContractModel):
    # 顶层块由 _index_blocks 赋连续下标；页眉页脚内的段落没有块下标。
    index: Optional[int] = Field(default=None, ge=0)
    type: Literal["paragraph"]
    text: str
    kind: Optional[str] = None
    runs: list[Run] = Field(default_factory=list)
    alignment: Optional[ALIGNMENT] = None
    first_line_indent_pt: Optional[float] = None
    # 段落左侧缩进（磅）：位置类条款（“续表与表格左边线对齐”）要算这一段的起点。
    left_indent_pt: Optional[float] = None
    line_spacing_multiple: Optional[float] = Field(default=None, ge=0)
    line_spacing_pt: Optional[float] = Field(default=None, ge=0)
    space_before_pt: Optional[float] = None
    space_after_pt: Optional[float] = None
    formula_xml: Optional[list[str]] = None
    formula_type: Optional[Literal["omml", "ole", "text"]] = None
    formula_text: Optional[str] = None
    formula_number: Optional[str] = None
    formula_assets: Optional[list[FormulaAsset]] = None
    formula_preview: Optional[dict] = None
    hyperlinks: Optional[list[Hyperlink]] = None
    fields: Optional[list[str]] = None
    # 编号由 Word 自动编号（w:numPr）生成、不在段落文本里。官方模板的标题就是这种：
    # 文本里没有“1.1.1”，编号由 Word 渲染。判定层据此跳过“编号格式”这类看文本的检查。
    auto_number: Optional[bool] = None
    # 自动编号坐标 (numId, ilvl)：写回层据此改 numbering.xml 的编号格式（如把
    # chineseCounting 改为 decimal，让“第一章”显示为“第1章”），保留 Word 自动编号。
    auto_number_ref: Optional[list[int]] = None
    # 段落位于图形内部（形状/文本框的 w:txbxContent）。这类文字属于“图内”，
    # 模板对它的要求是“图内字体字号：宋体五号字”，与正文规则不同，故单列标记。
    figure_text: Optional[bool] = None
    # 自动编号渲染出的可见编号文字（`[%1]` + 计数器 → `[3]`）。编号定义还原不出来
    # 时为空：判定层据此跳过看编号的检查，而不是把“编号不在文本里”当成没编号。
    auto_number_text: Optional[str] = None
    # 段落 pPr 内嵌 sectPr 的分节符空段标记。
    section_break: Optional[bool] = None
    # 有效段落属性里是否带“居中制表位”（段落样式链里定义也算）：WPS 的公式段落
    # 用“居中制表位 + 右对齐”表达“公式居中、编号行末”，判定层据此不再报
    # 这类公式“未居中”（见 detection/rules/formula 的 alignment 判定）。
    center_tab: Optional[bool] = None


class TableCell(ContractModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text: str
    font_name: Optional[str] = None
    east_asia_font: Optional[str] = None
    font_size_pt: Optional[float] = Field(default=None, ge=0)
    runs: list[Run] = Field(default_factory=list)
    grid_span: Optional[int] = Field(default=None, ge=1)
    vmerge: Optional[Literal["restart", "continue"]] = None
    # 单元格显式设置的对齐（未设置则为 None，写回层不会虚构对齐）。
    align: Optional[ALIGNMENT] = None


class TableBlock(ContractModel):
    index: Optional[int] = Field(default=None, ge=0)
    type: Literal["table"]
    cells: list[TableCell] = Field(min_length=1)
    header_repeat: Optional[bool] = None
    # 表格样式名（<w:tblStyle>）；None 表示源表没有表格样式。
    style: Optional[str] = None
    # 表格对齐（<w:tblPr>/<w:jc>）；None 表示源表未设置。
    align: Optional[ALIGNMENT] = None
    col_width_ratio: Optional[list[float]] = None
    # 表格宽度与左缩进（磅）：位置类条款（续表与表格左边线对齐）的判据。
    width_pt: Optional[float] = Field(default=None, gt=0)
    indent_pt: Optional[float] = None
    row_heights_cm: Optional[list[Optional[float]]] = None
    n_rows: Optional[int] = Field(default=None, ge=0)
    n_cols: Optional[int] = Field(default=None, ge=0)
    # recognition.context 打的标记：前置区里的表格（封面信息表等）不是论文表格。
    front_matter: Optional[bool] = None


class ImageBlock(ContractModel):
    index: int = Field(ge=0)
    type: Literal["image"]
    path: Optional[str] = None
    width_inch: Optional[float] = Field(default=None, gt=0)
    height_inch: Optional[float] = Field(default=None, gt=0)
    align: Optional[ALIGNMENT] = None
    # recognition.context 打的标记：位于前置区（承诺书页/封面/目录之前）的图片是版式元素
    # （校徽、整页扫描的承诺书），不是论文插图，检测层不按图表规则查它。
    front_matter: Optional[bool] = None
    # 与文字同处一段的行内图形（MathType/公式的 OLE 预览图、符号图），不是插图：
    # 模板“图号和图题名排于图的下方”针对独立成段的插图，这类不要求图题。
    inline: Optional[bool] = None


class HeaderFooter(ContractModel):
    """页眉/页脚条目（parser.docx_to_json._collect_header_footers）。"""

    kind: Literal["header", "footer"]
    type: Literal["default", "first_page", "even_page"]
    is_linked_to_previous: bool = True
    text: str = ""
    paragraphs: list[ParagraphBlock] = Field(default_factory=list)
    tables: list[TableBlock] = Field(default_factory=list)
    image_count: int = Field(default=0, ge=0)


Block = Annotated[Union[ParagraphBlock, TableBlock, ImageBlock], Field(discriminator="type")]


class Section(ContractModel):
    index: int = Field(ge=0)
    paper_size: Optional[str] = None
    orientation: Optional[Literal["portrait", "landscape"]] = None
    # 页面宽高（厘米）：算“版心宽度”要用（位置类条款，如续表与表格左边线对齐）。
    width_cm: Optional[float] = Field(default=None, gt=0)
    height_cm: Optional[float] = Field(default=None, gt=0)
    left_cm: Optional[float] = Field(default=None, ge=0)
    right_cm: Optional[float] = Field(default=None, ge=0)
    top_cm: Optional[float] = Field(default=None, ge=0)
    bottom_cm: Optional[float] = Field(default=None, ge=0)
    header_cm: Optional[float] = Field(default=None, ge=0)
    footer_cm: Optional[float] = Field(default=None, ge=0)
    headers: Optional[list[HeaderFooter]] = None
    footers: Optional[list[HeaderFooter]] = None


class DocumentContract(ContractModel):
    # README 早期约定仅列 sections/blocks；缺失版本按现有 1.0 处理。
    schema_version: Literal["1.0"] = "1.0"
    sections: list[Section]
    blocks: list[Block]
    file_name: Optional[str] = None

    @model_validator(mode="after")
    def check_indexes(self):
        if [block.index for block in self.blocks] != list(range(len(self.blocks))):
            raise ValueError("blocks.index 必须从 0 连续编号并与块顺序一致")
        if [section.index for section in self.sections] != list(range(len(self.sections))):
            raise ValueError("sections.index 必须从 0 连续编号")
        return self
