"""
文本处理模块 - 负责文本分块、去重、预过滤、批量并发纠错

处理管线：
  原始文本段 → 去重 → 预过滤(跳过无中文/过短) → 缓存命中跳过 →
  动态分组成批次 → 线程池并发批量API调用 → 应用结果
"""

import re
import threading
from typing import List, Tuple, Dict, Optional, Callable
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import DEFAULT_CHUNK_SIZE, DEFAULT_BATCH_SIZE, DEFAULT_WORKERS, MAX_BATCH_CHARS, CHINESE_CHAR_PATTERN
from .epub_handler import TextSegment
from .cache_manager import CacheManager
from .llm_corrector import LLMCorrector


class TextProcessor:
    """文本处理器 - 批量+并发+预过滤管线"""

    def __init__(
        self,
        cache_manager: CacheManager,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_workers: int = DEFAULT_WORKERS,
    ):
        self.cache_manager = cache_manager
        self.chunk_size = chunk_size
        self.batch_size = batch_size
        self.max_workers = max_workers
        self._all_correction_pairs: List[Tuple[str, str]] = []
        self._pairs_lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self._completed_count = 0
        self._total_count = 0
        self._progress_callback: Optional[Callable[[int, int, str], None]] = None

    def _has_chinese(self, text: str) -> bool:
        """判断文本是否包含中文字符"""
        return bool(re.search(CHINESE_CHAR_PATTERN, text))

    def _is_skippable(self, text: str) -> bool:
        """判断文本是否可以跳过（无纠错价值）"""
        text = text.strip()
        if len(text) < 3:
            return True
        if not self._has_chinese(text):
            return True
        return False

    def process_segments(
        self,
        segments: List[TextSegment],
        corrector: LLMCorrector,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[TextSegment]:
        """
        批量并发处理管线

        1. 去重：相同文本只处理一次
        2. 预过滤：跳过无中文或过短的文本
        3. 缓存查询：命中缓存直接使用
        4. 动态分批：将待处理文本按 batch_size 分组成批
        5. 线程池并发：多线程同时发起批量 API 调用
        6. 结果应用：将纠正结果应用到所有引用该文本的段

        Args:
            segments: 文本段列表
            corrector: LLM纠错器实例
            progress_callback: 进度回调 (current, total, status)

        Returns:
            List[TextSegment]: 处理后的文本段（包含纠正结果）
        """
        self._all_correction_pairs = []
        self._completed_count = 0
        self._progress_callback = progress_callback

        # 1. 去重
        unique_texts: OrderedDict[str, List[TextSegment]] = OrderedDict()
        for seg in segments:
            text = seg.original_text.strip()
            if text not in unique_texts:
                unique_texts[text] = []
            unique_texts[text].append(seg)

        # 2. 预过滤 + 3. 缓存查询 → 分离出需要 API 处理的文本
        all_texts = list(unique_texts.keys())
        api_texts: List[Tuple[str, int]] = []  # (text, index_in_all)
        cache_hits = 0
        skip_no_chinese = 0
        skip_too_short = 0

        for idx, text in enumerate(all_texts):
            ref_segments = unique_texts[text]

            # 预过滤
            if self._is_skippable(text):
                if not self._has_chinese(text):
                    skip_no_chinese += 1
                else:
                    skip_too_short += 1
                for seg in ref_segments:
                    seg.corrected_text = text
                    seg.is_modified = False
                continue

            # 缓存查询
            cached = self.cache_manager.get(text)
            if cached is not None:
                cache_hits += 1
                self._apply_result(ref_segments, text, cached)
                continue

            api_texts.append((text, idx))

        self._total_count = len(api_texts)
        if self._total_count == 0:
            self._report_skip_stats(len(all_texts), cache_hits, skip_no_chinese, skip_too_short, 0)
            self.cache_manager.save_cache()
            return segments

        # 4. 动态分批
        batches = self._build_batches(api_texts)

        self._report_skip_stats(len(all_texts), cache_hits, skip_no_chinese, skip_too_short, len(batches))

        # 5. 线程池并发处理
        self._process_batches_concurrent(batches, unique_texts, all_texts, corrector)

        # 保存缓存
        self.cache_manager.save_cache()
        return segments

    def _report_skip_stats(self, total: int, cache_hits: int, no_cn: int, too_short: int, batch_count: int):
        """报告预处理阶段的跳过统计"""
        if cache_hits > 0:
            print(f"    缓存命中：{cache_hits}")
        if no_cn > 0:
            print(f"    跳过(无中文)：{no_cn}")
        if too_short > 0:
            print(f"    跳过(过短)：{too_short}")
        print(f"    需API处理文本段：{self._total_count}（{batch_count}个批次）")
        print()

    def _build_batches(
        self, api_texts: List[Tuple[str, int]]
    ) -> List[List[Tuple[str, int]]]:
        """
        动态构建批次，确保每批次总字符数不超过 MAX_BATCH_CHARS

        Args:
            api_texts: [(text, original_index), ...]

        Returns:
            List[List[Tuple[str, int]]]: 分批结果
        """
        batches = []
        current_batch = []
        current_chars = 0

        for text, idx in api_texts:
            text_len = len(text)
            if current_batch and (len(current_batch) >= self.batch_size or current_chars + text_len > MAX_BATCH_CHARS):
                batches.append(current_batch)
                current_batch = []
                current_chars = 0
            current_batch.append((text, idx))
            current_chars += text_len

        if current_batch:
            batches.append(current_batch)

        return batches

    def _process_batches_concurrent(
        self,
        batches: List[List[Tuple[str, int]]],
        unique_texts: OrderedDict,
        all_texts: List[str],
        corrector: LLMCorrector,
    ):
        """线程池并发处理所有批次"""
        total_batches = len(batches)
        completed_batches = [0]
        batch_lock = threading.Lock()

        def process_one_batch(batch: List[Tuple[str, int]]) -> int:
            """处理单个批次，返回处理的文本数"""
            texts_only = [t for t, _ in batch]

            try:
                results = corrector.correct_batch(texts_only)
            except Exception as e:
                print(f"\n警告：批次处理失败 - {e}")
                for text, idx in batch:
                    ref_segments = unique_texts[text]
                    for seg in ref_segments:
                        seg.corrected_text = text
                        seg.is_modified = False
                    self.cache_manager.set(text, text)
                return len(batch)

            processed = 0
            for (original_text, _), (corrected_text, correction_pairs) in zip(batch, results):
                ref_segments = unique_texts[original_text]

                self._apply_result(ref_segments, original_text, corrected_text)

                with self._pairs_lock:
                    self._all_correction_pairs.extend(correction_pairs)

                self.cache_manager.set(original_text, corrected_text)
                processed += 1

            with batch_lock:
                completed_batches[0] += 1
                with self._pairs_lock:
                    self._completed_count += processed

            if self._progress_callback:
                with self._progress_lock:
                    self._progress_callback(
                        self._completed_count,
                        self._total_count,
                        f"批次 {completed_batches[0]}/{total_batches}",
                    )

            return processed

        print(f"    共 {total_batches} 个批次，{self.max_workers} 个并发工作线程")
        print()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(process_one_batch, batch): i for i, batch in enumerate(batches)}

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    batch_idx = futures[future]
                    print(f"\n警告：批次 {batch_idx + 1} 处理异常 - {e}")

        if self._progress_callback:
            self._progress_callback(
                self._total_count,
                self._total_count,
                f"完成！共 {total_batches} 批次",
            )

    def _apply_result(
        self,
        ref_segments: List[TextSegment],
        original_text: str,
        corrected_text: str,
    ):
        """将纠正结果应用到所有引用该文本的段"""
        for seg in ref_segments:
            if corrected_text != original_text:
                seg.corrected_text = corrected_text
                seg.is_modified = True
            else:
                seg.corrected_text = original_text
                seg.is_modified = False

    def get_correction_pairs(self) -> List[Tuple[str, str]]:
        """获取去重后的纠错对列表"""
        seen = set()
        unique_pairs = []
        for wrong, right in self._all_correction_pairs:
            key = (wrong, right)
            if key not in seen:
                seen.add(key)
                unique_pairs.append((wrong, right))
        return unique_pairs

    def get_statistics(self) -> Dict:
        """获取文本处理统计信息"""
        return {
            "total_correction_pairs": len(self._all_correction_pairs),
            "unique_correction_pairs": len(self.get_correction_pairs()),
            "cache_hits": self.cache_manager.get_cache_count(),
        }
