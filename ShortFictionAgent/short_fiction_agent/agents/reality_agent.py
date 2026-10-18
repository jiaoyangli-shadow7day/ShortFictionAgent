"""RealityAgent - 负责审核内容的现实逻辑合理性。"""

import yaml
import re
from pathlib import Path
from ..config import save_markdown, load_markdown, load_yaml
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class RealityAgent:
    """现实逻辑审核。

    检查小说内容是否与现实世界的时代技术、教育制度、地理交通、
    社会经济、日常细节、制度流程相符。
    """

    def __init__(self, paths: ProjectPaths, llm: LLMClient, memory: FileMemoryStore, pack=None):
        from ..genres import resolve_project_pack
        self.paths = paths
        self.llm = llm
        self.memory = memory
        self.pack = pack or resolve_project_pack(paths)
        self.runtime = AgentRuntime(paths, llm)

    def _get_prompt(self, name: str) -> str:
        return self.pack.read_prompt(name)

    def check_outline(self) -> str:
        """审核全书大纲的现实逻辑。"""
        outline_path = self.paths.master_outline
        if not outline_path.exists():
            return "[错误] 全书大纲不存在，请先执行 plan-book 命令。"

        outline_content = load_markdown(outline_path)
        if not outline_content:
            return "[错误] 全书大纲为空。"

        return self._run_check(outline_content, "全书大纲")

    def check_chapter(self, chapter_id: int) -> str:
        """审核指定章节的现实逻辑。"""
        chapter_content = load_markdown(self.paths.chapter_file(chapter_id))
        if not chapter_content:
            return f"[错误] 第 {chapter_id} 章不存在，请先执行 write 命令。"

        chapter_outline = {}
        outlines = load_yaml(self.paths.chapter_outline)
        if outlines and "chapters" in outlines:
            for ch in outlines["chapters"]:
                if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                    chapter_outline = ch
                    break

        return self._run_check(chapter_content, f"第{chapter_id}章", chapter_outline)

    def _run_check(self, content: str, label: str, chapter_outline: dict = None) -> str:
        """执行现实逻辑审核。"""
        reality_prompt = self._get_prompt("reality_check_prompt.md")
        novel_config = load_yaml(self.paths.novel_config)

        preview = self.runtime.preview_text(content, 4200, 1500)

        full_prompt = f"""{reality_prompt}

## 小说基本信息
```yaml
{yaml.dump(novel_config, allow_unicode=True)}
```

## 待审核内容：{label}
```markdown
{preview}
```
"""
        if chapter_outline:
            full_prompt += f"""
## 本章大纲
```yaml
{yaml.dump(chapter_outline, allow_unicode=True)}
```
"""

        full_prompt += """
请基于以上所有信息，对该内容进行全面现实逻辑审核。"""

        if "大纲" in label:
            report_path = self.paths.reviews_dir / "outline_reality_check.md"
        else:
            # label 形如 "第N章"，提取章号后用集中管理的路径，保证读写一致。
            import re as _re
            m = _re.search(r"(\d+)", label)
            if m:
                report_path = self.paths.reality_report_file(int(m.group(1)))
            else:
                report_path = self.paths.reviews_dir / f"{label.replace('第', 'chapter_').replace('章', '')}_reality.md"
        policy = AgentCallPolicy(
            agent_name="RealityAgent",
            task_type="reality_check",
            target_prompt_tokens=12000,
            min_response_chars=120,
        )
        response = self.runtime.call(
            full_prompt,
            policy,
            compact_prompt=lambda: self._build_compact_reality_prompt(
                reality_prompt,
                novel_config,
                content,
                label,
                chapter_outline,
            ),
            output_path=report_path,
        )

        return response

    def _build_compact_reality_prompt(
        self,
        reality_prompt: str,
        novel_config: dict,
        content: str,
        label: str,
        chapter_outline: dict = None,
    ) -> str:
        prompt = f"""{self.runtime.truncate_text(reality_prompt, 1600)}

## 小说基本信息
```yaml
{yaml.dump(novel_config, allow_unicode=True)}
```

## 待审核内容：{label}（紧凑版）
```markdown
{self.runtime.preview_text(content, 2200, 800)}
```
"""
        if chapter_outline:
            prompt += f"""
## 本章大纲（关键字段）
```yaml
{yaml.dump(self.runtime.compact_yaml_items([chapter_outline], max_items=1), allow_unicode=True)}
```
"""
        prompt += "\n请审核现实逻辑，只列严重问题、建议问题和提醒。"
        return prompt

    def get_issue_count(self, report_content: str) -> dict:
        """从审核报告中提取问题统计。"""
        # 只统计各维度下的实际条目（**[]** 格式），而非仅看章节标题
        severe_section = re.search(r'###\s*🔴\s*严重.*?\n(.*?)(?=###|\Z)', report_content, re.DOTALL)
        suggestion_section = re.search(r'###\s*🟡\s*建议.*?\n(.*?)(?=###|\Z)', report_content, re.DOTALL)
        reminder_section = re.search(r'###\s*🔵\s*提醒.*?\n(.*?)(?=###|\Z)', report_content, re.DOTALL)

        def count_items(section_text: str) -> int:
            if not section_text:
                return 0
            # 如果有 "无" 或 "无。" 则返回 0
            if re.search(r'^\s*(无|无。|暂无|未发现)\s*$', section_text.strip(), re.MULTILINE):
                return 0
            return len(re.findall(r'[-\*]\s*\*\*\[', section_text))

        severe = count_items(severe_section.group(1) if severe_section else "")
        suggestion = count_items(suggestion_section.group(1) if suggestion_section else "")
        reminder = count_items(reminder_section.group(1) if reminder_section else "")

        return {
            "severe": severe,
            "suggestion": suggestion,
            "reminder": reminder,
            "has_severe_issues": severe > 0,
        }

    def check_outline_with_summary(self) -> dict:
        """审核全书大纲并返回摘要。"""
        report = self.check_outline()
        issues = self.get_issue_count(report)
        return {
            "report": report,
            "issues": issues,
            "passed": not issues.get("has_severe_issues", False),
        }

    def check_chapter_with_summary(self, chapter_id: int) -> dict:
        """审核指定章节并返回摘要。"""
        report = self.check_chapter(chapter_id)
        issues = self.get_issue_count(report)
        return {
            "chapter_id": chapter_id,
            "report": report,
            "issues": issues,
            "passed": not issues.get("has_severe_issues", False),
        }

