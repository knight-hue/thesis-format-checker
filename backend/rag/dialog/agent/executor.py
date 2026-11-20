# -*- coding: utf-8 -*-
"""工具执行器：白名单 + 同参缓存 + 调用记录。

缓存的意义不是省时间，是**防循环**：模型反复用同样的参数调同一个工具时，
第二次直接返回上一次的结果，并且不会在执行记录里多出一条"有效动作"。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from backend.rag.dialog.agent.tools import (
    TOOL_NAMES,
    ToolContext,
    ToolResult,
    dumps,
    execute,
)


@dataclass
class CallRecord:
    step: int
    name: str
    args: dict
    ok: bool
    error: str = ""
    cached: bool = False

    @property
    def trace_line(self) -> str:
        return f"agent:{self.name}"


@dataclass
class ToolExecutor:
    """按白名单执行工具，并保留全部返回结果供输出校验。"""

    ctx: ToolContext
    max_cache: int = 64
    results: list[ToolResult] = field(default_factory=list)
    records: list[CallRecord] = field(default_factory=list)
    step: int = 0
    _cache: dict = field(default_factory=dict)

    def begin_step(self, step: int) -> None:
        self.step = int(step)

    def run(self, name: str, args=None) -> ToolResult:
        if name not in TOOL_NAMES:
            result = ToolResult(False, str(name), {}, f"未知工具：{name}")
            self.records.append(CallRecord(self.step, str(name), {}, False, result.error))
            self.results.append(result)
            return result
        clean_args = args if isinstance(args, dict) else {}
        key = (str(name), dumps(clean_args))
        if key in self._cache:
            cached = replace(self._cache[key], cached=True)
            self.records.append(CallRecord(self.step, str(name), clean_args,
                                           cached.ok, cached.error, cached=True))
            return cached
        result = execute(str(name), clean_args, self.ctx)
        if len(self._cache) < self.max_cache:
            self._cache[key] = result
        self.records.append(CallRecord(self.step, str(name), clean_args,
                                       result.ok, result.error))
        self.results.append(result)
        return result

    @property
    def trace(self) -> list[str]:
        return [record.trace_line for record in self.records]

    @property
    def ok_payloads(self) -> list[dict]:
        """成功返回的数据（输出校验的"事实白名单"）。"""
        return [result.data for result in self.results if result.ok]

    def has_new_evidence(self, before: int) -> bool:
        return len(self.results) > before
