import json
import os
import secrets
import shutil
import time
import uuid

import httpx
import redis
from celery import Celery
from fastapi import (
    APIRouter,
    Cookie,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from app.api.schemas import (
    AppConfig,
    JobProgress,
    JobSummary,
    ModelInfo,
    ModelStats,
    OutputFormat,
    UploadResponse,
)
from app.config import DISPLAY_NAMES, MODEL_PARAMS, settings
from app.validation.file_validator import (
    FileValidationError,
    validate_file_extension,
    validate_magic_bytes,
)
from app.validation.sanitizer import sanitize_filename

router = APIRouter(prefix="/api")

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
celery_app = Celery("ocr", broker=settings.redis_url)

UPLOAD_DIR = os.path.join(settings.data_dir, "uploads")
RESULT_DIR = os.path.join(settings.data_dir, "results")


def _check_access(
    job_id: str,
    token: str | None,
    session_token: str | None,
) -> None:
    stored_token = redis_client.hget(f"job:{job_id}", "access_token")
    if not stored_token:
        raise HTTPException(status_code=404, detail="Job not found")
    if token and secrets.compare_digest(token, stored_token):
        return
    if session_token:
        if redis_client.sismember(f"session:{session_token}", job_id):
            return
    raise HTTPException(status_code=403, detail="Access denied")


def _get_stats(data: dict) -> ModelStats | None:
    if data.get("processing_seconds"):
        return ModelStats(
            model=data.get("model", ""),
            processing_seconds=float(data.get("processing_seconds", 0)),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            completion_tokens=int(data.get("completion_tokens", 0)),
        )
    return None


# Cloudflare adds these to every request it proxies; their absence means the
# client reached the origin directly (LAN or localhost), where no cap applies.
CLOUDFLARE_HEADERS = ("cf-ray", "cf-connecting-ip")
CLOUDFLARE_MAX_SUBMISSION_MB = 100


def _submission_limit_mb(request: Request) -> int | None:
    """Return the effective upload cap for this client, or None if unlimited."""
    if any(h in request.headers for h in CLOUDFLARE_HEADERS):
        return CLOUDFLARE_MAX_SUBMISSION_MB
    return None


@router.get("/config")
async def get_config(request: Request) -> AppConfig:
    available = []
    for name, url in settings.available_models.items():
        if url == "local":
            display = DISPLAY_NAMES.get(name, name)
            available.append(ModelInfo(key=name, display=display))
            continue
        try:
            base = url.rstrip("/")
            if base.endswith("/v1"):
                # OpenAI-compatible backend (vLLM or the LiteLLM gateway):
                # /v1/models is instant and confirms the API is serving.
                # LiteLLM's /health pings every backend and is far too slow
                # for this 2s startup probe.
                health_url = base + "/models"
            else:
                # Docling Serve and other non-OpenAI backends
                health_url = base + "/health"
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get(health_url)
                if resp.status_code == 200:
                    display = DISPLAY_NAMES.get(name, name)
                    available.append(ModelInfo(key=name, display=display))
        except Exception:
            pass
    # Order the picker fast/small -> accurate/large (matches the UI scale)
    available.sort(key=lambda m: (MODEL_PARAMS.get(m.key, 999), m.key))
    return AppConfig(models=available, max_submission_mb=_submission_limit_mb(request))


@router.post("/upload", status_code=202)
async def upload_files(
    files: list[UploadFile],
    response: Response,
    output_format: OutputFormat = Form(...),
    models: str = Form(...),
    session_token: str | None = Cookie(default=None),
) -> UploadResponse:
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    if not model_list:
        raise HTTPException(status_code=400, detail="No models selected")
    for m in model_list:
        if m not in settings.available_models:
            raise HTTPException(status_code=400, detail=f"Unknown model '{m}'")

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files (max {settings.max_files_per_request})",
        )

    group_id = str(uuid.uuid4())
    job_upload_dir = os.path.join(UPLOAD_DIR, group_id)
    os.makedirs(job_upload_dir, exist_ok=True)

    saved_files: list[str] = []
    try:
        for file in files:
            if not file.filename:
                raise HTTPException(status_code=400, detail="File has no filename")
            content = await file.read()
            # No server-side size limit: uploads are bounded in practice by
            # Cloudflare's 100MB request-body cap on the tunnel. Direct LAN
            # submissions are deliberately unrestricted.
            safe_name = sanitize_filename(file.filename)
            try:
                validate_file_extension(safe_name)
            except FileValidationError as e:
                raise HTTPException(status_code=415, detail=str(e))
            try:
                validate_magic_bytes(content, safe_name)
            except FileValidationError as e:
                raise HTTPException(status_code=422, detail=str(e))
            filepath = os.path.join(job_upload_dir, safe_name)
            with open(filepath, "wb") as f:
                f.write(content)
            saved_files.append(filepath)
    except HTTPException:
        shutil.rmtree(job_upload_dir, ignore_errors=True)
        raise

    if not session_token:
        session_token = secrets.token_hex(32)

    multi_model = len(model_list) > 1
    job_ids = []
    access_tokens = {}

    for model in model_list:
        job_id = str(uuid.uuid4())
        access_token = secrets.token_hex(32)
        vllm_url = settings.available_models[model]

        file_prefix = model.replace(" ", "_") if multi_model else ""

        job_data = {
            "status": "queued",
            "output_format": output_format.value,
            "model": model,
            "file_count": str(len(saved_files)),
            "access_token": access_token,
            "created_at": str(time.time()),
            "result_files": "[]",
            "group_id": group_id,
        }
        redis_client.hset(f"job:{job_id}", mapping=job_data)
        redis_client.expire(f"job:{job_id}", 36 * 3600)

        redis_client.sadd(f"session:{session_token}", job_id)

        celery_app.send_task(
            "app.tasks.process_ocr_job",
            args=[job_id, saved_files, "", output_format.value, access_token, model, vllm_url, file_prefix],
            queue="ocr_queue",
        )

        job_ids.append(job_id)
        access_tokens[job_id] = access_token

    redis_client.hset(f"group:{group_id}", mapping={
        "job_ids": json.dumps(job_ids),
        "models": json.dumps(model_list),
    })
    redis_client.expire(f"group:{group_id}", 36 * 3600)
    redis_client.expire(f"session:{session_token}", 36 * 3600)

    response.set_cookie(
        key="session_token",
        value=session_token,
        max_age=36 * 3600,
        httponly=True,
        samesite="lax",
    )

    msg = f"Your {len(saved_files)} file(s) queued for OCR with {len(model_list)} model(s)."

    return UploadResponse(
        job_ids=job_ids,
        access_tokens=access_tokens,
        group_id=group_id,
        status="queued",
        message=msg,
        file_count=len(saved_files),
        models=model_list,
    )


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    token: str | None = Query(default=None),
    session_token: str | None = Cookie(default=None),
) -> JobProgress:
    _check_access(job_id, token, session_token)

    data = redis_client.hgetall(f"job:{job_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Job not found")

    result_files = json.loads(data.get("result_files", "[]"))

    return JobProgress(
        job_id=job_id,
        status=data.get("status", "unknown"),
        model=data.get("model", ""),
        current_file=int(data.get("current_file", 0)),
        total_files=int(data.get("file_count", 0)),
        current_page=int(data.get("current_page", 0)),
        total_pages=int(data.get("total_pages", 0)),
        filename=data.get("current_filename", ""),
        result_files=result_files,
        error=data.get("error"),
        stats=_get_stats(data),
    )


@router.get("/jobs/{job_id}/files/{filename}")
async def download_result_file(
    job_id: str,
    filename: str,
    token: str | None = Query(default=None),
    session_token: str | None = Cookie(default=None),
):
    _check_access(job_id, token, session_token)

    safe_name = sanitize_filename(filename)
    filepath = os.path.join(RESULT_DIR, job_id, safe_name)

    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    real_path = os.path.realpath(filepath)
    real_result_dir = os.path.realpath(os.path.join(RESULT_DIR, job_id))
    if not real_path.startswith(real_result_dir):
        raise HTTPException(status_code=403, detail="Access denied")

    from fastapi.responses import FileResponse

    return FileResponse(
        path=filepath,
        filename=safe_name,
        media_type="application/octet-stream",
    )


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(
    job_id: str,
    token: str | None = Query(default=None),
    session_token: str | None = Cookie(default=None),
):
    _check_access(job_id, token, session_token)

    data = redis_client.hgetall(f"job:{job_id}")
    if not data:
        raise HTTPException(status_code=404, detail="Job not found")

    result_dir = os.path.join(RESULT_DIR, job_id)
    shutil.rmtree(result_dir, ignore_errors=True)

    upload_dir = os.path.join(UPLOAD_DIR, job_id)
    shutil.rmtree(upload_dir, ignore_errors=True)

    redis_client.delete(f"job:{job_id}")

    if session_token:
        redis_client.srem(f"session:{session_token}", job_id)


@router.get("/my-jobs")
async def list_my_jobs(
    session_token: str | None = Cookie(default=None),
) -> list[JobSummary]:
    if not session_token:
        return []

    job_ids = redis_client.smembers(f"session:{session_token}")
    jobs = []

    for job_id in sorted(job_ids, reverse=True):
        data = redis_client.hgetall(f"job:{job_id}")
        if not data:
            continue
        result_files = json.loads(data.get("result_files", "[]"))
        jobs.append(
            JobSummary(
                job_id=job_id,
                status=data.get("status", "unknown"),
                file_count=int(data.get("file_count", 0)),
                output_format=data.get("output_format", ""),
                created_at=data.get("created_at", ""),
                model=data.get("model", ""),
                result_files=result_files,
                stats=_get_stats(data),
                group_id=data.get("group_id", ""),
            )
        )

    return jobs
