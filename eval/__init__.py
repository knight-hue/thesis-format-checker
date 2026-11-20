# -*- coding: utf-8 -*-
"""论文格式校验系统评测框架。

评估两个维度：
- 准确性：合成样张（合规基线 + 单错误注入）上的解析/检测/修复/写回全链路指标；
- 性能：单机分阶段耗时、规模扫描、内存与预热时间。

用法（项目根目录）：
    python -m eval.run --stage accuracy,perf --report-dir eval/out
"""
