import asyncio
import threading
import time
import unittest
from unittest.mock import patch

import backend.api as api


class ApiConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_remains_responsive_during_slow_document_check(self):
        """慢同步检测不能占住承载其他请求的事件循环。"""
        started = threading.Event()
        release = threading.Event()

        def slow_check_document(**_kwargs):
            started.set()
            release.wait(timeout=2)
            return {"passed": True, "issues": [], "breakdown": {}}

        request = api.CheckRequest.model_validate(
            {
                "college_id": "jlu_cs",
                "document": {
                    "schema_version": "1.0",
                    "sections": [],
                    "blocks": [],
                },
                "include_semantic": False,
            }
        )
        with patch.object(api, "check_document", side_effect=slow_check_document):
            fallback_release = threading.Timer(1, release.set)
            fallback_release.start()
            started_at = time.perf_counter()
            check_task = asyncio.create_task(api.check_paper(request))
            try:
                await asyncio.sleep(0.05)
                self.assertTrue(started.is_set())
                health = await asyncio.wait_for(api.health_check(), 0.5)
            finally:
                release.set()
                fallback_release.cancel()
            elapsed = time.perf_counter() - started_at
            checked = await asyncio.wait_for(check_task, 1)

        self.assertEqual(health["status"], "healthy")
        self.assertLess(elapsed, 0.5)
        self.assertEqual(
            checked,
            {
                "passed": True,
                "issues": [],
                "breakdown": {},
                "total_issues": 0,
                "grouped_issues": 0,
                "returned_issues": 0,
                "issues_truncated": False,
                "issue_summary": [],
                # 图内条款等能力缺口会在 unchecked 里显式列出；本用例的文档没有图，
                # 因此必须是空数组（有缺口却不报，等于静默漏检）。
                "unchecked": [],
                # “AI 判断，仅供参考”的条目单独一列（不混进 total_issues）。
                "advisory_issues": [],
                # 三态契约的待人工队列；本用例没有语义检查，必须是空数组。
                "pending_review": [],
            },
        )

    async def test_lifespan_uses_dedicated_worker_pool(self):
        with patch.object(api, "_warmup_ai") as warmup_ai:
            async with api.lifespan(api.app):
                worker_name = await api._run_blocking(
                    lambda: threading.current_thread().name
                )
                deadline = time.monotonic() + 1
                while not warmup_ai.called and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)

        self.assertEqual(warmup_ai.call_count, 1)
        self.assertIsInstance(warmup_ai.call_args.args[0], threading.Event)
        self.assertTrue(worker_name.startswith("thesis-job"))
        self.assertIsNone(api.app.state.blocking_executor)


if __name__ == "__main__":
    unittest.main()
