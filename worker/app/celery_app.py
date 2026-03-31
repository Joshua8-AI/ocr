from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery = Celery("ocr", broker=settings.redis_url, backend=settings.redis_url)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3000,
    task_default_queue="ocr_queue",
    beat_schedule={
        "cleanup-old-files": {
            "task": "app.cleanup.cleanup_old_files",
            "schedule": crontab(hour=3, minute=0),
        },
    },
)

# Import tasks so Celery discovers them
import app.tasks  # noqa: E402, F401
import app.cleanup  # noqa: E402, F401
