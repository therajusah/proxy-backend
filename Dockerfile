# RelayNorth control plane (FastAPI API + optional regional worker).
# The public site and admin console are deployed as their own services.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY worker/ ./worker/
COPY alembic/ ./alembic/
COPY alembic.ini ./

EXPOSE 8000
# APP_MODULE lets the same image run the worker (worker.main:app) instead.
CMD ["sh", "-c", "uvicorn ${APP_MODULE:-backend.app.main:app} --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-*}"]
