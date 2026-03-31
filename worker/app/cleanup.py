import logging
import os
import shutil
import time

from app.celery_app import celery
from app.config import settings

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join(settings.data_dir, "uploads")
RESULT_DIR = os.path.join(settings.data_dir, "results")


@celery.task(name="app.cleanup.cleanup_old_files")
def cleanup_old_files() -> dict:
    """Remove upload and result directories older than retention period.

    Runs daily via Celery Beat. Catches orphaned files from failed jobs,
    crashes, etc.
    """
    cutoff = time.time() - (settings.result_retention_hours * 3600)
    removed_uploads = 0
    removed_results = 0

    # Clean uploads
    for dirname in _scan_old_dirs(UPLOAD_DIR, cutoff):
        removed_uploads += 1

    # Clean results
    for dirname in _scan_old_dirs(RESULT_DIR, cutoff):
        removed_results += 1

    logger.info(
        f"Cleanup complete: removed {removed_uploads} upload dirs, "
        f"{removed_results} result dirs"
    )
    return {
        "removed_uploads": removed_uploads,
        "removed_results": removed_results,
    }


def _scan_old_dirs(base_dir: str, cutoff: float):
    """Yield and remove directories older than cutoff timestamp."""
    if not os.path.isdir(base_dir):
        return

    for name in os.listdir(base_dir):
        dirpath = os.path.join(base_dir, name)
        if not os.path.isdir(dirpath):
            continue
        try:
            mtime = os.path.getmtime(dirpath)
            if mtime < cutoff:
                shutil.rmtree(dirpath, ignore_errors=True)
                logger.info(f"Removed old directory: {dirpath}")
                yield dirpath
        except OSError:
            logger.exception(f"Error checking directory: {dirpath}")
