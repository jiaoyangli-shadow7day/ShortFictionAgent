"""ReviewAgent - 负责审查章节质量。"""

import yaml
from pathlib import Path
from ..config import save_markdown, load_markdown, load_yaml
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from ..models import ReviewThresholds
from ..agent_runtime import AgentRuntime, AgentCallPolicy
import re


class ReviewAgent:
    """章节质量审查。"""

    def __init__(self, paths: ProjectPaths, llm: LLMClient, memory: FileMemoryStore, pack=None):
        from ..genres import resolve_project_pack
        self.paths = paths
        self.llm = llm
        self.memory = memory
        self.pack = pack or resolve_project_pack(paths)
        self.runtime = AgentRuntime(paths, llm)

    def _get_prompt(self, name: str) -> str:
        return self.pack.read_prompt(name)

    def _get_rules(self) -> str:
        return self.pack.rules_text()

    def _get_commercial_rules(self) -> str:
        return self.pack.read_prompt("commercial_narrative_rules.md")

    def review_chapter(self, chapter_id: int) -> str:
        """审查指定章节。"""
        # 读取章节内容
        chapter_content = load_markdown(self.paths.chapter_file(chapter_id))
        if not chapter_content:
            return f"[错误] 第 {chapter_id} 章不存在，请先执行 write 命令。"

        # 读取审查所需上下文
        chapter_outline = {}
        outlines = load_yaml(self.paths.chapter_outline)
        if outlines and "chapters" in outlines:
            for ch in outlines["chapters"]:
                if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                    chapter_outline = ch
                    break

        relationship_state = self.memory.load_relationship_state()
        recent_summaries = self.memory.get_recent_summaries(n=3)
        male = load_yaml(self.paths.protagonist_male)
        female = load_yaml(self.paths.protagonist_female)
        style = load_yaml(self.paths.style_config)

        review_prompt = self._get_prompt("review_prompt.md")
        rules = self._get_rules()
        commercial_rules = self._get_commercial_rules()

        # Review the complete chapter whenever the normal prompt budget allows it.
        chapter_preview = chapter_content

        # 精简角色设定
        male_brief = {k: male.get(k, "") for k in ["name", "personality", "speaking_style", "forbidden_behaviors"] if k in male}
        female_brief = {k: female.get(k, "") for k in ["name", "personality", "speaking_style", "forbidden_behaviors"] if k in female}

        full_prompt = f"""{review_prompt}

## 本章内容
```markdown
{chapter_preview}
```

## 本章大纲
```yaml
{yaml.dump(chapter_outline, allow_unicode=True)}
```

## 男主设定（精简）
```yaml
{yaml.dump(male_brief, allow_unicode=True)}
```

## 女主设定（精简）
```yaml
{yaml.dump(female_brief, allow_unicode=True)}
```

## 当前关系状态
```yaml
{yaml.dump(relationship_state, allow_unicode=True)}
```

## 最近章节摘要
```json
{yaml.dump(recent_summaries, allow_unicode=True)}
```

## 写作规则
{rules}

## 商业追读叙事规则
{commercial_rules}

请基于以上所有信息，对第 {chapter_id} 章进行全面审查。"""

        review_path = self.paths.review_file(chapter_id)
        policy = AgentCallPolicy(
            agent_name="ReviewAgent",
            task_type="review",
            target_prompt_tokens=14000,
            min_response_chars=220,
            required_regex=r"总分[\s\S]*?\d+\s*/\s*100",
        )
        compact_prompt = self._build_compact_review_prompt(
            chapter_id=chapter_id, review_prompt=review_prompt,
            chapter_content=chapter_content, chapter_outline=chapter_outline,
            relationship_state=relationship_state, recent_summaries=recent_summaries,
            male_brief=male_brief, female_brief=female_brief, rules=rules,
            commercial_rules=commercial_rules,
        )
        if self.runtime.estimate_tokens(compact_prompt) > self.runtime.policy_limit(policy):
            return "[错误] 全文审查超过安全上下文预算，不能以截断正文的报告放行。"
        response = self.runtime.call(
            full_prompt,
            policy,
            compact_prompt=compact_prompt,
            output_path=review_path,
        )

        return response

    def _build_compact_review_prompt(
        self,
        chapter_id: int,
        review_prompt: str,
        chapter_content: str,
        chapter_outline: dict,
        relationship_state: dict,
        recent_summaries: list,
        male_brief: dict,
        female_brief: dict,
        rules: str,
        commercial_rules: str,
    ) -> str:
        """Build a smaller review prompt that preserves scoring structure."""
        compact_chapter = chapter_content
        compact_outline = self.runtime.compact_yaml_items([chapter_outline], max_items=1)
        compact_relationship = {
            k: relationship_state.get(k)
            for k in [
                "relationship_stage",
                "romance_level",
                "trust_level",
                "conflict_level",
                "unresolved_tensions",
                "male_to_female",
                "female_to_male",
            ]
            if isinstance(relationship_state, dict) and relationship_state.get(k) not in (None, "", [])
        }
        compact_summaries = recent_summaries[-1:] if isinstance(recent_summaries, list) else recent_summaries

        return f"""{self.runtime.compress_text(review_prompt, 1800, "审查提示词")}

## 本章内容（紧凑版，保留开头与结尾）
```markdown
{compact_chapter}
```

## 本章大纲（关键字段）
```yaml
{yaml.dump(compact_outline, allow_unicode=True)}
```

## 角色设定（极简）
```yaml
male: {yaml.dump(male_brief, allow_unicode=True)}
female: {yaml.dump(female_brief, allow_unicode=True)}
```

## 当前关系状态（关键字段）
```yaml
{yaml.dump(compact_relationship, allow_unicode=True)}
```

## 最近章节摘要（仅保留最近 1 条）
```yaml
{yaml.dump(compact_summaries, allow_unicode=True)}
```

## 写作规则摘要
{self.runtime.compress_text(rules, 1200, "写作规则")}

## 商业追读规则摘要
{self.runtime.compress_text(commercial_rules, 1200, "商业规则")}

请对第 {chapter_id} 章进行审查。必须输出「总分：x/100」，并列出主要问题、必须修改项和保留优点。"""

    def check_thresholds(self, chapter_id: int, thresholds: ReviewThresholds = None) -> dict:
        """检查审查评分是否低于阈值，返回告警列表。"""
        if thresholds is None:
            thresholds = ReviewThresholds()

        review_content = load_markdown(self.paths.review_file(chapter_id))
        if not review_content:
            return {"warnings": [], "error": "审查报告不存在"}

        warnings = []

        # 提取总分
        total_score = None
        match = re.search(r'总分[\s\S]*?(\d+)\s*/\s*100', review_content)
        if match:
            total_score = int(match.group(1))

        if total_score is not None and total_score < thresholds.min_total_score:
            warnings.append({
                "type": "total_score_low",
                "severity": "high",
                "chapter_id": chapter_id,
                "message": f"第{chapter_id}章总分 {total_score}/100 低于阈值 {thresholds.min_total_score}/100",
                "suggestion": "建议根据审查报告的「必须修改」项进行修订后重新审查。",
                "actual": total_score,
                "threshold": thresholds.min_total_score,
            })

        # 提取各维度评分
        dim_pattern = re.findall(r'[-\*]\s*(.{2,20})[:：]\s*(\d+)\s*/\s*10', review_content)
        for dim_name, score_str in dim_pattern:
            score = int(score_str)
            if score < thresholds.min_dimension_score:
                warnings.append({
                    "type": "dimension_low",
                    "severity": "medium",
                    "chapter_id": chapter_id,
                    "dimension": dim_name.strip(),
                    "message": f"第{chapter_id}章「{dim_name.strip()}」维度得分 {score}/10 低于阈值 {thresholds.min_dimension_score}/10",
                    "suggestion": f"请关注本章的{dim_name.strip()}问题。",
                    "actual": score,
                    "threshold": thresholds.min_dimension_score,
                })

        # 检查高风险项
        risk_pattern = re.findall(r'[-\*]\s*(.{2,20}(?:风险|腔))[:：]\s*(高|中|低)', review_content)
        for risk_name, risk_level in risk_pattern:
            if risk_level == "高":
                warnings.append({
                    "type": "high_risk",
                    "severity": "high",
                    "chapter_id": chapter_id,
                    "risk": risk_name.strip(),
                    "message": f"第{chapter_id}章「{risk_name.strip()}」风险为高",
                    "suggestion": f"{risk_name.strip()}高风险可能严重影响阅读体验，建议优先处理。",
                })

        result = {
            "chapter_id": chapter_id,
            "total_score": total_score,
            "warnings_count": len(warnings),
            "warnings": warnings,
            "passed": len(warnings) == 0,
        }

        # 保存告警报告
        self._save_threshold_report(chapter_id, result)

        return result

    def review_with_thresholds(self, chapter_id: int, thresholds: ReviewThresholds = None) -> dict:
        """审查章节并检查阈值。返回审查结果 + 阈值告警。"""
        review_result = self.review_chapter(chapter_id)
        threshold_result = self.check_thresholds(chapter_id, thresholds)
        return {
            "review": review_result,
            "threshold_check": threshold_result,
        }

    def batch_review(self, from_chapter: int, to_chapter: int, thresholds: ReviewThresholds = None) -> dict:
        """批量审查多个章节。"""
        results = []
        all_passed = True
        for ch_id in range(from_chapter, to_chapter + 1):
            ch_path = self.paths.chapter_file(ch_id)
            if not ch_path.exists():
                results.append({"chapter_id": ch_id, "error": "章节文件不存在", "passed": False})
                continue
            result = self.check_thresholds(ch_id, thresholds)
            results.append(result)
            if not result.get("passed", False):
                all_passed = False

        return {
            "chapters_checked": list(range(from_chapter, to_chapter + 1)),
            "total_warnings": sum(r.get("warnings_count", 0) for r in results),
            "all_passed": all_passed,
            "results": results,
        }

    def _save_threshold_report(self, chapter_id: int, result: dict):
        """保存阈值检查报告。"""
        report_md = f"""# 第 {chapter_id} 章阈值检查报告

## 检查结果: {'✅ 通过' if result['passed'] else '⚠️ 未通过'}

- **总分**: {result.get('total_score', 'N/A')}
- **告警数**: {result['warnings_count']}

"""
        if result["warnings"]:
            report_md += "## 告警详情\n\n"
            for i, w in enumerate(result["warnings"]):
                severity_icon = {"high": "🔴", "medium": "🟠", "low": "🟡"}.get(w.get("severity", "low"), "⚪")
                report_md += f"""### {severity_icon} {w.get('message', '')}
- **类型**: {w.get('type', '')}
- **建议**: {w.get('suggestion', '')}

"""

        report_path = self.paths.reviews_dir / f"chapter_{chapter_id:03d}_threshold.md"
        save_markdown(report_path, report_md)

    def get_review(self, chapter_id: int) -> str:
        """读取已有审查报告。"""
        review_path = self.paths.review_file(chapter_id)
        return load_markdown(review_path)

