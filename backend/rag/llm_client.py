"""生成模型客户端构造，以及"取文本 / 取 JSON 对象"两类请求封装。

判定与改写链路要求"失败不中断"，因此请求失败统一用 None / "" 表达，
由调用方决定如何处理；问答主链路不使用这里的封装，异常照旧上抛。
"""
import json
import logging
import os
import re
import threading
from urllib.parse import urlparse

from openai import OpenAI

from backend.config import (
    ALLOW_CLOUD_LLM_FOR_DOCUMENTS,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TIMEOUT_SECONDS,
    LLM_MAX_RETRIES,
    LLM_MAX_CONCURRENCY,
)

logger = logging.getLogger(__name__)


class _PermitStream:
    """在流式响应消费完毕前持续占用并发许可。"""

    def __init__(self, stream, semaphore):
        self._stream = iter(stream)
        self._raw_stream = stream
        self._semaphore = semaphore
        self._released = False

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self._stream)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._released:
            return
        self._released = True
        try:
            close = getattr(self._raw_stream, "close", None)
            if close is not None:
                close()
        finally:
            self._semaphore.release()

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()


class _LimitedCompletions:
    def __init__(self, completions, semaphore, acquire_timeout=LLM_TIMEOUT_SECONDS):
        self._completions = completions
        self._semaphore = semaphore
        self._acquire_timeout = acquire_timeout

    def create(self, *args, **kwargs):
        if not self._semaphore.acquire(timeout=self._acquire_timeout):
            raise TimeoutError("等待 LLM 并发许可超时")
        try:
            response = self._completions.create(*args, **kwargs)
        except BaseException:
            self._semaphore.release()
            raise
        if kwargs.get("stream"):
            return _PermitStream(response, self._semaphore)
        self._semaphore.release()
        return response

    def __getattr__(self, name):
        return getattr(self._completions, name)


class _LimitedChat:
    def __init__(self, chat, semaphore):
        self._chat = chat
        self.completions = _LimitedCompletions(chat.completions, semaphore)

    def __getattr__(self, name):
        return getattr(self._chat, name)


class _LimitedClient:
    """只限制生成请求，其余 OpenAI 客户端能力原样透传。"""

    def __init__(self, client, max_concurrency):
        self._client = client
        semaphore = threading.BoundedSemaphore(max_concurrency)
        self.chat = _LimitedChat(client.chat, semaphore)

    def __getattr__(self, name):
        return getattr(self._client, name)


def _new_client(api_key, base_url):
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=LLM_TIMEOUT_SECONDS,
        max_retries=LLM_MAX_RETRIES,
    )
    return _LimitedClient(client, LLM_MAX_CONCURRENCY)


def _resolve_base_url(env_name, default, what):
    """取模型服务的 OpenAI 兼容地址。

    空地址在 OpenAI 客户端里不会被当成"缺省"，而是发出相对 URL 请求并必然失败；
    这里直接抛错，避免把配置问题拖成每次问答重试后 500。
    """
    value = (os.getenv(env_name) or "").strip() or (default or "").strip()
    if not value:
        raise ValueError(
            f"{what}的 base_url 为空，请在 config.py 或环境变量 {env_name} 中填写 OpenAI 兼容地址"
        )
    return value


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}


def is_local_endpoint(base_url) -> bool:
    """端点是否在本机（含 127.0.0.0/8、localhost 与 *.local 主机名）。"""
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTS or host.startswith("127."):
        return True
    return host.endswith(".local") or host.endswith(".localhost")


def documents_may_leave_machine(base_url) -> bool:
    """论文/文档内容能否发给这个模型端点。

    项目铁律：论文内容只允许发往本机模型端点。非本机端点默认禁止；
    仅在中期演示或**不含敏感数据**的联调场景下显式放行：
    ``THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS=1``（一次性），或
    ``local_settings.ALLOW_CLOUD_LLM_FOR_DOCUMENTS = True``（本机持久，不入库）。
    环境变量设为 ``0`` 可覆盖本地配置临时关回。

    注意：这里只看端点位置，不判断调用类型。规范问答（只发规范片段与提问）
    不走该闸；语义判定/段落改写（带论文正文）必须先问这一函数。
    """
    if os.getenv("THESIS_ALLOW_CLOUD_LLM_FOR_DOCUMENTS", "").strip() == "1":
        return True
    if ALLOW_CLOUD_LLM_FOR_DOCUMENTS:
        return True
    return is_local_endpoint(base_url)


def create_llm_client():
    """构造生成模型的 OpenAI 兼容客户端。

    将来换成本地模型时只改这里：同样返回 (client, model_name)，调用方不需要
    知道模型跑在哪里。

    返回 (client, model_name)。
    """
    client = _new_client(
        os.getenv("LLM_API_KEY", LLM_API_KEY),
        _resolve_base_url("LLM_BASE_URL", LLM_BASE_URL, "在线 API 模型"),
    )
    return client, os.getenv("LLM_MODEL", LLM_MODEL)


def chat_text(client, model, prompt, temperature=0.0, max_tokens=LLM_MAX_TOKENS):
    """单轮对话取文本内容；调用失败返回 None。"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.warning("LLM 文本请求失败（model=%s）：%s", model, e)
        return None


def chat_json_text(client, model, prompt, max_tokens=LLM_MAX_TOKENS):
    """按 JSON 对象模式取文本内容；调用失败返回 None。

    与 chat_text 的唯一区别是请求带 response_format={"type": "json_object"}，
    由服务端约束模型只输出一个 JSON 对象，从根上排除"返回数组/标量/裸文本"
    这类解析不了的回复（前置条件是 prompt 里必须出现 JSON 字样）。

    服务端不支持该参数时回退普通请求，避免把参数不兼容变成"调用失败"。
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
            stream=False,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.warning("LLM JSON 模式请求失败，回退普通请求（model=%s）：%s", model, e)
        return chat_text(client, model, prompt, max_tokens=max_tokens)


def chat_json_object(client, model, messages, expected_count=None,
                     max_tokens=LLM_MAX_TOKENS):
    """流式请求 JSON 对象输出，边收边拼；results 数量足够时提前终止。

    流式失败时回退到非流式请求。
    """
    response = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            stream=True,
            response_format={"type": "json_object"},
        )
        text = ""
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None) or ""
            text += content
            if (expected_count is not None
                    and json_object_complete(text, expected_count)):
                break
        return text
    except Exception as e:
        logger.warning("流式 JSON 请求失败，回退非流式（model=%s）：%s", model, e)
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if close is not None:
                close()
    return _nonstream_json_object(client, model, messages, max_tokens)


def _nonstream_json_object(client, model, messages, max_tokens=LLM_MAX_TOKENS):
    """非流式请求 JSON 对象输出（流式失败时的兜底）。"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            stream=False,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error("非流式 JSON 请求失败，本次判定按解析失败处理（model=%s）：%s",
                     model, e)
        return ""


def json_object_complete(text, expected_count):
    """判断流式累积文本是否已构成完整的 JSON 对象且 results 数量达标。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE).strip()
    if not t.startswith("{"):
        return False
    try:
        data = json.loads(t)
    except Exception:
        return False
    results = data.get("results") if isinstance(data, dict) else None
    return isinstance(results, list) and len(results) >= expected_count
