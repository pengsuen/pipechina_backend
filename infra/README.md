# 容器与基础设施

所有命令从**仓库根目录**执行，显式传入环境文件，避免移动 Compose 后读取错配置。

```text
infra/
  compose/
    shared.yml       PostgreSQL、MySQL、RabbitMQ、Redis、SeaweedFS
    knowledge.yml    ES、Qdrant、Neo4j，以及 RAG 独立服务的部署编排
    project.yml      可选 ASR、视觉服务、对象存储初始化任务
  docker/
    backend.Dockerfile
  env/
    knowledge.env.example
services/            项目自有服务源码及各自的 Dockerfile、requirements.txt
```

`infra` 管部署，`services` 管项目实现。解析、向量化、重排也有项目维护的 HTTP 服务代码，
所以和音视频服务一样放在 `services`；ES、PostgreSQL 等直接使用官方镜像。
RAG 的六个容器继续按原来的独立 Compose 项目部署，没有合并进后端进程。

## 运行边界

| 能力 | 放置位置 | 原因 |
|---|---|---|
| 版本/权限治理、意图分段、混合检索、引用验证、问答、评测 | 主后端 `app/modules/knowledge` | 属于业务流程，复用权限、事务、任务和模型端口 |
| 多 Agent 调查、工具授权、人工审核 | 主后端事件及维检模块 | 需要业务状态与证据约束，不额外拆微服务 |
| OCR、版面和表格解析 | `services/knowledge_parser`，独立容器 | Docling/OCR 原生依赖、耗时任务与内存隔离 |
| Embedding、重排 | `services/knowledge_models`，两个独立容器 | 常驻模型避免反复加载，可独立分配 CPU/GPU |
| 音频转写、视觉分析 | `services/asr`、`services/vision`，独立容器 | 模型依赖与主后端 Python 运行时隔离 |
| ES、Qdrant、Neo4j、数据库、队列 | `infra/compose` 引用外部镜像 | 通用基础设施，不复制软件源码到业务模块 |

服务既可以在容器里运行，也可以按各自依赖环境直接启动 HTTP 服务；主后端只依赖接口契约。
独立运行不等于代码不属于项目，也不意味着每个业务步骤都拆成容器。

## 启动

首次配置参考根目录 `.env.example`，已有 `.env` 不要覆盖。

```bash
docker compose --env-file .env -f infra/compose/shared.yml up -d postgres rabbitmq redis
docker compose --env-file .env -f infra/compose/project.yml --profile local-models up -d --build asr vision
```

RAG：将 `infra/env/knowledge.env.example` 复制到根目录 `.env.knowledge`，填写密码、API Key
和固定模型版本。该文件被 Git 忽略。后端还需在自己的环境中配置同样的连接参数。

```bash
docker compose --env-file .env.knowledge -f infra/compose/knowledge.yml config --quiet
docker compose --env-file .env.knowledge -f infra/compose/knowledge.yml up -d --build
```

可选对象存储初始化（需已启动 SeaweedFS）：

```bash
docker compose --env-file .env -f infra/compose/shared.yml up -d seaweedfs
docker compose --env-file .env -f infra/compose/project.yml --profile s3-local run --rm object-storage-init
```

单独构建后端镜像：

```bash
docker build -f infra/docker/backend.Dockerfile -t pipechina-backend:dev .
```

