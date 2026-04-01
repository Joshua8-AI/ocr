from dataclasses import dataclass, field

import httpx
import tenacity

from app.config import settings


@dataclass
class OcrResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=2, min=2, max=30),
    retry=tenacity.retry_if_exception_type((httpx.ConnectError, httpx.HTTPStatusError)),
    before_sleep=tenacity.before_sleep_log(None, 20),
)
def ocr_image(
    image_base64: str,
    model_name: str,
    vllm_url: str,
    is_native_ocr: bool = False,
    text_prompt: str = "",
) -> OcrResult:
    """Send a base64-encoded image to vLLM for OCR. Returns text + token usage.

    is_native_ocr: if True, skip system prompt (model is a dedicated OCR model).
    text_prompt: if set, use this as the user text content alongside the image
                 (e.g. "Text Recognition:" for GLM-OCR).
    """
    OCR_SYSTEM_PROMPT = (
        "You are a strict OCR engine. Extract all visible text from the image exactly as it appears. "
        "Preserve the original layout, formatting, and structure. Output only the text found in the image. "
        "Do not add any interpretation, analysis, commentary, summaries, or insights. "
        "Do not add emoji. Do not describe what the image shows. "
        "For tables and charts, extract the data values as a markdown table. "
        "For diagrams, extract only the text labels and annotations that appear in the image. "
        "If no text is visible, output only: [no text detected]"
    )

    image_content = {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{image_base64}"},
    }

    if text_prompt:
        # Model-specific fixed prompt (e.g. GLM-OCR "Text Recognition:")
        messages = [
            {
                "role": "user",
                "content": [
                    image_content,
                    {"type": "text", "text": text_prompt},
                ],
            }
        ]
    elif is_native_ocr:
        # Native OCR model — bare image, no prompt
        messages = [
            {
                "role": "user",
                "content": [image_content],
            }
        ]
    else:
        # General VLM — system prompt + instruction
        messages = [
            {
                "role": "system",
                "content": OCR_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    image_content,
                    {"type": "text", "text": "Extract all text from this image exactly as written."},
                ],
            }
        ]

    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": settings.ocr_max_tokens,
        "temperature": 0.1,
    }

    with httpx.Client(timeout=settings.ocr_timeout_seconds) as client:
        resp = client.post(
            f"{vllm_url}/chat/completions",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    usage = data.get("usage", {})
    return OcrResult(
        text=data["choices"][0]["message"]["content"],
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
    )
