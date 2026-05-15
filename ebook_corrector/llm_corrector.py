"""
LLM纠错模块 - 调用大语言模型进行文本纠错

功能：
- 支持单条和批量两种纠错模式
- 批量模式一次 API 调用处理多条文本（核心速度优化）
- 自动重试和指数退避机制
"""

import time
import re
import difflib
from typing import Optional, List, Tuple, Dict
from openai import OpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError

from .config import (
    DEFAULT_MODEL,
    DEFAULT_API_BASE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    CORRECTION_PROMPT,
    BATCH_CORRECTION_PROMPT,
    SYSTEM_ROLE,
    DEFAULT_REQUEST_INTERVAL,
    MAX_RETRIES,
    RETRY_BACKOFF,
)


class LLMCorrector:
    """大语言模型文本纠错器"""

    def __init__(
        self,
        api_key: str,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        self.api_key = api_key
        self.api_base = api_base or DEFAULT_API_BASE
        self.model = model or DEFAULT_MODEL
        self.temperature = temperature if temperature is not None else DEFAULT_TEMPERATURE
        self.max_tokens = max_tokens or DEFAULT_MAX_TOKENS
        self._last_request_time: float = 0
        self._client: Optional[OpenAI] = None
        self._init_client()

    def _init_client(self):
        """初始化OpenAI客户端"""
        try:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
                timeout=120.0,
                max_retries=0,
            )
        except Exception as e:
            raise ConnectionError(f"无法初始化API客户端：{e}")

    def _rate_limit_wait(self):
        """请求限流等待"""
        elapsed = time.time() - self._last_request_time
        if elapsed < DEFAULT_REQUEST_INTERVAL:
            time.sleep(DEFAULT_REQUEST_INTERVAL - elapsed)
        self._last_request_time = time.time()

    def _call_api(self, messages: List[Dict]) -> str:
        """统一的 API 调用入口，含重试逻辑"""
        last_exception = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                self._rate_limit_wait()

                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                )

                content = response.choices[0].message.content
                return content.strip() if content else ""

            except RateLimitError as e:
                wait_time = RETRY_BACKOFF ** (attempt + 1)
                print(f"\nAPI限流，等待 {wait_time:.0f} 秒后重试（第 {attempt + 1}/{MAX_RETRIES} 次）...")
                time.sleep(wait_time)
                last_exception = e

            except (APIConnectionError, APITimeoutError) as e:
                wait_time = RETRY_BACKOFF ** (attempt + 1)
                print(f"\nAPI连接异常，等待 {wait_time:.0f} 秒后重试（第 {attempt + 1}/{MAX_RETRIES} 次）...")
                time.sleep(wait_time)
                last_exception = e

            except APIError as e:
                error_msg = str(e)
                if any(kw in error_msg.lower() for kw in ("context_length", "maximum context", "too long")):
                    raise ValueError(f"文本过长，超出模型上下文限制：{e}")
                wait_time = RETRY_BACKOFF ** (attempt + 1)
                print(f"\nAPI错误，等待 {wait_time:.0f} 秒后重试（第 {attempt + 1}/{MAX_RETRIES} 次）...")
                time.sleep(wait_time)
                last_exception = e

        raise ConnectionError(f"API调用失败，已重试 {MAX_RETRIES} 次：{last_exception}")

    def correct_text(self, text: str) -> Tuple[str, List[Tuple[str, str]]]:
        """
        单条文本纠错

        Args:
            text: 待纠正的文本

        Returns:
            Tuple[str, List[Tuple[str, str]]]: (纠正后文本, 纠错对列表)
        """
        if not self._client:
            raise ConnectionError("API客户端未初始化")

        prompt = f"{CORRECTION_PROMPT}{text}"
        corrected_text = self._call_api([
            {"role": "system", "content": SYSTEM_ROLE},
            {"role": "user", "content": prompt},
        ])

        if not corrected_text:
            corrected_text = text

        correction_pairs = self._extract_corrections(text, corrected_text)
        return corrected_text, correction_pairs

    def correct_batch(
        self, texts: List[str]
    ) -> List[Tuple[str, List[Tuple[str, str]]]]:
        """
        批量文本纠错（核心速度优化）
        一次 API 调用处理多条文本

        Args:
            texts: 待纠正的文本列表

        Returns:
            List[Tuple[str, List[Tuple[str, str]]]]: 每条文本的 (纠正后文本, 纠错对列表)
        """
        if not self._client:
            raise ConnectionError("API客户端未初始化")
        if not texts:
            return []

        segments_block = "\n\n".join(
            f"[{i}] {t}" for i, t in enumerate(texts)
        )

        prompt = BATCH_CORRECTION_PROMPT.format(
            count=len(texts),
            segments=segments_block,
        )

        response_text = self._call_api([
            {"role": "system", "content": SYSTEM_ROLE},
            {"role": "user", "content": prompt},
        ])

        results = self._parse_batch_response(texts, response_text)
        return results

    def _parse_batch_response(
        self, originals: List[str], response: str
    ) -> List[Tuple[str, List[Tuple[str, str]]]]:
        """
        解析批量 API 返回结果

        API 应返回格式：
        [0] 纠正后文本0
        [1] 纠正后文本1
        ...

        Args:
            originals: 原始文本列表
            response: LLM 返回的原始响应

        Returns:
            List[Tuple[str, List]]: 每条文本的纠正结果
        """
        parsed: Dict[int, str] = {}

        pattern = re.compile(r'\[(\d+)\]\s*(.*?)(?=\[\d+\]|\Z)', re.DOTALL)
        for match in pattern.finditer(response):
            idx = int(match.group(1))
            text = match.group(2).strip()
            if idx < len(originals):
                parsed[idx] = text

        results = []
        for i, original in enumerate(originals):
            if i in parsed and parsed[i]:
                corrected = parsed[i]
                # 移除 LLM 可能附带的编号前缀
                corrected = re.sub(rf'^\[{i}\]\s*', '', corrected).strip()
            else:
                corrected = original

            correction_pairs = self._extract_corrections(original, corrected)
            results.append((corrected, correction_pairs))

        return results

    def _extract_corrections(
        self, original: str, corrected: str
    ) -> List[Tuple[str, str]]:
        """
        使用difflib比较原始文本和纠正后文本，提取纠错对照

        Args:
            original: 原始文本
            corrected: 纠正后文本

        Returns:
            List[Tuple[str, str]]: 纠错对列表 [(错误文本, 正确文本), ...]
        """
        if original == corrected:
            return []

        matcher = difflib.SequenceMatcher(None, original, corrected)
        correction_pairs: List[Tuple[str, str]] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "replace":
                wrong = original[i1:i2]
                right = corrected[j1:j2]
                if wrong and right and wrong != right:
                    correction_pairs.append((wrong, right))
            elif tag == "delete":
                wrong = original[i1:i2]
                if wrong:
                    correction_pairs.append((wrong, ""))
            elif tag == "insert":
                right = corrected[j1:j2]
                if right:
                    correction_pairs.append(("", right))

        return correction_pairs

    def batch_correct(
        self, texts: List[str], progress_callback=None
    ) -> List[Tuple[str, List[Tuple[str, str]]]]:
        """
        批量纠正文本（兼容旧接口，逐条处理）

        Args:
            texts: 待纠正的文本列表
            progress_callback: 进度回调函数 callback(current, total)

        Returns:
            List[Tuple[str, List[Tuple[str, str]]]]: 纠正结果列表
        """
        results = []
        total = len(texts)

        for idx, text in enumerate(texts):
            result = self.correct_text(text)
            results.append(result)

            if progress_callback:
                progress_callback(idx + 1, total)

        return results
