# -*- coding: utf-8 -*-
"""
============================================================================
量化交易系统 V5.1 - 自定义异常定义
----------------------------------------------------------------------------
⚠ 重要提示：当前属于跨券商行情+交易架构，天然存在网络时差滑点风险
============================================================================
"""


class QuantSystemError(Exception):
    """量化系统基础异常类"""
    pass


class ConfigError(QuantSystemError):
    """配置文件加载或解析错误"""
    pass


class MarketDataError(QuantSystemError):
    """
    行情数据异常基类
    ⚠ FutuOpenD 内存占用较高，2核2G服务器建议配置Swap分区
    """
    pass


class MarketConnectionError(MarketDataError):
    """行情通道连接异常"""
    pass


class MarketDisconnectedError(MarketDataError):
    """行情通道断开连接"""
    pass


class MarketSubscriptionError(MarketDataError):
    """行情订阅失败"""
    pass


class TradingError(QuantSystemError):
    """
    交易异常基类
    ⚠ 老虎期权API仅支持限价类订单，无市价单，剧烈波动存在成交失败风险
    """
    pass


class TradingConnectionError(TradingError):
    """交易通道连接异常"""
    pass


class TradingDisconnectedError(TradingError):
    """交易通道断开连接"""
    pass


class OrderError(TradingError):
    """订单相关异常"""
    pass


class OrderRejectedError(OrderError):
    """订单被风控拒绝"""
    pass


class DuplicateOrderError(OrderError):
    """重复订单异常"""
    pass


class OrderTimeoutError(OrderError):
    """订单超时"""
    pass


class RiskControlError(QuantSystemError):
    """风控异常"""
    pass


class DailyLossLimitError(RiskControlError):
    """单日亏损阈值触发"""
    pass


class PositionLimitError(RiskControlError):
    """仓位超限"""
    pass


class ConsecutiveLossError(RiskControlError):
    """连续亏损冷却触发"""
    pass


class TradingHoursError(RiskControlError):
    """非交易时段操作"""
    pass


class ForceLiquidationError(RiskControlError):
    """Tier0 强制平仓触发"""
    pass


class StrategyError(QuantSystemError):
    """策略执行异常"""
    pass


class NotificationError(QuantSystemError):
    """通知推送异常（不中断主程序）"""
    pass


class DatabaseError(QuantSystemError):
    """数据库操作异常"""
    pass


class AuthenticationError(QuantSystemError):
    """鉴权异常"""
    pass
