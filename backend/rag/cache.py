"""RAG 问答结果的进程内 LRU 缓存。

按 CACHE_MAX_SIZE 限制容量，超出后淘汰最久未访问的条目。
"""
from collections import OrderedDict

from backend.config import CACHE_MAX_SIZE


# ========== LRU 缓存 ==========
class LRUCache:
    """LRU 缓存实现"""

    def __init__(self, max_size=CACHE_MAX_SIZE):
        self.max_size = max_size
        self.cache = OrderedDict()

    def get(self, key):
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def set(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)
