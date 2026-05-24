from enum import Enum

from pydantic import BaseModel


class OutputFormat(str, Enum):
    markdown = "markdown"
    html = "html"
    plaintext = "plaintext"
    searchable_pdf = "searchable_pdf"
    docx = "docx"


class ModelInfo(BaseModel):
    key: str
    display: str


class AppConfig(BaseModel):
    models: list[ModelInfo]


class UploadResponse(BaseModel):
    job_ids: list[str]
    access_tokens: dict[str, str]
    group_id: str
    status: str = "queued"
    message: str
    file_count: int
    models: list[str]


class ModelStats(BaseModel):
    model: str = ""
    processing_seconds: float = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class JobProgress(BaseModel):
    job_id: str
    status: str
    model: str = ""
    current_file: int = 0
    total_files: int = 0
    current_page: int = 0
    total_pages: int = 0
    filename: str = ""
    result_files: list[str] = []
    error: str | None = None
    stats: ModelStats | None = None


class JobSummary(BaseModel):
    job_id: str
    status: str
    file_count: int
    output_format: str
    created_at: str
    model: str = ""
    result_files: list[str] = []
    stats: ModelStats | None = None
    group_id: str = ""
