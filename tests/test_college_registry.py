# -*- coding: utf-8 -*-
"""学院注册表的两件事：坐标字段齐全、与规则文件双向对得上。

背景：学院 ID 早就渗进了缓存键、会话表、知识库目录，而"有哪些学院"原先只在
`config/colleges.json` 里，`RuleEngine.for_college()` 却只看 `config/rules/<学校>/<学院>/<id>_rules.json`
是否存在——两份真相可以各自漂移。这里把两者钉在一起，并把"学校/学院/专业/多版本"
从 id 字符串里挪出来变成显式字段。
"""
import json
import os
import re
import tempfile
import unittest

from backend.college_registry import file_path, path_of, rule_sets
from backend.config import COLLEGE_ID_PATTERN
from backend.detection.rule_engine import RuleEngine
from backend.rag.colleges import (
    coordinates,
    validate_registry,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(ROOT, "config", "rules")
REGISTRY = os.path.join(ROOT, "config", "colleges.json")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _entry(**overrides):
    info = {
        "school": "jlu",
        "school_name": "吉林大学",
        "college": "cs",
        "college_name": "计算机科学与技术学院",
        "major": None,
        "major_name": None,
        "template_version": "Z-3",
        "effective_year": None,
        "rule_file": "rules/placeholder.txt",
        "chunk_size": 300,
        "chunk_overlap": 50,
    }
    info.update(overrides)
    return info


class RealRegistryTest(unittest.TestCase):
    """真实配置必须自洽（这是"加学院漏了文件"的第一道闸）。"""

    def test_registry_and_rule_files_match(self):
        self.assertEqual([], validate_registry(root=ROOT, rules_dir=RULES_DIR))

    def test_every_rule_set_is_registered(self):
        with open(REGISTRY, encoding="utf-8") as handle:
            registry = json.load(handle)
        registered = set(registry)
        found = rule_sets(RULES_DIR)
        self.assertTrue(found, "config/rules/<学校>/<学院>/ 下没有找到判定配置")
        for cid, path in found:
            with self.subTest(rule_set=os.path.relpath(path, RULES_DIR)):
                self.assertIn(cid, registered)

    def test_ids_follow_the_naming_rule(self):
        pattern = re.compile(COLLEGE_ID_PATTERN)
        with open(REGISTRY, encoding="utf-8") as handle:
            registry = json.load(handle)
        for cid in registry:
            with self.subTest(college_id=cid):
                self.assertRegex(cid, pattern)

    def test_structure_table_of_every_college_is_loadable(self):
        """结构识别表写错要在测试里就红（它是解析层入口，线上出错会整块区域错位）。"""
        from backend.recognition.structure import load_structure

        with open(REGISTRY, encoding="utf-8") as handle:
            registry = json.load(handle)
        for college_id in registry:
            path = path_of(college_id, "structure", RULES_DIR)
            if not os.path.isfile(path):
                continue
            with self.subTest(college_id=college_id):
                self.assertTrue(load_structure(college_id, rules_dir=RULES_DIR))

    def test_coordinates_expose_school_and_college(self):
        items = {item["college_id"]: item for item in coordinates(REGISTRY)}
        self.assertEqual({"jlu_cs", "jlu_se"}, set(items))
        for item in items.values():
            self.assertTrue(item["school"] and item["school_name"])
            self.assertTrue(item["college"] and item["college_name"])
            self.assertIn("·", item["display_name"])

    def test_schools_group_their_colleges(self):
        # 前端在 /api/colleges 的坐标字段上按 school 分组（学校 → 学院），
        # 这里只钉住分组所需的字段都在，不额外提供树形接口。
        items = coordinates(REGISTRY)
        grouped = {}
        for item in items:
            grouped.setdefault(item["school"], set()).add(item["college_id"])
        self.assertEqual({"jlu_cs", "jlu_se"}, grouped["jlu"])
        for item in items:
            self.assertTrue(item["display_name"])


class RegistryShapedForGrowthTest(unittest.TestCase):
    """第二所学校、同院多专业、同院多版模板都能表达，且不靠拆 id 字符串。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.rules_dir = os.path.join(self.root, "rules")

    def tearDown(self):
        self.tmp.cleanup()

    def _add_rule_set(self, college_id, school="abc", college="ee",
                      text="页边距：上 2 厘米\n"):
        """按 `config/rules/<学校>/<学院>/` 写一套规则文件，返回注册表要填的 rule_file。"""
        _write(file_path(school, college, college_id, "rules", self.rules_dir),
               json.dumps({"schema_version": "1.0", "college_id": college_id},
                          ensure_ascii=False))
        _write(file_path(school, college, college_id, "basis", self.rules_dir),
               json.dumps({"page.": "页边距：上 2 厘米"}, ensure_ascii=False))
        corpus = file_path(school, college, college_id, "corpus", self.rules_dir)
        _write(corpus, text)
        return os.path.relpath(corpus, self.root)

    def _registry(self, entries):
        path = os.path.join(self.root, "colleges.json")
        _write(path, json.dumps(entries, ensure_ascii=False, indent=2))
        return path

    def test_second_school_majors_and_versions_are_expressible(self):
        entries = {}
        for cid, college, major, major_name, version in (
            ("jlu_cs", "cs", None, None, "Z-3"),
            ("jlu_cs@2024", "cs", None, None, "2024"),
            ("abc_ee", "ee", "comm", "通信工程", None),
            ("abc_ee_cs", "ee", "computer", "计算机", None),
        ):
            entries[cid] = _entry(college=college, major=major,
                                  major_name=major_name,
                                  template_version=version, school="abc",
                                  school_name="某大学")
            entries[cid]["rule_file"] = self._add_rule_set(cid, college=college)
        registry = self._registry(entries)

        self.assertEqual([], validate_registry(registry, root=self.root,
                                              rules_dir=self.rules_dir))
        # 同一学院的 2024 版与现行版并存：两条条目、坐标只差版本，
        # 文件落在同一个学院目录里，靠文件名开头的完整 id 区分
        self.assertEqual({"jlu_cs_rules.json", "jlu_cs@2024_rules.json"},
                         {os.path.basename(path)
                          for cid, path in rule_sets(self.rules_dir)
                          if cid.startswith("jlu_cs")})
        # 同一学院的 2024 版与现行版并存：两条条目、坐标只差版本
        cs_entries = [item for item in coordinates(registry)
                      if item["college"] == "cs"]
        self.assertEqual({"Z-3", "2024"}, {i["template_version"] for i in cs_entries})
        # 同校多专业：学校下面挂两个条目，专业写在字段里
        ee = [item for item in coordinates(registry) if item["college"] == "ee"]
        self.assertEqual({"通信工程", "计算机"}, {item["major_name"] for item in ee})
        self.assertEqual({"abc"}, {item["school"] for item in ee})
        # 规则引擎能直接按 id 载入（文件名就是 id）
        engine = RuleEngine.for_college("abc_ee_cs", rules_dir=self.rules_dir,
                                        config_path=registry)
        self.assertEqual("abc_ee_cs", engine.rules["college_id"])

    def test_problems_are_named_one_by_one(self):
        entries = {
            # id 含大写与中文：不合规
            "JLU_CS": _entry(rule_file="rules/JLU_CS.txt"),
            # 缺 school_name
            "abc_ee": _entry(school="abc", school_name="", college="ee"),
            # @后缀与 template_version 不一致
            "abc_me@2024": _entry(school="abc", college="me",
                                  template_version="2025"),
            # 坐标与 abc_ee 完全相同（同校同院同专业又没写版本）
            "abc_ee2": _entry(school="abc", college="ee"),
            # 规范文本不存在
            "abc_ph": _entry(school="abc", college="ph",
                             rule_file="rules/不存在的文件.txt"),
        }
        for cid, college in (("abc_ee", "ee"), ("abc_me@2024", "me"),
                             ("abc_ee2", "ee"), ("abc_ph", "ph")):
            self._add_rule_set(cid, college=college)
        registry = self._registry(entries)

        problems = validate_registry(registry, root=self.root,
                                     rules_dir=self.rules_dir)
        joined = "\n".join(problems)
        self.assertIn("JLU_CS：学院 ID 不合规", joined)
        self.assertIn("abc_ee：缺少必填字段 school_name", joined)
        self.assertIn("abc_me@2024：template_version（2025）与 id 的 @ 后缀（2024）不一致",
                      joined)
        self.assertIn("abc_ee2：与 abc_ee 的坐标完全相同", joined)
        self.assertIn("abc_ph：规范文本不存在", joined)
        # 规则集与依据表缺失也点名（消息里的路径相对临时项目根，JLU_CS 的文件没写）
        self.assertIn("JLU_CS：缺少规则文件 rules/jlu/cs/JLU_CS_rules.json", joined)

    def test_orphan_rule_set_is_reported(self):
        rule_file = self._add_rule_set("abc_ee")
        self._add_rule_set("abc_ghost", college="ghost")
        registry = self._registry({"abc_ee": _entry(school="abc", college="ee",
                                                    rule_file=rule_file)})
        problems = validate_registry(registry, root=self.root,
                                     rules_dir=self.rules_dir)
        self.assertEqual(["rules/abc/ghost/abc_ghost_rules.json：规则集没有登记在 "
                          f"{registry}（孤儿规则集）"], problems)


class CollegeIdSafetyTest(unittest.TestCase):
    """id 会拼进文件路径与知识库目录：不合规的形态必须被拦下。"""

    def test_traversal_and_separators_are_rejected(self):
        for bad in ("../secrets", "a/b", "jlu.cs", "jlu cs", "JLU_CS", ""):
            with self.subTest(college_id=bad):
                with self.assertRaises(ValueError):
                    RuleEngine.for_college(bad, rules_dir=RULES_DIR)

    def test_versioned_id_is_accepted(self):
        # 带 @版本 的 id 走同一套加载逻辑：文件以完整 id 开头，放在学院目录里
        with tempfile.TemporaryDirectory() as root:
            rules_dir = os.path.join(root, "rules")
            registry = os.path.join(root, "colleges.json")
            _write(registry, json.dumps({"jlu_cs@2024": {"school": "jlu",
                                                         "college": "cs"}}))
            _write(file_path("jlu", "cs", "jlu_cs@2024", "rules", rules_dir),
                   json.dumps({"college_id": "jlu_cs@2024"}))
            engine = RuleEngine.for_college("jlu_cs@2024", rules_dir=rules_dir,
                                            config_path=registry)
            self.assertEqual("jlu_cs@2024", engine.rules["college_id"])


if __name__ == "__main__":
    unittest.main()
