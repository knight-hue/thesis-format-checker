import os

# 接口访问日志开关；不记录请求正文、文件名或查询参数。
ACCESS_LOG_ENABLED = os.getenv("THESIS_ACCESS_LOG_ENABLED", "1") == "1"

# 同步解析、检测、RAG 与写回任务使用独立线程池，避免阻塞 FastAPI 事件循环。
# 线程数保持较小，防止并发处理大文档时耗尽内存；可按部署机器资源调整。
BLOCKING_JOB_MAX_WORKERS = max(
    1, int(os.getenv("THESIS_BLOCKING_JOB_MAX_WORKERS", "4"))
)

# 检测结果输出边界：同规则、实际值、期望值的问题先聚合，再限制
# 返回的逻辑问题数和每组定位样本数，避免异常文档撑大响应与前端内存。
MAX_RETURNED_ISSUES = min(
    1000, max(1, int(os.getenv("THESIS_MAX_RETURNED_ISSUES", "1000")))
)
MAX_ISSUE_LOCATION_SAMPLES = min(
    10, max(1, int(os.getenv("THESIS_MAX_ISSUE_LOCATION_SAMPLES", "10")))
)

# 异步检测任务：结果持久化到本地 SQLite，上传文件只保留到任务结束。
# 本地队列用于单机部署；限制活跃任务数，避免并发上传耗尽磁盘和内存。
CHECK_JOB_DB_PATH = os.getenv("THESIS_CHECK_JOB_DB_PATH", "var/data/check_jobs.db")
CHECK_JOB_FILES_DIR = os.getenv("THESIS_CHECK_JOB_FILES_DIR", "var/data/check_jobs")
CHECK_JOB_EXPIRE_SECONDS = max(
    60, int(os.getenv("THESIS_CHECK_JOB_EXPIRE_SECONDS", "86400"))
)
CHECK_JOB_MAX_ACTIVE = max(
    1, int(os.getenv("THESIS_CHECK_JOB_MAX_ACTIVE", "8"))
)

# DOCX 上传边界：同时限制压缩包本身体积、ZIP 条目数和解压后总体积，避免伪造
# 后缀、残缺 OOXML 包或高压缩比文件进入解析器并消耗过多磁盘/内存。
MAX_UPLOAD_BYTES = max(
    1024, int(os.getenv("THESIS_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
)
MAX_DOCX_ENTRIES = max(
    10, int(os.getenv("THESIS_MAX_DOCX_ENTRIES", "10000"))
)
MAX_DOCX_UNCOMPRESSED_BYTES = max(
    MAX_UPLOAD_BYTES,
    int(os.getenv("THESIS_MAX_DOCX_UNCOMPRESSED_BYTES", str(250 * 1024 * 1024))),
)

# /api/fix 的 issues_json 请求边界。先限制原始 JSON 大小，再限制 issue 数量，
# 避免超大嵌套请求在进入 DOCX 解析和修复链前占用过多内存与处理时间。
MAX_FIX_ISSUES_JSON_BYTES = max(
    1024, int(os.getenv("THESIS_MAX_FIX_ISSUES_JSON_BYTES", str(2 * 1024 * 1024)))
)
MAX_FIX_ISSUES = max(1, int(os.getenv("THESIS_MAX_FIX_ISSUES", "500")))

# ========== 生成模型配置 ==========
# 生成模型当前走在线 API；将来换成本地模型时，在 rag/llm_client.create_llm_client 里接上即可。
# 密钥不入库：优先读环境变量 THESIS_LLM_API_KEY，其次读未被 git 跟踪的 local_settings.py。
# （历史提交里曾写下真实密钥，若该密钥仍有效，请先到平台吊销再重新生成。）
try:
    from local_settings import LLM_API_KEY as _LOCAL_LLM_API_KEY
except ImportError:
    _LOCAL_LLM_API_KEY = ""

LLM_API_KEY = os.getenv("THESIS_LLM_API_KEY", _LOCAL_LLM_API_KEY)     #填写api
LLM_BASE_URL = "https://api.deepseek.com"
LLM_MODEL = "deepseek-chat"

# ========== 论文内容外发开关（演示/联调）==========
# 项目铁律：论文内容只允许发往本机模型端点。默认关闭，语义判定/改写会降级。
# 仅在中期演示或**不含敏感数据**的联调场景显式打开：
#   1) 环境变量 THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS=1（一次性，推荐）；
#   2) local_settings.py 中写 ALLOW_CLOUD_LLM_FOR_DOCUMENTS = True（本机持久，不入库）。
# 环境变量优先级更高：设 0 可在不修改文件的情况下临时关回。
# 打开后语义判定/段落改写会把论文正文发往外部模型端点，演示结束请关回。
try:
    from local_settings import ALLOW_CLOUD_LLM_FOR_DOCUMENTS as _LOCAL_ALLOW_CLOUD_DOCS
except ImportError:
    _LOCAL_ALLOW_CLOUD_DOCS = False

_env_allow_cloud_docs = os.getenv("THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS")
ALLOW_CLOUD_LLM_FOR_DOCUMENTS = (
    bool(_LOCAL_ALLOW_CLOUD_DOCS)
    if _env_allow_cloud_docs is None
    else _env_allow_cloud_docs.strip() == "1"
)

# ========== 通用生成参数 ==========
LLM_TEMPERATURE = 0.05
LLM_MAX_TOKENS = 2048

# LLM 外部依赖保护：SDK 仅对连接错误、408/409/429 和 5xx 等瞬时故障重试。
LLM_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("THESIS_LLM_TIMEOUT_SECONDS", "60"))
)
LLM_MAX_RETRIES = max(0, int(os.getenv("THESIS_LLM_MAX_RETRIES", "2")))
LLM_MAX_CONCURRENCY = max(
    1, int(os.getenv("THESIS_LLM_MAX_CONCURRENCY", "2"))
)

# ========== Embedding 模型配置（本地向量化）==========
# 推理走 onnxruntime，不依赖 torch/transformers，可省约 520MB 运行时体积。
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"            # 逻辑名称，仅用于日志展示
EMBEDDING_ONNX_REPO = "Xenova/bge-small-zh-v1.5"      # ONNX 权重出处（BAAI 官方仓库不提供 ONNX），仅用于缺失时报出该去哪拿
EMBEDDING_TOKENIZER_REPO = "BAAI/bge-small-zh-v1.5"   # 分词器取自原仓库，与 sentence-transformers 所用版本逐字节一致
EMBEDDING_DIR = "models/bge-small-zh-v1.5-onnx"       # 权重目录：不随仓库分发，也不由程序下载，需自行放置
EMBEDDING_ONNX_PRECISION = "fp32"                     # fp32≈91MB（与原 torch 完全一致，推荐）/ int8≈23MB（更省空间，但检索结果会漂移）
EMBEDDING_MAX_LENGTH = 512                            # 与原模型 sentence_bert_config.json 的 max_seq_length 一致
EMBEDDING_BATCH_SIZE = 32
# 是否把西文统一转小写。BAAI 的 tokenizer.json 标的是 lowercase=false，但
# sentence-transformers 走 vocab.txt 后端时实际会小写化（"Times" → "times"）。
# 保持 True 才能与既有向量库、既有向量空间一致；改成 False 会让所有含西文的
# 段落产生成片 [UNK]，向量随之偏移，必须重建向量库。
EMBEDDING_LOWERCASE = True

# ========== 检索配置 ==========
VECTOR_K = 5
BM25_K = 5
RECALL_TOP_K = 5
SCORE_THRESHOLD = 0.1

# ========== 功能开关 ==========
USE_QUERY_REWRITE = True
USE_CACHE = True

# ========== 对话编排开关 ==========
# 关闭后 answer() 完全走改造前的规范问答链路（回滚开关）
USE_DIALOG_ROUTER = True
# 状态机步数护栏：超过后直接落规范问答，杜绝死循环
DIALOG_MAX_STEPS = 4
# 是否允许产出澄清追问（只影响追问，不影响答案）
DIALOG_CLARIFY_ENABLED = True

# ========== 对话 Agent（ReAct + tool calling）开关 ==========
# 关闭时行为与改造前完全一致（总回滚开关）
USE_DIALOG_AGENT = False
# Agent 最多执行几步（含一次纠正机会）
AGENT_MAX_STEPS = 4
# 路由策略：off / fallback（仅兜底）/ complex（兜底 + 复合请求）/ all（全部走 Agent）
AGENT_ROUTE = "complex"

# ========== 查询改写配置 ==========
QUERY_REWRITE_MODE = "rule"

# ========== 缓存配置 ==========
CACHE_MAX_SIZE = 1000

# ========== 学院配置文件路径 ==========
COLLEGES_CONFIG_PATH = "config/colleges.json"

# ========== 学院坐标规范 ==========
# 学院 ID 是"规则集 + 知识库 + 缓存 + 会话"共同的主键，会拼进文件名与目录名
# （config/rules/<学校>/<学院>/<id>_rules.json）；因此收窄字符集：小写字母、数字、下划线、连字符，
# 多版本用 @ 后缀（如 jlu_cs@2024）。禁中文、空格与「.」——后者顺手堵住路径穿越。
# 学校/学院/专业这些人类可读的信息写在 config/colleges.json 的字段里，不塞进 id。
COLLEGE_ID_PATTERN = r"[a-z0-9][a-z0-9_@-]*"

# 学院配置的必填字段：少了任何一个，条目就定位不到规范文本或无法展示给用户。
COLLEGE_REQUIRED_FIELDS = ("school", "school_name", "college", "college_name",
                           "rule_file")

# ========== 知识库落盘根目录 ==========
# 库的目录名是"语料内容 + 切片参数"的哈希（见 rag/kb_store）：语料一变键就变，
# 取不到库就是明确提示重建；语料相同的学院共用同一个目录。
KB_ROOT_DIR = "var/kb"

# ========== 会话存储配置（SQLite，无需额外服务）==========
SESSION_DB_PATH = "var/data/sessions.db"   # 会话数据库文件，随项目本地保存
SESSION_MAX_HISTORY_TURNS = 10         # 对话记忆保留最近几轮
SESSION_EXPIRE_SECONDS = 86400         # 会话24小时不活跃自动过期
