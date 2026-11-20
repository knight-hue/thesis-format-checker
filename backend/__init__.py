# -*- coding: utf-8 -*-
"""后端代码根：接口层、学院注册表、文档契约与各处理层都在这里。

    backend/api.py               HTTP 层与流程编排（uvicorn backend.api:app）
    backend/config.py            全局配置：模型、路径、阈值
    backend/college_registry.py  学院注册表读取 + 规则文件布局（config/rules/）
    backend/document_contract.py document / issues 的校验与取值字面量
    backend/check_jobs.py        本地异步检测任务状态与结果持久化
    backend/parser/              解析层：DOCX → document
    backend/recognition/         结构识别：判定"这一段是什么"
    backend/detection/           检测层：规则引擎 + 语义检查
    backend/fix/                 修复层：把 issues 应用到 document
    backend/write_back/          写回层：document → 新 DOCX
    backend/rag/                 知识库、问答、会话记忆、文字改写
    backend/docx_common/         跨层共用的 DOCX 事实

包内一律用绝对导入（`from backend.config import ...`）；`local_settings.py` 仍放在
仓库根，由 `backend/config.py` 按本地约定读取。分层与依赖方向见 README「后端分层」。
"""
