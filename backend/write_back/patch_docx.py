# -*- coding: utf-8 -*-
"""在原 DOCX 包上安全写回；重建仍由 json_to_docx 提供。

写回策略（W5/W6/W7）：
- 能安全定位的改动就地改 XML，未改动的 ZIP 部件逐字节保留；
- 无法安全定位的改动（含字段的 run、合并结构、未知字段等）登记为告警并跳过，
  其余改动照常写入，不再因为一处写不动而整次失败；
- 只有契约层面的错误（原 DOCX 与解析结果对不上、分节数量不一致、
  缺少 word/document.xml、图片部件名冲突）才抛 ValueError。

实现按写回对象拆在 ``write_back/_patch/`` 里，本模块只作对外入口。
"""
from ._patch import PatchReport, patch_docx  # noqa: F401

__all__ = ["PatchReport", "patch_docx"]
