"""CharacterCanonSyncAgent - 角色设定同步检查。

当正文已经把人物写出了新的长期欲望、短板和家庭压力，而
characters/*.yaml 仍停留在早期模板时，后续 agent 会继续按旧人设判断。
这个 Agent 负责发现这种“角色 canon 漂移”。
"""

from pathlib import Path

from ..config import load_yaml, save_yaml, load_markdown, save_markdown
from ..paths import ProjectPaths


class CharacterCanonSyncAgent:
    """检查角色设定是否跟随正文/大纲进化。"""

    FEMALE_CANON_MARKERS = {
        "艺术/创作线": ["画画", "画本", "速写", "美术", "美院", "艺术"],
        "偏科/成绩压力": ["数学", "物理", "偏科", "成绩", "排名", "补课"],
        "家庭压力": ["小店", "店铺", "清仓", "父母", "家庭", "家庭生意"],
        "未来选择": ["大学", "美院", "未来", "志愿", "选择", "专业"],
        "自尊": ["自尊", "不想被看轻", "不想输", "拒绝帮助", "要强"],
    }

    MALE_CANON_MARKERS = {
        "学业/未来": ["远方", "大城市", "大学", "专业", "成绩", "竞赛"],
        "克制/表达": ["克制", "不善表达", "忍", "高考后"],
        "家庭/压力": ["父亲", "家庭", "期待", "失控"],
    }

    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    def audit(self) -> dict:
        female = load_yaml(self.paths.protagonist_female)
        male = load_yaml(self.paths.protagonist_male)
        corpus = self._corpus()

        issues = []
        suggestions = []
        self._audit_profile(
            role="女主",
            profile=female,
            markers=self.FEMALE_CANON_MARKERS,
            corpus=corpus,
            issues=issues,
            suggestions=suggestions,
        )
        self._audit_profile(
            role="男主",
            profile=male,
            markers=self.MALE_CANON_MARKERS,
            corpus=corpus,
            issues=issues,
            suggestions=suggestions,
        )

        score = max(0, 100 - len(issues) * 12)
        return {
            "score": score,
            "passed": score >= 80 and not issues,
            "issues": issues,
            "suggestions": suggestions,
        }

    def save_report(self, result: dict | None = None) -> Path:
        if result is None:
            result = self.audit()
        path = self.paths.consistency_dir / "character_canon_sync_report.md"
        lines = [
            "# 角色设定同步报告",
            "",
            f"- 结果: {'通过' if result.get('passed') else '未通过'}",
            f"- 分数: {result.get('score', 0)}/100",
            "",
        ]
        if result.get("issues"):
            lines.append("## 问题")
            for issue in result["issues"]:
                lines.append(f"- {issue}")
            lines.append("")
        if result.get("suggestions"):
            lines.append("## 建议")
            for suggestion in result["suggestions"]:
                lines.append(f"- {suggestion}")
        save_markdown(path, "\n".join(lines))
        return path

    def sync_profiles(self) -> dict:
        """把同步建议写入角色文件的 canon_sync_notes。

        这里不直接改写 role/core_desire 等正文设定，避免把某个项目的
        具体人物模板写进通用 agent。真正的人设改写仍交给作者确认。
        """
        result = self.audit()
        notes = result.get("suggestions", [])
        updates = []
        for path in [self.paths.protagonist_female, self.paths.protagonist_male]:
            profile = load_yaml(path)
            if not profile:
                continue
            existing = profile.get("canon_sync_notes", [])
            if not isinstance(existing, list):
                existing = [str(existing)]
            merged = self._merge_list(existing, notes)
            if merged != existing:
                profile["canon_sync_notes"] = merged
                save_yaml(path, profile)
                updates.append(str(path))
        return {"updated": bool(updates), "paths": updates, "notes_count": len(notes)}

    def _audit_profile(self, role: str, profile: dict, markers: dict, corpus: str, issues: list, suggestions: list):
        profile_text = self._profile_text(profile)
        for label, words in markers.items():
            in_corpus = any(word in corpus for word in words)
            in_profile = any(word in profile_text for word in words)
            if in_corpus and not in_profile:
                issues.append(f"{role}正文/大纲已有“{label}”，但角色设定未同步。")
                suggestions.append(f"在 {role} 设定中补入“{label}”相关的核心欲望、恐惧、短板或成长弧。")

        if role == "女主" and "各科均衡" in profile_text and any(word in corpus for word in ["数学", "物理", "偏科", "美院"]):
            issues.append("女主设定仍写“各科均衡”，但正文已呈现偏科、专业目标或文化课压力。")
            suggestions.append("把女主学习标签改为语文/美术强、数学物理不稳，避免后续 agent 误判。")
        if role == "女主" and "喜不喜欢" in str(profile.get("core_desire", "")) and any(word in corpus for word in ["画画", "美术", "家庭", "未来"]):
            issues.append("女主 core_desire 仍偏恋爱确认，落后于职业/家庭/未来主线。")
            suggestions.append("将女主 core_desire 改为个人未来、自尊压力和感情确认并行。")

    def _corpus(self) -> str:
        parts = [
            load_markdown(self.paths.master_outline),
            str(load_yaml(self.paths.chapter_outline)),
        ]
        for ch_path in sorted(self.paths.chapters_dir.glob("chapter_*.md")):
            parts.append(load_markdown(ch_path))
        return "\n".join(parts)

    def _profile_text(self, profile: dict) -> str:
        parts = []
        for value in profile.values():
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
            else:
                parts.append(str(value))
        return " ".join(parts)

    def _merge_list(self, original: list, additions: list) -> list:
        result = list(original) if isinstance(original, list) else []
        for item in additions:
            if item not in result:
                result.append(item)
        return result

