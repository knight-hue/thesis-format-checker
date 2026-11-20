"""学院配置读取、坐标展开与校验。

单独成一个模块是为了让 API 启动阶段能直接列出学院：
- api 的 /api/colleges 在启动后立刻就要返回学院列表，绝不能因为加载向量模型而变慢；
- KBManager 初始化知识库时也读同一份配置。

因此这里只依赖 json + config，不 import 任何 langchain / 向量库。

配置形态（config/colleges.json）：**id 是不透明主键，坐标写在字段里**。

    {
      "jlu_cs": {
        "school": "jlu",           "school_name": "吉林大学",
        "college": "cs",           "college_name": "计算机科学与技术学院",
        "major": null,             "major_name": null,
        "template_version": "Z-3", "effective_year": null,
        "rule_file": "config/rules/jlu/cs/jlu_cs.txt",
        "chunk_size": 300, "chunk_overlap": 50
      }
    }

id 只允许小写字母、数字、下划线、连字符，多版本用 @ 后缀（如 jlu_cs@2024）：它要
拼进文件名与知识库目录名，所以字符集必须收窄；学校/学院/专业一律进字段，不塞进 id，
免得以后靠拆字符串猜坐标（id 已经渗进缓存键、会话表、知识库目录）。同一
school+college+major 挂多版模板时，每版一个条目，id 带 @版本后缀，template_version
必须与后缀一致。

规则文件放在 `config/rules/<school>/<college>/`（学校 → 学院两层，文件以完整 id 开头），
布局与"按 id 找文件"只由 `college_registry` 一处决定，本模块负责展示名与校验。
"""
import json
import os
import re

from backend.college_registry import RULES_ROOT, file_path, load_colleges, rule_sets

from backend.config import (
    COLLEGE_ID_PATTERN,
    COLLEGE_REQUIRED_FIELDS,
    COLLEGES_CONFIG_PATH,
)

# 展示名里坐标之间的分隔符：学校·学院（版本）
_NAME_SEPARATOR = "·"

_ID_RE = re.compile(COLLEGE_ID_PATTERN)


def _posix(path: str) -> str:
    """问题清单里的路径一律用正斜杠：Windows 上反斜杠会被当成转义，也难读。"""
    return path.replace(os.sep, "/")


def display_name(info) -> str:
    """条目的展示名：学校·学院（专业）（模板版本），前端下拉直接用。"""
    parts = [str(info.get("school_name") or "").strip(),
             str(info.get("college_name") or "").strip(),
             str(info.get("major_name") or "").strip()]
    name = _NAME_SEPARATOR.join(part for part in parts if part)
    version = str(info.get("template_version") or "").strip()
    return f"{name}（{version}）" if version and name else name


def coordinates(config_path=COLLEGES_CONFIG_PATH):
    """展开成坐标列表：id 是主键，学校/学院/专业/版本是坐标字段。"""
    items = []
    for cid, info in load_colleges(config_path).items():
        items.append({
            "college_id": cid,
            "display_name": display_name(info),
            "school": info.get("school"),
            "school_name": info.get("school_name"),
            "college": info.get("college"),
            "college_name": info.get("college_name"),
            "major": info.get("major"),
            "major_name": info.get("major_name"),
            "template_version": info.get("template_version"),
            "effective_year": info.get("effective_year"),
        })
    return items


def validate_registry(config_path=COLLEGES_CONFIG_PATH, root=".", rules_dir=None):
    """校验配置与规则文件是否对得上，返回问题清单（空列表 = 通过）。

    守两件事：
      ① 每个条目的坐标字段齐全、id 合乎规范、规范文本与规则集/依据表都在；
      ② 反向：`config/rules/<学校>/<学院>/` 下每个规则集都必须在配置里登记，不允许
         "孤儿规则集"（否则那个学院判不了、也没人知道它存在）。
    """
    try:
        colleges = load_colleges(config_path)
    except FileNotFoundError:
        return [f"缺少学院配置：{config_path}"]
    except json.JSONDecodeError as exc:
        return [f"学院配置不是合法 JSON：{config_path}（{exc}）"]
    if not isinstance(colleges, dict) or not colleges:
        return [f"学院配置应是非空对象：{config_path}"]

    rules_dir = rules_dir or os.path.join(root, RULES_ROOT)
    problems, seen = [], {}
    for cid, info in colleges.items():
        if not isinstance(info, dict):
            problems.append(f"{cid}：配置项应为对象")
            continue
        if not _ID_RE.fullmatch(cid):
            problems.append(
                f"{cid}：学院 ID 不合规（只允许小写字母、数字、下划线、连字符，多版本用 @版本后缀）")
        for field in COLLEGE_REQUIRED_FIELDS:
            if not str(info.get(field) or "").strip():
                problems.append(f"{cid}：缺少必填字段 {field}")
        version = info.get("template_version")
        if "@" in cid:
            suffix = cid.split("@", 1)[1]
            if not str(version or "").strip():
                problems.append(f"{cid}：id 带 @ 后缀就必须写 template_version")
            elif str(version) != suffix:
                problems.append(
                    f"{cid}：template_version（{version}）与 id 的 @ 后缀（{suffix}）不一致")
        rule_file = str(info.get("rule_file") or "")
        if rule_file:
            path = rule_file if os.path.isabs(rule_file) \
                else os.path.join(root, rule_file)
            if not os.path.isfile(path):
                problems.append(f"{cid}：规范文本不存在（{rule_file}）")
        for kind, label in (("rules", "规则文件"), ("basis", "依据表")):
            try:
                path = file_path(info.get("school"), info.get("college"), cid,
                                 kind, rules_dir)
            except ValueError as exc:
                # 坐标不能当目录名：说一次原因就够，后面不再报"缺文件"。
                problems.append(f"{cid}：{exc}")
                break
            if not os.path.isfile(path):
                problems.append(
                    f"{cid}：缺少{label} {_posix(os.path.relpath(path, root))}")
        coordinate = (info.get("school"), info.get("college"), info.get("major"),
                      version)
        if coordinate in seen:
            problems.append(
                f"{cid}：与 {seen[coordinate]} 的坐标完全相同"
                "（同校同院同专业的多版模板要靠 @版本后缀区分）")
        else:
            seen[coordinate] = cid

    for cid, path in rule_sets(rules_dir):
        if cid not in colleges:
            problems.append(
                f"{_posix(os.path.relpath(path, root))}：规则集没有登记在 "
                f"{config_path}（孤儿规则集）")
    return problems
