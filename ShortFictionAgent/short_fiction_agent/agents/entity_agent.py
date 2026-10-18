"""EntityConsistencyAgent — 通用实体一致性检查（批量 LLM 版）。

一次 LLM 调用处理 10 章，提取人物/设定/物件等实体，
然后确定性比对跨章矛盾。不绑定任何具体小说。
"""

from __future__ import annotations

import json, re, hashlib
from pathlib import Path

from ..config import save_markdown, load_markdown
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class EntityConsistencyAgent:
    """通用实体一致性检查器 — 批量 LLM 提取 + 确定性比对。"""

    BATCH_SIZE = 10  # 每次 LLM 调用处理的章数

    def __init__(self, paths: ProjectPaths, llm: LLMClient, memory: FileMemoryStore,
                 pack=None, fallback_llm=None):
        from ..genres import resolve_project_pack
        self.paths = paths
        self.llm = llm
        self.memory = memory
        self.pack = pack or resolve_project_pack(paths)
        self.runtime = AgentRuntime(paths, llm, fallback_llm=fallback_llm)

    # ── public API ──────────────────────────────────────────────

    def check_all(self, from_chapter: int = 1, to_chapter: int | None = None) -> dict:
        """运行全部实体一致性检查。"""
        chapters = sorted(self.paths.chapters_dir.glob("chapter_*.md"))
        if not chapters:
            return {"error": "没有找到任何章节文件", "issues": [], "score": 100}

        max_ch = len(chapters)
        if to_chapter is None:
            to_chapter = max_ch
        to_chapter = min(to_chapter, max_ch)

        # 构建章节批处理
        all_entities = {}
        for batch_start in range(from_chapter, to_chapter + 1, self.BATCH_SIZE):
            batch_end = min(batch_start + self.BATCH_SIZE - 1, to_chapter)
            batch_chapters = {}
            for ch_id in range(batch_start, batch_end + 1):
                ch_path = self.paths.chapter_file(ch_id)
                if ch_path.exists():
                    content = load_markdown(ch_path)
                    batch_chapters[ch_id] = content[:400]  # 每章取前 400 字足够
            if batch_chapters:
                print(f"  正在提取第 {batch_start}-{batch_end} 章实体...", flush=True)
                extracted = self._extract_batch(batch_chapters)
                all_entities.update(extracted)

        print(f"  提取完成: {len(all_entities)} 章有效实体", flush=True)
        print(f"  开始比对...", flush=True)

        # 确定性比对
        issues = self._compare_entities(all_entities)

        # 评分
        severity_deduction = {"critical": 15, "high": 7, "medium": 3, "low": 1}
        total_deduction = sum(severity_deduction.get(i.get("severity", "low"), 1) for i in issues)
        score = max(0, 100 - total_deduction)

        # 保存报告
        self._save_report(issues, score, from_chapter, to_chapter)

        return {
            "issues": issues,
            "score": score,
            "total_issues": len(issues),
            "checked_range": [from_chapter, to_chapter],
        }

    # ── 批量 LLM 提取 ──────────────────────────────────────────

    def _extract_batch(self, chapters: dict[int, str]) -> dict[int, dict]:
        """一次 LLM 调用提取一批章节的实体。"""
        # 构建章节摘要
        chapter_texts = []
        for ch_id in sorted(chapters):
            text = chapters[ch_id]
            chapter_texts.append(f"### 第{ch_id}章\n{text}\n")

        chapters_block = "\n".join(chapter_texts)

        prompt = f"""你是一位小说编辑。请从以下章节的开头部分，提取关键实体信息。

对每章，请识别：
- 班主任：姓名、性别、所教学科
- 年级：高一/高二/高三
- 班级名称（如 文科三班/理科四班）
- 主角住校/走读（池念、云辞）
- 各科老师的姓名
- 关键物件的归属（画本、笔记本、围巾等）

请严格按以下 JSON 格式输出，不要输出任何 JSON 之外的内容：
```json
{{
  "chapters": {{
    "1": {{
      "homeroom_teacher": {{"name": "周", "gender": "男", "subject": "物理"}},
      "grade": "高一",
      "class_name": "一班",
      "living": {{"池念": "走读", "云辞": "未提及"}},
      "teachers": {{"物理": "周", "英语": null}},
      "key_objects": [{{"name": "画本", "owner": "池念"}}]
    }},
    "2": {{...}}
  }}
}}
```

如果某项在本章没出现，写 null。如果角色有多章，每章都要单独标注。

## 章节内容

{chapters_block}"""

        try:
            policy = AgentCallPolicy(
                agent_name="EntityAgent",
                task_type="entity_extract",
                target_prompt_tokens=25000,
                min_response_chars=300,
                quality_tier="routine",
                allow_fallback=True,
            )
            response = self.runtime.call(prompt, policy)
            return self._parse_batch_response(response)
        except Exception as e:
            print(f"    LLM 提取失败: {e}", flush=True)
            return {}

    def _parse_batch_response(self, response: str) -> dict[int, dict]:
        """解析批量 LLM 返回的 JSON。"""
        try:
            start = response.find("{")
            end = response.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(response[start:end + 1])
                chapters = data.get("chapters", {})
                # 转换字符串 key 为 int
                return {int(k): v for k, v in chapters.items() if v is not None}
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            print(f"    JSON 解析失败: {e}", flush=True)
        return {}

    # ── 确定性比对 ──────────────────────────────────────────────

    def _compare_entities(self, all_entities: dict) -> list[dict]:
        """跨章节比对实体，返回矛盾列表。"""
        issues = []
        issues.extend(self._check_homeroom(all_entities))
        issues.extend(self._check_grade(all_entities))
        issues.extend(self._check_living(all_entities))
        issues.extend(self._check_teachers(all_entities))
        issues.extend(self._check_objects(all_entities))
        return issues

    def _check_homeroom(self, entities: dict) -> list[dict]:
        """检查班主任姓名/性别是否跨章一致。"""
        issues = []
        hr_data = {}  # ch_id -> {name, gender, subject}
        for ch_id, ch_data in sorted(entities.items()):
            hr = ch_data.get("homeroom_teacher")
            if hr and isinstance(hr, dict) and hr.get("name"):
                hr_data[ch_id] = hr

        names = {}
        genders = {}
        for ch_id, hr in hr_data.items():
            n = hr.get("name", "")
            g = hr.get("gender", "")
            if n:
                names.setdefault(n, []).append(ch_id)
            if g and g != "未知":
                genders.setdefault(g, []).append(ch_id)

        if len(names) > 1:
            name_list = ", ".join(f"'{n}'(第{chs[0]}-{chs[-1]}章)" if len(chs) > 1 else f"'{n}'(第{chs[0]}章)" for n, chs in names.items())
            issues.append({
                "issue_id": "entity_hr_name",
                "issue_type": "班主任姓名不一致",
                "severity": "critical",
                "description": f"班主任姓名跨章不一致: {name_list}",
                "chapter_id_a": min(min(chs) for chs in names.values()),
                "chapter_id_b": max(max(chs) for chs in names.values()),
                "evidence_a": f"姓名: {list(names.keys())}",
                "evidence_b": f"章节: { {n: chs for n, chs in names.items()} }",
                "suggestion": "统一班主任姓名。如果是不同班级的班主任，确认是否叙述清楚。",
            })

        if len(genders) > 1:
            issues.append({
                "issue_id": "entity_hr_gender",
                "issue_type": "班主任性别不一致",
                "severity": "critical",
                "description": f"班主任性别跨章不一致: {dict(genders)}",
                "chapter_id_a": min(min(chs) for chs in genders.values()),
                "chapter_id_b": max(max(chs) for chs in genders.values()),
                "evidence_a": f"性别A: {list(genders.keys())[0]}",
                "evidence_b": f"性别B: {list(genders.keys())[1]}",
                "suggestion": "统一班主任性别设定。",
            })

        return issues

    def _check_grade(self, entities: dict) -> list[dict]:
        """检查年级是否单调递增。"""
        issues = []
        grades = {}
        for ch_id, ch_data in sorted(entities.items()):
            g = ch_data.get("grade")
            if g and g in ("高一", "高二", "高三"):
                grades[ch_id] = g

        order = {"高一": 1, "高二": 2, "高三": 3}
        sorted_items = sorted(grades.items())
        for i in range(1, len(sorted_items)):
            prev_ch, prev_g = sorted_items[i - 1]
            curr_ch, curr_g = sorted_items[i]
            if order.get(curr_g, 0) < order.get(prev_g, 0):
                issues.append({
                    "issue_id": f"entity_grade_{curr_ch}",
                    "issue_type": "年级倒退",
                    "severity": "high",
                    "description": f"年级倒退: 第{prev_ch}章为'{prev_g}'，第{curr_ch}章为'{curr_g}'",
                    "chapter_id_a": prev_ch,
                    "chapter_id_b": curr_ch,
                    "evidence_a": f"第{prev_ch}章: {prev_g}",
                    "evidence_b": f"第{curr_ch}章: {curr_g}",
                    "suggestion": "检查是否倒叙/回忆段落，或年级设置有误。",
                })

        return issues

    def _check_living(self, entities: dict) -> list[dict]:
        """检查主角住校/走读是否跨章一致。"""
        issues = []
        for char in ("池念", "云辞"):
            situations = {}
            for ch_id, ch_data in sorted(entities.items()):
                living = ch_data.get("living", {})
                s = living.get(char) if isinstance(living, dict) else None
                if s and s != "未提及":
                    situations.setdefault(s, []).append(ch_id)

            if len(situations) > 1:
                sit_list = ", ".join(
                    f"'{s}'(第{chs[0]}-{chs[-1]}章)" if len(chs) > 1 else f"'{s}'(第{chs[0]}章)"
                    for s, chs in situations.items()
                )
                issues.append({
                    "issue_id": f"entity_living_{char}",
                    "issue_type": "住校/走读不一致",
                    "severity": "medium",
                    "description": f"{char}住校/走读状态跨章不一致: {sit_list}",
                    "chapter_id_a": min(min(chs) for chs in situations.values()),
                    "chapter_id_b": max(max(chs) for chs in situations.values()),
                    "evidence_a": f"状态A: {list(situations.keys())[0]}",
                    "evidence_b": f"状态B: {list(situations.keys())[1]}",
                    "suggestion": "确认是否有搬家/换宿舍等剧情交代。如无，统一设定。",
                })

        return issues

    def _check_teachers(self, entities: dict) -> list[dict]:
        """检查科任老师姓名是否跨章一致。"""
        issues = []
        all_teachers = {}  # subject -> {name -> [ch_ids]}
        for ch_id, ch_data in sorted(entities.items()):
            teachers = ch_data.get("teachers", {})
            if isinstance(teachers, dict):
                for subject, name in teachers.items():
                    if name and name != "null":
                        all_teachers.setdefault(subject, {}).setdefault(name, []).append(ch_id)

        for subject, names in all_teachers.items():
            if len(names) > 1:
                name_list = ", ".join(
                    f"'{n}'(第{chs[0]}-{chs[-1]}章)" for n, chs in names.items()
                )
                issues.append({
                    "issue_id": f"entity_teacher_{subject}",
                    "issue_type": "科任老师不一致",
                    "severity": "medium",
                    "description": f"{subject}老师姓名跨章不一致: {name_list}",
                    "chapter_id_a": min(min(chs) for chs in names.values()),
                    "chapter_id_b": max(max(chs) for chs in names.values()),
                    "evidence_a": f"老师A: {list(names.keys())[0]}",
                    "evidence_b": f"老师B: {list(names.keys())[1]}",
                    "suggestion": f"确认{subject}老师是否换过人。如果是不同班级的老师，确认叙述清晰。",
                })

        return issues

    def _check_objects(self, entities: dict) -> list[dict]:
        """检查关键物件归属变化。"""
        issues = []
        obj_owners = {}  # obj_name -> {owner -> [ch_ids]}
        for ch_id, ch_data in sorted(entities.items()):
            objs = ch_data.get("key_objects", [])
            if isinstance(objs, list):
                for obj in objs:
                    if isinstance(obj, dict):
                        name = obj.get("name", "")
                        owner = obj.get("owner", "")
                        if name and owner:
                            obj_owners.setdefault(name, {}).setdefault(owner, []).append(ch_id)

        for obj_name, owners in obj_owners.items():
            if len(owners) > 1:
                owner_list = ", ".join(
                    f"'{o}'(第{chs[0]}-{chs[-1]}章)" for o, chs in owners.items()
                )
                issues.append({
                    "issue_id": f"entity_obj_{obj_name}",
                    "issue_type": "物件归属变化",
                    "severity": "low",
                    "description": f"'{obj_name}'归属跨章变化: {owner_list}",
                    "chapter_id_a": min(min(chs) for chs in owners.values()),
                    "chapter_id_b": max(max(chs) for chs in owners.values()),
                    "evidence_a": f"归属A: {list(owners.keys())[0]}",
                    "evidence_b": f"归属B: {list(owners.keys())[1]}",
                    "suggestion": f"确认'{obj_name}'的归属变化是否有剧情交代（赠送/归还/丢失）。",
                })

        return issues

    # ── 报告 ────────────────────────────────────────────────────

    def _save_report(self, issues: list[dict], score: int, from_ch: int, to_ch: int) -> None:
        report_path = self.paths.reviews_dir / "entity_consistency_report.md"
        icons = {"critical": "⛔", "high": "🔴", "medium": "🟠", "low": "🟡"}

        lines = [
            "# 实体一致性检查报告",
            "",
            f"**总分**: {score}/100",
            f"**检查范围**: 第 {from_ch} - {to_ch} 章",
            f"**问题总数**: {len(issues)}",
            "",
            "---",
            "",
        ]

        if not issues:
            lines.append("✅ 未发现实体一致性问题。")
        else:
            for i, issue in enumerate(issues, 1):
                icon = icons.get(issue.get("severity", "low"), "⚪")
                lines.append(f"## {i}. {icon} {issue.get('description', '')}")
                lines.append(f"- **类型**: {issue.get('issue_type', '')}")
                lines.append(f"- **严重程度**: {issue.get('severity', '')}")
                lines.append(f"- **涉及章节**: 第{issue.get('chapter_id_a')}章 vs 第{issue.get('chapter_id_b')}章")
                lines.append(f"- **证据 A**: {issue.get('evidence_a', '')}")
                lines.append(f"- **证据 B**: {issue.get('evidence_b', '')}")
                lines.append(f"- **建议**: {issue.get('suggestion', '')}")
                lines.append("")

        save_markdown(report_path, "\n".join(lines))

