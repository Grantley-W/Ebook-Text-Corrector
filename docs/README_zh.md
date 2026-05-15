# 电子书文本纠错助手

一款基于大语言模型（LLM）的命令行工具，专用于纠正 EPUB 电子书中因 OCR 识别产生的错别字与生僻字问题。

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)]()

---

## 功能特性

- **智能 OCR 纠错** — 调用大语言模型检测并纠正 OCR 识别错误（形近字混淆如"倬→使"、"人↔入"；音近字混淆；偏旁部首识别错误），根据上下文判断是否需要修改，避免误改正确文字。
- **EPUB 格式完整保留** — 通过 lxml 解析电子书，在 HTML 树上进行字符级差异修改，最终输出的 EPUB 保持原排版、格式和结构不变。
- **批量 + 并发处理** — 将多条文本打包为一次 API 调用（默认每批 10 条），配合可配置的线程池并发数（默认 3 线程），典型场景下 API 调用次数从 ~4500 次降至 ~120 次。
- **去重与缓存** — 相同文本只处理一次；按文本 MD5 哈希值缓存纠错结果至本地 JSON 文件，避免重复 API 调用。中断后重新运行可从中断处继续。
- **预过滤** — 自动跳过无中文内容及过短文本段（< 3 字），节省 API 额度。
- **详细报告** — 生成 Markdown 格式的纠错对照表和统计摘要报告。
- **进度显示** — 实时进度条，显示当前处理状态和批次信息。
- **优雅中断** — Ctrl+C 自动保存缓存，下次运行可从中断处继续。

---

## 快速开始

### 安装

```bash
git clone https://github.com/yourusername/ebook-ocr-corrector.git
cd ebook-ocr-corrector
pip install -r requirements.txt
```

### 基本使用

```bash
# 试运行（仅分析不纠错）
python main.py book.epub --dry-run

# 完整纠错（默认 DeepSeek API）
python main.py book.epub --api-key sk-xxxxx

# 完整纠错（OpenAI 兼容 API）
python main.py book.epub --api-key sk-xxxxx --model gpt-4 --api-base https://api.openai.com/v1

# 指定输出目录
python main.py book.epub --api-key sk-xxxxx --output-dir ./output
```

---

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `epub_path` | EPUB 电子书文件路径 | (必填) |
| `--api-key` | API 密钥（也可通过环境变量 `OPENAI_API_KEY` / `EBOOK_API_KEY` 设置） | — |
| `--api-base` | API 基础 URL | `https://api.deepseek.com` |
| `--model` | 模型名称 | `deepseek-v4-flash` |
| `--temperature` | 模型温度，越低越保守 | `0.1` |
| `--chunk-size` | 文本分块大小（字符数） | `2000` |
| `--batch-size` | 每批次打包的文本条数 | `10` |
| `--workers` | 并发工作线程数 | `3` |
| `--output-dir` | 输出目录 | 当前目录 |
| `--output-name` | 输出 EPUB 文件名（不含扩展名） | `{原文件名}_corrected` |
| `--cache-dir` | 缓存目录 | `.corrector_cache` |
| `--no-cache` | 禁用缓存 | `False` |
| `--dry-run` | 仅分析文件结构，不进行纠错 | `False` |
| `--verbose` / `-v` | 显示详细输出 | `False` |

---

## 生成文件

成功运行后，会在输出目录生成以下文件：

| 文件 | 格式 | 说明 |
|------|------|------|
| `{名称}_corrected.epub` | EPUB | 纠错后的电子书 |
| `{名称}_correction_table.md` | Markdown | 错误→正确纠错对照表 |
| `{名称}_correction_report.md` | Markdown | 统计摘要报告 |

---

## 工作原理

处理管线包含 5 个阶段：

```
[1/5] 解析 EPUB   → 通过 lxml 按块级元素提取文本
[2/5] 初始化引擎   → 连接 LLM API，从磁盘加载缓存
[3/5] 执行纠错     → 去重 → 预过滤 → 缓存查询 →
                    动态分批 → 线程池并发 API 调用 →
                    将结果应用到所有引用该文本的段
[4/5] 生成报告     → 纠错对照表 + 统计摘要
[5/5] 生成 EPUB    → 在 HTML 树上应用修正 → 重新打包
```

### 各阶段详解

**1. EPUB 解析**：使用 `ebooklib` 读取 EPUB，通过 `lxml.html` 解析 HTML 文档。按块级元素（`<p>`、`<div>`、`<h1>`–`<h6>`、`<li>` 等）提取完整文本内容。使用 `text_content()` 方法获取元素的所有后代文本拼接，避免内联标签（`<span>`、`<em>`、`<a>` 等）导致的文本碎片化。

**2. 纠错提示词**：LLM 被明确指示：
- **仅纠正** OCR 导致的单个汉字错误（形近字、音近字、偏旁部首错误）
- **严禁**改写句子、替换词语、增删内容、修改标点、改动专有名词
- 不确定时**宁可不改**，逐字比对保留原样
- 无错误则原样返回

**3. 批量 + 并发管线**（核心速度优化）：
- 预过滤：跳过无中文内容和过短的文本段
- 去重：通过 `OrderedDict` 对相同文本只处理一次
- 缓存查询：MD5 哈希匹配，命中直接使用
- 动态分批：按 `--batch-size` 分组，同时保证每批总字符数不超过限制
- 线程池并发：通过 `ThreadPoolExecutor` 同时处理多个批次（`--workers`）
- 每批仅需 1 次 API 调用，包内文本以 `[N]` 编号标识

**4. 精确修正写入**：`apply_corrections` 不再使用原始字符串 `find()` 方法（旧方法因 HTML 实体如 `&amp;` 导致匹配失败），而是：
- 用 `lxml.html` 重新解析 HTML
- 按 `element_index` 定位块级元素，匹配其 `text_content()`
- 用 `difflib` 计算字符级差异，提取 (错误文本, 正确文本) 替换对
- 递归遍历元素树，在每个文本节点上执行 `str.replace()`
- `html.tostring()` 序列化时自动处理 HTML 实体编解码

**5. EPUB 打包**：将修改后的 HTML 文件写回以原 EPUB 为模板的新 ZIP 压缩包，保留所有非文本资源（图片、样式表、字体等）。

---

## 性能优化

以一本 73 万字的 EPUB 为例：

| 优化手段 | 优化前 | 优化后 |
|----------|--------|--------|
| **预过滤** | — | 自动跳过无中文 / 过短文段 |
| **去重** | 4790 次调用 | 4289 条独特文本 |
| **批量**（batch-size=10） | 4289 次调用 | 约 429 次调用 |
| **并发**（workers=3） | 约 429 次串行 | 约 143 批并行 |
| **缓存** | 中断后全部重跑 | 中断后可断点续传 |

预估速度提升：**10 倍以上**（相比朴素串行逐条调用）。

---

## 环境变量配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `EBOOK_API_KEY` | — | API 密钥（替代 `--api-key`） |
| `OPENAI_API_KEY` | — | API 密钥（兜底） |
| `EBOOK_CORRECTOR_MODEL` | `deepseek-v4-flash` | 模型名称 |
| `EBOOK_CORRECTOR_API_BASE` | `https://api.deepseek.com` | API 地址 |
| `EBOOK_CORRECTOR_BATCH_SIZE` | `10` | 每批文本数 |
| `EBOOK_CORRECTOR_WORKERS` | `3` | 并发线程数 |
| `EBOOK_CORRECTOR_CHUNK_SIZE` | `2000` | 文本分块大小（字符） |
| `EBOOK_CORRECTOR_MAX_TOKENS` | `4096` | 最大输出 Token 数 |
| `EBOOK_CORRECTOR_TEMPERATURE` | `0.1` | 模型温度 |
| `EBOOK_CORRECTOR_CACHE_DIR` | `.corrector_cache` | 缓存目录 |
| `EBOOK_CORRECTOR_REQUEST_INTERVAL` | `0.1` | 请求限流间隔（秒） |
| `EBOOK_CORRECTOR_MAX_RETRIES` | `3` | API 最大重试次数 |

---

## 项目结构

```
ebook-ocr-corrector/
├── main.py                              # CLI 入口（参数解析、进度显示、5 阶段编排）
├── requirements.txt                     # 依赖管理
├── .corrector_cache/                    # 缓存目录（自动创建）
├── docs/                                # 文档目录
│   ├── README_en.md                     # 英文文档
│   └── README_zh.md                     # 中文文档
├── ebook_corrector/                     # 核心模块包
│   ├── __init__.py                      # 包初始化
│   ├── config.py                        # 全局配置、LLM 提示词、纠错规则
│   ├── epub_handler.py                  # EPUB I/O：解析、修正写入、保存
│   ├── llm_corrector.py                 # LLM API 客户端（单条 + 批量纠错）
│   ├── cache_manager.py                 # 文本哈希缓存持久化
│   ├── text_processor.py                # 处理管线：去重、预过滤、分批、并发
│   └── report_generator.py              # 纠错对照表和摘要报告生成
```

### 模块说明

| 模块 | 职责 | 核心类 |
|------|------|--------|
| `config.py` | 默认配置、系统提示词、纠错规则、中文字符正则 | — |
| `epub_handler.py` | EPUB 文件解析、lxml 树遍历修正、EPUB 打包 | `EpubHandler`, `TextSegment`, `ChapterInfo` |
| `llm_corrector.py` | OpenAI 兼容 API 客户端，含单条和批量纠错、重试机制 | `LLMCorrector` |
| `cache_manager.py` | MD5 哈希文本缓存，JSON 持久化 | `CacheManager` |
| `text_processor.py` | 处理管线编排，线程安全的结果收集和进度回调 | `TextProcessor` |
| `report_generator.py` | Markdown 格式报告生成 | `ReportGenerator` |
| `main.py` | CLI 入口，参数解析，5 阶段流程执行 | — |

---

## 使用示例

```bash
# 1. 先分析 EPUB 结构
python main.py "我的电子书.epub" --dry-run

# 示例输出：
# [1/5] 解析EPUB文件...
#     章节数：96 | 文档数：97 | 文本段：4940 | 总字符：745,954

# 2. 使用 DeepSeek（默认）进行纠错
python main.py "我的电子书.epub" --api-key sk-xxxxx

# 3. 使用 OpenAI GPT-4 进行纠错
python main.py "我的电子书.epub" --api-key sk-xxxxx --model gpt-4 --api-base https://api.openai.com/v1

# 4. 激进加速模式（API 额度充足时）
python main.py "我的电子书.epub" --api-key sk-xxxxx --batch-size 20 --workers 5

# 5. 保守模式（API 不稳定时）
python main.py "我的电子书.epub" --api-key sk-xxxxx --batch-size 5 --workers 1 --temperature 0.05
```

---

## 依赖

- Python 3.8+
- [ebooklib](https://github.com/aerkalov/ebooklib) — EPUB 文件读写
- [openai](https://pypi.org/project/openai/) — LLM API 客户端
- [lxml](https://lxml.de/) — HTML/XML 解析与树操作
- [tqdm](https://tqdm.github.io/) — 进度条（可选）

---

## 后续规划

- PyQt5 图形界面（当前模块化设计已预留接口）
- 支持更多电子书格式（PDF、DJVU）
- 支持本地模型（通过 llama.cpp / ollama）
- 自定义纠错规则配置文件
- 纠错前后对比查看器

---

## 许可证

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
