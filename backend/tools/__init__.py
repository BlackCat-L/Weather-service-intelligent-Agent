# -*- coding: utf-8 -*-
"""工具集装配入口：构建 Agent 可用的全部工具。

新增工具只需：写一个模块 → 实现 register(reg) → 在这里登记一行。
工具注册表本身是领域无关的，换领域只需要换这个文件里登记的工具。
"""
from ..agent.registry import ToolRegistry
from . import derive, forecast, geo, kb_search


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    geo.register(reg)          # 地名 → 经纬度
    forecast.register(reg)     # 预报 / 实况 / 历史
    derive.register(reg)       # 派生指标 / 风险评估
    kb_search.register(reg)    # 领域知识库检索
    return reg


__all__ = ["build_registry", "geo", "forecast", "derive", "kb_search"]
