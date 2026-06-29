FROM python:3.11-slim

# System deps: Cairo (SVG→PNG rasterization), CJK fonts, pandoc
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libcairo2-dev \
    pkg-config \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    fonts-noto \
    fonts-noto-cjk \
    pandoc \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install existing project deps first (layer cache)
COPY skills/ppt-master/requirements.txt skills/ppt-master/requirements.txt
RUN pip install --no-cache-dir -r skills/ppt-master/requirements.txt

# Install web server deps
RUN pip install --no-cache-dir \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.29.0" \
    "openai>=1.30.0" \
    "anthropic>=0.40.0" \
    "aiofiles>=23.0.0" \
    "python-multipart>=0.0.9"

# Copy repo
COPY . .

# Ensure project/export dirs exist
RUN mkdir -p /app/projects /app/exports

EXPOSE 8080

CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
