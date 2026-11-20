"""模板样例文字的写法约定：段末的排版说明括注。

官方模板把排版要求写在被说明那行文字的末尾括注里（“摘要（黑体三号字、居中）”
“三级标题（黑体小4号、居左）”）：这些字不是在写字号就是在写对齐，**不属于被说明的
那行内容**，是模板写给排版的批注。拿正文去比对模板文字的地方都得先剥掉它们，否则
锚点认不出、关键词数被括注里的顿号拆开、按 run 判字号时“小4号”自己会被当成正文内容。

被 `recognition`（判段落类型）与 `detection`（比规则）两层共用，故放在这里；
纯字符串函数，不碰 DOCX，也不引入 python-docx。
"""

import re

# 段末括注（“摘要（黑体三号字、居中）”）参与精确比对会让锚点失效：该行降级成
# 正文/关键词内容，后面的区域状态机跟着走偏。比对模板文字前先剥掉它。
_TRAILING_NOTE_RE = re.compile(r"[（(【\[][^（()）\[\]【】]*[）)】\]]\s*$")


def strip_trailing_note(value):
    """剥掉段末的排版说明括注（模板样例文字：“摘要（黑体三号字、居中）”）。

    规则层（关键词数量/分隔符）也要用同一套口径：括注里的逗号、顿号会把
    模板样例行的关键词数拆成 7 个，报出“关键词数量超出”这种与事实不符的错。
    """
    if not value:
        return value
    stripped = _TRAILING_NOTE_RE.sub("", value).strip()
    return stripped or value


# 排版说明括注里一定出现的字眼（“黑体小4号、居左”“Times New Roman小三号字、加粗，居中”）。
# 段末括注不一定是排版说明——“第3章 结论或论文主体（视个人情况而定）”里的括注就是
# 正文内容，不能当说明剥掉，所以只认带这些字眼的括注。
_STYLE_NOTE_HINT_RE = re.compile(
    r"字体|字号|号字|加粗|粗体|正体|斜体|居中|居左|居右|对齐|行距|行间距|间距"
    r"|缩进|黑体|宋体|楷体|仿宋|times|Times|TNR",
)


def style_note_start(value):
    """段末“排版说明括注”的起始下标；该段末尾不是排版说明时返回 None。

    官方模板的样例行把排版要求写在行末括注里（“三级标题（黑体小4号、居左）”），
    这些字不是在写字号就是在写对齐，与被说明的那行文字不是同一种内容：
    规则层按 run 判字体字号时要跳过它们，否则“小4号”这几个字自己会被当成
    正文内容判字号（模板的标题样例行就会因此被报“标题字号不符合要求”）。
    """
    if not value:
        return None
    match = _TRAILING_NOTE_RE.search(value)
    if not match or not _STYLE_NOTE_HINT_RE.search(match.group()):
        return None
    return match.start()
