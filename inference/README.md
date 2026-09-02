# 本地推理服务

主后端固定使用 Python 3.14。ASR 与视觉模型通过内部 HTTP 契约隔离，因此模型运行时可以使用各自稳定兼容的 Python、CUDA 和原生依赖，不污染主后端环境。

- `asr_service`：faster-whisper 1.2.1，默认 `large-v3`、CPU、INT8。
- `vision_service`：MiniCPM-V 4.6，默认使用 Transformers 5.7.0 自动选择可用设备。

Docker Compose 中两项服务属于 `local-models` profile：

```bash
docker compose --profile local-models up --build
```

首次启动需要从模型仓库下载权重。生产环境应预下载并固定模型快照，同时通过内网访问控制保护 8101/8102 端口。
