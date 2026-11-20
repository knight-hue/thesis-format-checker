import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from backend.rag import llm_client


class _BlockingCompletions:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.started = 0
        self.lock = threading.Lock()
        self.release = threading.Event()

    def create(self, *_args, **_kwargs):
        with self.lock:
            self.active += 1
            self.started += 1
            self.max_active = max(self.max_active, self.active)
        self.release.wait(timeout=2)
        with self.lock:
            self.active -= 1
        return object()


class LlmClientResilienceTests(unittest.TestCase):
    def test_client_configures_timeout_and_sdk_retries(self):
        raw = SimpleNamespace(chat=SimpleNamespace(completions=object()))
        with patch.object(llm_client, "OpenAI", return_value=raw) as constructor:
            client = llm_client._new_client("key", "https://example.test")

        constructor.assert_called_once_with(
            api_key="key",
            base_url="https://example.test",
            timeout=llm_client.LLM_TIMEOUT_SECONDS,
            max_retries=llm_client.LLM_MAX_RETRIES,
        )
        self.assertIs(client._client, raw)

    def test_nonstream_requests_respect_concurrency_limit(self):
        delegate = _BlockingCompletions()
        limited = llm_client._LimitedCompletions(
            delegate, threading.BoundedSemaphore(2)
        )

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(limited.create) for _ in range(4)]
            deadline = time.monotonic() + 1
            while delegate.started < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(delegate.started, 2)
            self.assertEqual(delegate.max_active, 2)
            delegate.release.set()
            for future in futures:
                future.result(timeout=1)

        self.assertEqual(delegate.started, 4)
        self.assertEqual(delegate.max_active, 2)

    def test_waiting_for_concurrency_permit_times_out(self):
        semaphore = threading.BoundedSemaphore(1)
        semaphore.acquire()
        limited = llm_client._LimitedCompletions(
            SimpleNamespace(create=lambda: object()),
            semaphore,
            acquire_timeout=0.01,
        )

        with self.assertRaisesRegex(TimeoutError, "LLM 并发许可"):
            limited.create()
        semaphore.release()

    def test_stream_holds_permit_until_consumed(self):
        second_started = threading.Event()

        class Completions:
            def create(self, *_args, **kwargs):
                if kwargs.get("stream"):
                    return iter(["chunk"])
                second_started.set()
                return object()

        limited = llm_client._LimitedCompletions(
            Completions(), threading.BoundedSemaphore(1)
        )
        stream = limited.create(stream=True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(limited.create, stream=False)
            self.assertFalse(second_started.wait(timeout=0.1))
            self.assertEqual(list(stream), ["chunk"])
            self.assertTrue(second_started.wait(timeout=1))
            future.result(timeout=1)

    def test_early_json_completion_releases_stream_permit(self):
        chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content='{"results": []}'))]
        )

        class Completions:
            def create(self, *_args, **kwargs):
                if kwargs.get("stream"):
                    return iter([chunk, chunk])
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
                )

        limited = llm_client._LimitedCompletions(
            Completions(), threading.BoundedSemaphore(1)
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=limited))
        with patch.object(llm_client, "json_object_complete", return_value=True):
            result = llm_client.chat_json_object(
                client, "model", [{"role": "user", "content": "JSON"}], 1
            )

        self.assertEqual(result, '{"results": []}')
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(limited.create, stream=False).result(timeout=1)


if __name__ == "__main__":
    unittest.main()
