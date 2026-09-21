FROM python:3.12-slim

WORKDIR /app

COPY ingest/requirements.txt ingest/requirements.txt
COPY data/requirements.txt data/requirements.txt
RUN pip install --no-cache-dir -r ingest/requirements.txt -r data/requirements.txt

COPY ingest ingest
COPY data data

CMD ["python", "-m", "ingest.scheduler"]
