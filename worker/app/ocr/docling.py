"""Docling OCR engine — processes entire documents via Docling Serve API."""
import json
import logging

import httpx

from app.config import settings
from app.ocr.engine import OcrResult

logger = logging.getLogger(__name__)


def ocr_docling(filepath: str, docling_url: str, use_vlm: bool = False) -> OcrResult:
    """Upload a file to Docling Serve and return the markdown content.

    Args:
        filepath: Path to the document file
        docling_url: Base URL of Docling Serve (e.g. http://docling:5001)
        use_vlm: If True, use VLM pipeline against DOCLING_VLM_URL
    """
    url = f"{docling_url}/v1/convert/file"

    with open(filepath, "rb") as f:
        files = {"files": (filepath.split("/")[-1], f)}
        data = {
            "to_formats": "md",
            "image_export_mode": "embedded",
            "include_images": "true",
        }
        if use_vlm:
            if not settings.docling_vlm_url:
                raise RuntimeError(
                    "DOCLING_VLM_URL is not set; required for Docling-VLM pipeline"
                )
            data["pipeline"] = "vlm"
            data["vlm_pipeline_model_api"] = json.dumps({
                "url": settings.docling_vlm_url,
                "headers": {},
                "params": {"model": settings.docling_vlm_model, "max_tokens": 16384},
                "timeout": 300,
                "concurrency": 4,
                "prompt": "Convert this page to markdown. Do not miss any text and only output the bare markdown!",
                "scale": 2.0,
                "response_format": "markdown",
            })

        with httpx.Client(timeout=1800) as client:
            resp = client.post(url, files=files, data=data)
            resp.raise_for_status()
            result = resp.json()

    doc = result.get("document", {})
    logger.info("Docling response status=%s, doc keys=%s", result.get("status"), list(doc.keys()))
    for k, v in doc.items():
        if v is not None and k != "filename":
            logger.info("Docling doc[%s] length=%s", k, len(str(v)))

    if result.get("status") != "success":
        errors = result.get("errors", [])
        raise RuntimeError(f"Docling conversion failed: {errors}")

    md_content = doc.get("md_content") or doc.get("text_content") or ""
    return OcrResult(text=md_content, prompt_tokens=0, completion_tokens=0)
