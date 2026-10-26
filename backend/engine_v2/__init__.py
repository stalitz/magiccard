"""魔法卡片 · engine_v2 = 生产调度内核（不含并行实现）。

本包只有一份决策/执行实现（用户 2026-10-26 定调「决策层和执行层就是同一套」）：

  world         读状态层（WorldState：箱/炉/槽的聚合快照）
  model         静态模型层（ThemeModel：包装生产 ThemePlan 的合成树/炉时）
  plan          契约层（Action / Plan）
  dp_controller 决策层（骨架 + 有限偏差；生产 auto_refine 直接调用）
  engine        仿真驱动器（把生产的 AutoRefine.tick() 跑在 VirtualSession 上）

注意：早期把生产贪心/执行**重写**成的 planner.py / executor.py / dp_adapter.py
已删除（2026-10-26）。仿真与生产共用 AutoRefine + DPTickController，唯一被替换的
是「网络 + 时钟」（VirtualSession）。详见 docs/engine_v2_架构与代码导读.md。
"""
from .world import WorldState, Furnace
from .model import ThemeModel
from .plan import Action, Plan
from .engine import Engine

__all__ = ["WorldState", "Furnace", "ThemeModel", "Action", "Plan", "Engine"]
