"""OCR 引擎：RapidOCR（ONNX 本地推理，模型随包内置）。

惰性加载：未安装依赖或加载失败时 available=False，管线自动跳过扫描页 OCR。
"""
from __future__ import annotations

import io
import logging
import threading

log = logging.getLogger(__name__)

_MIN_SCORE = 0.45


class OcrEngine:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._engine = None
        self._init_lock = threading.Lock()
        self._init_tried = False

    @property
    def available(self) -> bool:
        if not self.enabled:
            return False
        if not self._init_tried:
            with self._init_lock:
                if not self._init_tried:
                    self._init_tried = True
                    try:
                        from rapidocr_onnxruntime import RapidOCR  # 延迟重依赖

                        self._engine = RapidOCR()
                        log.info("RapidOCR 已加载")
                    except Exception as e:  # 未安装或环境不支持
                        log.warning("OCR 不可用（将跳过扫描页）: %s", e)
        return self._engine is not None

    def image_png(self, png_bytes: bytes) -> str:
        """识别 PNG 位图，返回按行拼接的文本。"""
        if not self.available:
            return ""
        try:
            import numpy as np

            result, _ = self._engine(io.BytesIO(png_bytes).read())
            if not result:
                return ""
            lines = [
                text
                for _box, text, score in result
                if float(score) >= _MIN_SCORE and text.strip()
            ]
            _ = np  # numpy 由 rapidocr 依赖带入，保持显式引用避免误删
            return "\n".join(lines)
        except Exception as e:
            log.warning("OCR 单页识别失败: %s", e)
            return ""
