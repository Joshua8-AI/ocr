from pydantic_settings import BaseSettings

# Tesseract runs locally in the worker, no vLLM endpoint needed
LOCAL_MODELS = {"Tesseract": "local", "Tesseract-FS": "local"}

DISPLAY_NAMES = {
    "Tesseract": "Tesseract",
    "Tesseract-FS": "Tesseract (no headers/footers)",
    "Docling": "Docling",
    "Docling-VLM": "Docling+Qwen3.6",
    "LightOnOCR-2-1B": "LightOnOCR",
    "GLM-OCR": "GLM-OCR",
    "OlmOCR2": "OlmOCR2",
    "Qwen35-9B": "Qwen3.5-9B",
    "Qwen35-9B-FS": "Qwen3.5-9B (no headers/footers)",
    "Qwen35-122B": "Qwen3.5-122B",
    "Qwen35-122B-FS": "Qwen3.5-122B (no headers/footers)",
    "Qwen3.6-35B": "Qwen3.6-35B",
    "Qwen3.6-35B-FS": "Qwen3.6-35B (no headers/footers)",
    "Gemma4-12B": "Gemma4-12B",
    "Gemma4-26B": "Gemma4-26B",
    "Gemma4-31B": "Gemma4-31B",
    "Gemma4-E4B": "Gemma4-E4B",
    "Chandra": "Chandra",
    "DeepSeek-OCR": "DeepSeek-OCR",
    "dots-ocr": "dots.ocr",
    "Nanonets-OCR2": "Nanonets-OCR2",
    "PaddleOCR-VL": "PaddleOCR-VL",
    "OvisOCR2": "OvisOCR2",
}


# Approximate model size in billions of parameters, used to order the model
# picker from fast/small to accurate/large (matches the "fast → accurate" scale
# under the selector). Tesseract/Docling have no LLM params; ties are nudged with
# decimals so the faster/lighter option sits left of its heavier sibling.
MODEL_PARAMS = {
    "Tesseract": 0,
    "Tesseract-FS": 0.1,
    "Docling": 0.5,
    "OvisOCR2": 0.8,
    "PaddleOCR-VL": 0.9,
    "GLM-OCR": 1,
    "LightOnOCR-2-1B": 1.5,
    "dots-ocr": 2,
    "DeepSeek-OCR": 3,
    "Nanonets-OCR2": 3.1,
    "Gemma4-E4B": 4,
    "Chandra": 5,
    "OlmOCR2": 7,
    "Qwen35-9B": 9,
    "Qwen35-9B-FS": 9.1,
    "Gemma4-12B": 12,
    "Gemma4-26B": 26,
    "Gemma4-31B": 31,
    "Qwen3.6-35B": 35,
    "Qwen3.6-35B-FS": 35.1,
    "Docling-VLM": 35.5,
    "Qwen35-122B": 122,
    "Qwen35-122B-FS": 122.1,
}


def _parse_models(raw: str) -> dict[str, str]:
    """Parse OCR_MODELS env var: 'DisplayName=http://host:port/v1;Other=http://...'"""
    models = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if "=" not in entry:
            continue
        name, url = entry.split("=", 1)
        models[name.strip()] = url.strip()
    return models


class Settings(BaseSettings):
    redis_url: str = "redis://redis:6379/0"
    vllm_url: str = "http://vllm:8000/v1"
    app_base_url: str = "http://localhost:8200"
    data_dir: str = "/data"
    max_file_size_mb: int = 100
    max_files_per_request: int = 20
    ocr_models: str = ""

    @property
    def available_models(self) -> dict[str, str]:
        """Returns dict of model display name -> vLLM base URL (or 'local')."""
        models = dict(LOCAL_MODELS)
        if self.ocr_models:
            models.update(_parse_models(self.ocr_models))
        else:
            models["LightOnOCR-2-1B"] = self.vllm_url
        return models


settings = Settings()
