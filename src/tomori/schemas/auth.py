"""认证、账号、角色与业务范围授权相关 Pydantic v2 Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from tomori.schemas.common import AccountStatus, ORMModel, RoleCode, ScopeType


class LoginIn(BaseModel):
    """登录请求体。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    """当前账号改密请求体。"""

    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ResetPasswordIn(BaseModel):
    """管理员重置账号密码请求体。"""

    new_password: str = Field(min_length=8, max_length=128)


class RoleOut(ORMModel):
    """角色字典只读响应。"""

    id: int
    code: RoleCode
    name: str
    description: str | None = None


class ScopeAuthorizationCreateIn(BaseModel):
    """授予区域业务范围授权请求体。"""

    account_id: int = Field(gt=0)
    region_id: int = Field(gt=0)
    scope_type: ScopeType
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    remark: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def _validate_valid_window(self) -> ScopeAuthorizationCreateIn:
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to <= self.valid_from
        ):
            raise ValueError("valid_to 必须晚于 valid_from")
        return self


class ScopeAuthorizationOut(ORMModel):
    """业务范围授权响应模型。"""

    id: int
    account_id: int
    region_id: int
    region_code: str | None = None
    region_name: str | None = None
    scope_type: ScopeType
    is_active: bool
    valid_from: datetime
    valid_to: datetime | None = None
    granted_by_account_id: int | None = None
    remark: str | None = None
    created_at: datetime


class AccountCreateIn(BaseModel):
    """管理员创建账号请求体（一账号一角色）。"""

    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_.-]{2,63}$",
    )
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=64)
    role_code: RoleCode
    employee_no: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=128)
    skills: list[str] = Field(default_factory=list)
    max_active_dispatch: int = Field(default=3, ge=1, le=20)
    manager_account_id: int | None = Field(default=None, gt=0)


class AccountUpdateIn(BaseModel):
    """管理员更新账号信息请求体。"""

    full_name: str | None = Field(default=None, min_length=1, max_length=64)
    employee_no: str | None = Field(default=None, max_length=32)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=128)
    skills: list[str] | None = None
    max_active_dispatch: int | None = Field(default=None, ge=1, le=20)
    status: AccountStatus | None = None
    manager_account_id: int | None = Field(default=None, gt=0)


class AccountOut(ORMModel):
    """账号响应模型。"""

    id: int
    username: str
    full_name: str
    employee_no: str | None = None
    phone: str | None = None
    email: str | None = None
    status: AccountStatus
    role_code: RoleCode
    skills: list[str] = Field(default_factory=list)
    max_active_dispatch: int
    token_version: int
    manager_account_id: int | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    scope_authorizations: list[ScopeAuthorizationOut] | None = None


class LoginOut(ORMModel):
    """登录成功响应。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    account: AccountOut


class AuthMeOut(ORMModel):
    """当前登录用户上下文响应。"""

    account: AccountOut
    role_code: RoleCode
    scope_authorizations: list[ScopeAuthorizationOut] = Field(default_factory=list)


__all__ = [
    "AccountCreateIn",
    "AccountOut",
    "AccountUpdateIn",
    "AuthMeOut",
    "ChangePasswordIn",
    "LoginIn",
    "LoginOut",
    "ResetPasswordIn",
    "RoleOut",
    "ScopeAuthorizationCreateIn",
    "ScopeAuthorizationOut",
]
