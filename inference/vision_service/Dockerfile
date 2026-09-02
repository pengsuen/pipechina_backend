FROM python:3.13.15-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /service
COPY requirements.txt ./
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
RUN python -m pip install --no-cache-dir \
        torch==2.11.0 torchvision==0.26.0 \
        --index-url "${PYTORCH_INDEX_URL}" \
    && python -m pip install --no-cache-dir --requirement requirements.txt
COPY app.py ./

RUN useradd --system --uid 10003 --create-home inference \
    && install -d -o inference -g inference /models
USER 10003

EXPOSE 8102
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8102"]
