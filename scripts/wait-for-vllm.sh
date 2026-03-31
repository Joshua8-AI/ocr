#!/bin/bash
# Wait for vLLM to be ready before starting dependent services
set -e

VLLM_URL="${VLLM_URL:-http://vllm:8000}"
MAX_RETRIES=60
RETRY_INTERVAL=5

echo "Waiting for vLLM at $VLLM_URL..."
for i in $(seq 1 $MAX_RETRIES); do
    if curl -sf "$VLLM_URL/health" > /dev/null 2>&1; then
        echo "vLLM is ready!"
        exit 0
    fi
    echo "Attempt $i/$MAX_RETRIES - vLLM not ready yet, waiting ${RETRY_INTERVAL}s..."
    sleep $RETRY_INTERVAL
done

echo "ERROR: vLLM did not become ready in time"
exit 1
