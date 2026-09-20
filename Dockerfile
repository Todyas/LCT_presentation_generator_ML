FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress \
    poppler-utils \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uv", "run", "--frozen", "--no-dev", "uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
