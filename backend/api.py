"""
FastAPI 接口服务
"""
import asyncio
import json
import copy
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from functools import partial
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator
from pydantic import ValidationError
from typing import Any, List, Literal, Optional, TYPE_CHECKING
from backend.rag.colleges import coordinates
from backend.docx_common.assets import images_dir
from backend.detection.checker_service import check_document
from backend.detection.issue_compaction import compact_check_result
from backend.parser import docx_to_json
from backend.recognition.anomalies import structure_warnings
from backend.recognition.structure import load_structure
from backend.fix import IMAGE_RULE_IDS
from backend.fix.fix_blocks import fix_document
from backend.fix.fixability import annotate_issues
from backend.fix.applied_match import fixed_issue_indexes
from backend.fix.verification import verify_export
from backend.write_back.json_to_docx import json_to_docx
from backend.write_back.patch_docx import patch_docx
from backend.write_back.numbering import patch_numbering_formats
from backend.document_contract import DocumentContract
from backend.check_jobs import CheckJobStore, DuplicateJobId, TooManyActiveJobs
from backend.config import (
    ACCESS_LOG_ENABLED,
    BLOCKING_JOB_MAX_WORKERS,
    CHECK_JOB_DB_PATH,
    CHECK_JOB_EXPIRE_SECONDS,
    CHECK_JOB_FILES_DIR,
    CHECK_JOB_MAX_ACTIVE,
    MAX_DOCX_ENTRIES,
    MAX_DOCX_UNCOMPRESSED_BYTES,
    MAX_FIX_ISSUES,
    MAX_FIX_ISSUES_JSON_BYTES,
    MAX_ISSUE_LOCATION_SAMPLES,
    MAX_RETURNED_ISSUES,
    MAX_UPLOAD_BYTES,
)

if TYPE_CHECKING:  # 仅用于类型标注：运行时不导入，避免启动阶段就把模型栈拉进来
    from backend.rag.llm_service import LLMService
    from backend.rag.rag_service import RAGService

logger = logging.getLogger(__name__)
access_logger = logging.getLogger("thesis.access")
access_logger.setLevel(logging.INFO)
if not access_logger.handlers:
    access_handler = logging.StreamHandler()
    access_handler.setFormatter(logging.Formatter("%(message)s"))
    access_logger.addHandler(access_handler)
access_logger.propagate = False
# X-Fixed-Indexes 响应头的字符上限，超了就放弃回传（前端退化为不自动刷新列表）
MAX_FIXED_HEADER_CHARS = 4096

# X-Writeback-Warnings 响应头的字符上限，超了就只回传条数（明细看服务端日志）
MAX_WARNING_HEADER_CHARS = 2048


class FixIssueLocation(BaseModel):
    """修复项定位契约；按定位类型约束必需的坐标字段。"""

    model_config = ConfigDict(extra="allow", strict=True)

    type: Literal["section", "paragraph", "table", "table_cell", "image", "document"]
    index: Optional[int] = Field(default=None, ge=0)
    row: Optional[int] = Field(default=None, ge=0)
    col: Optional[int] = Field(default=None, ge=0)
    run: Optional[int] = Field(default=None, ge=0)
    text_preview: Optional[str] = Field(default=None, max_length=500)
    kinds: Optional[List[str]] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_coordinates(self):
        if self.type != "document" and self.index is None:
            raise ValueError(f"location.type={self.type} 时必须提供 index")
        if self.type == "table_cell" and (self.row is None or self.col is None):
            raise ValueError("location.type=table_cell 时必须提供 row 和 col")
        return self


class FixIssueBlockRef(BaseModel):
    """语义 issue 指向的正文片段。"""

    model_config = ConfigDict(extra="allow", strict=True)

    index: int = Field(ge=0)
    text: Optional[str] = Field(default=None, max_length=10000)


class FixIssueInput(BaseModel):
    """/api/fix 接收的单条 issue；允许业务模块附加已知之外的展示字段。"""

    model_config = ConfigDict(extra="allow", strict=True)

    rule_id: str = Field(min_length=1, max_length=128)
    location: FixIssueLocation
    locations: Optional[List[FixIssueLocation]] = Field(default=None, max_length=200)
    source: Optional[Literal["rule_engine", "rag"]] = None
    category: Optional[str] = Field(default=None, max_length=100)
    severity: Optional[Literal["error", "warning", "info"]] = None
    actual: Optional[Any] = None
    expected: Optional[Any] = None
    message: Optional[str] = Field(default=None, max_length=10000)
    suggestion: Optional[str] = Field(default=None, max_length=10000)
    basis: Optional[str] = Field(default=None, max_length=20000)
    basis_detail: Optional[str] = Field(default=None, max_length=20000)
    conclusion: Optional[str] = Field(default=None, max_length=10000)
    blocks: Optional[List[FixIssueBlockRef]] = Field(default=None, max_length=500)
    fixable: Optional[bool] = None
    fix_reason: Optional[str] = Field(default=None, max_length=10000)

    @field_validator("rule_id")
    @classmethod
    def validate_rule_id(cls, value):
        if value != value.strip():
            raise ValueError("rule_id 首尾不能包含空白")
        return value

    @model_validator(mode="before")
    @classmethod
    def reject_json_image_bytes(cls, value):
        if isinstance(value, dict) and "image_bytes" in value:
            raise ValueError("image_bytes 不能放入 issues_json，请使用 images 文件字段上传")
        return value


FIX_ISSUES_ADAPTER = TypeAdapter(List[FixIssueInput])


def _issues_validation_detail(error: ValidationError) -> str:
    """只返回第一处可定位错误，避免把整份请求内容写进响应或日志。"""
    first = error.errors(include_url=False, include_input=False)[0]
    path = ".".join(str(part) for part in first.get("loc", ())) or "issues_json"
    return f"issues_json 校验失败：{path}：{first['msg']}"


def _parse_fix_issues(issues_json: str) -> list[dict]:
    if len(issues_json.encode("utf-8")) > MAX_FIX_ISSUES_JSON_BYTES:
        raise HTTPException(status_code=413, detail="issues_json 超过大小限制")
    try:
        raw_issues = json.loads(issues_json)
    except (TypeError, json.JSONDecodeError, RecursionError):
        raise HTTPException(status_code=400, detail="issues_json 不是合法 JSON")
    if not isinstance(raw_issues, list):
        raise HTTPException(status_code=400, detail="issues 必须是对象列表")
    if len(raw_issues) > MAX_FIX_ISSUES:
        raise HTTPException(status_code=400, detail=f"issues 数量不能超过 {MAX_FIX_ISSUES}")
    try:
        issues = FIX_ISSUES_ADAPTER.validate_python(raw_issues)
    except ValidationError as error:
        raise HTTPException(status_code=400, detail=_issues_validation_detail(error))
    return [issue.model_dump(exclude_unset=True) for issue in issues]


def _validate_issue_targets(issues: list[dict], document: dict) -> None:
    """确认 issue 中的深层坐标确实指向本次上传文档。"""
    blocks = document.get("blocks", [])
    sections = document.get("sections", [])
    expected_block_type = {
        "paragraph": "paragraph",
        "table": "table",
        "table_cell": "table",
        "image": "image",
    }

    for issue_position, issue in enumerate(issues):
        # 聚合问题会带 locations（同规则多处）：每一个位置都要指向本次文档。
        targets = [issue["location"]] + list(issue.get("locations") or [])
        for target_position, location in enumerate(targets):
            location_type = location["type"]
            index = location.get("index")
            if target_position == 0:
                prefix = f"issues[{issue_position}].location"
            else:
                prefix = f"issues[{issue_position}].locations[{target_position - 1}]"

            if location_type == "section":
                if index >= len(sections):
                    raise HTTPException(status_code=400, detail=f"{prefix}.index 指向不存在的分节")
                continue
            if location_type == "document":
                for ref_position, ref in enumerate(issue.get("blocks") or []):
                    if ref["index"] >= len(blocks):
                        raise HTTPException(
                            status_code=400,
                            detail=f"issues[{issue_position}].blocks[{ref_position}].index 指向不存在的数据块",
                        )
                continue
            if index >= len(blocks):
                raise HTTPException(status_code=400, detail=f"{prefix}.index 指向不存在的数据块")

            required_type = expected_block_type.get(location_type)
            block = blocks[index]
            if required_type is not None and block.get("type") != required_type:
                raise HTTPException(status_code=400, detail=f"{prefix} 未指向 {required_type} 数据块")
            if location_type == "table_cell" and not any(
                cell.get("row") == location["row"] and cell.get("col") == location["col"]
                for cell in block.get("cells", [])
            ):
                raise HTTPException(status_code=400, detail=f"{prefix} 指向不存在的表格单元格")

# ========== 初始化 ==========
# AI 能力分两阶段预热，都不阻塞启动：uvicorn 先开始服务，健康检查与学院列表 2~3 秒就可用。
#   1. 生成模型门面（LLMService）：只构造 OpenAI 客户端，秒级就绪，检测链路的语义判定/改写靠它；
#   2. 知识库（RAGService）：要加载 Chroma / BM25 / 词表索引，慢得多，只有问答链路需要它。
llm_service: Optional["LLMService"] = None
llm_status = "initializing"  # initializing | ready | failed
llm_error: Optional[str] = None

rag_service: Optional["RAGService"] = None
rag_status = "initializing"  # initializing | ready | failed
rag_error: Optional[str] = None


async def _run_blocking(function, *args, **kwargs):
    """在线程池中执行同步重活，让事件循环继续服务其他请求。"""
    loop = asyncio.get_running_loop()
    executor = getattr(app.state, "blocking_executor", None)
    return await loop.run_in_executor(executor, partial(function, *args, **kwargs))


def _prepare_llm_service():
    """后台线程：构造 LLMService（生成模型门面，语义检测与段落改写的最小依赖）。"""
    global llm_service, llm_status, llm_error
    started = time.perf_counter()
    try:
        from backend.rag.llm_service import LLMService

        service = LLMService()
    except Exception as exc:
        llm_status = "failed"
        llm_error = f"{type(exc).__name__}: {exc}"
        logger.exception("生成模型服务初始化失败，规则检测和格式写回仍可用")
        return
    llm_service = service
    llm_status = "ready"
    logger.info("生成模型服务就绪（%s），耗时 %.2fs", service.describe(), time.perf_counter() - started)


def _prepare_rag_service():
    """后台线程：构造 RAGService（耗时主要在这里面的知识库索引加载）。"""
    global rag_service, rag_status, rag_error
    if llm_service is None:
        rag_status = "failed"
        rag_error = llm_error or "生成模型服务不可用，跳过知识库预热"
        logger.warning("跳过知识库预热：%s", rag_error)
        return
    started = time.perf_counter()
    try:
        from backend.rag.rag_service import RAGService

        # 复用同一个门面：检测链路与问答链路共享一份生成模型客户端
        service = RAGService(llm_service=llm_service)
    except Exception as exc:
        rag_status = "failed"
        rag_error = f"{type(exc).__name__}: {exc}"
        logger.exception("知识库初始化失败，规则检测、语义检测与格式写回仍可用")
        return
    rag_service = service
    rag_status = "ready"
    logger.info("知识库就绪，耗时 %.1fs", time.perf_counter() - started)


def _warmup_ai(stopped: threading.Event):
    """后台线程：先备好检测链路要的生成模型门面，再加载只为问答服务的知识库。"""
    _prepare_llm_service()
    if stopped.is_set():
        return
    _prepare_rag_service()


def _require_rag() -> "RAGService":
    if rag_service is not None:
        return rag_service
    if rag_status == "failed":
        raise HTTPException(status_code=503, detail="知识库初始化失败，请查看后端日志后重启")
    raise HTTPException(status_code=503, detail="知识库正在初始化，请稍后重试")


def _detection_service():
    """检测/改写链路要的服务实例：生成模型门面。

    检测只需要判定与改写能力，不必等知识库；知识库未就绪时也能跑完整语义层。
    但语义判定/改写会把论文正文发给模型：端点不是本机且没有显式放行时，
    这里返回 None，检测自动降级为「零 LLM 的确定性条款」，结果页会明示降级。
    """
    if llm_service is None:
        return None
    if not getattr(llm_service, "documents_allowed", False):
        return None
    return llm_service


def _semantic_skip_reason(include_semantic: bool) -> str:
    """语义定位条款没跑的原因，供结果页如实显示（不参与判定）。"""
    if not include_semantic:
        return "disabled"
    if llm_service is None:
        return "unavailable" if llm_status == "failed" else "initializing"
    if not getattr(llm_service, "documents_allowed", False):
        return "privacy"
    return ""


def _annotate_result_issues(result, llm_available: bool) -> None:
    """给三类结果补 decision 与 fixable（仅补缺失的）。

    - issues：默认 confirmed；
    - advisory_issues：默认 suggested；
    - pending_review：默认 pending_review。

    三类条目共用 IssueResponse 响应模型，fixable / decision 都有默认值；这里显式
    写入是为了让 advisory 不会因为模型默认值被误标成 confirmed。

    只补缺失项、不覆盖已有值：聚合层会把「合并了多个位置」的条目降级为
    fixable=False 并写入 fix_reason；读取旧任务时若整批重标，会把这条安全降级
    覆盖回 True，前端又允许勾选一个只改第一处的修复项。
    """
    decision_by_key = {
        "issues": "confirmed",
        "advisory_issues": "suggested",
        "pending_review": "pending_review",
    }
    for key, default_decision in decision_by_key.items():
        items = [item for item in (result.get(key) or [])
                 if isinstance(item, dict)]
        for item in items:
            item.setdefault("decision", default_decision)
        missing = [item for item in items if "fixable" not in item]
        if missing:
            annotate_issues(missing, llm_available=llm_available)


def _require_detection_service():
    """语义修复等入口对最小 AI 能力的要求：门面就绪且允许发送论文内容。"""
    service = _detection_service()
    if service is not None:
        return service
    if llm_service is not None and not getattr(llm_service, "documents_allowed", False):
        raise HTTPException(
            status_code=400,
            detail="当前生成模型端点不是本机，按隐私规则不能把论文内容发往外部服务；"
                   "请改用本机模型端点，或在演示/无敏感数据联调时显式设置 "
                   "THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS=1"
                   "（也可在 local_settings.py 中设置 "
                   "ALLOW_CLOUD_LLM_FOR_DOCUMENTS = True）",
        )
    if llm_status == "failed":
        raise HTTPException(status_code=503, detail="生成模型服务初始化失败，请查看后端日志后重启")
    raise HTTPException(status_code=503, detail="生成模型服务正在初始化，请稍后重试")


async def _save_docx_upload(file: UploadFile, directory: str) -> str:
    """保存并基本校验上传文件，避免无上限地读入内存。"""
    if not file.filename or not file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 .docx 文件")

    path = os.path.join(directory, "input.docx")
    try:
        total = await _run_blocking(_copy_upload_to_path, file.file, path)
    finally:
        await file.close()

    if total == 0:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    await _run_blocking(_validate_docx_package, path)
    return path


def _copy_upload_to_path(source, path: str) -> int:
    source.seek(0)
    total = 0
    with open(path, "wb") as output:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                limit_mb = max(
                    1, (MAX_UPLOAD_BYTES + 1024 * 1024 - 1) // (1024 * 1024)
                )
                raise HTTPException(
                    status_code=413,
                    detail=f"上传文件不能超过 {limit_mb} MB",
                )
            output.write(chunk)
    return total


def _validate_docx_package(path: str) -> None:
    """Validate the ZIP envelope before python-docx/parser processes the upload."""
    required_parts = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
    try:
        with zipfile.ZipFile(path) as package:
            entries = package.infolist()
            if len(entries) > MAX_DOCX_ENTRIES:
                raise HTTPException(
                    status_code=413,
                    detail=f"DOCX 内部文件数量不能超过 {MAX_DOCX_ENTRIES}",
                )
            names = {entry.filename for entry in entries}
            if not required_parts.issubset(names):
                raise HTTPException(status_code=400, detail="文件不是有效的 DOCX 文档")
            total_uncompressed = sum(entry.file_size for entry in entries)
            if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                limit_mb = MAX_DOCX_UNCOMPRESSED_BYTES // (1024 * 1024)
                raise HTTPException(
                    status_code=413,
                    detail=f"DOCX 解压后内容不能超过 {limit_mb} MB",
                )
            bad_entry = package.testzip()
            if bad_entry is not None:
                raise HTTPException(status_code=400, detail="DOCX 压缩包内容已损坏")
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        NotImplementedError,
        OSError,
        RuntimeError,
    ):
        raise HTTPException(status_code=400, detail="文件不是有效的 DOCX 文档")


def _remove_temp_dir(path: str):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def _load_document_contract(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as document_file:
        document = json.load(document_file)
    return DocumentContract.model_validate(document).model_dump(exclude_unset=True)


class ErrorResponse(BaseModel):
    """FastAPI/HTTPException 当前使用的统一 HTTP 错误体。"""

    detail: Any


class IssueLocationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    index: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    run: Optional[int] = None
    text_preview: Optional[str] = None
    kinds: Optional[List[str]] = None


class IssueVariantResponse(BaseModel):
    """同一规则名下的一处实测：实测值 / 要求值 / 结论 / 位置。"""

    model_config = ConfigDict(extra="allow")

    actual: Any = None
    expected: Any = None
    conclusion: Optional[str] = None
    location: IssueLocationResponse


class EarlierOccurrenceResponse(BaseModel):
    """同一检查范围内更早出现的位置，供术语规则说明“非首次出现”。"""

    index: int
    kind: str
    excerpt: str


class IssueEvidence(BaseModel):
    """语义检查判定依据：范围、首次出现、更早出现与机器可读原因。"""

    scope: Optional[Literal[
        "body", "zh_abstract", "en_abstract", "acknowledgement"
    ]] = None
    first_occurrence: Optional[bool] = None
    earlier_occurrences: List[EarlierOccurrenceResponse] = Field(
        default_factory=list, max_length=3
    )
    rule_hits: List[str] = Field(default_factory=list)
    reason_code: Optional[Literal[
        "exempt_by_level",
        "not_first_in_scope",
        "first_occurrence_no_gloss",
        "context_unavailable",
        "model_guess_unlisted_term",
    ]] = None


class IssueResponse(BaseModel):
    """规则引擎与语义检测共享的问题结构；保留规则专属扩展字段。"""

    model_config = ConfigDict(extra="allow")

    rule_id: str
    category: str
    severity: Literal["error", "warning", "info"]
    location: IssueLocationResponse
    actual: Any
    expected: Any
    message: str
    suggestion: str = ""
    source: Literal["rule_engine", "rag"]
    fixable: bool
    fix_reason: Optional[str] = None
    basis: Optional[str] = None
    basis_detail: Optional[str] = None
    conclusion: Optional[str] = None
    blocks: Optional[List[FixIssueBlockRef]] = None
    occurrence_count: int = Field(ge=1)
    location_samples: List[IssueLocationResponse] = Field(max_length=10)
    # 聚合问题携带的全部可修复位置；前端提交 /api/fix 时原样回传，实现一次全改。
    locations: Optional[List[IssueLocationResponse]] = Field(default=None, max_length=200)
    variants: List[IssueVariantResponse] = Field(default_factory=list, max_length=10)
    # 三态契约：确定性判定 / 模型建议 / 待人工。本 PR 只落字段与序列化，
    # 判定来源由后续运行时接入；issues 默认 confirmed，advisory 显式写 suggested。
    decision: Literal["confirmed", "suggested", "pending_review"] = "confirmed"
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    evidence: Optional[IssueEvidence] = None


class RuleBreakdownResponse(BaseModel):
    passed: bool
    total_issues: int = Field(ge=0)


class SemanticBreakdownResponse(BaseModel):
    enabled: bool
    llm_enabled: bool
    # 语义定位条款未执行的原因：privacy / unavailable / initializing / disabled；空 = 已执行
    reason: str = ""
    passed: Optional[bool]
    total_issues: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    prefilter_skips: int = Field(ge=0)
    # 三态计数：当前只落字段，运行时判定接入后由 checker 填充。
    confirmed_count: int = Field(default=0, ge=0)
    pending_review_count: int = Field(default=0, ge=0)


class CheckBreakdownResponse(BaseModel):
    rule_engine: RuleBreakdownResponse
    semantic: SemanticBreakdownResponse


class IssueCategorySummaryResponse(BaseModel):
    category: str
    occurrence_count: int = Field(ge=0)
    grouped_count: int = Field(ge=0)


class UncheckedItemResponse(BaseModel):
    """本次检测**没有执行**的条款，以及原因。

    模板里写着、但当前环境或能力做不到的检查（如图内文字：位图里的字是像素，
    字体名与字号都无法从图上可靠取得），不能静默消失——否则学生以为“全查过了”。
    这里逐条列出。
    """
    rule_id: str
    reason: str


class CheckResponse(BaseModel):
    schema_version: Literal["1.0"]
    college_id: str
    passed: bool
    total_issues: int = Field(ge=0)
    grouped_issues: int = Field(ge=0)
    returned_issues: int = Field(ge=0, le=1000)
    issues_truncated: bool
    issue_summary: List[IssueCategorySummaryResponse]
    issues: List[IssueResponse] = Field(max_length=1000)
    advisory_issues: List[IssueResponse] = Field(default_factory=list,
                                               max_length=1000)
    # 规则与模型都无法判定的条目：单独返回，不计入 total_issues / passed。
    pending_review: List[IssueResponse] = Field(default_factory=list,
                                               max_length=1000)
    breakdown: CheckBreakdownResponse
    unchecked: List[UncheckedItemResponse] = Field(default_factory=list)


class CheckJobLookupRequest(BaseModel):
    job_id: uuid.UUID


class CheckJobAcceptedResponse(BaseModel):
    job_id: uuid.UUID
    status: Literal["queued"]
    progress: int = Field(ge=0, le=100)
    stage: Literal["queued"]
    message: str
    created_at: datetime
    expires_at: datetime


class CheckJobStatusResponse(BaseModel):
    job_id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed"]
    progress: int = Field(ge=0, le=100)
    stage: Literal[
        "queued", "validating", "parsing", "checking", "saving", "completed", "failed"
    ]
    message: str
    result_available: bool
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class CheckJobResultResponse(BaseModel):
    job_id: uuid.UUID
    status: Literal["succeeded"]
    result: CheckResponse


def _error_responses(*status_codes: int) -> dict[int, dict[str, Any]]:
    descriptions = {
        400: "请求或 DOCX 内容不合法",
        404: "请求的资源不存在",
        409: "请求的资源状态冲突",
        413: "上传内容超过限制",
        429: "待处理任务已达上限",
        500: "服务内部处理失败",
        503: "AI 或知识库服务尚不可用",
    }
    return {
        status_code: {
            "model": ErrorResponse,
            "description": descriptions[status_code],
        }
        for status_code in status_codes
    }


check_job_store = CheckJobStore(
    CHECK_JOB_DB_PATH,
    expire_seconds=CHECK_JOB_EXPIRE_SECONDS,
    max_active=CHECK_JOB_MAX_ACTIVE,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务生命周期：后台分阶段预热 AI，并管理同步重任务线程池。"""
    executor = ThreadPoolExecutor(
        max_workers=BLOCKING_JOB_MAX_WORKERS,
        thread_name_prefix="thesis-job",
    )
    app.state.blocking_executor = executor
    app.state.check_tasks = set()
    interrupted = check_job_store.fail_incomplete()
    if interrupted:
        logger.warning("服务启动时将 %d 个未完成检测任务标记为失败", interrupted)
    logger.info("后端已就绪，生成模型服务与知识库转入后台初始化...")
    stopped = threading.Event()
    threading.Thread(
        target=_warmup_ai, args=(stopped,), name="ai-warmup", daemon=True
    ).start()
    try:
        yield
    finally:
        stopped.set()
        pending = list(app.state.check_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        executor.shutdown(wait=True, cancel_futures=True)
        app.state.blocking_executor = None
        app.state.check_tasks = set()
        logger.info("AI 服务已关闭")


app = FastAPI(
    title="论文格式规范 RAG 问答 API",
    description="基于检索增强生成的论文格式规范答疑服务，支持多学院、混合检索、重排序、查询改写",
    version="2.0.0",
    lifespan=lifespan
)
app.state.check_tasks = set()


@app.middleware("http")
async def log_api_access(request: Request, call_next):
    """记录不包含用户输入的接口访问元数据。"""
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    status_code = 500
    error_type = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:
        error_type = type(exc).__name__
        raise
    finally:
        if ACCESS_LOG_ENABLED:
            route = request.scope.get("route")
            record = {
                "event": "api_access",
                "request_id": request_id,
                "method": request.method,
                "route": route.path if route is not None else "<unmatched>",
                "status": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
            if error_type is not None:
                record["error_type"] = error_type
            access_logger.info(json.dumps(record, ensure_ascii=False))

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # 自定义响应头默认不暴露给跨域前端，这里显式放行修复统计与写回告警
    expose_headers=[
        "X-Applied-Count",
        "X-Skipped-Count",
        "X-Llm-Calls",
        "X-Fixed-Indexes",
        "X-Writeback-Skip-Count",
        "X-Writeback-Warnings",
    ],
)


def _log_structure_warnings(document, college_id):
    """解析后看一眼结构识别，锚点没认出来时记一条告警（原本完全静默）。

    只写日志：不进 issues、不参与聚合、不影响判定结果；这里再兜一层异常，
    辅助信息出问题绝不能拖累检测本身。
    """
    try:
        warnings = structure_warnings(document.get("blocks") or [],
                                     load_structure(college_id))
    except Exception:  # noqa: BLE001 - 辅助信息，任何异常都不该影响检测
        logger.exception("生成结构识别告警失败（忽略）")
        return
    if warnings:
        logger.warning("结构识别可疑（%s）：%s", college_id, "；".join(warnings))


async def _process_saved_docx(input_path: str,
                              working_dir: str,
                              *,
    college_id: str,
    include_semantic: bool,
    conversation_id: Optional[str],
    recheck: bool,
    terminology_strict: bool = False,
    terminology_b04_domain_exempt: bool = False,
    progress=None,
) -> dict:
    """对已落盘且已校验的 DOCX 执行检测，供同步与异步入口复用。"""
    if progress is not None:
        await progress(25, "parsing", "正在解析论文结构")
    document_path = await _run_blocking(docx_to_json, input_path,
                                       output_dir=working_dir,
                                       structure=load_structure(college_id))
    document = await _run_blocking(_load_document_contract, document_path)
    _log_structure_warnings(document, college_id)

    if progress is not None:
        await progress(55, "checking", "正在执行格式检测")
    ai_service = _detection_service()
    result = await _run_blocking(
        check_document,
        document=document,
        college_id=college_id,
        rag_service=ai_service,
        include_semantic=include_semantic,
        semantic_skip_reason=_semantic_skip_reason(include_semantic),
        terminology_strict=terminology_strict,
        terminology_b04_domain_exempt=terminology_b04_domain_exempt,
    )
    _annotate_result_issues(result, llm_available=ai_service is not None)
    if conversation_id and ai_service is not None:
        await _run_blocking(
            ai_service.bind_paper_errors,
            conversation_id,
            college_id,
            result["issues"],
            keep_history=recheck,
            unchecked=result.get("unchecked"),
        )

    if progress is not None:
        await progress(90, "saving", "正在保存检测结果")
    return await _run_blocking(
        compact_check_result,
        result,
        max_returned=MAX_RETURNED_ISSUES,
        max_location_samples=MAX_ISSUE_LOCATION_SAMPLES,
    )


async def _execute_check_job(
    job_id: str,
    input_path: str,
    working_dir: str,
    *,
    college_id: str,
    include_semantic: bool,
    conversation_id: Optional[str],
    recheck: bool,
    terminology_strict: bool = False,
    terminology_b04_domain_exempt: bool = False,
) -> None:
    async def report(progress: int, stage: str, message: str):
        await _run_blocking(
            check_job_store.update_progress,
            job_id,
            progress=progress,
            stage=stage,
            message=message,
        )

    try:
        await report(10, "validating", "上传文件校验完成")
        result = await _process_saved_docx(
            input_path,
            working_dir,
            college_id=college_id,
            include_semantic=include_semantic,
            conversation_id=conversation_id,
            recheck=recheck,
            terminology_strict=terminology_strict,
            terminology_b04_domain_exempt=terminology_b04_domain_exempt,
            progress=report,
        )
        await _run_blocking(check_job_store.succeed, job_id, result)
    except asyncio.CancelledError:
        await _run_blocking(
            check_job_store.fail, job_id, "服务正在关闭，本次检测未完成，请重新上传论文"
        )
        raise
    except Exception:
        logger.exception("异步文件检测失败（job_id=%s）", job_id)
        await _run_blocking(
            check_job_store.fail, job_id, "检测失败，请检查 DOCX 文件后重试"
        )
    finally:
        await _run_blocking(_remove_temp_dir, working_dir)


def _job_status_payload(job: dict) -> dict:
    return {key: job[key] for key in (
        "job_id", "status", "progress", "stage", "message", "result_available",
        "created_at", "updated_at", "expires_at",
    )}


@app.post(
    "/api/check-jobs/create",
    response_model=CheckJobAcceptedResponse,
    status_code=202,
    responses=_error_responses(400, 409, 413, 429, 500),
    summary="创建异步论文检测任务",
)
async def create_check_job(
    file: UploadFile = File(..., description="待检测的 docx 论文文件"),
    college_id: str = Form(..., description="学院 ID，如 jlu_cs"),
    job_id: Optional[str] = Form(
        None, description="客户端预生成的 UUID，用于创建响应丢失后的任务恢复"
    ),
    include_semantic: bool = Form(True, description="是否执行语义检查"),
    conversation_id: Optional[str] = Form(None, description="会话 ID"),
    recheck: bool = Form(False, description="是否为修复后的重检"),
    terminology_strict: bool = Form(
        False,
        description="术语严格模式：AI 等 B 级条件豁免词仍要求首次解释",
    ),
    terminology_b04_domain_exempt: bool = Form(
        False,
        description="B04 医学词条按论文实际研究领域豁免；默认关闭",
    ),
):
    try:
        normalized_job_id = str(uuid.UUID(job_id)) if job_id else str(uuid.uuid4())
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="job_id 必须是合法 UUID")

    os.makedirs(CHECK_JOB_FILES_DIR, exist_ok=True)
    working_dir = tempfile.mkdtemp(
        prefix=f"{normalized_job_id}_", dir=CHECK_JOB_FILES_DIR
    )
    try:
        input_path = await _save_docx_upload(file, working_dir)
        job = await _run_blocking(check_job_store.create, normalized_job_id)
    except DuplicateJobId:
        await _run_blocking(_remove_temp_dir, working_dir)
        raise HTTPException(status_code=409, detail="job_id 已存在，请直接查询任务状态")
    except TooManyActiveJobs:
        await _run_blocking(_remove_temp_dir, working_dir)
        raise HTTPException(status_code=429, detail="待处理检测任务已达上限，请稍后重试")
    except Exception:
        await _run_blocking(_remove_temp_dir, working_dir)
        raise

    task = asyncio.create_task(
        _execute_check_job(
            normalized_job_id,
            input_path,
            working_dir,
            college_id=college_id,
            include_semantic=include_semantic,
            conversation_id=conversation_id,
            recheck=recheck,
            terminology_strict=terminology_strict,
            terminology_b04_domain_exempt=terminology_b04_domain_exempt,
        ),
        name=f"check-job-{normalized_job_id}",
    )
    app.state.check_tasks.add(task)
    task.add_done_callback(app.state.check_tasks.discard)
    return {
        key: job[key]
        for key in (
            "job_id", "status", "progress", "stage", "message", "created_at", "expires_at"
        )
    }


@app.post(
    "/api/check-jobs/status",
    response_model=CheckJobStatusResponse,
    responses=_error_responses(404),
    summary="查询异步论文检测进度",
)
async def get_check_job_status(request: CheckJobLookupRequest):
    job = await _run_blocking(check_job_store.get, str(request.job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="检测任务不存在或结果已过期")
    return _job_status_payload(job)


@app.post(
    "/api/check-jobs/result",
    response_model=CheckJobResultResponse,
    responses=_error_responses(404, 409, 500),
    summary="获取异步论文检测结果",
)
async def get_check_job_result(request: CheckJobLookupRequest):
    job = await _run_blocking(check_job_store.get, str(request.job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="检测任务不存在或结果已过期")
    if job["status"] != "succeeded":
        detail = job["message"] if job["status"] == "failed" else "检测尚未完成"
        raise HTTPException(status_code=409, detail=detail)
    if not job["result_available"]:
        raise HTTPException(status_code=500, detail="检测结果读取失败，请重新上传论文")
    # 兼容本次修复前落库的任务：旧结果的 advisory_issues 没有 fixable，
    # 读取时补一次标注，避免历史任务一直 500。
    _annotate_result_issues(job["result"], llm_available=_detection_service() is not None)
    return {"job_id": job["job_id"], "status": "succeeded", "result": job["result"]}


@app.post(
    "/api/check-file",
    response_model=CheckResponse,
    responses=_error_responses(400, 404, 413, 500),
    summary="上传论文并执行格式检测",
)
async def check_paper_file(
    file: UploadFile = File(..., description="待检测的 docx 论文文件"),
    college_id: str = Form(..., description="学院 ID，如 jlu_cs"),
    include_semantic: bool = Form(
        True,
        description="是否执行语义检查；AI 服务不可用时自动只跑零 LLM 的确定性条款"
                    "（目录/致谢/参考文献/列表序号/单位/数字）",
    ),
    conversation_id: Optional[str] = Form(
        None, description="会话 ID，传入时把检测结果写入会话上下文，供后续对话引用"
    ),
    recheck: bool = Form(
        False,
        description="重检模式：同一篇论文修复后再次检测，保留会话对话历史，只替换问题列表",
    ),
    terminology_strict: bool = Form(
        False,
        description="术语严格模式：AI 等 B 级条件豁免词仍要求首次解释",
    ),
    terminology_b04_domain_exempt: bool = Form(
        False,
        description="B04 医学词条按论文实际研究领域豁免；默认关闭",
    ),
):
    """解析上传的 DOCX，并将解析结果交给现有检测编排层。"""
    tmp_dir = tempfile.mkdtemp(prefix="thesis_parse_")
    try:
        input_path = await _save_docx_upload(file, tmp_dir)
        return await _process_saved_docx(
            input_path,
            tmp_dir,
            college_id=college_id,
            include_semantic=include_semantic,
            conversation_id=conversation_id,
            recheck=recheck,
            terminology_strict=terminology_strict,
            terminology_b04_domain_exempt=terminology_b04_domain_exempt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        # 不同客户端回显内部路径：真实原因进日志，响应只给固定文案。
        logger.error("文件检测依赖的内部文件缺失：%s", e)
        raise HTTPException(status_code=404, detail="检测依赖的内部文件缺失，请联系管理员")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("文件检测失败")
        raise HTTPException(status_code=500, detail="检测失败，请检查 DOCX 文件后重试")
    finally:
        await _run_blocking(_remove_temp_dir, tmp_dir)


# ========== 数据模型 ==========
class CollegeInfo(BaseModel):
    """学院坐标：id 是主键，学校/学院/专业/模板版本是坐标字段（见 config/colleges.json）。"""

    college_id: str
    college_name: str
    display_name: str = ""            # 学校·学院（版本），前端下拉直接用
    school: Optional[str] = None
    school_name: Optional[str] = None
    college: Optional[str] = None
    major: Optional[str] = None
    major_name: Optional[str] = None
    template_version: Optional[str] = None
    effective_year: Optional[int] = None


class SourceItem(BaseModel):
    chapter: str
    section: str
    full_path: str
    content: str
    preview: str
    rerank_score: Optional[float] = None


class RAGRequest(BaseModel):
    college_id: str
    question: str
    conversation_id: Optional[str] = None
    # 只走规范问答：规则解释弹窗等场景用，避免自然语言被对话意图层拆成多问题/论文类问题
    spec_only: bool = False


class ChatIssueTarget(BaseModel):
    """精确高亮目标：规则号 + 定位。前端按两者一起匹配，只亮那一条。"""
    rule_id: str = ""
    location: str = ""


class ChatActionItem(BaseModel):
    """对话动作：前端据此跳转/高亮，不让模型复述清单。"""
    type: Literal["open-result", "highlight-issues"]
    rule_ids: List[str] = Field(default_factory=list)
    targets: List[ChatIssueTarget] = Field(default_factory=list)
    hint: Optional[str] = None


class ClarifyPayload(BaseModel):
    """澄清追问：问题 + 可点选的选项。"""
    kind: Literal["paper_index", "heading_level", "multi_question"]
    question: str
    options: List[str] = Field(default_factory=list)


class ChatIssueCard(BaseModel):
    """对话中的单条问题卡片：字段直接来自检测结果。"""
    index: Optional[int] = None
    rule_id: str = ""
    category: str = ""
    severity: str = ""
    location: str = ""
    message: str = ""
    expected: str = ""
    suggestion: str = ""
    actual: Any = None
    basis: str = ""


class ChatBlock(BaseModel):
    """结构化回答块；前端优先渲染 blocks，缺省时回退到 conclusion。"""
    type: Literal["summary", "issues", "text"]
    title: str = ""
    text: str = ""
    issues: List[ChatIssueCard] = Field(default_factory=list)


class ChatSuggestion(BaseModel):
    """可点击的后续问题；只提供当前已支持的问法。"""
    label: str
    query: str


class RAGResponse(BaseModel):
    college: str
    question: str
    conclusion: str
    basis: str
    suggestion: str
    # 结构化回答字段：全部带默认值，旧客户端/旧缓存仍可用。
    can_judge: Optional[bool] = None
    observed: List[str] = Field(default_factory=list)
    unknown: List[str] = Field(default_factory=list)
    answer_schema_version: str = "1.0"
    sources: List[SourceItem]
    found: bool
    confidence: Optional[float] = None
    total_time: float
    from_cache: bool = False
    # 对话编排新增字段：全部带默认值，旧客户端不受影响
    intent: str = "spec_qa"
    slots: dict = Field(default_factory=dict)
    actions: List[ChatActionItem] = Field(default_factory=list)
    blocks: List[ChatBlock] = Field(default_factory=list)
    suggestions: List[ChatSuggestion] = Field(default_factory=list)
    clarify: Optional[ClarifyPayload] = None
    answer_source: Literal[
        "paper_facts", "spec_kb", "out_of_scope", "clarify", "agent", "none"
    ] = "spec_kb"
    response_id: str = ""
    trace: List[str] = Field(default_factory=list)
    # 检测结果指纹：消息据此判断自己是不是基于旧检测结果（空 = 无检测结果）
    paper_version: str = ""


class ChatFeedbackRequest(BaseModel):
    """回答反馈：只收评分与元数据，不收问题/回答正文。"""
    response_id: str = Field(min_length=1, max_length=64)
    conversation_id: Optional[str] = None
    rating: Literal["helpful", "not_helpful", "off_topic"]
    intent: str = ""
    answer_source: str = ""


class ChatFeedbackResponse(BaseModel):
    status: Literal["success", "skipped"]
    message: str
    response_id: str



class BuildKBRequest(BaseModel):
    college_id: Optional[str] = None


class BuildKBResponse(BaseModel):
    status: Literal["success"]
    message: str


class CheckRequest(BaseModel):
    """论文格式检测请求：document 为解析契约，include_semantic 控制是否跑语义检查。"""
    college_id: str
    document: DocumentContract
    include_semantic: bool = True
    conversation_id: Optional[str] = None
    recheck: bool = False
    # 三态契约预留开关：本 PR 只进契约；运行时判定接入后生效，当前传值不改变检测结果。
    terminology_strict: bool = Field(
        default=False,
        description="严格模式：AI 等 B 级条件豁免词仍要求首次解释；"
                    "运行时判定接入后生效，当前版本仅落契约。",
    )
    terminology_b04_domain_exempt: bool = Field(
        default=False,
        description="B04 医学词条按论文实际研究领域豁免；"
                    "运行时判定接入后生效，当前版本仅落契约。",
    )


class SessionResetRequest(BaseModel):
    """清空会话对话历史（保留论文检测结果），用于前端“新对话”。"""
    conversation_id: str


class SessionResetResponse(BaseModel):
    status: Literal["success", "skipped"]
    message: str
    conversation_id: str


class HealthFeaturesResponse(BaseModel):
    hybrid_search: bool
    rerank: bool
    query_rewrite: bool
    self_check: bool
    cache: bool


class HealthResponse(BaseModel):
    status: Literal["healthy"]
    llm_initialized: bool
    llm_status: Literal["initializing", "ready", "failed"]
    llm_error: Optional[str]
    rag_initialized: bool
    rag_status: Literal["initializing", "ready", "failed"]
    rag_error: Optional[str]
    features: HealthFeaturesResponse


# ========== 接口定义 ==========
@app.get(
    "/api/colleges",
    response_model=List[CollegeInfo],
    responses=_error_responses(500),
    summary="获取所有可用学院列表（含学校/学院/专业/模板版本坐标）",
)
async def get_colleges():
    """获取所有支持的学院列表（只读学院配置，不等待知识库就绪）"""
    try:
        colleges = await _run_blocking(coordinates)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="学院配置缺失，请检查 config/colleges.json")
    return colleges


@app.post(
    "/api/rag/answer",
    response_model=RAGResponse,
    responses=_error_responses(400, 404, 500, 503),
    summary="RAG 问答",
)
async def rag_answer(request: RAGRequest):
    """
    基于知识库回答论文格式规范问题
    - **college_id**: 学院ID，如 jlu_cs、jlu_se
    - **question**: 用户问题
    - **conversation_id**: 可选会话ID，传入后启用多轮对话记忆
    """
    rag = _require_rag()
    try:
        result = await _run_blocking(
            rag.answer,
            request.college_id,
            request.question,
            conversation_id=request.conversation_id,
            spec_only=request.spec_only,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        # 知识库文件路径同样不外泄。
        logger.error("RAG 问答依赖的文件缺失：%s", e)
        raise HTTPException(status_code=404, detail="知识库文件缺失，请联系管理员")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("RAG 问答失败")
        raise HTTPException(status_code=500, detail="RAG 问答失败，请稍后重试")


@app.post(
    "/api/rag/feedback",
    response_model=ChatFeedbackResponse,
    responses=_error_responses(400, 503),
    summary="回答反馈（有用 / 没用 / 答非所问）",
)
async def rag_feedback(request: ChatFeedbackRequest):
    """记录一条回答反馈。

    只保存 response_id、评分和来源元数据，不接收也不保存问题/回答正文。
    """
    rag = _require_rag()
    saved = await _run_blocking(
        rag.save_feedback,
        request.response_id,
        conversation_id=request.conversation_id or "",
        rating=request.rating,
        intent=request.intent,
        answer_source=request.answer_source,
    )
    return {
        "status": "success" if saved else "skipped",
        "message": "反馈已记录" if saved else "反馈未记录",
        "response_id": request.response_id,
    }


@app.post(
    "/api/kb/build",
    response_model=BuildKBResponse,
    responses=_error_responses(400, 500, 503),
    summary="构建知识库",
)
async def build_kb(request: BuildKBRequest):
    """构建知识库"""
    rag = _require_rag()
    try:
        if request.college_id:
            await _run_blocking(rag.kb_manager.build_kb, request.college_id)
            return {"status": "success", "message": f"知识库构建完成：{request.college_id}"}
        else:
            await _run_blocking(rag.kb_manager.build_all)
            return {"status": "success", "message": "全部知识库构建完成"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("知识库构建失败")
        raise HTTPException(status_code=500, detail="知识库构建失败，请稍后重试")


@app.post(
    "/api/session/reset",
    response_model=SessionResetResponse,
    responses=_error_responses(503),
    summary="清空会话对话历史（保留论文检测结果）",
)
async def reset_session(request: SessionResetRequest):
    """
    开启“新对话”：清空该会话的对话历史，但保留绑定的论文检测结果。
    - **conversation_id**: 会话ID
    """
    rag = _require_rag()
    cleared = await _run_blocking(rag.reset_history, request.conversation_id)
    return {
        "status": "success" if cleared else "skipped",
        "message": "对话历史已清空" if cleared else "会话不存在或已无对话历史",
        "conversation_id": request.conversation_id,
    }


@app.post(
    "/api/check",
    response_model=CheckResponse,
    responses=_error_responses(400, 404, 500),
    summary="论文格式检测（规则引擎 + 语义检查）",
)
async def check_paper(request: CheckRequest):
    """
    对一篇论文解析结果执行完整检测。
    - **college_id**: 学院ID，如 jlu_cs
    - **document**: 论文解析契约 dict（blocks/sections）
    - **include_semantic**: 是否执行语义检查；为 false 时只跑确定性规则引擎。
      为 true 时零 LLM 的确定性语义条款始终执行，定位类条款需要 AI 服务
    - **conversation_id**: 可选，传入时把检测结果写入会话上下文，供后续对话引用
    - **recheck**: 为 true 时视为同一篇论文修复后的重检，保留会话对话历史
    """
    try:
        ai_service = _detection_service()
        result = await _run_blocking(
            check_document,
            document=request.document.model_dump(exclude_unset=True),
            college_id=request.college_id,
            rag_service=ai_service,
            include_semantic=request.include_semantic,
            semantic_skip_reason=_semantic_skip_reason(request.include_semantic),
            terminology_strict=request.terminology_strict,
            terminology_b04_domain_exempt=request.terminology_b04_domain_exempt,
        )
        _annotate_result_issues(result, llm_available=ai_service is not None)
        if request.conversation_id and rag_service is not None:
            await _run_blocking(
                rag_service.bind_paper_errors,
                request.conversation_id,
                request.college_id,
                result["issues"],
                keep_history=request.recheck,
                unchecked=result.get("unchecked"),
            )
        return await _run_blocking(
            compact_check_result,
            result,
            max_returned=MAX_RETURNED_ISSUES,
            max_location_samples=MAX_ISSUE_LOCATION_SAMPLES,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        # 同 /api/check-file：内部路径只写日志，不进响应正文。
        logger.error("结构化检测依赖的内部文件缺失：%s", e)
        raise HTTPException(status_code=404, detail="检测依赖的内部文件缺失，请联系管理员")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("结构化文档检测失败")
        raise HTTPException(status_code=500, detail="检测失败，请检查 document 后重试")


@app.post(
    "/api/fix",
    response_class=FileResponse,
    responses={
        200: {
            "description": "修复后的完整 DOCX 文件",
            "content": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
            "headers": {
                "X-Applied-Count": {"schema": {"type": "integer"}},
                "X-Skipped-Count": {"schema": {"type": "integer"}},
                "X-Llm-Calls": {"schema": {"type": "integer"}},
                "X-Fixed-Indexes": {"schema": {"type": "string"}},
                "X-Writeback-Skip-Count": {"schema": {"type": "integer"}},
                "X-Writeback-Warnings": {"schema": {"type": "string"}},
            },
        },
        **_error_responses(400, 413, 500, 503),
    },
    summary="写回修复（格式 + 文字），返回完整论文文件",
)
async def fix_paper(
    file: UploadFile = File(..., description="原始 docx 论文文件"),
    college_id: str = Form(..., description="学院 ID，如 jlu_cs"),
    issues_json: str = Form(..., description="用户确认修复的 issue 列表（JSON 字符串）"),
    images: List[UploadFile] = File(
        default=[], description="图片类修复项的新图片文件，按 issue 出现顺序对应"
    ),
    conversation_id: Optional[str] = Form(
        None, description="会话 ID，传入时把已修复项从会话检测结果中同步移除"
    ),
):
    """
    对用户确认的 issue 执行格式写回 + 文字改写，返回修复后的完整 docx。
    - **file**: 原始论文 docx（写回的载体，保存时保留其余全部内容）
    - **college_id**: 学院 ID，用于语义改写时的知识库检索
    - **issues_json**: 前端从 /api/check 结果中筛选、用户确认修复的 issue 列表 JSON。
      题注文字（caption_text）、页边距等区块级属性直接放在 issue 内。
    - **images**: 对 rule_id 为 image.replace / image.insert 的修复项，
      按其在 issues 中的出现顺序上传对应的新图片文件；后端注入 issue["image_bytes"]。
    - **conversation_id**: 会话 ID（可选），传入时把本次真正修复的项从会话的
      检测结果里移除，使对话上下文与屏幕上的列表保持一致。

    响应头携带修复统计：X-Applied-Count / X-Skipped-Count / X-Llm-Calls，
    另有 X-Fixed-Indexes（本次真正修复项在 issues 中的下标，逗号分隔），
    供前端把已修复项从结果列表中移除。

    写回层自身的失败（例如段落含域/图形无法安全改写、多节文档做结构性变更、
    区块契约外的字段）不会中断整次写回：这些条目被跳过并汇总，响应头给出
    X-Writeback-Skip-Count（跳过条数）与 X-Writeback-Warnings（JSON 数组，
    非 ASCII 字符按 \\uXXXX 转义，便于直接 JSON.parse），同时逐条写入服务端日志。
    注意这些跳过项是"写回层面"的，不会出现在 X-Fixed-Indexes 里：
    X-Fixed-Indexes 只统计 fix_document 真正改动的 issue。
    """
    issues = _parse_fix_issues(issues_json)

    # 把上传的图片按需注入到对应 issue 的 image_bytes（二进制不经过 JSON）
    image_iter = iter(images)
    for issue in issues:
        if issue.get("rule_id") in IMAGE_RULE_IDS and not issue.get("image_bytes"):
            try:
                image = next(image_iter)
            except StopIteration:
                raise HTTPException(
                    status_code=400,
                    detail=f"图片类修复项 {issue.get('rule_id')} 缺少对应上传的图片文件",
                )
            issue["image_bytes"] = await image.read()

    tmp_dir = tempfile.mkdtemp(prefix="thesis_fix_")
    out_path = os.path.join(tmp_dir, "output.docx")
    try:
        in_path = await _save_docx_upload(file, tmp_dir)
        has_semantic_issues = any(
            issue.get("source") == "rag" for issue in issues
        )
        if has_semantic_issues:
            rag = _require_detection_service()
        else:
            rag = None
        # 修复 JSON 后在原始 DOCX 包上原位写回；显式重建只属于 /api/rebuild。
        json_path = await _run_blocking(docx_to_json, in_path, output_dir=tmp_dir,
                                       structure=load_structure(college_id))
        document = await _run_blocking(_load_document_contract, json_path)
        _validate_issue_targets(issues, document)
        original_document = copy.deepcopy(document)
        source_ids = {id(block): index for index, block in enumerate(document["blocks"])}
        result = await _run_blocking(
            fix_document,
            document,
            issues,
            college_id=college_id,
            rag_service=rag,
            images_dir=images_dir(tmp_dir),
        )
        await _run_blocking(DocumentContract.model_validate, document)
        writeback_report = await _run_blocking(
            patch_docx,
            in_path, out_path, original_document, document, source_ids,
            images_dir=images_dir(tmp_dir),
        )
        # 自动编号（numbering.xml）单独修：保留 Word 自动编号，只改该级 numFmt。
        numbering_report = await _run_blocking(
            patch_numbering_formats,
            out_path, original_document.get("blocks") or [], issues,
        )
        if numbering_report["applied"]:
            result["format"]["applied"].extend(numbering_report["applied"])
            result["applied_count"] += len(numbering_report["applied"])
        if numbering_report["skipped"]:
            result["format"]["skipped"].extend(numbering_report["skipped"])
            result["skipped_count"] += len(numbering_report["skipped"])
        for skip_reason in writeback_report.get("skipped", []):
            logger.warning("/api/fix 写回跳过：%s", skip_reason)
    except ValidationError:
        await _run_blocking(_remove_temp_dir, tmp_dir)
        raise HTTPException(status_code=400, detail="文档解析契约校验失败")
    except ValueError as e:
        await _run_blocking(_remove_temp_dir, tmp_dir)
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        await _run_blocking(_remove_temp_dir, tmp_dir)
        raise
    except Exception as e:
        await _run_blocking(_remove_temp_dir, tmp_dir)
        logger.exception("文档写回失败")
        raise HTTPException(status_code=500, detail="写回失败，请检查 DOCX 和修复项后重试")

    # 写回配对只产生候选；导出重检成功后才确认解决并从会话移除。
    applied_indexes = fixed_issue_indexes(issues, result)
    fixed_indexes = []
    verification = {}
    try:
        if applied_indexes:
            verification = await _run_blocking(
                verify_export, out_path, os.path.join(tmp_dir, "recheck"), original_document,
                college_id, issues, applied_indexes, in_path, rag,
            )
            fixed_indexes = verification.get("confirmed") or []
    except Exception:
        logger.exception("导出文件重检失败，保留所有未确认的问题")
    if verification.get("parts_unchanged") is False:
        writeback_report.setdefault("skipped", []).append(
            "导出文件改动了非文档部件，本次不确认任何修复")
    if verification.get("non_target_unchanged") is False:
        writeback_report.setdefault("skipped", []).append(
            "导出文件改动了非目标段落/表格，本次不确认任何修复")
    if verification.get("non_target_unchanged") is None and applied_indexes:
        writeback_report.setdefault("skipped", []).append(
            "导出文件块数变化，未做非目标内容逐块比对，请人工核对")
    unconfirmed = sorted(set(applied_indexes) - set(fixed_indexes))
    if unconfirmed:
        writeback_report.setdefault("skipped", []).append(
            "已执行修改但尚未重检确认解决，请重新检测或人工核对，提交项下标：" +
            ",".join(map(str, unconfirmed)))
    if rag_service is not None:
        await _run_blocking(
            rag_service.remove_fixed_issues,
            conversation_id, [issues[position] for position in fixed_indexes]
        )

    headers = {
        "X-Applied-Count": str(result.get("applied_count", 0)),
        "X-Skipped-Count": str(result.get("skipped_count", 0)),
        "X-Llm-Calls": str(result.get("llm_calls", 0)),
    }
    writeback_skips = list(writeback_report.get("skipped", []))
    if writeback_skips:
        headers["X-Writeback-Skip-Count"] = str(len(writeback_skips))
        # 响应头只能放 ASCII：转义成 \uXXXX 后仍是合法 JSON，前端可直接 JSON.parse
        encoded = json.dumps(writeback_skips, ensure_ascii=True)
        if len(encoded) <= MAX_WARNING_HEADER_CHARS:
            headers["X-Writeback-Warnings"] = encoded
        else:
            logger.warning(
                "/api/fix 写回跳过明细过多（%d 条），已跳过 X-Writeback-Warnings 响应头",
                len(writeback_skips),
            )
    # 修复项很多时下标串会很长，超限就不回传，让前端退化为"手动重新检测"
    fixed_header = ",".join(str(position) for position in fixed_indexes)
    if len(fixed_header) <= MAX_FIXED_HEADER_CHARS:
        headers["X-Fixed-Indexes"] = fixed_header
    elif fixed_indexes:
        logger.warning(
            "/api/fix 修复项过多（%d 条），已跳过 X-Fixed-Indexes 响应头", len(fixed_indexes)
        )

    def cleanup():
        _remove_temp_dir(tmp_dir)

    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="fixed_paper.docx",
        headers=headers,
        background=BackgroundTask(cleanup),
    )


@app.post(
    "/api/rebuild",
    response_class=FileResponse,
    responses={
        200: {
            "description": "按解析契约重建的完整 DOCX 文件",
            "content": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        },
        **_error_responses(400, 413, 500),
    },
    summary="按解析契约重建 docx（图片/表格/公式可生成新版）",
)
async def rebuild_paper(
    file: UploadFile = File(..., description="原始 docx 论文文件"),
    college_id: Optional[str] = Form(
        None, description="学院 ID，如 jlu_cs；传入时按该院的结构识别表解析"),
):
    """
    把上传的 docx 解析成契约 JSON，再经 json_to_docx 重建链生成新 docx。
    用于“生成新文档”模式：校验、归一化，或在契约块上做增删后重建。
    """
    tmp_dir = tempfile.mkdtemp(prefix="thesis_rebuild_")
    out_path = os.path.join(tmp_dir, "rebuilt.docx")
    try:
        in_path = await _save_docx_upload(file, tmp_dir)
        json_path = await _run_blocking(docx_to_json, in_path, output_dir=tmp_dir,
                                       structure=load_structure(college_id))
        await _run_blocking(_load_document_contract, json_path)
        await _run_blocking(json_to_docx, json_path, output_docx=out_path)
    except ValueError as e:
        await _run_blocking(_remove_temp_dir, tmp_dir)
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        await _run_blocking(_remove_temp_dir, tmp_dir)
        raise
    except Exception as e:
        await _run_blocking(_remove_temp_dir, tmp_dir)
        logger.exception("文档重建失败")
        raise HTTPException(status_code=500, detail="重建失败，请检查 DOCX 文件后重试")

    def cleanup():
        _remove_temp_dir(tmp_dir)

    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="rebuilt_paper.docx",
        background=BackgroundTask(cleanup),
    )


@app.get("/api/health", response_model=HealthResponse, summary="健康检查")
async def health_check():
    """健康检查。llm_status / rag_status 为 initializing 时表示后端可用、AI 能力仍在后台加载。"""
    return {
        "status": "healthy",
        "llm_initialized": llm_service is not None,
        "llm_status": llm_status,
        "llm_error": llm_error,
        "rag_initialized": rag_service is not None,
        "rag_status": rag_status,
        "rag_error": rag_error,
        "features": {
            "hybrid_search": True,
            "rerank": True,
            "query_rewrite": True,
            "self_check": True,
            "cache": True
        }
    }


# ========== 启动入口 ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.api:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
