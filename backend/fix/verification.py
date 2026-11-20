"""Confirm exported DOCX fixes with fresh parsing; applied is not verified."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from backend.parser import docx_to_json
from backend.recognition.structure import load_structure
from backend.document_contract import DocumentContract
from backend.detection.checker_service import check_document

# 写回允许改动的部件；其余原始部件必须逐字节保持不变。
MUTABLE_PARTS = {"word/document.xml", "word/numbering.xml"}


def _block_signature(block):
    cells = tuple(
        (cell.get("row"), cell.get("col"), (cell.get("text") or "").strip())
        for cell in (block.get("cells") or [])
    )
    return (
        block.get("type"), block.get("kind"), (block.get("text") or "").strip(), cells,
    )


def non_target_blocks_unchanged(before, after, candidates, issues):
    """Compare blocks not targeted by a fix. None means block count changed."""
    before_blocks = before.get("blocks") or []
    after_blocks = after.get("blocks") or []
    if len(before_blocks) != len(after_blocks):
        return None
    targets = set()
    for index in candidates:
        location = (issues[index].get("location") or {}) if index < len(issues) else {}
        if isinstance(location.get("index"), int):
            targets.add(location["index"])
    for index, (old, new) in enumerate(zip(before_blocks, after_blocks)):
        if index in targets:
            continue
        if _block_signature(old) != _block_signature(new):
            return False
    return True


def _part_hashes(path: str) -> dict[str, str]:
    hashes = {}
    with zipfile.ZipFile(path) as package:
        for name in package.namelist():
            if name in MUTABLE_PARTS or name.endswith("/"):
                continue
            hashes[name] = hashlib.sha256(package.read(name)).hexdigest()
    return hashes


def unchanged_opc_parts(original_path: str | None, output_path: str) -> bool | None:
    """Original non-document parts must survive byte-for-byte. None = not checked."""
    if not original_path or not Path(original_path).is_file():
        return None
    before = _part_hashes(original_path)
    after = _part_hashes(output_path)
    return all(after.get(name) == digest for name, digest in before.items())


def confirmed_indexes(issues, candidates, before, after, semantic_checked=False):
    """Conservative rule-wide confirmation, safe even when block indexes shift."""
    before_rules = {i.get("rule_id") for i in before.get("issues", [])}
    after_rules = {i.get("rule_id") for i in after.get("issues", [])}
    unchecked = {i.get("rule_id") for i in after.get("unchecked", [])}
    # Newly introduced deterministic errors invalidate confirmation for this export.
    if after_rules - before_rules:
        return []
    allowed_sources = {"rule_engine"}
    if semantic_checked:
        allowed_sources.add("rag")
    return [n for n in candidates if issues[n].get("source") in allowed_sources
            and issues[n].get("rule_id") in before_rules
            and issues[n].get("rule_id") not in after_rules | unchecked]


def verify_export(path, output_dir, original, college_id, issues, candidates,
                  original_path=None, rag_service=None):
    """Return confirmed indexes plus explicit preservation checks."""
    semantic_checked = bool(
        rag_service is not None
        and any(issues[n].get("source") == "rag" for n in candidates)
    )
    service = rag_service if semantic_checked else None
    before = check_document(original, college_id, rag_service=service,
                            include_semantic=semantic_checked)
    parsed_path = docx_to_json(path, output_dir=str(output_dir), structure=load_structure(college_id))
    data = json.loads(Path(parsed_path).read_text(encoding="utf-8"))
    DocumentContract.model_validate(data)
    after = check_document(data, college_id, rag_service=service,
                           include_semantic=semantic_checked)
    confirmed = confirmed_indexes(issues, candidates, before, after,
                                 semantic_checked=semantic_checked)
    parts = unchanged_opc_parts(original_path, path)
    blocks = non_target_blocks_unchanged(original, data, candidates, issues)
    if parts is False or blocks is False:
        confirmed = []
    return {
        "confirmed": confirmed,
        "parts_unchanged": parts,
        "non_target_unchanged": blocks,
        "semantic_checked": semantic_checked,
    }
