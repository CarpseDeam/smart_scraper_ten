# Dockerfile

# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables for Python to run in an optimized way
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container first
COPY requirements.txt .

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Correctly install Playwright's browser and its OS dependencies.
# First, install the system dependencies for Chromium.
RUN playwright install-deps chromium
# Then, download the actual Chromium browser binary managed by Playwright.
RUN playwright install chromium

# Copy the rest of your application code into the container
COPY . .

# Expose the port the app will run on. Railway provides this via the $PORT variable.
EXPOSE 8000

# Final, most robust command form.
CMD ["/bin/sh", "-c", "gunicorn -k uvicorn.workers.UvicornWorker -w 2 main:app --bind 0.0.0.0:${PORT:-8000}"]