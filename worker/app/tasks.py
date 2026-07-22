import json
import logging
import os
import shutil
import time

import redis

from app.celery_app import celery
from app.config import settings
from app.converters.input_converter import convert_to_images
from app.converters.output_converter import (
    save_docx,
    save_html,
    save_markdown,
    save_plaintext,
    save_searchable_pdf,
)
from app.ocr.engine import ocr_image
from app.ocr.hybrid_pdf import process_pdf
from app.ocr.image_prep import prepare_image
from app.ocr.tesseract import ocr_image_tesseract

logger = logging.getLogger(__name__)

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

UPLOAD_DIR = os.path.join(settings.data_dir, "uploads")
RESULT_DIR = os.path.join(settings.data_dir, "results")

# Each value is the model id sent in the API request. All of these route
# through the LiteLLM gateway (ultra7:8000) and are served under the litellm
# names below.
MODEL_HF_IDS = {
    "LightOnOCR-2-1B": "lightonocr:1b",
    "GLM-OCR": "glm-ocr:1b",
    "OlmOCR2": "olmocr2:7b",
    "Qwen35-9B": "qwen3.5:9b",
    "Qwen35-9B-FS": "qwen3.5:9b",  # 9B + Docling furniture-strip (see FURNITURE_STRIP_MODELS)
    "Qwen35-122B": "qwen3.5-122b",
    "Qwen35-122B-FS": "qwen3.5-122b",  # 122B + Docling furniture-strip (see FURNITURE_STRIP_MODELS)
    "Qwen3.6-35B": "qwen3.6-35b",
    # Same backend model as Qwen3.6-35B; post-processed to strip page furniture
    # (running headers/footers) via Docling layout detection. See FURNITURE_STRIP_MODELS.
    "Qwen3.6-35B-FS": "qwen3.6-35b",
    "Gemma4-12B": "gemma4:12b",
    "Gemma4-26B": "gemma4:26b",
    "Gemma4-31B": "gemma4:31b",
    "Gemma4-E4B": "gemma4:e4b",
    "Chandra": "chandra:5b",
    "DeepSeek-OCR": "deepseek-ocr:3b",
    "dots-ocr": "dots-ocr:2b",
    "Nanonets-OCR2": "nanonets-ocr2:3b",
    "PaddleOCR-VL": "paddleocr-vl:1b",
    "OvisOCR2": "ovisocr2:0.8b",
}

NATIVE_OCR_MODELS = {"LightOnOCR-2-1B", "OlmOCR2"}

# Chandra is a task-specific OCR fine-tune: it must get its exact training prompt,
# near-greedy sampling, image-before-text, no system prompt, thinking disabled, and
# a large token budget, or it drifts (bbox-only / garbled). It emits HTML via the
# `ocr` prompt (`ocr_layout` returns bbox-only), which we convert to markdown.
# Prompt reproduced verbatim from datalab-to/chandra chandra/prompts.py.
_CHANDRA_TAGS = ["math", "br", "i", "b", "u", "del", "sup", "sub", "table", "tr", "td", "p", "th", "div", "pre", "h1", "h2", "h3", "h4", "h5", "ul", "ol", "li", "input", "a", "span", "img", "hr", "tbody", "small", "caption", "strong", "thead", "big", "code", "chem"]
_CHANDRA_ATTRS = ["class", "colspan", "rowspan", "display", "checked", "type", "border", "value", "style", "href", "alt", "align", "data-bbox", "data-label"]
_CHANDRA_ENDING = f"""
Only use these tags {_CHANDRA_TAGS}, and these attributes {_CHANDRA_ATTRS}.

Guidelines:
* Inline math: Surround math with <math>...</math> tags. Math expressions should be rendered in KaTeX-compatible LaTeX. Use display for block math.
* Tables: Use colspan and rowspan attributes to match table structure.
* Formatting: Maintain consistent formatting with the image, including spacing, indentation, subscripts/superscripts, and special characters.
* Images: Include a description of any images in the alt attribute of an <img> tag. Do not fill out the src property. Describe in detail inside the div tag. Also convert charts to high fidelity data, and convert diagrams to mermaid.
* Forms: Mark checkboxes and radio buttons properly.
* Text: join lines together properly into paragraphs using <p>...</p> tags.  Use <br> tags for line breaks within paragraphs, but only when absolutely necessary to maintain meaning.
* Chemistry: Use <chem>...</chem> tags for chemical formulas with reactive SMILES.
* Lists: Preserve indents and proper list markers.
* Use the simplest possible HTML structure that accurately represents the content of the block.
* Make sure the text is accurate and easy for a human to read and interpret.  Reading order should be correct and natural.
""".strip()
CHANDRA_PROMPT = f"""
OCR this image to HTML.

{_CHANDRA_ENDING}
""".strip()

# DeepSeek-OCR / dots.ocr / Nanonets-OCR2 are task-specific OCR fine-tunes served
# by llama.cpp; each gets its image-first text prompt (no system prompt), verified
# on olmOCR-bench. DeepSeek emits clean markdown (no post); dots.ocr and Nanonets
# emit HTML tables (-> html2md). All run near-greedy (temp 0). NOTE: llama.cpp
# serves one request at a time — concurrent calls 500 (engine retries absorb it).
DEEPSEEK_OCR_PROMPT = "Convert the document to markdown."
DOTS_OCR_PROMPT = "Extract the text content from this image."
# PaddleOCR-VL (Baidu, ~0.9B NaViT+ERNIE) is a task-specific OCR fine-tune. Its
# documented text-recognition trigger is the bare "OCR:" prompt (no system prompt,
# image-first); it returns clean reading-order text/markdown directly, so no
# html2md post-process. Verified against the gateway (paddleocr-vl:1b) 2026-06-07.
# LIMITATION: "OCR:" is plain reading-order text only — tables come out flattened
# (no pipe structure). PaddleOCR-VL's table/formula/chart prompts ("Table
# Recognition:", etc.) are meant to run inside PaddleOCR's layout pipeline (detect
# + crop region -> per-type prompt -> parse the OTSL <fcel>/<lcel>/<nl> tokens they
# emit). We send whole pages in one call and have no OTSL parser, so those prompts
# aren't usable here. For structured tables use a markdown-table VLM (Qwen/Gemma)
# or an HTML fine-tune (dots/Nanonets -> html2md) instead.
PADDLEOCR_PROMPT = "OCR:"
# OvisOCR2 (AIDC-AI, ~0.8B) is a task-specific OCR fine-tune served by llama.cpp.
# Image-first text prompt, no system prompt. Verified against the gateway
# (ovisocr2:0.8b) 2026-07-21: the model IGNORES the prompt text — "OCR:" and
# "Convert the document to markdown." returned byte-identical output on both a
# dense-text and two table pages — so the value below is only a trigger, not a
# tuning knob. It emits body text as markdown but tables as HTML
# (`<table border=1>...`), so it needs the html2md post-process (see MODEL_POST).
# NOTE: it also emits `<img src="images/bbox_...jpg"/>` placeholders for figures,
# which html2md renders as dead `![](images/bbox_...)` links.
OVISOCR_PROMPT = "OCR:"
NANONETS_OCR_PROMPT = (
    "Extract the text from the above document as if you were reading it naturally. "
    "Return the tables in html format. Return the equations in LaTeX representation. "
    "If there is an image in the document and image caption is not present, add a small "
    "description of the image inside the <img></img> tag; otherwise, add the image caption "
    "inside <img></img>. Watermarks should be wrapped in brackets. "
    "Ex: <watermark>OFFICIAL COPY</watermark>. Page numbers should be wrapped in brackets. "
    "Ex: <page_number>14</page_number> or <page_number>9/22</page_number>. "
    "Prefer using ☐ and ☑ for check boxes."
)

# Models that use a fixed text prompt instead of system prompt
MODEL_PROMPTS = {
    "GLM-OCR": "Text Recognition:",
    "Chandra": CHANDRA_PROMPT,
    "DeepSeek-OCR": DEEPSEEK_OCR_PROMPT,
    "dots-ocr": DOTS_OCR_PROMPT,
    "Nanonets-OCR2": NANONETS_OCR_PROMPT,
    "PaddleOCR-VL": PADDLEOCR_PROMPT,
    "OvisOCR2": OVISOCR_PROMPT,
}

# Tuned general-VLM prompt for Qwen3.6-35B. With the default shared prompt the
# 35B space-aligns tables (unparseable) and pads output with page furniture. This
# prompt + temperature 0 lifted it from 56.3% to 72.9% Overall on olmOCR-bench
# (150-doc sample, 2026-05-23) — see results/prompt_iter_log.md. It is *not*
# applied to the other general-VLM models (9B/122B/Gemmas), which don't share the
# table problem and can regress with the stricter "omit furniture" wording.
QWEN36_SYSTEM_PROMPT = (
    "You are a strict OCR engine. Extract all visible text from the image exactly as it appears, "
    "in natural reading order. Output GitHub-flavored Markdown. "
    "For ANY tabular data you MUST output a Markdown table delimited with | pipe | characters and a "
    "header separator row (e.g. | --- | --- |). Never reproduce table columns using spaces or "
    "fixed-width alignment. "
    "Output only the main body content found in the image. Do NOT transcribe running headers, "
    "running footers, page numbers, or watermarks; omit this repeated page furniture entirely. "
    "Do not add interpretation, analysis, commentary, summaries, or insights. "
    "Do not add emoji. Do not describe what the image shows. "
    "For diagrams, extract only the text labels and annotations that appear in the image. "
    "If no text is visible, output only: [no text detected]"
)
QWEN36_USER_PROMPT = (
    "Extract all text from this image exactly as written. Render every table as a Markdown pipe table."
)

# Tuned prompt for Qwen35-9B AND Qwen35-122B (identical winners from per-model
# hill-climbs, olmOCR-bench 2026-05-24): Qwen35-9B 62.6->68.1%, Qwen35-122B
# 61.8->70.7% @ temp 0. Adds, vs the 35B prompt, a COMPLETENESS safeguard (the 9B
# over-omits dense text without it) and CONCRETE furniture examples + a
# standalone-page-number rule (the 9B/122B GAIN headers from these; the 35B does
# NOT, which is why it keeps its simpler QWEN36 prompt). See results/q9_iter_log.md,
# q122_iter_log.md; prompts in ocr-bench/winning_prompts/.
QWEN35_SYSTEM_PROMPT = (
    "You are a strict OCR engine. Extract all visible text from the image exactly as it appears, "
    "in natural reading order. Output GitHub-flavored Markdown. "
    "For ANY tabular data you MUST output a Markdown table delimited with | pipe | characters and a "
    "header separator row (e.g. | --- | --- |). Never reproduce table columns using spaces or "
    "fixed-width alignment. "
    "Transcribe the COMPLETE body content: every line of body text, including dense, small, faint, "
    "or footnote text, and every mathematical expression. "
    "Do NOT transcribe anything printed in the top or bottom page margins — running headers, "
    "running footers, page numbers, journal or publisher names, and DOIs or URLs printed in the "
    "margin — and never output a standalone page number. "
    "Do not add interpretation, analysis, commentary, summaries, or insights. "
    "Do not add emoji. Do not describe what the image shows. "
    "For diagrams, extract only the text labels and annotations that appear in the image. "
    "If no text is visible, output only: [no text detected]"
)

# Per-model general-VLM prompt overrides (system + user instruction). Models not
# listed fall back to engine.ocr_image's default OCR_SYSTEM_PROMPT + generic line.
MODEL_SYSTEM_PROMPTS = {
    "Qwen3.6-35B": QWEN36_SYSTEM_PROMPT,
    "Qwen3.6-35B-FS": QWEN36_SYSTEM_PROMPT,
    "Qwen35-9B": QWEN35_SYSTEM_PROMPT,
    "Qwen35-9B-FS": QWEN35_SYSTEM_PROMPT,
    "Qwen35-122B": QWEN35_SYSTEM_PROMPT,
    "Qwen35-122B-FS": QWEN35_SYSTEM_PROMPT,
}
MODEL_USER_PROMPTS = {
    "Qwen3.6-35B": QWEN36_USER_PROMPT,
    "Qwen3.6-35B-FS": QWEN36_USER_PROMPT,
    "Qwen35-9B": QWEN36_USER_PROMPT,
    "Qwen35-9B-FS": QWEN36_USER_PROMPT,
    "Qwen35-122B": QWEN36_USER_PROMPT,
    "Qwen35-122B-FS": QWEN36_USER_PROMPT,
}

# Per-model sampling / request-body overrides merged into the chat payload.
MODEL_SAMPLING = {
    "Chandra": {
        "temperature": 0.0,
        "top_p": 0.1,
        "max_tokens": 12384,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    },
    # Greedy decoding scored higher and removed the run-to-run variance seen at
    # temp 0.1 (olmOCR-bench tuning, 2026-05-23).
    "Qwen3.6-35B": {"temperature": 0.0},
    "Qwen3.6-35B-FS": {"temperature": 0.0},
    "Qwen35-9B": {"temperature": 0.0},
    "Qwen35-9B-FS": {"temperature": 0.0},
    "Qwen35-122B": {"temperature": 0.0},
    "Qwen35-122B-FS": {"temperature": 0.0},
    "DeepSeek-OCR": {"temperature": 0.0},
    "dots-ocr": {"temperature": 0.0},
    "Nanonets-OCR2": {"temperature": 0.0},
    # Deterministic OCR fine-tunes; run greedy like the other dedicated OCR models.
    "PaddleOCR-VL": {"temperature": 0.0},
    "OvisOCR2": {"temperature": 0.0},
    # Gemma4 are thinking models on the llama.cpp gateway; disable thinking or they
    # spend the whole token budget on reasoning -> empty output / gateway 500s.
    "Gemma4-12B": {"temperature": 0.0, "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    "Gemma4-26B": {"temperature": 0.0, "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    "Gemma4-31B": {"temperature": 0.0, "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    "Gemma4-E4B": {"temperature": 0.0, "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
}

# Per-model output post-processing ("html2md" => convert HTML to markdown).
MODEL_POST = {
    "Chandra": "html2md",
    "dots-ocr": "html2md",
    "Nanonets-OCR2": "html2md",
    # Emits markdown body text but HTML tables — see OVISOCR_PROMPT.
    "OvisOCR2": "html2md",
}

# Models that run locally without vLLM
LOCAL_MODELS = {"Tesseract", "Tesseract-FS"}

# Models that use Docling Serve API (not vLLM)
DOCLING_MODELS = {"Docling": False, "Docling-VLM": True}  # value = use_vlm flag

# General-VLM models whose per-page output is post-processed to strip running
# headers/footers using Docling's furniture detection (settings.docling_url).
# These OCR exactly like their base model, then drop page furniture the prompt
# can't reliably suppress. olmOCR-bench: Qwen3.6-35B 72.3% -> 80.9% Overall.
FURNITURE_STRIP_MODELS = {"Qwen3.6-35B-FS", "Qwen35-9B-FS", "Qwen35-122B-FS", "Tesseract-FS"}


def _resolve_model_id(model_name: str) -> str:
    return MODEL_HF_IDS.get(model_name, model_name)


def _update_progress(job_id: str, **kwargs) -> None:
    str_kwargs = {k: str(v) for k, v in kwargs.items()}
    redis_client.hset(f"job:{job_id}", mapping=str_kwargs)


@celery.task(
    name="app.tasks.process_ocr_job",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_ocr_job(
    self,
    job_id: str,
    file_paths: list[str],
    email: str,
    output_format: str,
    access_token: str,
    model: str = "LightOnOCR-2-1B",
    vllm_url: str = "",
    file_prefix: str = "",
) -> None:
    """Main OCR pipeline task."""
    is_local = model in LOCAL_MODELS
    is_docling = model in DOCLING_MODELS

    if not is_local and not is_docling:
        if not vllm_url:
            vllm_url = settings.vllm_url
        model_id = _resolve_model_id(model)
        is_native_ocr = model in NATIVE_OCR_MODELS
        text_prompt = MODEL_PROMPTS.get(model, "")
        sampling = MODEL_SAMPLING.get(model)
        post = MODEL_POST.get(model, "")
        system_prompt = MODEL_SYSTEM_PROMPTS.get(model, "")
        user_prompt = MODEL_USER_PROMPTS.get(model, "")

    result_dir = os.path.join(RESULT_DIR, job_id)
    os.makedirs(result_dir, exist_ok=True)

    _update_progress(job_id, status="processing", current_file=0)

    all_result_files: list[str] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    start_time = time.time()

    try:
        for file_idx, filepath in enumerate(file_paths, 1):
            filename = os.path.basename(filepath)
            ext = os.path.splitext(filename)[1].lower()
            base_name = os.path.splitext(filename)[0]

            if file_prefix:
                base_name = f"{file_prefix}_{base_name}"

            _update_progress(
                job_id,
                current_file=file_idx,
                current_filename=filename,
                current_page=0,
                total_pages=0,
            )

            if is_docling:
                page_texts, pt, ct = _process_docling(job_id, filepath, vllm_url, model)
            elif is_local:
                page_texts, pt, ct = _process_local(job_id, filepath, ext, model)
            elif ext == ".pdf":
                page_texts, pt, ct = _process_pdf_file(job_id, filepath, model_id, vllm_url, is_native_ocr, text_prompt, sampling, post, system_prompt, user_prompt)
            else:
                page_texts, pt, ct = _process_image_file(job_id, filepath, model_id, vllm_url, is_native_ocr, text_prompt, sampling, post, system_prompt, user_prompt)

            if model in FURNITURE_STRIP_MODELS:
                from app.ocr.furniture import strip_furniture_pages
                page_texts = strip_furniture_pages(page_texts, filepath, settings.docling_url)

            total_prompt_tokens += pt
            total_completion_tokens += ct

            output_file = _generate_output(
                output_format, base_name, page_texts, filepath, result_dir
            )
            all_result_files.append(os.path.basename(output_file))

        elapsed = round(time.time() - start_time, 1)

        _update_progress(
            job_id,
            status="completed",
            result_files=json.dumps(all_result_files),
            processing_seconds=elapsed,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            model=model,
        )

        # Delete input files only if no sibling jobs share them
        group_id = redis_client.hget(f"job:{job_id}", "group_id")
        if group_id:
            sibling_ids = json.loads(redis_client.hget(f"group:{group_id}", "job_ids") or "[]")
            all_done = all(
                redis_client.hget(f"job:{sid}", "status") in ("completed", "failed")
                for sid in sibling_ids
            )
            if all_done:
                upload_dir = os.path.join(UPLOAD_DIR, group_id)
                shutil.rmtree(upload_dir, ignore_errors=True)
        else:
            upload_dir = os.path.join(UPLOAD_DIR, job_id)
            shutil.rmtree(upload_dir, ignore_errors=True)

    except Exception as exc:
        elapsed = round(time.time() - start_time, 1)
        logger.exception(f"OCR job {job_id} failed")
        _update_progress(
            job_id,
            status="failed",
            error=str(exc)[:500],
            processing_seconds=elapsed,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
        )
        raise self.retry(exc=exc)


def _process_local(
    job_id: str, filepath: str, ext: str, model: str,
) -> tuple[list[str], int, int]:
    """Process with a local model (Tesseract)."""
    import fitz
    from app.ocr.tesseract import ocr_image_tesseract
    from app.ocr.hybrid_pdf import classify_page, render_page_to_image

    ocr_fn = ocr_image_tesseract

    if ext == ".pdf":
        doc = fitz.open(filepath)
        total_pages = len(doc)
        page_texts = []
        for page_num in range(total_pages):
            page = doc[page_num]
            page_type = classify_page(page)
            if page_type == "text":
                text = page.get_text("text").strip()
            else:
                img = render_page_to_image(page)
                result = ocr_fn(img)
                text = result.text
            page_texts.append(text)
            _update_progress(job_id, current_page=page_num + 1, total_pages=total_pages)
        doc.close()
        return page_texts, 0, 0
    else:
        images = convert_to_images(filepath)
        total = len(images)
        _update_progress(job_id, total_pages=total, current_page=0)
        page_texts = []
        for i, img in enumerate(images, 1):
            result = ocr_fn(img)
            page_texts.append(result.text)
            _update_progress(job_id, current_page=i)
        return page_texts, 0, 0


def _process_docling(
    job_id: str, filepath: str, docling_url: str, model: str,
) -> tuple[list[str], int, int]:
    """Process a file via Docling Serve API (whole-document conversion)."""
    import fitz
    from app.ocr.docling import ocr_docling

    use_vlm = DOCLING_MODELS.get(model, False)

    # Count actual pages for progress display
    ext = os.path.splitext(filepath)[1].lower()
    total_pages = 1
    if ext == ".pdf":
        try:
            doc = fitz.open(filepath)
            total_pages = len(doc)
            doc.close()
        except Exception:
            pass

    _update_progress(job_id, total_pages=total_pages, current_page=0)
    result = ocr_docling(filepath, docling_url, use_vlm=use_vlm)
    _update_progress(job_id, current_page=total_pages, total_pages=total_pages)
    return [result.text], 0, 0


def _process_pdf_file(
    job_id: str, filepath: str, model_id: str, vllm_url: str, is_native_ocr: bool, text_prompt: str = "",
    sampling: dict | None = None, post: str = "", system_prompt: str = "", user_prompt: str = "",
) -> tuple[list[str], int, int]:
    def progress_cb(page_num: int, total_pages: int):
        _update_progress(job_id, current_page=page_num, total_pages=total_pages)

    return process_pdf(filepath, model_id, vllm_url, is_native_ocr, text_prompt, sampling=sampling, post=post, system_prompt=system_prompt, user_prompt=user_prompt, progress_callback=progress_cb)


def _process_image_file(
    job_id: str, filepath: str, model_id: str, vllm_url: str, is_native_ocr: bool, text_prompt: str = "",
    sampling: dict | None = None, post: str = "", system_prompt: str = "", user_prompt: str = "",
) -> tuple[list[str], int, int]:
    images = convert_to_images(filepath)
    total = len(images)

    _update_progress(job_id, total_pages=total, current_page=0)

    page_texts = []
    total_prompt = 0
    total_completion = 0
    for i, img in enumerate(images, 1):
        img_b64 = prepare_image(img)
        result = ocr_image(img_b64, model_id, vllm_url, is_native_ocr, text_prompt, sampling=sampling, post=post, system_prompt=system_prompt, user_prompt=user_prompt)
        page_texts.append(result.text)
        total_prompt += result.prompt_tokens
        total_completion += result.completion_tokens
        _update_progress(job_id, current_page=i)

    return page_texts, total_prompt, total_completion


def _generate_output(
    output_format: str,
    base_name: str,
    page_texts: list[str],
    original_path: str,
    result_dir: str,
) -> str:
    if output_format == "markdown":
        path = os.path.join(result_dir, f"{base_name}.md")
        return save_markdown(page_texts, path)
    elif output_format == "html":
        path = os.path.join(result_dir, f"{base_name}.html")
        return save_html(page_texts, path)
    elif output_format == "plaintext":
        path = os.path.join(result_dir, f"{base_name}.txt")
        return save_plaintext(page_texts, path)
    elif output_format == "searchable_pdf":
        path = os.path.join(result_dir, f"{base_name}_searchable.pdf")
        return save_searchable_pdf(original_path, page_texts, path)
    elif output_format == "docx":
        path = os.path.join(result_dir, f"{base_name}.docx")
        return save_docx(page_texts, path)
    else:
        raise ValueError(f"Unknown output format: {output_format}")
