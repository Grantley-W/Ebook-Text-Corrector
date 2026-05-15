"""
EPUB处理模块 - 负责EPUB文件的解析、文本提取和重建

核心设计：
  - 解析阶段：用 lxml 提取每个段落元素的完整 text_content()，而非散落的 text()
             避免了 HTML 内联标签导致的文本碎片化
  - 应用阶段：用 lxml 重新解析 HTML，遍历树找到匹配的元素，直接修改 lxml 节点
             避免了 raw string find 因 HTML 实体(如 &amp;)导致匹配失败
"""

import os
import re
import tempfile
import zipfile
import shutil
import difflib
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from lxml import etree, html
import ebooklib
from ebooklib import epub

BLOCK_XPATH = (
    '//body//p | //body//div | //body//h1 | //body//h2 | //body//h3 '
    '| //body//h4 | //body//h5 | //body//h6 '
    '| //body//li | //body//td | //body//th | //body//blockquote '
    '| //body//dt | //body//dd | //body//figcaption | //body//pre'
)

SKIP_TAGS = {'script', 'style', 'head', 'title', 'meta', 'link', 'br', 'hr', 'img'}


@dataclass
class TextSegment:
    """文本段 - 记录文本及其在文档中的位置信息"""
    file_name: str
    segment_id: int
    element_index: int
    element_tag: str
    original_text: str
    corrected_text: Optional[str] = None
    is_modified: bool = False


@dataclass
class ChapterInfo:
    """章节信息"""
    file_name: str
    title: str
    segments: List[TextSegment] = field(default_factory=list)


BLOCK_TAGS_SET = {
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'li', 'td', 'th', 'blockquote', 'dt', 'dd', 'figcaption', 'pre',
    'section', 'article', 'header', 'footer', 'nav', 'aside', 'main',
    'hr', 'br', 'table', 'tr', 'ul', 'ol', 'dl',
}


class EpubHandler:
    """EPUB文件处理器 - 解析、提取文本、重建EPUB"""

    def __init__(self, epub_path: str):
        self.epub_path = epub_path
        self.book: Optional[epub.EpubBook] = None
        self.chapters: List[ChapterInfo] = []
        self._original_html: Dict[str, bytes] = {}
        self._spine_order: List[str] = []
        self._text_segments: List[TextSegment] = []
        self._total_chars: int = 0
        self._segment_counter: int = 0

    def parse(self) -> List[TextSegment]:
        """
        解析EPUB文件，按块级元素提取完整的文本内容

        使用 element.text_content() 获取元素内所有后代文本的拼接，
        避免内联标签（<span>, <em>, <a> 等）导致的文本碎片化。

        Returns:
            List[TextSegment]: 提取的文本段列表
        """
        self.book = epub.read_epub(self.epub_path)
        self._spine_order = []
        self._text_segments = []
        self._total_chars = 0
        self.chapters = []
        self._segment_counter = 0

        linear_items = []
        for item in self.book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            linear_items.append(item)

        spine_ids = set()
        if hasattr(self.book, 'spine') and self.book.spine:
            for spine_ref in self.book.spine:
                if hasattr(spine_ref, 'idref'):
                    spine_ids.add(spine_ref.idref)
                elif isinstance(spine_ref, tuple):
                    spine_ids.add(spine_ref[0])

        sorted_items = []
        if spine_ids:
            for item in linear_items:
                item_id = getattr(item, 'id', None) or getattr(item, 'file_name', None)
                if item_id in spine_ids:
                    sorted_items.append(item)
            remaining = [item for item in linear_items
                         if (getattr(item, 'id', None) or getattr(item, 'file_name', None)) not in spine_ids]
            sorted_items.extend(remaining)
        else:
            sorted_items = linear_items

        for item in sorted_items:
            item_id = getattr(item, 'id', None) or getattr(item, 'file_name', '')
            file_name = getattr(item, 'file_name', item_id)
            content = item.get_content()
            self._original_html[file_name] = content
            self._spine_order.append(file_name)

            try:
                tree = html.fromstring(content)
            except Exception:
                self._extract_text_simple(file_name, content)
                continue

            title = self._extract_title(tree)
            chapter = ChapterInfo(file_name=file_name, title=title)

            block_elements = tree.xpath(BLOCK_XPATH)

            for el_idx, el in enumerate(block_elements):
                if el.tag in SKIP_TAGS:
                    continue

                text = el.text_content().strip()
                if not text or len(text) < 2:
                    continue

                self._segment_counter += 1
                segment = TextSegment(
                    file_name=file_name,
                    segment_id=self._segment_counter,
                    element_index=el_idx,
                    element_tag=el.tag,
                    original_text=text,
                )
                chapter.segments.append(segment)
                self._text_segments.append(segment)
                self._total_chars += len(text)

            if chapter.segments:
                self.chapters.append(chapter)

        return self._text_segments

    def _extract_title(self, tree) -> str:
        """从HTML树中提取标题"""
        for tag in ("h1", "h2", "h3", "title"):
            elements = tree.xpath(f"//{tag}")
            if elements and elements[0].text:
                return elements[0].text.strip()
        return "未命名章节"

    def _extract_text_simple(self, file_name: str, content: bytes):
        """简单的文本提取（当HTML解析失败时）"""
        try:
            text = content.decode("utf-8", errors="ignore")
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) >= 2:
                self._segment_counter += 1
                segment = TextSegment(
                    file_name=file_name,
                    segment_id=self._segment_counter,
                    element_index=0,
                    element_tag="body",
                    original_text=text,
                )
                self._text_segments.append(segment)
                self._total_chars += len(text)
        except Exception:
            pass

    def apply_corrections(self, segments: List[TextSegment]) -> None:
        """
        将纠正后的文本应用到EPUB文档（基于lxml树遍历）

        核心改进（v2.0）：
          - 不再使用 raw string find()（旧方法因 HTML 实体 &amp; 等导致匹配失败）
          - 改为重新解析 HTML，遍历块级元素，匹配 text_content()
          - 对匹配到的元素，用 difflib 做字符级 diff 并精确修改 lxml 文本节点
          - 通过 lxml 序列化自动保持 HTML 实体编码一致性

        Args:
            segments: 包含纠正后文本的文本段列表
        """
        corrections_by_file: Dict[str, List[TextSegment]] = {}
        for seg in segments:
            if seg.is_modified and seg.corrected_text is not None:
                corrections_by_file.setdefault(seg.file_name, []).append(seg)

        total_corrections = sum(len(v) for v in corrections_by_file.values())
        applied_count = 0

        for file_name, file_segments in corrections_by_file.items():
            if file_name not in self._original_html:
                continue

            content_bytes = self._original_html[file_name]
            try:
                tree = html.fromstring(content_bytes)
            except Exception:
                print(f"    警告：{file_name} 重新解析失败，跳过 {len(file_segments)} 条修正")
                continue

            block_elements = tree.xpath(BLOCK_XPATH)

            for seg in file_segments:
                if seg.original_text == seg.corrected_text:
                    applied_count += 1
                    continue

                if seg.element_index >= len(block_elements):
                    continue

                el = block_elements[seg.element_index]
                current_text = el.text_content().strip()

                if current_text != seg.original_text:
                    continue

                self._apply_text_diff_to_element(el, seg.original_text, seg.corrected_text)
                applied_count += 1

            result_html = html.tostring(
                tree, encoding="unicode"
            )
            self._original_html[file_name] = result_html.encode("utf-8")

        if applied_count < total_corrections:
            missed = total_corrections - applied_count
            print(f"    [注意] {missed}/{total_corrections} 条修正未能精确应用到HTML（"
                  f"可能因文本段在文档中不唯一或已被其他修正覆盖）")

    def _apply_text_diff_to_element(
        self, element, original: str, corrected: str
    ):
        """
        在 lxml 元素内应用字符级修正

        策略：
          1. 用 difflib 比较 original 与 corrected，找出所有 -> (错误, 正确) 替换对
          2. 遍历元素树中每个文本节点 (.text / .tail)
          3. 对每个节点逐一执行 str.replace(wrong, right)
          4. 跳过嵌套块级子元素的内部（它们属于其他 TextSegment）

        这种 str.replace 方式不存在"多操作互相覆盖"的问题，
        因为同一个节点上的所有替换是在最终构建 new_text 时一次性完成的。

        Args:
            element: lxml 元素
            original: 原始文本（必须 == element.text_content()）
            corrected: 纠正后文本
        """
        if original == corrected:
            return

        matcher = difflib.SequenceMatcher(None, original, corrected)
        replacements: Dict[str, str] = {}
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'replace':
                wrong = original[i1:i2]
                right = corrected[j1:j2]
                if wrong and right and wrong != right:
                    replacements[wrong] = right

        if not replacements:
            return

        # 直接遍历元素树，对每个文本节点执行替换
        # 跳过嵌套块级元素内部（避免越界修改其他段落的内容）
        self._apply_replacements_to_tree(element, replacements)

    def _apply_replacements_to_tree(self, element, replacements: Dict[str, str]):
        """
        递归遍历元素树，对每个文本节点执行 str.replace
        跳过嵌套块级子元素的内部
        """
        if element.text:
            for wrong, right in replacements.items():
                element.text = element.text.replace(wrong, right)

        for child in element:
            if child.tag in BLOCK_TAGS_SET:
                continue
            self._apply_replacements_to_tree(child, replacements)

        if element.tail:
            for wrong, right in replacements.items():
                element.tail = element.tail.replace(wrong, right)

    def save(self, output_path: str) -> str:
        """
        保存纠正后的EPUB文件

        Args:
            output_path: 输出文件路径

        Returns:
            str: 输出文件路径
        """
        temp_dir = tempfile.mkdtemp(prefix="epub_correct_")

        try:
            with zipfile.ZipFile(self.epub_path, "r") as zin:
                zin.extractall(temp_dir)

            for file_name, corrected_content in self._original_html.items():
                file_path = os.path.join(temp_dir, file_name)

                if os.path.exists(file_path):
                    with open(file_path, "wb") as f:
                        f.write(corrected_content)
                else:
                    for root, dirs, files in os.walk(temp_dir):
                        for fname in files:
                            if fname == os.path.basename(file_name):
                                alt_path = os.path.join(root, fname)
                                with open(alt_path, "wb") as f:
                                    f.write(corrected_content)
                                break

            if os.path.exists(output_path):
                os.remove(output_path)

            with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zout:
                mimetype_path = os.path.join(temp_dir, "mimetype")
                if os.path.exists(mimetype_path):
                    zout.write(mimetype_path, "mimetype", zipfile.ZIP_STORED)

                for root, dirs, files in os.walk(temp_dir):
                    for fname in files:
                        if fname == "mimetype":
                            continue
                        file_path = os.path.join(root, fname)
                        arcname = os.path.relpath(file_path, temp_dir)
                        arcname = arcname.replace("\\", "/")
                        zout.write(file_path, arcname, zipfile.ZIP_DEFLATED)

            if not output_path.endswith(".epub"):
                final_path = output_path + ".epub"
                os.rename(output_path, final_path)
                output_path = final_path

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return output_path

    def get_statistics(self) -> Dict:
        """获取EPUB文件统计信息"""
        return {
            "total_chars": self._total_chars,
            "total_segments": len(self._text_segments),
            "total_chapters": len(self.chapters),
            "spine_items": len(self._spine_order),
        }

    def get_total_chars(self) -> int:
        return self._total_chars
