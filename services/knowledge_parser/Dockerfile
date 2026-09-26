FROM python:3.13-slim
WORKDIR /service
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY services/knowledge_parser/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/knowledge_parser/main.py services/knowledge_parser/convert.py ./
COPY app/shared/media/documents.py ./app/shared/media/documents.py
RUN useradd --uid 10001 --create-home worker && mkdir /data && chown worker /data \
    && mkdir -p /usr/local/lib/python3.13/site-packages/rapidocr/models \
    && chown worker /usr/local/lib/python3.13/site-packages/rapidocr/models \
    && mkdir -p /home/worker/.cache \
    && chown worker /home/worker/.cache
USER worker
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
