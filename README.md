# 高校论文格式校验与解释系统

把学院本科毕业论文的格式规范编译成**可执行判据**，对 Word 论文做确定性校验、按需修复，并用 RAG 解释「为什么不符合」。

> **关于本仓库**
> 本项目为团队协作项目，本仓库是**本人负责模块的脱敏演示版**。
> 全部数据为合成数据，不含任何真实论文；`config/rules/` 为自行整理的规范条目。
> 本地论文语料（`samples/`）、模型资产（`models/`）、运行时数据（`var/`）均不随仓库分发。

---

## 它解决什么问题

学院模板里写的是自然语言（「正文行距：1.5 倍间距」「图题置于图下方」），
学生拿到后不知道哪些项会被卡、卡在哪一段、依据是模板哪一句。

本系统做三件事：

1. **把规范变成判据** —— 模板原句 → 机器可判的规则号，每条规则绑定原句，报告里每条问题都能回指；
2. **判定不猜** —— 格式类判定零模型调用，逐项比对；
3. **判不了就说判不了** —— 无法判定的条款显式列进 `unchecked`，不静默、不估数。

## 分层

```text
DOCX ──parser──► document ──detection──► issues ──(用户勾选)──► fix
                    ▲                                          │
                    └──────── 原地修改数据块 ◄──────────────────┘
                                                               ▼
                                          write_back ──► 修复后的 DOCX
```

| 层 | 职责 |
| --- | --- |
| `backend/parser/` | DOCX → 统一 `document`（段落、表格、公式、图片、域代码、编号） |
| `backend/recognition/` | 判定「这一段是什么」：词表初判 + 上下文校正 |
| `backend/detection/` | 规则引擎 + 语义检查，输出统一 `issues` |
| `backend/fix/` | 把用户确认的 issue 应用到数据块 |
| `backend/write_back/` | 数据块 → DOCX（原位 patch 或显式重建） |
| `backend/rag/` | 知识库、规范解释、对话编排 |
| `backend/docx_common/` | 被多层共用的 DOCX 事实，只此一份 |

## 本人负责的模块

- **规则引擎**：学院规范编成规则号 + 「规则号 → 模板原句」依据表，JSON 配置驱动、零模型调用；跨 run 合并比较、样式继承链回溯、三值判定（符合 / 不符合 / 未判定）。
- **数据块修复**：把用户确认的问题确定性应用到文档数据块（格式 / 文字 / 结构增删），可修复性由后端静态判定下发。
- **RAG 语义检查**：多学院知识库隔离与检索、规范解释、语义条款判定（确定性 gate + 模型残差）、文本改写、会话管理。
- **对话编排**：正则意图层 → 确定性状态机 → 事实层 → ReAct Agent 兜底。
- **结构识别**：段落类型判定、上下文分区、结构识别表按学院外置。

## 快速开始

### 环境要求

- Python 3.10+（开发环境 3.13）
- Node.js 20.19+ 或 22.12+

### 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

cd frontend
npm ci
cd ..
```

### 配置生成模型（可选）

格式检测**不需要**任何模型或 API Key。只有「规范解释 / 语义条款 / 段落改写」需要：

```powershell
$env:LLM_API_KEY = "<your-key>"
```

不配置时系统进入**降级模式**（见下节）。

### 运行

```powershell
.\start.ps1 -OpenBrowser
```

后端 `http://127.0.0.1:8000`（接口文档 `/docs`），前端 `http://127.0.0.1:5173`。

### 试一下

仓库自带一份**合成样例**（不含任何真实论文内容）：

```powershell
python examples\make_sample.py      # 生成 examples/sample.docx
```

把 `sample.docx` 上传，会检出约 20 条格式问题，覆盖页面设置、标题、正文、图表题注与参考文献。
样例里每一条错误都是脚本故意留的，见 `examples/make_sample.py` 顶部注释。

## 没有 API Key / 没有模型时会怎样

系统不会假装检查过：

| 能力 | 无 AI 时 |
| --- | --- |
| 格式类判定（页面、正文、标题、图表、公式、参考文献） | 照常执行 |
| 确定性语义条款（致谢内容、参考文献格式、列表序号、量和单位、数字用法） | 照常执行 |
| 术语判定 | 确定性 gate 照常跑，词表外的残差跳过 |
| 坐标轴说明 | 跳过，并记入 `unchecked` |
| 规范问答 / 段落改写 | 不可用，前端给出降级提示 |
| 未检查的条款 | 响应中 `breakdown.semantic.llm_enabled=false`，逐条列进 `unchecked` |

**「没查」和「通过」在数据里是两个状态**：`unchecked` 的条款不计入通过，也不会被静默丢弃。

## 设计取舍

**为什么判定不用模型。**
格式判定要的是确定性。字号是不是 12pt，规则一查就知道且永远一致；模型有概率偏差，还说不清依据。模型只用在它真正擅长的两件事上：语义理解与自然语言解释。

**为什么未检必须显式暴露。**
把它藏起来，学生会以为「全查过了」。宁可承认「这条做不到」，也不给一个看起来漂亮但不可信的报告。

**为什么 Agent 只做兜底。**
正则意图 + 确定性状态机覆盖了绝大多数问法，不花 token、不会跑偏。只有「把图的问题按类别整理并标出能自动改的」这类复合请求才路由进 Agent；Agent 失败自动降级回状态机，不能让学生看到报错。

**为什么修复必须由用户勾选。**
检测只出报告。任何改动都要经过用户在结果页勾选后调用 `/api/fix`，系统不存在「顺手帮你改好」的路径。

## 目录

```text
backend/            后端：接口层 + 各处理层
config/
  colleges.json     学院注册表
  rules/            学院规范：规范原文 + 机读规则 + 依据表 + 结构识别表
docs/               设计文档与接口快照
examples/           合成样例生成脚本
frontend/           Vue 3 + TypeScript 前端
scripts/            环境检查、依赖安装、OpenAPI 快照、接口自测、Agent 评测
tests/              单元与集成测试
```

## 测试

```powershell
python -m unittest discover -s tests -t .
```

约 970 条用例，覆盖解析、判定、修复、写回、RAG 守卫与对话编排。
测试不依赖真实论文、API Key 或知识库。

接口契约由 `docs/openapi.json` 固化：

```powershell
python scripts\export_openapi.py --check
```

## 文档

- [`docs/CAPABILITY_SPLIT.md`](docs/CAPABILITY_SPLIT.md) —— 能力切分：哪些能判、哪些判不了
- [`docs/SEMANTIC_THREE_STATE.md`](docs/SEMANTIC_THREE_STATE.md) —— 语义三态契约
- [`docs/RAG_ANSWER_GUARD.md`](docs/RAG_ANSWER_GUARD.md) —— 回答护栏
- [`docs/RAG_ANSWER_SCHEMA.md`](docs/RAG_ANSWER_SCHEMA.md) —— 回答结构契约

## License

见 [LICENSE](LICENSE)。
