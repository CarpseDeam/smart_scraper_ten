# Dockerfile

# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables for Python to run in an optimized way
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies required for Google Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Install Google Chrome. This is needed for Selenium to work.
# We are using the official Google repository for stability.
RUN wget --quiet --output-document=- https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor > /etc/apt/trusted.gpg.d/google-archive.gpg && \
    sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list' && \
    apt-get update && \
    apt-get install -y google-chrome-stable --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container first
COPY requirements.txt .

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container
COPY . .

# Expose the port the app will run on. Railway provides this via the $PORT variable.
EXPOSE 8000

# Command to run the application using Gunicorn as a production-ready process manager
# for Uvicorn workers. It will listen on all available network interfaces.
# Railway will automatically set the $PORT environment variable for you.
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "main:app", "--bind", "0.0.0.0:${PORT:-8000}"]