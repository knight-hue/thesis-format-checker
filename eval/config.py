# -*- coding: utf-8 -*-
"""评测配置：学院、样张规模、性能基准参数、输出目录。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class EvalConfig:
    college_id: str = "jlu_cs"
    # 合成样张：注入器数量 = 单错误样张数；另加 1 个干净样张测误报。
    include_semantic: bool = True          # 检测是否跑语义层（无 LLM 时自动只跑确定性条款）
    repeat_clean: int = 1                  # 干净样张重复次数（每次重新生成，稀释随机性）
    seed: int = 42                         # 随机种子（标题/正文随机化）

    # 性能基准
    scale_pages: tuple = (10, 50, 100, 200)  # 规模扫描页数档位
    scale_repeat: int = 1                    # 每档重复次数（取中位数）
    timing_repeat: int = 3                   # 分阶段耗时重复次数（取中位数）
    # 每个规模档位生成论文的正文段数：约 1 段/80 字 ≈ 1 页
    paragraphs_per_page: int = 24
    # 每个规模档位生成论文的图表数
    tables_per_page: int = 2
    figures_per_page: int = 2

    # 输出
    report_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "eval" / "out")
    # 真实样张目录：用户把待测 Word 文档（.docx）放进这里，评测统一读取逐个处理
    samples_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "eval" / "samples")
    # 运行时中间产物（样张 docx 等），默认不保留；True 时便于人工复查样张
    keep_artifacts: bool = False

    def __post_init__(self):
        if self.report_dir is not None:
            self.report_dir = Path(self.report_dir)
        else:
            self.report_dir = PROJECT_ROOT / "eval" / "out"
        if self.samples_dir is not None:
            self.samples_dir = Path(self.samples_dir)
        else:
            self.samples_dir = PROJECT_ROOT / "eval" / "samples"

    @property
    def artifacts_dir(self) -> Path:
        return self.report_dir / "artifacts"

    @property
    def data_dir(self) -> Path:
        return self.report_dir / "data"

    def ensure_dirs(self) -> None:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.keep_artifacts:
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
