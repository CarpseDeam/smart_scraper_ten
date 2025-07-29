# Dockerfile

# --- Stage 1: Build Stage ---
# Use a modern, slim Python base image.
FROM python:3.11-slim as base

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory inside the container
WORKDIR /app

# Copy only the requirements file first to leverage Docker's layer caching.
# This layer will only be rebuilt if the requirements file changes.
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt


# --- Stage 2: Final Stage ---
# Copy the installed dependencies from the base stage
FROM base as final

# Copy the rest of your application code into the container
COPY . .

# Expose the port that the application will run on
# FastAPI default is 8000, which is what Uvicorn/Gunicorn will bind to.
EXPOSE 8000

# The command to run your application using Gunicorn as a process manager
# and Uvicorn as the ASGI worker. This is a production-ready setup.
# It will automatically use the PORT variable provided by Railway.
CMD ["/bin/sh", "-c", "gunicorn -k uvicorn.workers.UvicornWorker -w 2 main:app --bind 0.0.0.0:${PORT:-8000}"]