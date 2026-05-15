# Ebook OCR Text Corrector

A powerful command-line tool that corrects OCR (Optical Character Recognition) errors in EPUB ebooks using Large Language Models (LLM).

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)]()

---

## Features

- **Intelligent OCR Error Correction** — Leverages LLMs to detect and fix OCR misrecognitions (visually similar characters, phonetic confusions, radical errors) based on context, avoiding over-correction of correct words.
- **EPUB Format Preservation** — Parses EPUB via lxml, applies character-level diffs directly to the HTML tree, and outputs a corrected EPUB with original formatting, layout, and structure fully intact.
- **Batch & Concurrent Processing** — Groups texts into batches for single API calls, with a configurable thread pool for dramatic speed improvement. Supports ~120 API calls instead of ~4500 serial calls for a typical book.
- **Deduplication & Caching** — Deduplicates identical text segments before processing; caches correction results by text MD5 hash in a local JSON file to avoid redundant API calls. Resume safely after interruption.
- **Pre-filtering** — Automatically skips non-Chinese and very short text segments, saving API quota on irrelevant content.
- **Detailed Reporting** — Generates a Markdown correction diff table and a summary statistics report.
- **Progress Display** — Real-time progress bar with batch status during processing.
- **Graceful Interruption** — Saves cache on Ctrl+C; resume from where you left off.

---

## Quick Start

### Installation

```bash
git clone https://github.com/yourusername/ebook-ocr-corrector.git
cd ebook-ocr-corrector
pip install -r requirements.txt
```

### Basic Usage

```bash
# Analyze only (no actual correction)
python main.py book.epub --dry-run

# Full correction (default: DeepSeek API)
python main.py book.epub --api-key sk-xxxxx

# Full correction (OpenAI compatible API)
python main.py book.epub --api-key sk-xxxxx --model gpt-4 --api-base https://api.openai.com/v1

# Specify output directory
python main.py book.epub --api-key sk-xxxxx --output-dir ./output
```

---

## Command Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `epub_path` | EPUB file path | (required) |
| `--api-key` | API key (also via `OPENAI_API_KEY` / `EBOOK_API_KEY` env) | — |
| `--api-base` | API base URL | `https://api.deepseek.com` |
| `--model` | Model name | `deepseek-v4-flash` |
| `--temperature` | Model temperature (lower = more conservative) | `0.1` |
| `--chunk-size` | Max chunk size (chars) for text | `2000` |
| `--batch-size` | Number of texts per API batch call | `10` |
| `--workers` | Number of concurrent worker threads | `3` |
| `--output-dir` | Output directory | current directory |
| `--output-name` | Output EPUB filename (no extension) | `{original}_corrected` |
| `--cache-dir` | Cache directory | `.corrector_cache` |
| `--no-cache` | Disable caching | `False` |
| `--dry-run` | Analyze only, no correction | `False` |
| `--verbose` / `-v` | Verbose output | `False` |

---

## Output Files

| File | Format | Description |
|------|--------|-------------|
| `{name}_corrected.epub` | EPUB | Corrected ebook |
| `{name}_correction_table.md` | Markdown | Error-to-correction mapping table |
| `{name}_correction_report.md` | Markdown | Summary statistics report |

---

## How It Works

The processing pipeline consists of 5 stages:

```
[1/5] Parse EPUB       → Extract text by block-level elements via lxml
[2/5] Init Engine      → Connect to LLM API, load cache from disk
[3/5] Correct Text     → Dedup → Pre-filter → Cache lookup →
                          Batch grouping → Thread pool concurrent API calls →
                          Apply results to segments
[4/5] Generate Reports → Correction table + Summary statistics
[5/5] Build EPUB       → Apply corrections to HTML tree → Re-package EPUB
```

### Stage Details

**1. EPUB Parsing**: Uses `ebooklib` to read the EPUB, then `lxml.html` to parse HTML documents. Text is extracted by block-level elements (`<p>`, `<div>`, `<h1>`–`<h6>`, `<li>`, etc.) using `text_content()`, which concatenates all descendant text and avoids fragmentation from inline tags.

**2. Correction Prompt**: The LLM is instructed to:
- Only correct **single-character OCR errors** (visually similar chars like `倬`→`使`, `人`↔`入`; phonetically similar chars; radical errors)
- **Never** rewrite sentences, rephrase, replace synonyms, add/delete content, modify punctuation, or alter proper nouns
- Preserve every character unless certain it's an OCR error. Return verbatim if no errors found.

**3. Batch + Concurrent Pipeline**:
- Pre-filtering skips non-Chinese and very short (< 3 chars) texts
- Remaining texts are deduplicated via `OrderedDict`; identical texts processed once
- Texts are partitioned into batches (configurable via `--batch-size`)
- Multiple batches are processed concurrently via `ThreadPoolExecutor` (configurable via `--workers`)
- Each batch sends one API call with `[N]`-indexed text segments

**4. Precision Application**: Instead of raw string matching (which fails on HTML entities like `&amp;`), the tool re-parses the corrected HTML, locates matching block elements by index, computes character-level diffs using `difflib`, and applies replacements directly to lxml text nodes. `html.tostring()` serialization automatically handles HTML entity encoding/decoding.

**5. EPUB Packaging**: Modified HTML files are written back into a new EPUB ZIP archive using the original EPUB as a template, preserving all non-text resources (images, stylesheets, fonts).

---

## Performance

For a typical 730,000-character EPUB:

| Technique | Without | With |
|-----------|---------|------|
| **Pre-filtering** | — | Skips ~5% of texts automatically |
| **Deduplication** | ~4,791 API calls | ~4,289 unique texts |
| **Batching** (batch-size=10) | ~4,289 calls | ~429 calls |
| **Concurrency** (workers=3) | ~428 serial calls | ~143 parallel batches |
| **Caching** | Full reprocess | Instant resume after interrupt |

Estimated time improvement: **>10x** compared to naive serial processing.

---

## Configuration via Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EBOOK_API_KEY` | — | API key (alternative to `--api-key`) |
| `OPENAI_API_KEY` | — | API key (fallback) |
| `EBOOK_CORRECTOR_MODEL` | `deepseek-v4-flash` | Model name |
| `EBOOK_CORRECTOR_API_BASE` | `https://api.deepseek.com` | API base URL |
| `EBOOK_CORRECTOR_BATCH_SIZE` | `10` | Texts per batch |
| `EBOOK_CORRECTOR_WORKERS` | `3` | Concurrent worker threads |
| `EBOOK_CORRECTOR_CHUNK_SIZE` | `2000` | Max text chunk (characters) |
| `EBOOK_CORRECTOR_MAX_TOKENS` | `4096` | Max output tokens |
| `EBOOK_CORRECTOR_TEMPERATURE` | `0.1` | Model temperature |
| `EBOOK_CORRECTOR_CACHE_DIR` | `.corrector_cache` | Cache directory |
| `EBOOK_CORRECTOR_REQUEST_INTERVAL` | `0.1` | Rate limit interval (seconds) |
| `EBOOK_CORRECTOR_MAX_RETRIES` | `3` | API retry count |

---

## Project Structure

```
ebook-ocr-corrector/
├── main.py                              # CLI entry point
├── requirements.txt                     # Dependencies
├── .corrector_cache/                    # Cache directory (auto-created)
├── docs/                                # Documentation
│   ├── README_en.md                     # English documentation
│   └── README_zh.md                     # Chinese documentation
├── ebook_corrector/                     # Core module package
│   ├── __init__.py                      # Package init
│   ├── config.py                        # Global config, LLM prompts, correction rules
│   ├── epub_handler.py                  # EPUB I/O: parse, apply corrections, save
│   ├── llm_corrector.py                 # LLM API client: single & batch correction
│   ├── cache_manager.py                 # Text hash-based cache persistence
│   ├── text_processor.py                # Pipeline: dedup, pre-filter, batch, concurrency
│   └── report_generator.py              # Correction table & summary report generation
```

### Module Descriptions

| Module | Responsibility | Key Classes |
|--------|---------------|-------------|
| `config.py` | Defaults, prompts, patterns | — |
| `epub_handler.py` | EPUB I/O, lxml tree manipulation | `EpubHandler`, `TextSegment`, `ChapterInfo` |
| `llm_corrector.py` | OpenAI-compatible API client | `LLMCorrector` |
| `cache_manager.py` | MD5-hash based JSON cache | `CacheManager` |
| `text_processor.py` | Processing pipeline orchestration | `TextProcessor` |
| `report_generator.py` | Markdown report generation | `ReportGenerator` |
| `main.py` | CLI entry point | — |

---

## Example Workflows

```bash
# 1. Analyze the EPUB structure first
python main.py "my_book.epub" --dry-run

# Sample output:
# [1/5] 解析EPUB文件...
#     章节数：96 | 文档数：97 | 文本段：4940 | 总字符：745,954

# 2. Run correction with DeepSeek (default)
python main.py "my_book.epub" --api-key sk-xxxxx

# 3. Run correction with OpenAI GPT-4
python main.py "my_book.epub" --api-key sk-xxxxx --model gpt-4 --api-base https://api.openai.com/v1

# 4. Aggressive speed mode (for large books with high API quota)
python main.py "my_book.epub" --api-key sk-xxxxx --batch-size 20 --workers 5

# 5. Conservative mode (for unstable APIs)
python main.py "my_book.epub" --api-key sk-xxxxx --batch-size 5 --workers 1 --temperature 0.05
```

---

## Dependencies

- Python 3.8+
- [ebooklib](https://github.com/aerkalov/ebooklib) — EPUB file I/O
- [openai](https://pypi.org/project/openai/) — LLM API client
- [lxml](https://lxml.de/) — HTML/XML parsing and tree manipulation
- [tqdm](https://tqdm.github.io/) — Progress bar (optional)

---

## Planned Improvements

- PyQt5 GUI (modular architecture already prepared for this)
- Support for additional ebook formats (PDF, DJVU)
- Local model support (via llama.cpp / ollama)
- Custom correction rules configuration file
- Side-by-side diff viewer for corrections

---

## License

MIT License

## Contributing

Issues and Pull Requests are welcome!
