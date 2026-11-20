# -*- coding: utf-8 -*-
"""`write_back.patch_docx` 的实现，按写回对象拆成子模块。

原文是一个 416 行的单文件，段落/表格/分节/结构变更/打包五件事混在一起，
改一处要读全文。这里只按“写回什么”切分；其中 W5/W6/W7 有意改了失败策略
（个别改不动只登记进报告并跳过，不再让整次写回失败），细则见 ``pipeline``：

- ``report``    写回结果报告（skipped 跳过明细）
- ``keys``      契约字段白名单与 OOXML 常量
- ``diff``      字段/块级差异判断
- ``locate``    契约 blocks ↔ 原 DOCX 正文元素的定位
- ``text``      段落文字的最小改动改写
- ``runs``      run 级格式
- ``paragraph`` 段落级
- ``section``   分节级
- ``table``     表格级
- ``structure`` 结构性变更（新增/重排块）
- ``opc``       OPC 部件（关系表/内容类型表）合并
- ``pipeline``  主流程

对外的入口仍是 ``write_back.patch_docx``（薄壳）。
"""
from .pipeline import patch_docx
from .report import PatchReport

__all__ = ["PatchReport", "patch_docx"]
