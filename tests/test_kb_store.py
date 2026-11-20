# -*- coding: utf-8 -*-
"""知识库的内容寻址：库的键只由"语料内容 + 切片参数"决定。

守两件事：
  ① 语料一变，键就变——取不到库等于明确提示重建，不会静默用旧规范建的库回答；
  ② 语料相同的学院（同校共用一份规范）命中同一个目录，天然共用一份库。
"""
import json
import os
import tempfile
import unittest

from backend.rag.kb_store import (
    FINGERPRINT_NAME,
    college_status,
    corpus_sha256,
    is_built,
    kb_dir,
    kb_key,
    needs_rebuild,
    read_fingerprint,
    write_fingerprint,
)

TEXT_A = "页边距：上 2 厘米\n"
TEXT_B = "页边距：上 2.5 厘米\n"


class KbKeyTest(unittest.TestCase):
    def test_same_corpus_and_params_give_the_same_key(self):
        self.assertEqual(kb_key(TEXT_A, 300, 50), kb_key(TEXT_A, 300, 50))

    def test_corpus_change_changes_the_key(self):
        self.assertNotEqual(kb_key(TEXT_A, 300, 50), kb_key(TEXT_B, 300, 50))

    def test_chunk_params_change_the_key(self):
        self.assertNotEqual(kb_key(TEXT_A, 300, 50), kb_key(TEXT_A, 300, 20))
        self.assertNotEqual(kb_key(TEXT_A, 300, 50), kb_key(TEXT_A, 200, 50))

    def test_sha_is_over_content_not_file_location(self):
        self.assertEqual(corpus_sha256(TEXT_A), corpus_sha256(TEXT_A))


class NeedsRebuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.key = kb_key(TEXT_A, 300, 50)
        self.path = kb_dir(self.root, self.key)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_directory_needs_build(self):
        self.assertTrue(needs_rebuild(self.path, self.key))
        self.assertFalse(is_built(self.path))

    def test_directory_without_database_needs_build(self):
        os.makedirs(self.path)
        self.assertTrue(needs_rebuild(self.path, self.key))

    def test_built_library_with_matching_fingerprint_is_reused(self):
        os.makedirs(self.path)
        open(os.path.join(self.path, "chroma.sqlite3"), "w").close()
        write_fingerprint(self.path, {"kb_key": self.key, "colleges": ["jlu_cs"]})
        self.assertFalse(needs_rebuild(self.path, self.key))
        self.assertEqual(["jlu_cs"], read_fingerprint(self.path)["colleges"])

    def test_fingerprint_of_another_corpus_needs_build(self):
        os.makedirs(self.path)
        open(os.path.join(self.path, "chroma.sqlite3"), "w").close()
        write_fingerprint(self.path, {"kb_key": "死值", "colleges": []})
        self.assertTrue(needs_rebuild(self.path, self.key))

    def test_corrupt_fingerprint_needs_build(self):
        os.makedirs(self.path)
        open(os.path.join(self.path, "chroma.sqlite3"), "w").close()
        with open(os.path.join(self.path, FINGERPRINT_NAME), "w", encoding="utf-8") as f:
            f.write("{ 不是 json")
        self.assertTrue(needs_rebuild(self.path, self.key))


class CollegeStatusTest(unittest.TestCase):
    """状态报告要能一眼看出"谁没建、和谁共用"，且不加载向量模型。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.corpus = os.path.join(self.root, "shared.txt")
        with open(self.corpus, "w", encoding="utf-8") as f:
            f.write(TEXT_A)

    def tearDown(self):
        self.tmp.cleanup()

    def _colleges(self):
        return {
            "jlu_cs": {"rule_file": self.corpus, "chunk_size": 300, "chunk_overlap": 50},
            "abc_ee": {"rule_file": self.corpus, "chunk_size": 300, "chunk_overlap": 50},
        }

    def test_missing_library_is_reported(self):
        status = college_status(self._colleges(), self.root)
        self.assertEqual(["jlu_cs", "abc_ee"], [item["college_id"] for item in status])
        for item in status:
            with self.subTest(college_id=item["college_id"]):
                self.assertFalse(item["built"])
                self.assertEqual(self.root, os.path.dirname(item["kb_path"]))
                self.assertTrue(item["kb_key"])

    def test_same_corpus_becomes_one_shared_library(self):
        status = {item["college_id"]: item for item in college_status(self._colleges(),
                                                                     self.root)}
        self.assertEqual(status["jlu_cs"]["kb_path"], status["abc_ee"]["kb_path"])
        # 建好之后：两个学院共用一个目录，且各自在指纹里看得见对方
        path = status["jlu_cs"]["kb_path"]
        os.makedirs(path, exist_ok=True)
        open(os.path.join(path, "chroma.sqlite3"), "w").close()
        write_fingerprint(path, {"kb_key": status["jlu_cs"]["kb_key"],
                                 "colleges": ["abc_ee", "jlu_cs"]})
        ready = {item["college_id"]: item
                 for item in college_status(self._colleges(), self.root)}
        self.assertTrue(ready["jlu_cs"]["built"])
        self.assertEqual(["abc_ee"], ready["jlu_cs"]["shared_with"])
        self.assertEqual(["jlu_cs"], ready["abc_ee"]["shared_with"])

    def test_unreadable_corpus_is_reported_not_raised(self):
        colleges = {"jlu_cs": {"rule_file": os.path.join(self.root, "没有这个文件.txt")}}
        item = college_status(colleges, self.root)[0]
        self.assertIn("读不到规范文本", item["error"])
        self.assertFalse(item["built"])


class RealRegistryStatusTest(unittest.TestCase):
    """真实配置里每个学院都要算得出键（不依赖库是否已构建）。"""

    def test_every_real_college_has_a_key(self):
        from backend.config import COLLEGES_CONFIG_PATH, KB_ROOT_DIR
        from backend.rag.colleges import load_colleges

        for item in college_status(load_colleges(COLLEGES_CONFIG_PATH)):
            with self.subTest(college_id=item["college_id"]):
                self.assertEqual("", item["error"])
                self.assertRegex(item["kb_key"], r"^[0-9a-f]{12}$")
                self.assertEqual(KB_ROOT_DIR, os.path.dirname(item["kb_path"]))


if __name__ == "__main__":
    unittest.main()
