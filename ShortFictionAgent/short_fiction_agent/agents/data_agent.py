"""DataAgent - 写后数据提取与记忆更新。"""

import json
import yaml
from pathlib import Path
from ..config import save_markdown, load_markdown, load_yaml, load_json
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class DataAgent:
    """从章节中提取结构化数据，更新记忆和关系状态。"""

    def __init__(self, paths: ProjectPaths, llm: LLMClient, memory: FileMemoryStore, pack=None):
        from ..genres import resolve_project_pack
        self.paths = paths
        self.llm = llm
        self.memory = memory
        self.pack = pack or resolve_project_pack(paths)
        self.runtime = AgentRuntime(paths, llm)

    def _get_prompt(self, name: str) -> str:
        return self.pack.read_prompt(name)

    def update_memory(self, chapter_id: int) -> bool:
        """从章节中提取数据并更新所有记忆文件。"""
        # 读取章节内容
        chapter_content = load_markdown(self.paths.chapter_file(chapter_id))
        if not chapter_content:
            raise RuntimeError(f"第 {chapter_id} 章不存在。")

        # 读取章节大纲
        chapter_outline = {}
        outlines = load_yaml(self.paths.chapter_outline)
        if outlines and "chapters" in outlines:
            for ch in outlines["chapters"]:
                if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                    chapter_outline = ch
                    break

        # 获取写作规则(题材相关)
        rules = self.pack.rules_text()

        data_extract_prompt = self._get_prompt("data_extract_prompt.md")

        chapter_preview = self.runtime.preview_text(chapter_content, 4800, 1600)
        rules_preview = self.runtime.compress_text(rules, 1800, "写作规则")

        full_prompt = f"""{data_extract_prompt}

## 章节大纲
```yaml
{yaml.dump(chapter_outline, allow_unicode=True)}
```

## 章节正文（保留开头与结尾）
```markdown
{chapter_preview}
```

## 写作规则
{rules_preview}

请从本章中提取结构化数据，以 JSON 格式输出。"""

        policy = AgentCallPolicy(
            agent_name="DataAgent",
            task_type="data_extract",
            target_prompt_tokens=12000,
            min_response_chars=60,
            # 模型由集中式路由表决定(data_extract=lite→flash)。
            # 若发现关系/伏笔抽取准确性下降, 可在 model.yaml 把 DataAgent 改回 primary。
        )
        response = self.runtime.call(
            full_prompt,
            policy,
            compact_prompt=lambda: self._build_compact_data_prompt(
                data_extract_prompt,
                chapter_outline,
                chapter_content,
                rules,
            ),
        )

        # 解析 JSON
        data = self._parse_data_response(response)

        if not data:
            raise RuntimeError("无法解析数据抽取结果，拒绝继续更新记忆。")

        # 更新各记忆文件
        self._apply_data(chapter_id, data)
        return True

    def _build_compact_data_prompt(
        self,
        data_extract_prompt: str,
        chapter_outline: dict,
        chapter_content: str,
        rules: str,
    ) -> str:
        return f"""{self.runtime.truncate_text(data_extract_prompt, 1600)}

## 章节大纲（关键字段）
```yaml
{yaml.dump(self.runtime.compact_yaml_items([chapter_outline], max_items=1), allow_unicode=True)}
```

## 章节正文（紧凑版）
```markdown
{self.runtime.preview_text(chapter_content, 2200, 900)}
```

## 写作规则摘要
{self.runtime.compress_text(rules, 1000, "写作规则")}

请只输出 JSON 对象，字段至少包含 chapter_summary，可选 events、foreshadowing、relationship_update。"""

    def _parse_data_response(self, response: str) -> dict:
        """解析 LLM 返回的 JSON 数据。"""
        # 提取 JSON 块
        lines = response.split("\n")
        json_lines = []
        in_json = False
        for line in lines:
            if line.strip().startswith("```"):
                if in_json:
                    break
                in_json = True
                continue
            if in_json:
                json_lines.append(line)

        json_str = "\n".join(json_lines)

        # 如果没找到代码块，尝试直接解析整个响应
        if not json_str.strip():
            json_str = response

        try:
            # 尝试找到 JSON 对象
            start = json_str.find("{")
            end = json_str.rfind("}")
            if start >= 0 and end > start:
                return json.loads(json_str[start:end + 1])
        except json.JSONDecodeError:
            pass
        return {}

    def _apply_data(self, chapter_id: int, data: dict):
        """将抽取的数据写入记忆文件。"""
        updated_files = []

        # 1. 更新章节摘要
        if "chapter_summary" in data:
            self.memory.append_chapter_summary(chapter_id, data["chapter_summary"])
            updated_files.append("memory/chapter_summaries.json")

        # 2. 更新事件日志
        if "events" in data and isinstance(data["events"], list):
            self.memory.append_events(data["events"])
            updated_files.append("memory/event_log.json")

        # 3. 更新伏笔
        if "foreshadowing" in data and isinstance(data["foreshadowing"], list):
            for item in data["foreshadowing"]:
                if isinstance(item, dict):
                    self.memory.upsert_foreshadowing(item)
            updated_files.append("memory/foreshadowing.json")

        # 4. 更新关系状态
        if "relationship_update" in data:
            self.memory.update_relationship_state(data["relationship_update"])
            updated_files.append("relationship/relationship_state.yaml")

        if updated_files:
            for f in updated_files:
                print(f"  已更新: {f}")
        else:
            print("  未检测到可更新的数据。")

