# -*- coding: utf-8 -*-
"""知识库的落盘位置与指纹：库的键 = 语料内容 + 切片参数的哈希（内容寻址）。

原先库的路径写在配置里（`var/kb/<学院 id>`），有两个隐患：

1. **语料换了、库没重建时没有任何提示**：检测按新规则判、问答还按旧规范答，
   两边结论互相矛盾却静默发生。
2. **语料相同的学院各建一份库**：同校共用一份规范时，磁盘与建库时间白涨。

改成内容寻址后：库目录名就是语料哈希前缀——语料一变键就变，取不到库就是明确
"需要重建"，不可能拿到旧语料建的库；语料相同的学院天然命中同一个库。

本模块只做路径计算与指纹读写，不加载向量模型（`--status` 要在没模型时也能跑）。
"""
import hashlib
import json
import os
import sys

from backend.config import KB_ROOT_DIR

# 指纹文件名与结构版本：指纹里的语料哈希与参数就是"这个库是按什么建的"的凭据。
FINGERPRINT_NAME = "fingerprint.json"
FINGERPRINT_SCHEMA = 1

# 库目录名的哈希前缀长度：12 个十六进制字符（48 位）足够区分校内的几十份语料。
KEY_LENGTH = 12


def corpus_sha256(text: str) -> str:
    """语料原文的 sha256（按 UTF-8 稳定编码，与文件换行符无关地看内容）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_corpus(rule_file: str) -> str:
    with open(rule_file, "r", encoding="utf-8") as handle:
        return handle.read()


def kb_key(text: str, chunk_size: int, chunk_overlap: int) -> str:
    """库的键：语料内容 + 切片参数（同语料不同切片也要各建一份）。"""
    payload = "\n".join([corpus_sha256(text), str(chunk_size), str(chunk_overlap)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:KEY_LENGTH]


def kb_dir(root: str, key: str) -> str:
    return os.path.join(root, key)


def is_built(path: str) -> bool:
    """库目录建好了没：Chroma 的持久化库至少要有 chroma.sqlite3。"""
    return os.path.isfile(os.path.join(path, "chroma.sqlite3"))


def read_fingerprint(path: str):
    """读库目录里的指纹；没有或坏了都返回 None（调用方按"未建库"处理）。"""
    try:
        with open(os.path.join(path, FINGERPRINT_NAME), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_fingerprint(path: str, payload: dict) -> str:
    """写指纹，返回文件路径。"""
    os.makedirs(path, exist_ok=True)
    target = os.path.join(path, FINGERPRINT_NAME)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({**payload, "schema_version": FINGERPRINT_SCHEMA}, handle,
                  ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return target


def needs_rebuild(path: str, key: str) -> bool:
    """这个键对应的库要不要重建。

    内容寻址下"键相同"就意味着语料与切片参数都一样，理论上必然能直接用；真正
    需要重建只有两种情况：库不存在（没建过/换过语料），或目录在但没建完
    （中途失败留下的空目录）。
    """
    if not is_built(path):
        return True
    fingerprint = read_fingerprint(path)
    if fingerprint is None:
        return True
    return str(fingerprint.get("kb_key") or "") != key


def college_status(colleges: dict, root: str = KB_ROOT_DIR) -> list:
    """逐个学院报告：期望的库键、库在哪、建好了没、指纹里还记着哪些学院。

    只读文件，不加载模型——启动自检与 CI 用它，不要为了看一眼状态载入 ONNX。
    """
    report = []
    for college_id, info in colleges.items():
        rule_file = str(info.get("rule_file") or "")
        chunk_size = int(info.get("chunk_size", 300))
        chunk_overlap = int(info.get("chunk_overlap", 50))
        item = {
            "college_id": college_id,
            "rule_file": rule_file,
            "kb_key": "",
            "kb_path": "",
            "built": False,
            "shared_with": [],
            "error": "",
        }
        try:
            key = kb_key(read_corpus(rule_file), chunk_size, chunk_overlap)
        except OSError as exc:
            item["error"] = f"读不到规范文本：{exc}"
            report.append(item)
            continue
        path = kb_dir(root, key)
        item.update({"kb_key": key, "kb_path": path, "built": is_built(path)})
        fingerprint = read_fingerprint(path) if item["built"] else None
        item["shared_with"] = [
            cid for cid in (fingerprint or {}).get("colleges", []) if cid != college_id
        ]
        report.append(item)
    return report


def _main(argv=None):
    """临时自检入口：`python -m backend.rag.kb_store --status [--root var/kb]`。"""
    import argparse

    from backend.config import COLLEGES_CONFIG_PATH
    from backend.rag.colleges import load_colleges

    parser = argparse.ArgumentParser(description="知识库状态（内容寻址）")
    parser.add_argument("--status", action="store_true",
                        help="打印每个学院的期望库键与是否已构建（默认行为）")
    parser.add_argument("--root", default=KB_ROOT_DIR,
                        help=f"知识库根目录（默认 {KB_ROOT_DIR}）")
    parser.add_argument("--config", default=COLLEGES_CONFIG_PATH)
    parser.add_argument("--missing-only", action="store_true",
                        help="只打印需要（重新）构建的学院 ID，每行一个")
    args = parser.parse_args(argv)

    status = college_status(load_colleges(args.config), args.root)
    missing = [item["college_id"] for item in status if not item["built"]]
    if args.missing_only:
        for college_id in missing:
            print(college_id)
        return 1 if missing else 0
    for item in status:
        note = "" if item["built"] else "（待构建）"
        if item["error"]:
            note = f"（{item['error']}）"
        shared = f" 共用者：{'、'.join(item['shared_with'])}" if item["shared_with"] else ""
        print(f"{item['college_id']}\t{item['kb_key'] or '-'}\t{item['kb_path']}{note}{shared}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(_main())
