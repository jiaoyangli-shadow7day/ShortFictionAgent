"""Versioned author contracts and genre/format separation."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
import hashlib
import json
import re
import unicodedata
from uuid import uuid4


class StoryError(ValueError):
    pass


FORMAT_DEFAULTS = {
    "micro_story": (500, 900, None),
    "short_story": (3000, 8000, None),
    "short_serial": (10000, 20000, None),
    "novella": (12000, 30000, None),
    "long_serial": (10000, 200000, None),
}

ENGINES = {
    "relationship_choice", "mystery_reveal", "goal_competition",
    "survival_dilemma", "progression", "comedy_misunderstanding",
    "speculative_dilemma", "emotional_reframing",
}

GENRE_ALIASES = {
    "青春校园恋爱": "campus_romance", "校园": "campus_romance",
    "情感": "romance", "言情": "romance", "现代言情": "romance",
    "悬疑": "mystery", "悬疑推理": "mystery", "推理": "mystery",
    "科幻": "science_fiction", "科幻小说": "science_fiction",
    "幻想": "fantasy", "奇幻": "fantasy", "玄幻": "fantasy",
}

GENRE_GUIDANCE = {
    "campus_romance": "人物关系靠具体处境与选择推进；校园日常细节必须改变理解、决定或后果。",
    "romance": "关系变化要有具体选择与代价；可以克制，但不能靠不说话反复延长篇幅。",
    "mystery": "谜题有可核对的线索、知情范围和公平揭示；禁止凭空出现关键答案。",
    "science_fiction": "科幻假设有明确规则、限制和代价；主角的选择利用或挑战这些规则。",
    "fantasy": "世界规则和能力限制稳定；奇观与人物选择相互作用。",
}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: object) -> str:
    data = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def visible_count(body: str) -> int:
    body = re.sub(r"(?m)^=== 第\d+集 ===\s*$", "", unicodedata.normalize("NFC", body))
    return sum(not c.isspace() for c in body)


def clean_body(body: str) -> str:
    if not isinstance(body, str):
        raise StoryError("正文必须是字符串")
    if not body.strip():
        raise StoryError("正文不能为空")
    return unicodedata.normalize("NFC", body).strip() + "\n"


def length_guidance(contract_or_length: dict) -> str:
    """Render planning targets separately from declared hard limits."""
    length = contract_or_length.get("length", contract_or_length)
    parts = [f"参考目标约{length['target']}个可见字符，用于安排篇幅；超出目标本身不构成超长。"]
    parts.append(f"整篇最低{length['min']}个可见字符。")
    maximum = length.get("max")
    if maximum is None:
        parts.append("未设整篇硬上限；按完整情节和自然表达完成，不为贴合目标截断场景或压缩导语。")
    else:
        source = length.get("hard_max_source") or {}
        label = {"author": "作者明确", "platform": "已核实投稿"}.get(source.get("kind"), "已声明")
        parts.append(f"{label}硬上限为{maximum}个可见字符；超过时报告整篇超长，保留完整导语和原稿。")
    parts.append("导语先保证读者看懂处境、欲望与吸引点，不按正文剩余字数削减必要信息。")
    return "".join(parts)


@dataclass
class StoryBrief:
    project_id: str = field(default_factory=lambda: str(uuid4()))
    schema_version: int = 2
    title: str = ""
    language: str = "zh-CN"
    audience: dict = field(default_factory=lambda: {"description": "中文故事读者", "expectations": []})
    genre: dict = field(default_factory=lambda: {"primary": "custom", "secondary": [], "custom_description": ""})
    story_engine: str = "emotional_reframing"
    format: str = "micro_story"
    length: dict = field(default_factory=dict)
    scene_count: dict = field(default_factory=dict)
    tone: str = "自然、具体"
    point_of_view: str = "third_person_limited"
    ending_mode: str = "closed"
    reader_promise: str = ""
    constraints: dict = field(default_factory=lambda: {"must_keep": [], "avoid": []})
    channels: list[str] = field(default_factory=lambda: ["generic_text"])
    author_input: dict = field(default_factory=lambda: {"premise": "", "locked_facts": []})
    characters: list[dict] = field(default_factory=list)
    world: dict = field(default_factory=dict)
    story_facts: list[dict] = field(default_factory=list)
    market_contract: dict = field(default_factory=dict)
    publication_contract: dict = field(default_factory=dict)
    research_preferences: dict = field(default_factory=dict)
    opening_mode: str = ""
    review_mode: str = "chief_editor"
    reader_rewards: list[dict] = field(default_factory=list)
    support_level: str = "experimental"

    @classmethod
    def from_dict(cls, raw: dict) -> "StoryBrief":
        if not isinstance(raw, dict):
            raise StoryError("brief.yaml 必须是 YAML 映射")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(raw) - allowed
        if unknown:
            raise StoryError(f"未知简报字段: {sorted(unknown)}")
        data = dict(raw)
        if isinstance(data.get("genre"), str):
            data["genre"] = {"primary": data["genre"]}
        brief = cls(**data)
        if brief.format not in FORMAT_DEFAULTS:
            raise StoryError(f"未知体裁: {brief.format}")
        if brief.opening_mode not in ('', 'lead_then_chapter', 'direct_scene'):
            raise StoryError('opening_mode 仅支持 lead_then_chapter/direct_scene')
        if brief.opening_mode and brief.format != 'short_story':
            raise StoryError('opening_mode 当前只用于完整短篇')
        if brief.review_mode not in ('chief_editor', 'deep_review'):
            raise StoryError('review_mode 仅支持 chief_editor/deep_review')
        if brief.story_engine not in ENGINES:
            raise StoryError(f"未知故事引擎: {brief.story_engine}")
        if not isinstance(brief.genre, dict):
            raise StoryError("genre 必须是题材配置")
        genre = dict(brief.genre)
        primary = str(genre.get("primary") or "custom").strip()
        genre["primary"] = GENRE_ALIASES.get(primary, primary)
        genre["secondary"] = [GENRE_ALIASES.get(str(x), str(x)) for x in (genre.get("secondary") or [])]
        genre["custom_description"] = str(genre.get("custom_description") or "")
        brief.genre = genre
        if not isinstance(brief.length, dict):
            raise StoryError("length 必须是映射")
        _, target, _ = FORMAT_DEFAULTS[brief.format]
        target = brief.length.get("target", target)
        if not isinstance(target, int) or target <= 0:
            raise StoryError("length.target 必须是正整数")
        length = {"unit": "visible_char_v1", "target": target,
                  "min": round(target * 0.8), "max": None,
                  "scope": "whole_story"}
        length.update(brief.length)
        if length["unit"] != "visible_char_v1" or length["scope"] != "whole_story":
            raise StoryError("当前只支持整篇 visible_char_v1 篇幅合同")
        if any(not isinstance(length[k], int) or length[k] <= 0 for k in ("min", "target")):
            raise StoryError("篇幅必须是正整数")
        if length["max"] is not None and (not isinstance(length["max"], int) or length["max"] < length["target"]):
            raise StoryError("length.max 必须不小于 target")
        if length["min"] > length["target"]:
            raise StoryError("length.min 必须不大于 target")
        source = length.get("hard_max_source")
        if source is not None:
            if (not isinstance(source, dict) or source.get("kind") not in ("author", "platform")
                or not isinstance(source.get("reference"), str) or not source["reference"].strip()
                or source.get("kind") == "platform" and source.get("verified") is not True):
                raise StoryError("length.hard_max_source 须记录作者要求或已核实投稿规则及具体来源")
            if length["max"] is None:
                raise StoryError("无硬上限时不能声明 hard_max_source")
        brief.length = length
        if not isinstance(brief.scene_count, dict):
            raise StoryError("scene_count 必须是映射")
        if brief.scene_count:
            minimum_scenes = brief.scene_count.get("min")
            maximum_scenes = brief.scene_count.get("max")
            if (brief.format != "short_story" or
                    not isinstance(minimum_scenes, int) or isinstance(minimum_scenes, bool) or
                    not isinstance(maximum_scenes, int) or isinstance(maximum_scenes, bool) or
                    not 1 <= minimum_scenes <= maximum_scenes):
                raise StoryError("短篇 scene_count 的 min/max 必须是正整数且 min 不大于 max")
            brief.scene_count = {"min": minimum_scenes, "max": maximum_scenes}
        if brief.ending_mode not in ("closed", "open_interpretation"):
            raise StoryError("ending_mode 仅支持 closed/open_interpretation")
        brief.characters = list(brief.characters or [])
        for index, character in enumerate(brief.characters):
            if not isinstance(character, dict):
                raise StoryError("角色记录必须是映射")
            character.setdefault("id", f"character_{index + 1}")
        ids = [c["id"] for c in brief.characters]
        if len(ids) != len(set(ids)):
            raise StoryError("角色 id 必须唯一")
        if not isinstance(brief.world, dict):
            raise StoryError("world 必须是映射")
        if not isinstance(brief.story_facts, list):
            raise StoryError("story_facts 必须是列表")
        fact_ids = set()
        for fact in brief.story_facts:
            if not isinstance(fact, dict) or not isinstance(fact.get("id"), str) or not fact["id"].strip():
                raise StoryError("故事事实必须有非空 id")
            if fact["id"] in fact_ids:
                raise StoryError("故事事实 id 必须唯一")
            fact_ids.add(fact["id"])
            if not isinstance(fact.get("statement"), str) or not fact["statement"].strip():
                raise StoryError("故事事实必须有非空 statement")
            if not isinstance(fact.get("critical", True), bool):
                raise StoryError("故事事实 critical 必须是布尔值")
            for field_name in ("required_text", "forbidden_text"):
                terms = fact.get(field_name, [])
                if not isinstance(terms, list) or any(not isinstance(term, str) or not term for term in terms):
                    raise StoryError(f"故事事实 {field_name} 必须是非空字符串列表")
                fact[field_name] = terms
            fact.setdefault("critical", True)
        brief.constraints = {"must_keep": list((brief.constraints or {}).get("must_keep") or []),
                             "avoid": list((brief.constraints or {}).get("avoid") or [])}
        author_input = brief.author_input or {}
        brief.author_input = {"premise": str(author_input.get("premise") or ""),
                              "locked_facts": list(author_input.get("locked_facts") or [])}
        if "entry_facts" in author_input:
            facts = author_input["entry_facts"]
            if (not isinstance(facts, list) or len(facts) > 6
                or any(not isinstance(fact, str) or not fact.strip() for fact in facts)):
                raise StoryError("author_input.entry_facts 须为至多六条非空导语前提")
            brief.author_input["entry_facts"] = list(dict.fromkeys(fact.strip() for fact in facts))
        if not brief.author_input["premise"] and not brief.reader_promise:
            raise StoryError("至少提供 author_input.premise 或 reader_promise")
        if not isinstance(brief.channels, list) or not brief.channels or any(
            not isinstance(channel, str) or not channel.strip() for channel in brief.channels
        ):
            raise StoryError("channels 必须是非空字符串列表")
        from .market import validate_market_contract
        brief.market_contract = validate_market_contract(
            brief.market_contract, brief.channels, brief.audience, brief.reader_promise)
        from .publication import validate_publication_contract
        brief.publication_contract = validate_publication_contract(
            brief.publication_contract, brief.channels, brief.length)
        from .research_guidance import validate_research_preferences
        brief.research_preferences = validate_research_preferences(brief.research_preferences)
        from .reader_rewards import validate_spec
        brief.reader_rewards = validate_spec(brief.reader_rewards)
        brief.support_level = "experimental"
        return brief

    def to_dict(self) -> dict:
        return asdict(self)

    def effective_contract(self) -> dict:
        result = self.to_dict()
        if not self.opening_mode:
            result.pop('opening_mode', None)
            if self.format == 'short_story' and 'fanqie_short' in self.channels:
                result['opening_mode'] = 'lead_then_chapter'
        if not self.scene_count:
            result.pop("scene_count", None)
        if not self.reader_rewards:
            result.pop('reader_rewards', None)
        if not self.research_preferences:
            # Empty opt-in settings must not invalidate existing author contracts.
            result.pop("research_preferences", None)
        from .publication import resolve_publication_contract, publication_guidance
        publication = resolve_publication_contract(result)
        if publication:
            result["publication_contract"] = publication
            result["publication_guidance"] = publication_guidance(result)
        else:
            # Empty opt-in fields must keep generic saved review hashes valid.
            result.pop("publication_contract", None)
        if self.market_contract:
            from .market import market_guidance, MARKET_SIGNAL_SOURCES
            result["market_guidance"] = market_guidance(result)
            result["market_basis"] = {"status": "editorial_hypothesis", "checked_at": "2026-09-30",
                                      "sources": MARKET_SIGNAL_SOURCES if self.market_contract["profile_id"].startswith("fanqie_") else [],
                                      "scope": "编辑试验，未验证读者留存、收益或平台准入"}
        else:
            # Preserve existing generic contract hashes and saved review validity.
            result.pop("market_contract", None)
        result["genre_guidance"] = [GENRE_GUIDANCE[g] for g in
                                    [self.genre["primary"], *self.genre["secondary"]]
                                    if g in GENRE_GUIDANCE]
        result["source"] = "brief.yaml"
        result["genre_schema_status"] = "built_in" if self.genre["primary"] in GENRE_GUIDANCE and all(
            g in GENRE_GUIDANCE for g in self.genre["secondary"]) else "custom_experimental"
        return result
