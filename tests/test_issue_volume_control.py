import unittest

import backend.api as api
from backend.detection.issue_compaction import compact_check_result


def _issue(index, *, rule_id="page.left_cm", category="页面设置",
           actual="2.5 cm", expected="3.0 cm", fixable=True):
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": "error",
        "location": {"type": "section", "index": index},
        "actual": actual,
        "expected": expected,
        "message": "格式不符合要求",
        "suggestion": "请修改",
        "source": "rule_engine",
        "fixable": fixable,
    }


def _result(issues):
    return {
        "schema_version": "1.0",
        "college_id": "jlu_cs",
        "passed": not issues,
        "total_issues": len(issues),
        "issues": issues,
        "breakdown": {
            "rule_engine": {"passed": not issues, "total_issues": len(issues)},
            "semantic": {
                "enabled": False,
                "llm_enabled": False,
                "passed": None,
                "total_issues": 0,
                "llm_calls": 0,
                "cache_hits": 0,
                "prefilter_skips": 0,
            },
        },
    }


class IssueVolumeControlTests(unittest.TestCase):
    def test_same_rule_actual_and_expected_are_grouped(self):
        raw = [_issue(index) for index in range(100)]

        compacted = compact_check_result(
            _result(raw), max_returned=1000, max_location_samples=10
        )

        self.assertEqual(compacted["total_issues"], 100)
        self.assertEqual(compacted["grouped_issues"], 1)
        self.assertEqual(compacted["returned_issues"], 1)
        self.assertFalse(compacted["issues_truncated"])
        grouped = compacted["issues"][0]
        self.assertEqual(grouped["occurrence_count"], 100)
        self.assertEqual(len(grouped["location_samples"]), 10)
        # 同规则多处不再降级为不可修：带上全部位置，交给 /api/fix 一次全改。
        self.assertTrue(grouped["fixable"])
        self.assertEqual(len(grouped["locations"]), 100)
        self.assertNotIn("fix_reason", grouped)
        self.assertEqual(
            compacted["issue_summary"],
            [{"category": "页面设置", "occurrence_count": 100, "grouped_count": 1}],
        )
        api.CheckResponse.model_validate(compacted)

    def test_result_is_capped_but_summary_covers_all_groups(self):
        raw = [
            _issue(index, rule_id=f"body.rule_{index}", category="正文格式")
            for index in range(1005)
        ]

        compacted = compact_check_result(
            _result(raw), max_returned=1000, max_location_samples=10
        )

        self.assertEqual(compacted["total_issues"], 1005)
        self.assertEqual(compacted["grouped_issues"], 1005)
        self.assertEqual(compacted["returned_issues"], 1000)
        self.assertTrue(compacted["issues_truncated"])
        self.assertEqual(len(compacted["issues"]), 1000)
        self.assertEqual(compacted["issue_summary"][0]["grouped_count"], 1005)
        api.CheckResponse.model_validate(compacted)

    def test_grouping_key_handles_nested_values_stably(self):
        first = _issue(1, actual={"font": "Arial", "size": [10, 12]})
        second = _issue(2, actual={"size": [10, 12], "font": "Arial"})

        compacted = compact_check_result(
            _result([first, second]), max_returned=1000, max_location_samples=10
        )

        self.assertEqual(compacted["grouped_issues"], 1)
        self.assertEqual(compacted["issues"][0]["occurrence_count"], 2)
        self.assertEqual(first.get("occurrence_count"), None)

    def test_pathological_repeated_result_stays_small(self):
        raw = [
            _issue(
                index,
                rule_id=f"page.margin_{index % 5}",
                actual="2.5 cm",
                expected="3.0 cm",
            )
            for index in range(42256)
        ]

        compacted = compact_check_result(
            _result(raw), max_returned=1000, max_location_samples=10
        )

        self.assertEqual(compacted["total_issues"], 42256)
        self.assertEqual(compacted["grouped_issues"], 5)
        self.assertEqual(compacted["returned_issues"], 5)
        self.assertFalse(compacted["issues_truncated"])
        self.assertEqual(
            sum(issue["occurrence_count"] for issue in compacted["issues"]),
            42256,
        )
        self.assertTrue(all(len(issue["location_samples"]) == 10 for issue in compacted["issues"]))


class NewGroupingTests(unittest.TestCase):
    """同一规则的不同实测值也合成一条，实测值放进 variants。"""

    def test_same_rule_with_different_actuals_is_one_row(self):
        raw = [_issue(index, rule_id="list_item.number_format",
                      actual=f"{index}、") for index in range(19)]

        compacted = compact_check_result(
            _result(raw), max_returned=1000, max_location_samples=10
        )

        self.assertEqual(compacted["total_issues"], 19)
        self.assertEqual(compacted["grouped_issues"], 1, "同一规则应只出一条")
        grouped = compacted["issues"][0]
        self.assertEqual(grouped["occurrence_count"], 19)
        self.assertEqual(len(grouped["variants"]), 10, "variants 最多带 10 个不同实测值")
        self.assertEqual(grouped["variants"][0]["actual"], "0、")
        self.assertEqual(grouped["variants"][9]["actual"], "9、")
        for variant in grouped["variants"]:
            self.assertEqual(variant["expected"], "3.0 cm")
            self.assertIn("location", variant)
        api.CheckResponse.model_validate(compacted)

    def test_identical_variants_are_not_repeated(self):
        raw = [_issue(index, rule_id="body.font_size", actual="10.5 pt")
               for index in range(5)]

        compacted = compact_check_result(
            _result(raw), max_returned=1000, max_location_samples=10
        )

        grouped = compacted["issues"][0]
        self.assertEqual(grouped["occurrence_count"], 5)
        self.assertEqual(len(grouped["variants"]), 1)

    def test_variant_carries_its_own_conclusion(self):
        """同一条规则下各处“缺什么”可能不同（参考文献缺页码/缺出版地），
        每处的结论要跟着实测值一起带走，否则聚合后看不出差别。"""
        missing_pages = dict(_issue(0, rule_id="reference.format",
                                    actual="［1］张三．综述[J]．学报，2020．"),
                             conclusion="参考文献条目的著录项与模板样例不符：缺卷(期)号后的起止页码")
        missing_place = dict(_issue(1, rule_id="reference.format",
                                    actual="［2］李四．专著[M]．某出版社，2019．"),
                             conclusion="参考文献条目的著录项与模板样例不符：缺出版地：出版者这类用“：”分隔的项")

        compacted = compact_check_result(
            _result([missing_pages, missing_place]),
            max_returned=1000, max_location_samples=10,
        )

        variants = compacted["issues"][0]["variants"]
        self.assertEqual([variant["conclusion"] for variant in variants],
                         [missing_pages["conclusion"], missing_place["conclusion"]])
        api.CheckResponse.model_validate(compacted)

    def test_errors_come_before_warnings_and_bigger_groups_first(self):
        raw = [
            _issue(0, rule_id="note.space_before", actual="0 pt"),
            _issue(1, rule_id="body.font_size"),
            _issue(2, rule_id="body.font_size"),
            _issue(3, rule_id="note.space_before", actual="1 pt"),
            _issue(4, rule_id="body.font_size", actual="11 pt"),
        ]
        for index in (0, 3):
            raw[index]["severity"] = "warning"

        compacted = compact_check_result(
            _result(raw), max_returned=1000, max_location_samples=10
        )

        self.assertEqual([item["rule_id"] for item in compacted["issues"]],
                         ["body.font_size", "note.space_before"])

    def test_truncation_keeps_the_biggest_groups(self):
        raw = []
        for index in range(200):
            raw.append(_issue(index, rule_id="big.rule"))
        for index in range(5):
            raw.append(_issue(index, rule_id=f"small.rule_{index}"))
        for issue in raw[200:]:
            issue["severity"] = "warning"

        compacted = compact_check_result(
            _result(raw), max_returned=3, max_location_samples=10
        )

        self.assertEqual(compacted["grouped_issues"], 6)
        self.assertEqual(compacted["returned_issues"], 3)
        self.assertTrue(compacted["issues_truncated"])
        self.assertEqual(compacted["issues"][0]["rule_id"], "big.rule")


if __name__ == "__main__":
    unittest.main()
