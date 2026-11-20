# thesis-format-checker

高校论文格式规范校验与RAG解释辅助系统

## 项目简介

本系统面向高校毕业论文格式校验场景，采用「规则引擎判定 + RAG智能解释」双层架构。
实现 Word 文档本地解析、结构化格式校验、学院规范智能问答、错误解释与修改建议生成。
论文文件与解析数据保存在本机；规则判定、知识库检索全部本地执行。

生成模型走在线 API（DeepSeek）。本地模型待自研蒸馏模型训练完成后接入，接入点是 `backend/rag/llm_client.create_llm_client`，替换只影响这一处，调用方无需改动。

隐私硬闸在 `backend/rag/llm_client.documents_may_leave_machine`：语义判定与段落改写会把论文正文发给模型，因此默认只允许本机模型端点，非本机端点自动降级为「不跑语义条款」。中期演示期间如需使用在线模型，显式放行（任选其一）：

```powershell
.\start.ps1 -AllowCloudDocs          # 本次启动放行
$env:THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS = "1"   # 或启动前设环境变量
```

也可在未入库的 `local_settings.py` 中写 `ALLOW_CLOUD_LLM_FOR_DOCUMENTS = True`（本机持久生效）。放行期间论文正文会离开本机，请确认演示语料不含不宜外传的内容；演示结束把开关关回（环境变量设 `0` 可覆盖本地配置）。

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 20.19+ 或 22.12+：项目便携版可放在 `.venv\nodejs\`，不存在时 `scripts/setup.ps1` / `start.ps1` 会回退使用系统 PATH 中的 Node.js。
- 嵌入模型资产放在 `models/`（见「说明」一节）：该目录在 `.gitignore` 内，不随仓库分发，需要自行放置。

### 环境检查

环境检查只读取本机状态，不安装依赖、不启动服务，也不会输出 API Key 内容：

```powershell
.\scripts\check_environment.ps1
```

检查结果分为基础模式、在线 AI 和完整 RAG 三档。缺少 API Key、ONNX 模型或知识库只会警告；缺少项目虚拟环境、Python 依赖、Node.js 或前端依赖时，基础模式判定为不可用并返回非零退出码。自动化脚本可使用 `-Json` 获取结构化结果，使用 `-SkipDependencyCheck` 跳过耗时的 `pip check`，或用 `-ProjectRoot <路径>` 检查另一份项目工作树。

### 安装依赖

推荐在项目根目录执行首次安装脚本。它会创建或复用 `.venv`、安装后端依赖、优先使用项目便携 Node 并回退到系统 Node、执行 `npm ci` 和前端生产构建，最后自动运行环境检查；不会下载模型、写入 API Key 或构建知识库。

```powershell
.\scripts\setup.ps1
```

可选参数：

```powershell
.\scripts\setup.ps1 -SkipBackend       # 只准备前端
.\scripts\setup.ps1 -SkipFrontend      # 只准备后端
.\scripts\setup.ps1 -SkipFrontendBuild # 安装前端依赖但跳过生产构建
```

也可以手工安装：

```powershell
# 后端
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# 测试与本地接口自测依赖（pytest / httpx）
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 前端
cd frontend
npm ci
cd ..
```

### 前端开发与校验（TypeScript）

前端已迁移到 TypeScript。安装依赖后，在 `frontend/` 执行：

```powershell
npm run typecheck   # 严格检查 Vue / TypeScript
npm run build       # 生产构建
npm run dev         # 开发，默认连接真实后端
```

现有 `start.ps1` 启动方式和参数不变，`-Build` 仍执行依赖安装与生产构建；类型检查需单独运行。接口模型快照更新后，执行 `npm run gen:api` 生成前端契约类型，再检查和构建。
聊天面板统一使用后端回答，支持结构化问题卡片、后续问题、澄清选项、跳转结果和问题高亮。后端在 `RAGResponse` 中返回 `intent` / `actions` / `blocks` / `suggestions` / `clarify` / `answer_source` / `response_id` / `trace` / `paper_version`；旧响应缺少这些字段时前端按默认值兼容。论文类问题由后端事实层直接回答，不调用生成模型；回答反馈经 `/api/rag/feedback` 只记录评分与元数据。

对话里的每条回答都以检测结果字段为准：卡片带「要求 / 实测 / 建议 / 依据」（依据是模板原句）；列表只展示前 3 条时会说明「共 N 条」，并可用「继续看」往后翻；支持「第 N 条」「下一条 / 上一条」「不是第 2 条，是第 3 条」；「依据哪句模板」「还有哪些没查」分别只回写 issue 的 `basis` 与会话保存的 `unchecked` 清单，没有记录时如实说明，不补写模板文字。`paper_version` 是检测结果指纹，前端据此把基于旧检测结果的消息标为「基于旧检测结果」。Mock 开关、场景选择及目录说明见 [前端 README](frontend/README.md)。

### 生成模型

生成模型走在线 API：依次读取环境变量 `LLM_API_KEY`、`THESIS_LLM_API_KEY` 和未被 Git 跟踪的 `local_settings.py`。未配置 Key 时 `start.ps1` 不再阻断启动，而是进入基础模式；需要确保 AI 能力可用时使用 `-RequireAI`。自研的本地模型训练完成后，在 `backend/rag/llm_client.create_llm_client` 里换成指向本地服务的客户端即可。

### AI 服务不可用时的降级

RAG/AI 服务初始化失败时，检测链路不会中断（`/api/check-file` 不再返回 503）：

- **零 LLM 的确定性语义条款照常执行**：致谢内容、参考文献格式、列表序号、量和单位、数字用法。
- **仅依赖 AI 的定位类条款被跳过**：术语、坐标轴说明。**图/表位置不依赖 AI**，由规则引擎按段落顺序判（另有“相隔过远”一项模板无距离标准，列入“未检”）。响应中 `breakdown.semantic.llm_enabled` 为 `false`，前端结果页会给出降级提示。
- 语义类问题的自动改写依赖 AI：AI 不可用时这些问题标记为 `fixable=false`（附 `fix_reason`），仍会正常列出，不会被静默丢弃。

### 一键启动

```powershell
.\start.ps1 -OpenBrowser      # 在线 API 模型 + 后端 + 前端，并打开浏览器
.\start.ps1 -NoFrontend       # 只启动后端
.\start.ps1 -Build            # 启动前端前先执行 npm ci 与 npm run build
.\start.ps1 -RequireAI        # 未配置 API Key 时拒绝启动
```

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `-BackendPort` | `8000` | 后端端口 |
| `-FrontendPort` | `5173` | 前端端口 |
| `-Build` | 关 | 启动前端前执行 `npm ci` 与 `npm run build` |
| `-NoFrontend` | 关 | 不启动前端 |
| `-NoBackend` | 关 | 不启动后端 |
| `-RequireAI` | 关 | 要求 API Key 已配置，否则停止启动 |
| `-OpenBrowser` | 关 | 启动完成后打开前端页面 |

启动后：后端 `http://127.0.0.1:8000`（接口文档 `/docs`），前端 `http://127.0.0.1:5173`。按 `Ctrl+C` 或关闭窗口会一并停止脚本拉起的进程。

### 接口文档快照

仓库中的 `docs/openapi.json` 是由当前 FastAPI 应用生成的可审查接口快照，可直接
导入 Apifox。修改路由、请求模型或响应模型后，先重新生成快照：

```powershell
.\.venv\Scripts\python.exe scripts\export_openapi.py
```

只检查代码与快照是否一致而不写文件：

```powershell
.\.venv\Scripts\python.exe scripts\export_openapi.py --check
```

快照一致性也包含在后端全量单元测试中。`/docs`、`/redoc` 与
`/openapi.json` 仍用于本地运行时查看；仓库快照用于 Review、版本追踪和导入
Apifox，不应手工编辑。

启动前会检查 `-BackendPort` / `-FrontendPort` 是否已被占用：

- 占用者是**本项目上一次启动遗留的进程**（uvicorn / vite，命令行里带项目根目录）时，脚本会先结束它再继续启动，不会出现"端口被占用导致后端以退出码 3 立即退出"。
- 占用者是**其它程序**时，脚本会打印该进程的 PID、名称与命令行，并提示用 `Stop-Process -Id <PID> -Force` 结束它，或用 `-BackendPort` / `-FrontendPort` 换个端口，然后中止启动。

若上次的窗口是被直接关闭（而非 `Ctrl+C`）或进程被外部杀掉的，脚本无法回收它的子进程，后端/前端就会作为孤儿进程继续占着端口——上面的端口预检正是为此准备的。

#### 启动时序与后台预热

脚本会等到 `/api/health` 返回 200 才打印就绪，通常只需 **2~4 秒**；AI 能力分两阶段在后台线程预热，脚本会分别打印「生成模型服务已就绪」与「知识库已就绪」：

1. **生成模型门面**（秒级）：只构造 OpenAI 兼容客户端，检测链路的语义判定与段落改写靠它；
2. **知识库门面**（约 5~10 秒）：加载嵌入模型（ONNX）。Chroma / BM25 / 词表索引在真正检索时才惰性加载，不占启动时间。

预热期间：

- 规则检测、格式写回、学院列表（`/api/colleges`）完全可用；
- 门面就绪、知识库还没好时，语义检测与改写已经能跑，只有规范问答会提示「知识库正在初始化」；
- 生成模型门面失败时，仅依赖 AI 的语义条款自动跳过（结果页给出降级提示），稍后重启补齐。

`/api/health` 用它报告状态：`llm_status` / `rag_status` 取 `initializing`（加载中）/ `ready`（就绪）/ `failed`（失败，`llm_error` / `rag_error` 给出原因；此时不需要 AI 的功能仍可用，需查看后端日志后重启）。

### 说明

- 嵌入模型（`backend/config.py` 中的 `EMBEDDING_MODEL`，ONNX 格式）的资产放在 `models/` 下，**需要自行放置**：该目录不随仓库分发，程序也不会自动下载，缺失时启动建库会直接报出缺哪个文件、以及可以去哪个仓库取（见 `backend.config.EMBEDDING_ONNX_REPO` / `EMBEDDING_TOKENIZER_REPO`）。
- 会话数据（对话记忆 + 论文检测结果）存在本地 SQLite 文件 `var/data/sessions.db`，无需额外启动任何服务，首次运行自动创建。
- 前端上传默认走异步检测：`/api/check-jobs/create` 返回任务 ID，再用 `/api/check-jobs/status` 查进度、`/api/check-jobs/result` 取结果；`/api/check-file` 是保留的同步兼容入口。
- 任务状态与结果存 `var/data/check_jobs.db`，默认保留 24 小时；浏览器保存任务 ID，超时或刷新后可继续查询，策略用 `THESIS_CHECK_JOB_*` 调整。当前面向单进程：已完成结果可跨重启恢复，重启时未完成的标记为失败；多 worker 需换共享任务队列。
- DOCX 上传默认限制为压缩包 50 MB、内部条目 10000 个、解压后 250 MB；服务会在解析前校验 OOXML 核心部件和 ZIP 完整性。部署时可通过 `THESIS_MAX_UPLOAD_BYTES`、`THESIS_MAX_DOCX_ENTRIES`、`THESIS_MAX_DOCX_UNCOMPRESSED_BYTES` 调整，其中字节数配置使用整数。
- 检测结果按 `rule_id` 聚合：同一条规则报几百处只占一行，`occurrence_count` 是原始出现次数，`variants` 列出不同的实测值（最多 10 种），`location_samples` 列出定位样本（最多 10 个）。
- 排序为“错误在前，同级别里出现次数多的在前”；`total_issues` 保留原始次数，`grouped_issues` / `returned_issues` / `issues_truncated` / `issue_summary` 描述聚合、截断与分类统计。
- 返回条数上限用 `THESIS_MAX_RETURNED_ISSUES` / `THESIS_MAX_ISSUE_LOCATION_SAMPLES` 向下调（契约上限 1000 / 10）；合并组含多个位置时 `fixable` 为 `false`（自动修复只支持单点改写）。
- 每条 issue 都带 `basis`：模板里直接写着这条要求的原句，取自 `config/rules/<学校>/<学院>/<college>_basis.json`（按 `rule_id` 最长前缀匹配），语义层的报告也不例外。语义层自己的判定说明（如“确定性检查：正则匹配”）放在 `basis_detail` 里保留，展示为“判定说明”。依据表与判定配置分开维护，改文案不动判定。

### 学院坐标与新增学院

学院注册表是 `config/colleges.json`：**`college_id` 是不透明主键，学校/学院/专业/模板版本写在字段里**，不要靠拆 id 猜坐标（id 已经渗进知识库目录、缓存键、会话表）。

- `college_id` 只允许小写字母、数字、下划线、连字符，多版模板用 `@版本后缀`（如 `jlu_cs@2024`）；注册表里的 `template_version` 必须与后缀一致。禁中文、空格与 `.`——它要拼进文件名与目录名，收窄字符集顺手堵住路径穿越（`config.COLLEGE_ID_PATTERN`）。
- 规则文件按 `config/rules/<学校>/<学院>/` 两层放（学校目录名 = 注册表的 `school`，学院目录名 = `college`），四份文件都以完整 `college_id` 开头，同校同院的多版本、多专业落在同一个学院目录里，靠文件名区分。这个布局与"按 id 找文件"只由根目录的 `backend/college_registry.py` 一处决定，识别层、检测层、RAG 与测试都调它，不再各自拼路径。
- 必填字段：`school` / `school_name` / `college` / `college_name` / `rule_file`；可选：`major` / `major_name` / `template_version` / `effective_year` / `chunk_size` / `chunk_overlap`。同校同院同专业挂多版模板时，每版一条，靠 `@版本` 后缀区分（坐标完全相同会被守门测试点名）。
- 知识库不在这里配路径：库目录名是"规范文本内容 + 切片参数"的哈希（`backend/rag/kb_store.py`），语料一变键就变，取不到库就是明确提示重建；语料相同的学院共用同一个目录。

新增一个学院（或同院加一个专业/版本）：

1. `config/rules/<学校>/<学院>/<id>.txt`：把该学院模板里的说明句逐条抄成"要求 → 取值"的行（这也是向量库语料）；
2. `config/rules/<学校>/<学院>/<id>_rules.json`：照 `.txt` 填机读判定（照抄现有学院的结构改差异项）；
3. `config/rules/<学校>/<学院>/<id>_basis.json`：`rule_id → 模板原句`，报告里的"模板依据"从这里取；
4. `config/rules/<学校>/<学院>/<id>_structure.json`：结构识别表——只在该院模板的**写法**与默认表不同时才要改（承诺书叫"学术诚信声明"、图题写"Fig."、标题样式叫"章标题"等）。与默认表同值就照抄一份，缺文件会退回默认表；
5. 在 `config/colleges.json` 加一条：`college_id` + 坐标字段（`school` 定学校目录、`college` 定学院目录）+ `rule_file`；
6. `python -m backend.rag.kb_store --status` 看状态、`python -m backend.rag.kb_manager --build <id>` 建库（语料相同则直接复用已有库）；
7. `python -m unittest tests.test_college_registry tests.test_structure_config` 过一遍守门测试。

判定逻辑一行都不用改：按学院启停的条款由 `backend/detection/checker_service.SEMANTIC_RULE_GATES` 按"配置键是否存在"决定，样式类条款由 `config/rules/<学校>/<学院>/<id>_rules.json` 决定。

### 结构识别表

判定分两件事：**"要求是什么"**在 `config/rules/<学校>/<学院>/<college>_rules.json`，**"这一段是什么"**在结构识别表 `config/rules/<学校>/<学院>/<college>_structure.json`（`backend/recognition/structure.py`）。

- 外置的是**词表**：章节锚点（摘要/目录/参考文献/致谢…的别名）、承诺书与声明页标题、封面字段名、题注前缀（图/表/续表）、参考文献条目序号括号。
- **没外置**的是形态类判据：编号数字形态、编号分隔符、目录点线与页码、以及"整句话不是标题"这类句子护栏——它们是解析逻辑而非措辞，外置成字符串只会让写错时更难查。
- 合并语义：**按分组覆盖，分组内的列表/字符集是整组替换**（不是追加）；只想加一个写法时要把原有取值一起写上。`_` 开头的键当注释忽略。
- 校验是**严格**的：未知分组/未知键、锚点指向不存在的 kind、空词表、非法 JSON 都在加载时抛错并指出文件与键——配置写错必须马上看得见，不能静默按默认表跑。
- 默认表 = 吉大两版模板的写法。两院的 `*_structure.json` 目前与默认表同值；换学校时才需要改。
  表里的写法会随真实样张反馈增补（如"中文摘要"、"表格 3.1…"这类学生常见写法），
  增补只影响**识别**、不新增要求——要求句始终只在 `config/rules/<学校>/<学院>/<college>_rules.json` 里。

识别结果由 `tests/test_structure_config.py` 与 `tests/test_misattribution_fixes.py` 守门：改词表必须改结论。

解析完成后服务端会看一眼识别结果：锚点没认出来时记一条日志（`backend/recognition/anomalies.py`，由 `backend/api.py` 在解析后调用）。这条告警不进 `issues`、不影响报告条数——它的用途是异校模板某个锚点没被认出来时能查到根因，而不是悄无声息地让整块区域按正文来量。它用的锚点口径与识别层同一份（`backend/recognition/classification.py` 的 `anchor_kind_of`），否则会对着正常论文报"没认出目录标题"。

另外，目录条目除了"点线 + 页码"启发式，还会认 Word 自动目录的 `toc 1/2/3` 样式（模板里就有这组样式）：手工排版、没有点线的目录也能认出来，不再被当成正文逐条报格式错。

## 技术栈

- **后端**：FastAPI + Uvicorn + Pydantic；DOCX 读写用 python-docx
- **检索**：LangChain + Chroma 向量库；召回为 BM25（`rank-bm25` + `jieba` 分词）与向量的混合检索，以 BM25 为主、向量为辅
- **嵌入**：ONNX Runtime 本地加载 `BAAI/bge-small-zh-v1.5`（`models/bge-small-zh-v1.5-onnx/`，见 `config.EMBEDDING_MODEL` / `EMBEDDING_ONNX_PRECISION`），不依赖 torch
- **生成模型**：调用 DeepSeek `deepseek-chat`（OpenAI 兼容 SDK），见 `backend/rag/llm_client.create_llm_client`
- **会话存储**：SQLite（`var/data/sessions.db`），无额外服务依赖
- **规则配置**：JSON 机读规则 `config/rules/<学校>/<学院>/<college_id>_rules.json`（规则引擎逐项比对）+ 规范原文 `config/rules/<学校>/<学院>/<college_id>.txt`（作为向量库语料）+ 依据表 `<college_id>_basis.json`；学院坐标记在 `config/colleges.json`
- **前端**：Vue 3 + Vue Router + Axios + Element Plus，构建工具 Vite

## 代码结构

### 目录总览

```text
thesis-format-checker/
├── backend/                 后端：接口层 + 各处理层（uvicorn backend.api:app）
│   ├── api.py                 FastAPI 入口：HTTP 层 + 流程编排 + 会话读写
│   ├── config.py              全局配置：模型、路径、阈值、会话库位置
│   ├── document_contract.py   文档契约：document / issues 的校验与取值字面量
│   ├── college_registry.py    学院注册表读取 + 规则文件布局（config/rules/，各层共用）
│   ├── check_jobs.py          本地异步检测任务状态与结果持久化（SQLite）
│   ├── parser/                解析层：DOCX → document
│   ├── recognition/           结构识别：判定“这一段是什么”（词表 + 初判 + 上下文校正）
│   │   ├── structure.py       结构识别表（词表按学院可配，默认值 = 吉大两版模板的写法）
│   │   ├── classification.py  初判：只看单段，认出标题/题注/参考文献条目等
│   │   ├── context.py         按上下文校正：区域状态机、承诺书免检、封面字段、前置区标记
│   │   └── anomalies.py       结构识别可疑信号的告警（只写日志，不参与判定）
│   ├── detection/             检测层：规则引擎 + 语义检查
│   │   ├── rules/             确定性规则子包（骨架 / 页面 / 段落 / 图表 / 公式 / 参考文献）
│   │   └── semantic/          语义检查子包（模式表 / 确定性检查 / 规则元数据 / LLM 编排）
│   ├── fix/                   修复层：把 issues 应用到 document 数据块
│   ├── write_back/            写回层：document → 新 DOCX
│   │   └── _patch/            原位写回子包（定位 / 文本 / run / 段落 / 表格 / 结构 / OPC）
│   ├── rag/                   RAG 层：生成模型门面、知识库、检索问答、对话编排、会话管理、文字改写
│   └── docx_common/           跨层共用的事实（对齐取值 / run 流 / 块级遍历 / 资产路径 / 括注剥离）
├── config/                  学院配置与规范
│   ├── colleges.json         学院注册表：college_id → 学校/学院/专业/模板版本 + 规范文本 + 切片参数
│   └── rules/                学院规范：学校 → 学院两层
│       └── jlu/              吉林大学
│           ├── cs/           计算机科学与技术学院
│           │   ├── jlu_cs.txt             规范原文，作为向量库语料由 backend/rag/kb_manager 切片入库
│           │   ├── jlu_cs_rules.json      机读规则，由 backend/detection/rules 逐项比对
│           │   ├── jlu_cs_basis.json      rule_id → 模板里那句要求的原文（报告中“模板依据”的来源）
│           │   └── jlu_cs_structure.json  结构识别表：管“这一段是什么”（锚点/承诺书/封面字段/题注前缀）
│           └── se/           软件学院同上
├── var/                     运行产物（已 gitignore，全部可删可重建）
│   ├── data/                 会话库 sessions.db、异步任务库 check_jobs.db 与上传文件
│   ├── kb/<语料哈希>/        Chroma 持久化目录；目录名由“规范文本内容 + 切片参数”算出（backend/rag/kb_store.py）
│   └── output/               导出产物
├── models/                  本地模型资产（已 gitignore，不随仓库分发，需自行放置）
│   └── bge-small-zh-v1.5-onnx/  嵌入模型（ONNX，fp32/int8 两套精度）
├── tests/                   测试：分层守护、规则接线、真实样张端到端等（import backend.*）
├── scripts/                 环境检查、setup.ps1 依赖安装、OpenAPI 快照导出
├── docs/openapi.json        接口快照（由 scripts/export_openapi.py 生成与校验）
├── frontend/                Vue3 + Element Plus 前端（见 frontend/README.md）
├── start.ps1                一键启动脚本（后端 + 前端）
├── requirements.txt
├── requirements-dev.txt     测试与接口自测依赖（pytest / httpx）
└── README.md
```

### 后端分层

依赖方向自上而下，不允许反向：

```text
backend/api.py         L2  HTTP 层：参数校验、会话读写、调用下层、组装响应
  ├── detection/       L1  规则引擎与语义检查 ──► parser/（公式识别）
  ├── rag/             L1  知识库 / 问答 / 会话记忆 / 文字改写 ──► detection/（语义规则元数据）
  ├── fix/             L1  修复：issue → document 数据块
  │                        ──► detection/、parser/formula、write_back.json_to_docx
  │                        └─ 仅真正写回时惰性调用 rag/text_rewrite_service
  └── write_back/      L0  原位写回 / 显式重建：document → DOCX

backend/recognition/   地板  结构识别：判定“这一段是什么”（词表 + 初判 + 上下文校正）
                            parser 依赖它；它只依赖 docx_common 的纯文本工具，不 import 任何业务包
backend/docx_common/   地板  跨层公共事实：对齐取值 / run 流 / 资产布局 / 模板括注写法
                            parser、write_back、fix、api、recognition、detection 都依赖它；
                            它只依赖 document_contract
```

`backend/parser` 的第一方依赖是 `backend/docx_common` 与 `backend/recognition`，`backend/write_back` 的只有 `backend/docx_common`；
把地板摊在根目录会让它们看起来像 L2，所以单独成包——准入标准写在 `backend/docx_common/__init__.py`。

约束：

- `backend/parser` / `backend/write_back` 不 import 上层；`detection` 不 import `rag` / `fix`；`rag` 不 import `fix`；任何模块都不 import `api`。
- `backend/detection` 不 import `recognition`：段落类型一律走 `document` 里的 `kind`，剥模板括注走 `backend/docx_common/notes.py`；`write_back` 不 import `parser`。这两条和上面几条由 `tests/test_import_layers.py` 读 AST 断言。
- `backend/recognition` 不读写 DOCX、不产出数据块：它只依赖标准库、`backend/docx_common/notes.py`（剥模板括注的纯文本工具）与 `backend/college_registry.py`（找本学院的结构识别表），可以脱离解析单独测试。
- `backend/fix` 对 `rag` 只有一条边，且写在 `backend/fix/_document.py` 的函数体内（惰性导入），保证 `import fix` 时不加载 rag。
- 跨包共享的常量与白名单放在“事实归属”那一层对外公开，不在 `backend/api.py` 里复制第二份：`REWRITABLE_SEMANTIC_RULES` / `MANUAL_ONLY_SEMANTIC_RULES`（`backend/detection/semantic/rules.py`）、`IMAGE_RULE_IDS` / `_TEXT_LAYER_RULE_IDS`（`backend/fix/_core.py`）。
- `config/rules/*.json` 的每个配置项都必须真的被生产代码读取，代码里硬编码的规范常量（如题注字体）必须与 `config/rules` 对齐；`tests/test_rules_wiring.py` 断言这两条，并用"改配置必须改结论"的对照用例锁住 `tables.caption.position`、`figures.caption.position`、`formula.number_position` 的接线。
- 归属不了单一层的事实（取值表、元素遍历顺序、磁盘布局、模板括注写法）放 `backend/docx_common/`，任何层都不再抄第二份；判断标准是"是否被两个以上层 import"，只有单层用的仍留在该层。

### 各层职责

| 层 | 目录 | 职责 | 关键文件 |
| --- | --- | --- | --- |
| 地板 | `backend/recognition/` | 判定“这一段是什么”：词表 + 初判 + 上下文校正；只依赖 `backend/docx_common/notes.py` | `structure.py`（结构识别表，按学院可配）、`classification.py`（初判）、`context.py`（上下文校正） |
| L0 | `backend/parser/` | DOCX → 统一 `document`，提取结构、字体、表格、公式、图片 | `docx_to_json.py`（入口）、`font_reader.py`、`numbering.py`（还原 Word 自动编号）、`formula.py`、`media.py`、`table_reader.py`、`fields.py` |
| L0 | `backend/write_back/` | `/api/fix` 在原 DOCX 包上安全写回；`/api/rebuild` 显式重建 | `patch_docx.py` → `_patch/`（原位写回：定位 / 文字 / 段落 / 表格 / 节 / 结构 / OPC）、`json_to_docx.py` + `op_*.py`（重建）、`_defaults.py`（缺省值，与 `config/rules` 由测试对齐） |
| L1 | `backend/detection/` | 逐项比对学院规范 + 语义检查，输出统一 `issues` | `checker_service.py`（编排）、`rule_engine.py`（兼容入口）→ `rules/`（`base.py` 分派表 + 各职责 mixin）、`semantic/`（语义检查）、`figure_image/`（位图图内文字：只出未检说明）、`numbering.py`（题注 / 公式编号推导） |
| L1 | `backend/fix/` | 把用户确认的 `issues` 确定性应用到 `document` | `fix_blocks.py`（facade）→ `_document.py`（总入口）→ `_apply.py` / `_text.py` / `_structural.py`；`_content.py`（数据块操作原语，被 `_apply` / `_structural` 共用）、`_core.py`（常量与规则→字段映射）、`fixability.py`（可修复性判定）、`applied_match.py`（写回明细 ↔ 修复项配对） |
| L1 | `backend/rag/` | 知识库构建与检索、规范问答、会话记忆、文字改写 | `llm_service.py`（生成模型门面，检测链路的最小依赖）、`llm_client.py`（客户端构造与请求封装）、`rag_service.py`（知识库门面）、`kb_manager.py`、`compliance.py`、`query_rewriter.py`、`answer.py`、`session_manager.py`（SQLite）、`text_rewrite_service.py`、`dialog/`（意图识别 + 确定性状态机）、`dialog/agent/`（复合请求的 ReAct 编排：只读工具 + 输出溯源校验） |
| L2 | `backend/` | HTTP 接口、异步任务编排与文档契约校验 | `backend/api.py`、`backend/check_jobs.py`、`backend/document_contract.py`：异步检测使用 `/api/check-jobs/create`、`/api/check-jobs/status`、`/api/check-jobs/result`；`/api/check-file` 保留同步兼容；另含修复、重建、RAG、知识库、会话、学院与健康检查接口 |
| 地板 | `backend/docx_common/` | 被多层共用的事实，不含业务判断 | `alignment.py`（对齐取值三向映射）、`runs.py`（run 遍历顺序与字符格式原语）、`blocks.py`（块级遍历，下钻内容控件等包装）、`assets.py`（图片 / 公式资产路径）、`notes.py`（段末排版说明括注的剥离） |

### 数据流与契约

```text
DOCX ──parser──► document ──detection──► issues ──(用户勾选确认)──► fix
                    ▲                                               │
                    └────────── 原地修改数据块 ◄────────────────────┘
                                                                    ▼
                                            write_back ──► 修复后的 DOCX
```

- `document`：`schema_version` 当前为 `"1.0"`，含 `sections` 与 `blocks`；`blocks` 按文档真实顺序混排（段落、表格、图片、公式同一序列），`index` 从 0 连续编号，校验见 `backend/document_contract.py`。
- 段落块的 `kind` 由 `backend/recognition/classification.py` 初判、`backend/recognition/context.py` 按上下文校正，检测层全部按 `kind` 取规则。
- 前置区（承诺书页、封面、目录之前）不是正文：承诺书自身的文字不参与检查，封面字段行归 `cover_info`，该区域的图片 / 表格是版式元素（解析层打 `front_matter: true`），检测层不按图表规则要求题注。
- Word 自动编号生成的段落带 `auto_number: true`，解析层按 `word/numbering.xml` 还原出可见编号写进 `auto_number_text`；判定层据此按可见编号比格式，还原不出来时跳过看编号的检查，不拿猜出来的编号报错。
- `issues`：`{rule_id, category, severity, location, location_samples, occurrence_count, variants, actual, expected, message, suggestion, basis, basis_detail}`。`basis` 是模板里写着这条要求的原句，语义层自己的判定说明放在 `basis_detail`。
- 按学院启停的条款（如 `reference.format` 只在计算机学院查）由 `backend/detection/checker_service.SEMANTIC_RULE_GATES` 决定，关闭时既不报也不进统计；返回前由 `backend.fix.annotate_issues` 为每条补 `fixable` / `fix_reason`。
- `unchecked`：**本次没有执行的条款**及原因（`[{"rule_id", "reason"}]`）。模板里写着、当前环境或能力做不到的检查不能静默消失，否则学生以为“全查过了”。目前两类：位图的图内文字（像素里取不到字体名与字号）、没有 AI 服务时的坐标轴说明与坐标单位。
- 对话编排：`backend/rag/dialog/intent.py` 用正则产出「意图 + 槽位」，`backend/rag/dialog/graph.py` 的状态机按意图调用事实层，只有 `spec_qa` 才进入检索与生成，其余分支不调用模型。
- 复合请求（一轮多问、整理 / 排序 / “能自动改”、`R9` 兜底）在 `config.USE_DIALOG_AGENT` 打开时交给 `backend/rag/dialog/agent/`：8 个只读工具（概览 / 筛选 / 单条 / 改法 / 依据 / 未检 / 规则号 / 规范检索），最多 `AGENT_MAX_STEPS` 步、`temperature=0`、同参缓存防循环；答案里的数字与规则号必须能在工具返回里找到，否则先纠正一次、仍不合格就降级回状态机。默认关闭，关闭时行为与改造前一致。
- 工具层有两条事实约定：既没检测过也没问题记录时，`get_overview` 返回“尚未检测”而不是“0 条问题”；概览会带上本次检测所属的学院（`college_id` + 学院名），客服式回答不得把用户口述的学院当成本次检测的学院。
- `RAGResponse.answer_source` 取值：`paper_facts` / `spec_kb` / `out_of_scope` / `clarify` / `agent` / `none`；`trace` 是步骤名列表，Agent 的步骤以 `agent:` 前缀标记（如 `agent:list_issues`）。
- 边界约定：判定只认学院模板里明确写着的文字说明句，没有说明的项一律不卡（页面设置因此只查页边距；纸张尺寸、页面方向、页眉页脚、目录整块都没有说明句）。
- 修复层只改 JSON 数据块、不碰 DOCX XML；写回层不做规则判定。`/api/fix` 以原 DOCX 包为载体，只替换必要的正文 XML 与图片关系，未修改部件保留原样。
- 写回层无法安全定位或改写的单个对象（段落含域代码 / 图形、多节文档做结构性变更、契约外字段等）会跳过并汇总告警：响应头 `X-Writeback-Skip-Count` / `X-Writeback-Warnings`，其余修复照常生效；只有契约级不一致（区块数 / 节数不匹配、图片部件名冲突等）才返回 400。
- 新增一条规则通常只需在 `detection` 加判定、在 `fix` 加对应修复通道；整段套用某条样式规范的 kind 只要在 `backend/detection/rules/base.py` 的 `STYLE_BLOCKS` 表里加一行。

### 前端

`frontend/src/` 分 `api/`（`request.ts` 统一 `baseURL=/api`，按业务分模块）、`views/`（Home / Result / Detail / Help / Settings）、`components/`（ErrorCard、FixBar、ChatPanel、ScoreCard 等）、`utils/`（修复流程、本地缓存、issue 字段归一化）、`router/`、`mock/`（`VITE_USE_MOCK=true` 时无需后端即可跑通页面）。详见 `frontend/README.md`。

## 团队分工

### 后端与系统整合

- 负责 API、文件处理、服务编排、异常处理、日志和基础设施接入。
- 负责串联解析、规则检查、RAG、写回与前端，维护统一接口并完成联调。
- 负责本地部署、启动配置、资源管理和集成发布。
- 负责解析结果与检测结果的数据结构校验。

### 规则引擎、修复与 RAG 语义检查

#### 1.规则引擎

- 负责学院规则配置与确定性校验，配置学院标准模板检测数据块，输出统一结构与检查结果编排。
- 负责多学校多学院可扩展：实现学院、规则与结构识别表结构。
- 负责判定级别与依据：语义检查出的问题一律记为提示，每条附模板原句。

#### 2.数据块修复

- 负责把用户确认的问题确定性应用到数据块：格式修复、文字改写、结构性增删，产出修复后的数据块。

#### 3.RAG语义检查

- 负责多学校多学院知识库隔离、构建与检索、规范解释、语义检查与文本改写。
- 负责会话管理，按 ID 记忆检测结果与对话。

#### 4.结构识别

- 实现结构识别：判定段落类型（标题、正文、题注、参考文献…），规则引擎和语义检查据此对应规则。
- 实现上下文校正：按锚点把文档划成摘要/目录/正文/参考文献/致谢等区域，承诺书整页免检，封面信息行单独成类，前置区的版式块不与正文混在一起。
- 实现结构识别表：词表按学院外置，换学院改配置而不动代码。
- 实现结构识别告警：锚点没认出来时记一条日志，异校模板问题可见。

#### 5.跨层公共事实

- 负责被多个层共用的 DOCX 事实：对齐取值三向映射、run 遍历顺序与字符格式原语、块级遍历（下钻内容控件与文本框）、图片与公式资产路径。
- 负责段末排版说明括注的剥离口径，识别层与检测层共用同一份。

### 解析与 DOCX 写回

- 负责将 DOCX 解析为统一的数据块，提取结构、格式、图表、公式等信息。
- 优化解析性能，把文档样式的重复解析缓存。
- 负责复杂 Word 对象识别与异常容错，并保证解析出的数据块编号与写回时定位到的对象一致。
- 写回：显式将数据块复现为全新 DOCX。
- 提供稳定的解析/写回接口并验证输出文档可打开；后端不直接操作 DOCX XML。

### 交互与可视化

- 负责上传、检测进度、问题展示、RAG 对话、修复确认和结果下载。
- 负责调用统一后端 API，以及加载状态、错误提示和交互体验。

### 测试与工程化

- 负责模块测试、接口联调、兼容性与端到端回归测试。
- 负责依赖、版本管理、质量检查、文档和发布交付。

真实 DOCX 的基础业务闭环可单独验收。测试会生成包含页眉、页码域、目录域、
自定义样式、图片和表格的论文样例，依次调用检测与修复接口，再重新解析、重新
检测并核对未修改的 OOXML 部件保持不变；同时覆盖页面、正文与表格三类问题的
批量修复。该测试不依赖 API Key 或知识库：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_real_docx_e2e -v
```

提交接口相关改动前，可运行离线核心接口冒烟测试。它依次验证健康检查、学院
列表、文件检测、结构化契约检测、原位修复、修复后重检、显式重建以及重建后
再次检测，并校验 JSON 响应模型和 DOCX 包完整性；同样不依赖 API Key 或知识库：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_api_core_smoke_e2e -v
```

聊天改动可运行固定问法回归集，覆盖概览、类目定位、规则号、只看错误、
上下文追问、多问题澄清、模板依据、未检清单、多类目并列、纠正序号、
继续翻页与越界拒答；只跑规则/事实层，不加载知识库或模型：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_chat_eval -v
```

复合请求的 Agent 链路可运行独立评测：默认用脚本化模型把工具、循环、
溯源校验整条链路跑一遍（不联网、不需要 Key）；`--live` 才连真实模型，
给出的才是模型能力的数字：

```powershell
.\.venv\Scripts\python.exe scripts\eval_dialog_agent.py
.\.venv\Scripts\python.exe scripts\eval_dialog_agent.py --live
```

实测（`deepseek-chat`，8 条合成任务，不含论文内容）：8/8 完成、0 降级，
平均 2.62 步；步数上限 4，全部答案通过数字与规则号溯源校验。

另有 27 条对抗用例（提示词注入、索要系统提示、要求直接改文件、要求换学院规范、
未检项追问、超范围提问、错误前提确认等），同样只跑合成数据：

```powershell
.\.venv\Scripts\python.exe scripts\eval_dialog_agent.py --live --adversarial --with-kb
```

`--with-kb` 接入真实知识库，使规范检索工具可用。实测 27/27 无违规：注入与越权请求均被拒，
未检项只说“没查”不下结论，模板没写的数值（如 1.5 倍行距对应多少磅）不做换算。

## 模块交接边界

业务处理链路统一为：

```text
原始 DOCX
  ↓
论文解析
  ↓ document
接口编排
  ↓
规则引擎判定 + RAG 依据检索与解释
  ↓ issues
返回结果、展示依据并接收用户确认
  ↓
按确认结果执行 DOCX 写回
  ↓
返回修复后的 DOCX
```

模块之间只通过约定的数据接口通信：

- 解析模块输出 `document`，其中 `blocks` 按文档实际顺序混排，`index` 从 0 开始连续编号。
- 检测模块接收 `document`，输出包含 `passed`、`issues` 和 `breakdown` 的结果。
- 写回模块接收原始 DOCX 和用户确认的 `issues`，输出修改后的 DOCX 及处理统计。
- 后端负责 HTTP 层和流程串联；业务模块负责人负责各自模块内部逻辑和契约稳定性。
- 对图表位置等解析难度较高或规则边界不明确的问题，系统应标记为“建议复核”或“需结合规范确认”，由用户或教师作最终判断。判定标准只取学院模板里明确写着的文字说明句：模板自己的排版结果、我们自己整理的文字材料都不算标准，没有说明的项一律不卡。
- 在线模型接口主要用于前期验证和效果对比；规则校验、文档解析、规范知识库和可选轻量模型优先支持本地运行。

## 分支规范

- master：最终稳定发布分支（结题版本）
- dev：开发集成分支
- feature/xxx：个人功能开发分支

## 协作规范

1. 所有功能在独立feature分支开发
2. 所有代码必须PR+至少1人Review
3. 禁止直接推送保护分支
