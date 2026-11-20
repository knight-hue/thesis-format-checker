"""DOCX 写回（重建）模块。

职责：把解析契约 JSON 复现为新的 DOCX 文档。
写回 = 修复 + 重建，本包只负责后半段（重建），
前半段（把 issue 应用到 JSON 数据块）见 fix 包。

注意：本包不做再导出。若在此写 `from .json_to_docx import json_to_docx`，
同名子模块属性会被函数覆盖，`import write_back.json_to_docx` 拿到的就不再是模块。
调用方一律用完整路径导入：`from write_back.json_to_docx import json_to_docx`。
"""