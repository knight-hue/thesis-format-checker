# -*- coding: utf-8 -*-

"""段落文字的最小改动改写：只动真正变化的 run，其余（域代码/图形/批注锚点）原样保留。"""

from docx.text.run import Run

from backend.docx_common.runs import iter_run_entries

from .keys import RUN_TEXT_CHILDREN


def run_writable(run_el):
    """run 是否只由 w:rPr/w:t 组成——只有这种 run 才能整段换字而不丢内容。"""
    return all(child.tag in RUN_TEXT_CHILDREN for child in run_el)


def run_index_at(starts, position):
    """字符下标落在第几个 run 上；越界时收敛到相邻 run（空 run 序列落到前一个）。"""
    index = 0
    for candidate, start in enumerate(starts):
        if start <= position:
            index = candidate
        else:
            break
    return index


def common_affix(old_text, new_text):
    """新旧文字的最长公共前缀/后缀长度，用于把整段改写收窄成最小改动区间。"""
    limit = min(len(old_text), len(new_text))
    prefix = 0
    while prefix < limit and old_text[prefix] == new_text[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old_text[-1 - suffix] == new_text[-1 - suffix]:
        suffix += 1
    return prefix, suffix


def rewrite_paragraph_text(paragraph, old_text, new_text, report, where):
    """按 run 边界做最小改动改写段落文字（W5）。

    旧实现要求整段“无字段/批注/图片”才能改写，否则直接抛错导致整次写回失败：
    目录域段、脚注锚点段、含图段落一律改不动。现在只改真正变化的 run：
    - 未落到改动区间上的 run（域代码、图形、批注锚点等）原样保留；
    - 改动区间落在不可安全改写的 run 上时才跳过并告警；
    - 段落当前文字与解析契约不一致时跳过，避免按错位的下标写字。
    """
    entries = [element for element, _hyperlink in iter_run_entries(paragraph._p)]
    texts = [Run(element, None).text for element in entries]
    if "".join(texts) != old_text:
        return report.skip("段落文字与原文档不一致，无法安全改写", where)
    if old_text == new_text:
        return True
    if not entries:
        paragraph.add_run(new_text)
        return True
    if old_text == "":
        # 空段落没有可用的“改动位置”：追加到末尾，最不具破坏性
        if not run_writable(entries[-1]):
            return report.skip("段落末尾含字段/图形等不可安全改写的 run，已跳过", where)
        tail = Run(entries[-1], None)
        tail.text = (tail.text or "") + new_text
        return True

    starts = []
    position = 0
    for text in texts:
        starts.append(position)
        position += len(text)
    prefix, suffix = common_affix(old_text, new_text)
    change_end = len(old_text) - suffix
    head = prefix if prefix < len(old_text) else len(old_text) - 1
    first = run_index_at(starts, head)
    last = first if change_end <= prefix else run_index_at(starts, change_end - 1)
    if not all(run_writable(entries[index]) for index in range(first, last + 1)):
        return report.skip("改写区间含字段/图形等不可安全改写的 run，已跳过", where)
    middle = new_text[prefix:len(new_text) - suffix]
    head_keep = texts[first][:prefix - starts[first]]
    tail_keep = texts[last][change_end - starts[last]:]
    if first == last:
        Run(entries[first], None).text = head_keep + middle + tail_keep
    else:
        Run(entries[first], None).text = head_keep + middle
        for index in range(first + 1, last):
            Run(entries[index], None).text = ""
        Run(entries[last], None).text = tail_keep
    return True
