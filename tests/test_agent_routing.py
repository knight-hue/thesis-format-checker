# -*- coding: utf-8 -*-
"""Agent 路由测试：开关、兜底、复合请求与「不该走 Agent」的场景。"""
import unittest
from unittest import mock

from backend import config
from backend.rag.dialog.agent import router
from backend.rag.dialog.intent import Intent


def use_agent(route="complex", enabled=True):
    return mock.patch.multiple(config, USE_DIALOG_AGENT=enabled, AGENT_ROUTE=route)


class RouterTest(unittest.TestCase):
    def test_disabled_by_default(self):
        with use_agent(enabled=False):
            self.assertFalse(router.should_use_agent(
                Intent("spec_qa", {}, 0.3, "R9 兜底"), "哪里不对", True))

    def test_route_off_overrides_enabled(self):
        with use_agent(route="off", enabled=True):
            self.assertFalse(router.should_use_agent(
                Intent("spec_qa", {}, 0.3, "R9 兜底"), "哪里不对", True))

    def test_fallback_reason_with_paper(self):
        with use_agent():
            self.assertTrue(router.should_use_agent(
                Intent("spec_qa", {}, 0.3, "R9 兜底"), "随便问问", True))

    def test_fallback_reason_without_paper_stays_deterministic(self):
        with use_agent():
            self.assertFalse(router.should_use_agent(
                Intent("paper_detail", {"scope": "all"}, 0.3, "R9 兜底"), "哪里不对", False))

    def test_simple_index_question_stays_deterministic(self):
        with use_agent():
            self.assertFalse(router.should_use_agent(
                Intent("paper_detail", {"scope": "index", "index": 3}, 0.95, "R2 序号指代"),
                "第3条是什么", True))

    def test_multi_question_goes_to_agent(self):
        with use_agent():
            self.assertTrue(router.should_use_agent(
                Intent("clarify", {"kind": "multi_question", "options": ["a", "b"]},
                       0.85, "R0 多问题待拆分"),
                "哪里不对？还有哪些没查？", True))

    def test_strong_marker_goes_to_agent(self):
        with use_agent():
            self.assertTrue(router.should_use_agent(
                Intent("paper_fix", {"scope": "all"}, 0.92, "R3 修改类"),
                "把图表的问题整理一下，能自动改的标出来", True))

    def test_two_weak_markers_go_to_agent(self):
        with use_agent():
            self.assertTrue(router.should_use_agent(
                Intent("paper_detail", {"scope": "all"}, 0.85, "R5 类目定位"),
                "先看看图题，然后再说表题，另外还有公式", True))

    def test_single_weak_marker_is_not_enough(self):
        with use_agent():
            self.assertFalse(router.should_use_agent(
                Intent("paper_overview", {"scope": "all"}, 0.9, "R4 概览类"),
                "哪里不对然后呢", True))

    def test_paper_intent_without_paper_is_not_agent(self):
        with use_agent():
            self.assertFalse(router.should_use_agent(
                Intent("paper_detail", {"scope": "index", "index": 1}, 0.95, "R2 序号指代"),
                "第1条是什么", False))

    def test_mode_all_always_uses_agent(self):
        with use_agent(route="all"):
            self.assertTrue(router.should_use_agent(
                Intent("paper_detail", {"scope": "index", "index": 1}, 0.95, "R2 序号指代"),
                "第1条是什么", True))

    def test_mode_fallback_ignores_complex_question(self):
        with use_agent(route="fallback"):
            self.assertFalse(router.should_use_agent(
                Intent("paper_fix", {"scope": "all"}, 0.92, "R3 修改类"),
                "把图表的问题整理一下，能自动改的标出来", True))

    def test_complex_detection_is_pure_text(self):
        self.assertTrue(router.is_complex_question("按类别整理一下"))
        self.assertFalse(router.is_complex_question("第3条是什么"))


if __name__ == "__main__":
    unittest.main()
