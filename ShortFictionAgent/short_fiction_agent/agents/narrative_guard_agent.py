"""NarrativeGuardAgent - 商业追读结构质检。

这个 Agent 不评价文笔漂不漂亮，只检查故事骨架是否足够驱动读者继续读。
"""

import re
from pathlib import Path
from ..config import load_yaml, load_markdown, save_markdown
from ..paths import ProjectPaths
from ..genres import get_genre_pack
from .commercial_editor_agent import CommercialQualitySuite


class NarrativeGuardAgent:
    """检查章节大纲和正文是否具备追读引擎。

    追读"结构字段"(reader_hook/obstacle/cost…)是题材无关的故事骨架, 留在代码里;
    但"哪些尾钩算情绪余味、哪些冲突词算软"是题材知识, 从 GenrePack 注入。
    """

    # 结构字段是题材无关的故事骨架
    REQUIRED_OUTLINE_FIELDS = [
        "reader_hook",
        "story_question",
        "character_desire",
        "obstacle",
        "action",
        "cost",
        "conflict",
        "relationship_turn",
        "irreversible_change",
        "reader_reward",
        "ending_hook",
        "next_chapter_question",
    ]

    PASS_SCORE = 70

    def __init__(self, paths: ProjectPaths, pack=None):
        self.paths = paths
        self.pack = pack or get_genre_pack()
        self.commercial_suite = CommercialQualitySuite(paths, self.pack)
        # 题材相关的判定词表(从 GenrePack 注入)
        self.WEAK_ENDING_PATTERNS = self.pack.lex("ng_weak_ending")
        self.WEAK_CONFLICT_WEAK = self.pack.lex("ng_weak_conflict_weak")
        self.WEAK_CONFLICT_STRONG = self.pack.lex("ng_weak_conflict_strong")
        self.MOOD_HOOK_STRONG = self.pack.lex("ng_mood_hook_strong")
        self.WEAK_CHANGE_WEAK = self.pack.lex("ng_weak_change_weak")
        self.WEAK_CHANGE_CONCRETE = self.pack.lex("ng_weak_change_concrete")
        self.REPETITIVE_SILENCE = self.pack.lex("ng_repetitive_silence")
        self.MOOD_TAIL_STRONG = self.pack.lex("ng_mood_tail_strong")
        self.MOOD_TAIL_MOOD = self.pack.lex("ng_mood_tail_mood")

    def audit_outline(self, chapter_id: int) -> dict:
        """检查单章大纲是否具备追读结构。"""
        chapter = self._get_chapter_outline(chapter_id)
        if not chapter:
            return {
                "chapter_id": chapter_id,
                "score": 0,
                "passed": False,
                "warnings": ["找不到章节大纲"],
                "suggestions": ["请先生成 chapter_outline.yaml"],
            }

        warnings = []
        suggestions = []
        score = 100

        for field in self.REQUIRED_OUTLINE_FIELDS:
            value = chapter.get(field)
            if self._is_empty(value):
                warnings.append(f"缺少结构字段: {field}")
                suggestions.append(f"为 {field} 补一个具体、可执行的设计，不要只写情绪词。")
                score -= 6

        intensity = chapter.get("emotional_intensity")
        if not isinstance(intensity, int) or intensity < 1 or intensity > 5:
            warnings.append("emotional_intensity 必须是 1-5 的整数")
            suggestions.append("给本章标注情绪强度：1 喘息，3 推进，5 爆点。")
            score -= 5

        conflict = str(chapter.get("conflict", "")).strip()
        obstacle = str(chapter.get("obstacle", "")).strip()
        if self._looks_weak_conflict(conflict) and self._looks_weak_conflict(obstacle):
            warnings.append("冲突/阻碍过轻，容易变成纯氛围章节")
            suggestions.append("把阻碍写成会改变选择、关系或局面的具体压力。")
            score -= 12

        ending_hook = str(chapter.get("ending_hook", "")).strip()
        next_question = str(chapter.get("next_chapter_question", "")).strip()
        if self._looks_like_mood_only_hook(ending_hook) and not next_question:
            warnings.append("尾钩偏情绪余韵，没有形成明确的下一章问题")
            suggestions.append("尾钩要让读者问“然后呢”，例如秘密露出、选择代价或关系被看见。")
            score -= 10

        irreversible = str(chapter.get("irreversible_change", "")).strip()
        if self._looks_weak_change(irreversible):
            warnings.append("不可逆变化不够明确")
            suggestions.append("写清本章之后，人物关系、处境或信息差具体哪里回不去了。")
            score -= 8

        score = max(0, min(100, score))
        result = {
            "chapter_id": chapter_id,
            "score": score,
            "passed": score >= self.PASS_SCORE and not self._has_missing_core(warnings),
            "warnings": warnings,
            "suggestions": suggestions,
        }
        return self._merge_commercial_result(result, self.commercial_suite.audit_outline(chapter_id))

    def audit_all_outlines(self) -> dict:
        """检查所有章节大纲，并保存报告。"""
        outlines = load_yaml(self.paths.chapter_outline)
        chapters = outlines.get("chapters", []) if isinstance(outlines, dict) else []
        results = []
        for ch in chapters:
            if isinstance(ch, dict) and ch.get("chapter_id"):
                results.append(self.audit_outline(int(ch["chapter_id"])))

        failed = [r for r in results if not r.get("passed")]
        report = {
            "total": len(results),
            "failed": len(failed),
            "avg_score": round(sum(r["score"] for r in results) / len(results), 1) if results else 0,
            "results": results,
        }
        self._save_outline_report(report)
        try:
            self.commercial_suite.save_project_report()
        except Exception:
            pass
        return report

    def audit_chapter_text(self, chapter_id: int) -> dict:
        """轻量检查正文是否可能落入“漂亮废稿”。"""
        text = load_markdown(self.paths.chapter_file(chapter_id))
        outline = self._get_chapter_outline(chapter_id)
        warnings = []
        suggestions = []
        score = 100

        if not text:
            return {
                "chapter_id": chapter_id,
                "score": 0,
                "passed": False,
                "warnings": ["找不到章节正文"],
                "suggestions": ["请先生成章节正文。"],
            }

        outline_guard = self.audit_outline(chapter_id)
        if not outline_guard.get("passed"):
            warnings.append("对应章节大纲追读结构未通过，正文容易继承“有氛围无骨架”的问题")
            suggestions.append("先补齐章纲里的读者钩子、故事问题、欲望、阻碍、行动、代价和不可逆变化，再重写正文。")
            score -= min(35, max(15, len(outline_guard.get("warnings", [])) * 3))

        dialogue_count = len(re.findall(r"[“\"].{1,80}?[”\"]", text))
        if dialogue_count < 6:
            warnings.append("对话数量偏少，人物可能主要靠旁白和观察推进")
            suggestions.append("增加能暴露欲望、误会、试探或立场的自然对话。")
            score -= 10

        if self._contains_repetitive_silence(text):
            warnings.append("沉默/观察/没说出口的表达重复偏多")
            suggestions.append("把至少一处沉默改成行动、误会、维护、发现或代价。")
            score -= 10

        if self._has_mood_only_ending(text):
            warnings.append("结尾更像情绪空镜，下一章问题不够明确")
            suggestions.append("把结尾改成新信息露出、秘密被看见、选择代价出现或关系被第三方捕捉。")
            score -= 10

        if outline:
            for field in ["reader_reward", "relationship_turn", "irreversible_change"]:
                expected = str(outline.get(field, "")).strip()
                if expected and not self._contains_any_keyword(text, expected):
                    warnings.append(f"正文可能没有兑现大纲字段: {field}")
                    suggestions.append(f"检查正文是否明确写出 {field} 对应的情节效果。")
                    score -= 5

        score = max(0, min(100, score))
        result = {
            "chapter_id": chapter_id,
            "score": score,
            "passed": score >= self.PASS_SCORE,
            "warnings": warnings,
            "suggestions": suggestions,
        }
        return self._merge_commercial_result(result, self.commercial_suite.audit_chapter_text(chapter_id))

    def save_chapter_text_report(self, chapter_id: int, result: dict | None = None) -> Path:
        """保存正文追读质检报告。"""
        if result is None:
            result = self.audit_chapter_text(chapter_id)
        report_path = self.paths.reviews_dir / f"chapter_{chapter_id:03d}_narrative_guard.md"
        save_markdown(report_path, self._format_single_report(result, title=f"第 {chapter_id} 章追读质检"))
        try:
            self.commercial_suite.save_chapter_report(chapter_id)
        except Exception:
            pass
        return report_path

    def _merge_commercial_result(self, base: dict, commercial: dict) -> dict:
        """合并商业编辑层的结果，保留 NarrativeGuard 的主判断。"""
        warnings = list(base.get("warnings", []))
        suggestions = list(base.get("suggestions", []))
        warnings.extend(commercial.get("warnings", []))
        suggestions.extend(commercial.get("suggestions", []))
        base_score = float(base.get("score", 0))
        commercial_score = float(commercial.get("score", 0))
        combined_score = round(base_score * 0.65 + commercial_score * 0.35, 1)
        return {
            **base,
            "score": combined_score,
            "passed": combined_score >= self.PASS_SCORE and base.get("passed", False) and commercial.get("score", 0) >= 60,
            "warnings": warnings,
            "suggestions": suggestions,
            "commercial_quality": commercial,
        }

    def _get_chapter_outline(self, chapter_id: int) -> dict:
        outlines = load_yaml(self.paths.chapter_outline)
        if not outlines or "chapters" not in outlines:
            return {}
        for ch in outlines["chapters"]:
            if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                return ch
        return {}

    def _save_outline_report(self, report: dict):
        path = self.paths.story_dir / "narrative_guard_report.md"
        lines = [
            "# 章节大纲追读质检报告",
            "",
            f"- 总章节数: {report['total']}",
            f"- 未通过: {report['failed']}",
            f"- 平均分: {report['avg_score']}/100",
            "",
        ]
        for result in report["results"]:
            lines.append(self._format_single_report(result, title=f"第 {result['chapter_id']} 章"))
            lines.append("")
        save_markdown(path, "\n".join(lines))

    def _format_single_report(self, result: dict, title: str) -> str:
        status = "通过" if result.get("passed") else "未通过"
        lines = [
            f"## {title}",
            "",
            f"- 结果: {status}",
            f"- 分数: {result.get('score', 0)}/100",
        ]
        if result.get("warnings"):
            lines.append("")
            lines.append("### 问题")
            for item in result["warnings"]:
                lines.append(f"- {item}")
        if result.get("suggestions"):
            lines.append("")
            lines.append("### 建议")
            for item in result["suggestions"]:
                lines.append(f"- {item}")
        return "\n".join(lines)

    def _is_empty(self, value) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, dict)):
            return len(value) == 0
        return False

    def _looks_weak_conflict(self, text: str) -> bool:
        if not text:
            return True
        return (any(w in text for w in self.WEAK_CONFLICT_WEAK)
                and not any(w in text for w in self.WEAK_CONFLICT_STRONG))

    def _looks_like_mood_only_hook(self, text: str) -> bool:
        if not text:
            return True
        if any(marker in text for marker in self.MOOD_HOOK_STRONG):
            return False
        return any(pattern in text for pattern in self.WEAK_ENDING_PATTERNS)

    def _looks_weak_change(self, text: str) -> bool:
        if not text:
            return True
        return (any(w in text for w in self.WEAK_CHANGE_WEAK)
                and not any(w in text for w in self.WEAK_CHANGE_CONCRETE))

    def _has_missing_core(self, warnings: list) -> bool:
        core = ["reader_hook", "character_desire", "obstacle", "action", "cost", "ending_hook"]
        return any(any(field in warning for field in core) for warning in warnings)

    def _contains_repetitive_silence(self, text: str) -> bool:
        hits = sum(text.count(pattern) for pattern in self.REPETITIVE_SILENCE)
        return hits >= 12

    def _has_mood_only_ending(self, text: str) -> bool:
        cleaned = text.strip()
        if not cleaned:
            return True
        tail = cleaned[-500:]
        if any(marker in tail for marker in self.MOOD_TAIL_STRONG):
            return False
        return any(marker in tail for marker in self.MOOD_TAIL_MOOD)

    def _contains_any_keyword(self, text: str, expected: str) -> bool:
        keywords = [
            token for token in re.split(r"[，。；、\s：:（）()《》\"“”]+", expected)
            if len(token) >= 2
        ]
        if not keywords:
            return True
        return any(token in text for token in keywords[:8])

