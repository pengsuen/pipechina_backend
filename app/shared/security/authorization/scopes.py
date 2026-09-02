from enum import StrEnum

# 定义数据范围类型及其参数规则，供权限依赖生成资源过滤条件。
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ScopeType(StrEnum):
    """枚举授权支持的数据访问范围。"""

    OWN_ORG = "own_org"
    ORG_AND_DESCENDANTS = "org_and_descendants"
    CUSTOM_ORGS = "custom_orgs"
    GLOBAL = "global"
    OWNED = "owned"
    ASSIGNED = "assigned"


class DataScopeInput(BaseModel):
    """描述角色授权携带的数据范围参数。"""

    type: ScopeType = ScopeType.OWN_ORG
    organization_unit_ids: set[UUID] = Field(default_factory=set)

    @model_validator(mode="after")
    def validate_shape(self) -> DataScopeInput:
        """校验范围类型与组织列表的组合是否合法。"""
        if self.type == ScopeType.CUSTOM_ORGS and not self.organization_unit_ids:
            raise ValueError("custom_orgs scope requires organization_unit_ids")
        if self.type in {
            ScopeType.OWN_ORG,
            ScopeType.GLOBAL,
            ScopeType.OWNED,
            ScopeType.ASSIGNED,
        }:
            if self.organization_unit_ids:
                raise ValueError(f"{self.type} scope does not accept organization_unit_ids")
        return self

    def as_document(self) -> dict:
        """将数据范围转换为可持久化的字典。"""
        return {
            "type": self.type.value,
            "organization_unit_ids": sorted(str(item) for item in self.organization_unit_ids),
        }
