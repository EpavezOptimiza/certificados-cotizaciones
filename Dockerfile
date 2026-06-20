FROM python:3.11-slim

# Chromium + chromedriver de Debian (version-matched, sin descargas externas)
RUN apt-get update && apt-get install -y \
    chromium chromium-driver \
    fonts-liberation libatk-bridge2.0-0 libgtk-3-0 libxss1 \
    && rm -rf /var/lib/apt/lists/*

# Deshabilitar Selenium Manager para que no intente descargar su propio chromedriver
ENV SE_MANAGER_ENABLED=false

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 600
