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
        data = {}
        if use_vlm:
            data["pipeline"] = "vlm"
            data["vlm_pipeline_model_api"] = json.dumps({
                "url": "http://ultra7:8005/v1/chat/completions",
                "headers": {},
                "params": {"model": "cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit", "max_tokens": 4096},
                "timeout": 120,
                "concurrency": 4,
                "prompt": "Convert this page to docling.",
                "scale": 2.0,
                "response_format": "doctags",
            })

        with httpx.Client(timeout=600) as client:
            resp = client.post(url, files=files, data=data)
            resp.raise_for_status()
            result = resp.json()

    if result.get("status") != "success":
        errors = result.get("errors", [])
        raise RuntimeError(f"Docling conversion failed: {errors}")

    md_content = result.get("document", {}).get("md_content", "")
    return OcrResult(text=md_content, prompt_tokens=0, completion_tokens=0)
