# -*- coding: utf-8 -*-
"""学院上架流水线 CLI：``python -m backend.onboarding <子命令>``。

- ``extract <规范文档.docx> [--out 目录]``：抽取条目 → spec_items.json + corpus.txt；
- ``coverage <college_id> [--decisions 台账.json] [--out 目录]``：覆盖率对账
  （门禁不过退出码 1，可直接进 CI）；
- ``check``：注册表校验 + 知识库状态自检（不加载向量模型）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _cmd_extract(args) -> int:
    from backend.onboarding.extract import extract_docx, write_outputs

    result = extract_docx(args.docx)
    out_dir = args.out or os.path.join(
        "var", "onboarding",
        os.path.splitext(os.path.basename(args.docx))[0])
    files = write_outputs(result, out_dir)
    total = sum(len(group["items"]) for group in result["sections"])
    print(f"抽取完成：条目 {total} 条，章/节组 {len(result['sections'])} 个")
    for warning in result["warnings"]:
        print(f"  ⚠ {warning}")
    for name, path in files.items():
        print(f"  {name}: {path}")
    return 0


def _cmd_coverage(args) -> int:
    from backend.onboarding.coverage import render_markdown, run_coverage

    decisions = None
    if args.decisions:
        with open(args.decisions, encoding="utf-8") as handle:
            decisions = json.load(handle)
    report = run_coverage(args.college_id, decisions)
    text = render_markdown(report)
    print(text)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        target = os.path.join(args.out, f"coverage_{args.college_id}.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"已写出：{target}")
    return 0 if report["gate_passed"] else 1


def _cmd_check(args) -> int:
    from backend.college_registry import RULES_ROOT, load_colleges
    from backend.rag.colleges import validate_registry
    from backend.rag.kb_store import college_status

    colleges = load_colleges()
    problems = validate_registry(root=".", rules_dir=RULES_ROOT)
    if problems:
        print("注册表校验未通过：")
        for problem in problems:
            print(f"  - {problem}")
    status = college_status(colleges)
    missing = [item for item in status if not item["built"]]
    print(f"学院 {len(colleges)} 个；注册表问题 {len(problems)}；"
          f"待建知识库 {len(missing)}")
    for item in status:
        note = "已构建" if item["built"] else "待构建"
        if item["error"]:
            note = item["error"]
        print(f"  {item['college_id']}\t{item['kb_key'] or '-'}\t{note}")
    return 1 if problems or missing else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.onboarding",
        description="学院上架流水线（抽取 / 覆盖率对账 / 自检）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="规范文档 → 条目台账 + corpus 草案")
    p_extract.add_argument("docx", help="规范文档（.docx；.doc 请先另存为 docx）")
    p_extract.add_argument("--out", default=None, help="输出目录（默认 var/onboarding/<文件名>）")
    p_extract.set_defaults(func=_cmd_extract)

    p_cov = sub.add_parser("coverage", help="覆盖率对账（每句归宿三选一）")
    p_cov.add_argument("college_id")
    p_cov.add_argument("--decisions", default=None,
                       help='"明确不查"台账 JSON（键=条目文本或其子串，值=理由）')
    p_cov.add_argument("--out", default=None, help="报告输出目录（可选）")
    p_cov.set_defaults(func=_cmd_coverage)

    p_check = sub.add_parser("check", help="注册表校验 + 知识库状态自检")
    p_check.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
