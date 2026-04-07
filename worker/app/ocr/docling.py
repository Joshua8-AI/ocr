"""Docling OCR engine — processes entire documents via Docling Serve API."""
import json
import logging

import httpx

from app.ocr.engine import OcrResult

logger = logging.getLogger(__name__)


def ocr_docling(filepath: str, docling_url: str, use_vlm: bool = False) -> OcrResult:
    """Upload a file to Docling Serve and return the markdown content.

    Args:
        filepath: Path to the document file
        docling_url: Base URL of Docling Serve (e.g. http://192.168.30.208:5001)
        use_vlm: If True, use VLM pipeline with qwen35 preset
    """
    url = f"{docling_url}/v1/convert/file"

    with open(filepath, "rb") as f:
        files = {"files": (filepath.split("/")[-1], f)}
        data = {"to_formats": "md"}
        if use_vlm:
            data["pipeline"] = "vlm"
            data["vlm_pipeline_model_api"] = json.dumps({
                "url": "http://192.168.30.211:8005/v1/chat/completions",
                "headers": {},
                "params": {"model": "cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit", "max_tokens": 16384},
                "timeout": 300,
                "concurrency": 4,
                "prompt": "Convert this page to markdown. Do not miss any text and only output the bare markdown!",
                "scale": 2.0,
                "response_format": "markdown",
            })

        with httpx.Client(timeout=600) as client:
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
