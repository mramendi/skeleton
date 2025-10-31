# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install build dependencies, compile Python packages, then remove build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get remove -y gcc \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY main.py ./main.py
COPY system_prompts.yaml ./system_prompts.yaml

# enable this to copy any plugins you want installed, such as tools or functions
# COPY plugins/ ./plugins/


# Create data directory
RUN mkdir -p /app/data

# Create uploads directory
RUN mkdir -p /app/uploads

# Set environment variables
ENV PYTHONPATH=/app
ENV DATA_PATH=/app/data
ENV UPLOAD_DIR=/app/uploads

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["python", "main.py"]
