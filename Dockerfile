FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ocrmypdf \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    ghostscript \
    qpdf \
    unpaper \
    pngquant \
    img2pdf \
    poppler-utils \
    pandoc \
    libreoffice-writer \
    fonts-dejavu \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY app /app

RUN mkdir -p /data/input /data/output /data/work

ENTRYPOINT ["python", "/app/convert.py"]
CMD ["--help"]
