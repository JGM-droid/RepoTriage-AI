FROM python:3.12.10-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY fixtures ./fixtures

RUN pip install --no-cache-dir .

# Bake the embedding model into the image (Milestone 2.3, ADR 0009) so
# runtime containers never need network access to use it: fastembed caches
# the ONNX model files under this directory on first use, and this RUN
# step is that first use, at build time.
ENV EMBEDDING_CACHE_DIR=/app/.fastembed_cache
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/app/.fastembed_cache')"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]