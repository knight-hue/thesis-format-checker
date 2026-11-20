# -*- coding: utf-8 -*-
"""会话存储的对话状态：新列、迁移、清理与修复同步。"""
import os
import json
import sqlite3
import tempfile
import time
import unittest

from backend.rag.session_manager import SessionManager


def summary(rule_id, index, category="正文格式", source="rule_engine"):
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": "error",
        "location": "第 %d 段" % index,
        "message": "问题 %s" % rule_id,
        "expected": "",
        "source": source,
    }


class SessionDialogStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "sessions.db")
        self.manager = SessionManager(db_path=self.db, expire_seconds=0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_and_setters(self):
        self.manager.create_session("c1", "jlu_cs")
        session = self.manager.get_session("c1")
        self.assertEqual(session["pending_clarify"], None)
        self.assertEqual(session["last_intent"], "")
        self.assertEqual(session["last_listed"], [])
        self.assertEqual(session["last_scope"], {})
        # 只建会话、没写检测结果 = 还没检测过
        self.assertFalse(session["paper_checked"])

        self.manager.set_pending_clarify("c1", {"kind": "paper_index"})
        self.manager.set_last_listed("c1", [{"rule_id": "a", "location": "第 1 段"}])
        self.manager.set_last_intent("c1", "paper_detail")
        self.manager.set_last_scope("c1", {"scope": "category", "category": "图表"})
        session = self.manager.get_session("c1")
        self.assertEqual(session["pending_clarify"]["kind"], "paper_index")
        self.assertEqual(session["last_listed"][0]["rule_id"], "a")
        self.assertEqual(session["last_intent"], "paper_detail")
        self.assertEqual(session["last_scope"]["category"], "图表")

        self.manager.set_pending_clarify("c1", None)
        self.assertIsNone(self.manager.get_session("c1")["pending_clarify"])

    def test_old_database_is_migrated(self):
        # 用独立的临时库：setUp 里的 manager 已经建过新表，不能在同一文件里再造旧表
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "old.db")
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "CREATE TABLE sessions ("
                    "conversation_id TEXT PRIMARY KEY, college_id TEXT, "
                    "paper_errors TEXT NOT NULL DEFAULT '[]', "
                    "paper_total INTEGER NOT NULL DEFAULT 0, "
                    "history TEXT NOT NULL DEFAULT '[]', updated_at REAL NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                    ("old", "jlu_cs", "[]", 0, "[]", time.time()),
                )
                conn.execute(
                    "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                    ("old2", "jlu_cs", json.dumps([summary("a", 1)]), 1, "[]", time.time()),
                )
                conn.commit()
            finally:
                conn.close()

            manager = SessionManager(db_path=db, expire_seconds=0)
            session = manager.get_session("old")
            self.assertIsNotNone(session)
            self.assertIsNone(session["pending_clarify"])
            self.assertEqual(session["last_listed"], [])
            self.assertEqual(session["last_scope"], {})
            self.assertFalse(session["paper_checked"])
            # 旧库没有标记，但问题列表非空 → 推断为已检测
            self.assertTrue(manager.get_session("old2")["paper_checked"])
            manager.set_pending_clarify("old", {"kind": "heading_level"})
            self.assertEqual(manager.get_session("old")["pending_clarify"]["kind"],
                             "heading_level")

    def test_new_detection_resets_dialog_state(self):
        self.manager.create_session("c", "jlu_cs")
        self.manager.set_paper_errors("c", [summary("a", 1)])
        self.manager.set_pending_clarify("c", {"kind": "paper_index"})
        self.manager.set_last_listed("c", [{"rule_id": "a", "location": "第 1 段"}])
        self.manager.set_last_scope("c", {"scope": "category", "category": "图表"})
        self.manager.set_paper_errors("c", [summary("b", 2)])
        session = self.manager.get_session("c")
        self.assertIsNone(session["pending_clarify"])
        self.assertEqual(session["last_listed"], [])
        self.assertEqual(session["last_scope"], {})
        self.assertTrue(session["paper_checked"])

    def test_remove_fixed_issues_prunes_last_listed(self):
        self.manager.create_session("c", "jlu_cs")
        self.manager.set_paper_errors("c", [summary("a", 1), summary("b", 2)])
        self.manager.set_last_listed("c", [
            {"rule_id": "a", "location": "第 1 段"},
            {"rule_id": "b", "location": "第 2 段"},
        ])
        self.manager.remove_paper_errors("c", [{
            "rule_id": "a",
            "location": {"type": "paragraph", "index": 1},
            "source": "rule_engine",
        }])
        session = self.manager.get_session("c")
        self.assertEqual(session["paper_total"], 1)
        self.assertEqual([item["rule_id"] for item in session["last_listed"]], ["b"])

    def test_clear_history_keeps_paper_and_clears_dialog_state(self):
        self.manager.create_session("c", "jlu_cs")
        self.manager.set_paper_errors("c", [summary("a", 1)])
        self.manager.update_history("c", "问题", "回答")
        self.manager.set_pending_clarify("c", {"kind": "paper_index"})
        self.manager.set_last_listed("c", [{"rule_id": "a", "location": "第 1 段"}])
        self.manager.set_last_scope("c", {"scope": "category", "category": "图表"})
        self.manager.clear_history("c")
        session = self.manager.get_session("c")
        self.assertEqual(session["history"], [])
        self.assertEqual(len(session["paper_errors"]), 1)
        self.assertIsNone(session["pending_clarify"])
        self.assertEqual(session["last_listed"], [])
        self.assertEqual(session["last_scope"], {})


if __name__ == "__main__":
    unittest.main()
