#!/usr/bin/env python3
"""
电子书文本纠错助手 - 命令行入口

使用方法：
    python main.py <epub文件路径> --api-key <API密钥> [可选参数]

示例：
    python main.py book.epub --api-key sk-xxx
    python main.py book.epub --api-key sk-xxx --model deepseek-v4-flash
    python main.py book.epub --api-key sk-xxx --output-dir ./output
"""

import os
import sys
import argparse
import time

from ebook_corrector.epub_handler import EpubHandler
from ebook_corrector.llm_corrector import LLMCorrector
from ebook_corrector.cache_manager import CacheManager
from ebook_corrector.text_processor import TextProcessor
from ebook_corrector.report_generator import ReportGenerator
from ebook_corrector.config import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MODEL,
    DEFAULT_API_BASE,
    DEFAULT_TEMPERATURE,
    DEFAULT_CACHE_DIR,
    DEFAULT_BATCH_SIZE,
    DEFAULT_WORKERS,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="电子书文本纠错助手 - 基于大语言模型的EPUB电子书OCR文本纠错工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python main.py book.epub --api-key sk-xxxxx
  python main.py book.epub --api-key sk-xxxxx --model deepseek-v4-flash
  python main.py book.epub --api-key sk-xxxxx --output-dir ./output
        """,
    )

    parser.add_argument(
        "epub_path",
        type=str,
        help="EPUB电子书文件路径",
    )

    parser.add_argument(
        "--api-key",
        type=str,
        required=False,
        default=None,
        help="大语言模型API密钥（也可通过环境变量 OPENAI_API_KEY 或 EBOOK_API_KEY 设置）",
    )

    parser.add_argument(
        "--api-base",
        type=str,
        default=DEFAULT_API_BASE,
        help=f"API基础URL（默认: {DEFAULT_API_BASE}）",
    )

    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"模型名称（默认: {DEFAULT_MODEL}）",
    )

    parser.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_TEMPERATURE,
        help=f"模型温度参数，越低越保守（默认: {DEFAULT_TEMPERATURE}）",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"文本处理块大小（默认: {DEFAULT_CHUNK_SIZE}）",
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"每批次打包的文本条数，增大可提速但增加单次API耗时（默认: {DEFAULT_BATCH_SIZE}）",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"并发工作线程数，增大可提速但增加API压力（默认: {DEFAULT_WORKERS}）",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=".",
        help="输出目录（默认: 当前目录）",
    )

    parser.add_argument(
        "--output-name",
        type=str,
        default=None,
        help="输出EPUB文件名（不含扩展名，默认: 原文件名_corrected）",
    )

    parser.add_argument(
        "--cache-dir",
        type=str,
        default=DEFAULT_CACHE_DIR,
        help=f"缓存目录（默认: {DEFAULT_CACHE_DIR}）",
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="禁用缓存机制",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅分析文件结构，不进行实际纠错",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="显示详细输出信息",
    )

    return parser.parse_args()


def get_api_key(args) -> str:
    """获取API密钥，优先级：命令行参数 > 环境变量"""
    api_key = args.api_key
    if not api_key:
        api_key = os.environ.get("EBOOK_API_KEY")
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "未提供API密钥。请通过 --api-key 参数指定，或设置环境变量 "
            "EBOOK_API_KEY / OPENAI_API_KEY"
        )
    return api_key


def print_separator(char: str = "=", length: int = 60):
    """打印分隔线"""
    print(char * length)


def print_progress(current: int, total: int, status: str = ""):
    """打印进度信息"""
    percent = (current / total * 100) if total > 0 else 0
    bar_length = 40
    filled = int(bar_length * current / total) if total > 0 else 0
    bar = "#" * filled + "-" * (bar_length - filled)
    print(f"\r进度: [{bar}] {percent:.1f}% ({current}/{total}) {status}", end="")
    sys.stdout.flush()


def main():
    args = parse_args()

    if not os.path.exists(args.epub_path):
        print(f"错误：文件不存在 - {args.epub_path}")
        sys.exit(1)

    if not args.epub_path.lower().endswith(".epub"):
        print("警告：文件扩展名不是 .epub，仍将尝试处理...")

    os.makedirs(args.output_dir, exist_ok=True)

    epub_name = os.path.basename(args.epub_path)
    base_name = os.path.splitext(epub_name)[0]
    output_epub_name = args.output_name or f"{base_name}_corrected"
    output_epub_path = os.path.join(args.output_dir, f"{output_epub_name}.epub")

    print_separator()
    print("[电子书文本纠错助手] v1.0.0")
    print_separator()
    print(f"输入文件：{epub_name}")
    print(f"输出文件：{output_epub_name}.epub")
    print(f"模型：{args.model}")
    print(f"API地址：{args.api_base}")
    print()

    print("[1/5] 解析EPUB文件...")
    start_time = time.time()

    try:
        epub_handler = EpubHandler(args.epub_path)
        segments = epub_handler.parse()
    except Exception as e:
        print(f"错误：EPUB文件解析失败 - {e}")
        sys.exit(1)

    if not segments:
        print("警告：未从EPUB文件中提取到文本内容")
        sys.exit(0)

    stats = epub_handler.get_statistics()
    print(f"    章节数：{stats['total_chapters']}")
    print(f"    文档数：{stats['spine_items']}")
    print(f"    文本段：{stats['total_segments']}")
    print(f"    总字符：{stats['total_chars']:,}")
    print(f"    去重后文本段：{len(set(s.original_text for s in segments))}")
    print()

    if args.dry_run:
        print("[OK] 试运行模式完成，未进行实际纠错")
        print_separator()
        return

    print("[2/5] 初始化纠错引擎...")
    try:
        api_key = get_api_key(args)
        corrector = LLMCorrector(
            api_key=api_key,
            api_base=args.api_base,
            model=args.model,
            temperature=args.temperature,
        )
    except Exception as e:
        print(f"错误：纠错引擎初始化失败 - {e}")
        sys.exit(1)

    cache_manager = CacheManager(
        cache_dir="" if args.no_cache else args.cache_dir
    )

    text_processor = TextProcessor(
        cache_manager=cache_manager,
        chunk_size=args.chunk_size,
        batch_size=args.batch_size,
        max_workers=args.workers,
    )

    print("    纠错引擎就绪")
    print(f"    批次大小：{args.batch_size} | 并发线程：{args.workers}")
    if not args.no_cache:
        print(f"    缓存条目：{cache_manager.get_cache_count()}")
    print()

    print("[3/5] 执行文本纠错...")
    print()

    corrected_count = [0]

    def progress_callback(current: int, total: int, status: str):
        corrected_count[0] = current
        print_progress(current, total, status)

    try:
        text_processor.process_segments(
            segments=segments,
            corrector=corrector,
            progress_callback=progress_callback,
        )
    except KeyboardInterrupt:
        print("\n\n用户中断操作")
        cache_manager.save_cache()
        print("缓存已保存，下次运行将继续使用已缓存结果")
        sys.exit(1)
    except Exception as e:
        print(f"\n错误：文本纠错过程异常 - {e}")
        cache_manager.save_cache()
        sys.exit(1)

    print()

    correction_pairs = text_processor.get_correction_pairs()
    modified_segments = [s for s in segments if s.is_modified]

    print()
    print(f"    [OK] 纠错完成")
    print(f"    纠正文本段：{len(modified_segments)}/{len(segments)}")
    print(f"    纠错对数：{len(correction_pairs)}")
    print(f"    缓存命中：{cache_manager.get_cache_count()}")
    print()

    print("[4/5] 生成报告...")
    report_gen = ReportGenerator(output_dir=args.output_dir)

    correction_table_path = report_gen.generate_correction_table(
        correction_pairs=correction_pairs,
        output_name=f"{base_name}_correction_table",
    )
    print(f"    纠错对照表：{correction_table_path}")

    summary_report_path = report_gen.generate_summary_report(
        correction_pairs=correction_pairs,
        total_chars=epub_handler.get_total_chars(),
        corrected_segments=len(modified_segments),
        total_segments=len(segments),
        epub_stats=stats,
        process_stats=text_processor.get_statistics(),
        epub_name=epub_name,
        output_name=f"{base_name}_correction_report",
    )
    print(f"    摘要报告：{summary_report_path}")
    print()

    print("[5/5] 生成纠正后的EPUB文件...")
    try:
        epub_handler.apply_corrections(segments)
        saved_path = epub_handler.save(output_epub_path)
        print(f"    [OK] 输出文件：{saved_path}")
    except Exception as e:
        print(f"错误：EPUB文件生成失败 - {e}")
        sys.exit(1)

    elapsed = time.time() - start_time
    print()
    print_separator()
    print("[完成] 处理完成！")
    print_separator()
    print(f"总耗时：{elapsed:.1f} 秒")
    print(f"总字符：{epub_handler.get_total_chars():,}")
    print(f"纠错数：{len(correction_pairs)} 处")
    print(f"纠正段：{len(modified_segments)}/{len(segments)}")
    print()
    print("生成文件：")
    print(f"  [EPUB] 纠正后EPUB：{os.path.abspath(saved_path)}")
    print(f"  [TABLE] 纠错对照表：{os.path.abspath(correction_table_path)}")
    print(f"  [REPORT] 摘要报告：{os.path.abspath(summary_report_path)}")
    print()


if __name__ == "__main__":
    main()
