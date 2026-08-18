import logging
from pathlib import Path

import casbin

logger = logging.getLogger(__name__)

_config_dir = Path(__file__).parent.parent.parent / "config"
_model_path = str(_config_dir / "rbac_model.conf")
_policy_path = str(_config_dir / "rbac_policy.csv")

_enforcer: casbin.Enforcer | None = None


def get_enforcer() -> casbin.Enforcer:
    global _enforcer
    if _enforcer is None:
        _enforcer = casbin.Enforcer(_model_path, _policy_path)
        logger.info("Casbin enforcer initialized")
    return _enforcer


def check_permission(username: str, obj: str, act: str) -> bool:
    """检查用户是否有权限"""
    enforcer = get_enforcer()
    return enforcer.enforce(username, obj, act)


def add_role(username: str, role: str):
    """给用户添加角色"""
    enforcer = get_enforcer()
    enforcer.add_role_for_user(username, role)
    _save_policy()


def remove_role(username: str, role: str):
    """移除用户角色"""
    enforcer = get_enforcer()
    enforcer.delete_role_for_user(username, role)
    _save_policy()


def get_user_roles(username: str) -> list[str]:
    """获取用户角色"""
    enforcer = get_enforcer()
    return enforcer.get_roles_for_user(username)


def add_policy(sub: str, obj: str, act: str):
    """添加策略"""
    enforcer = get_enforcer()
    enforcer.add_policy(sub, obj, act)
    _save_policy()


def remove_policy(sub: str, obj: str, act: str):
    """删除策略"""
    enforcer = get_enforcer()
    enforcer.remove_policy(sub, obj, act)
    _save_policy()


def get_all_policies() -> list[list[str]]:
    """获取所有策略"""
    enforcer = get_enforcer()
    return enforcer.get_policy()


def get_all_roles() -> list[list[str]]:
    """获取所有角色映射"""
    enforcer = get_enforcer()
    return enforcer.get_grouping_policy()


def _save_policy():
    """保存策略到文件"""
    enforcer = get_enforcer()
    enforcer.save_policy()
    logger.info("Casbin policy saved")
