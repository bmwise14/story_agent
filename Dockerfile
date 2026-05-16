FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/

# SERVICE_ROLE controls which Cloud Run service this container acts as.
# "router" → public FastAPI, high concurrency, runs /auth/* and /story/* routes
# "worker" → internal, concurrency=1, runs /internal/worker (Pub/Sub push target)
# Both roles use the same image — role is selected at deploy time via env var.
ENV SERVICE_ROLE=router
ENV PORT=8080

CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port $PORT"]
