# -*- coding: utf-8 -*-

"""写回结果报告：把“写不动”的改动记下来，而不是让整次写回失败。"""


class PatchReport:
    """写回结果报告：输出路径 + 是否真有改动 + 跳过明细。

    W5/W6/W7 之后，“做不到”的改动不再让整次写回失败：写不动的部分登记到
    这里并继续应用其余改动；只有契约层面的错误（原 DOCX 与解析结果对不上、
    分节数量不一致等）才继续抛 ValueError 中止写回。

    明细只有 ``skipped`` 一份：``/api/fix`` 把它转成 ``X-Writeback-Warnings``
    响应头，前端据此提示哪些改动没落到文档里。
    """

    def __init__(self):
        self.skipped = []

    def skip(self, reason, where=None):
        """登记一处被跳过的改动，返回 False 方便调用方直接 return。"""
        message = f"{where}：{reason}" if where else reason
        self.skipped.append(message)
        return False

    def to_dict(self, output, changed):
        return {
            "output": output,
            "changed": bool(changed),
            "skipped": list(self.skipped),
        }
