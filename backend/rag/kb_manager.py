import os
import re
import time
from datetime import datetime
from typing import List, Optional
from backend.config import (
    EMBEDDING_MODEL,
    VECTOR_K, BM25_K, RECALL_TOP_K, SCORE_THRESHOLD,
    COLLEGES_CONFIG_PATH, KB_ROOT_DIR
)
from backend.rag.colleges import display_name, load_colleges
from backend.rag.kb_store import (
    FINGERPRINT_NAME,
    corpus_sha256,
    kb_dir,
    kb_key,
    needs_rebuild,
    read_corpus,
    read_fingerprint,
    write_fingerprint,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from backend.rag.embedding import OnnxEmbeddings
from langchain_chroma import Chroma
import chromadb
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from rank_bm25 import BM25Okapi
import jieba
from collections import Counter


class HybridRetriever(BaseRetriever):
    """
    混合检索器
    召回：BM25 + 向量
    """
    model_config = {"arbitrary_types_allowed": True}
    vectorstore: Optional[Chroma] = None
    bm25: Optional[BM25Okapi] = None
    all_docs: List[Document] = []

    # 召回参数
    vector_k: int = VECTOR_K
    bm25_k: int = BM25_K
    recall_top_k: int = RECALL_TOP_K
    score_threshold: float = SCORE_THRESHOLD

    # 性能统计
    last_search_time: float = 0
    last_recall_time: float = 0
    last_recall_count: int = 0

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        """检索主入口"""
        start_time = time.time()
        recalled_docs = self._recall(query)
        self.last_recall_time = time.time() - start_time
        self.last_recall_count = len(recalled_docs)
        self.last_search_time = time.time() - start_time
        return recalled_docs

    def _recall(self, query: str) -> List[Document]:
        """召回阶段：BM25 精确命中优先，向量语义补位。

        bm25_docs 已过滤 0 分，仅含正分命中；不足 top-k 时，
        由向量检索按相似度降序补位，不再让 0 分 chunk 顶位。
        """
        bm25_docs = self._bm25_search(query)
        vector_docs = self._vector_search(query)
        # 合并去重，BM25 正分命中优先，向量补位
        merged = []
        seen_texts = set()
        for doc in bm25_docs:
            if doc.page_content not in seen_texts:
                merged.append(doc)
                seen_texts.add(doc.page_content)
        for doc in vector_docs:
            if doc.page_content not in seen_texts:
                merged.append(doc)
                seen_texts.add(doc.page_content)
        return merged[:self.recall_top_k]

    def _vector_search(self, query: str) -> List[Document]:
        """向量检索 - 语义匹配"""
        if not self.vectorstore:
            return []
        retriever = self.vectorstore.as_retriever(
            search_type="similarity_score_threshold",
            search_kwargs={"k": self.vector_k, "score_threshold": self.score_threshold}
        )
        return retriever.invoke(query)

    def _bm25_search(self, query: str) -> List[Document]:
        """BM25 检索 - 关键词精确匹配。

        只保留正分命中（score > 0），0 分 chunk 不进入候选，
        避免无命中时把无关 chunk 按原始顺序顶进 top-k。
        """
        if not self.bm25 or not self.all_docs:
            return []
        query_tokens = list(jieba.cut(query))
        scores = self.bm25.get_scores(query_tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        top_indices = [i for i in top_indices if scores[i] > 0][:self.bm25_k]
        return [self.all_docs[i] for i in top_indices]


class KBManager:
    """知识库管理器"""

    def __init__(self, config_path=COLLEGES_CONFIG_PATH, kb_root=KB_ROOT_DIR):
        self.colleges = load_colleges(config_path)
        # 知识库根目录：库的目录名是"语料哈希"，不再由配置逐学院指定（见 rag/kb_store）
        self.kb_root = kb_root

        # 注册 jieba 自定义词典（防止复合词被拆碎）
        self._register_jieba_words()

        # 加载 Embedding 模型（仅构造对象，权重在首次向量化时才载入）
        print("正在准备向量模型...")
        self.embeddings = OnnxEmbeddings()
        print(f"✅ 向量模型就绪：{EMBEDDING_MODEL}（ONNX {self.embeddings.precision}）\n")

        self._retrievers = {}
        self._vocabs = {}  # 各学院高区分度词汇表

    def _register_jieba_words(self):
        """注册 jieba 自定义词典"""
        custom_words = [
            "一级标题", "二级标题", "三级标题",
            "图题", "表题", "图号", "表号", "图名", "表名",
            "摘要标题", "摘要正文", "关键词标题", "关键词内容",
            "正文字体", "正文行距",
            "页边距", "左页边距", "右页边距", "页眉边距", "页脚边距",
            "参考文献", "连续出版物",
            "公式编号", "公式排版",
            "目录格式", "目录内容",
            "致谢标题", "致谢正文",
            "Times New Roman",
            "1.5倍行距", "行间距",
        ]
        for w in custom_words:
            jieba.add_word(w)

    def _build_vocab(self, chunks, college_id):
        """
        从知识库文本中自动提取高区分度词汇表
        只保留 DF（文档频率）在 2~5 之间的词
        """
        word_doc_freq = Counter()
        for doc in chunks:
            full_text = doc.metadata.get('full_path', '') + ' ' + doc.page_content
            words = set(jieba.lcut(full_text))
            for w in words:
                if len(w) > 1:
                    word_doc_freq[w] += 1
        high_value_vocab = {w for w, df in word_doc_freq.items() if 2 <= df <= 5}
        self._vocabs[college_id] = high_value_vocab
        print(f"✅ 词汇表生成完成：{len(high_value_vocab)} 个高区分度词")
        return high_value_vocab

    def get_vocab(self, college_id):
        """获取学院的高区分度词汇表"""
        return self._vocabs.get(college_id, set())

    def list_colleges(self):
        return [(cid, display_name(info)) for cid, info in self.colleges.items()]

    @staticmethod
    def _reset_vector_store(kb_path):
        """清空持久化目录里已有的 collection。

        Chroma.from_documents 只追加不清空：重复构建会把同一批分块再存一份，
        检索时同一段文本会重复占掉召回位。
        """
        if not os.path.isdir(kb_path):
            return
        client = chromadb.PersistentClient(path=kb_path)
        for collection in client.list_collections():
            client.delete_collection(getattr(collection, "name", collection))

    def kb_key_for(self, college_id):
        """该学院当前语料对应的知识库键（语料内容 + 切片参数的哈希）。"""
        info = self.colleges[college_id]
        return kb_key(read_corpus(info["rule_file"]),
                      int(info.get("chunk_size", 300)),
                      int(info.get("chunk_overlap", 50)))

    def kb_path_for(self, college_id):
        """该学院当前语料的知识库目录：var/kb/<语料哈希>。

        路径不再写在配置里——语料一变键就变，翻不出"按旧规范建的库"这个坑；
        语料相同的学院命中同一个目录，天然共用一份库。
        """
        return kb_dir(self.kb_root, self.kb_key_for(college_id))

    def _register_college(self, college_id, path, key, info):
        """把学院记进库目录的指纹（共用同一个库的学院列在这里）。"""
        fingerprint = read_fingerprint(path) or {}
        colleges = [cid for cid in fingerprint.get("colleges", []) if cid != college_id]
        colleges.append(college_id)
        write_fingerprint(path, {
            "kb_key": key,
            "corpus_sha256": corpus_sha256(read_corpus(info["rule_file"])),
            "rule_file": info["rule_file"],
            "chunk_size": int(info.get("chunk_size", 300)),
            "chunk_overlap": int(info.get("chunk_overlap", 50)),
            "built_at": fingerprint.get("built_at") or _now(),
            "colleges": sorted(colleges),
        })

    def build_kb(self, college_id):
        """构建指定学院的知识库（语料与切片参数没变时直接复用，不重复建）。"""
        if college_id not in self.colleges:
            raise ValueError(f"未找到学院配置：{college_id}")
        config = self.colleges[college_id]
        rule_file = config["rule_file"]
        chunk_size = config.get("chunk_size", 300)
        chunk_overlap = config.get("chunk_overlap", 50)
        kb_path = self.kb_path_for(college_id)

        if not needs_rebuild(kb_path, self.kb_key_for(college_id)):
            self._register_college(college_id, kb_path, self.kb_key_for(college_id),
                                   config)
            others = [cid for cid in read_fingerprint(kb_path).get("colleges", [])
                      if cid != college_id]
            share = f"（与 {'、'.join(others)} 共用）" if others else ""
            print(f"✅ 知识库已是最新，直接复用：{kb_path}{share}")
            return self.get_retriever(college_id)

        print(f"=" * 50)
        print(f"正在构建知识库：{display_name(config)}")
        print(f"=" * 50)

        # 1. 读取文档
        with open(rule_file, "r", encoding="utf-8") as f:
            text = f.read()
        print(f"✅ 读取文档：{rule_file}（{len(text)}字）")

        # 2. 智能分块
        chunks = self._smart_split(text, chunk_size, chunk_overlap)
        print(f"✅ 文本分块：共 {len(chunks)} 块")

        # 3. 构建向量库（先清掉上一次的 collection，否则同一批分块会再存一份）
        print("正在构建向量库...")
        self._reset_vector_store(kb_path)
        vectorstore = Chroma.from_documents(
            documents=chunks,
            embedding=self.embeddings,
            persist_directory=kb_path
        )
        print(f"✅ 向量库已保存到：{kb_path}")

        # 4. 构建 BM25 索引
        print("正在构建 BM25 索引...")
        tokenized_corpus = [list(jieba.cut(doc.page_content)) for doc in chunks]
        bm25 = BM25Okapi(tokenized_corpus)
        print("✅ BM25 索引构建完成")

        # 5. 创建混合检索器
        retriever = HybridRetriever()
        retriever.vectorstore = vectorstore
        retriever.bm25 = bm25
        retriever.all_docs = chunks
        self._retrievers[college_id] = retriever
        print("✅ 混合检索器构建完成")

        # 6. 自动生成高区分度词汇表
        self._build_vocab(chunks, college_id)

        # 7. 落下指纹：这个库是按哪份语料、哪组切片参数建的，哪些学院共用
        self._register_college(college_id, kb_path, self.kb_key_for(college_id), config)
        print(f"✅ 指纹已写入：{os.path.join(kb_path, FINGERPRINT_NAME)}")

        return retriever

    def build_all(self):
        print(f"共有 {len(self.colleges)} 个学院需要构建知识库\n")
        for college_id in self.colleges:
            self.build_kb(college_id)
            print()
        print("✅ 全部知识库构建完成！")

    def get_retriever(self, college_id):
        """获取指定学院的混合检索器"""
        if college_id not in self.colleges:
            raise ValueError(f"未找到学院配置：{college_id}")
        if college_id in self._retrievers:
            return self._retrievers[college_id]

        # 从磁盘加载：路径由当前语料哈希决定
        config = self.colleges[college_id]
        kb_path = self.kb_path_for(college_id)
        rule_file = config["rule_file"]
        if needs_rebuild(kb_path, self.kb_key_for(college_id)):
            legacy = os.path.join(self.kb_root, college_id)
            hint = (f"（检测到旧版目录 {legacy}，已不再使用）"
                    if os.path.isdir(legacy) else "")
            raise FileNotFoundError(
                f"知识库与当前规范文本不一致或尚未构建：{kb_path}{hint}；"
                f"请先构建：POST /api/kb/build {{'college_id': '{college_id}'}}"
            )
        print(f"正在加载知识库：{display_name(config)}...")

        # 加载向量库
        vectorstore = Chroma(
            persist_directory=kb_path,
            embedding_function=self.embeddings
        )

        # 重新构建 BM25 索引（切片参数取该学院配置，与建库时一致）
        chunk_size = int(config.get("chunk_size", 300))
        chunk_overlap = int(config.get("chunk_overlap", 50))
        with open(rule_file, "r", encoding="utf-8") as f:
            text = f.read()
        chunks = self._smart_split(text, chunk_size, chunk_overlap)
        tokenized_corpus = [list(jieba.cut(doc.page_content)) for doc in chunks]
        bm25 = BM25Okapi(tokenized_corpus)

        # 创建混合检索器
        retriever = HybridRetriever()
        retriever.vectorstore = vectorstore
        retriever.bm25 = bm25
        retriever.all_docs = chunks
        self._retrievers[college_id] = retriever

        # 加载时也生成词汇表
        self._build_vocab(chunks, college_id)

        print(f"✅ 知识库加载完成")
        return retriever

    def _smart_split(self, text, chunk_size, chunk_overlap):
        """
        智能分块（和原始版本完全一致）
        - 按二级标题【第X章 / X.X 标题】切分
        - 每个块加上完整路径前缀
        - 元数据包含章节信息
        """
        section_pattern = r'【(第[一二三四五六七八九十\d]+章\s+[^/]+)\s*/\s*([^】]+)】'
        parts = re.split(section_pattern, text)
        chunks = []
        current_chapter = "前言"
        current_section = "前言"
        for i, part in enumerate(parts):
            if i % 3 == 1:
                current_chapter = part.strip()
            elif i % 3 == 2:
                current_section = part.strip()
            else:
                content = part.strip()
                if not content:
                    continue
                full_path = f"{current_chapter} / {current_section}"
                if len(content) > chunk_size:
                    text_splitter = RecursiveCharacterTextSplitter(
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        separators=["\n\n", "\n", "。", "；", ""]
                    )
                    sub_chunks = text_splitter.split_text(content)
                    for sub in sub_chunks:
                        chunks.append(Document(
                            page_content=f"{full_path}：\n{sub}",
                            metadata={
                                "chapter": current_chapter,
                                "section": current_section,
                                "full_path": full_path
                            }
                        ))
                else:
                    chunks.append(Document(
                        page_content=f"{full_path}：\n{content}",
                        metadata={
                            "chapter": current_chapter,
                            "section": current_section,
                            "full_path": full_path
                        }
                    ))
        return chunks


def _now():
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="知识库状态与构建")
    parser.add_argument("--status", action="store_true",
                        help="只报告状态（不加载向量模型）")
    parser.add_argument("--build", nargs="?", const="all", default=None,
                        metavar="学院ID", help="构建指定学院（缺省全部）")
    args = parser.parse_args()

    if args.status:
        from backend.rag.kb_store import _main as status_main
        raise SystemExit(status_main([]))

    manager = KBManager()
    print("可用学院：")
    for cid, name in manager.list_colleges():
        print(f"  - {cid}: {name}")
    print()
    if args.build in (None, "all"):
        manager.build_all()
    else:
        manager.build_kb(args.build)