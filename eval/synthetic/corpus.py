# -*- coding: utf-8 -*-
"""样张语料编排：把合规基线 + 注入器组装成评测用例集。

每个注入器产出一个"单错误样张"（金标准恰好 1 条），外加干净样张测误报。
"""
import shutil
from pathlib import Path

from ..config import EvalConfig
from ..golden import SyntheticCase
from .baseline import build_baseline
from .injectors import INJECTORS, injector_names


def _artifact_dir(config: EvalConfig) -> Path:
    path = config.artifacts_dir if config.keep_artifacts else config.data_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_docx(config: EvalConfig, name: str, mutate) -> SyntheticCase:
    """构建一个样张；mutate 若提供，其返回值（list[GoldenIssue]）作为金标准。"""
    docx_path = str(_artifact_dir(config) / f"{name}.docx")
    golden = []

    def _wrap(handles):
        nonlocal golden
        if mutate is not None:
            golden.extend(mutate(handles) or [])

    build_baseline(docx_path, mutate=_wrap)
    return SyntheticCase(
        name=name, docx_path=docx_path, golden=golden,
        college_id=config.college_id, is_clean=mutate is None,
    )


def build_corpus(config: EvalConfig) -> list:
    """构建全部合成样张。返回 list[SyntheticCase]（clean 在前，注入器在后）。

    注入器返回空金标准（如因与结构识别耦合而无法干净注入的规则）会被跳过，
    避免产出"无金标准的非干净样张"干扰误报统计。
    """
    cases: list = []
    for i in range(config.repeat_clean):
        cases.append(_build_docx(
            config,
            "clean" if config.repeat_clean == 1 else f"clean_{i}",
            mutate=None,
        ))
    for name in injector_names():
        case = _build_docx(config, name, mutate=INJECTORS[name])
        if not case.golden:
            Path(case.docx_path).unlink(missing_ok=True)
            continue
        cases.append(case)
    return cases


def clear_data(config: EvalConfig) -> None:
    """清空评测数据目录（保留报告输出结构）。"""
    for sub in ("data", "artifacts"):
        path = config.report_dir / sub
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
