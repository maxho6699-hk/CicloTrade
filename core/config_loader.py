# -*- coding: utf-8 -*-
"""
============================================================================
量化交易系统 V5.1 - 配置加载器
----------------------------------------------------------------------------
安全规则：敏感信息只通过环境变量或未纳入版本控制的本机配置提供，
禁止代码硬编码。日志禁止打印密钥、账号凭证、Token 等敏感数据。
============================================================================
"""

import os
import yaml
from pathlib import Path
from typing import Any, Dict

from core.exceptions import ConfigError

# 敏感字段列表，日志/输出时必须遮蔽
SENSITIVE_KEYS = [
    "password", "bot_token", "chat_id", "tiger_id",
    "private_key_path", "account", "private_key"
]


def _mask_value(key: str, value: Any) -> Any:
    """对敏感配置值进行脱敏处理"""
    if key.lower() in SENSITIVE_KEYS or any(sk in key.lower() for sk in SENSITIVE_KEYS):
        if isinstance(value, str) and len(value) > 4:
            return value[:2] + "***" + value[-2:]
        return "***"
    return value


def _mask_dict(d: Dict) -> Dict:
    """递归脱敏字典中的敏感字段"""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _mask_dict(v)
        elif isinstance(v, list):
            result[k] = [_mask_dict(item) if isinstance(item, dict) else _mask_value(k, item) for item in v]
        else:
            result[k] = _mask_value(k, v)
    return result


class ConfigLoader:
    """
    配置加载器：从 YAML 文件加载配置，提供安全的配置访问接口。
    支持嵌套键访问（如 "risk.max_daily_loss"）。
    """

    def __init__(self, config_path: str = None):
        if config_path is None:
            # 项目只有一个受版本控制的非敏感配置入口。
            project_root = Path(__file__).parent.parent
            config_path = str(project_root / "config.yaml")

        self._config_path = config_path
        self._config: Dict = {}
        self.load()

    def load(self) -> None:
        """加载配置文件"""
        if not os.path.exists(self._config_path):
            raise ConfigError(f"配置文件不存在: {self._config_path}")

        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"配置文件解析失败: {e}")
        except Exception as e:
            raise ConfigError(f"配置文件加载异常: {e}")

        if self._config is None:
            raise ConfigError("配置文件为空")

    def get(self, key: str, default: Any = None) -> Any:
        """
        通过点分隔的嵌套键获取配置值
        例如: config.get("risk.max_daily_loss")
        """
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def get_all(self) -> Dict:
        """获取全部配置（已脱敏）"""
        return _mask_dict(self._config)

    def get_raw(self) -> Dict:
        """
        获取原始配置（仅内部使用，切勿直接暴露到日志/输出）
        """
        return self._config

    def __repr__(self) -> str:
        """安全打印：自动脱敏"""
        return f"ConfigLoader(path={self._config_path}, config={_mask_dict(self._config)})"


# 全局单例配置实例
_config_instance: ConfigLoader = None


def get_config(config_path: str = None) -> ConfigLoader:
    """获取全局配置单例"""
    global _config_instance
    if _config_instance is None:
        _config_instance = ConfigLoader(config_path)
    return _config_instance


def reload_config(config_path: str = None) -> ConfigLoader:
    """重新加载配置"""
    global _config_instance
    _config_instance = ConfigLoader(config_path)
    return _config_instance
