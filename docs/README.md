# 项目文档导航

本目录记录当前仓库的开发与联调约定。功能与接口以代码为准，终端命令默认从仓库根目录执行。

## 阅读顺序

| 文档 | 主要内容 |
|---|---|
| [第1章：项目技术与功能模块](第01章_项目技术与功能模块介绍.md) | 技术栈、公共能力和全部业务模块 |
| [第2章：公共基座](第02章_公共基座搭建启动与测试.md) | 文件编写顺序、运行组成、启动步骤和测试命令 |
| [第3章：调度交接班](第03章_调度交接班记录模块开发.md) | 录音、转写、校订、摘要和确认 |
| [第4章：生产运行事件](第04章_生产运行事件抽取与归档模块开发.md) | 来源固化、抽取、版本、确认、合并与拆分 |
| [第5章：异常调查与维检工单](第05章_异常分级与维检工单模块开发.md) | 多Agent、MCP、离线评测、人工审核和工单状态机 |
| [第6章：巡检](第06章_巡检图片分析与隐患上报模块开发.md) | 图片、视觉候选、人工复核和事件关联 |
| [第7章：生产报告](第07章_生产运行日报与事件复盘模块开发.md) | 来源汇集、Map-Reduce、版本、审核发布、DOCX和PDF |
| [第8章：企业知识与RAG](第08章_企业知识与RAG模块开发.md) | 解析、意图分段、三路索引、引用问答和评测 |
| [第9章：会议录音与知识发布](第09章_会议录音转写与知识发布.md) | 会议录音、转写校订、纪要、录音治理和知识发布 |
| [第10章：隐患排查治理](第10章_隐患排查治理模块开发.md) | 登记、风险评估、整改、验收、驳回和销项 |
| [项目面试题](项目面试题.md) | 按真实实现组织回答，区分已实现与规划 |

## 关键能力入口

| 能力 | 实现位置 | 对应说明 |
|---|---|---|
| 公共基座 | `app/bootstrap`、`app/shared`、`app/ports`、`app/infrastructure` | 第2章说明编写顺序、启动方法和测试范围 |
| 多智能体事件调查 | `app/modules/operation_event/application/investigation.py` | 第5章介绍五角色LangGraph调查、受控工具循环和Critic修订 |
| 调查断点恢复 | `app/modules/operation_event/application/investigation.py`、`alembic/versions/20260926_0001_initial_schema.py` | 第5章介绍PostgreSQL检查点、任务租约和重试边界 |
| MCP工具服务 | `app/modules/operation_event/api/mcp_server.py` | 第5章介绍`/mcp/`、三个只读工具、认证和数据范围 |
| 智能体离线评测 | `app/modules/operation_event/application/evaluation.py` | 第5章介绍专家标签、指标和评测边界 |
| 会议资料治理 | `app/modules/meeting/` | 第9章介绍纪要、知识撤回同步和录音保留期 |
| 报告导出 | `app/modules/report/application/service.py` | 第7章介绍固定版本的DOCX和PDF导出 |

容器、卷和启动命令见[基础设施部署说明](../infra/README.md)，独立模型服务见[服务说明](../services/README.md)。

## 系统边界

```text
app/        模块化单体：API、业务规则、事务、Worker、RAG与Agent编排
services/   项目自有HTTP服务：ASR、视觉、文档解析、Embedding和重排
infra/      Docker Compose、后端Dockerfile和部署环境模板
```

独立容器是运行边界，不改变代码归属。意图分段、检索融合和引用验证仍在主后端。Elasticsearch负责全文索引，Qdrant负责向量索引，Neo4j负责带来源的关系索引，PostgreSQL保存权限、版本、发布和任务事实。

八个业务模块是`handover`、`meeting`、`operation_event`、`maintenance_order`、`inspection`、`hazard`、`report`和`knowledge`。事件调查采用业务专用多角色协作，RAG问答使用确定的检索与生成流水线。

## 统一约定

- API默认前缀为`/api/v1`，省略前缀的路径会单独说明。字段和枚举以对应`domain/schemas.py`及`/openapi.json`为准。
- `RUN_TASKS_INLINE=false`使用正常Outbox和Worker流程。测试内联执行不能代表消息代理、Beat或真实模型已经验收。
- HTTP请求编号、幂等键、`AsyncJob.id`、业务运行ID和Agent步骤ID具有不同用途。
- 通用中间件不重放敏感响应。已经完成的重复写请求返回`409 REQUEST_ALREADY_COMPLETED`。知识接口依靠资源状态约束重复操作。
- RAG和事件分类接入执行租约，其他任务继续遵守各自的状态、版本和行锁规则。
- 数据库测试夹具会删除目标数据库的`public` Schema。`TEST_DATABASE_URL`必须指向可以丢弃的独立测试数据库。
- `.env`、`.env.knowledge`、真实JWT和API Key不能写入文档或Git。

## 已知限制与验证口径

| 项目 | 当前事实 |
|---|---|
| Agent工具 | 内部调查使用进程内注册函数，`/mcp/`向外部客户端提供三个只读MCP工具 |
| Agent质量分 | 运行时质量分检查结构、引用和Critic结果；专家标签评测单独执行 |
| 图检索 | 实体关系邻接扩展，不包含社区发现和社区摘要型完整GraphRAG |
| RAG全局问题 | 有访问范围和上下文预算，不保证穷尽企业全部资料 |
| RAG评测 | 包含Recall、MRR、nDCG、拒答匹配和延迟，尚未覆盖完整语义正确性 |
| 模型别名 | 固定运行参数，不负责自动部署或切换模型权重 |
| 报告导出 | 支持DOCX和PDF，仍需在目标环境验收字体、下载和版式 |
| 数据库迁移 | 当前只保留`20260926_0001`初始迁移；旧迁移链数据库需要备份并单独迁移 |
| 真实服务验收 | 外部索引、本地模型和真实LLM测试需要显式执行，跳过不表示通过 |

测试结果只代表已覆盖范围。投产前仍需完成真实服务、并发、权限撤销、备份恢复、容量和网络验收。
