"""QueryAgent - 简单关键词搜索。"""

import json
import yaml
from pathlib import Path
from ..config import load_markdown, load_yaml, load_json
from ..paths import ProjectPaths
from ..memory import FileMemoryStore


class QueryAgent:
    """跨项目文件的关键词搜索。"""

    def __init__(self, paths: ProjectPaths, memory: FileMemoryStore):
        self.paths = paths
        self.memory = memory

    def search(self, keyword: str) -> list:
        """在项目文件中搜索关键词。"""
        results = []

        # 1. 搜索记忆文件
        memory_results = self.memory.search_all(keyword)
        results.extend(memory_results)

        # 2. 搜索 YAML 文件
        yaml_files = [
            self.paths.novel_config,
            self.paths.style_config,
            self.paths.protagonist_male,
            self.paths.protagonist_female,
            self.paths.supporting_characters,
            self.paths.relationship_state,
            self.paths.romance_progress,
            self.paths.school_setting,
            self.paths.class_schedule,
            self.paths.campus_events,
            self.paths.exam_calendar,
            self.paths.chapter_outline,
        ]

        for yaml_path in yaml_files:
            if not yaml_path.exists():
                continue
            try:
                data = load_yaml(yaml_path)
                text = json.dumps(data, ensure_ascii=False)
                if keyword.lower() in text.lower():
                    # 找到相关片段
                    snippet = self._find_snippet(text, keyword)
                    results.append({
                        "source": str(yaml_path.relative_to(self.paths.root.parent)),
                        "type": "yaml_config",
                        "match": snippet,
                    })
            except Exception:
                pass

        # 3. 搜索 Markdown 文件
        md_files = [
            self.paths.premise,
            self.paths.master_outline,
            self.paths.volume_outline,
        ]

        for md_path in md_files:
            if not md_path.exists():
                continue
            try:
                content = load_markdown(md_path)
                if keyword.lower() in content.lower():
                    snippet = self._find_snippet(content, keyword)
                    results.append({
                        "source": str(md_path.relative_to(self.paths.root.parent)),
                        "type": "markdown",
                        "match": snippet,
                    })
            except Exception:
                pass

        # 4. 搜索已有章节
        chapters_dir = self.paths.chapters_dir
        if chapters_dir.exists():
            for chapter_file in sorted(chapters_dir.glob("chapter_*.md")):
                try:
                    content = load_markdown(chapter_file)
                    if keyword.lower() in content.lower():
                        snippet = self._find_snippet(content, keyword)
                        results.append({
                            "source": str(chapter_file.relative_to(self.paths.root.parent)),
                            "type": "chapter",
                            "match": snippet,
                        })
                except Exception:
                    pass

        return results

    def _find_snippet(self, text: str, keyword: str, context_chars: int = 60) -> str:
        """在文本中找到关键词并返回上下文片段。"""
        idx = text.lower().find(keyword.lower())
        if idx < 0:
            return text[:context_chars * 2]

        start = max(0, idx - context_chars)
        end = min(len(text), idx + len(keyword) + context_chars)

        snippet = ""
        if start > 0:
            snippet += "..."
        snippet += text[start:end]
        if end < len(text):
            snippet += "..."

        return snippet.strip()

    def format_results(self, results: list, keyword: str) -> str:
        """格式化搜索结果。"""
        if not results:
            return f"\n## 关键词搜索结果：\"{keyword}\"\n\n未找到匹配结果。\n"

        lines = [f"\n## 关键词搜索结果：\"{keyword}\"\n"]
        for i, result in enumerate(results):
            source = result.get("source", "未知")
            rtype = result.get("type", "未知")
            match = result.get("match", "")
            # 截断过长的匹配
            if isinstance(match, str) and len(match) > 200:
                match = match[:200] + "..."
            elif isinstance(match, dict):
                match = json.dumps(match, ensure_ascii=False, indent=2)[:300]

            lines.append(f"### {i + 1}. {source}")
            lines.append(f"类型：{rtype}")
            lines.append(f"```\n{match}\n```\n")

        lines.append(f"共找到 {len(results)} 个匹配结果。\n")
        return "\n".join(lines)

