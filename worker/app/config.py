from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://redis:6379/0"
    vllm_url: str = "http://vllm:8000/v1"
    agentmail_api_key: str = ""
    agentmail_inbox_id: str = ""
    app_base_url: str = "http://localhost:8200"
    data_dir: str = "/data"
    result_retention_hours: int = 36
    ocr_timeout_seconds: int = 120
    ocr_max_tokens: int = 8192


settings = Settings()
