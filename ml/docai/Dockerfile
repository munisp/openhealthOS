# ml/docai sidecar image. Built only in CI/deploy — the sandbox does not
# build it (no heavy wheels). Composition doctrine: docintel talks to this
# over HTTP; nothing in the platform imports these packages.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

ENV DOCAI_PORT=8100
EXPOSE 8100
CMD ["python3", "server.py"]
