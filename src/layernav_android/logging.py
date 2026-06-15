"""结构化日志（structlog，stdlib 集成）。

用法::

    from layernav_android.logging import get_logger
    logger = get_logger(__name__)

    logger.info("event_name", key=value)       # 结构化（推荐）
    logger.info("msg with %s", arg)            # printf 兼容（过渡期）
    logger.warning("event", exc_info=True)     # 异常记录
"""

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)


def get_logger(name: str = ""):
    """返回 structlog logger（stdlib 绑定）。"""
    return structlog.get_logger(name)
