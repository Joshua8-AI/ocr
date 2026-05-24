from dataclasses import dataclass, field

import httpx
import tenacity

from app.config import settings


@dataclass
class OcrResult:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _html_to_markdown(html: str) -> str:
    """Convert a model's HTML output (e.g. Chandra) to markdown. Drops layout
    attributes like data-bbox/data-label, keeps tables/headings/emphasis, and
    preserves math LaTeX in two forms:

    1. <math> KaTeX tags (Chandra) -> $...$/$$...$$ via the convert_math override.
       Plain markdownify strips these tags, so every equation was lost — on
       olmOCR-bench that pinned Chandra at 0.0% for arxiv_math/old_scans_math
       despite 75-100% elsewhere.
    2. $...$ / $$...$$ markdown LaTeX (Nanonets-OCR2) -> protected from escaping.
       markdownify escapes _ ^ * inside text, turning $x_i^2$ into $x\\_i^2$ and
       breaking math rendering / the bench equation matcher (Nanonets old_scans_math
       was 3.9%). We stash $-spans behind alphanumeric sentinels before conversion
       and restore them after; only spans with a LaTeX-ish char are protected, so
       plain currency like $5.00 is left alone.
    """
    import re

    from markdownify import MarkdownConverter

    stash: list[str] = []

    def _protect(match: "re.Match") -> str:
        body = match.group(0)
        if not re.search(r"[\\^_{}]", body):
            return body  # not math (e.g. currency) — leave for normal handling
        stash.append(body)
        return f"zZmathZz{len(stash) - 1}zZendZz"

    html = re.sub(r"\$\$.+?\$\$", _protect, html, flags=re.DOTALL)
    html = re.sub(r"\$(?!\$)[^$\n]+?\$", _protect, html)

    class _MathMarkdownConverter(MarkdownConverter):
        def convert_math(self, el, text, *args, **kwargs):
            # Use the raw element text, not `text`: markdownify has already escaped
            # the latter (\_, \^, \* ...), which would corrupt the LaTeX.
            latex = el.get_text().strip()
            if not latex:
                return ""
            display = el.get("display")
            is_block = display is not None and display.lower() != "inline"
            return f"\n\n$$\n{latex}\n$$\n\n" if is_block else f"${latex}$"

    md = _MathMarkdownConverter(heading_style="ATX").convert(html).strip()
    for i, span in enumerate(stash):
        md = md.replace(f"zZmathZz{i}zZendZz", span)
    return md


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
    sampling: dict | None = None,
    post: str = "",
    system_prompt: str = "",
    user_prompt: str = "",
) -> OcrResult:
    """Send a base64-encoded image to vLLM for OCR. Returns text + token usage.

    is_native_ocr: if True, skip system prompt (model is a dedicated OCR model).
    text_prompt: if set, use this as the user text content alongside the image
                 (e.g. "Text Recognition:" for GLM-OCR).
    sampling: optional per-model overrides for temperature/top_p/max_tokens, plus
              an "extra_body" dict merged into the request (e.g. Chandra needs
              temperature=0, top_p=0.1 and chat_template_kwargs.enable_thinking=False).
    post: if "html2md", convert HTML output to markdown (Chandra emits HTML).
    system_prompt: general-VLM system prompt override (defaults to OCR_SYSTEM_PROMPT).
    user_prompt: general-VLM user instruction override (defaults to the generic
                 "Extract all text..." line). Used for the tuned Qwen3.6-35B prompt.
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
        # General VLM — system prompt + instruction (per-model overridable)
        messages = [
            {
                "role": "system",
                "content": system_prompt or OCR_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": [
                    image_content,
                    {"type": "text", "text": user_prompt or "Extract all text from this image exactly as written."},
                ],
            }
        ]

    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": settings.ocr_max_tokens,
        "temperature": 0.1,
    }
    if sampling:
        for k in ("temperature", "top_p", "max_tokens"):
            if k in sampling:
                payload[k] = sampling[k]
        payload.update(sampling.get("extra_body", {}))

    with httpx.Client(timeout=settings.ocr_timeout_seconds) as client:
        resp = client.post(
            f"{vllm_url}/chat/completions",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    content = data["choices"][0]["message"]["content"] or ""
    if post == "html2md":
        content = _html_to_markdown(content)

    usage = data.get("usage", {})
    return OcrResult(
        text=content,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
    )
