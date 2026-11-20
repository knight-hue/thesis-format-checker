import unittest

from backend.fix.verification import confirmed_indexes, non_target_blocks_unchanged


class ExportVerificationTests(unittest.TestCase):
    """导出文件重检：只有重检确认的问题才算“已修复”。"""

    def test_applied_but_remaining_is_not_confirmed(self):
        issue = {'source': 'rule_engine', 'rule_id': 'body.font_size'}
        before = {'issues': [issue]}
        self.assertEqual(confirmed_indexes([issue], [0], before, before), [])
        self.assertEqual(confirmed_indexes([issue], [0], before, {'issues': []}), [0])
        self.assertEqual(
            confirmed_indexes([issue], [0], before, {'issues': [{'rule_id': 'new.error'}]}), [])

    def test_unchecked_is_not_confirmed(self):
        issue = {'source': 'rule_engine', 'rule_id': 'body.font_size'}
        self.assertEqual(
            confirmed_indexes(
                [issue], [0], {'issues': [issue]},
                {'issues': [], 'unchecked': [{'rule_id': 'body.font_size'}]}), [])

    def test_semantic_issue_requires_semantic_recheck(self):
        issue = {'source': 'rag', 'rule_id': 'terminology'}
        before = {'issues': [issue]}
        after = {'issues': []}
        self.assertEqual(confirmed_indexes([issue], [0], before, after), [])
        self.assertEqual(
            confirmed_indexes([issue], [0], before, after, semantic_checked=True), [0])

    def test_non_target_block_change_is_detected(self):
        before = {'blocks': [{'type': 'paragraph', 'kind': 'body', 'text': 'a'},
                             {'type': 'paragraph', 'kind': 'body', 'text': 'b'}]}
        after = {'blocks': [{'type': 'paragraph', 'kind': 'body', 'text': 'a'},
                            {'type': 'paragraph', 'kind': 'body', 'text': 'changed'}]}
        self.assertFalse(
            non_target_blocks_unchanged(before, after, [0], [{'location': {'index': 0}}]))


if __name__ == '__main__':
    unittest.main()
