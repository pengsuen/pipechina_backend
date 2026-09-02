from dataclasses import dataclass

# 集中维护权限代码，避免路由和服务中散落字符串常量。


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    """描述权限目录中的单项权限元数据。"""

    code: str
    module: str
    name: str
    risk_level: str = "normal"


class Permissions:
    """集中定义系统使用的权限代码常量。"""

    ALL = "*"

    HANDOVER_READ = "handover:read"
    HANDOVER_CREATE = "handover:create"
    HANDOVER_EDIT = "handover:edit"
    HANDOVER_PROCESS = "handover:process"
    HANDOVER_CONFIRM = "handover:confirm"
    HANDOVER_DELETE = "handover:delete"

    EVENT_READ = "event:read"
    EVENT_EXTRACT = "event:extract"
    EVENT_EDIT = "event:edit"
    EVENT_REVIEW = "event:review"
    EVENT_TRANSFORM = "event:transform"
    EVENT_CLASSIFY = "event:classify"

    INSPECTION_READ = "inspection:read"
    INSPECTION_CREATE = "inspection:create"
    INSPECTION_UPLOAD = "inspection:upload"
    INSPECTION_ANALYZE = "inspection:analyze"
    INSPECTION_REVIEW = "inspection:review"
    INSPECTION_LINK = "inspection:link"
    INSPECTION_WORKFLOW = "inspection:workflow"
    INSPECTION_DELETE = "inspection:delete"

    MAINTENANCE_READ = "maintenance:read"
    MAINTENANCE_WRITE = "maintenance:write"
    MAINTENANCE_REVIEW = "maintenance:review"
    MAINTENANCE_APPROVE = "maintenance:approve"
    MAINTENANCE_DISPATCH = "maintenance:dispatch"
    MAINTENANCE_EXECUTE = "maintenance:execute"
    MAINTENANCE_CLOSE = "maintenance:close"
    MAINTENANCE_ATTACHMENT = "maintenance:attachment"

    REPORT_READ = "report:read"
    REPORT_CREATE = "report:create"
    REPORT_GENERATE = "report:generate"
    REPORT_EDIT = "report:edit"
    REPORT_REVIEW = "report:review"
    REPORT_PUBLISH = "report:publish"
    REPORT_WITHDRAW = "report:withdraw"
    REPORT_EXPORT = "report:export"

    JOB_READ = "job:read"
    JOB_CANCEL = "job:cancel"
    JOB_RETRY = "job:retry"

    ADMIN_USER = "admin:user"
    ADMIN_ORG = "admin:org"
    ADMIN_ROLE = "admin:role"
    ADMIN_PERMISSION = "admin:permission"
    ADMIN_AUDIT = "admin:audit"
    ADMIN_MODEL = "admin:model"
    ADMIN_PROMPT = "admin:prompt"
    ADMIN_CONFIG = "admin:config"


GLOBAL_ONLY_PERMISSIONS = {
    Permissions.ADMIN_ROLE,
    Permissions.ADMIN_MODEL,
    Permissions.ADMIN_PROMPT,
    Permissions.ADMIN_CONFIG,
}


_NAMES = {
    Permissions.ALL: ("system", "系统全部能力", "critical"),
    Permissions.HANDOVER_READ: ("handover", "查看交接班记录", "normal"),
    Permissions.HANDOVER_CREATE: ("handover", "创建交接班记录", "normal"),
    Permissions.HANDOVER_EDIT: ("handover", "编辑交接班内容", "high"),
    Permissions.HANDOVER_PROCESS: ("handover", "转写和生成交接摘要", "normal"),
    Permissions.HANDOVER_CONFIRM: ("handover", "确认交接班记录", "high"),
    Permissions.HANDOVER_DELETE: ("handover", "删除交接班记录", "high"),
    Permissions.EVENT_READ: ("event", "查看生产事件", "normal"),
    Permissions.EVENT_EXTRACT: ("event", "抽取生产事件", "normal"),
    Permissions.EVENT_EDIT: ("event", "编辑生产事件", "high"),
    Permissions.EVENT_REVIEW: ("event", "审核生产事件", "high"),
    Permissions.EVENT_TRANSFORM: ("event", "重抽取、合并和拆分事件", "high"),
    Permissions.EVENT_CLASSIFY: ("event", "执行异常分级", "high"),
    Permissions.INSPECTION_READ: ("inspection", "查看巡检记录", "normal"),
    Permissions.INSPECTION_CREATE: ("inspection", "创建巡检记录", "normal"),
    Permissions.INSPECTION_UPLOAD: ("inspection", "上传巡检图片", "normal"),
    Permissions.INSPECTION_ANALYZE: ("inspection", "分析巡检图片", "normal"),
    Permissions.INSPECTION_REVIEW: ("inspection", "审核巡检隐患", "high"),
    Permissions.INSPECTION_LINK: ("inspection", "关联巡检隐患", "high"),
    Permissions.INSPECTION_WORKFLOW: ("inspection", "启动隐患处置流程", "high"),
    Permissions.INSPECTION_DELETE: ("inspection", "删除巡检图片", "high"),
    Permissions.MAINTENANCE_READ: ("maintenance", "查看维检工单", "normal"),
    Permissions.MAINTENANCE_WRITE: ("maintenance", "编辑和提交维检工单", "normal"),
    Permissions.MAINTENANCE_REVIEW: ("maintenance", "复核异常分类", "high"),
    Permissions.MAINTENANCE_APPROVE: ("maintenance", "审批维检工单", "high"),
    Permissions.MAINTENANCE_DISPATCH: ("maintenance", "派发维检工单", "high"),
    Permissions.MAINTENANCE_EXECUTE: ("maintenance", "执行和解决维检工单", "normal"),
    Permissions.MAINTENANCE_CLOSE: ("maintenance", "关闭或取消维检工单", "high"),
    Permissions.MAINTENANCE_ATTACHMENT: ("maintenance", "上传维检附件", "normal"),
    Permissions.REPORT_READ: ("report", "查看生产报告", "normal"),
    Permissions.REPORT_CREATE: ("report", "创建生产报告", "normal"),
    Permissions.REPORT_GENERATE: ("report", "生成生产报告", "normal"),
    Permissions.REPORT_EDIT: ("report", "编辑生产报告", "high"),
    Permissions.REPORT_REVIEW: ("report", "审核生产报告", "high"),
    Permissions.REPORT_PUBLISH: ("report", "发布生产报告", "critical"),
    Permissions.REPORT_WITHDRAW: ("report", "撤回生产报告", "critical"),
    Permissions.REPORT_EXPORT: ("report", "导出生产报告", "normal"),
    Permissions.JOB_READ: ("job", "查看异步任务", "normal"),
    Permissions.JOB_CANCEL: ("job", "取消异步任务", "high"),
    Permissions.JOB_RETRY: ("job", "重试异步任务", "high"),
    Permissions.ADMIN_USER: ("admin", "管理用户", "critical"),
    Permissions.ADMIN_ORG: ("admin", "管理组织", "critical"),
    Permissions.ADMIN_ROLE: ("admin", "管理角色", "critical"),
    Permissions.ADMIN_PERMISSION: ("admin", "分配和撤销权限", "critical"),
    Permissions.ADMIN_AUDIT: ("admin", "查看安全审计", "high"),
    Permissions.ADMIN_MODEL: ("admin", "管理模型配置", "critical"),
    Permissions.ADMIN_PROMPT: ("admin", "管理提示词", "critical"),
    Permissions.ADMIN_CONFIG: ("admin", "管理业务配置", "critical"),
}

PERMISSION_CATALOG = {
    code: PermissionSpec(code=code, module=module, name=name, risk_level=risk)
    for code, (module, name, risk) in _NAMES.items()
}

BUILTIN_ROLES: dict[str, tuple[str, set[str]]] = {
    "system_administrator": ("系统管理员", {Permissions.ALL}),
    "security_auditor": (
        "安全审计员",
        {
            Permissions.ADMIN_AUDIT,
            Permissions.HANDOVER_READ,
            Permissions.EVENT_READ,
            Permissions.INSPECTION_READ,
            Permissions.MAINTENANCE_READ,
            Permissions.REPORT_READ,
            Permissions.JOB_READ,
        },
    ),
    "dispatcher": (
        "生产调度员",
        {
            Permissions.HANDOVER_READ,
            Permissions.HANDOVER_CREATE,
            Permissions.HANDOVER_EDIT,
            Permissions.HANDOVER_PROCESS,
            Permissions.HANDOVER_CONFIRM,
            Permissions.EVENT_READ,
            Permissions.EVENT_EXTRACT,
            Permissions.EVENT_EDIT,
            Permissions.EVENT_REVIEW,
            Permissions.EVENT_TRANSFORM,
            Permissions.EVENT_CLASSIFY,
            Permissions.MAINTENANCE_READ,
            Permissions.REPORT_READ,
        },
    ),
    "inspection_operator": (
        "巡检作业员",
        {
            Permissions.INSPECTION_READ,
            Permissions.INSPECTION_CREATE,
            Permissions.INSPECTION_UPLOAD,
            Permissions.INSPECTION_ANALYZE,
        },
    ),
    "maintenance_executor": (
        "维检执行人",
        {
            Permissions.MAINTENANCE_READ,
            Permissions.MAINTENANCE_EXECUTE,
            Permissions.MAINTENANCE_ATTACHMENT,
        },
    ),
    "report_reviewer": (
        "生产报告审核员",
        {
            Permissions.REPORT_READ,
            Permissions.REPORT_REVIEW,
            Permissions.REPORT_PUBLISH,
            Permissions.REPORT_WITHDRAW,
            Permissions.REPORT_EXPORT,
        },
    ),
}


def validate_permission_codes(codes: set[str]) -> None:
    """检查权限代码是否全部存在于权限目录。"""
    unknown = sorted(codes - PERMISSION_CATALOG.keys())  # 找出未登记的权限
    if unknown:
        raise ValueError(f"unknown permission codes: {', '.join(unknown)}")
