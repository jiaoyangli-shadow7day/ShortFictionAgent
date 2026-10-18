"""Commercial editor modules for campus romance.

这些模块补足第二阶段商业化能力：强开篇、硬事件、名场面、情绪兑现、
人设标签、配角功能、语言去重、长线悬念。
"""

import json
import re
from pathlib import Path
from ..config import load_yaml, load_markdown, save_markdown
from ..paths import ProjectPaths
from ..genres import get_genre_pack


class _CommercialBase:
    # 子类用 LEXICON_MAP 声明 "实例属性名 -> 题材包 lexicon key"。
    # 基类在 __init__ 里据此从 GenrePack 注入关键词表, 方法体照常用 self.XXX。
    LEXICON_MAP: dict = {}

    def __init__(self, paths: ProjectPaths, pack=None):
        self.paths = paths
        self.pack = pack or get_genre_pack()
        for attr, key in self.LEXICON_MAP.items():
            setattr(self, attr, self.pack.lex(key))

    def _get_chapter_outline(self, chapter_id: int) -> dict:
        outlines = load_yaml(self.paths.chapter_outline)
        if not outlines or "chapters" not in outlines:
            return {}
        for ch in outlines["chapters"]:
            if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                return ch
        return {}

    def _get_all_outlines(self) -> list:
        outlines = load_yaml(self.paths.chapter_outline)
        chapters = outlines.get("chapters", []) if isinstance(outlines, dict) else []
        return [ch for ch in chapters if isinstance(ch, dict)]

    def _chapter_text(self, chapter_id: int) -> str:
        return load_markdown(self.paths.chapter_file(chapter_id))

    def _result(self, name: str, score: int, warnings: list, suggestions: list) -> dict:
        score = max(0, min(100, int(score)))
        return {
            "module": name,
            "score": score,
            "passed": score >= 70,
            "warnings": warnings,
            "suggestions": suggestions,
        }


class OpeningHookAgent(_CommercialBase):
    """专治开篇弱。"""

    LEXICON_MAP = {"STRONG_HOOK_MARKERS": "opening_strong_hook"}

    def audit(self, chapter_id: int) -> dict:
        outline = self._get_chapter_outline(chapter_id)
        text = self._chapter_text(chapter_id)
        warnings, suggestions = [], []
        score = 100

        if chapter_id != 1:
            return self._result("OpeningHookAgent", 100, [], [])

        hook_fields = [
            str(outline.get("reader_hook", "")),
            str(outline.get("story_question", "")),
            str(outline.get("ending_hook", "")),
            str(outline.get("next_chapter_question", "")),
        ]
        hook_blob = " ".join(hook_fields)
        if not hook_blob.strip():
            warnings.append("首章大纲缺少明确开篇钩子。")
            suggestions.append("为首章补 3 个以上候选钩子：旧物、秘密、成绩危机、未来重逢或关系反差。")
            score -= 35
        elif not any(marker in hook_blob for marker in self.STRONG_HOOK_MARKERS):
            warnings.append("首章钩子偏温柔，缺少非看不可的问题。")
            suggestions.append("首章结尾应抛出强问题，例如旧物露出、日记反差、草稿纸秘密或成绩危机。")
            score -= 25

        first_1200 = text[:1200]
        if first_1200 and not any(marker in first_1200 for marker in self.STRONG_HOOK_MARKERS):
            warnings.append("正文前 1200 字缺少强商业信号。")
            suggestions.append("在前 1200 字内放出关系悬念、身份反差或危机苗头。")
            score -= 20

        if any(soft in str(outline.get("ending_hook", "")) for soft in ["没什么特别", "正好", "明天", "余味"]):
            warnings.append("首章尾钩仍偏文学余味。")
            suggestions.append("首章尾钩要让读者立刻想问“他/她到底藏了什么”。")
            score -= 15

        return self._result("OpeningHookAgent", score, warnings, suggestions)


class BeatHardnessScorer(_CommercialBase):
    """专治章节任务软。"""

    LEXICON_MAP = {"HARD_MARKERS": "beat_hard_markers", "SOFT_MARKERS": "beat_soft_markers"}

    def score_outline(self, chapter_id: int) -> dict:
        outline = self._get_chapter_outline(chapter_id)
        warnings, suggestions = [], []
        blob = " ".join(str(outline.get(k, "")) for k in [
            "main_event", "obstacle", "action", "cost", "conflict",
            "relationship_turn", "irreversible_change", "ending_hook",
            "famous_scene_payload", "supporting_pressure",
        ])
        hard_hits = sum(1 for marker in self.HARD_MARKERS if marker in blob)
        soft_hits = sum(1 for marker in self.SOFT_MARKERS if marker in blob)
        score = min(100, 45 + hard_hits * 12 - min(soft_hits, 4) * 5)
        declared_hardness = outline.get("beat_hardness")
        if isinstance(declared_hardness, int):
            score = round(score * 0.7 + min(100, declared_hardness * 20) * 0.3)

        if hard_hits == 0:
            warnings.append("本章事件硬度不足，像情绪递进而不是问题推进。")
            suggestions.append("加入外部压力、第三方误读、公开后果、失败代价或秘密暴露。")
        if "cost" in outline and len(str(outline.get("cost", "")).strip()) < 8:
            warnings.append("本章代价字段过轻或过空。")
            suggestions.append("把代价写成会改变关系、处境、信息差或自尊的具体后果。")
            score -= 10

        return self._result("BeatHardnessScorer", score, warnings, suggestions)

    def audit_cadence(self) -> dict:
        chapters = self._get_all_outlines()
        warnings, suggestions = [], []
        weak = []
        for ch in chapters:
            score = self.score_outline(int(ch.get("chapter_id", 0))).get("score", 0)
            if score < 60:
                weak.append(ch.get("chapter_id"))
        score = 100 - min(60, len(weak) * 5)
        if len(weak) >= 3:
            warnings.append(f"事件硬度偏低的章节较多: {weak[:12]}")
            suggestions.append("每 3 章至少安排一次外部压力或不可逆后果，每 5-8 章安排一次强爆点。")
        return self._result("BeatHardnessScorer:Cascade", score, warnings, suggestions)


class EarlyPacingGate(_CommercialBase):
    """前十章节奏门禁。

    这个门禁不强迫开篇变成狗血爆点，只要求前十章有足够的
    “回不去”的变化，避免连续氛围章节把读者缓慢劝退。
    """

    EARLY_WINDOW = 10
    MIN_IRREVERSIBLE_CHANGES = 3
    LEXICON_MAP = {
        "CHANGE_MARKERS": "early_change_markers",
        "VISIBLE_CONSEQUENCE": "early_visible_consequence",
    }

    def audit_project(self) -> dict:
        chapters = [ch for ch in self._get_all_outlines() if int(ch.get("chapter_id", 0)) <= self.EARLY_WINDOW]
        warnings, suggestions = [], []
        if not chapters:
            return self._result("EarlyPacingGate", 100, [], [])

        irreversible_hits = []
        visible_consequence_hits = []
        for ch in chapters:
            ch_id = int(ch.get("chapter_id", 0))
            blob = " ".join(str(ch.get(k, "")) for k in [
                "main_event", "cost", "conflict", "relationship_turn",
                "irreversible_change", "reader_reward", "ending_hook",
                "supporting_pressure", "famous_scene_payload",
            ])
            if any(marker in blob for marker in self.CHANGE_MARKERS):
                irreversible_hits.append(ch_id)
            if any(marker in blob for marker in self.VISIBLE_CONSEQUENCE):
                visible_consequence_hits.append(ch_id)

        score = 100
        if len(set(irreversible_hits)) < self.MIN_IRREVERSIBLE_CHANGES:
            warnings.append(
                f"前 {self.EARLY_WINDOW} 章不可逆变化不足: "
                f"{len(set(irreversible_hits))}/{self.MIN_IRREVERSIBLE_CHANGES}"
            )
            suggestions.append("前十章至少固定 3 个关系/信息差/处境回不去的节点，例如旧物秘密、成绩失败、第三方介入或公开后果。")
            score -= 35
        if len(set(visible_consequence_hits)) < 2:
            warnings.append("前十章可见外部后果不足，容易只有内心波动。")
            suggestions.append("至少安排 2 个别人也能看见的后果：老师谈话、成绩排名、同学误读、任务失败或旧物被发现。")
            score -= 20

        return self._result("EarlyPacingGate", score, warnings, suggestions)


class ProtagonistDesireAgent(_CommercialBase):
    """检查男女主是否都有独立欲望线，而不是只围绕恋爱确认。"""

    LEXICON_MAP = {
        "FEMALE_INDEPENDENT_MARKERS": "female_independent",
        "MALE_INDEPENDENT_MARKERS": "male_independent",
        "ROMANCE_ONLY_MARKERS": "romance_only",
    }

    def audit_outline(self, chapter_id: int) -> dict:
        outline = self._get_chapter_outline(chapter_id)
        male = load_yaml(self.paths.protagonist_male)
        female = load_yaml(self.paths.protagonist_female)
        warnings, suggestions = [], []
        score = 100

        blob = " ".join(str(outline.get(k, "")) for k in [
            "character_desire", "obstacle", "action", "cost", "conflict",
            "character_growth", "relationship_turn", "reader_reward",
            "supporting_pressure", "famous_scene_payload",
        ])
        female_profile = self._profile_text(female)
        male_profile = self._profile_text(male)

        female_has_independent_profile = any(m in female_profile for m in self.FEMALE_INDEPENDENT_MARKERS)
        female_has_independent_chapter = any(m in blob for m in self.FEMALE_INDEPENDENT_MARKERS)
        male_has_independent_profile = any(m in male_profile for m in self.MALE_INDEPENDENT_MARKERS)
        male_has_independent_chapter = any(m in blob for m in self.MALE_INDEPENDENT_MARKERS)

        if self._looks_romance_only(female_profile) and not female_has_independent_profile:
            warnings.append("女主角色设定的核心欲望偏恋爱确认，缺少独立主线。")
            suggestions.append("给女主设定长期欲望：艺术/专业目标、家庭压力、自尊、偏科补强或未来选择。")
            score -= 25
        if not female_has_independent_chapter:
            warnings.append("本章没有推进女主独立欲望线。")
            suggestions.append("即使是恋爱章节，也要让女主的创作、家庭、自尊、成绩或未来选择至少被压到一次。")
            score -= 15
        if not male_has_independent_profile:
            warnings.append("男主角色设定缺少清晰独立欲望线。")
            suggestions.append("明确男主除感情外的长期目标，例如大学、成绩、家庭期待或自我选择。")
            score -= 15
        if not male_has_independent_chapter and chapter_id % 3 == 0:
            warnings.append("连续推进中男主独立欲望线存在感偏弱。")
            suggestions.append("每 2-3 章让男主的成绩、家庭、未来或自控问题承担一次真实压力。")
            score -= 10

        return self._result("ProtagonistDesireAgent", score, warnings, suggestions)

    def audit_project(self) -> dict:
        chapters = self._get_all_outlines()
        warnings, suggestions = [], []
        score = 100
        if not chapters:
            return self._result("ProtagonistDesireAgent:Cascade", 100, [], [])

        female_hits = []
        male_hits = []
        for ch in chapters[:12]:
            ch_id = int(ch.get("chapter_id", 0))
            blob = " ".join(str(ch.get(k, "")) for k in [
                "character_desire", "obstacle", "action", "cost", "conflict",
                "character_growth", "relationship_turn", "supporting_pressure",
            ])
            if any(m in blob for m in self.FEMALE_INDEPENDENT_MARKERS):
                female_hits.append(ch_id)
            if any(m in blob for m in self.MALE_INDEPENDENT_MARKERS):
                male_hits.append(ch_id)

        if len(set(female_hits)) < 3:
            warnings.append(f"前 12 章女主独立欲望线推进不足: {female_hits}")
            suggestions.append("把女主的创作/偏科/家庭压力更早主线化，至少在前 12 章出现 3 次可见推进。")
            score -= 35
        if len(set(male_hits)) < 3:
            warnings.append(f"前 12 章男主独立欲望线推进不足: {male_hits}")
            suggestions.append("把男主的成绩、家庭期待、大学选择或失控风险更早做成外部压力。")
            score -= 20

        return self._result("ProtagonistDesireAgent:Cascade", score, warnings, suggestions)

    def _profile_text(self, profile: dict) -> str:
        parts = []
        for value in profile.values():
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
            else:
                parts.append(str(value))
        return " ".join(parts)

    def _looks_romance_only(self, text: str) -> bool:
        return any(marker in text for marker in self.ROMANCE_ONLY_MARKERS)


class TextEventHardnessAgent(_CommercialBase):
    """正文级事件硬度检查。

    防止章纲写得很硬，正文却只剩漂亮观察和心理解释。
    """

    LEXICON_MAP = {
        "DESIRE_MARKERS": "text_desire",
        "OBSTACLE_MARKERS": "text_obstacle",
        "ACTION_MARKERS": "text_action",
        "COST_MARKERS": "text_cost",
        "TURN_MARKERS": "text_turn",
        "VISIBLE_CONSEQUENCE": "text_visible_consequence",
    }

    def audit_chapter(self, chapter_id: int) -> dict:
        text = self._chapter_text(chapter_id)
        warnings, suggestions = [], []
        if not text:
            return self._result("TextEventHardnessAgent", 100, [], [])

        score = 100
        checks = [
            ("欲望", self.DESIRE_MARKERS, "补清本章人物想要什么，最好落实到一个可见目标。"),
            ("阻碍", self.OBSTACLE_MARKERS, "补一个具体阻碍，不要只靠不敢说和心里乱。"),
            ("行动", self.ACTION_MARKERS, "让人物做出能被别人看见的动作，而不是只观察/回想。"),
            ("代价", self.COST_MARKERS, "写出行动后的代价：成绩、公开后果、自尊刺痛、关系误读或实际损失。"),
            ("关系转向", self.TURN_MARKERS, "明确本章之后信息差、关系或选择哪里变了。"),
        ]
        for label, markers, suggestion in checks:
            if not any(marker in text for marker in markers):
                warnings.append(f"正文缺少清楚的{label}信号。")
                suggestions.append(suggestion)
                score -= 12

        visible_consequence_hits = sum(text.count(marker) for marker in self.VISIBLE_CONSEQUENCE)
        if visible_consequence_hits < 2:
            warnings.append("正文可见后果偏弱，事件主要停留在内心。")
            suggestions.append("至少让一个第三方、成绩单、老师谈话、物品位置或公开场合承接本章后果。")
            score -= 15

        return self._result("TextEventHardnessAgent", score, warnings, suggestions)


class FamousSceneAgent(_CommercialBase):
    """专治名场面不炸。"""

    LEXICON_MAP = {"SCENE_TYPES": "famous_scene_types"}

    def audit_outline(self, chapter_id: int) -> dict:
        outline = self._get_chapter_outline(chapter_id)
        warnings, suggestions = [], []
        score = 100
        viral_scene = str(outline.get("viral_scene", ""))
        scene_type = str(outline.get("famous_scene_type", ""))
        scene_payload = str(outline.get("famous_scene_payload", ""))
        line_goal = str(outline.get("quotable_line_goal", ""))
        reward = str(outline.get("reader_reward", ""))

        if not viral_scene.strip() and not scene_payload.strip():
            warnings.append("本章缺少可切片名场面设计。")
            suggestions.append("补一个可被短视频剪出来的场景：公开维护、秘密暴露、第一次破例或旧物回收。")
            score -= 30
        elif not any(t in viral_scene + reward + scene_type + scene_payload for t in self.SCENE_TYPES):
            warnings.append("名场面类型不够清晰，可能只是漂亮场景。")
            suggestions.append("明确名场面类型，并写出冲突双方、第三方视角、情绪爆点和后续影响。")
            score -= 15

        if not line_goal.strip():
            warnings.append("缺少可传播台词/金句目标。")
            suggestions.append("为本章设计一句能截图传播的克制台词，不要写成空泛旁白。")
            score -= 20

        return self._result("FamousSceneAgent", score, warnings, suggestions)

    def audit_cadence(self) -> dict:
        chapters = self._get_all_outlines()
        strong = []
        for ch in chapters:
            blob = str(ch.get("viral_scene", "")) + str(ch.get("reader_reward", "")) + str(ch.get("famous_scene_type", "")) + str(ch.get("famous_scene_payload", ""))
            intensity = ch.get("emotional_intensity", 0)
            if any(t in blob for t in self.SCENE_TYPES) or (isinstance(intensity, int) and intensity >= 4):
                strong.append(int(ch.get("chapter_id", 0)))

        warnings, suggestions = [], []
        score = 100
        if len(strong) >= 2:
            gaps = [b - a for a, b in zip(strong, strong[1:])]
            too_long = [gap for gap in gaps if gap > 8]
            if too_long:
                warnings.append(f"名场面间隔过长: {too_long}")
                suggestions.append("每 5-8 章至少安排一次明确名场面，避免全靠细腻日常。")
                score -= min(40, len(too_long) * 10)
        elif chapters:
            warnings.append("全卷名场面数量不足。")
            suggestions.append("先在章节拍表里固定每 5-8 章的强爆点。")
            score -= 40
        return self._result("FamousSceneAgent:Cascade", score, warnings, suggestions)


class PayoffLedger(_CommercialBase):
    """专治甜点/虐点不兑现。"""

    LEXICON_MAP = {"PAYOFF_MARKERS": "payoff_markers", "DEBT_MARKERS": "debt_markers"}

    def build_from_outlines(self) -> list:
        ledger = []
        for ch in self._get_all_outlines():
            chapter_id = int(ch.get("chapter_id", 0))
            blob = " ".join(str(ch.get(k, "")) for k in ["reader_reward", "emotional_payoff", "ending_hook", "relationship_turn", "payoff_type", "payoff_debt", "payoff_visibility"])
            debt = [m for m in self.DEBT_MARKERS if m in blob]
            payoff = [m for m in self.PAYOFF_MARKERS if m in blob]
            if debt or payoff:
                ledger.append({
                    "chapter_id": chapter_id,
                    "debt_markers": debt,
                    "payoff_markers": payoff,
                    "summary": blob[:160],
                })
        return ledger

    def audit_cadence(self) -> dict:
        ledger = self.build_from_outlines()
        warnings, suggestions = [], []
        score = 100
        payoff_chapters = [item["chapter_id"] for item in ledger if item["payoff_markers"]]
        if len(payoff_chapters) >= 2:
            gaps = [b - a for a, b in zip(payoff_chapters, payoff_chapters[1:])]
            long_gaps = [gap for gap in gaps if gap > 4]
            if long_gaps:
                warnings.append(f"情绪兑现间隔偏长: {long_gaps}")
                suggestions.append("每 3-4 章给一次明确小兑现：特殊对待、主动靠近、公开维护或旧物回收。")
                score -= min(50, len(long_gaps) * 10)
        elif self._get_all_outlines():
            warnings.append("章节拍表里几乎没有明确甜点/虐点兑现。")
            suggestions.append("建立 PayoffLedger，标注每章制造的情绪债和偿还节点。")
            score -= 45
        return self._result("PayoffLedger", score, warnings, suggestions)

    def save(self) -> Path:
        path = self.paths.memory_dir / "payoff_ledger.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.build_from_outlines(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


class CharacterTagPressure(_CommercialBase):
    """专治人设标签不锋利。"""

    LEXICON_MAP = {
        "TAG_CANDIDATES": "character_tag_candidates",
        "TAG_COLLIDE": "character_tag_collide",
    }

    def audit_outline(self, chapter_id: int) -> dict:
        outline = self._get_chapter_outline(chapter_id)
        male = load_yaml(self.paths.protagonist_male)
        female = load_yaml(self.paths.protagonist_female)
        warnings, suggestions = [], []
        score = 100
        blob = " ".join(str(outline.get(k, "")) for k in [
            "character_desire", "obstacle", "action", "character_growth",
            "relationship_turn", "reader_reward", "viral_scene",
        ])

        male_tags = self._extract_tags(male)
        female_tags = self._extract_tags(female)
        explicit_pressure = str(outline.get("character_tag_pressure", "")).strip()
        male_hit = bool(explicit_pressure) or any(tag and tag in blob for tag in male_tags)
        female_hit = bool(explicit_pressure) or any(tag and tag in blob for tag in female_tags)

        if not male_hit:
            warnings.append("本章没有明显压到男主人设标签。")
            suggestions.append("让男主的核心标签被挑战、反转或兑现，例如完美学霸崩盘/破例/失控。")
            score -= 20
        if not female_hit:
            warnings.append("本章没有明显压到女主人设标签。")
            suggestions.append("让女主的核心标签被挑战、反转或兑现，例如画画、自尊、成绩压力或主动选择。")
            score -= 20
        if male_hit and female_hit and not any(w in blob for w in self.TAG_COLLIDE):
            warnings.append("男女主人设都有出现，但没有形成标签对撞。")
            suggestions.append("把两人的标签设计成互相理解或互相刺痛的关系推进点。")
            score -= 10

        return self._result("CharacterTagPressure", score, warnings, suggestions)

    def _extract_tags(self, profile: dict) -> list:
        text = " ".join(str(profile.get(k, "")) for k in [
            "role", "personality", "core_desire", "core_fear", "growth_arc", "background"
        ])
        return [c for c in self.TAG_CANDIDATES if c in text]


class RivalFunctionGuard(_CommercialBase):
    """专治配角工具人。"""

    LEXICON_MAP = {"RIVAL_NAMES": "rival_names", "FUNCTION_MARKERS": "rival_function"}

    def audit_outline(self, chapter_id: int) -> dict:
        outline = self._get_chapter_outline(chapter_id)
        warnings, suggestions = [], []
        blob = " ".join(str(v) for v in outline.values())
        if not any(name in blob for name in self.RIVAL_NAMES):
            return self._result("RivalFunctionGuard", 100, [], [])

        score = 100
        rival_function = str(outline.get("rival_function", "")) + str(outline.get("supporting_pressure", ""))
        combined = blob + rival_function
        if not any(marker in combined for marker in self.FUNCTION_MARKERS):
            warnings.append("配角/竞争者出现了，但功能像工具人。")
            suggestions.append("配角必须代表另一种可能，改变主角选择，或逼男女主承认关系弱点。")
            score -= 35
        if "嫉妒" in combined and not any(marker in combined for marker in ["选择", "价值", "公开", "承认"]):
            warnings.append("配角只制造嫉妒，缺少价值竞争。")
            suggestions.append("让配角在能力、公开表达、理解女主/男主方面提供真实竞争。")
            score -= 20
        return self._result("RivalFunctionGuard", score, warnings, suggestions)


class StyleDedupeAgent(_CommercialBase):
    """专治 AI 句式和意象重复。"""

    LEXICON_MAP = {"PHRASES": "style_phrases", "IMAGES": "style_images"}

    def audit_single_chapter(self, chapter_id: int) -> dict:
        """只看当前这一章的绝对重复(单章可归因、单章可修)。

        与 audit_chapter 的滚动窗口不同: 滚动窗口会把"前几章的重复"算到
        当前章头上, 导致重写当前章也无法让门禁通过(根因之一)。
        这个单章版本只统计本章自身的意象/句式过度使用, 重写本章即可修复,
        因此适合作为"写完一章立刻判定"的前移硬门禁。
        """
        text = self._chapter_text(chapter_id)
        warnings, suggestions = [], []
        score = 100
        if not text:
            return self._result("StyleDedupeAgent:Single", 100, [], [])

        phrase_counts = {p: text.count(p) for p in self.PHRASES}
        image_counts = {p: text.count(p) for p in self.IMAGES}
        # 单章阈值: 一章约 2000-4000 字, 同一意象出现 6+ 次即过度;
        # 解释型句式("不是""只是""没有说话"等)出现 5+ 次即过度。
        repeated_phrases = [f"{p}({c})" for p, c in phrase_counts.items() if c >= 5]
        repeated_images = [f"{p}({c})" for p, c in image_counts.items() if c >= 6]

        if repeated_phrases:
            warnings.append("本章重复句式偏多: " + "、".join(repeated_phrases[:8]))
            suggestions.append("减少解释型心理句, 改用选择动作、对话打断、第三方误读或物品位置变化。")
            score -= min(35, len(repeated_phrases) * 10)
        if repeated_images:
            warnings.append("本章重复意象偏多: " + "、".join(repeated_images[:8]))
            suggestions.append("给本章建立专属感官系统, 少用窗、光、风、草稿纸反复收尾。")
            score -= min(35, len(repeated_images) * 8)

        return self._result("StyleDedupeAgent:Single", score, warnings, suggestions)

    def audit_chapter(self, chapter_id: int, recent_n: int = 5) -> dict:
        warnings, suggestions = [], []
        score = 100
        texts = []
        for ch in range(max(1, chapter_id - recent_n + 1), chapter_id + 1):
            text = self._chapter_text(ch)
            if text:
                texts.append(text)
        combined = "\n".join(texts)
        if not combined:
            return self._result("StyleDedupeAgent", 100, [], [])

        phrase_counts = {p: combined.count(p) for p in self.PHRASES}
        image_counts = {p: combined.count(p) for p in self.IMAGES}
        repeated_phrases = [f"{p}({c})" for p, c in phrase_counts.items() if c >= 4]
        repeated_images = [f"{p}({c})" for p, c in image_counts.items() if c >= 8]

        if repeated_phrases:
            warnings.append("近几章重复句式偏多: " + "、".join(repeated_phrases[:8]))
            suggestions.append("减少解释型心理句，改用选择动作、对话打断、第三方误读或物品位置变化。")
            score -= min(35, len(repeated_phrases) * 8)
        if repeated_images:
            warnings.append("近几章重复意象偏多: " + "、".join(repeated_images[:8]))
            suggestions.append("建立本章专属感官系统，少用窗、光、风、草稿纸收尾。")
            score -= min(30, len(repeated_images) * 6)

        return self._result("StyleDedupeAgent", score, warnings, suggestions)


class LongSuspensePlanner(_CommercialBase):
    """专治长线悬念普通。"""

    REQUIRED = ["long_suspense", "reveal_schedule", "misdirection", "payoff"]
    LEXICON_MAP = {"MARKERS": "long_suspense_markers"}

    def audit_book(self) -> dict:
        outline = load_markdown(self.paths.master_outline)
        warnings, suggestions = [], []
        score = 100
        if not outline:
            return self._result("LongSuspensePlanner", 0, ["找不到全书大纲。"], ["请先生成 master_outline.md。"])

        if not any(marker in outline for marker in self.MARKERS):
            warnings.append("全书大纲缺少长线悬念信号。")
            suggestions.append("设计一个贯穿全书的旧物/秘密/误会/未来重逢悬念，并拆成埋设、误导、局部揭示、最终回收。")
            score -= 40
        if "长线悬念" not in outline and "全书级长悬念" not in outline:
            warnings.append("全书大纲没有独立标出长线悬念。")
            suggestions.append("在全书大纲中新增长线悬念表：开篇埋点、中段误导、揭示节点、结尾回收。")
            score -= 30
        return self._result("LongSuspensePlanner", score, warnings, suggestions)

    def audit_outline(self, chapter_id: int) -> dict:
        outline = self._get_chapter_outline(chapter_id)
        warnings, suggestions = [], []
        score = 100
        blob = " ".join(str(outline.get(k, "")) for k in ["foreshadowing_new", "foreshadowing_recall", "ending_hook", "next_chapter_question", "long_suspense_role", "long_suspense_clue"])
        if not any(marker in blob for marker in self.MARKERS):
            warnings.append("本章没有参与长线悬念埋设/误导/回收。")
            suggestions.append("至少用旧物、秘密、误会、日记、画稿、草稿纸等元素推进一条长线悬念。")
            score -= 20
        return self._result("LongSuspensePlanner:Chapter", score, warnings, suggestions)


class CommercialQualitySuite(_CommercialBase):
    """汇总商业编辑模块。"""

    def __init__(self, paths: ProjectPaths, pack=None):
        super().__init__(paths, pack)
        pack = self.pack  # 解析后的题材包, 传给所有子模块, 保证一致
        self.opening_hook = OpeningHookAgent(paths, pack)
        self.early_pacing = EarlyPacingGate(paths, pack)
        self.beat_hardness = BeatHardnessScorer(paths, pack)
        self.famous_scene = FamousSceneAgent(paths, pack)
        self.payoff_ledger = PayoffLedger(paths, pack)
        self.protagonist_desire = ProtagonistDesireAgent(paths, pack)
        self.character_tag = CharacterTagPressure(paths, pack)
        self.rival_guard = RivalFunctionGuard(paths, pack)
        self.style_dedupe = StyleDedupeAgent(paths, pack)
        self.text_event_hardness = TextEventHardnessAgent(paths, pack)
        self.long_suspense = LongSuspensePlanner(paths, pack)

    def audit_outline(self, chapter_id: int) -> dict:
        modules = [
            self.opening_hook.audit(chapter_id),
            self.beat_hardness.score_outline(chapter_id),
            self.famous_scene.audit_outline(chapter_id),
            self.protagonist_desire.audit_outline(chapter_id),
            self.character_tag.audit_outline(chapter_id),
            self.rival_guard.audit_outline(chapter_id),
            self.long_suspense.audit_outline(chapter_id),
        ]
        return self._combine(f"第 {chapter_id} 章商业编辑质检", modules)

    def audit_chapter_text(self, chapter_id: int) -> dict:
        """单章正文质检(前移硬门禁): 只用单章可归因、单章可修的信号。

        事件硬度+外部后果(TextEventHardnessAgent)和单章重复(StyleDedupe:Single)
        都只看本章, 重写本章即可修复, 因此适合"写完一章立刻判定→不合格立刻
        返修"的闭环。跨章累积的风格重复改由 audit_rolling_style 在阶段门禁里
        以"近 N 章"为返修范围处理。
        """
        modules = [
            self.text_event_hardness.audit_chapter(chapter_id),
            self.style_dedupe.audit_single_chapter(chapter_id),
        ]
        return self._combine(f"第 {chapter_id} 章正文商业质检", modules)

    def audit_rolling_style(self, chapter_id: int, recent_n: int = 5) -> dict:
        """跨章风格去重(阶段门禁用): 统计近 N 章的意象/句式累积重复。

        返修范围是整个窗口而非单章, 因此交给阶段门禁(每 5 章)处理,
        避免把前几章的重复算到当前单章头上、导致单章返修永远修不掉。
        """
        return self.style_dedupe.audit_chapter(chapter_id, recent_n=recent_n)

    def audit_project(self) -> dict:
        modules = [
            self.early_pacing.audit_project(),
            self.beat_hardness.audit_cadence(),
            self.famous_scene.audit_cadence(),
            self.payoff_ledger.audit_cadence(),
            self.protagonist_desire.audit_project(),
            self.long_suspense.audit_book(),
        ]
        self.payoff_ledger.save()
        return self._combine("全书商业结构质检", modules)

    def save_chapter_report(self, chapter_id: int, outline_result: dict | None = None, text_result: dict | None = None) -> Path:
        if outline_result is None:
            outline_result = self.audit_outline(chapter_id)
        if text_result is None:
            text_result = self.audit_chapter_text(chapter_id)
        path = self.paths.reviews_dir / f"chapter_{chapter_id:03d}_commercial.md"
        save_markdown(path, self._format_report([outline_result, text_result], title=f"第 {chapter_id} 章商业编辑报告"))
        return path

    def save_project_report(self, result: dict | None = None) -> Path:
        if result is None:
            result = self.audit_project()
        path = self.paths.story_dir / "commercial_quality_report.md"
        save_markdown(path, self._format_report([result], title="全书商业质量报告"))
        return path

    def _combine(self, title: str, modules: list) -> dict:
        scores = [m.get("score", 0) for m in modules]
        warnings = []
        suggestions = []
        for module in modules:
            warnings.extend([f"{module.get('module')}: {w}" for w in module.get("warnings", [])])
            suggestions.extend([f"{module.get('module')}: {s}" for s in module.get("suggestions", [])])
        score = round(sum(scores) / len(scores), 1) if scores else 0
        return {
            "title": title,
            "score": score,
            "passed": score >= 70 and not warnings,
            "required": score < 70,
            "modules": modules,
            "warnings": warnings,
            "suggestions": suggestions,
        }

    def _format_report(self, results: list, title: str) -> str:
        lines = [f"# {title}", ""]
        for result in results:
            lines.extend([
                f"## {result.get('title', '质检')}",
                "",
                f"- 结果: {'通过' if result.get('passed') else '未通过'}",
                f"- 分数: {result.get('score', 0)}/100",
                "",
            ])
            for module in result.get("modules", []):
                lines.append(f"### {module.get('module')} - {module.get('score')}/100")
                for warning in module.get("warnings", []):
                    lines.append(f"- 问题: {warning}")
                for suggestion in module.get("suggestions", []):
                    lines.append(f"- 建议: {suggestion}")
                if not module.get("warnings") and not module.get("suggestions"):
                    lines.append("- 通过")
                lines.append("")
        return "\n".join(lines)

