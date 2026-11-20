import unittest
from unittest.mock import Mock

from backend.rag.compliance import ComplianceJudge
from backend.detection.semantic.checker import SemanticChecker


class UnknownTests(unittest.TestCase):
    """语义判定四态：unknown / not_applicable 不能折叠成“通过”。"""

    def setUp(self):
        self.judge = ComplianceJudge(None, 'unused')

    def test_invalid_missing_and_string_false_are_unknown(self):
        for text in ('bad', '{"results":[]}', '{"results":[{"compliant":"false"}]}'):
            self.assertIsNone(self.judge._parse_compliance_mixed_json(text, 1)[0]['compliant'])

    def test_four_state_and_failure_reasons(self):
        rows = self.judge._parse_compliance_mixed_json('bad', 1)
        self.assertEqual((rows[0]['status'], rows[0]['reason']), ('unknown', 'parse_error'))
        rows = self.judge._parse_compliance_mixed_json('{"results":[]}', 1)
        self.assertEqual((rows[0]['status'], rows[0]['reason']), ('unknown', 'missing_evidence'))
        rows = self.judge._parse_compliance_mixed_json('{"results":[{"compliant":"false"}]}', 1)
        self.assertEqual((rows[0]['status'], rows[0]['reason']), ('unknown', 'invalid_schema'))
        rows = self.judge._parse_compliance_mixed_json(
            '{"results":[{"compliant":null,"conclusion":"该条款不适用"}]}', 1)
        self.assertEqual(rows[0]['status'], 'not_applicable')
        rows = self.judge._parse_compliance_mixed_json(
            '{"results":[{"index":1,"compliant":true},{"index":2,"compliant":false}]}', 2)
        self.assertEqual([r['status'] for r in rows], ['match', 'mismatch'])

    def test_reordered_and_duplicate_indexes(self):
        rows = self.judge._parse_compliance_mixed_json(
            '{"results":[{"index":2,"compliant":false},{"index":1,"compliant":true}]}', 2)
        self.assertEqual([r['compliant'] for r in rows], [True, False])
        rows = self.judge._parse_compliance_mixed_json(
            '{"results":[{"index":1,"compliant":false},{"index":1,"compliant":true}]}', 2)
        self.assertIsNone(rows[0]['compliant'])

    def test_failed_judgment_reported_unchecked_and_not_cached(self):
        rag = Mock(documents_allowed=True, llm_model='test')
        rag.judge_compliance_mixed_batch.side_effect = RuntimeError('test failure')
        checker = SemanticChecker(rag)
        checker._collect_located_candidates = Mock(return_value=[('fragment', 1)])
        rules = [{'rule_id': 'terminology'}]
        self.assertEqual(checker._run_located_merged(rules, {}, 'jlu_se'), [])
        self.assertEqual(checker.unchecked[0]['rule_id'], 'terminology')
        self.assertEqual(checker._cache, {})

    def test_unchecked_reason_carries_failure_code(self):
        # CheckResponse.unchecked 只暴露 rule_id/reason，失败码必须写在 reason 里。
        rag = Mock(documents_allowed=True, llm_model='test')
        rag.judge_compliance_mixed_batch.side_effect = RuntimeError('test failure')
        checker = SemanticChecker(rag)
        checker._collect_located_candidates = Mock(return_value=[('fragment', 1)])
        checker._run_located_merged([{'rule_id': 'terminology'}], {}, 'jlu_se')
        self.assertIn('llm_error', checker.unchecked[0]['reason'])
        self.assertEqual(set(checker.unchecked[0]), {'rule_id', 'reason'})


if __name__ == '__main__':
    unittest.main()
