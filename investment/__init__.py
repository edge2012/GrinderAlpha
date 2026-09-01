"""GrinderAlpha 核心包（决策引擎 + 抽象层 + 方法论 + 辩论引擎）。"""

from investment.decision_report import (  # noqa: F401
    Action,
    DataSourceInfo,
    DecisionReport,
    DimensionCheck,
    TraceStep,
    format_report,
    resolve_action,
)
