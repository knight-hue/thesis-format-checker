"""本地异步检测任务状态与结果持久化。"""
from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone


_SCHEMA = """
CREATE TABLE IF NOT EXISTS check_jobs (
    job_id       TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    progress     INTEGER NOT NULL,
    stage        TEXT NOT NULL,
    message      TEXT NOT NULL,
    result_json  TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    expires_at   REAL NOT NULL
)
"""


class TooManyActiveJobs(RuntimeError):
    """本机异步检测队列已达到容量上限。"""


class DuplicateJobId(RuntimeError):
    """客户端提交了已经存在的任务 ID。"""


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


class CheckJobStore:
    """用 SQLite 保存任务状态和最终结果，供请求超时后继续查询。"""

    def __init__(self, db_path: str, expire_seconds: int, max_active: int):
        self.db_path = db_path
        self.expire_seconds = expire_seconds
        self.max_active = max_active
        self._init_lock = threading.Lock()
        self._prepare_db()

    @contextlib.contextmanager
    def _session(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _prepare_db(self):
        with self._init_lock:
            directory = os.path.dirname(os.path.abspath(self.db_path))
            os.makedirs(directory, exist_ok=True)
            with self._session() as connection:
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                except sqlite3.DatabaseError:
                    pass
                connection.execute(_SCHEMA)
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_check_jobs_expiry "
                    "ON check_jobs(expires_at)"
                )

    @staticmethod
    def _delete_expired(connection, now: float):
        connection.execute("DELETE FROM check_jobs WHERE expires_at <= ?", (now,))

    @staticmethod
    def _row_to_job(row) -> dict | None:
        if row is None:
            return None
        result = None
        if row["result_json"]:
            try:
                result = json.loads(row["result_json"])
            except (TypeError, ValueError):
                result = None
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "progress": row["progress"],
            "stage": row["stage"],
            "message": row["message"],
            "result_available": row["status"] == "succeeded" and result is not None,
            "result": result,
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "expires_at": _iso(row["expires_at"]),
        }

    def create(self, job_id: str) -> dict:
        now = time.time()
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._delete_expired(connection, now)
            if connection.execute(
                "SELECT 1 FROM check_jobs WHERE job_id = ?", (job_id,)
            ).fetchone():
                raise DuplicateJobId(job_id)
            active = connection.execute(
                "SELECT COUNT(*) FROM check_jobs WHERE status IN ('queued', 'running')"
            ).fetchone()[0]
            if active >= self.max_active:
                raise TooManyActiveJobs(str(active))
            connection.execute(
                "INSERT INTO check_jobs "
                "(job_id, status, progress, stage, message, result_json, "
                "created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (
                    job_id,
                    "queued",
                    0,
                    "queued",
                    "任务已进入检测队列",
                    now,
                    now,
                    now + self.expire_seconds,
                ),
            )
        return self.get(job_id)

    def update_progress(
        self, job_id: str, *, progress: int, stage: str, message: str
    ) -> None:
        with self._session() as connection:
            connection.execute(
                "UPDATE check_jobs SET status = 'running', progress = ?, stage = ?, "
                "message = ?, updated_at = ? WHERE job_id = ? "
                "AND status IN ('queued', 'running')",
                (max(0, min(99, progress)), stage, message, time.time(), job_id),
            )

    def succeed(self, job_id: str, result: dict) -> None:
        now = time.time()
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self._session() as connection:
            connection.execute(
                "UPDATE check_jobs SET status = 'succeeded', progress = 100, "
                "stage = 'completed', message = '检测完成', result_json = ?, "
                "updated_at = ?, expires_at = ? WHERE job_id = ?",
                (payload, now, now + self.expire_seconds, job_id),
            )

    def fail(self, job_id: str, message: str) -> None:
        now = time.time()
        with self._session() as connection:
            connection.execute(
                "UPDATE check_jobs SET status = 'failed', stage = 'failed', "
                "message = ?, result_json = NULL, updated_at = ?, expires_at = ? "
                "WHERE job_id = ?",
                (message, now, now + self.expire_seconds, job_id),
            )

    def get(self, job_id: str) -> dict | None:
        now = time.time()
        with self._session() as connection:
            self._delete_expired(connection, now)
            row = connection.execute(
                "SELECT * FROM check_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row_to_job(row)

    def fail_incomplete(self) -> int:
        """服务重启后不能续跑内存任务，将旧任务明确标记为失败。"""
        now = time.time()
        with self._session() as connection:
            self._delete_expired(connection, now)
            cursor = connection.execute(
                "UPDATE check_jobs SET status = 'failed', stage = 'failed', "
                "message = '服务已重启，本次检测未完成，请重新上传论文', "
                "updated_at = ?, expires_at = ? "
                "WHERE status IN ('queued', 'running')",
                (now, now + self.expire_seconds),
            )
            return cursor.rowcount
