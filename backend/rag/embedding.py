"""本地 ONNX 文本嵌入。

替代 sentence-transformers + torch：只依赖 onnxruntime 与 tokenizers
（两者本来就是 chromadb 的依赖），推理栈体积因此减少约 520MB。

数值行为与原先的 HuggingFaceEmbeddings(BAAI/bge-small-zh-v1.5) 严格对齐：
BERT 编码 → 取 [CLS] 位置向量 → L2 归一化；最大长度 512；不加任何查询前缀。
换行替换为空格的处理也照搬原实现，避免分词结果出现偏差。

分词器的大小写行为见 _align_normalizer：原方案会把西文小写化，这里必须复刻，
否则含 "Times New Roman" 之类的段落会整片变成 [UNK]，向量严重偏移。

模型会话在首次使用时才创建，构造对象本身不触碰磁盘与网络。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, List

import numpy as np
from pydantic import BaseModel, ConfigDict, PrivateAttr

from langchain_core.embeddings import Embeddings

from backend.config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIR,
    EMBEDDING_LOWERCASE,
    EMBEDDING_MAX_LENGTH,
    EMBEDDING_MODEL,
    EMBEDDING_ONNX_PRECISION,
    EMBEDDING_ONNX_REPO,
    EMBEDDING_TOKENIZER_REPO,
)

# models/ 为运行期资源，不进入版本库；config.py 里的相对路径一律相对项目根目录。
# backend/rag/embedding.py → rag → backend → 仓库根：模型目录按仓库根解析，
# 与 config.py 里"相对仓库根"的写法保持一致。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 精度 → (来源仓库内的路径, 本地文件名)。取自 Xenova 的 transformers.js 转换仓库。
# 实测（36 段知识库 + 12 条真实查询，对照原 torch 流水线）：
#   fp32 余弦 min=1.0000、检索 top1/top5 100% 一致
#   int8 余弦 min=0.9836、检索 top1 83.3%、top5 90.0%
# 所以默认走 fp32，int8 仅作为极限省空间的备选。
_PRECISION_FILES = {
    "int8": ("onnx/model_quantized.onnx", "model-int8.onnx"),
    "fp32": ("onnx/model.onnx", "model-fp32.onnx"),
}


def _require_asset(target: Path, repo: str, remote_path: str) -> Path:
    """确认本地资产在位。项目不下载模型：缺文件只能由使用者补齐。"""
    if target.is_file() and target.stat().st_size > 0:
        return target
    raise RuntimeError(
        f"缺少模型资产：{target}\n"
        f"该文件不随仓库分发，程序也不会自动下载，请自行放置"
        f"（来源：{repo} 的 {remote_path}）。"
    )


class OnnxEmbeddings(BaseModel, Embeddings):
    """与 langchain_huggingface.HuggingFaceEmbeddings 接口一致的本地方案。

    刻意沿用 HuggingFaceEmbeddings 的写法：pydantic BaseModel + Embeddings，
    运行期对象挂在私有属性上。这样 langchain_chroma 一侧无需任何改动。
    """

    model_config = ConfigDict(
        extra="forbid", protected_namespaces=(), populate_by_name=True
    )

    model_name: str = EMBEDDING_MODEL
    model_dir: str = EMBEDDING_DIR
    precision: str = EMBEDDING_ONNX_PRECISION
    max_length: int = EMBEDDING_MAX_LENGTH
    batch_size: int = EMBEDDING_BATCH_SIZE
    lowercase: bool = EMBEDDING_LOWERCASE

    _session: Any = PrivateAttr(default=None)
    _tokenizer: Any = PrivateAttr(default=None)
    _output_index: int = PrivateAttr(default=0)
    _input_names: tuple = PrivateAttr(default=())
    _lock: Any = PrivateAttr(default=None)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._lock = threading.Lock()

    @property
    def model_dir_path(self) -> Path:
        path = Path(self.model_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def _load(self) -> None:
        """创建 ONNX 会话与分词器；资产就位检查与加载都发生在这里。"""
        precision = self.precision.lower()
        if precision not in _PRECISION_FILES:
            raise ValueError(
                f"不支持的 EMBEDDING_ONNX_PRECISION={self.precision!r}，"
                f"可选：{', '.join(_PRECISION_FILES)}"
            )
        remote_path, local_name = _PRECISION_FILES[precision]

        model_path = _require_asset(
            self.model_dir_path / local_name, EMBEDDING_ONNX_REPO, remote_path
        )
        tokenizer_path = _require_asset(
            self.model_dir_path / "tokenizer.json",
            EMBEDDING_TOKENIZER_REPO,
            "tokenizer.json",
        )

        from tokenizers import Tokenizer, normalizers

        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._align_normalizer(tokenizer, normalizers)
        pad_id = tokenizer.token_to_id("[PAD]")
        tokenizer.enable_truncation(max_length=self.max_length)
        tokenizer.enable_padding(
            pad_id=pad_id if pad_id is not None else 0, pad_token="[PAD]"
        )

        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )

        outputs = session.get_outputs()
        names = [output.name for output in outputs]
        if "last_hidden_state" in names:
            output_index = names.index("last_hidden_state")
        else:
            # 兜底：取第一个三维输出（[batch, seq, hidden]）
            output_index = next(
                (i for i, output in enumerate(outputs) if len(output.shape) == 3), 0
            )

        self._input_names = tuple(item.name for item in session.get_inputs())
        self._output_index = output_index
        self._tokenizer = tokenizer
        self._session = session

    def _align_normalizer(self, tokenizer: Any, normalizers: Any) -> None:
        """把分词器的大小写行为对齐到原先的 sentence-transformers 方案。

        BAAI/bge-small-zh-v1.5 的 tokenizer.json 里 normalizer 标的是
        lowercase=false，tokenizer_config.json 也写着 do_lower_case=false，
        但 sentence-transformers 实际走的是 vocab.txt 后端，会先小写化：
        "Times New Roman" → times / new / roman，而快速分词器会在这里吐出
        成片的 [UNK]。只覆盖 lowercase，其余 normalizer 参数保持 tokenizer.json
        的取值（clean_text / handle_chinese_chars / strip_accents）。
        """
        normalizer = tokenizer.normalizer
        if not isinstance(normalizer, normalizers.BertNormalizer):
            return
        if normalizer.lowercase == self.lowercase:
            return
        tokenizer.normalizer = normalizers.BertNormalizer(
            clean_text=normalizer.clean_text,
            handle_chinese_chars=normalizer.handle_chinese_chars,
            strip_accents=normalizer.strip_accents,
            lowercase=self.lowercase,
        )

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is None:
                self._load()

    def _run_batch(self, batch: List[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(list(batch))
        feeds = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = np.array([e.ids for e in encodings], dtype=np.int64)
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = np.array(
                [e.attention_mask for e in encodings], dtype=np.int64
            )
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.array(
                [e.type_ids for e in encodings], dtype=np.int64
            )

        hidden = self._session.run(None, feeds)[self._output_index]
        # CLS 池化：BGE 的输出取第 0 个 token（[CLS]）
        cls = np.asarray(hidden[:, 0], dtype=np.float32)
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        return cls / np.maximum(norms, 1e-12)

    def _embed(self, texts: List[str]) -> np.ndarray:
        self._ensure_loaded()
        # 与原 HuggingFaceEmbeddings._embed 保持一致
        cleaned = [text.replace("\n", " ") for text in texts]
        if not cleaned:
            dimension = int(self._session.get_outputs()[self._output_index].shape[-1])
            return np.zeros((0, dimension), dtype=np.float32)
        chunks = [
            self._run_batch(cleaned[i:i + self.batch_size])
            for i in range(0, len(cleaned), self.batch_size)
        ]
        return chunks[0] if len(chunks) == 1 else np.vstack(chunks)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._embed(list(texts)).tolist()

    def embed_query(self, text: str) -> List[float]:
        return self._embed([text])[0].tolist()
