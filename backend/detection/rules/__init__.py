"""规则引擎包：把确定性规则检查按职责拆成若干 mixin，再组合成 RuleEngine。

- base.py       常量、块上下文、规则装载、分派表、通用断言
- sections.py   页面设置、页眉与页码
- paragraphs.py 正文/关键词/列表/注释/作者/目录
- captions.py   表格、图片与题注
- formula.py    公式
- refs.py       参考文献与致谢
"""
from .base import RuleEngineBase
from .captions import CaptionsMixin
from .figure_content import FigureContentMixin
from .formula import FormulaMixin
from .paragraphs import ParagraphsMixin
from .refs import RefsMixin
from .sections import SectionsMixin


class RuleEngine(RuleEngineBase, SectionsMixin, ParagraphsMixin,
                 CaptionsMixin, FormulaMixin, RefsMixin, FigureContentMixin):
    """确定性论文格式校验引擎。

    各 mixin 的方法名互不重叠，因此 MRO 顺序不影响分派结果；
    base 放在最前，保证 __init__/check 取自骨架。
    """


__all__ = ["RuleEngine"]
