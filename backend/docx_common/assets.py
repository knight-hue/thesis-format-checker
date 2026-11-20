# -*- coding: utf-8 -*-
"""图片/公式资产的位置约定：磁盘布局与契约 ``path`` 写法的唯一出处。

解析层落盘、修复层换图、写回层读图都依赖同一套约定：

- 磁盘上图片落在 ``<output_dir>/images/``；
- 契约里 ``block["path"]`` 一律写成 ``images/<文件名>``（相对 JSON 文件所在目录）。

三层各写一份 ``os.path.join("images", ...)`` 时，任何一处改动都会让"JSON 与
images 目录同级"这个前提静默失效，因此集中到本模块。
"""
import os

IMAGES_DIRNAME = "images"


def images_dir(base_dir):
    """契约 JSON 同级的 images 目录绝对路径。"""
    return os.path.join(base_dir, IMAGES_DIRNAME)


def image_path(filename):
    """``block["path"]`` 的规范写法，相对于 JSON 文件所在目录。"""
    return os.path.join(IMAGES_DIRNAME, filename)


def find_image_file(directory, index):
    """按 block 的 index 在 images 目录里反查图片文件，返回绝对路径；找不到返回 None。

    解析层按 ``img_{index:04d}`` 命名，但历史数据出现过多档位，这里逐个探测。
    """
    if not os.path.isdir(directory):
        return None

    candidates = sorted(os.listdir(directory))
    prefixes = [
        f"img_{index:04d}.",
        f"img_{index:03d}.",
        f"img_{index}.",
        f"image_{index:04d}.",
        f"image_{index:03d}.",
        f"pic_{index:04d}.",
        f"pic_{index:03d}.",
    ]
    for prefix in prefixes:
        for fname in candidates:
            if fname.startswith(prefix):
                return os.path.join(directory, fname)
    return None
