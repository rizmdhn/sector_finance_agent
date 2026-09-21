FROM python:3.12-slim

WORKDIR /app

COPY gateway/requirements.txt gateway/requirements.txt
COPY data/requirements.txt data/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt -r data/requirements.txt

COPY gateway gateway
COPY data data
COPY models.yaml models.yaml

CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
