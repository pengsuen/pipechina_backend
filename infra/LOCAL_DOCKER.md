# 本机Docker操作

以下命令均从仓库根目录执行。Compose读取根目录`.env`，命令中不重复填写账号、密码、模型名称或镜像版本。

## 1. 创建网络

共享基础设施和项目模型服务通过同一个外部网络通信。首次使用时创建一次：

```bash
docker network create shared-infra
```

查看网络：

```bash
docker network inspect shared-infra
```

## 2. 创建数据卷

共享基础设施数据卷：

```bash
docker volume create pg_data
docker volume create mysql_data
docker volume create rabbitmq_data
docker volume create redis_data
docker volume create seaweed_data
docker volume create es_data
docker volume create qdrant_data
docker volume create neo4j_data
```

项目模型和任务数据卷：

```bash
docker volume create pipechina_knowledge_runtime_data
docker volume create pipechina_knowledge_runtime_models
docker volume create pipechina_knowledge_runtime_ocr
docker volume create pipechina_asr_models
docker volume create pipechina_vision_models
```

`docker volume create`可以重复执行，已存在的数据卷不会被清空。

## 3. 创建并启动容器

后端日常运行需要PostgreSQL、RabbitMQ、Redis和三个知识索引：

```bash
docker compose --env-file .env -f infra/compose/shared.yml up -d \
  postgres rabbitmq redis elasticsearch qdrant neo4j
```

需要MySQL或S3兼容对象存储时单独启动：

```bash
docker compose --env-file .env -f infra/compose/shared.yml up -d mysql
docker compose --env-file .env -f infra/compose/shared.yml up -d seaweedfs
```

创建并启动当前项目固定使用的Knowledge Runtime：

```bash
docker compose --env-file .env -f infra/compose/project.yml up -d --build knowledge-runtime
```

同时创建并启动Knowledge Runtime、ASR和Vision：

```bash
docker compose --env-file .env -f infra/compose/project.yml \
  --profile local-models up -d --build
```

使用SeaweedFS时初始化项目Bucket：

```bash
docker compose --env-file .env -f infra/compose/project.yml \
  --profile s3-local run --rm object-storage-init
```

## 4. 查看和停止容器

```bash
docker compose --env-file .env -f infra/compose/shared.yml ps
docker compose --env-file .env -f infra/compose/project.yml --profile local-models ps
```

停止但保留容器和数据：

```bash
docker compose --env-file .env -f infra/compose/shared.yml stop
docker compose --env-file .env -f infra/compose/project.yml --profile local-models stop
```

删除容器但保留数据卷：

```bash
docker compose --env-file .env -f infra/compose/shared.yml down
docker compose --env-file .env -f infra/compose/project.yml --profile local-models down
```

## 5. 清空数据卷

清空数据卷会永久删除其中的数据。应先停止并删除使用这些数据卷的容器，再删除数据卷；需要继续运行时重新创建数据卷并启动容器。

清空全部共享基础设施数据：

```bash
docker compose --env-file .env -f infra/compose/shared.yml down
docker volume rm \
  pg_data mysql_data rabbitmq_data redis_data seaweed_data \
  es_data qdrant_data neo4j_data
```

清空当前项目的解析任务、模型缓存和OCR数据：

```bash
docker compose --env-file .env -f infra/compose/project.yml --profile local-models down
docker volume rm \
  pipechina_knowledge_runtime_data \
  pipechina_knowledge_runtime_models \
  pipechina_knowledge_runtime_ocr \
  pipechina_asr_models \
  pipechina_vision_models
```

只清空一个组件时，删除对应数据卷即可。例如清空Elasticsearch数据：

```bash
docker compose --env-file .env -f infra/compose/shared.yml stop elasticsearch
docker compose --env-file .env -f infra/compose/shared.yml rm -f elasticsearch
docker volume rm es_data
docker volume create es_data
docker compose --env-file .env -f infra/compose/shared.yml up -d elasticsearch
```

删除全部容器和数据卷后，如果本机不再运行这些服务，可以删除共享网络：

```bash
docker network rm shared-infra
```
