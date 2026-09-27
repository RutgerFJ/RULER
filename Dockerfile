# Use official Python runtime as base image
FROM python:3.14.5-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# Install system dependencies including BLAST
RUN apt-get update && apt-get install -y --no-install-recommends \
    ncbi-blast+ \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install uv installer
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set work directory
WORKDIR /app

# Copy project files
COPY . .

# Install Python dependencies
RUN uv sync

CMD ["gunicorn", "mlva.wsgi:application", "--bind", "0.0.0.0:8000"]