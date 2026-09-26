# 项目自有独立服务

主后端固定使用 Python 3.14。ASR 与视觉模型通过内部 HTTP 契约隔离，因此模型运行时可以使用各自稳定兼容的 Python、CUDA 和原生依赖，不污染主后端环境。

- `asr/`：faster-whisper 音频转写。
- `vision/`：MiniCPM-V 视觉分析。
- `knowledge_parser/`：Docling 文档解析、版面与 OCR。
- `knowledge_models/`：Embedding、重排（以两个独立容器运行）。

这里保留服务源码、独立依赖和 Dockerfile；所有 Compose 编排集中在 `infra/compose/`。
PostgreSQL、ES 等通用软件没有项目源码，不放在这里。

Docker Compose 中两项服务属于 `local-models` profile：

```bash
docker compose --env-file .env -f infra/compose/project.yml --profile local-models up --build asr vision
```

首次启动需要从模型仓库下载权重。生产环境应预下载并固定模型快照，同时通过内网访问控制保护 8101/8102 端口。
