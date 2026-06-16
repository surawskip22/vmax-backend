FROM node:22-alpine AS frontend

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
COPY --from=frontend /build/static /app/static

CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn panmajster.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
