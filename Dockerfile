FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py config.yaml ./

# Run as non-root
RUN useradd -m scraper
USER scraper

CMD ["python", "main.py"]
