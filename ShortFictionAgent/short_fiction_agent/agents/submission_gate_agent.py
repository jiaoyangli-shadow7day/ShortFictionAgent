"""Submission-grade stage gates for long-form novel generation.

This agent is a lightweight harness layer: it does not replace the writer,
reviewer, or commercial editors. It decides when a run should pause before
more chapters are generated, so structural debt is fixed while the affected
window is still small.
"""

from __future__ import annotations

from datetime import datetime

from pathlib import Path

from ..config import load_json, load_markdown, save_markdown
from ..memory import FileMemoryStore
from ..paths import ProjectPaths
from .character_canon_sync_agent import CharacterCanonSyncAgent
from .commercial_editor_agent import CommercialQualitySuite
from .consistency_agent import ConsistencyAgent


class SubmissionGateAgent:
    """阶段门禁：面向可投稿终稿的结构阻断层。

    设计原则：
    - 尽量复用已有本地报告和规则，避免新增大 prompt。
    - 门禁只给出阻断和定向返修范围，不直接重写正文。
    - 保持通用，不绑定任何具体小说、角色或书名。
    """

    OPENING_WINDOW = 10
    ROLLING_WINDOW = 5
    ARC_WINDOW = 30
    HARD_GATE_THRESHOLD = 72
    SOFT_GATE_THRESHOLD = 68

    def __init__(self, paths: ProjectPaths, memory: FileMemoryStore | None = None, pack=None):
        from ..genres import get_genre_pack
        self.paths = paths
        self.memory = memory or FileMemoryStore(paths)
        self.pack = pack or get_genre_pack()
        self.commercial_suite = CommercialQualitySuite(paths, self.pack)
        self.canon_sync = CharacterCanonSyncAgent(paths)

    # ------------------------------------------------------------------
    # Public gates
    # ------------------------------------------------------------------

    def run_opening10_gate(self, up_to_chapter: int | None = None) -> dict:
        """前十章硬门禁。

        关注首屏钩子、前十章不可逆变化、男女主独立欲望线、正文事件硬度。
        """
        up_to_chapter = min(up_to_chapter or self.OPENING_WINDOW, self.OPENING_WINDOW)
        chapters = self._existing_chapter_ids(1, up_to_chapter)
        modules = []
        warnings: list[str] = []
        suggestions: list[str] = []

        project_commercial = self.commercial_suite.audit_project()
        modules.extend(project_commercial.get("modules", []))

        text_modules = []
        for ch_id in chapters:
            text_result = self.commercial_suite.audit_chapter_text(ch_id)
            text_modules.append(text_result)
            modules.extend(text_result.get("modules", []))

        opening_stats = self._opening_text_stats(chapters)
        modules.append(opening_stats)

        if len(chapters) < up_to_chapter:
            warnings.append(f"前十章门禁只找到 {len(chapters)}/{up_to_chapter} 章正文。")
            suggestions.append("请先写完门禁窗口内章节，再判断能否继续扩大生产。")

        warnings.extend(project_commercial.get("warnings", []))
        suggestions.extend(project_commercial.get("suggestions", []))
        for result in text_modules:
            warnings.extend(result.get("warnings", []))
            suggestions.extend(result.get("suggestions", []))
        warnings.extend(opening_stats.get("warnings", []))
        suggestions.extend(opening_stats.get("suggestions", []))

        score = self._weighted_average([
            (project_commercial.get("score", 0), 0.55),
            (self._average_score(text_modules), 0.25),
            (opening_stats.get("score", 0), 0.20),
        ])
        result = self._gate_result(
            gate="Opening10SubmissionGate",
            score=score,
            threshold=self.HARD_GATE_THRESHOLD,
            severity="hard",
            modules=modules,
            warnings=warnings,
            suggestions=suggestions,
            revision_scope={"from": 1, "to": up_to_chapter, "reason": "开篇十章结构返修"},
        )
        result["report_path"] = str(self.save_gate_report(result, "opening10_submission_gate.md"))
        return result

    def run_rolling_gate(self, current_chapter: int, window: int | None = None) -> dict:
        """每 5 章滚动连续性门禁。"""
        window = window or self.ROLLING_WINDOW
        start = max(1, current_chapter - window + 1)
        local_consistency = self._local_consistency(start, current_chapter)
        relationship = self._relationship_progress_stats(start, current_chapter)
        foreshadowing = self._foreshadowing_stats(current_chapter)
        # 跨章风格累积去重: 单章门禁只看本章, 这里以近 N 章窗口兜住
        # "窗/光/草稿纸"等意象在多章里慢慢堆积的问题, 返修范围正好是本窗口。
        rolling_style = self.commercial_suite.audit_rolling_style(current_chapter, recent_n=window)

        modules = [local_consistency, relationship, foreshadowing, rolling_style]
        warnings = []
        suggestions = []
        for module in modules:
            warnings.extend(module.get("warnings", []))
            suggestions.extend(module.get("suggestions", []))

        score = self._average_score(modules)
        result = self._gate_result(
            gate="RollingContinuityGate",
            score=score,
            threshold=self.SOFT_GATE_THRESHOLD,
            severity="soft",
            modules=modules,
            warnings=warnings,
            suggestions=suggestions,
            revision_scope={"from": start, "to": current_chapter, "reason": "近五章断联/伏笔/关系节奏返修"},
        )
        result["report_path"] = str(self.save_gate_report(result, f"rolling_continuity_{start:03d}_{current_chapter:03d}.md"))
        return result

    def run_payoff_ledger_gate(self, current_chapter: int) -> dict:
        """情绪债/伏笔兑现门禁。"""
        payoff = self.commercial_suite.payoff_ledger.audit_cadence()
        foreshadowing = self._foreshadowing_stats(current_chapter)
        modules = [payoff, foreshadowing]
        warnings = payoff.get("warnings", []) + foreshadowing.get("warnings", [])
        suggestions = payoff.get("suggestions", []) + foreshadowing.get("suggestions", [])

        score = self._average_score(modules)
        result = self._gate_result(
            gate="PayoffLedgerGate",
            score=score,
            threshold=self.SOFT_GATE_THRESHOLD,
            severity="soft",
            modules=modules,
            warnings=warnings,
            suggestions=suggestions,
            revision_scope={"from": max(1, current_chapter - self.ROLLING_WINDOW + 1), "to": current_chapter, "reason": "情绪债与伏笔兑现返修"},
        )
        result["report_path"] = str(self.save_gate_report(result, f"payoff_ledger_gate_{current_chapter:03d}.md"))
        return result

    def run_arc_checkpoint_gate(self, current_chapter: int, window: int | None = None) -> dict:
        """每 30 章左右的卷/大段落门禁。"""
        window = window or self.ARC_WINDOW
        start = max(1, current_chapter - window + 1)
        project_commercial = self.commercial_suite.audit_project()
        relationship = self._relationship_progress_stats(start, current_chapter)
        local_consistency = self._local_consistency(start, current_chapter)
        modules = [project_commercial, relationship, local_consistency]

        warnings = []
        suggestions = []
        for module in modules:
            warnings.extend(module.get("warnings", []))
            suggestions.extend(module.get("suggestions", []))

        score = self._average_score(modules)
        result = self._gate_result(
            gate="ArcCheckpointGate",
            score=score,
            threshold=self.HARD_GATE_THRESHOLD,
            severity="hard",
            modules=modules,
            warnings=warnings,
            suggestions=suggestions,
            revision_scope={"from": start, "to": current_chapter, "reason": "卷级结构返修"},
        )
        result["report_path"] = str(self.save_gate_report(result, f"arc_checkpoint_{start:03d}_{current_chapter:03d}.md"))
        return result

    def run_final_submission_gate(self, from_chapter: int, to_chapter: int) -> dict:
        """终稿投稿前总门禁。"""
        project_commercial = self.commercial_suite.audit_project()
        local_consistency = self._local_consistency(from_chapter, to_chapter)
        relationship = self._relationship_progress_stats(from_chapter, to_chapter)
        foreshadowing = self._foreshadowing_stats(to_chapter)
        canon = self._canon_module()
        manuscript = self._manuscript_stats(from_chapter, to_chapter)
        modules = [project_commercial, local_consistency, relationship, foreshadowing, canon, manuscript]

        warnings = []
        suggestions = []
        for module in modules:
            warnings.extend(module.get("warnings", []))
            suggestions.extend(module.get("suggestions", []))

        score = self._weighted_average([
            (project_commercial.get("score", 0), 0.30),
            (local_consistency.get("score", 0), 0.20),
            (relationship.get("score", 0), 0.15),
            (foreshadowing.get("score", 0), 0.15),
            (canon.get("score", 0), 0.10),
            (manuscript.get("score", 0), 0.10),
        ])
        result = self._gate_result(
            gate="FinalSubmissionGate",
            score=score,
            threshold=self.HARD_GATE_THRESHOLD,
            severity="hard",
            modules=modules,
            warnings=warnings,
            suggestions=suggestions,
            revision_scope={"from": from_chapter, "to": to_chapter, "reason": "投稿前终稿返修"},
        )
        result["report_path"] = str(self.save_gate_report(result, "final_submission_gate.md"))
        return result

    # ------------------------------------------------------------------
    # Report helpers
    # ------------------------------------------------------------------

    def save_gate_report(self, result: dict, filename: str) -> Path:
        path = self.paths.reviews_dir / filename
        save_markdown(path, self.format_gate_report(result))
        return path

    def format_gate_report(self, result: dict) -> str:
        lines = [
            f"# {result.get('gate', 'SubmissionGate')} 报告",
            "",
            f"- 时间: {result.get('created_at', '')}",
            f"- 结果: {'通过' if result.get('passed') else '未通过'}",
            f"- 分数: {result.get('score', 0)}/100",
            f"- 阈值: {result.get('threshold', 0)}/100",
            f"- 严重性: {result.get('severity', '')}",
            "",
        ]
        scope = result.get("revision_scope") or {}
        if scope:
            lines.extend([
                "## 返修范围",
                "",
                f"- 章节: 第 {scope.get('from')} 章 至 第 {scope.get('to')} 章",
                f"- 原因: {scope.get('reason', '')}",
                "",
            ])
        if result.get("warnings"):
            lines.extend(["## 阻断问题", ""])
            for item in result["warnings"][:50]:
                lines.append(f"- {item}")
            lines.append("")
        if result.get("suggestions"):
            lines.extend(["## 定向修改建议", ""])
            for item in result["suggestions"][:50]:
                lines.append(f"- {item}")
            lines.append("")
        if result.get("modules"):
            lines.extend(["## 模块明细", ""])
            for module in result["modules"]:
                lines.append(f"### {module.get('module', module.get('title', '模块'))} - {module.get('score', 0)}/100")
                for warning in module.get("warnings", [])[:20]:
                    lines.append(f"- 问题: {warning}")
                for suggestion in module.get("suggestions", [])[:20]:
                    lines.append(f"- 建议: {suggestion}")
                if not module.get("warnings") and not module.get("suggestions"):
                    lines.append("- 通过")
                lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Local analysis modules
    # ------------------------------------------------------------------

    def _opening_text_stats(self, chapter_ids: list[int]) -> dict:
        warnings, suggestions = [], []
        score = 100
        if not chapter_ids:
            return self._module("OpeningTextStats", 0, ["缺少开篇正文。"], ["先生成开篇章节正文。"])

        first_text = load_markdown(self.paths.chapter_file(chapter_ids[0]))
        if self._count_zh(first_text[:1600]) < 900:
            warnings.append("首章前 1600 字有效正文偏少或内容异常。")
            suggestions.append("首章必须尽早给出主角、场景、核心问题和可追问钩子。")
            score -= 20

        strong_endings = 0
        visible_consequences = 0
        strong_end_markers = self.pack.lex("opening_strong_endings")
        visible_markers = self.pack.lex("opening_visible_consequences")
        for ch_id in chapter_ids:
            text = load_markdown(self.paths.chapter_file(ch_id))
            tail = text[-500:]
            if any(marker in tail for marker in strong_end_markers):
                strong_endings += 1
            if any(marker in text for marker in visible_markers):
                visible_consequences += 1

        if strong_endings < max(2, len(chapter_ids) // 3):
            warnings.append("开篇窗口章节尾钩偏少，追下一章动力不足。")
            suggestions.append("每 2-3 章至少用一次明确问题、信息差或可见后果收束。")
            score -= 20
        if visible_consequences < 2 and len(chapter_ids) >= 5:
            warnings.append("前期正文外部后果不足，可能只有情绪氛围。")
            suggestions.append("补充成绩、同学误读、老师谈话、家庭压力或任务失败等可见后果。")
            score -= 25

        return self._module("OpeningTextStats", score, warnings, suggestions)

    def _local_consistency(self, from_chapter: int, to_chapter: int) -> dict:
        try:
            agent = ConsistencyAgent(self.paths, None, self.memory)
            issues = agent._local_checks(from_chapter, to_chapter)
        except Exception as exc:
            return self._module("LocalContinuityStats", 60, [f"本地一致性检查失败: {exc}"], ["检查 memory 文件是否完整。"])

        warnings = []
        suggestions = []
        severity_cost = {"low": 2, "medium": 7, "high": 14, "critical": 25}
        score = 100
        for issue in issues:
            severity = issue.get("severity", "low")
            score -= severity_cost.get(severity, 2)
            warnings.append(issue.get("description", str(issue)))
            if issue.get("suggestion"):
                suggestions.append(issue["suggestion"])
        return self._module("LocalContinuityStats", score, warnings, suggestions)

    def _relationship_progress_stats(self, from_chapter: int, to_chapter: int) -> dict:
        summaries = self._chapter_summaries()
        selected = [
            item for item in summaries
            if isinstance(item, dict) and from_chapter <= int(item.get("chapter_id", 0)) <= to_chapter
        ]
        warnings, suggestions = [], []
        score = 100
        if not selected:
            return self._module("RelationshipProgressStats", 80, [], [])

        change_fields = []
        for item in selected:
            change = str(item.get("relationship_change", "") or item.get("relationship_development", "") or "")
            if change.strip():
                change_fields.append(change.strip())

        if len(change_fields) < max(1, len(selected) // 3):
            warnings.append(f"第 {from_chapter}-{to_chapter} 章关系变化记录偏少。")
            suggestions.append("补充至少一次可见关系转向：主动靠近、误读加深、公开维护、拒绝或旧物回收。")
            score -= 25
        if len(set(change_fields)) == 1 and len(change_fields) >= 3:
            warnings.append("连续章节关系变化描述高度重复，可能出现情感原地踏步。")
            suggestions.append("把关系推进拆成不同阶段：信息差、行动、代价、兑现，而不是重复暧昧观察。")
            score -= 25

        return self._module("RelationshipProgressStats", score, warnings, suggestions)

    def _foreshadowing_stats(self, current_chapter: int) -> dict:
        warnings, suggestions = [], []
        score = 100
        try:
            active = self.memory.get_active_foreshadowing()
        except Exception:
            active = []

        overdue = []
        for item in active:
            if not isinstance(item, dict):
                continue
            intended = item.get("intended_payoff_chapter")
            if isinstance(intended, int) and current_chapter > intended + 3:
                overdue.append(item)

        high_overdue = [item for item in overdue if item.get("importance") == "high"]
        if high_overdue:
            warnings.append(f"高重要性伏笔超期未回收: {len(high_overdue)} 条。")
            suggestions.append("下一阶段必须优先回收或重新安排高重要性伏笔，避免终稿断联。")
            score -= min(45, len(high_overdue) * 15)
        if len(overdue) >= 3:
            warnings.append(f"伏笔债务累积偏多: {len(overdue)} 条。")
            suggestions.append("建立 3-5 章内的回收清单，写作任务书必须显式引用。")
            score -= min(30, len(overdue) * 5)
        return self._module("ForeshadowingPayoffStats", score, warnings, suggestions)

    def _canon_module(self) -> dict:
        try:
            result = self.canon_sync.audit()
        except Exception as exc:
            return self._module("CharacterCanonSync", 60, [f"角色设定同步检查失败: {exc}"], ["检查 characters/*.yaml 是否存在且格式正确。"])

        score = 100 if result.get("passed") else 55
        warnings = result.get("issues", [])
        suggestions = result.get("suggestions", [])
        return self._module("CharacterCanonSync", score, warnings, suggestions)

    def _manuscript_stats(self, from_chapter: int, to_chapter: int) -> dict:
        warnings, suggestions = [], []
        score = 100
        existing = self._existing_chapter_ids(from_chapter, to_chapter)
        missing = [ch for ch in range(from_chapter, to_chapter + 1) if ch not in existing]
        if missing:
            warnings.append(f"缺少章节正文: {missing[:20]}")
            suggestions.append("投稿前必须补齐所有章节，且通过章节完整性校验。")
            score -= min(50, len(missing) * 5)

        too_short = []
        for ch_id in existing:
            text = load_markdown(self.paths.chapter_file(ch_id))
            if self._count_zh(text) < 1500:
                too_short.append(ch_id)
        if too_short:
            warnings.append(f"章节字数偏短或疑似异常: {too_short[:20]}")
            suggestions.append("检查这些章节是否被 API 截断，或是否需要补足场景与事件链。")
            score -= min(40, len(too_short) * 4)
        return self._module("ManuscriptCompletenessStats", score, warnings, suggestions)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def _gate_result(
        self,
        gate: str,
        score: float,
        threshold: int,
        severity: str,
        modules: list[dict],
        warnings: list[str],
        suggestions: list[str],
        revision_scope: dict,
    ) -> dict:
        score = round(float(score), 1)
        blockers = self._blocking_modules(modules)
        return {
            "gate": gate,
            "passed": score >= threshold and not blockers,
            "score": score,
            "threshold": threshold,
            "severity": severity,
            "modules": modules,
            "warnings": self._dedupe(warnings),
            "suggestions": self._dedupe(suggestions),
            "blockers": blockers,
            "revision_scope": revision_scope,
            "created_at": datetime.now().isoformat(),
        }

    def _module(self, name: str, score: float, warnings: list[str], suggestions: list[str]) -> dict:
        score = max(0, min(100, round(float(score), 1)))
        return {
            "module": name,
            "score": score,
            "passed": score >= 70 and not warnings,
            "required": score < 70,
            "warnings": self._dedupe(warnings),
            "suggestions": self._dedupe(suggestions),
        }

    def _has_critical_module(self, modules: list[dict]) -> bool:
        return any(float(module.get("score", 100)) < 45 for module in modules)

    def _blocking_modules(self, modules: list[dict], prefix: str = "") -> list[str]:
        """Propagate failed required checks through nested aggregate reports."""
        blockers = []
        for module in modules:
            if not isinstance(module, dict):
                continue
            name = str(module.get("module") or module.get("title") or "unnamed")
            label = f"{prefix}/{name}" if prefix else name
            if module.get("required", True) and module.get("passed") is False:
                blockers.append(label)
            if float(module.get("score", 100)) < 45:
                blockers.append(label)
            blockers.extend(self._blocking_modules(module.get("modules") or [], label))
        return self._dedupe(blockers)

    def _existing_chapter_ids(self, from_chapter: int, to_chapter: int) -> list[int]:
        ids = []
        for ch_id in range(from_chapter, to_chapter + 1):
            path = self.paths.chapter_file(ch_id)
            if path.exists() and load_markdown(path).strip():
                ids.append(ch_id)
        return ids

    def _chapter_summaries(self) -> list[dict]:
        data = load_json(self.paths.chapter_summaries)
        return data if isinstance(data, list) else []

    def _count_zh(self, text: str) -> int:
        return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")

    def _average_score(self, modules: list[dict]) -> float:
        scores = [float(item.get("score", 0)) for item in modules if isinstance(item, dict)]
        return round(sum(scores) / len(scores), 1) if scores else 100.0

    def _weighted_average(self, weighted_scores: list[tuple[float, float]]) -> float:
        total_weight = sum(weight for _, weight in weighted_scores)
        if total_weight <= 0:
            return 100.0
        return round(sum(float(score) * weight for score, weight in weighted_scores) / total_weight, 1)

    def _dedupe(self, items: list[str]) -> list[str]:
        seen = set()
        result = []
        for item in items:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

