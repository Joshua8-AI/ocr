"""Tesseract OCR engine for local processing without vLLM."""
import time

import pytesseract
from PIL import Image

from app.ocr.engine import OcrResult


def ocr_image_tesseract(image: Image.Image) -> OcrResult:
    """Run Tesseract OCR on a PIL Image. Returns OcrResult with text (no token counts)."""
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    text = pytesseract.image_to_string(image, lang="eng")
    return OcrResult(text=text.strip(), prompt_tokens=0, completion_tokens=0)
