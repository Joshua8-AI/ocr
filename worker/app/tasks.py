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

MODEL_HF_IDS = {
    "LightOnOCR-2-1B": "switzerchees/LightOnOCR-2-1B-NVFP4",
    "Qwen3.5-35B-A3B": "cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit",
    "GLM-OCR": "zai-org/GLM-OCR",
}

NATIVE_OCR_MODELS = {"LightOnOCR-2-1B"}

# Models that use a fixed text prompt instead of system prompt
MODEL_PROMPTS = {
    "GLM-OCR": "Text Recognition:",
}

# Models that run locally without vLLM
LOCAL_MODELS = {"Tesseract"}


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

    if not is_local:
        if not vllm_url:
            vllm_url = settings.vllm_url
        model_id = _resolve_model_id(model)
        is_native_ocr = model in NATIVE_OCR_MODELS
        text_prompt = MODEL_PROMPTS.get(model, "")

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

            if is_local:
                page_texts, pt, ct = _process_local(job_id, filepath, ext, model)
            elif ext == ".pdf":
                page_texts, pt, ct = _process_pdf_file(job_id, filepath, model_id, vllm_url, is_native_ocr, text_prompt)
            else:
                page_texts, pt, ct = _process_image_file(job_id, filepath, model_id, vllm_url, is_native_ocr, text_prompt)

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


def _process_pdf_file(
    job_id: str, filepath: str, model_id: str, vllm_url: str, is_native_ocr: bool, text_prompt: str = "",
) -> tuple[list[str], int, int]:
    def progress_cb(page_num: int, total_pages: int):
        _update_progress(job_id, current_page=page_num, total_pages=total_pages)

    return process_pdf(filepath, model_id, vllm_url, is_native_ocr, text_prompt, progress_callback=progress_cb)


def _process_image_file(
    job_id: str, filepath: str, model_id: str, vllm_url: str, is_native_ocr: bool, text_prompt: str = "",
) -> tuple[list[str], int, int]:
    images = convert_to_images(filepath)
    total = len(images)

    _update_progress(job_id, total_pages=total, current_page=0)

    page_texts = []
    total_prompt = 0
    total_completion = 0
    for i, img in enumerate(images, 1):
        img_b64 = prepare_image(img)
        result = ocr_image(img_b64, model_id, vllm_url, is_native_ocr, text_prompt)
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
