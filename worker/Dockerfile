FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 \
    djvulibre-bin \
    imagemagick \
    tesseract-ocr \
    tesseract-ocr-eng \
    pandoc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
RUN uv pip install --system -r pyproject.toml

COPY app/ app/

CMD ["celery", "-A", "app.celery_app", "worker", "--beat", "--loglevel=info", "--concurrency=2", "-Q", "ocr_queue"]
