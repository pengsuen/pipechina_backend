# 2.7 身份认证与数据库权限系统

## 2.7.1 本章目标

本章专门讲解项目当前的身份认证与权限系统。
本章对应的核心代码目录是：

```text
app/shared/security/
├── identity/                  # 数据库用户与组织身份
├── authentication/           # 外部 JWT 验签，AuthN
├── authorization/            # 角色、权限和数据范围，AuthZ
├── audit/                    # 请求追踪上下文
└── router.py                 # GET /api/v1/auth/me

dev/mock_idp/                 # 仅供开发和测试使用的独立身份提供方
scripts/bootstrap_mock_idp_users.py
scripts/bootstrap_security.py
```

### 2.7.1.1 IdP 是什么

IdP 是 **Identity Provider** 的缩写，中文通常称为“身份提供方”或“身份认证平台”。它是专门负责确认用户身份的系统。

IdP 一般负责：

- 保存和验证用户密码。
- 处理登录、多因素认证和单点登录。
- 用私钥签发 JWT。
- 通过 JWKS 公布验证 JWT 所需的公钥。

在本项目中，关系可以理解为：

```text
用户提交账号和密码
    -> IdP 验证身份并签发 JWT
    -> 用户携带 JWT 请求 PipeChina 主后端
    -> 主后端使用 IdP 公布的公钥验证 JWT
    -> 主后端查询自己的数据库，决定用户拥有哪些业务权限
```

`Mock IdP` 是项目为本地开发和自动化测试提供的简化身份提供方，固定使用 `admin`、`peter`、`tom` 三个测试账号。生产环境不运行 Mock IdP，而是接入管网内部企业统一身份平台（Java开发）。后期可能修改为Keycloak 或其他正式 IdP。

重要区别是：IdP 负责“证明你是谁”，PipeChina 业务数据库负责“决定你能做什么”。IdP 签发了有效 JWT，不代表主后端一定允许该用户访问。

---

## 2.7.2 先区分 AuthN 与 AuthZ

### 2.7.2.1 AuthN：Authentication

Authentication 的问题是：

```text
你是谁？
```

在当前项目中，AuthN 负责：

- 接收 Bearer Token。
- 从 Mock IdP 或真实 IdP 的 JWKS 中取得公钥。
- 验证 JWT 签名。
- 验证 `alg`、`iss`、`aud`、`exp`、`nbf` 和 `sub`。
- 使用 `iss + sub` 映射数据库用户。
- 检查数据库用户和所属组织是否启用。

AuthN 成功只能说明身份可信，并不说明用户可以执行某项业务操作。

### 2.7.2.2 AuthZ：Authorization

Authorization 的问题是：

```text
你能做什么？
你能对哪些数据做？
```

在当前项目中，AuthZ 负责：

- 从数据库加载用户当前有效的角色授权。
- 通过角色查询权限。
- 合并每一条授权自己的数据范围。
- 检查 API 所要求的动作权限。
- 在查询数据库时增加组织、所有者或负责人过滤条件。
- 防止低权限管理员把自己没有的权限授予别人。

### 2.7.2.3 为什么必须分开

以停用用户 `tom` 为例：

1. Mock IdP 可以确认 `tom / 123456` 的密码正确。
2. Mock IdP 给 `tom` 签发一个有效 JWT。
3. 主后端成功验证 JWT 的签名、签发方和有效期。
4. 主后端查询 `user_accounts`，发现 `tom.active = false`。
5. 主后端返回 `403 ACCOUNT_DISABLED`。

这不是矛盾，而是正确的职责分离：

```text
外部认证成功 != 业务系统允许访问
```

---

## 2.7.3 JWT、OIDC 与 JWKS 分别是什么

### 2.7.3.1 JWT 是凭据格式

JWT 一般由三部分组成：

```text
header.payload.signature
```

本项目使用到的关键声明是：

| 声明 | 含义 |
|---|---|
| `iss` | Token 的签发方，即 Identity Provider（IdP） |
| `sub` | 用户在该签发方中的稳定唯一标识 |
| `aud` | Token 允许访问的目标系统 |
| `exp` | 过期时间 |
| `nbf` | 在此时间之前不可使用 |
| `iat` | 签发时间 |
| `preferred_username` | 便于显示的用户名，不作为数据库主键 |
| `name` | 显示名称，不作为授权依据 |


### 2.7.3.2 OIDC 是身份协议

OpenID Connect，简称 OIDC，是建立在 OAuth 2.0 之上的身份协议。

真实 IdP 通常提供：

- 登录页面。
- Token Endpoint。
- Issuer。
- OIDC Discovery Endpoint。
- JWKS Endpoint。
- 用户、密码、MFA、单点登录和会话管理。

当前 Mock IdP 只实现了本项目开发测试所需的最小部分，不应把它当作完整生产 OIDC 服务。

### 2.7.3.3 JWKS 是公钥集合

JWKS 全称 JSON Web Key Set。

Mock IdP 的 JWKS 地址是：

```text
http://127.0.0.1:9001/.well-known/jwks.json
```

JWT Header 中带有 `kid`。主后端根据 `kid` 从 JWKS 中选择对应公钥，然后验证签名。

主后端只需要公钥，不需要也不应该取得 IdP 的 RSA 私钥。

### 2.7.3.4 为什么使用 RS256

RS256 是非对称签名：

```text
IdP 持有 RSA 私钥 -> 签发 JWT
业务后端持有或下载 RSA 公钥 -> 验证 JWT
```

即使主后端被错误配置，也无法使用公钥伪造新 Token。

---

## 2.7.4 当前系统的完整请求流程

一次受保护请求的流程如下：

```text
用户输入用户名和密码
        |
        v
独立 IdP 验证密码并使用私钥签发 RS256 JWT
        |
        v
客户端携带 Authorization: Bearer <JWT>
        |
        v
FastAPI 从 JWKS 获取公钥并验证 JWT
        |
        v
使用 iss + sub 查询 user_accounts
        |
        v
检查用户 active 和 organization_units.active
        |
        v
加载有效 role_assignments
        |
        v
role_definitions + role_permissions + permission_definitions
        |
        v
计算 CurrentUser、权限和每项权限的数据范围
        |
        v
API 动作权限检查 + SQL 数据范围过滤
        |
        v
允许访问或返回 401 / 403
```

这里有两个重要结论：

1. JWT 只负责证明外部身份。
2. 最终授权结果每次都从数据库加载。

因此，JWT 中即使出现下面的伪造内容，也不会成为授权来源：

```json
{
  "roles": ["system_administrator"],
  "permissions": ["*"],
  "org_scope": ["任意组织ID"]
}
```

用户的角色、权限和组织范围仍以 PostgreSQL 中的数据为准。

---

## 2.7.5 数据库权限模型

### 2.7.5.1 组织表 `organization_units`

组织是数据隔离的基础。

主要字段包括：

| 字段 | 作用 |
|---|---|
| `id` | 组织 UUID |
| `parent_id` | 上级组织 |
| `code` | 组织编码，例如 `ROOT` |
| `name` | 组织名称 |
| `unit_type` | company、department 等 |
| `path` | 组织树路径，例如 `/ROOT/DISPATCH` |
| `active` | 组织是否启用 |

组织被停用后，该组织下用户即使 Token 有效，也会得到：

```text
403 ORGANIZATION_DISABLED
```

### 2.7.5.2 用户表 `user_accounts`

主要字段包括：

| 字段 | 作用 |
|---|---|
| `external_issuer` | 外部 Token 的 `iss` |
| `external_subject` | 外部 Token 的 `sub` |
| `username` | 本系统用户名 |
| `display_name` | 显示名称 |
| `organization_unit_id` | 用户所属组织 |
| `active` | 本系统账号是否启用 |
| `authz_version` | 权限版本标记 |
| `attributes` | 扩展属性 |

外部身份的唯一键是：

```text
external_issuer + external_subject
```

不能只使用 `username`，因为不同 IdP 可能存在同名用户，用户名也可能被修改。

用户密码不存放在 `user_accounts` 中。密码只属于外部 IdP。

### 2.7.5.3 权限表 `permission_definitions`

一条权限表示一个原子业务动作，例如：

```text
handover:read
event:review
maintenance:approve
report:publish
admin:user
```

权限采用：

```text
模块:动作
```

每条权限还带有模块、中文名称和风险等级。

### 2.7.5.4 角色表 `role_definitions`

角色是权限集合的业务名称，例如：

- `system_administrator`
- `security_auditor`
- `dispatcher`
- `inspection_operator`
- `maintenance_executor`
- `report_reviewer`

角色本身不直接决定能访问哪些组织。组织范围存放在角色授权中。

`role_definitions.permissions` 是为了兼容旧数据库结构保留的字段。当前真正参与授权的是 `role_permissions` 关联表。

### 2.7.5.5 角色权限关联表 `role_permissions`

该表建立：

```text
role_id -> permission_id
```

同一个角色可以包含多项权限，同一项权限也可以被多个角色使用。

### 2.7.5.6 角色授权表 `role_assignments`

角色授权表示：

```text
把某个角色，以某种数据范围，在某段有效期内，授予某个用户
```

主要字段包括：

| 字段 | 作用 |
|---|---|
| `user_id` | 被授权用户 |
| `role_id` | 被授予角色 |
| `scope_type` | 数据范围类型 |
| `data_scope` | 数据范围 JSON 文档 |
| `scope_digest` | 范围摘要，用于识别重复授权 |
| `active` | 授权是否有效 |
| `effective_from` | 生效时间，可为空 |
| `expires_at` | 失效时间，可为空 |
| `assigned_by` | 授权人 |
| `grant_reason` | 授权原因 |
| `revoked_at` | 撤销时间 |
| `revoked_by` | 撤销人 |
| `revoke_reason` | 撤销原因 |

撤销授权不是直接删除数据库记录，而是保留记录并填写撤销信息。

### 2.7.5.7 审计表 `audit_logs`

权限管理操作会记录：

- 操作人。
- 所属组织。
- 动作名称。
- 资源类型和资源 ID。
- 修改前数据。
- 修改后数据。
- HTTP 请求的 `request_id`。
- 授权或撤销原因。

这样可以回答：

```text
谁在什么时候，因为什么原因，把什么权限授予了谁？
```

---

## 2.7.6 RBAC 与数据范围

### 2.7.6.1 RBAC 只解决动作权限

RBAC 是 Role-Based Access Control，即基于角色的访问控制。

最基本关系是：

```text
用户 -> 角色 -> 权限
```

例如：

```text
peter -> dispatcher -> event:read
```

这只能说明 `peter` 可以执行“查看事件”动作，不能说明他可以查看所有组织的事件。

### 2.7.6.2 本项目的数据范围类型

数据范围可以理解为“某项权限能作用到哪些数据行”。

例如，`event:read` 是动作权限，表示可以执行“读取事件”。但业务表中可能同时存在北京站、上海站和其他组织的事件，所以还必须回答：

```text
可以读取事件       -> 权限（permission）
可以读取哪些事件 -> 数据范围（data scope）
```

数据范围不是单独授予的。它作为一次角色授权的一部分，与角色中的权限绑定在一起：

```text
把 dispatcher 角色，以 own_org 范围，授予 peter
    -> peter 拥有 dispatcher 中的动作权限
    -> 这些权限只能作用于 peter 本组织的数据
```

本项目的范围可分为三类：

1. 组织范围：`own_org`、`org_and_descendants`、`custom_orgs`。
2. 数据与用户的关系范围：`owned`、`assigned`。
3. 无组织限制：`global`。

| 范围 | 含义 | `organization_unit_ids` |
|---|---|---|
| `own_org` | 用户自己的组织 | 必须为空 |
| `org_and_descendants` | 指定组织及全部启用的下级组织 | 可指定根组织；为空时使用用户本组织 |
| `custom_orgs` | 明确列出的多个组织 | 必须至少提供一个组织 ID |
| `global` | 全部组织 | 必须为空 |
| `owned` | 仅用户自己创建的数据 | 必须为空 |
| `assigned` | 仅分配给该用户的数据 | 必须为空 |

`organization_unit_ids` 是授权时提交的组织 ID 列表。它在 `own_org`、`global`、`owned` 和 `assigned` 中必须为空，原因是这些范围不需要手工指定组织：

- `own_org` 会根据用户账号的 `organization_unit_id` 自动得到本组织。
- `global` 根本不做组织限制。
- `owned` 比较业务数据的 `owner_id` 与当前用户 ID。
- `assigned` 比较业务数据的 `assignee_id` 与当前用户 ID。

假设组织树是：

```text
总部 ROOT
├─ 东部分公司
│  ├─ 上海站
│  └─ 杭州站
└─ 西部分公司
   └─ 成都站
```

如果 peter 属于上海站，同一项 `event:read` 权限使用不同范围时，结果如下：

| 授权范围 | peter 可读取的事件 |
|---|---|
| `own_org` | 只能读取上海站的事件 |
| `org_and_descendants`，指定东部分公司 | 东部分公司、上海站和杭州站的事件 |
| `org_and_descendants`，不指定组织 | 以 peter 的上海站为根，包含上海站及其下级组织 |
| `custom_orgs`，指定上海站和成都站 | 只能读取这两个明确列出的组织，不自动包含它们的下级组织 |
| `global` | 可以读取全部组织的事件 |
| `owned` | 只能读取 `owner_id` 是 peter 用户 ID 的事件 |
| `assigned` | 只能读取 `assignee_id` 是 peter 用户 ID 的事件 |

组织被停用后，会从计算得到的组织范围中排除。`owned` 和 `assigned` 只有在对应业务资源确实提供所有者或处理人字段时才能匹配；如果该类资源没有相应字段，这种范围不会自动获得访问权。

一个本组织授权文档是：

```json
{
  "type": "own_org",
  "organization_unit_ids": []
}
```

一个自定义组织授权文档是：

```json
{
  "type": "custom_orgs",
  "organization_unit_ids": [
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222"
  ]
}
```

当 API 查询列表时，系统会把范围转换成类似下面的 SQL 过滤条件：

```text
own_org/custom_orgs/org_and_descendants -> organization_unit_id IN (...)
owned                                  -> owner_id = 当前用户ID
assigned                               -> assignee_id = 当前用户ID
global                                 -> 不增加数据范围过滤
```

如果同一用户通过多条授权拥有同一项权限，这些范围会取并集。例如，`event:read + own_org` 和 `event:read + assigned` 同时存在时，用户可以读取“本组织的事件”或“分配给自己的事件”。但不同权限之间不能互相借用范围，这就是下一节要说明的内容。

### 2.7.6.3 权限与范围不能跨角色错误放大

假设某用户有两条授权：

```text
角色 A：event:read，global
角色 B：event:review，own_org
```

正确结果是：

- 可以查看全部组织事件。
- 只能审核本组织事件。

不能因为用户在另一个角色上拥有 `global`，就把 `event:review` 也扩大为全局。

因此项目内部保留的是逐条 `EffectiveGrant`：

```text
角色 + 权限 + 范围 + 授权记录ID
```

而不是先把全部权限和全部组织范围分别合并后随意组合。

### 2.7.6.4 全局管理权限

下面这些权限要求 `global` 范围：

- `admin:role`
- `admin:model`
- `admin:prompt`
- `admin:config`

即使某用户在 `own_org` 范围下拥有这些权限，也不能修改全局配置。

### 2.7.6.5 防止越权转授权

权限管理员不能把自己没有的能力授予别人。

系统会检查：

- 操作人是否能管理目标用户所在组织。
- 操作人是否拥有准备授予的权限。
- 操作人的数据范围是否覆盖准备授予的数据范围。
- 全局权限是否使用全局范围授予。
- 普通管理员是否试图给自己授权。

这可以避免出现：

```text
部门管理员把自己提升成系统管理员
```

---

## 2.7.7 权限目录与内置角色代码

权限常量位于：

```text
app/shared/security/authorization/permissions.py
```

业务路由不应直接散落难以追踪的字符串，而应引用 `Permissions`：

```python
class Permissions:
    EVENT_READ = "event:read"
    EVENT_REVIEW = "event:review"
    REPORT_PUBLISH = "report:publish"
    ADMIN_USER = "admin:user"
```

内置角色也在该文件中定义：

```python
BUILTIN_ROLES = {
    "system_administrator": ("系统管理员", {Permissions.ALL}),
    "dispatcher": (
        "生产调度员",
        {
            Permissions.HANDOVER_READ,
            Permissions.HANDOVER_CREATE,
            Permissions.EVENT_READ,
            Permissions.EVENT_REVIEW,
            Permissions.REPORT_READ,
        },
    ),
}
```

`sync_permission_catalog()` 和 `sync_builtin_roles()` 会把代码目录同步到数据库。

代码中的权限目录是系统允许使用的权限白名单，数据库负责保存角色如何组合这些权限，以及角色最终授予了谁。

---

## 2.7.8 代码目录职责

### 2.7.8.1 `identity`

```text
app/shared/security/identity/
├── models.py
├── repository.py
└── schemas.py
```

职责包括：

- 定义组织和用户数据库模型。
- 根据外部 `iss + sub` 查找数据库用户。
- 检查用户是否停用。
- 检查组织是否停用。
- 定义创建、更新用户和组织时的输入结构。

### 2.7.8.2 `authentication`

```text
app/shared/security/authentication/
├── dependencies.py
└── schemas.py
```

职责包括：

- 提取 Bearer Token。
- 下载和缓存 JWKS。
- 验证 JWT。
- 生成 `VerifiedToken`。
- 调用身份仓储生成 `AuthenticatedIdentity`。

这里不读取角色权限，也不签发 Token。

### 2.7.8.3 `authorization`

```text
app/shared/security/authorization/
├── dependencies.py
├── models.py
├── permissions.py
├── repository.py
├── router.py
├── schemas.py
└── scopes.py
```

职责包括：

- 定义权限、角色和角色授权模型。
- 定义权限目录和内置角色。
- 加载当前用户的有效授权。
- 进行动作权限检查。
- 进行单条资源的数据范围检查。
- 生成 SQL 查询过滤条件。
- 提供权限管理 API。

### 2.7.8.4 `audit`

```text
app/shared/security/audit/context.py
```

它使用 `ContextVar` 保存当前请求的 `request_id`，使业务审计记录可以关联一次 HTTP 请求。

---

## 2.7.9 阅读认证代码

认证入口位于：

```text
app/shared/security/authentication/dependencies.py
```

### 2.7.9.1 获取 JWKS 客户端

```python
@lru_cache(maxsize=16)
def _get_jwk_client(url: str, cache_seconds: int, timeout_seconds: float):
    return PyJWKClient(
        url,
        cache_keys=True,
        lifespan=cache_seconds,
        timeout=timeout_seconds,
    )
```

这里缓存的是 JWKS 客户端和公钥，不是用户权限。

### 2.7.9.2 严格验证 Token

核心验证逻辑等价于：

```python
signing_key = jwk_client.get_signing_key_from_jwt(token)

claims = jwt.decode(
    token,
    signing_key.key,
    algorithms=[settings.jwt_algorithm],
    issuer=settings.jwt_issuer,
    audience=settings.jwt_audience,
    options={"require": ["exp", "iss", "aud", "sub"]},
)
```

几个关键点：

- 算法由服务端配置固定，不能相信 JWT Header 自己声明任意算法。
- `issuer` 必须完全一致。
- `audience` 必须包含当前后端。
- `exp`、`iss`、`aud`、`sub` 必须存在。
- 签名、有效期、`nbf` 或 `kid` 错误都会返回 `INVALID_TOKEN`。

### 2.7.9.3 映射数据库身份

JWT 验证后，身份仓储执行的核心查询是：

```python
select(UserAccount).where(
    UserAccount.external_issuer == token.issuer,
    UserAccount.external_subject == token.subject,
)
```

如果查询不到，不会临时创建用户，而是返回：

```text
403 ACCOUNT_NOT_PROVISIONED
```

这可以防止任何能从外部 IdP 获得账号的人自动进入业务系统。

---

## 2.7.10 阅读授权代码

### 2.7.10.1 加载当前有效授权

`load_current_user()` 只加载满足下面条件的授权：

```text
role_assignments.active = true
role_definitions.active = true
effective_from 为空或已经到达
expires_at 为空或尚未过期
permission_definitions.active = true
```

最终生成：

```python
CurrentUser(
    user_id=...,
    roles={...},
    permissions={...},
    organization_scope={...},
    grants=[...],
    authz_version=...,
)
```

当前系统每次请求都重新从数据库加载授权。因此，停用用户、撤销角色授权或停用角色后，下一次请求立即生效。

`authz_version` 是权限变更版本标记，可用于审计、客户端刷新或未来增加安全缓存，但当前授权正确性不依赖缓存。

### 2.7.10.2 检查动作权限

路由通过 FastAPI 依赖声明权限：

```python
@router.get("/events")
async def list_events(
    user: Annotated[
        CurrentUser,
        Depends(require_permission(Permissions.EVENT_READ)),
    ],
): ...
```

缺少权限时返回：

```json
{
  "code": "PERMISSION_DENIED",
  "message": "current user does not have the required permission",
  "details": {
    "permission": "event:read"
  }
}
```

### 2.7.10.3 检查单条数据

已经取得业务对象后，可以调用：

```python
require_data_scope(
    user,
    event.organization_unit_id,
    Permissions.EVENT_READ,
    owner_id=event.created_by,
)
```

它会同时考虑：

- `global`
- 组织范围
- `owned`
- `assigned`

### 2.7.10.4 在 SQL 中提前过滤

列表接口不能先查询全部敏感数据再在 Python 中过滤。

项目通过 `data_scope_clause()` 生成 SQL 条件：

```python
statement = statement.where(
    data_scope_clause(
        user,
        Permissions.EVENT_READ,
        ProductionEvent.organization_unit_id,
        owner_column=ProductionEvent.created_by,
    )
)
```

无范围时返回 SQL `false()`，而不是省略过滤条件。

---

## 2.7.11 权限管理 API

权限管理路由统一使用前缀：

```text
/api/v1/admin/access
```

接口列表如下：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/permissions` | 查看权限目录 |
| GET | `/roles` | 查看角色及其权限 |
| POST | `/roles` | 创建角色 |
| PATCH | `/roles/{role_id}` | 更新或停用角色 |
| GET | `/organizations` | 查看可管理组织 |
| POST | `/organizations` | 创建组织 |
| PATCH | `/organizations/{organization_id}` | 更新或停用组织 |
| GET | `/users` | 查看可管理用户 |
| POST | `/users` | 创建数据库用户映射 |
| PATCH | `/users/{user_id}` | 修改组织、属性或停用用户 |
| GET | `/role-assignments` | 查看授权记录 |
| POST | `/role-assignments` | 授予角色 |
| DELETE | `/role-assignments/{assignment_id}` | 撤销授权并保留记录 |
| GET | `/users/{user_id}/effective-access` | 查看用户当前有效权限 |
| GET | `/audit-logs` | 查看权限与业务审计 |

这些接口只管理本业务系统中的用户映射和权限，不修改 Mock IdP 或真实 IdP 中的密码。

---

## 2.7.12 Mock IdP 的作用

Mock IdP 位于：

```text
dev/mock_idp/
├── Dockerfile
├── keys.py
├── main.py
└── users.json
```

它是一个独立 FastAPI 进程，提供：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health/live` | 健康检查 |
| GET | `/.well-known/jwks.json` | 发布 RSA 公钥 |
| GET | `/.well-known/openid-configuration` | 最小 Discovery 信息 |
| POST | `/token` | 验证固定开发账号并签发 JWT |
| POST | `/admin/rotate-key` | 开发测试密钥轮换 |

Mock IdP 启动时在内存中生成 RSA 私钥。重启后密钥会变化，因此旧 Token 可能失效，需要重新获取。

密钥轮换时会保留当前密钥和上一把密钥，使尚未过期的旧 Token 可以在过渡期继续验证。

固定开发账号是：

| 用户名 | 密码 | IdP 身份 | 业务数据库状态 |
|---|---|---|---|
| `admin` | `123456` | `sub=mock-admin` | 启用，全局系统管理员 |
| `peter` | `123456` | `sub=mock-peter` | 启用，本组织调度员 |
| `tom` | `123456` | `sub=mock-tom` | 停用，无角色 |

Mock IdP 的明文开发密码、测试场景和无保护的轮换接口决定了它只能用于开发和自动化测试。

生产环境必须使用 Keycloak、企业统一身份平台或其他正式 IdP。


## 2.7.13 使用长期运行的基础设施容器启动权限系统

本项目推荐的本地开发方式是：

- PostgreSQL、RabbitMQ、Redis 和 SeaweedFS 是开发机上由多个项目共用的基础设施容器，不绑定 PipeChina 项目的生命周期。
- Alembic、初始化脚本、Mock IdP 和主 API 在 PyCharm 终端或宿主机上运行。
- 宿主机进程通过容器映射端口访问基础设施，因此使用 `127.0.0.1`，不使用 Docker 容器名。

项目根目录的 `docker-run` 仅保留为这台开发机首次创建共享容器的记录。容器已经存在时不要重复执行整个文件；如果容器曾被停止，可以执行：

```bash
docker start postgres rabbitmq redis seaweedfs
```

这套方案中，名为 `postgres` 的容器就是本项目使用的 PostgreSQL，它将端口映射到宿主机 `5432`。不要再执行 `docker compose up -d postgres` 创建第二个 PostgreSQL 容器，否则会因为宿主机 `5432` 已被占用而启动失败。

### 2.7.13.1 检查共享 PostgreSQL

```bash
docker exec postgres pg_isready -U peter -d pipechina
```

预期包含：

```text
accepting connections
```

### 2.7.13.2 检查 `.env`

本机从 PyCharm 或终端直接运行 API 时，相关配置应是：

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
```

注意：

- 本机进程访问数据库使用 `127.0.0.1`。
- Docker 内部进程才使用服务名 `postgres`。
- `JWT_ISSUER` 是必须匹配的逻辑签发方字符串。
- `JWT_JWKS_URL` 是主后端实际下载公钥的网络地址。

### 2.7.13.3 执行数据库迁移

```bash
uv run alembic upgrade head
```

查看当前版本：

```bash
uv run alembic current
```

预期包含：

```text
20260902_0003 (head)
```

### 2.7.13.4 启动 Mock IdP

方式一：直接运行独立进程。

```bash
uv run uvicorn dev.mock_idp.main:app \
  --host 127.0.0.1 \
  --port 9001 \
  --reload
```

方式二：只启动 Mock IdP 容器。

```bash
docker compose up -d mock-idp
```

验证健康状态：

```bash
curl -i http://127.0.0.1:9001/health/live
```

预期：

```http
HTTP/1.1 200 OK
```

### 2.7.13.5 初始化开发用户和内置角色

确认前一步 Alembic 迁移已到 `20260902_0003 (head)` 后，在 PyCharm 终端或项目根目录执行：

```bash
uv run python scripts/bootstrap_mock_idp_users.py
```

该脚本是宿主机进程，即使 PostgreSQL 运行在 Docker 容器中，`.env` 中也必须使用已映射到宿主机的地址：

```env
DATABASE_URL=postgresql+asyncpg://peter:123456@127.0.0.1:5432/pipechina
```

不要在这个终端命令中使用 `@postgres:5432`。`postgres` 是 Docker 网络内的容器名，PyCharm 终端运行的 Python 进程应通过 `127.0.0.1:5432` 访问它。

预期输出类似：

```text
Provisioned admin: user_id=... active=True
Provisioned peter: user_id=... active=True
Provisioned tom: user_id=... active=False
```

该脚本可以重复运行，不会重复创建三名用户或重复创建相同的有效角色授权。

它完成：

- 创建或复用 `ROOT` 组织。
- 同步权限目录和内置角色。
- 创建或更新 `admin`、`peter`、`tom`。
- 给 `admin` 授予 `system_administrator + global`。
- 给 `peter` 授予 `dispatcher + own_org`。
- 保证 `tom.active = false`。

### 2.7.13.6 启动主 API

另开一个终端：

```bash
uv run uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

验证 API：

```bash
curl -i http://127.0.0.1:8000/health/ready
```

预期：

```http
HTTP/1.1 200 OK
```

---

## 2.7.14 获取外部 JWT

### 2.7.14.1 获取 admin Token

```bash
curl -sS -X POST http://127.0.0.1:9001/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=password&username=admin&password=123456' \
  | uv run python -m json.tool
```

响应结构：

```json
{
  "access_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

复制 `access_token`，设置：

```bash
export ADMIN_TOKEN='粘贴 admin 的 access_token'
```

### 2.7.14.2 获取 peter Token

```bash
curl -sS -X POST http://127.0.0.1:9001/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=password&username=peter&password=123456' \
  | uv run python -m json.tool
```

```bash
export PETER_TOKEN='粘贴 peter 的 access_token'
```

### 2.7.14.3 获取 tom Token

```bash
curl -sS -X POST http://127.0.0.1:9001/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=password&username=tom&password=123456' \
  | uv run python -m json.tool
```

```bash
export TOM_TOKEN='粘贴 tom 的 access_token'
```

注意：Mock IdP 给 `tom` 返回 Token 是预期行为。真正的停用判断发生在业务数据库。

---

## 2.7.15 验证三个开发用户

### 2.7.15.1 验证 admin

```bash
curl -sS \
  http://127.0.0.1:8000/api/v1/auth/me \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | uv run python -m json.tool
```

预期包含：

```json
{
  "subject": "mock-admin",
  "username": "admin",
  "roles": ["system_administrator"],
  "permissions": ["*"]
}
```

`admin` 可以读取权限管理用户列表：

```bash
curl -i \
  http://127.0.0.1:8000/api/v1/admin/access/users \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

预期：

```http
HTTP/1.1 200 OK
```

### 2.7.15.2 验证 peter

```bash
curl -sS \
  http://127.0.0.1:8000/api/v1/auth/me \
  -H "Authorization: Bearer $PETER_TOKEN" \
  | uv run python -m json.tool
```

预期：

- `username` 是 `peter`。
- `roles` 包含 `dispatcher`。
- 权限包含交接班、事件处理、维检读取和报告读取。
- 数据范围来自数据库中的 `own_org` 授权。

`peter` 访问用户管理接口：

```bash
curl -i \
  http://127.0.0.1:8000/api/v1/admin/access/users \
  -H "Authorization: Bearer $PETER_TOKEN"
```

预期：

```http
HTTP/1.1 403 Forbidden
```

响应中的权限应为：

```json
{
  "code": "PERMISSION_DENIED",
  "details": {
    "permission": "admin:user"
  }
}
```

### 2.7.15.3 验证 tom

```bash
curl -i \
  http://127.0.0.1:8000/api/v1/auth/me \
  -H "Authorization: Bearer $TOM_TOKEN"
```

预期：

```http
HTTP/1.1 403 Forbidden
```

```json
{
  "code": "ACCOUNT_DISABLED",
  "message": "the user account is disabled"
}
```

---

## 2.7.16 验证认证失败场景

### 2.7.16.1 没有 Token

```bash
curl -i http://127.0.0.1:8000/api/v1/auth/me
```

预期：

```text
401 AUTHENTICATION_REQUIRED
```

### 2.7.16.2 错误密码

```bash
curl -i -X POST http://127.0.0.1:9001/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=password&username=admin&password=wrong-password'
```

预期 Mock IdP 返回：

```http
HTTP/1.1 401 Unauthorized
```

### 2.7.16.3 篡改 Token

```bash
export TAMPERED_TOKEN="${ADMIN_TOKEN%?}x"

curl -i \
  http://127.0.0.1:8000/api/v1/auth/me \
  -H "Authorization: Bearer $TAMPERED_TOKEN"
```

预期：

```text
401 INVALID_TOKEN
```

### 2.7.16.4 过期 Token

Mock IdP 支持专门的异常场景：

```bash
curl -sS -X POST http://127.0.0.1:9001/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=password&username=admin&password=123456&scenario=expired' \
  | uv run python -m json.tool
```

把返回值设置为 `EXPIRED_TOKEN` 后访问 `/api/v1/auth/me`，预期返回：

```text
401 INVALID_TOKEN
```

其他可用场景包括：

| `scenario` | 验证内容 |
|---|---|
| `not_yet_valid` | `nbf` 尚未到达 |
| `wrong_audience` | `aud` 错误 |
| `wrong_issuer` | `iss` 错误 |
| `unknown_kid` | JWKS 中找不到签名密钥 |
| `forged_permissions` | JWT 伪造管理员角色和 `*` 权限 |

### 2.7.16.5 验证 JWT 权限声明无效

获取伪造权限的 peter Token：

```bash
curl -sS -X POST http://127.0.0.1:9001/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=password&username=peter&password=123456&scenario=forged_permissions' \
  | uv run python -m json.tool
```

使用该 Token 访问 `/api/v1/auth/me`，预期仍然是：

```text
roles 包含 dispatcher
permissions 不包含 *
roles 不包含 system_administrator
```

这证明 Token 中的 `roles` 和 `permissions` 没有参与授权。

---

## 2.7.17 实际创建角色、授权和撤销

下面使用 `admin` 给 `peter` 临时增加 `report:export` 权限。

### 2.7.17.1 查看用户和角色

```bash
curl -sS \
  http://127.0.0.1:8000/api/v1/admin/access/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | uv run python -m json.tool
```

从结果中复制 `peter` 的 `id`：

```bash
export PETER_ID='粘贴 peter 的数据库 user_id'
```

查看现有角色：

```bash
curl -sS \
  http://127.0.0.1:8000/api/v1/admin/access/roles \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | uv run python -m json.tool
```

### 2.7.17.2 创建演示角色

```bash
curl -sS -X POST \
  http://127.0.0.1:8000/api/v1/admin/access/roles \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "code": "report_exporter_demo",
    "name": "报告导出演示角色",
    "description": "用于验证数据库授权和立即撤销",
    "permission_codes": ["report:export"]
  }' \
  | uv run python -m json.tool
```

复制响应中的角色 ID：

```bash
export DEMO_ROLE_ID='粘贴角色 id'
```

如果重复执行并返回 `409 STATE_CONFLICT`，说明该角色编码已经存在。可以从角色列表复制已有 ID，或换一个新的演示编码。

### 2.7.17.3 给 peter 授权

```bash
curl -sS -X POST \
  http://127.0.0.1:8000/api/v1/admin/access/role-assignments \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{
    \"user_id\": \"$PETER_ID\",
    \"role_id\": \"$DEMO_ROLE_ID\",
    \"data_scope\": {
      \"type\": \"own_org\",
      \"organization_unit_ids\": []
    },
    \"reason\": \"第8章权限操作验证\"
  }" \
  | uv run python -m json.tool
```

复制响应中的授权 ID：

```bash
export ASSIGNMENT_ID='粘贴授权 id'
```

### 2.7.17.4 查看 peter 的有效权限

```bash
curl -sS \
  "http://127.0.0.1:8000/api/v1/admin/access/users/$PETER_ID/effective-access" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | uv run python -m json.tool
```

预期：

- `roles` 包含 `report_exporter_demo`。
- `permissions` 包含 `report:export`。
- `grants` 中该权限的 `scope_type` 是 `own_org`。

重新使用原来的 `PETER_TOKEN` 请求 `/api/v1/auth/me`，也应立即看到 `report:export`。

不需要重新签发 Token，因为权限不保存在 Token 中。

### 2.7.17.5 撤销授权

```bash
curl -i -X DELETE \
  "http://127.0.0.1:8000/api/v1/admin/access/role-assignments/$ASSIGNMENT_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"第8章验证完成，撤销临时授权"}'
```

预期：

```http
HTTP/1.1 204 No Content
```

再次查询有效权限，`report:export` 和 `report_exporter_demo` 应消失。

同一个旧 `PETER_TOKEN` 立即生效，无需等待 Token 过期。

### 2.7.17.6 停用演示角色

```bash
curl -sS -X PATCH \
  "http://127.0.0.1:8000/api/v1/admin/access/roles/$DEMO_ROLE_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"active":false}' \
  | uv run python -m json.tool
```

角色记录会保留，但不再参与有效权限计算。

---

## 2.7.18 查看审计日志

```bash
curl -sS \
  'http://127.0.0.1:8000/api/v1/admin/access/audit-logs?limit=20' \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | uv run python -m json.tool
```

本章操作后应看到类似动作：

```text
role.create
role_assignment.grant
role_assignment.revoke
role.update
```

每一条响应还可以通过 `X-Request-ID` 与日志和审计记录关联。

客户端也可以主动传入：

```bash
-H 'X-Request-ID: permission-demo-001'
```

---

## 2.7.19 直接检查 PostgreSQL

如果本机安装了 `psql`：

```bash
psql 'postgresql://peter:123456@127.0.0.1:5432/pipechina'
```

### 2.7.19.1 查看三个用户

```sql
SELECT
    username,
    external_issuer,
    external_subject,
    active,
    authz_version
FROM user_accounts
ORDER BY username;
```

预期：

```text
admin  ... mock-admin  true
peter  ... mock-peter  true
tom    ... mock-tom    false
```

### 2.7.19.2 查看角色授权

```sql
SELECT
    u.username,
    r.code AS role_code,
    ra.scope_type,
    ra.active,
    ra.grant_reason,
    ra.revoke_reason
FROM role_assignments ra
JOIN user_accounts u ON u.id = ra.user_id
JOIN role_definitions r ON r.id = ra.role_id
ORDER BY u.username, r.code, ra.created_at;
```

### 2.7.19.3 查看角色权限

```sql
SELECT
    r.code AS role_code,
    p.code AS permission_code
FROM role_permissions rp
JOIN role_definitions r ON r.id = rp.role_id
JOIN permission_definitions p ON p.id = rp.permission_id
ORDER BY r.code, p.code;
```

### 2.7.19.4 查看最近审计

```sql
SELECT
    occurred_at,
    action,
    resource_type,
    resource_id,
    reason,
    request_id
FROM audit_logs
ORDER BY occurred_at DESC
LIMIT 20;
```

注意：数据库中不会出现 `admin`、`peter` 或 `tom` 的登录密码。

---

## 2.7.20 自动化测试

### 2.7.20.1 认证测试

```bash
uv run pytest -W error tests/test_auth_and_health.py
```

覆盖：

- 无 Token。
- 主应用不存在本地 Token 签发接口。
- 配置中不存在本地 JWT 密钥模式。
- 真实 HTTP JWKS 验签。
- 过期、未生效、错误 audience、错误 issuer 和未知 `kid`。
- 未预配身份拒绝。
- JWT 伪造权限无效。
- 数据库停用用户拒绝。

### 2.7.20.2 授权测试

```bash
uv run pytest -W error tests/test_authorization.py
```

覆盖：

- 没有数据库授权时拒绝。
- 授权后立即允许。
- 撤销后立即拒绝。
- 不同角色的数据范围不会错误放大。
- 全局配置必须拥有全局授权。
- `owned` 范围过滤。
- 用户、角色、授权和审计管理完整流程。
- 跨组织读取和处理被拒绝。
- 停用用户被拒绝。
- 所有业务路由都声明了动作权限。

### 2.7.20.3 全项目检查

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app scripts dev
uv run pytest -W error
```

---

## 2.7.21 接入真实外部 IdP

将来接入 Keycloak 或企业身份平台时，主后端架构不需要重新设计。

只需要完成下面几项：

1. 在真实 IdP 中创建客户端或受众。
2. 配置真实 HTTPS issuer。
3. 配置真实 HTTPS JWKS URL。
4. 确认 JWT 使用后端允许的非对称算法。
5. 将真实用户的 `iss + sub` 预配到 `user_accounts`。
6. 在数据库中为用户授予角色和数据范围。
7. 停止并禁止部署 Mock IdP。

生产配置示例：

```env
APP_ENV=production
JWT_ISSUER=https://identity.company.example/realms/pipechina
JWT_AUDIENCE=pipechina-backend
JWT_ALGORITHM=RS256
JWT_JWKS_URL=https://identity.company.example/realms/pipechina/protocol/openid-connect/certs
```

首次生产管理员可以使用：

```bash
uv run python scripts/bootstrap_security.py \
  --issuer '真实 JWT 的 iss' \
  --subject '管理员 JWT 的 sub' \
  --username admin \
  --display-name '系统管理员' \
  --organization-code ROOT \
  --organization-name '总部'
```

生产环境要求 issuer 和 JWKS 都使用 HTTPS。

---

## 2.7.22 常见错误排查

### 2.7.22.1 `AUTHENTICATION_REQUIRED`

原因：

- 没有发送 `Authorization`。
- Header 不是 `Bearer <token>`。

检查：

```bash
test -n "$ADMIN_TOKEN" && echo 'Token is set'
```

### 2.7.22.2 `INVALID_TOKEN`

可能原因：

- Mock IdP 重启后 RSA 密钥发生变化。
- Token 已过期。
- `JWT_ISSUER` 与 Token 的 `iss` 不一致。
- `JWT_AUDIENCE` 与 Token 的 `aud` 不一致。
- `JWT_JWKS_URL` 无法访问。
- Token 被复制不完整或被修改。

处理顺序：

1. 检查 `http://127.0.0.1:9001/health/live`。
2. 检查 JWKS 地址。
3. 重新获取 Token。
4. 检查 `.env` 中 issuer 和 audience。

### 2.7.22.3 `ACCOUNT_NOT_PROVISIONED`

Token 合法，但数据库找不到相同的：

```text
external_issuer + external_subject
```

开发环境重新执行：

```bash
uv run python scripts/bootstrap_mock_idp_users.py
```

这里的脚本在宿主机运行，因此 `.env` 中的 `DATABASE_URL` 应指向 `127.0.0.1:5432`，不是 `postgres:5432`。如果报告 `permission_definitions does not exist`，先对同一个数据库执行 `uv run alembic upgrade head`。

如果仍然失败，重点比较：

- Token 的 `iss`。
- `.env` 的 `JWT_ISSUER`。
- `user_accounts.external_issuer`。
- Token 的 `sub`。
- `user_accounts.external_subject`。

### 2.7.22.4 `ACCOUNT_DISABLED`

外部身份正确，但 `user_accounts.active = false`。

`tom` 出现该错误是本章设计的正常验证结果。

### 2.7.22.5 `ORGANIZATION_DISABLED`

用户所属组织不存在或 `organization_units.active = false`。

需要恢复组织，或把用户移动到启用的组织。

### 2.7.22.6 `PERMISSION_DENIED`

重点检查：

- 用户是否有有效角色授权。
- 角色是否启用。
- 权限是否启用。
- 授权是否已生效或已经过期。
- 数据范围是否覆盖目标数据。
- 全局权限是否真的使用 `global` 范围。

### 2.7.22.7 数据范围返回 422

检查范围文档形状：

- `custom_orgs` 必须提供组织 ID。
- `own_org`、`global`、`owned`、`assigned` 不能提供组织 ID。
- `effective_from` 必须早于 `expires_at`。

### 2.7.22.8 本机数据库连接失败

本机直接运行 API 时必须使用：

```text
127.0.0.1:5432
```

不能使用 Docker 服务名：

```text
postgres:5432
```

Docker 服务之间才使用 `postgres`。

---

## 2.7.23 安全边界总结

当前权限系统遵循下面的边界：

```text
密码验证                 -> 外部 IdP
JWT 私钥与签发            -> 外部 IdP
JWT 公钥验证              -> 主后端 AuthN
外部身份到业务用户映射     -> PostgreSQL user_accounts
角色与权限                -> PostgreSQL
组织、所有者和负责人范围   -> PostgreSQL + 业务查询
授权与撤销历史            -> PostgreSQL role_assignments
关键操作审计              -> PostgreSQL audit_logs
```

主后端信任外部 IdP 对身份的证明，但不信任 JWT 自带的业务角色和权限。

这是当前设计最核心的一句话：

```text
外部系统负责证明“你是谁”，本系统数据库负责决定“你能做什么、能操作哪些数据”。
```

---

## 2.7.24 本章验收清单

- [ ] 能解释 AuthN 与 AuthZ 的区别。
- [ ] 能解释 JWT、OIDC、JWKS 和 RS256 的关系。
- [ ] 确认主应用不存在本地 JWT 签发接口和共享密钥。
- [ ] 确认用户通过 `iss + sub` 映射到数据库账号。
- [ ] 确认 JWT 中伪造的角色和权限不会参与授权。
- [ ] 能说出用户、角色、权限、授权和组织对应的数据库表。
- [ ] 能解释六种数据范围。
- [ ] 能使用本机 PostgreSQL `5432` 执行迁移。
- [ ] 能启动 Mock IdP 和主 API。
- [ ] 能获取 `admin`、`peter`、`tom` 的 Token。
- [ ] `admin` 调用权限管理接口返回 200。
- [ ] `peter` 调用权限管理接口返回 403。
- [ ] `tom` 通过 IdP 取得 Token 后，主后端返回 `ACCOUNT_DISABLED`。
- [ ] 能创建演示角色并授予 `peter`。
- [ ] 能撤销授权，并确认旧 Token 立即失去新增权限。
- [ ] 能从审计日志中找到授权和撤销记录。
- [ ] 认证与授权自动化测试全部通过。
