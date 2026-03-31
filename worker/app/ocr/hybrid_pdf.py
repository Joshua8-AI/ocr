import re

import fitz  # PyMuPDF
from PIL import Image

from app.ocr.engine import OcrResult, ocr_image
from app.ocr.image_prep import prepare_image

RENDER_DPI = 200
RENDER_SCALE = RENDER_DPI / 72.0
TEXT_THRESHOLD = 50


def _has_word_structure(text: str) -> bool:
    words = text.split()
    if len(words) < 3:
        return False
    alpha_words = sum(1 for w in words if re.search(r"[a-zA-Z0-9]", w))
    return alpha_words / len(words) > 0.4


def classify_page(page: fitz.Page) -> str:
    text = page.get_text("text").strip()
    images = page.get_images(full=True)
    has_text = len(text) > TEXT_THRESHOLD and _has_word_structure(text)
    has_images = bool(images)
    if has_text and not has_images:
        return "text"
    if not has_text:
        return "image"
    return "mixed"


def render_page_to_image(page: fitz.Page) -> Image.Image:
    mat = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
    pix = page.get_pixmap(matrix=mat)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def process_pdf(
    filepath: str,
    model_name: str,
    vllm_url: str,
    is_native_ocr: bool = False,
    progress_callback=None,
) -> tuple[list[str], int, int]:
    """Process a PDF with hybrid text extraction + OCR.

    Returns (page_texts, total_prompt_tokens, total_completion_tokens).
    """
    doc = fitz.open(filepath)
    total_pages = len(doc)
    page_texts = []
    total_prompt = 0
    total_completion = 0

    for page_num in range(total_pages):
        page = doc[page_num]
        page_type = classify_page(page)

        if page_type == "text":
            text = page.get_text("text").strip()
        else:
            img = render_page_to_image(page)
            img_b64 = prepare_image(img)
            result = ocr_image(img_b64, model_name, vllm_url, is_native_ocr)
            text = result.text
            total_prompt += result.prompt_tokens
            total_completion += result.completion_tokens

        page_texts.append(text)

        if progress_callback:
            progress_callback(page_num + 1, total_pages)

    doc.close()
    return page_texts, total_prompt, total_completion
