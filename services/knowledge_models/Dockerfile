FROM python:3.13-slim
WORKDIR /service
COPY services/knowledge_models/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/knowledge_models/main.py ./main.py
RUN useradd --uid 10001 --create-home worker \
    && mkdir -p /home/worker/.cache/huggingface \
    && chown -R worker /home/worker/.cache
USER worker
ENV HF_HOME=/home/worker/.cache/huggingface
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
