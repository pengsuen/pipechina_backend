# 第8章 企业知识与RAG

[文档导航](README.md) · [上一章：报告](第07章_生产运行日报与事件复盘模块开发.md) · [下一章：会议录音知识化](第09章_会议录音转写与知识发布.md)

## 8.1 业务定位

知识模块面向规程、标准、设备手册、案例、交接班材料和通知等复杂资料，提供可审核、可追溯、可撤回的知识检索与问答。复杂性来自资料结构、权限、版本、生效时间和引用可信度，不来自堆叠Agent。

典型问题包括：某型号设备如何操作、指定历史日期应适用哪个版本、案例中的异常与处理措施有什么关系、不同资料是否存在冲突。没有足够证据时返回未知或拒答，不把模型常识冒充企业制度。

它是现有模块化单体中的knowledge模块，不是另一个独立业务系统。交接班、事件、巡检和报告不会自动全部进入知识库；会议模块提供显式发布适配器，但仍进入知识审核流程。知识内容仍需显式入库、审核和发布。第5章的多Agent调查是另一个业务流程，当前RAG问答不使用多Agent/ReAct循环。

## 8.2 代码与部署边界

| 层次 | 位置或组件 | 责任 |
|---|---|---|
| 业务 | [app/modules/knowledge](../app/modules/knowledge) | 权限、版本、意图分段、检索融合、引用、评测 |
| 装配 | [app/bootstrap/knowledge.py](../app/bootstrap/knowledge.py) | 知识资源构造与生命周期 |
| 文档解析服务 | [services/knowledge_parser](../services/knowledge_parser) | Docling解析、受控转换进程、结构化产物 |
| 模型服务 | [services/knowledge_models](../services/knowledge_models) | Embedding与Rerank HTTP服务；独立运行实例 |
| 部署 | [infra/compose/knowledge.yml](../infra/compose/knowledge.yml) | 解析、模型与三个索引服务的容器编排 |
| 业务事实库 | PostgreSQL | ACL、发布状态、版本、证据、任务和运行记录 |
| 全文索引 | Elasticsearch | 词法/全文召回，不保存项目的向量检索职责 |
| 向量索引 | Qdrant | 稠密向量召回，按模型代次隔离 |
| 关系索引 | Neo4j | 带证据来源的实体关系与邻接扩展 |
| 对象存储 | StorageProvider | 原文件和解析产物等对象 |

三个索引是可重建的派生视图，不是权限事实源。自有服务代码仍在仓库里，Docker只是隔离依赖和资源；业务编排没有被拆进解析或模型容器。

## 8.3 核心数据模型

定义见 [domain/models.py](../app/modules/knowledge/domain/models.py)；输入契约见 [domain/schemas.py](../app/modules/knowledge/domain/schemas.py)。

| 实体 | 含义 |
|---|---|
| KnowledgeBase | 知识空间、组织与读者ACL |
| KnowledgeDocument | 稳定文档身份、编号、类型及文档级ACL |
| KnowledgeVersion | 上传文件、摘要、生效区间、设备型号、审核发布与索引代次 |
| KnowledgeChunk | 证据片段、父级上下文、定位信息 |
| KnowledgeFact | 实体关系、证据ID和原文引句 |
| KnowledgeConversation | 多轮问答的会话身份 |
| KnowledgeRun / KnowledgeRunEvent | 问答结果、证据、检索轨迹和可续读事件 |
| KnowledgeFeedback | 用户反馈，不直接改写模型或知识 |
| KnowledgeEvaluation | 数据集、评测任务和结果 |

文档类型为standard、manual、procedure、case、handover、notice。版本上传上限为200 MiB，要求小写十六进制SHA-256和带时区的生效时间，结束时间必须晚于开始时间。版本可携带设备型号集合及authority元数据；这些字段不是自动证明资料权威性的机制。

## 8.4 版本生命周期

```text
创建版本/上传授权 → 上传原文件 → complete 服务端校验 → uploaded
→ process 创建任务 → processing → review → approved → published
                                          ↘ rejected
处理失败 → failed；允许的状态可校正产物后重新处理
published → withdrawn（退出普通检索）
```

审核通过不等于已经发布。发布要求对应索引准备完成，并检查生效区间等约束。新版本发布时处理旧版本的有效期衔接；旧版本仍可保留published状态支持历史查询，不应简单理解成“一个文档只能有一个published行”。

产物校正支持失败、待审核或驳回等允许状态，且不能有活动任务。它针对结构块内容保存修订，不篡改原文件字节；之后重新处理并清理旧派生结果，不能只修改一个索引后直接宣称三个索引一致。

撤回先改变数据库可见性，不保证立刻物理删除三个外部索引的所有残留。数据库白名单和读取复核负责防止残留证据继续被使用；物理清理与逻辑撤回是不同动作。

## 8.5 解析：保留结构，再确定边界

[pipeline.py](../app/modules/knowledge/application/pipeline.py) 调用解析服务提交文件字节并轮询任务，传递的不是任意本机文件路径。服务使用持久任务记录和受控子进程，限制文件大小、转换时间和输入；业务轮询也有截止时间，不能无限等待。

结构化产物保留块ID、正文、标题、表格及页面/位置等信息。定位能力取决于文件类型与解析结果，不能保证所有格式都有精确页面框。缺少文本的图像块会暴露质量问题，不代表当前已实现所有图表和扫描件的准确理解。

分段见 [chunking.py](../app/modules/knowledge/application/chunking.py)：

1. 先按结构块处理，不把整个文件直接切成等长滑动窗口。
2. case/handover调用文本模型规划意图边界，每批最多30块，规划输入每块最多1200字符；返回的是已有块ID，未知ID不作为合法边界。
3. 模型规划只决定边界，不替换原文。正文保留原始块内容，避免摘要式分块丢失证据。
4. 标题等结构驱动分组；普通文本缓冲约3500字符。
5. 表格按每组15行拆分并重复前两行表头，同时保留父级上下文，降低表头丢失造成的误读。
6. 超过6000字符的块使用句子切分和硬长度兜底。结构/意图分段并不意味着永远不能使用长度保护。

这里的阈值是当前实现常量，不是经过全行业验证的最优参数。意图边界的规划输入会截断，跨批长主题也可能切断，需要用实际文档集评测。

## 8.6 实体关系与证据约束

文本模型从证据中抽取source、relation、target、evidence_id、quote。关系必须对应已有证据，原文引句必须存在，关系两端需要在引句中出现；按受控批次提取，单次输出数量受Schema限制。

Neo4j用于关系扩展，数据库保存规范事实记录。取回图结果后再次核对事实ID、属性与证据，不能把外部索引返回的任意关系当成可信知识。

当前实现是证据约束的实体关系检索，不包含社区发现、社区摘要或完整的分层GraphRAG。关系抽取模型也会漏报、误判，精确引句校验只能排除部分错误。

## 8.7 三路索引与失败恢复

入库任务生成片段、关系、向量及索引代次，在ES、Qdrant、Neo4j建立对应派生数据，并保存处理检查点。任务使用执行租约，结果落库前检查执行令牌，防止失去执行权的旧Worker覆盖新尝试。

PostgreSQL与三个索引没有分布式原子事务。因此需要阶段状态、重试清理、发布门槛和查询时数据库复核共同保证可用性，不能把它写成“四库同一事务”。

故障处理顺序：先查版本当前Job与失败阶段，再查解析/模型/索引服务，最后按平台重试接口创建新尝试。重试在同一数据库事务中重绑业务资源当前Job；不要手改job_id或把failed直接改成published。

## 8.8 配置快照与向量兼容

知识能力别名为knowledge-text、knowledge-parser、knowledge-embedding、knowledge-rerank。无别名时使用启动配置；显式配置却禁用或类型不符时拒绝执行，而不是静默绕过管理员设置。

[configuration.py](../app/modules/knowledge/application/configuration.py) 固化模型名、model_snapshot、维度和参数。同步与Worker按快照构造作用域资源，运行后释放，不把一个任务的配置覆盖到另一个任务的共享客户端上。

别名只选择参数，不负责下载和部署权重。外部服务必须真的提供匹配模型版本。vector/hybrid/global要求查询模型、维度、版本与目标索引代次相容；跨不兼容历史代次需要缩小范围或重建索引，当前没有自动路由到多套历史Embedding模型。

fulltext和graph不需要为查询调用Embedding，也不因向量模型代次不匹配而拒绝。重排、文本规划等其他能力仍可能被调用；“不调用Embedding”不等于“完全不调用模型”。

## 8.9 权限、时态与可见范围

[access.py](../app/modules/knowledge/application/access.py) 综合账号/组织有效性、操作权限、数据范围、知识库及文档ACL、发布状态、生效时间和设备型号确定可用版本。

- 默认只使用当前可见的已发布资料；as_of支持指定历史时点。
- 显式version_ids可选历史版本，不简单套用默认时效过滤，但仍检查发布状态与访问权限。
- 未发布内容走编辑/管理权限，不是普通读者直接可见。
- ACL更新有expected_version，避免静默覆盖并发修改。
- 超过实现允许的版本范围会拒绝并要求缩小范围，不能悄悄截断后假装结果完整。

数据库白名单先限制检索，取回后再核验证据；生成前后和读取历史运行时继续检查访问资格。会话历史不能成为绕过撤权的后门。当前保存的scope_version_ids涵盖运行涉及的可见范围，范围中资料撤权可能使旧回答整体不可读，而不只是删除某条引用。

## 8.10 检索模式

实现见 [retrieval.py](../app/modules/knowledge/application/retrieval.py)。

| mode | 当前路径 | 注意事项 |
|---|---|---|
| fulltext | ES → 重排 | 不生成查询向量 |
| vector | Embedding → Qdrant → 重排 | 要求向量代次兼容 |
| hybrid | ES + Qdrant + 可用实体的关系扩展 → 融合 → 重排 | 默认模式，不保证每次都有图命中 |
| graph | 查询规划 + ES + 图扩展 → 重排 | 不是纯Cypher查询 |
| global | 多路候选后，在许可范围内汇集全部片段 | 有硬范围/上下文限制，不做普通Top-K重排 |

expand_queries可开启查询改写；graph/global自动使用查询规划。规划可给出多个查询和实体；默认非规划实体识别偏向规则匹配，不能等同完整中文实体识别模型。

融合采用RRF，分数贡献为1/(60+rank+1)，先限制到最多60个候选再重排。关系扩展限制跳数与前沿，避免任意遍历企业图谱。普通模式最终条数由limit控制，默认8，范围1–20。

上下文优先使用不超过8000字符的父文本，否则退回片段，并记录完整性信息。普通预算为24000字符，全局为45000字符；global范围超过1000个片段会拒绝，预算不足也可能不能纳入全部文本。字符预算不是精确Token预算。

因此“总结所有制度”不代表无上限读取或精确全量统计；对穷尽性问题，应先确认范围和完整性标记。

## 8.11 引用问答与多轮上下文

[answers.py](../app/modules/knowledge/application/answers.py) 的流程为：权限范围 → 检索证据 → 保存轨迹 → 生成结构化结论 → 引用检查 → 模型支持性复核 → 保存结果。

输出包含claims、conflicts、unknowns、refused。每条claim使用evidence_id和quote，必须匹配实际检索证据及原文。无证据、引用不合法或支持性复核失败时不能继续包装成确定答案。

模型支持性复核仍使用配置的文本Provider，不是独立事实裁判；精确引句存在也不能证明推理必然正确。人工反馈与领域评测仍必要。

多轮问答可使用最近三次成功且仍有权读取的历史运行进行追问改写，之后重新检索，不直接无限拼接全部聊天历史。会话不等于拥有历史资料的永久访问授权。

这是受控RAG流水线，不是模型自由选择工具、反复规划的ReAct循环。复杂调查需要时走第5章，而不是给每次知识问答强加五个角色。

## 8.12 事件、取消、重试与观测

POST /runs创建异步运行；SSE /runs/{run_id}/events提供retrieving、generating、verified_claim、completed等阶段信息，支持Last-Event-ID或after续读。它不是逐Token输出。连接有轮询时限，客户端需要允许断线重连，并处理重新鉴权失败。

取消是协作式请求，外部模型调用不保证瞬间中止。重试创建新Job并重绑同一业务运行；历史事件序号可继续增长，不应把重试当成新会话或清空旧尝试。

Parser、Embedding、Rerank调用通过能力观测记录日志，未知使用量保留null。日志随业务事务提交，回滚时可能一起回滚，不能宣称是独立持久的全量失败审计；费用配置为零也不代表服务商实际免费。

## 8.13 评测与反馈

[evaluation.py](../app/modules/knowledge/application/evaluation.py) 接收development/held_out数据集，每套1–300条问题，每条包含相关证据ID与should_refuse。当前评测模式支持fulltext、vector、hybrid、graph，不包含global。

结果包含Recall、MRR、nDCG、拒答匹配和延迟。相关证据ID必须来自实际入库版本；换了分段或代次后需要检查标注仍有效。拒答匹配不是完整的答案事实正确率，也没有自动完成领域专家验收。

反馈类别包括helpful、wrong_citation、incomplete、outdated、incorrect。反馈只保存用于分析，不自动微调模型、修改ACL或覆盖已发布知识。

生成资料/测试PDF的辅助脚本提供合成夹具，不是实际客户数据或真实企业效果报告。评测报告应说明语料来源、版本、模型快照、模式、标注方法及是否留出测试集。

## 8.14 API导航

以下省略 /api/v1/knowledge前缀，完整契约见 [router.py](../app/modules/knowledge/api/router.py)。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST / GET | /bases | 创建 / 查询知识库 |
| POST | /bases/{base_id}/acl | 更新库ACL |
| GET | /bases/{base_id}/documents | 文档列表 |
| POST | /documents | 创建文档 |
| POST | /documents/{document_id}/acl | 更新文档ACL |
| POST / GET | /documents/{document_id}/versions | 创建 / 查询版本 |
| POST | /versions/{version_id}/uploads:complete | 校验上传 |
| POST | /versions/{version_id}:process | 入库任务，202 |
| GET | /versions/{version_id}/artifact | 解析产物 |
| POST | /versions/{version_id}/artifact:correct | 产物校正 |
| GET | /versions/{version_id}/evidence | 证据片段 |
| POST | /versions/{version_id}:review | 审核 |
| POST | /versions/{version_id}:publish | 发布 |
| POST | /versions/{version_id}:withdraw | 撤回 |
| GET | /versions/{version_id}/original | 原件访问 |
| GET | /versions/{version_id}/diff/{other_id} | 版本差异 |
| POST | /search | 同步检索，不生成完整问答运行 |
| POST / GET | /runs、/runs/{run_id} | 创建问答 / 读取结果 |
| GET | /runs/{run_id}/events | SSE阶段事件 |
| POST | /runs/{run_id}:cancel | 请求取消 |
| POST | /runs/{run_id}/feedback | 反馈 |
| POST | /conversations | 创建会话 |
| GET | /conversations/{conversation_id}/runs | 会话运行历史 |
| POST / GET | /evaluations、/evaluations/{evaluation_id} | 创建评测 / 读取结果 |

检索请求的question必填，支持base_ids、version_ids、equipment_model、as_of、mode、limit、conversation_id和expand_queries。未知输入字段会被拒绝；时间字段需要时区。

## 8.15 启动与验收

完整部署命令与凭据要求统一见 [infra/README.md](../infra/README.md)。从infra/env/knowledge.env.example创建本地 .env.knowledge，填写服务凭据；不要提交真实密钥。Compose的 --env-file只负责该命令环境插值，不会自动给独立启动的后端Python进程加载配置。

Worker消费knowledge_index、knowledge_query、knowledge_eval；解析轮询属于入库任务，不需要虚构独立knowledge_parse执行器。保留maintenance队列和Beat/Outbox发布。容器里的localhost与宿主机不同，后端容器部署必须配置实际可达地址。

验收顺序：

1. 验证数据库迁移、认证、对象存储和任务基础链路。
2. 上传含标题、跨页表格、长段落和案例意图的资料，检查产物、分段与定位。
3. 处理、审核、发布；分别检验全文、向量、混合、图及受限全局问题。
4. 检验版本生效、设备型号、ACL撤销、历史问答重新读取及撤回后的残留索引。
5. 模拟解析/模型/索引失败，确认不可提前发布，并验证重试绑定、租约和旧尝试隔离。
6. 用带真实证据标注的留出集比较召回、排序、引用和拒答，再做人工答案评审。

数据库测试会重建public schema，先配置可丢弃的独立TEST_DATABASE_URL，再按范围运行：

```bash
uv run pytest tests/test_knowledge_foundation.py tests/test_knowledge_quality.py tests/test_knowledge_parser_service.py -q
uv run pytest tests/test_knowledge_business.py tests/test_knowledge_runtime_db.py tests/test_consistency_db.py -q
```

真实ES/Qdrant/Neo4j测试位于test_knowledge_external.py，由RAG_EXTERNAL_TEST=1显式开启；真实模型测试位于test_knowledge_live_models.py，分别由RAG_MODELS_TEST=1、RAG_LLM_TEST=1开启。仅在隔离服务和可丢弃测试数据上运行；真实LLM会产生网络调用及潜在费用。默认跳过不等于这些能力已验收。

## 8.16 当前限制

- 不提供无限上下文、无损全资料统计或完整社区型GraphRAG。
- 不自动在多个不兼容历史向量模型间路由。
- 不保证任意扫描件、图表、复杂跨页表格都能准确解析。
- 不把引用字符串匹配或同模型复核等价为事实正确性证明。
- 不提供四库原子事务、所有外部调用即时取消或全量日志独立落库保证。
- 不自动把其他业务模块的数据同步、审核并发布为知识。

上述边界应纳入验收标准和后续需求清单。
