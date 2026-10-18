"""核心数据模型定义。"""

from dataclasses import dataclass, field, asdict
from typing import Optional
import json

@dataclass
class CharacterProfile:
    """角色设定。"""
    name: str = ""
    role: str = ""  # protagonist_male, protagonist_female, supporting
    age: int = 0
    grade: str = ""
    appearance: str = ""
    personality: str = ""
    background: str = ""
    strengths: list = field(default_factory=list)
    flaws: list = field(default_factory=list)
    defense_mechanisms: list = field(default_factory=list)
    speaking_style: str = ""
    core_desire: str = ""
    core_fear: str = ""
    growth_arc: str = ""
    forbidden_behaviors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CharacterProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class RelationshipState:
    """关系状态 - 核心恋爱进度控制。"""
    relationship_stage: str = "陌生人"
    stage_description: str = ""
    romance_level: int = 0  # 0-100
    trust_level: int = 0     # 0-100
    conflict_level: int = 0  # 0-100
    male_to_female: dict = field(default_factory=dict)
    female_to_male: dict = field(default_factory=dict)
    unresolved_tensions: list = field(default_factory=list)
    forbidden_progress: list = field(default_factory=list)
    recent_changes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RelationshipState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class RomanceProgress:
    """恋爱进度追踪。"""
    current_stage: str = ""
    stage_index: int = 0
    total_stages_planned: int = 5
    key_milestones: list = field(default_factory=list)
    completed_milestones: list = field(default_factory=list)
    next_milestone: str = ""
    romance_level_history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RomanceProgress":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class CampusEvent:
    """校园事件。"""
    event_id: str = ""
    event_type: str = ""  # 月考、运动会、社团、晚自习、家长会等
    description: str = ""
    date_in_story: str = ""
    characters_involved: list = field(default_factory=list)
    emotional_impact: str = ""
    relationship_impact: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CampusEvent":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class ChapterOutline:
    """章节大纲。"""
    chapter_id: int = 0
    volume: int = 1
    title: str = ""
    story_question: str = ""
    reader_hook: str = ""
    character_desire: str = ""
    campus_setting: str = ""
    main_event: str = ""
    obstacle: str = ""
    action: str = ""
    cost: str = ""
    emotion_progress: str = ""
    emotional_intensity: int = 3
    emotional_payoff: str = ""
    character_growth: str = ""
    conflict: str = ""
    relationship_turn: str = ""
    irreversible_change: str = ""
    reader_reward: str = ""
    viral_scene: str = ""
    quotable_line_goal: str = ""
    opening_hook_options: list = field(default_factory=list)
    beat_hardness: int = 3
    famous_scene_type: str = ""
    famous_scene_payload: str = ""
    payoff_type: str = ""
    payoff_debt: str = ""
    payoff_visibility: str = ""
    character_tag_pressure: str = ""
    rival_function: str = ""
    supporting_pressure: str = ""
    style_freshness_notes: str = ""
    long_suspense_role: str = ""
    long_suspense_clue: str = ""
    foreshadowing_new: list = field(default_factory=list)
    foreshadowing_recall: list = field(default_factory=list)
    ending_hook: str = ""
    next_chapter_question: str = ""
    forbidden_items: list = field(default_factory=list)
    status: str = "planned"  # planned, writing, written, reviewed, revised

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterOutline":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class WritingTask:
    """写作任务书。"""
    chapter_id: int = 0
    chapter_goal: str = ""
    reader_entry_hook: str = ""
    story_question: str = ""
    campus_scene: str = ""
    main_event: str = ""
    desire_obstacle_action_cost: dict = field(default_factory=dict)
    emotion_progress_goal: str = ""
    emotional_intensity_target: int = 3
    emotional_payoff_goal: str = ""
    character_growth_goal: str = ""
    relationship_turn_goal: str = ""
    irreversible_change_goal: str = ""
    reader_reward_goal: str = ""
    viral_scene_goal: str = ""
    quotable_line_goal: str = ""
    opening_hook_goal: str = ""
    beat_hardness_target: int = 3
    famous_scene_type: str = ""
    famous_scene_payload: str = ""
    payoff_goal: str = ""
    payoff_debt_to_create_or_clear: str = ""
    character_tag_pressure_goal: str = ""
    rival_function_goal: str = ""
    style_dedupe_goal: str = ""
    long_suspense_goal: str = ""
    must_recall: list = field(default_factory=list)
    forbidden_items: list = field(default_factory=list)
    tone_style: str = ""
    ending_hook_goal: str = ""
    next_chapter_question: str = ""
    context_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WritingTask":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class ChapterDraft:
    """章节草稿。"""
    chapter_id: int = 0
    title: str = ""
    content: str = ""
    word_count: int = 0
    status: str = "draft"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterDraft":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class ChapterReview:
    """章节审查报告。"""
    chapter_id: int = 0
    total_score: int = 0
    conclusion: str = ""
    major_strengths: list = field(default_factory=list)
    major_issues: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    must_fix: list = field(default_factory=list)
    optional_improvements: list = field(default_factory=list)
    next_chapter_suggestions: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterReview":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class ForeshadowingItem:
    """伏笔条目。"""
    foreshadow_id: str = ""
    description: str = ""
    planted_chapter: int = 0
    intended_payoff_chapter: Optional[int] = None
    actual_payoff_chapter: Optional[int] = None
    status: str = "active"  # active, recalled, abandoned
    related_characters: list = field(default_factory=list)
    importance: str = "medium"  # low, medium, high
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ForeshadowingItem":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class EventLogItem:
    """事件日志条目。"""
    event_id: str = ""
    chapter_id: int = 0
    event_type: str = ""
    description: str = ""
    characters_involved: list = field(default_factory=list)
    timestamp_story: str = ""
    relationship_impact: str = ""
    tags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EventLogItem":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class ChapterSummary:
    """章节摘要。"""
    chapter_id: int = 0
    title: str = ""
    summary: str = ""
    key_events: list = field(default_factory=list)
    relationship_change: str = ""
    word_count: int = 0
    date_in_story: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ChapterSummary":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class NovelConfig:
    """小说配置。"""
    title: str = ""
    genre: str = ""        # 题材由 --genre / novel.yaml 指定, 骨架不预设
    sub_genre: str = ""
    stage: str = ""        # 阶段/处境(校园=高中/大学, 都市=落魄归来…), 题材相关
    target_words: int = 200000
    tone: str = ""
    pace: str = ""
    perspective: str = "第三人称"
    protagonist_male_name: str = ""
    protagonist_female_name: str = ""
    setting_description: str = ""
    core_theme: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NovelConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

@dataclass
class ReviewThresholds:
    """审查评分阈值配置。"""
    min_total_score: int = 70
    min_dimension_score: int = 5
    auto_warn_dimensions: list = field(default_factory=lambda: [
        "emotion_naturalness", "ambiguity_feel", "character_stability",
        "campus_realism", "dialogue_naturalness", "ai_tone_risk",
        "domineering_tone_risk", "rushing_risk", "chapter_hook"
    ])
    warn_on_high_risk: list = field(default_factory=lambda: [
        "ai_tone_risk", "domineering_tone_risk", "rushing_risk"
    ])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ReviewThresholds":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ConsistencyIssue:
    """一致性问题。"""
    issue_id: str = ""
    issue_type: str = ""
    severity: str = "medium"
    description: str = ""
    chapter_id_a: int = 0
    chapter_id_b: int = 0
    evidence_a: str = ""
    evidence_b: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ConsistencyIssue":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ConsistencyReport:
    """一致性检查报告。"""
    checked_chapters: list = field(default_factory=list)
    total_issues: int = 0
    issues_by_type: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    overall_score: int = 100
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ConsistencyReport":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class DialogueSample:
    """角色对话样本（用于风格一致性检测）。"""
    character_name: str = ""
    chapter_id: int = 0
    sample_text: str = ""
    word_count: int = 0
    avg_sentence_length: float = 0.0
    speaking_features: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DialogueSample":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class VisualizationData:
    """可视化数据。"""
    title: str = ""
    chart_type: str = ""
    data_points: list = field(default_factory=list)
    labels: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "VisualizationData":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ModelConfig:
    """LLM 模型配置。"""
    provider: str = "mock"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.8
    thinking: str | None = None  # DeepSeek Chat Completions: enabled / disabled
    max_tokens: int = 6000
    max_prompt_tokens: int = 50000  # Prompt Token 预算上限，超出发警告

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ModelConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class MultiModelConfig:
    """多模型配置：支持 primary + fallback 模型链。"""
    primary: ModelConfig = field(default_factory=ModelConfig)
    fallback: ModelConfig | None = None
    default_provider: str = "primary"  # "primary" | "primary_then_fallback"

    def to_dict(self) -> dict:
        return {
            "primary": self.primary.to_dict(),
            "fallback": self.fallback.to_dict() if self.fallback else None,
            "default_provider": self.default_provider,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MultiModelConfig":
        primary = ModelConfig.from_dict(d.get("primary", {}))
        fb = d.get("fallback")
        fallback = ModelConfig.from_dict(fb) if fb else None
        return cls(
            primary=primary,
            fallback=fallback,
            default_provider=d.get("default_provider", "primary"),
        )

