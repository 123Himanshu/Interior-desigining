# Use the official Python lightweight image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables to prevent Python from writing pyc files to disc
# and to keep stdout/stderr unbuffered so logs work properly
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Ensure persistent storage and upload/output directories
RUN mkdir -p /data uploads outputs && chmod 777 /data uploads outputs

# Command to run the application. 
# Railway automatically assigns an environment variable $PORT, but we default to 8000 just in case.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
