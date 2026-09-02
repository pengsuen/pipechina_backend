# 国家油气管网智能生产运营辅助系统后端 V1.3

V1.3 是 Python 3.14 模块化单体后端。代码按业务功能垂直组织，并把文字模型、语音识别、视觉模型和对象存储拆成四套稳定端口。当前默认使用：

- 文字：阿里云百炼千问 `qwen3.7-plus`（远程 API）。
- 语音：本地 faster-whisper `large-v3`。
- 视觉：本地 MiniCPM-V 4.6。
- 文件：本地私有文件系统；以后可直接切换 AWS S3。

不使用 RAG、向量数据库或 Elasticsearch，也不连接或控制 SCADA。

## 1. 代码结构

```text
app/
├── modules/
│   ├── handover/              # 交接班录音、转写、校订和交接重点
│   ├── operation_event/       # 生产运行事件抽取、版本和确认
│   ├── maintenance_order/     # 异常分级、人工审核和工单状态机
│   ├── inspection/            # 人工上传巡检图片、隐患候选和人工复核
│   └── report/                # 日报/复盘、审核、发布和 DOCX 导出
│       ├── api/
│       ├── application/
│       ├── domain/
│       └── infrastructure/
├── ports/                     # 业务层唯一允许依赖的外部能力接口
│   ├── text.py
│   ├── speech.py
│   ├── vision.py
│   └── storage.py
├── infrastructure/
│   ├── providers/             # 千问、OpenAI、本地/自训 HTTP、Fake 适配器
│   └── storage/               # 本地文件、AWS/S3、内存测试适配器
├── shared/
│   ├── security/
│   │   ├── identity/         # 数据库用户和组织身份
│   │   ├── authentication/   # JWT 验签并解析数据库身份（AuthN）
│   │   ├── authorization/    # 角色、权限和数据范围决策（AuthZ）
│   │   └── audit/            # 安全审计上下文
│   └── ...                   # Outbox、任务和传输入口
└── bootstrap/                 # 配置及 Provider 装配

inference/
├── asr_service/               # 独立 faster-whisper 服务
└── vision_service/            # 独立 MiniCPM-V 服务

dev/mock_idp/                  # 仅供开发/测试使用的独立外部 JWT 签发服务
```

`tests/test_architecture.py` 会阻止业务模块绕过 `ports` 直接导入外部实现，也会阻止项目重新退化为一个大而全的 `AIProvider`。

## 2. 兼容基线

- 主后端：CPython `>=3.14,<3.15`；容器固定 `3.14.7-slim-trixie`。
- PostgreSQL `18.6`。
- FastAPI `0.141.1`、Pydantic `2.13.4`、SQLAlchemy `2.0.52`。
- Celery `5.6.3`、RabbitMQ `4.3.5`、Redis `8.10.1`。
- LangGraph `1.2.11`。
- 本地 ASR/视觉运行时单独使用 Python 3.13 容器，避免 PyTorch、CTranslate2、CUDA 等原生依赖影响 Python 3.14 主后端。

主后端所有直接和传递依赖固定在 `pyproject.toml` 与 `uv.lock`。本地推理服务各自拥有独立依赖文件。

## 3. PyCharm 本机启动

### 3.1 准备配置

```bash
cp .env.example .env
```

在 `.env` 中只需先填写：

```env
DASHSCOPE_API_KEY=你的百炼API密钥
```

默认的 `QWEN_BASE_URL` 使用百炼公共 OpenAI 兼容地址，不要求额外填写 Workspace ID。

### 3.2 启动基础设施

本地开发使用由开发机统一管理的共享 PostgreSQL、RabbitMQ、Redis 和 SeaweedFS 容器。这些容器不属于 PipeChina Compose 项目；容器已创建但尚未运行时，执行：

```bash
docker start postgres rabbitmq redis seaweedfs
docker ps
```

项目从 PyCharm 或宿主机启动，所有连接参数由 `.env` 提供，通过映射端口访问 `127.0.0.1`。不要为日常本地开发执行 `docker compose up -d postgres rabbitmq redis`，否则会与共享容器的端口冲突。

Mock IdP 作为项目开发进程单独启动：

```bash
uv run uvicorn dev.mock_idp.main:app --host 127.0.0.1 --port 9001 --reload
```

### 3.3 创建 Python 3.14 环境

```bash
uv python install 3.14
uv sync --frozen --group dev
uv run alembic upgrade head
uv run python scripts/bootstrap_mock_idp_users.py
```

Alembic、初始化脚本、API 和 Celery 均自动读取 `.env`，无需在命令行重复填写 URL。

PyCharm Interpreter 选择项目内 `.venv`。API 运行配置：

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Celery Worker 运行配置：

```bash
uv run celery -A app.bootstrap.celery_app:celery_app worker -l INFO -Q audio,ai_text,vision,report,maintenance
```

Celery Beat 运行配置：

```bash
uv run celery -A app.bootstrap.celery_app:celery_app beat -l INFO
```

### 3.4 启动本地语音和视觉模型

```bash
docker compose --profile local-models up -d --build asr vision
```

首次启动会下载 `large-v3` 和 MiniCPM-V 4.6 权重。因此，“只填千问 Key”足以启动文字能力，但要实际处理音频和图片，还必须启动这两个本地推理服务并具备相应磁盘、内存或 GPU 资源。

接口文档：`http://localhost:8000/docs`。

主后端不签发 JWT，也不保存 JWT 私钥或共享密钥。开发环境的 JWT 由独立的
`mock-idp` 进程使用 RS256 私钥签发，主后端只通过 JWKS 公钥验证。获取开发令牌：

```bash
curl -X POST http://127.0.0.1:9001/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=password&username=admin&password=123456'
```

开发账号如下：

| 用户名 | 密码 | 应用状态 | 数据库权限 |
|---|---|---|---|
| `admin` | `123456` | 启用 | 全局系统管理员 |
| `peter` | `123456` | 启用 | 本组织生产调度员 |
| `tom` | `123456` | 停用 | 无；即使外部认证成功，后端仍返回 `ACCOUNT_DISABLED` |

这些固定密码和 Mock IdP 只能用于本地开发及自动化测试，不得部署到生产环境。

### 3.5 初始化生产权限管理员

生产环境必须接入真实外部 IdP 的 HTTPS issuer 和 JWKS 地址。主后端只把 JWT
当作已签名的外部身份凭据；用户、角色、权限和数据范围均从数据库读取，JWT
中自带的 `roles`、`permissions` 或 `org_scope` 不参与授权。首次部署在迁移完成后执行一次：

```bash
uv run python scripts/bootstrap_security.py \
  --issuer "你的 JWT issuer" \
  --subject "管理员 JWT 的 sub" \
  --username admin \
  --display-name "系统管理员" \
  --organization-code ROOT \
  --organization-name "总部"
```

脚本创建数据库用户、根组织和全局系统管理员角色。后续通过 `/api/v1/admin/access/*` 管理组织、用户、角色、授权期限、数据范围和撤销原因；所有授权变更都会进入审计日志。

## 4. 全 Docker 启动

```bash
cp .env.example .env
# 编辑 .env，填写 DASHSCOPE_API_KEY
docker compose --profile local-models up --build
```

不需要立即测试音频和图片时，可先执行：

```bash
docker compose up --build
```

这会启动 API、Mock IdP、PostgreSQL、RabbitMQ、Redis、Worker、Beat，执行数据库迁移，
并幂等初始化 `admin`、`peter`、`tom` 三个开发账号，但不会下载本地模型。

## 5. 切换模型和存储

业务代码不需要修改，只改环境变量：

| 能力 | 当前默认 | 可替换值 |
|---|---|---|
| 文字 | `TEXT_PROVIDER=qwen` | `fake`、`openai`、`custom_http` |
| 语音 | `ASR_PROVIDER=local_http` | `fake`、`openai`、`custom_http` |
| 视觉 | `VISION_PROVIDER=local_http` | `fake`、`custom_http` |
| 存储 | `STORAGE_PROVIDER=local_filesystem` | `memory`、`s3` |

例如将文字和语音切到 OpenAI API：

```env
TEXT_PROVIDER=openai
ASR_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_TEXT_MODEL=...
OPENAI_ASR_MODEL=...
```

这里使用的是 OpenAI API，不是 ChatGPT 网页订阅。

## 6. 接入未来自训模型

推荐把自训模型部署成独立 HTTP 推理服务：

- 文字：OpenAI 兼容 `POST /v1/chat/completions`，支持 JSON Schema 结构化输出。
- ASR：multipart `POST /v1/transcriptions`，返回 `full_text`、`duration_ms` 和 `segments`。
- 视觉：multipart `POST /v1/vision/analyze`，返回 `{"findings": [...]}`。

然后设置：

```env
TEXT_PROVIDER=custom_http
CUSTOM_TEXT_BASE_URL=http://internal-text-server/v1
CUSTOM_TEXT_MODEL=company-text-v1

ASR_PROVIDER=custom_http
ASR_BASE_URL=http://internal-asr-server
ASR_MODEL=company-asr-v1

VISION_PROVIDER=custom_http
VISION_BASE_URL=http://internal-vision-server
VISION_MODEL=company-vision-v1
```

业务模块只接收统一的 `MediaRef` 和结构化结果，不知道文件来自本地还是 S3，也不知道推理来自本机、OpenAI 或自训模型。

## 7. 切换 AWS S3

参考 `.env.aws.example`：

```env
STORAGE_PROVIDER=s3
S3_ENDPOINT_URL=
S3_PUBLIC_ENDPOINT_URL=
S3_BUCKET=pipechina-production
S3_REGION=us-east-1
S3_ADDRESSING_STYLE=auto
```

在 ECS、EC2 或 EKS 上优先使用 IAM Role，保持 `S3_ACCESS_KEY` 和 `S3_SECRET_KEY` 为空。项目会使用 boto3 默认凭证链。桶本身应由 Terraform/CloudFormation 等基础设施代码创建；`ensure_bucket` 只用于本地 S3 兼容环境。

本地需要演练 S3 适配器时，可把 `.env` 改成 SeaweedFS 配置，并执行：

```bash
docker compose --profile s3-local up -d seaweedfs object-storage-init
```

## 8. 文件隐私边界

- 本地存储文件不会因为调用千问文字模型而自动上传到百炼。
- 音频由本地 ASR 读取；图片由本地视觉服务读取。
- 语音转写后的文字、事件原文和报告来源会在对应文字任务中发送给千问。
- 如果切换远程 ASR，音频会发送给相应远程供应商。
- 如果未来加入远程视觉适配器，图片才会发送给相应供应商。

## 9. 验证

默认单元和 API 测试为每个测试创建独立的临时 SQLite 数据库，不读写本机长期运行的 `pipechina` 开发库。CI 另外创建一次性 PostgreSQL `pipechina_ci` 实例，用于验证 Alembic 迁移和 PostgreSQL 模式。

```bash
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy app scripts
uv run python -m compileall -q app alembic tests scripts inference
uv run coverage run -m pytest -W error
uv run coverage report --fail-under=65
uv run pip-audit --local --skip-editable
```

默认测试使用 Fake Provider、SQLite 和内存存储，不调用收费 API，也不下载模型。真实百炼、本地模型和 AWS 联调均设计为显式的部署后冒烟测试。
