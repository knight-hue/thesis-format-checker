# -*- coding: utf-8 -*-
"""学院上架流水线（目标 3）的离线工具包。

三件事，按流水线顺序：

1. ``extract``：规范文档（docx）→ 结构化条目 → ``spec_items.json`` +
   ``corpus.txt`` 草案（保持 ``【章 / 节】+ 条目：值`` 的现役格式）；
2. ``coverage``：覆盖率对账——规范文本的**每一句**归宿三选一（有判据 /
   明确不查 / 未检），把金标准的"不宽不窄"机器化；同时做反向完整性检查
   （依据表引用的原句必须在规范文本里找得到）。
3. ``check``：注册表校验 + 知识库状态自检（可直接进 CI）。

红线段（不变）：本包只产**草案与证据**，判据激活必须人审；不依赖任何在线服务
（AI 草案生成在后续工作包，走 ``rag.llm_client`` 同一套 JSON 任务封装）。
"""
from backend.onboarding.corpus import (CorpusDocument, CorpusItem,
                                       build_corpus_text, normalize,
                                       parse_corpus)

__all__ = ["CorpusDocument", "CorpusItem", "build_corpus_text", "normalize",
           "parse_corpus"]
