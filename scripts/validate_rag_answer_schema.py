"""Validate RAG answer records against the schema v2 contract.

Standalone stdlib validator for training/eval JSONL. It does not call the LLM.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PASS_RE = re.compile(r"(符合|一致|合规|合格|通过|无需修改|均符合)")
WORD_RE = re.compile(r"(居中|居左|对齐|行距|加粗|字符间距|编号|首行缩进|缩进|相对位置|图下|图上)")
NEG_RE = re.compile(r"(不能|无法|未|不|没有|不足以|未知|未观测|非|不得|不应)")
REQUIREMENT_RE = re.compile(r"(应|需|须|要|要求|必须|应当)")
VERSIONS = {"1.0", "2.0"}
QUOTE_RE = re.compile(r"[“\"][^”\"]{0,100}[”\"]")
NORMATIVE_RE = re.compile(r"(建议|提议|新建|采用|如果|若|假设|受控|目标|要求|规范|条款|规则|应|需|须|无关|无法确定|缺失)")


def _is_string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _positive(text, pattern):
    for clause in re.split(r"[，。；\n]", QUOTE_RE.sub("", text or "")):
        for match in pattern.finditer(clause):
            prefix = clause[:match.start()]
            suffix = clause[match.end():].lstrip()
            if NEG_RE.search(prefix) or REQUIREMENT_RE.search(prefix[-10:]) or NORMATIVE_RE.search(prefix):
                continue
            if suffix.startswith(("以下要求", "下列要求", "如下要求")):
                continue
            return True
    return False


def _normative_quote(text):
    head = (text or "")[:160]
    return (
        any(key in head for key in ("规则片段", "条款", "规范", "要求"))
        and any(key in text for key in ("条件要求", "这些是条件"))
        and "实际格式符合" not in text
    ) or ("条件目标" in text and "不能" in text) or (
        any(key in head for key in ("规则片段", "条款", "规范"))
        and any(key in text for key in ("实际字体", "实际格式", "未观测", "证据不足", "不能作为", "无法确定", "无关"))
        and "实际格式符合" not in text
    )


def _positive_word(text):
    if _normative_quote(text):
        return False
    for clause in re.split(r"[，。；\n]", QUOTE_RE.sub("", text or "")):
        if NORMATIVE_RE.search(clause) and not any(k in clause for k in ("实际", "观测", "实测")):
            continue
        for match in WORD_RE.finditer(clause):
            if not NEG_RE.search(clause[:match.start()]) and not NEG_RE.search(clause[match.end():]):
                return True
    return False


WORD_KEYS = {
    "居中": ("alignment", "paragraph_alignment"),
    "居左": ("alignment", "paragraph_alignment"),
    "对齐": ("alignment", "paragraph_alignment"),
    "行距": ("line_spacing", "word_line_spacing"),
    "加粗": ("bold", "word_bold_setting"),
    "字符间距": ("character_spacing", "word_character_spacing"),
    "编号": ("numbering", "numbering_format"),
    "首行缩进": ("indent", "first_line_indent"),
    "缩进": ("indent", "first_line_indent"),
    "相对位置": ("object_relative_position",),
    "图下": ("object_relative_position",),
    "图上": ("object_relative_position",),
}


def _unknown_attribute_claimed(conclusion, unknown):
    if not unknown:
        return False
    if "actual_format_attributes" in unknown:
        return _positive_word(conclusion)
    for token, keys in WORD_KEYS.items():
        if token in conclusion and any(key in unknown for key in keys):
            return _positive_word(conclusion)
    return False


def validate_record(record):
    """Return a list of (code, message) tuples."""
    errors = []
    if not isinstance(record, dict):
        return [("not_object", "record must be a JSON object")]
    # 训练记录把回答放在 answer 里；校验其内容而不是外层 metadata。
    if not record.get("conclusion") and isinstance(record.get("answer"), dict):
        record = record["answer"]
    for key in ("conclusion", "basis", "suggestion"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append((f"missing_{key}", f"{key} must be a non-empty string"))

    version = record.get("answer_schema_version", "1.0")
    if version not in VERSIONS:
        errors.append(("bad_version", "answer_schema_version must be '1.0' or '2.0'"))
    if version == "2.0" and "can_judge" not in record:
        errors.append(("missing_can_judge", "schema 2.0 must include can_judge"))

    can_judge = record.get("can_judge")
    if can_judge is not None and not isinstance(can_judge, bool):
        errors.append(("bad_can_judge", "can_judge must be boolean or null"))
    observed = record.get("observed", [])
    unknown = record.get("unknown", [])
    if not _is_string_list(observed):
        errors.append(("bad_observed", "observed must be a list of non-empty strings"))
    elif any("=" not in item and ":" not in item for item in observed):
        errors.append(("observed_missing_value", "each observed item must contain a concrete value (key=value)"))
    if not _is_string_list(unknown):
        errors.append(("bad_unknown", "unknown must be a list of non-empty strings"))

    conclusion = record.get("conclusion") or ""
    if can_judge is False and _positive(conclusion, PASS_RE):
        errors.append(("can_judge_false_but_pass", "can_judge=false but conclusion claims compliance"))
    if _unknown_attribute_claimed(conclusion, unknown):
        errors.append(("unknown_attribute_claimed", "unknown is non-empty but conclusion claims a format attribute"))
    return errors


def load_records(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return data["records"]
    return [data]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate RAG answer schema v2 JSONL")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        records = load_records(args.path)
    except Exception as exc:
        print(f"load failed: {exc}", file=sys.stderr)
        return 2

    total_errors = 0
    for index, record in enumerate(records):
        for code, message in validate_record(record):
            total_errors += 1
            print(f"{args.path}:{index}: {code}: {message}")
    print(f"records={len(records)} errors={total_errors}")
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
