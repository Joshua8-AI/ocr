# OCR

A web app for running OCR against documents and images through a choice of
backends — Tesseract, Docling, and a handful of vision-language models served
over the vLLM OpenAI-compatible API. Pick a model and an output format
(markdown, plaintext, searchable PDF, or docx), upload files, and download the
results.

## Architecture

Three containers, orchestrated with `docker compose`:

- **web** — FastAPI frontend. Uploads, job tracking, downloads, health-checks
  the configured model endpoints and hides the ones that don't respond.
- **worker** — Celery worker. Runs Tesseract locally, calls remote vLLM
  endpoints for the VLM-based OCR models, and calls a
  [Docling Serve](https://github.com/docling-project/docling-serve) instance
  for Docling / Docling-VLM pipelines.
- **redis** — broker for Celery and session/job state.

Model endpoints are not bundled — you point the app at vLLM and Docling Serve
instances you run yourself.

## Supported models

| Key in `OCR_MODELS`   | What it is                                            |
| --------------------- | ----------------------------------------------------- |
| `Tesseract`           | Local Tesseract (always available; no endpoint)       |
| `LightOnOCR-2-1B`     | LightOn's 2.1B native OCR model on vLLM               |
| `GLM-OCR`             | ZAI's GLM-OCR on vLLM                                 |
| `Qwen35-9B`           | Qwen 3.5 9B on vLLM (general VLM w/ OCR system prompt)|
| `Qwen3.5-35B-A3B`     | Qwen 3.5 35B A3B on vLLM                              |
| `Docling`             | Docling Serve, standard (layout-based) pipeline       |
| `Docling-VLM`         | Docling Serve, VLM pipeline (calls a Qwen endpoint)   |

Any model whose `/health` check fails at app startup is hidden from the UI, so
you only need to list the models you actually run.

## Quick start

1. Copy the example env file and edit it:

   ```bash
   cp .env.example .env
   ```

   Set `OCR_MODELS` to the endpoints you have, and `DOCLING_VLM_URL` if you
   plan to use the `Docling-VLM` pipeline.

2. Bring the stack up:

   ```bash
   docker compose up -d --build
   ```

3. Visit `http://localhost:8500` (or whatever `APP_PORT` you set).

## Configuration

See [`.env.example`](./.env.example) for the full list. The important ones:

- `APP_PORT` — host port for the web UI.
- `OCR_MODELS` — `DisplayName=BASE_URL;DisplayName=BASE_URL;...`
- `DOCLING_VLM_URL` — full `/chat/completions` URL of the VLM backend that
  Docling should call when the `Docling-VLM` pipeline is selected.
- `DOCLING_VLM_MODEL` — model name string passed to that backend.

## Output formats

- **Markdown** — plain `.md`. Images extracted by Docling are embedded as
  base64 data URIs.
- **Plaintext** — `.txt`, one page per form-feed boundary.
- **Searchable PDF** — the original PDF with an OCR text layer added.
- **DOCX** — Pandoc converts the markdown (including LaTeX-style tables and
  embedded images) into a real Word document.

## Running model backends

This repo doesn't include vLLM or Docling Serve — run them wherever you like.
Minimal examples:

```bash
# vLLM with LightOnOCR
docker run --gpus all -p 8000:8000 \
  vllm/vllm-openai:latest \
  --model switzerchees/LightOnOCR-2-1B-NVFP4

# Docling Serve
docker run --gpus all -p 5001:5001 \
  ghcr.io/docling-project/docling-serve-cu130:main
```

Then add them to `OCR_MODELS` in `.env`.

## License

[MIT](./LICENSE).
