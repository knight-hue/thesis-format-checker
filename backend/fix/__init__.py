"""修复层：把检测出的 issue 应用到解析契约的 JSON 数据块上。

职责：
    fix_blocks.fix_document —— 执行修复（格式修复 / 文字改写 / 结构增删）。
    fixability.annotate_issues —— 判定每条 issue 是否可自动修复并标注原因。
    applied_match.fixed_issue_indexes —— 把写回明细与提交的修复项配对，
    算出真正被修复的是哪几条 issue（供下载后同步前端列表与会话用）。

依赖方向（自下而上）：
    detection.numbering、parser.formula、write_back.json_to_docx
    rag.text_rewrite_service（仅文本改写时惰性调用）。

对外常量：
    IMAGE_RULE_IDS —— 需要图片二进制才能修复的规则集（api 层据此注入图片）。
"""
from ._core import IMAGE_RULE_IDS

__all__ = ["IMAGE_RULE_IDS"]
