"""ConsistencyAgent - 跨章节一致性检查 + 角色对话风格一致性检测。"""

import json
import re
from pathlib import Path
from ..config import save_markdown, load_markdown, load_yaml, load_json
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class ConsistencyAgent:
    """跨章节一致性和对话风格检查。"""

    def __init__(self, paths: ProjectPaths, llm: LLMClient, memory: FileMemoryStore, pack=None):
        from ..genres import get_genre_pack
        self.paths = paths
        self.llm = llm
        self.memory = memory
        self.pack = pack or get_genre_pack()
        self.runtime = AgentRuntime(paths, llm)

    def _get_prompt(self, name: str) -> str:
        return self.pack.read_prompt(name)

    def _get_rules(self) -> str:
        return self.pack.rules_text()

    def check_all(self, from_chapter: int = 1, to_chapter: int = None) -> dict:
        """运行全部一致性检查。"""
        # 确定检查范围
        chapter_files = sorted(self.paths.chapters_dir.glob("chapter_*.md"))
        if not chapter_files:
            return {"error": "没有找到任何章节文件"}

        max_chapter = len(chapter_files)
        if to_chapter is None:
            to_chapter = max_chapter
        to_chapter = min(to_chapter, max_chapter)

        report = {
            "checked_chapters": list(range(from_chapter, to_chapter + 1)),
            "total_issues": 0,
            "issues_by_type": {},
            "issues": [],
            "overall_score": 100,
            "summary": "",
        }

        # 1. 本地规则检查（不需要 LLM）
        local_issues = self._local_checks(from_chapter, to_chapter)
        report["issues"].extend(local_issues)

        # 2. LLM 深度一致性检查（如果章节 >= 2）
        if to_chapter - from_chapter >= 1:
            llm_issues = self._llm_deep_check(from_chapter, to_chapter)
            report["issues"].extend(llm_issues)

        # 3. 对话风格一致性
        dialogue_issues = self._check_dialogue_consistency(from_chapter, to_chapter)
        report["issues"].extend(dialogue_issues)

        # 统计
        report["total_issues"] = len(report["issues"])
        type_counts = {}
        for issue in report["issues"]:
            itype = issue.get("issue_type", "unknown")
            type_counts[itype] = type_counts.get(itype, 0) + 1
        report["issues_by_type"] = type_counts

        # 计算总分（每个 issue 扣分）
        severity_deduction = {"low": 1, "medium": 3, "high": 7, "critical": 15}
        total_deduction = sum(severity_deduction.get(i.get("severity", "low"), 1) for i in report["issues"])
        report["overall_score"] = max(0, 100 - total_deduction)

        # 生成摘要
        report["summary"] = self._generate_summary(report)

        # 保存报告
        self._save_report(report)

        return report

    def _local_checks(self, from_chapter: int, to_chapter: int) -> list:
        """本地规则检查（不调用 LLM）。"""
        issues = []

        # 检查关系状态数值是否出现不合理跳跃
        ri = self.memory.load_relationship_state()
        romance_history = self._get_romance_history()

        for i in range(1, len(romance_history)):
            prev = romance_history[i - 1]
            curr = romance_history[i]
            # 进度轴(关系/力量/复仇线的数值维度)由题材包定义, 跳变阈值同理
            for key in self.pack.progression_axes:
                prev_val = prev.get(key, 0)
                curr_val = curr.get(key, 0)
                jump = abs(curr_val - prev_val)
                if jump > self.pack.relationship_jump_threshold:
                    issues.append({
                        "issue_id": f"local_rel_jump_{prev.get('chapter_id', i)}_{curr.get('chapter_id', i+1)}",
                        "issue_type": "relationship_jump",
                        "severity": "high" if jump > 20 else "medium",
                        "description": f"关系数值 {key} 从第{prev.get('chapter_id', '?')}章的{prev_val}跳到第{curr.get('chapter_id', '?')}章的{curr_val}，变化幅度{jump}分，超出正常范围",
                        "chapter_id_a": prev.get("chapter_id", 0),
                        "chapter_id_b": curr.get("chapter_id", 0),
                        "evidence_a": f"{key}: {prev_val}",
                        "evidence_b": f"{key}: {curr_val}",
                        "suggestion": "关系中大型变化应有明确事件支撑。检查对应章节是否有充分的情感事件推动此变化。如无，建议调整数值或增加铺垫章节。",
                    })

        # 检查伏笔是否超期未回收
        foreshadowing_data = self.memory.get_active_foreshadowing()
        for item in foreshadowing_data:
            if isinstance(item, dict):
                planted = item.get("planted_chapter", 0)
                intended = item.get("intended_payoff_chapter")
                if intended and isinstance(intended, int) and to_chapter > intended + 5:
                    if item.get("importance") == "high":
                        issues.append({
                            "issue_id": f"local_fore_overdue_{item.get('foreshadow_id', '?')}",
                            "issue_type": "foreshadowing_overdue",
                            "severity": "medium",
                            "description": f"高重要性伏笔 '{item.get('description', '')}' 计划在第{intended}章回收，但已写到第{to_chapter}章仍未回收",
                            "chapter_id_a": planted,
                            "chapter_id_b": to_chapter,
                            "evidence_a": f"种植于第{planted}章",
                            "evidence_b": f"当前第{to_chapter}章仍未回收",
                            "suggestion": f"考虑在第{to_chapter + 1}至{to_chapter + 3}章规划此伏笔的回收。",
                        })

        return issues

    def _llm_deep_check(self, from_chapter: int, to_chapter: int) -> list:
        """LLM 深度一致性检查。"""
        # 收集上下文
        chapters_content = []
        for ch_id in range(from_chapter, to_chapter + 1):
            ch_path = self.paths.chapter_file(ch_id)
            if ch_path.exists():
                content = load_markdown(ch_path)
                # 只取前 500 字摘要避免 prompt 过长
                summary_text = content[:500] + "..." if len(content) > 500 else content
                chapters_content.append(f"### 第{ch_id}章\n{summary_text}")

        male = load_yaml(self.paths.protagonist_male)
        female = load_yaml(self.paths.protagonist_female)
        relationship = self.memory.load_relationship_state()

        prompt = f"""你是一位小说一致性检查专家。请检查以下章节之间是否存在一致性问题。

## 角色设定
男主：{json.dumps(male, ensure_ascii=False)[:500]}
女主：{json.dumps(female, ensure_ascii=False)[:500]}

## 当前关系状态
{json.dumps(relationship, ensure_ascii=False)[:500]}

## 章节内容（摘要）
{chr(10).join(chapters_content)}

请检查：
1. 人物行为是否跨章节一致
2. 关系状态是否有矛盾
3. 时间线是否有冲突
4. 对话风格是否稳定

请以 JSON 格式输出发现的问题：
```json
[{{"issue_id": "llm_001", "issue_type": "character_inconsistency", "severity": "medium", "description": "...", "chapter_id_a": 1, "chapter_id_b": 2, "evidence_a": "...", "evidence_b": "...", "suggestion": "..."}}]
```

如果没有发现问题，输出空数组 []。"""

        try:
            policy = AgentCallPolicy(
                agent_name="ConsistencyAgent",
                task_type="consistency_check",
                target_prompt_tokens=12000,
                min_response_chars=100,
                allow_fallback=True,
            )
            response = self.runtime.call(prompt, policy)
            # 解析 JSON
            json_str = response
            start = json_str.find("[")
            end = json_str.rfind("]")
            if start >= 0 and end > start:
                return json.loads(json_str[start:end + 1])
        except Exception:
            pass
        return []

    def _check_dialogue_consistency(self, from_chapter: int, to_chapter: int) -> list:
        """角色对话风格一致性检测。"""
        issues = []

        # 提取每个角色的对话样本
        male_name = ""
        female_name = ""
        male_config = load_yaml(self.paths.protagonist_male)
        female_config = load_yaml(self.paths.protagonist_female)
        if isinstance(male_config, dict):
            male_name = male_config.get("name", "")
        if isinstance(female_config, dict):
            female_name = female_config.get("name", "")

        all_samples = {male_name: [], female_name: []}

        for ch_id in range(from_chapter, to_chapter + 1):
            ch_path = self.paths.chapter_file(ch_id)
            if not ch_path.exists():
                continue
            content = load_markdown(ch_path)
            # 提取引号内的对话
            for name in [male_name, female_name]:
                if not name:
                    continue
                dialogue_lines = self._extract_dialogue(content, name)
                if dialogue_lines:
                    all_samples[name].append({
                        "chapter_id": ch_id,
                        "lines": dialogue_lines[:5],  # 每章最多取 5 句
                    })

        # 对比分析
        for char_name, samples in all_samples.items():
            if len(samples) < 2:
                continue

            # 简单启发式：检查平均句子长度是否突变
            avg_lengths = []
            for s in samples:
                lengths = [len(line) for line in s["lines"]]
                avg_lengths.append({
                    "chapter_id": s["chapter_id"],
                    "avg_len": sum(lengths) / max(len(lengths), 1),
                })

            for i in range(1, len(avg_lengths)):
                prev = avg_lengths[i - 1]
                curr = avg_lengths[i]
                if prev["avg_len"] > 0 and (curr["avg_len"] / max(prev["avg_len"], 1) > 2.0 or curr["avg_len"] / max(prev["avg_len"], 1) < 0.3):
                    issues.append({
                        "issue_id": f"dialogue_len_{char_name}_{curr['chapter_id']}",
                        "issue_type": "dialogue_shift",
                        "severity": "low",
                        "description": f"{char_name} 在第{curr['chapter_id']}章的对话平均长度({curr['avg_len']:.1f}字)与第{prev['chapter_id']}章({prev['avg_len']:.1f}字)差异显著",
                        "chapter_id_a": prev["chapter_id"],
                        "chapter_id_b": curr["chapter_id"],
                        "evidence_a": f"平均对话长度: {prev['avg_len']:.1f}",
                        "evidence_b": f"平均对话长度: {curr['avg_len']:.1f}",
                        "suggestion": f"确认 {char_name} 的对话风格变化是否有剧情原因。如无特殊原因，建议保持一致的说话风格。",
                    })

        return issues

    def _extract_dialogue(self, text: str, char_name: str) -> list:
        """从文本中提取指定角色的对话行。"""
        lines = []
        # 匹配 "xxx说" 或 "xxx：" 等模式
        patterns = [
            rf'{char_name}[说问道回答喊叫嚷嘀咕]{1,2}[""''""]',
            rf'{char_name}：',
            rf'{char_name}:',
        ]
        for line in text.split('\n'):
            for pat in patterns:
                if re.search(pat, line):
                    # 提取引号内的内容
                    quoted = re.findall(r'[""]([^""]*)[""]', line)
                    if not quoted:
                        quoted = re.findall(r"['']([^'']*)['']", line)
                    if quoted:
                        lines.extend(quoted)
                    break
        return lines

    def _get_romance_history(self) -> list:
        """获取恋爱数值历史。"""
        romance_progress_path = self.paths.romance_progress
        if romance_progress_path.exists():
            data = load_yaml(romance_progress_path)
            if isinstance(data, dict) and "romance_level_history" in data:
                return data["romance_level_history"]

        # Fallback: 从 chapter_summaries + relationship_state 重建
        history = [{"chapter_id": 0, "romance_level": 0, "trust_level": 0, "conflict_level": 0}]
        summaries = self.memory.get_recent_summaries(n=100)
        ri = self.memory.load_relationship_state()
        if ri:
            history.append({
                "chapter_id": len(summaries),
                "romance_level": ri.get("romance_level", 0),
                "trust_level": ri.get("trust_level", 0),
                "conflict_level": ri.get("conflict_level", 0),
            })
        return history

    def _generate_summary(self, report: dict) -> str:
        """生成检查摘要。"""
        total = report["total_issues"]
        score = report["overall_score"]
        if score >= 95:
            grade = "优秀 - 跨章节一致性良好"
        elif score >= 80:
            grade = "良好 - 存在少量问题，建议修复后再继续"
        elif score >= 60:
            grade = "一般 - 存在较多不一致问题，强烈建议修复"
        else:
            grade = "差 - 一致性严重不足，建议暂停新章节创作，优先修复"

        summary = f"""## 一致性检查摘要

**总分**: {score}/100 ({grade})
**检查范围**: 第 {report['checked_chapters'][0]} - {report['checked_chapters'][-1]} 章
**问题总数**: {total}

### 问题分类
"""
        for itype, count in report.get("issues_by_type", {}).items():
            type_labels = {
                "relationship_jump": "关系数值跳跃",
                "foreshadowing_overdue": "伏笔超期未回收",
                "character_inconsistency": "角色行为不一致",
                "dialogue_shift": "对话风格变化",
                "event_conflict": "事件矛盾",
                "timeline_error": "时间线错误",
            }
            label = type_labels.get(itype, itype)
            summary += f"- {label}: {count} 处\n"

        if total == 0:
            summary += "\n未发现一致性问题。\n"
        else:
            summary += f"\n建议逐一修复以上问题后，再继续后续章节创作。\n"

        return summary

    def _save_report(self, report: dict):
        """保存一致性报告。"""
        report_md = f"""# 跨章节一致性检查报告

{report['summary']}

---

## 详细问题列表

"""
        for issue in report["issues"]:
            severity_icon = {"low": "🟡", "medium": "🟠", "high": "🔴", "critical": "⛔"}.get(issue.get("severity", "low"), "⚪")
            report_md += f"""
### {severity_icon} {issue.get('description', '')}

- **类型**: {issue.get('issue_type', '')}
- **严重程度**: {issue.get('severity', '')}
- **涉及章节**: 第 {issue.get('chapter_id_a', '?')} 章 vs 第 {issue.get('chapter_id_b', '?')} 章
- **证据 A**: {issue.get('evidence_a', '')}
- **证据 B**: {issue.get('evidence_b', '')}
- **建议**: {issue.get('suggestion', '')}

---
"""
        report_md += f"\n*报告生成时间: 自动检查完成*"

        save_markdown(self.paths.consistency_report_file(), report_md)

