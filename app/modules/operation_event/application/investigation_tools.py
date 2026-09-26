"""In-process, read-only investigation tools (not an MCP transport)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.handover.domain.models import ManualOperationRecord, ManualOperationRecordVersion
from app.modules.maintenance_order.domain.models import WorkOrder
from app.modules.operation_event.domain.agent_models import AgentEvidence, AgentRun, AgentTask
from app.modules.operation_event.domain.models import ProductionEvent, ProductionEventVersion
from app.shared.errors import ConflictError
from app.shared.security.authorization.dependencies import data_scope_clause
from app.shared.security.authorization.permissions import Permissions
from app.shared.security.authorization.schemas import CurrentUser

InvestigationHandler = Callable[
    [str], Awaitable[list[dict]]
]  # 接收查询词并异步返回业务记录的处理函数类型


@dataclass(frozen=True)
class InvestigationTool:
    """声明一个调查工具的来源标签、名称、只读属性和处理函数。"""

    server: str  # 工具来源标签
    name: str  # 工具白名单名称
    read_only: bool  # 只读标记，注册时拒绝写工具
    handler: InvestigationHandler  # 服务端绑定的异步处理函数


class InvestigationToolRegistry:
    """维护进程内只读工具白名单，并将工具返回内容保存为调查证据。"""

    def __init__(self) -> None:
        """初始化进程内调查工具映射。"""
        self._tools: dict[str, InvestigationTool] = {}  # 保存仅由服务端注册的工具映射

    def register(self, tool: InvestigationTool) -> None:
        """只允许注册只读工具，并按工具名保存处理函数。"""
        if not tool.read_only:  # 注册阶段拒绝具有写入能力的工具
            raise ConflictError("write investigation tools require an approval capability")
        self._tools[tool.name] = tool  # 以工具名注册，同名注册会替换原处理函数

    def list_tools(self) -> list[dict]:
        """返回可向模型公开的工具元信息，不暴露处理函数或数据库连接。"""
        return [
            {"server": item.server, "name": item.name, "read_only": item.read_only}
            for item in self._tools.values()
        ]

    async def call(
        self,
        session: AsyncSession,
        *,
        run: AgentRun,
        task: AgentTask,
        tool_name: str,
        query: str,
    ) -> list[AgentEvidence]:
        """校验工具白名单，执行限量查询并保存带哈希的证据快照。"""
        tool = self._tools.get(tool_name)
        if tool is None or not tool.read_only:  # 调用阶段再次校验工具白名单与只读属性
            raise ConflictError("investigation tool is not allowed", tool=tool_name)
        rows = await tool.handler(query[:100])  # 限制查询词长度后调用服务端绑定的函数
        evidence: list[AgentEvidence] = []
        for row in rows[:5]:  # 每次工具调用最多保存五条证据
            encoded = json.dumps(
                row, ensure_ascii=False, sort_keys=True
            ).encode()  # 稳定排序后编码，使同内容得到一致哈希
            item = AgentEvidence(
                run_id=run.id,  # 所属调查运行标识
                task_id=task.id,  # 所属角色任务标识
                source_server=tool.server,  # 证据来源标签，不表示 MCP 服务
                source_resource=str(row.get("record_id") or row.get("id")),  # 来源资源标识
                source_version=str(
                    row.get("version_id") or row.get("version") or ""
                ),  # 证据对应的来源版本
                content=row,  # 取证时保存的内容快照
                content_hash=hashlib.sha256(encoded).hexdigest(),  # 内容摘要，用于核对证据内容
                access_scope={
                    "organization_unit_id": str(run.organization_unit_id)
                },  # 取证时记录的访问范围
            )
            session.add(item)  # 将当前记录加入当前事务
            evidence.append(item)  # 收集本次保存的证据，供调查循环累积
        await session.flush()  # 将待写对象发送到数据库以取得标识，不提交事务
        return evidence  # 返回已持久化的工具证据


async def _read_business_tool(
    session: AsyncSession,
    user: CurrentUser,
    event: ProductionEvent,
    action: str,
    query: str,
) -> list[dict]:
    """按查询类型读取同组织且在用户权限范围内的业务证据。"""
    model: Any
    if action == "search_events":
        model, permission = ProductionEvent, Permissions.EVENT_READ
        statement = select(model).where(
            model.business_status == "confirmed",  # 历史事件证据仅取已确认记录
            model.id != event.id,  # 排除当前事件，其固定版本已作为初始证据
            model.title.contains(
                query, autoescape=True
            ),  # 按标题匹配并转义通配符，避免扩大检索范围
        )
    elif action == "search_work_orders":
        model, permission = WorkOrder, Permissions.MAINTENANCE_READ
        statement = select(model).where(model.title.contains(query, autoescape=True))
    elif action == "search_handover":
        model, permission = ManualOperationRecord, Permissions.HANDOVER_READ
        statement = (
            select(model)
            .join(
                ManualOperationRecordVersion,
                ManualOperationRecordVersion.id == model.current_version_id,
            )
            .where(ManualOperationRecordVersion.content.contains(query, autoescape=True))
        )
    else:
        raise ConflictError("unknown investigation tool")
    statement = (
        statement.where(
            model.organization_unit_id
            == event.organization_unit_id,  # 工具查询始终限定在目标事件所属组织
            data_scope_clause(  # 在 SQL 中约束用户可见的数据范围
                user,
                permission,
                model.organization_unit_id,
                owner_column=model.created_by,
                assignee_column=WorkOrder.assignee_id if model is WorkOrder else None,
            ),
        )
        .order_by(model.created_at.desc(), model.id)  # 按创建时间取最新记录，并用标识稳定排序
        .limit(5)  # 数据库层最多取五条业务记录
    )
    observations: list[dict] = []
    for row in (await session.scalars(statement)).all():
        item = {"id": f"{action}:{row.id}", "record_id": str(row.id)}
        if isinstance(row, ProductionEvent):
            version = await session.get(ProductionEventVersion, row.current_version_id)
            item.update(
                title=row.title,  # 事件或条目的标题
                status=row.business_status,  # 来源事件的业务状态
                version_id=str(row.current_version_id),
                text=version.description[:2000]
                if version
                else "",  # 截断历史事件正文以控制模型上下文
            )
        elif isinstance(row, WorkOrder):
            item.update(
                title=row.title,  # 事件或条目的标题
                status=row.status,  # 来源工单的流转状态
                text=row.description[:2000],  # 截断工单描述以控制模型上下文
                version=str(row.version),  # 所属记录内的版本号
                event_id=str(row.event_id),  # 关联生产事件标识
            )
        else:
            manual_version = await session.get(ManualOperationRecordVersion, row.current_version_id)
            item.update(
                version_id=str(row.current_version_id),
                text=manual_version.content[:2000]
                if manual_version
                else "",  # 截断人工记录正文，缺少版本时返回空串
            )
        observations.append(item)  # 汇总带来源标识的业务内容
    return observations  # 返回已过滤并截断的业务证据


def build_investigation_tools(
    session: AsyncSession, user: CurrentUser, event: ProductionEvent
) -> InvestigationToolRegistry:
    """为当前用户和目标事件绑定三个只读业务查询工具。"""
    registry = InvestigationToolRegistry()
    mapping = {
        "search_events": "event-tools",
        "search_work_orders": "work-order-tools",
        "search_handover": "handover-tools",
    }
    for name, server in mapping.items():

        async def handler(query: str, action: str = name) -> list[dict]:
            """使用创建闭包时绑定的动作名称查询证据，避免循环变量串用。"""
            return await _read_business_tool(session, user, event, action, query)

        registry.register(
            InvestigationTool(server=server, name=name, read_only=True, handler=handler)
        )
    return registry  # 返回绑定当前用户与事件的工具注册表
