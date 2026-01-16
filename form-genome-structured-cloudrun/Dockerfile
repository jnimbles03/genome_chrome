# Form Genome - Google Cloud Run Deployment
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies (including Playwright/Chromium requirements)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    wget \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only for efficiency)
# Note: Dependencies already installed above, so no --with-deps
RUN playwright install chromium

# Cache buster - invalidate cache from this point forward
ARG CACHEBUST=2025-11-24T16:00:00
RUN echo "Cache bust: $CACHEBUST"

# Copy application code
# Build: 2025-11-24T05:50 - LLM routing fixes (FORCED REBUILD)
COPY . .

# Create directory for SQLite database
RUN mkdir -p /app/data

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=main.py
ENV PORT=8080

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run the application with gunicorn (1 worker for SQLite compatibility)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 "main:create_app()"
