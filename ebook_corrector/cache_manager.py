"""
缓存管理模块 - 管理已处理内容的纠错结果缓存

功能：
- 基于文本哈希的缓存机制，避免对相同文本重复调用API
- 持久化缓存到磁盘JSON文件
- 支持缓存查询和写入
"""

import os
import json
import hashlib
from typing import Dict, Optional


class CacheManager:
    """缓存管理器"""

    def __init__(self, cache_dir: str = ".corrector_cache"):
        self.cache_dir = cache_dir
        self._cache: Dict[str, str] = {}
        self._cache_file = os.path.join(self.cache_dir, "correction_cache.json")
        self._load_cache()

    def _load_cache(self):
        """从磁盘加载缓存"""
        if os.path.exists(self._cache_file):
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._cache = {}

    def save_cache(self):
        """将缓存持久化到磁盘"""
        os.makedirs(self.cache_dir, exist_ok=True)
        try:
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"警告：缓存保存失败 - {e}")

    @staticmethod
    def _hash_text(text: str) -> str:
        """计算文本的MD5哈希值"""
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def get(self, text: str) -> Optional[str]:
        """
        查询缓存中是否已有该文本的纠正结果

        Args:
            text: 原始文本

        Returns:
            Optional[str]: 纠正后的文本，如果不存在则返回None
        """
        hash_key = self._hash_text(text)
        return self._cache.get(hash_key)

    def set(self, original_text: str, corrected_text: str):
        """
        将纠正结果写入缓存

        Args:
            original_text: 原始文本
            corrected_text: 纠正后的文本
        """
        hash_key = self._hash_text(original_text)
        self._cache[hash_key] = corrected_text

    def get_cache_count(self) -> int:
        """获取缓存条目数量"""
        return len(self._cache)
