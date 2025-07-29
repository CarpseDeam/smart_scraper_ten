# Dockerfile
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install-deps chromium
RUN playwright install chromium
COPY . .
EXPOSE 8000
CMD ["/bin/sh", "-c", "gunicorn -k uvicorn.workers.UvicornWorker -w 2 main:app --bind 0.0.0.0:${PORT:-8000}"]