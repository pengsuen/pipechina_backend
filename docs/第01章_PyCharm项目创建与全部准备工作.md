# 第 1 章 PyCharm 项目创建与全部准备工作

## 1.1 本章目标

本章从一个空目录开始建立可运行、可测试、可提交的 Python 后端工程。完成后应具备以下结果：

- PyCharm 项目目录为 `pipechina_backend`；
- 项目解释器由 `uv` 管理，Python 版本固定为 3.14；
- Git 仓库在创建项目时初始化；
- 项目采用 `app` 作为 Python 源码根包；
- 五个业务模块按功能垂直组织；
- 依赖、代码质量工具、环境变量模板和测试入口全部准备完成；
- FastAPI 存活检查可以运行；
- 完成第一次 Git 提交。

本章只建立工程骨架，不实现数据库、认证、异步任务和业务功能。这些公共能力在第 2 章集中完成。

## 1.2 准备开发环境

建议开发环境如下：

| 软件 | 版本要求 | 用途 |
|---|---:|---|
| PyCharm | 当前稳定版 | 创建和调试项目 |
| Python | `3.14.x` | 主后端运行时 |
| uv | 当前稳定版 | Python、虚拟环境、依赖和锁文件管理 |
| Git | `2.x` | 版本管理 |
| Docker Desktop 或 Docker Engine | 当前稳定版 | PostgreSQL、RabbitMQ、Redis 和本地模型服务 |

在终端执行以下命令，确认工具可用：

```bash
uv --version
git --version
docker --version
docker compose version
```

如系统尚未安装 Python 3.14，可由 uv 安装：

```bash
uv python install 3.14
uv python list
```

`uv python list` 的输出中应存在 3.14 解释器。

## 1.3 在 PyCharm 中创建项目

打开 PyCharm，选择 **New Project**，左侧选择 **Pure Python**。按照下图设置：

![PyCharm 新建项目设置](assets/pycharm_uv_python314_git.png)

各字段填写如下：

| PyCharm 字段 | 设置值 |
|---|---|
| Location | `/Users/peter/PycharmProjects/pipechina_backend` |
| Create Git repository | 勾选 |
| Create a welcome script | 不勾选 |
| Interpreter type | `uv` |
| Python version | `3.14` |
| Path to uv | PyCharm 自动识别的 uv 路径，例如 `/home/peter/.local/bin/uv` |
| Environment location | 项目根目录下的 `.venv` |

上表使用当前 macOS 开发机路径。其他用户或操作系统只需调整父目录，项目目录名保持 `pipechina_backend`。

点击 **Create**。创建完成后，在 PyCharm 底部 Terminal 中执行：

```bash
pwd
python --version
uv run python --version
git status
```

预期结果：

- 当前目录以 `pipechina_backend` 结尾；
- Python 版本为 `3.14.x`；
- Git 输出当前仓库状态，而不是“not a git repository”；
- 项目根目录存在 `.venv`。

## 1.4 建立 Python 版本文件

在项目根目录创建 `.python-version`：

```text
3.14
```

该文件使 uv、命令行和 IDE 使用相同的 Python 主版本，避免开发机解释器漂移。

## 1.5 建立 pyproject.toml

删除 PyCharm 自动创建且无业务意义的示例文件。在项目根目录创建 `pyproject.toml`：

```toml
[build-system]
requires = ["hatchling==1.32.0"]
build-backend = "hatchling.build"

[project]
name = "pipechina-backend"
version = "1.3.0"
description = "油气管网智能生产运营辅助系统后端"
readme = "README.md"
requires-python = ">=3.14,<3.15"
dependencies = [
  "fastapi==0.141.1",
  "uvicorn[standard]==0.52.4",
  "pydantic==2.13.4",
  "pydantic-settings==2.15.0",
  "sqlalchemy[asyncio]==2.0.52",
  "asyncpg==0.31.0",
  "psycopg[binary]==3.3.4",
  "alembic==1.19.1",
  "httpx==0.28.1",
  "PyJWT==2.13.0",
  "cryptography==50.0.0",
  "python-multipart==0.0.32",
  "celery==5.6.3",
  "redis==8.1.0",
  "boto3==1.43.79",
  "langgraph==1.2.11",
  "python-docx==1.2.0",
  "tzdata==2026.3",
  "celery-types>=0.26.0",
  "boto3-stubs~=1.43.79",
]

[dependency-groups]
dev = [
  "aiosqlite==0.22.1",
  "coverage==7.15.4",
  "httpx2==2.12.0",
  "pip-audit==2.10.1",
  "pytest==9.1.1",
  "pytest-asyncio==1.4.0",
  "PyYAML==6.0.3",
  "ruff==0.16.4",
  "mypy==2.3.1",
]

[tool.hatch.build.targets.wheel]
packages = ["app"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-q --disable-warnings --maxfail=1"

[tool.ruff]
target-version = "py314"
line-length = 100
extend-exclude = ["alembic/versions"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]
ignore = ["B008"]

[tool.mypy]
python_version = "3.14"
plugins = ["pydantic.mypy"]
warn_unused_ignores = true
check_untyped_defs = true
no_implicit_optional = true
exclude = ["alembic"]

[[tool.mypy.overrides]]
module = ["boto3", "botocore", "botocore.*", "celery", "celery.*"]
ignore_missing_imports = true

[tool.coverage.run]
source = ["app"]
branch = true

[tool.coverage.report]
fail_under = 65
show_missing = true
skip_covered = true
```

这里同时固定运行依赖和开发工具版本。`uv.lock` 将进一步记录全部传递依赖及文件哈希，使开发、测试和容器构建使用同一套依赖解析结果。

PyCharm 工程目录可以使用 `pipechina_backend`；`pipechina-backend` 是 Python 发行包名称。目录名和发行包名用途不同，保持上述命名即可与交付代码一致。

执行：

```bash
uv lock
uv sync --frozen --group dev
```

执行完成后，项目根目录应出现 `uv.lock`。

## 1.6 建立业务优先的目录结构

在 PyCharm Project 面板中逐级创建目录和空的 `__init__.py`。最终结构如下：

```text
pipechina_backend/
├── app/
│   ├── bootstrap/
│   ├── ports/
│   ├── infrastructure/
│   │   ├── providers/
│   │   └── storage/
│   ├── modules/
│   │   ├── handover/
│   │   ├── operation_event/
│   │   ├── maintenance_order/
│   │   ├── inspection/
│   │   └── report/
│   └── shared/
│       ├── media/
│       ├── platform/
│       └── security/
│           ├── audit/
│           ├── authentication/
│           ├── authorization/
│           └── identity/
├── alembic/
│   └── versions/
├── inference/
│   ├── asr_service/
│   └── vision_service/
├── scripts/
├── tests/
├── .github/
│   └── workflows/
├── .python-version
├── pyproject.toml
└── README.md
```

每个业务模块内部使用同一分层：

```text
handover/
├── api/
├── application/
├── domain/
└── infrastructure/
```

四层职责如下：

- `api`：HTTP 路由、依赖注入和响应模型；
- `application`：用例编排、事务边界和异步任务；
- `domain`：业务实体、状态规则和输入输出模型；
- `infrastructure`：数据库 Repository 等技术实现。

外部模型和文件系统不放进业务模块。业务代码只依赖 `app/ports` 中的接口，具体实现位于 `app/infrastructure`。

## 1.7 建立最小应用

创建 `app/__init__.py`：

```python
__version__ = "1.3.0"
```

创建 `app/main.py`：

下面是第1章阶段使用的最小版本。第2章完成后，当前项目中的 `app/main.py` 会进一步装配数据库、Provider、中间件和全部路由。

```python
from fastapi import FastAPI

from app import __version__


def create_app() -> FastAPI:
    app = FastAPI(
        title="油气管网智能生产运营辅助系统",
        version=__version__,
    )

    @app.get("/health/live", tags=["health"])
    async def health_live() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
```

创建 `tests/test_health.py`：

```python
from fastapi.testclient import TestClient

from app.main import app


def test_health_live() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.3.0"}
```

运行测试：

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy app
```

## 1.8 配置 PyCharm 运行项

选择 **Run → Edit Configurations → Add New → Python**，创建 `API` 配置：

| 字段 | 值 |
|---|---|
| Module name | `uvicorn` |
| Parameters | `app.main:app --host 127.0.0.1 --port 8000 --reload` |
| Working directory | 项目根目录 |
| Python interpreter | 项目 `.venv` |

运行后访问：

```text
http://127.0.0.1:8000/health/live
http://127.0.0.1:8000/docs
```

也可以直接在终端启动：

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## 1.9 建立环境变量模板

创建 `.env.example`。模板中不得写入真实密钥：

```env
APP_ENV=development
POSTGRES_DB=pipechina
POSTGRES_USER=peter
POSTGRES_PASSWORD=123456
RABBITMQ_DEFAULT_USER=peter
RABBITMQ_DEFAULT_PASS=123456
DATABASE_URL=postgresql+asyncpg://peter:123456@127.0.0.1:5432/pipechina
JWT_ISSUER=http://mock-idp:9001
JWT_AUDIENCE=pipechina-backend
JWT_ALGORITHM=RS256
JWT_JWKS_URL=http://127.0.0.1:9001/.well-known/jwks.json
AUTO_CREATE_SCHEMA=false
AUTO_SEED=false
RUN_TASKS_INLINE=false

TEXT_PROVIDER=qwen
DASHSCOPE_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_TEXT_MODEL=qwen3.7-plus

ASR_PROVIDER=local_http
ASR_BASE_URL=http://127.0.0.1:8101
ASR_MODEL=large-v3

VISION_PROVIDER=local_http
VISION_BASE_URL=http://127.0.0.1:8102
VISION_MODEL=openbmb/MiniCPM-V-4.6

STORAGE_PROVIDER=local_filesystem
LOCAL_STORAGE_ROOT=./data/objects
LOCAL_STORAGE_PUBLIC_BASE_URL=http://localhost:8000
LOCAL_STORAGE_SIGNING_SECRET=development-local-storage-secret-change-me

CELERY_BROKER_URL=amqp://peter:123456@127.0.0.1:5672//
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
REDIS_URL=redis://127.0.0.1:6379/0

PROVIDER_TIMEOUT_SECONDS=60
MEDIA_PROVIDER_TIMEOUT_SECONDS=900
PROVIDER_MAX_RETRIES=2
PROVIDER_TRUST_ENV=false
JOB_HEARTBEAT_TIMEOUT_SECONDS=1800
OUTBOX_MAX_ATTEMPTS=8
OUTBOX_BASE_RETRY_SECONDS=5

MAX_AUDIO_BYTES=524288000
MAX_IMAGE_BYTES=20971520
ALLOWED_IMAGE_TYPES=["image/jpeg","image/png","image/webp"]
```

上面是本地开发示例值。实际运行时统一由 `.env` 提供配置；仓库中的 `.env.example` 是权威字段清单，新增配置时应同步更新该文件。

复制开发配置：

```bash
cp .env.example .env
```

`.env` 只保存在开发机，不提交 Git。

## 1.10 配置 Git 忽略规则

创建 `.gitignore`：

```gitignore
.venv/
.env
.idea/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache*/
.coverage
htmlcov/
data/
dist/
build/
*.egg-info/
*.log
.DS_Store
```

检查待提交内容：

```bash
git status --short
```

输出中不应出现 `.env`、`.venv`、`.idea` 或 `data`。

## 1.11 完成第一次提交

```bash
git add .
git commit -m "chore: initialize Python 3.14 uv backend project"
git log --oneline -1
```

## 1.12 本章验收清单

- [ ] PyCharm 项目路径为 `pipechina_backend`；
- [ ] 项目使用 uv 和 Python 3.14；
- [ ] 创建项目时已经勾选 Git；
- [ ] `uv lock` 和 `uv sync --frozen --group dev` 执行成功；
- [ ] 五个业务模块均采用 `api/application/domain/infrastructure` 四层目录；
- [ ] `/health/live` 返回 200；
- [ ] pytest、Ruff 和 mypy 均通过；
- [ ] `.env` 未进入 Git；
- [ ] 第一次提交已经完成。

下一步进入公共基础设施建设。完成第 2 章后，后续五个业务模块只需依赖稳定的数据库、认证、异步任务、模型端口和文件存储能力。
