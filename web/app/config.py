from pydantic_settings import BaseSettings

# Tesseract runs locally in the worker, no vLLM endpoint needed
LOCAL_MODELS = {"Tesseract": "local"}

DISPLAY_NAMES = {
    "Tesseract": "Tesseract",
    "Docling": "Docling",
    "Docling-VLM": "Docling+Qwen3.5",
    "LightOnOCR-2-1B": "LightOnOCR",
    "GLM-OCR": "GLM-OCR",
    "Qwen35-9B": "Qwen3.5-9B",
    "Qwen3.5-35B-A3B": "Qwen3.5-35B",
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
