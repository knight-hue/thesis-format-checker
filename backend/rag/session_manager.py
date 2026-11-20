"""
独立会话管理器（SQLite）
- SQLite 持久化存储，重启不丢，无需额外服务
- 支持自动过期清理（读取时惰性判定）
- 管理：论文检测结果 + 对话历史
- 论文检测结果按当前问题召回后再拼进 prompt（关键词 BM25），避免整份问题列表稀释模型注意力
"""
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter

import jieba
from rank_bm25 import BM25Okapi

# 单次拼进 prompt 的检测结果条数上限（按问题召回后仍超过时截断）
RECALL_LIMIT = 12

# 召回检索用的停用词：问句里的高频虚词对定位问题没有帮助
_STOPWORDS = {
    "的", "了", "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "着", "过", "地", "得",
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "自己",
    "这", "那", "这个", "那个", "这些", "那些", "这里", "那里", "这样", "那样",
    "是", "有", "在", "和", "与", "及", "或", "把", "被", "对", "给", "让", "到",
    "就", "都", "也", "还", "又", "很", "太", "更", "最", "会", "能", "要", "想",
    "可以", "应该", "需要", "请", "帮", "麻烦", "一下", "现在", "然后", "如果",
    "因为", "所以", "但是", "而且", "以及", "一个", "一些", "看看", "告诉", "说说",
    "什么", "怎么", "怎样", "如何", "为什么", "哪", "哪个", "哪里", "哪些", "多少",
    "关于", "对于", "问题", "论文",
}

# 纯标点/符号 token（中英文标点、空白、emoji 等），不参与召回
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text) -> list:
    """分词并过滤停用词，用于召回打分。"""
    if not text:
        return []
    tokens = []
    for word in jieba.lcut(str(text)):
        word = word.strip()
        if not word or word in _STOPWORDS or _PUNCT_RE.fullmatch(word):
            continue
        tokens.append(word.lower())
    return tokens


def _search_text(err) -> str:
    """单条检测结果的可检索文本（字段与 summarize_issues 写入的结构一致）"""
    return " ".join(
        str(err.get(key) or "") for key in ("category", "location", "message", "expected")
    )


def _recall(errors, query, limit=RECALL_LIMIT):
    """按问题相关性召回检测结果。

    Returns: (选中的条目列表, 模式)；模式取值：
        all    - 总数不超过上限，全部返回
        recall - 按 BM25 相关度筛出最相关的 limit 条
        head   - 问题没有可用关键词或全部不匹配，回退为列表开头 limit 条
    """
    if len(errors) <= limit:
        return list(errors), "all"

    query_tokens = _tokenize(query)
    if query_tokens:
        corpus = [_tokenize(_search_text(err)) for err in errors]
        if any(corpus):
            scores = BM25Okapi(corpus).get_scores(query_tokens)
            ranked = sorted(range(len(errors)), key=lambda i: (-float(scores[i]), i))
            hit = [i for i in ranked if scores[i] > 0][:limit]
            if hit:
                return [errors[i] for i in hit], "recall"

    return list(errors[:limit]), "head"


# 写入会话的检测结果条数上限（仅防止超长文档把会话库撑大；
# 真正进 prompt 的条数由 RECALL_LIMIT 按问题召回后决定）
MAX_STORED_ISSUES = 500

# 「上一次列出顺序」最多保存多少条：对话里「第 N 条」只能指到这些条目，
# 超出部分请到检测结果页查看（事实层文案会说明这一点）。
MAX_LISTED_ENTRIES = 100

# set_paper_errors 的哨兵：不传 unchecked 时保留会话里已有的未检清单
_UNSET = object()

# issue.location.type -> 可读定位模板
_LOCATION_TEMPLATES = {
    "section": "第 {index} 节",
    "table": "表格第 {index} 块",
    "table_cell": "表格第 {index} 块（{row} 行 {col} 列）",
    "image": "图片第 {index} 块",
    "paragraph": "第 {index} 段",
    "document": "全文",
}


def format_location(location) -> str:
    """把 issue.location 转成可读定位。

    刻意不含 text_preview：那是论文原文，拼进 prompt 会引入提示注入风险。
    """
    if not isinstance(location, dict):
        return ""
    template = _LOCATION_TEMPLATES.get(location.get("type") or "")
    if not template:
        return ""
    try:
        return template.format(
            index=int(location.get("index") or 0),
            row=location.get("row", "?"),
            col=location.get("col", "?"),
        )
    except (TypeError, ValueError):
        return template


def _issue_source(issue) -> str:
    """issue 的 source，缺省按规则引擎算（与 summarize_issues 保持一致）。"""
    if not isinstance(issue, dict):
        return "rule_engine"
    return issue.get("source", "rule_engine")


def result_version(paper_errors, paper_total=0) -> str:
    """一次检测结果的指纹：问题条目或总数一变，指纹就变。

    用途有两个，都只依赖会话里已存的字段：
    - 缓存 key：换论文/修复后不会命中旧答案；
    - 聊天消息的“基于旧检测结果”判定：消息携带当时的指纹，与最新指纹不同即过期。
    """
    raw = json.dumps({"issues": paper_errors or [], "total": int(paper_total or 0)},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def summarize_issues(issues, limit: int = MAX_STORED_ISSUES) -> list:
    """把检测结果裁剪成会话上下文摘要。

    只保留系统生成的字段（分类/规则/定位/结论/期望/实测/建议/模板依据），
    不含论文原文。
    这里不做条数裁剪（只留一个防爆上限），因为后续按问题召回需要完整列表。

    为什么连 suggestion / actual / basis 一起存：对话里的「这条怎么改」「依据模板
    哪一句」都只能从 issue 字段回答；早前把它们裁掉，导致聊天卡片永远是空的建议、
    修改类问题一律回答「该条不提供自动修改」——那是与会话里的事实不符。
    这三项都是系统生成/模板原句，不是论文原文。
    """
    summary = []
    for issue in issues[:limit]:
        summary.append({
            "category": issue.get("category", ""),
            "rule_id": issue.get("rule_id", ""),
            "severity": issue.get("severity", ""),
            "location": format_location(issue.get("location")),
            "message": issue.get("message", ""),
            "expected": issue.get("expected", ""),
            "actual": _compact_actual(issue.get("actual")),
            "suggestion": issue.get("suggestion", ""),
            "basis": issue.get("basis", ""),
            "source": _issue_source(issue),
        })
    return summary


def _compact_actual(value):
    """实测值原样保留字符串/数字；其它结构只做短文本化，避免会话库被撑大。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text[:200]


# 默认数据库文件位置（相对仓库根目录）
DEFAULT_DB_PATH = os.path.join("data", "sessions.db")

# 会话表：一行一个会话，paper_errors / history 以 JSON 文本保存
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    conversation_id TEXT PRIMARY KEY,
    college_id      TEXT,
    paper_errors    TEXT NOT NULL DEFAULT '[]',
    paper_total     INTEGER NOT NULL DEFAULT 0,
    paper_checked   INTEGER NOT NULL DEFAULT 0,
    paper_unchecked TEXT,
    history         TEXT NOT NULL DEFAULT '[]',
    pending_clarify TEXT,
    last_intent     TEXT,
    last_listed     TEXT NOT NULL DEFAULT '[]',
    last_scope      TEXT NOT NULL DEFAULT '{}',
    updated_at      REAL NOT NULL
)
"""

# 旧库迁移：列不存在时补上（幂等，可重复执行）
# paper_unchecked 可空：NULL = 这次结果没有记录未检清单（旧版本），[] = 记录过且没有未检项。
_EXTRA_COLUMNS = (
    ("pending_clarify", "pending_clarify TEXT"),
    ("last_intent", "last_intent TEXT"),
    ("last_listed", "last_listed TEXT NOT NULL DEFAULT '[]'"),
    ("last_scope", "last_scope TEXT NOT NULL DEFAULT '{}'"),
    ("paper_checked", "paper_checked INTEGER NOT NULL DEFAULT 0"),
    ("paper_unchecked", "paper_unchecked TEXT"),
)

# 对话反馈表：只存评分与来源元数据，不存问题/回答原文（论文内容不出本机）。
_FEEDBACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_feedback (
    response_id     TEXT PRIMARY KEY,
    conversation_id TEXT,
    rating          TEXT NOT NULL,
    intent          TEXT,
    answer_source   TEXT,
    created_at      REAL NOT NULL
)
"""


class SessionManager:
    """SQLite 会话管理器。

    - 数据落在 var/data/sessions.db，无需额外服务，重启不丢；
    - updated_at 记录最后写入时间，读取时按 expire_seconds 惰性判定过期
      （过期即当作不存在并顺手删掉，不需要另起定时清理任务）；
    - 每次操作新建一个短连接：sqlite3 连接不能跨线程复用，而 FastAPI 的
      同步接口跑在线程池里，短连接最稳。
    """

    def __init__(self, db_path=None, max_history_turns=2, expire_seconds=86400):
        """
        Args:
            db_path: SQLite 数据库文件路径（默认 var/data/sessions.db）
            max_history_turns: 保留最近几轮对话（默认2轮=4条消息）
            expire_seconds: 会话过期时间（默认24小时，读取时惰性判定）
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self.max_history_turns = max_history_turns
        self.expire_seconds = expire_seconds
        self._init_lock = threading.Lock()
        self._prepare_db()

    # ---------- 连接与读写 ----------
    def _prepare_db(self):
        """建库建表；首次创建时顺带切到 WAL 日志模式。"""
        with self._init_lock:
            directory = os.path.dirname(os.path.abspath(self.db_path))
            os.makedirs(directory, exist_ok=True)
            with self._session() as conn:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.DatabaseError:
                    # 个别文件系统不支持 WAL，退回默认日志模式即可，不影响使用
                    pass
                conn.execute(_SCHEMA)
                conn.execute(_FEEDBACK_SCHEMA)
                self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn):
        """为旧库补新列；新库建表时已经包含这些列。"""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
        for name, ddl in _EXTRA_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE sessions ADD COLUMN {ddl}")

    @contextlib.contextmanager
    def _session(self):
        """打开一个短连接：正常退出即提交，无论如何都关闭。"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _load(self, conn, conversation_id):
        """读会话（dict）或 None；读到已过期的会话顺手删掉。"""
        row = conn.execute(
            "SELECT college_id, paper_errors, paper_total, paper_checked, paper_unchecked, "
            "history, updated_at, pending_clarify, last_intent, last_listed, last_scope "
            "FROM sessions WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return None
        if self.expire_seconds and row["updated_at"] + self.expire_seconds < time.time():
            conn.execute(
                "DELETE FROM sessions WHERE conversation_id = ?", (conversation_id,)
            )
            return None
        try:
            paper_errors = json.loads(row["paper_errors"])
            history = json.loads(row["history"])
        except (TypeError, ValueError):
            paper_errors, history = [], []
        try:
            pending_clarify = json.loads(row["pending_clarify"]) if row["pending_clarify"] else None
        except (TypeError, ValueError):
            pending_clarify = None
        try:
            last_listed = json.loads(row["last_listed"])
        except (TypeError, ValueError):
            last_listed = []
        try:
            last_scope = json.loads(row["last_scope"])
        except (TypeError, ValueError):
            last_scope = {}
        try:
            paper_unchecked = (json.loads(row["paper_unchecked"])
                               if row["paper_unchecked"] is not None else None)
        except (TypeError, ValueError):
            paper_unchecked = None
        return {
            "college_id": row["college_id"],
            "paper_errors": paper_errors if isinstance(paper_errors, list) else [],
            "paper_total": row["paper_total"] or 0,
            # 旧库没有 paper_checked：非空问题列表仍可推断为“检测过”；
            # 空列表且旧列缺失时只能保守当作未检测。
            "paper_checked": bool(row["paper_checked"]) or bool(paper_errors),
            # None = 这次结果没有记录未检清单（旧版本）；[] = 记录过且没有未检项
            "paper_unchecked": paper_unchecked if isinstance(paper_unchecked, list) else None,
            "history": history if isinstance(history, list) else [],
            "pending_clarify": pending_clarify if isinstance(pending_clarify, dict) else None,
            "last_intent": row["last_intent"] or "",
            "last_listed": last_listed if isinstance(last_listed, list) else [],
            "last_scope": last_scope if isinstance(last_scope, dict) else {},
        }

    def _save(self, conn, conversation_id, data):
        conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(conversation_id, college_id, paper_errors, paper_total, paper_checked, "
            "paper_unchecked, history, pending_clarify, last_intent, last_listed, "
            "last_scope, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                data.get("college_id"),
                json.dumps(data.get("paper_errors") or [], ensure_ascii=False),
                int(data.get("paper_total") or 0),
                1 if data.get("paper_checked") else 0,
                (json.dumps(data["paper_unchecked"], ensure_ascii=False)
                 if data.get("paper_unchecked") is not None else None),
                json.dumps(data.get("history") or [], ensure_ascii=False),
                json.dumps(data["pending_clarify"], ensure_ascii=False) if data.get("pending_clarify") else None,
                data.get("last_intent") or "",
                json.dumps(data.get("last_listed") or [], ensure_ascii=False),
                json.dumps(data.get("last_scope") or {}, ensure_ascii=False),
                time.time(),
            ),
        )

    # ---------- 对外接口 ----------
    def create_session(self, conversation_id, college_id, paper_errors=None,
                       unchecked=None):
        """创建/重置会话（对话历史清空，写入本次检测结果）

        unchecked 为本次检测「没有执行的条款」清单；None 表示这次结果没带该信息，
        与「记录过且为空」区分开，避免把旧结果的未知说成“没有未检项”。
        """
        with self._session() as conn:
            self._save(
                conn,
                conversation_id,
                {
                    "college_id": college_id,
                    "paper_errors": paper_errors or [],
                    "paper_total": len(paper_errors or []),
                    # None = 还没检测过；[] = 检测过且 0 条问题
                    "paper_checked": 1 if paper_errors is not None else 0,
                    "paper_unchecked": unchecked,
                    "history": [],
                    "pending_clarify": None,
                    "last_intent": "",
                    "last_listed": [],
                    "last_scope": {},
                },
            )

    # 未传 unchecked 时的哨兵：保留会话里已有的清单（修复后同步删条目时要保留）
    def get_session(self, conversation_id):
        """获取会话数据"""
        with self._session() as conn:
            return self._load(conn, conversation_id)

    def update_history(self, conversation_id, user_message, assistant_message):
        """追加一轮对话，自动截断保留最近N轮"""
        with self._session() as conn:
            data = self._load(conn, conversation_id)
            if data is None:
                return False
            history = data.get("history") or []
            history.append({"role": "user", "content": user_message})
            history.append({"role": "assistant", "content": assistant_message})
            # 只保留最近 max_history_turns 轮
            max_messages = self.max_history_turns * 2
            if len(history) > max_messages:
                history = history[-max_messages:]
            data["history"] = history
            self._save(conn, conversation_id, data)
            return True

    def set_paper_errors(self, conversation_id, paper_errors, total=None, reset_dialog=True,
                         unchecked=_UNSET):
        """更新论文检测结果。

        total 为本次检测出的问题总数；paper_errors 可能是截断后的前 N 条，
        记录总数可避免模型误以为问题只有这些。
        reset_dialog 为 True 时同时清空澄清状态与上次列出的顺序（条目已变）。
        unchecked 不传时保留已有清单（修复后只删条目，未检项并未变化）；
        显式传 None 表示本次结果没有带未检信息，与「记录过且为空」区分。
        """
        with self._session() as conn:
            data = self._load(conn, conversation_id)
            if data is None:
                return False
            data["paper_errors"] = paper_errors
            data["paper_total"] = len(paper_errors) if total is None else total
            data["paper_checked"] = 1
            if unchecked is not _UNSET:
                data["paper_unchecked"] = unchecked
            if reset_dialog:
                data["pending_clarify"] = None
                data["last_listed"] = []
                data["last_scope"] = {}
            self._save(conn, conversation_id, data)
            return True

    def bind_paper_errors(self, conversation_id, college_id, issues, keep_history=False,
                          unchecked=None):
        """把检测结果摘要写入会话，供多轮问答按问题召回。

        一次新检测意味着上一篇论文的对话历史已失效（可能指向另一篇论文），
        因此默认直接重建会话，只保留本次检测结果。

        keep_history 为 True 时（同一篇论文修复后的重检）保留对话历史，只替换问题列表；
        会话不存在时回退到重建，避免重检结果无处可写。
        unchecked 为本次检测未执行的条款清单，对话里的「哪些没查」只认它。
        """
        summary = summarize_issues(issues)
        if keep_history and self.set_paper_errors(
            conversation_id, summary, total=len(issues), unchecked=unchecked
        ):
            return
        self.create_session(conversation_id, college_id, unchecked=unchecked)
        self.set_paper_errors(conversation_id, summary, total=len(issues),
                              unchecked=unchecked)

    def remove_paper_errors(self, conversation_id, issues):
        """从会话的检测结果里删掉这些已修复的问题。

        会话里存的是 summarize_issues 的摘要（location 已转成字符串），
        所以两边都用 format_location 生成同一个定位串来配对；同名条目按计数消耗，
        同一位置重复出现时只删对应条数。会话不存在/已过期则静默跳过。
        """
        if not conversation_id or not issues:
            return
        data = self.get_session(conversation_id)
        if not data:
            return
        stored_errors = data.get("paper_errors") or []
        removed_keys = Counter(
            (
                str(issue.get("rule_id") or ""),
                format_location(issue.get("location")),
                _issue_source(issue),
            )
            for issue in issues
        )
        kept = []
        for entry in stored_errors:
            if not isinstance(entry, dict):
                kept.append(entry)
                continue
            key = (
                str(entry.get("rule_id") or ""),
                str(entry.get("location") or ""),
                _issue_source(entry),
            )
            if removed_keys[key] > 0:
                removed_keys[key] -= 1
                continue
            kept.append(entry)
        removed = len(stored_errors) - len(kept)
        if removed <= 0:
            return
        try:
            total = int(data.get("paper_total") or 0)
        except (TypeError, ValueError):
            total = len(stored_errors)
        # 同步劈掉「上次列出顺序」里已修复的条目（按 rule_id + location 配对）
        kept_keys = {
            (str(entry.get("rule_id") or ""), str(entry.get("location") or ""))
            for entry in kept if isinstance(entry, dict)
        }
        data["last_listed"] = [
            entry for entry in (data.get("last_listed") or [])
            if isinstance(entry, dict)
            and (str(entry.get("rule_id") or ""), str(entry.get("location") or "")) in kept_keys
        ]
        self.set_paper_errors(conversation_id, kept, total=max(0, total - removed),
                              reset_dialog=False)
        # 上一次列出的顺序已在内存里劈掉失效条目，这里落库（避免 set_paper_errors 重新读库覆盖）
        self.set_last_listed(conversation_id, data.get("last_listed") or [])

    # ---------- 对话反馈（只存评分与元数据） ----------
    def save_feedback(self, response_id, conversation_id, rating, intent="", answer_source=""):
        """记录一次回答反馈；同一 response_id 重复提交时覆盖旧值。

        刻意不接收 question / conclusion 文本：反馈表不保存任何论文内容。
        """
        response_id = (response_id or "").strip()
        if not response_id:
            return False
        with self._session() as conn:
            conn.execute(
                "INSERT INTO chat_feedback "
                "(response_id, conversation_id, rating, intent, answer_source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(response_id) DO UPDATE SET "
                "rating = excluded.rating, intent = excluded.intent, "
                "answer_source = excluded.answer_source, created_at = excluded.created_at",
                (response_id, conversation_id, rating, intent, answer_source, time.time()),
            )
            return True

    def clear_history(self, conversation_id):
        """清空对话历史，保留论文检测结果（用于“新对话”）"""
        with self._session() as conn:
            data = self._load(conn, conversation_id)
            if data is None:
                return False
            data["history"] = []
            data["pending_clarify"] = None
            data["last_listed"] = []
            data["last_scope"] = {}
            self._save(conn, conversation_id, data)
            return True

    def clear_session(self, conversation_id):
        """删除会话"""
        with self._session() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE conversation_id = ?", (conversation_id,)
            )

    # ---------- 对话编排状态（澄清 / 上次列出顺序 / 最近意图）----------
    def set_pending_clarify(self, conversation_id, payload):
        """写入或清除澄清状态；payload 为 None 表示本轮已消费。"""
        with self._session() as conn:
            data = self._load(conn, conversation_id)
            if data is None:
                return False
            data["pending_clarify"] = payload if isinstance(payload, dict) else None
            self._save(conn, conversation_id, data)
            return True

    def set_last_listed(self, conversation_id, items):
        """记录上一次答案列出的条目顺序，供「第 N 条」指代使用。"""
        with self._session() as conn:
            data = self._load(conn, conversation_id)
            if data is None:
                return False
            data["last_listed"] = [item for item in (items or [])
                                   if isinstance(item, dict)][:MAX_LISTED_ENTRIES]
            self._save(conn, conversation_id, data)
            return True

    def set_last_scope(self, conversation_id, scope):
        """记录上一次论文筛选范围，供「这些」「只看错误」等上下文追问使用。"""
        with self._session() as conn:
            data = self._load(conn, conversation_id)
            if data is None:
                return False
            data["last_scope"] = scope if isinstance(scope, dict) else {}
            self._save(conn, conversation_id, data)
            return True

    def set_last_intent(self, conversation_id, intent):
        """只记意图名，不记问题原文（隐私）。"""
        with self._session() as conn:
            data = self._load(conn, conversation_id)
            if data is None:
                return False
            data["last_intent"] = str(intent or "")
            self._save(conn, conversation_id, data)
            return True

    def build_paper_context(self, conversation_id, query="", limit=RECALL_LIMIT):
        """论文检测结果上下文。

        字段与 set_paper_errors 写入的摘要结构一致：
        {category, rule_id, severity, location, message, expected, source}。
        传到 prompt 前会按 query 召回最相关的 limit 条（问题为空或全部不匹配时回退为列表开头），
        避免把整份问题列表塞进 prompt 稀释注意力。
        刻意不含论文原文，避免把用户可控文本拼进 prompt。
        Args:
            conversation_id: 会话ID
            query: 用户当前问题，用于召回
        Returns: 上下文文本，没有检测结果返回空字符串
        """
        session = self.get_session(conversation_id) or {}
        errors = session.get("paper_errors") or []
        if not errors:
            return ""

        selected, mode = _recall(errors, query, limit)

        parts = ["# 当前论文的检测结果（用户可能针对其中某条提问）"]
        for i, err in enumerate(selected, 1):
            line = (
                f"{i}. [{err.get('category', '')}] "
                f"{err.get('location', '')}：{err.get('message', '')}"
            )
            expected = err.get("expected")
            if expected:
                line += f"（规范要求：{expected}）"
            parts.append(line)

        total = session.get("paper_total") or len(errors)
        if mode == "recall":
            parts.append(
                f"（本次共检测出 {total} 条问题，已按当前问题筛出最相关的 "
                f"{len(selected)} 条并按相关度排序）"
            )
        elif total > len(selected):
            parts.append(f"（本次共检测出 {total} 条问题，以上仅列出前 {len(selected)} 条）")
        return "\n".join(parts)

    def paper_fingerprint(self, conversation_id):
        """当前会话论文检测结果的指纹，用于缓存 key 与聊天消息的“旧结果”判定。

        Returns: 12位十六进制字符串；无检测结果时为空列表的指纹
        """
        session = self.get_session(conversation_id) or {}
        return result_version(session.get("paper_errors") or [],
                              session.get("paper_total") or 0)

    def build_history_context(self, conversation_id):
        """对话历史上下文。
        Returns: 上下文文本，没有历史返回空字符串
        """
        session = self.get_session(conversation_id) or {}
        history = session.get("history") or []
        if not history:
            return ""

        parts = ["# 对话历史"]
        for msg in history:
            role = "用户" if msg.get("role") == "user" else "助手"
            parts.append(f"{role}: {msg.get('content', '')}")
        return "\n".join(parts)

    def build_context_text(self, conversation_id, query="", limit=RECALL_LIMIT):
        """
        构建完整上下文文本（按问题召回的检测结果 + 对话历史），直接拼进prompt
        Args:
            conversation_id: 会话ID
            query: 用户当前问题，用于召回论文检测结果
        Returns: 上下文字符串，没有上下文返回空字符串
        """
        blocks = [
            self.build_paper_context(conversation_id, query, limit),
            self.build_history_context(conversation_id),
        ]
        return "\n\n".join(block for block in blocks if block)