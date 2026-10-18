"""集中式模型路由表。

此前每个 Agent 的模型选择散落在各自的 AgentCallPolicy 里(而且大多没设,
default_model 默认 "primary" → 所有 Agent 都走 pro, 又贵又慢)。这里把
"哪个任务用 pro / 哪个用 flash" 收敛到一张可配置的表, 作为唯一事实来源。

路由规则(优先级从高到低):
1. agent_overrides[agent_name]  —— 针对具体 Agent 的硬指定
2. 按 task_type 推断 tier, 再按 tier_model 映射到 primary/fallback
3. 兜底 primary

可通过 config/model.yaml 的 routing 段覆盖默认值, 例如::

    routing:
      tiers:        # 覆盖 tier → 模型
        routine: primary
      agents:       # 覆盖具体 Agent
        ContextAgent: primary
"""

from __future__ import annotations


# task_type → 质量档位
DEFAULT_TASK_TIER = {
    # 关键质量任务: 必须 pro
    "write_chapter": "critical",
    "writing": "critical",
    "review": "critical",
    "book_outline": "critical",
    "chapter_outline": "critical",
    "setting": "critical",
    # 例行任务: 默认 flash(有 fallback 时), 省钱提速
    "outline_update": "routine",
    "context": "routine",
    "revise": "routine",
    "consistency_check": "routine",
    "reality_check": "routine",
    "entity_extract": "routine",
    "general": "routine",
    # 轻量任务: flash
    "data_extract": "lite",
    "review_lite": "lite",
}

# tier → 模型槽位
DEFAULT_TIER_MODEL = {
    "critical": "primary",
    "stage_gate": "primary",
    "routine": "fallback",
    "lite": "fallback",
}


class ModelRouter:
    """把 (agent, task_type, tier) 解析为 'primary' 或 'fallback'。"""

    def __init__(
        self,
        task_tier: dict | None = None,
        tier_model: dict | None = None,
        agent_overrides: dict | None = None,
    ):
        self.task_tier = {**DEFAULT_TASK_TIER, **(task_tier or {})}
        self.tier_model = {**DEFAULT_TIER_MODEL, **(tier_model or {})}
        self.agent_overrides = dict(agent_overrides or {})

    def tier_of(self, policy) -> str:
        # task_type 优先; 否则用 policy 上显式的 quality_tier; 再否则 routine
        tier = self.task_tier.get(policy.task_type)
        if tier:
            return tier
        return getattr(policy, "quality_tier", None) or "routine"

    def resolve(self, policy) -> str:
        """返回 'primary' 或 'fallback'(目标模型槽位)。"""
        override = self.agent_overrides.get(policy.agent_name)
        if override in ("primary", "fallback"):
            return override
        tier = self.tier_of(policy)
        slot = self.tier_model.get(tier, "primary")
        return slot if slot in ("primary", "fallback") else "primary"

    @classmethod
    def from_config(cls, routing: dict | None) -> "ModelRouter":
        routing = routing or {}
        return cls(
            task_tier=routing.get("tasks") or routing.get("task_tier"),
            tier_model=routing.get("tiers") or routing.get("tier_model"),
            agent_overrides=routing.get("agents") or routing.get("agent_overrides"),
        )


# 模块级默认路由器: AgentRuntime 未被注入自定义路由器时使用。
DEFAULT_ROUTER = ModelRouter()

