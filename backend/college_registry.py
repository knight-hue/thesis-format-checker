# -*- coding: utf-8 -*-
"""规则文件的磁盘布局：学院 ID → `config/rules/<学校>/<学院>/`（各层共用，只此一份）。

## 布局

    config/rules/<school>/<college>/    ← 学校 → 学院，两层
        <college_id>_rules.json      机读规则（规则引擎逐项比对）
        <college_id>_basis.json      模板原句（报告里的"模板依据"）
        <college_id>_structure.json  结构识别表（"这一段是什么"）
        <college_id>.txt             规范原文（RAG 语料，注册表的 rule_file）

目录名取注册表 `config/colleges.json` 里的 `school` / `college` 字段：**id 是不透明
主键，不拆 id 猜坐标**（id 已经渗进缓存键、会话表、知识库目录）。同校同院的多个版本
与多个专业落在同一个学院目录里，靠文件名开头的完整 id 区分，不会互相覆盖。

## 为什么单独一个模块

识别层要按 id 找结构识别表、检测层要按 id 找规则与依据表，两边各拼一套路径，改一次
布局必漏一处。本模块只依赖 json + os + config，谁都能 import，不引入任何层。
注册表的字段含义、展示名与校验在 `rag/colleges.py`。
"""
import json
import os
import re

from backend.config import COLLEGES_CONFIG_PATH

# school / college 会拼进目录名：只允许小写字母、数字、连字符，
# 免得注册表里写个 "../.." 就能跑到 rules/ 外面去。
_SEGMENT_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

# 规范目录的根：学校 → 学院两层（相对项目根，见模块开头）。
RULES_ROOT = "config/rules"

# 学院目录里四份文件的固定后缀；文件名一律以完整 id 开头。
SUFFIXES = {
    "rules": "_rules.json",
    "basis": "_basis.json",
    "structure": "_structure.json",
    "corpus": ".txt",
}
# 判定配置的后缀：按 id 反查学院目录用。
RULE_SUFFIX = SUFFIXES["rules"]


def load_colleges(config_path=COLLEGES_CONFIG_PATH) -> dict:
    """读取学院配置，返回 {college_id: 学院配置}。"""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def college_dir(school, college, rules_dir=RULES_ROOT) -> str:
    """按坐标拼学院目录：`<rules_dir>/<school>/<college>`（纯拼接，不查注册表）。"""
    for part in (school, college):
        if not _SEGMENT_RE.fullmatch(str(part or "")):
            raise ValueError(f"学校/学院不能作为目录名：{part!r}")
    return os.path.join(rules_dir, str(school), str(college))


def file_path(school, college, college_id, kind, rules_dir=RULES_ROOT) -> str:
    """按坐标拼某份文件的路径（纯拼接，不查注册表；kind 见 SUFFIXES）。"""
    if kind not in SUFFIXES:
        raise ValueError(f"未知的规则文件类型：{kind}")
    return os.path.join(college_dir(school, college, rules_dir),
                        f"{college_id}{SUFFIXES[kind]}")


def dir_of(college_id, rules_dir=RULES_ROOT, config_path=COLLEGES_CONFIG_PATH) -> str:
    """按学院 ID 查注册表得到学院目录；未登记一律抛 ValueError。"""
    info = load_colleges(config_path).get(college_id)
    if not isinstance(info, dict):
        raise ValueError(f"未知学院：{college_id}")
    return college_dir(info.get("school"), info.get("college"), rules_dir)


def path_of(college_id, kind, rules_dir=RULES_ROOT,
            config_path=COLLEGES_CONFIG_PATH) -> str:
    """按学院 ID 查注册表得到某份文件的路径；未登记一律抛 ValueError。"""
    info = load_colleges(config_path).get(college_id)
    if not isinstance(info, dict):
        raise ValueError(f"未知学院：{college_id}")
    return file_path(info.get("school"), info.get("college"), college_id, kind,
                     rules_dir)


def rule_sets(rules_dir=RULES_ROOT):
    """扫 `config/rules/<学校>/<学院>/` 里的判定配置，返回 [(学院 ID, 文件路径), ...]。"""
    found = []
    if not os.path.isdir(rules_dir):
        return found
    for school in sorted(os.listdir(rules_dir)):
        school_path = os.path.join(rules_dir, school)
        if not os.path.isdir(school_path):
            continue
        for college in sorted(os.listdir(school_path)):
            college_path = os.path.join(school_path, college)
            if not os.path.isdir(college_path):
                continue
            for name in sorted(os.listdir(college_path)):
                if name.endswith(RULE_SUFFIX):
                    found.append((name[: -len(RULE_SUFFIX)],
                                  os.path.join(college_path, name)))
    return found
