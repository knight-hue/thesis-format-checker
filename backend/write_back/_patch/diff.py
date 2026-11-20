# -*- coding: utf-8 -*-

"""契约差异判断：字段级与块级“变没变”，是写回是否动手的唯一开关。"""


def value_changed(old, new, key):
    return old.get(key) != new.get(key)


def block_changed(old, new, skip=frozenset()):
    """块级差异判断；结构/索引字段等由调用方通过 skip 排除。"""
    return any(
        value_changed(old, new, key)
        for key in old.keys() | new.keys()
        if key not in skip
    )
