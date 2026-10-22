"""魔法卡片 · 新调度引擎（engine_v2）。

与旧 auto_refine 完全解耦：本包实现「单一滚动重规划器」架构
（见 docs/调度引擎架构设计.md），复用旧代码的静态知识（ThemePlan 合成树、
parse_full 解析、CHANGE_FACE 约束）与 _sim_faithful.VirtualSession 忠实游戏后端，
但不修改任何旧文件。

分层：
  world  读状态层（WorldState，聚合而非枚举）
  model  静态模型层（ThemeModel 包装 ThemePlan）
  plan   契约层（Action / Plan）
  planner 决策层（5 步流水线，纯函数无副作用）
  executor 执行层（纯 API，无决策）
  engine  编排（仿真循环 / 真实循环共用）
"""
from .world import WorldState, Furnace
from .model import ThemeModel
from .plan import Action, Plan
from .planner import Planner
from .executor import Executor
from .engine import Engine

__all__ = [
    "WorldState", "Furnace", "ThemeModel", "Action", "Plan",
    "Planner", "Executor", "Engine",
]
