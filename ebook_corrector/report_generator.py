"""
报告生成模块 - 生成纠错对照表和统计报告

功能：
- 生成详细的错别字替换对照表
- 生成纠错处理摘要报告
- 支持Markdown格式输出
"""

import os
from typing import List, Dict, Tuple
from collections import Counter
from datetime import datetime


class ReportGenerator:
    """报告生成器"""

    def __init__(self, output_dir: str = "."):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_correction_table(
        self,
        correction_pairs: List[Tuple[str, str]],
        output_name: str = "correction_table",
    ) -> str:
        """
        生成纠错对照表

        Args:
            correction_pairs: 纠错对列表 [(错误, 正确), ...]
            output_name: 输出文件名（不含扩展名）

        Returns:
            str: 生成的文件路径
        """
        file_path = os.path.join(self.output_dir, f"{output_name}.md")

        aggregated: Dict[str, str] = {}
        for wrong, right in correction_pairs:
            if wrong in aggregated:
                if len(right) < len(aggregated[wrong]):
                    aggregated[wrong] = right
            else:
                aggregated[wrong] = right

        lines = [
            "# 电子书文本纠错对照表",
            "",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "| 序号 | 错误文本 | 正确文本 |",
            "|------|----------|----------|",
        ]

        sorted_pairs = sorted(aggregated.items(), key=lambda x: len(x[0]), reverse=True)

        for idx, (wrong, right) in enumerate(sorted_pairs, 1):
            wrong_escaped = wrong.replace("|", "\\|").replace("\n", " ")
            right_escaped = right.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {idx} | `{wrong_escaped}` | `{right_escaped}` |")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        return file_path

    def generate_summary_report(
        self,
        correction_pairs: List[Tuple[str, str]],
        total_chars: int,
        corrected_segments: int,
        total_segments: int,
        epub_stats: Dict,
        process_stats: Dict,
        epub_name: str = "",
        output_name: str = "correction_report",
    ) -> str:
        """
        生成纠错处理摘要报告

        Args:
            correction_pairs: 纠错对列表
            total_chars: 总处理字符数
            corrected_segments: 被纠正的文本段数量
            total_segments: 文本段总数
            epub_stats: EPUB文件统计信息
            process_stats: 处理过程统计信息
            epub_name: EPUB文件名
            output_name: 输出文件名（不含扩展名）

        Returns:
            str: 生成的文件路径
        """
        file_path = os.path.join(self.output_dir, f"{output_name}.md")

        error_type_counter = Counter()
        for wrong, _ in correction_pairs:
            for char in wrong:
                error_type_counter[char] += 1

        correction_ratio = (
            (corrected_segments / total_segments * 100) if total_segments > 0 else 0
        )

        lines = [
            "# 电子书文本纠错处理摘要报告",
            "",
            f"**处理时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
            "## 基本信息",
            "",
            f"- **原始文件**：{epub_name}",
            f"- **总处理字符数**：{total_chars:,}",
            f"- **文本段总数**：{total_segments}",
            f"- **纠正文本段数**：{corrected_segments}",
            f"- **纠正比例**：{correction_ratio:.2f}%",
            "",
            "## EPUB文件结构",
            "",
            f"- **章节数**：{epub_stats.get('total_chapters', 0)}",
            f"- **文档数**：{epub_stats.get('spine_items', 0)}",
            f"- **文本段总数**：{epub_stats.get('total_segments', 0)}",
            f"- **总字符数**：{epub_stats.get('total_chars', 0):,}",
            "",
            "## 纠错统计",
            "",
            f"- **错误类型（字符）计数**：{len(error_type_counter)} 种",
            f"- **总纠错次数**：{len(correction_pairs)} 次",
            "",
        ]

        if process_stats:
            lines.extend([
                "## 处理过程统计",
                "",
                f"- **缓存命中数**：{process_stats.get('cache_hits', 0)}",
                f"- **纠错对总数（含重复）**：{process_stats.get('total_correction_pairs', 0)}",
                f"- **去重后纠错对数**：{process_stats.get('unique_correction_pairs', 0)}",
                "",
            ])

        if error_type_counter:
            lines.extend([
                "## 常见错误字符统计",
                "",
                "| 序号 | 错误字符 | 出现次数 |",
                "|------|----------|----------|",
            ])

            for idx, (char, count) in enumerate(
                error_type_counter.most_common(20), 1
            ):
                char_escaped = char.replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {idx} | `{char_escaped}` | {count} |")

            lines.append("")

        if correction_pairs:
            lines.extend([
                "## 纠错样例（前50条）",
                "",
                "| 序号 | 错误文本 | 正确文本 |",
                "|------|----------|----------|",
            ])

            for idx, (wrong, right) in enumerate(correction_pairs[:50], 1):
                wrong_escaped = wrong.replace("|", "\\|").replace("\n", " ")
                right_escaped = right.replace("|", "\\|").replace("\n", " ")
                lines.append(f"| {idx} | `{wrong_escaped}` | `{right_escaped}` |")

            lines.append("")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        return file_path
