"""OutlineAgent - 负责生成全书大纲、卷纲、章节大纲。"""

import yaml
import json
from pathlib import Path
from ..config import save_yaml, save_markdown, load_markdown, load_yaml
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class OutlineAgent:
    """动态生成大纲。不写死任何固定结构。"""

    def __init__(self, paths: ProjectPaths, llm: LLMClient, memory: FileMemoryStore = None, pack=None):
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

    def _build_context(self) -> dict:
        """收集生成大纲所需的所有上下文。"""
        ctx = {}
        ctx["novel_config"] = load_yaml(self.paths.novel_config)
        ctx["male"] = load_yaml(self.paths.protagonist_male)
        ctx["female"] = load_yaml(self.paths.protagonist_female)
        ctx["supporting"] = load_yaml(self.paths.supporting_characters)
        ctx["school"] = load_yaml(self.paths.school_setting)
        ctx["relationship"] = load_yaml(self.paths.relationship_state)
        return ctx

    def plan_book(self) -> str:
        """生成全书大纲。"""
        ctx = self._build_context()
        book_outline_prompt = self._get_prompt("book_outline_prompt.md")
        rules = self._get_rules()
        commercial_rules = self._get_commercial_rules()

        full_prompt = f"""{book_outline_prompt}

## 青春校园恋爱写作规则
{rules}

## 商业追读叙事规则
{commercial_rules}

## 当前小说设定
```yaml
{yaml.dump(ctx, allow_unicode=True, default_flow_style=False)}
```

请根据以上设定，动态生成全书大纲。"""

        response = self.runtime.call(
            full_prompt,
            AgentCallPolicy(
                agent_name="OutlineAgent.plan_book",
                task_type="book_outline",
                target_prompt_tokens=16000,
                min_response_chars=500,
            ),
            compact_prompt=lambda: self._build_compact_book_prompt(book_outline_prompt, ctx, rules, commercial_rules),
            output_path=self.paths.master_outline,
        )
        return response

    def plan_chapters(self, volume: int = 1):
        """生成指定卷的章节大纲（与已有大纲合并，不覆盖其他卷）。"""
        ctx = self._build_context()
        master_outline = load_markdown(self.paths.master_outline)

        chapter_outline_prompt = self._get_prompt("chapter_outline_prompt.md")
        rules = self._get_rules()
        commercial_rules = self._get_commercial_rules()

        full_prompt = f"""{chapter_outline_prompt}

## 青春校园恋爱写作规则
{rules}

## 商业追读叙事规则
{commercial_rules}

## 小说设定
```yaml
{yaml.dump(ctx, allow_unicode=True, default_flow_style=False)}
```

## 全书大纲
{master_outline}

## 要求
请为第 {volume} 卷生成章节大纲。输出格式为 YAML。"""

        response = self.runtime.call(
            full_prompt,
            AgentCallPolicy(
                agent_name="OutlineAgent.plan_chapters",
                task_type="chapter_outline",
                target_prompt_tokens=16000,
                min_response_chars=500,
            ),
            compact_prompt=lambda: self._build_compact_chapter_prompt(
                chapter_outline_prompt,
                ctx,
                master_outline,
                volume,
                rules,
                commercial_rules,
            ),
        )

        # 解析新生成的大纲
        new_outlines = self._parse_chapter_outline_response(response, volume)

        # 与已有大纲合并，而非覆盖
        merged = self._merge_chapter_outlines(new_outlines, volume)
        save_yaml(self.paths.chapter_outline, merged)

        # 生成后立即做追读结构质检，提醒大纲是否只是“漂亮但不追读”
        try:
            from .narrative_guard_agent import NarrativeGuardAgent
            report = NarrativeGuardAgent(self.paths).audit_all_outlines()
            failed = report.get("failed", 0)
            avg_score = report.get("avg_score", 0)
            if failed:
                print(f"  [追读质检] {failed} 章未通过，平均分 {avg_score}/100，报告: {self.paths.story_dir / 'narrative_guard_report.md'}")
            else:
                print(f"  [追读质检] 章节大纲通过，平均分 {avg_score}/100")
        except Exception as e:
            print(f"  [警告] 追读质检失败: {e}")
        return response

    def _merge_chapter_outlines(self, new_outlines: dict, volume: int) -> dict:
        """将新生成的章节大纲与已有大纲合并。"""
        # 加载已有大纲
        existing = load_yaml(self.paths.chapter_outline)
        if not existing or not isinstance(existing, dict):
            existing = {"chapters": []}

        existing_chapters = existing.get("chapters", [])
        if not isinstance(existing_chapters, list):
            existing_chapters = []

        new_chapters = new_outlines.get("chapters", [])
        if not isinstance(new_chapters, list):
            # 新大纲解析失败，保留旧大纲
            print(f"  [警告] 第 {volume} 卷大纲解析失败，保留已有大纲")
            return existing

        # 确保新章节有 volume 标记
        for ch in new_chapters:
            if isinstance(ch, dict) and "volume" not in ch:
                ch["volume"] = volume

        # 按 chapter_id 合并（新章节替换同 ID 旧章节）
        merged_map = {}
        for ch in existing_chapters:
            if isinstance(ch, dict):
                merged_map[ch.get("chapter_id", 0)] = ch

        for ch in new_chapters:
            if isinstance(ch, dict):
                ch_id = ch.get("chapter_id", 0)
                if ch_id > 0:
                    merged_map[ch_id] = ch

        # 排序
        merged_list = sorted(merged_map.values(), key=lambda x: x.get("chapter_id", 0) if isinstance(x, dict) else 0)

        print(f"  章节大纲合并: 已有 {len(existing_chapters)} 章 + 新增 {len(new_chapters)} 章 → 合并后 {len(merged_list)} 章")
        return {"chapters": merged_list}

    def _parse_chapter_outline_response(self, response: str, volume: int) -> dict:
        """解析 LLM 返回的章节大纲。"""
        lines = response.split("\n")
        yaml_lines = []
        in_yaml = False
        for line in lines:
            if line.strip().startswith("```"):
                if in_yaml:
                    break
                in_yaml = True
                continue
            if in_yaml:
                yaml_lines.append(line)

        if yaml_lines:
            try:
                return yaml.safe_load("\n".join(yaml_lines))
            except yaml.YAMLError:
                pass

        # 兼容裸 YAML 响应。Mock、本地模型或部分 API 有时不会包 ```yaml。
        try:
            parsed = yaml.safe_load(response)
            if isinstance(parsed, dict):
                return parsed
        except yaml.YAMLError:
            pass

        # 如果解析失败，返回原始响应包装
        return {"volume": volume, "raw_response": response, "parse_error": True}

    def update_outline_from_chapters(self, from_chapter: int) -> str:
        """增量更新大纲：根据已写章节的实际内容和审查反馈，调整后续章节大纲。"""
        ctx = self._build_context()
        master_outline = load_markdown(self.paths.master_outline)

        # 获取已写章节信息
        written_info = []
        history_start = max(1, from_chapter - 8)
        for ch_id in range(history_start, from_chapter):
            ch_path = self.paths.chapter_file(ch_id)
            if ch_path.exists():
                content = load_markdown(ch_path)
                summary_text = self.runtime.preview_text(content, 260, 120)
                written_info.append("### 第" + str(ch_id) + "章已写内容（摘要）")
                written_info.append(summary_text[:420])

            # 审查反馈
            review_path = self.paths.review_file(ch_id)
            review_text = ""
            if review_path.exists():
                review_content = load_markdown(review_path)
                review_text = review_content[:200]

            # 记忆数据
            summary = {}
            if self.memory:
                summary = self.memory.get_chapter_summary(ch_id) or {}

            written_info.append("关系变化: " + str(summary.get("relationship_change", "")))
            if review_text:
                written_info.append("审查反馈: " + review_text[:150])

        # 获取当前章节大纲中未写的部分
        current_outlines = load_yaml(self.paths.chapter_outline)
        unwritten = []
        if current_outlines and "chapters" in current_outlines:
            for ch in current_outlines["chapters"]:
                if isinstance(ch, dict) and ch.get("chapter_id", 0) >= from_chapter:
                    unwritten.append(ch)
                    if len(unwritten) >= 12:
                        break

        outline_update_prompt = self._get_prompt("outline_update_prompt.md")
        rules = self._get_rules()
        commercial_rules = self._get_commercial_rules()

        full_prompt = (
            outline_update_prompt + "\n\n"
            + "## 青春校园恋爱写作规则\n" + self.runtime.compress_text(rules, 2000, "写作规则") + "\n\n"
            + "## 商业追读叙事规则\n" + self.runtime.compress_text(commercial_rules, 2000, "商业规则") + "\n\n"
            + "## 小说设定\n```yaml\n" + yaml.dump(ctx, allow_unicode=True, default_flow_style=False) + "\n```\n\n"
            + "## 全书大纲（摘要）\n" + self.runtime.truncate_text(master_outline, 5000) + "\n\n"
            + "## 最近已写章节反馈（滚动窗口）\n" + "\n".join(written_info) + "\n\n"
            + "## 需要调整的后续大纲（最多后续 12 章）\n```yaml\n" + yaml.dump(self.runtime.compact_yaml_items(unwritten, max_items=12), allow_unicode=True) + "\n```\n\n"
            + "## 要求\n请基于已写章节的实际内容和审查反馈，对从第 " + str(from_chapter) + " 章起的后续大纲进行必要调整。输出格式为 YAML，只输出需要调整的章节。未调整的章节不需要输出。"
        )

        response = self.runtime.call(
            full_prompt,
            AgentCallPolicy(
                agent_name="OutlineAgent.update_outline",
                task_type="outline_update",
                target_prompt_tokens=14000,
                min_response_chars=80,
            ),
            compact_prompt=lambda: self._build_compact_update_prompt(
                outline_update_prompt,
                ctx,
                from_chapter,
                written_info,
                unwritten,
                rules,
                commercial_rules,
            ),
        )

        # 尝试解析并合并
        updated = self._parse_chapter_outline_response(response, 0)

        if updated and "chapters" in updated and not updated.get("parse_error"):
            all_chapters = self.get_chapter_outlines()
            updated_chapters = updated.get("chapters", [])

            for updated_ch in updated_chapters:
                if not isinstance(updated_ch, dict):
                    continue
                ch_id = updated_ch.get("chapter_id", 0)
                found = False
                for i, existing_ch in enumerate(all_chapters):
                    if isinstance(existing_ch, dict) and existing_ch.get("chapter_id") == ch_id:
                        all_chapters[i] = updated_ch
                        found = True
                        break
                if not found:
                    all_chapters.append(updated_ch)

            # 重新排序保存
            all_chapters.sort(key=lambda x: x.get("chapter_id", 0) if isinstance(x, dict) else 0)
            save_yaml(self.paths.chapter_outline, {"chapters": all_chapters})
            try:
                from .narrative_guard_agent import NarrativeGuardAgent
                NarrativeGuardAgent(self.paths).audit_all_outlines()
            except Exception as e:
                print(f"  [警告] 追读质检失败: {e}")

        # 保存增量更新日志
        update_log = (
            "# 大纲增量更新日志\n\n"
            + "## 更新时间\n"
            + "触发章节: 第 " + str(from_chapter) + " 章之前\n\n"
            + "## LLM 响应\n" + response + "\n\n"
            + "## 影响的章节\n```yaml\n"
            + yaml.dump(updated.get("chapters", []) if updated else [], allow_unicode=True)
            + "\n```\n"
        )
        log_path = self.paths.story_dir / "outline_update_log.md"
        save_markdown(log_path, update_log)

        return response

    def _build_compact_book_prompt(self, book_outline_prompt: str, ctx: dict, rules: str, commercial_rules: str) -> str:
        return f"""{self.runtime.truncate_text(book_outline_prompt, 2200)}

## 写作规则摘要
{self.runtime.compress_text(rules, 1800, "写作规则")}

## 商业追读规则摘要
{self.runtime.compress_text(commercial_rules, 1800, "商业规则")}

## 当前小说设定
```yaml
{yaml.dump(ctx, allow_unicode=True, default_flow_style=False)}
```

请生成全书大纲，保留卷结构、主线欲望、核心阻碍、情绪兑现、长线伏笔和节奏风险。"""

    def _build_compact_chapter_prompt(
        self,
        chapter_outline_prompt: str,
        ctx: dict,
        master_outline: str,
        volume: int,
        rules: str,
        commercial_rules: str,
    ) -> str:
        return f"""{self.runtime.truncate_text(chapter_outline_prompt, 2200)}

## 写作规则摘要
{self.runtime.compress_text(rules, 1400, "写作规则")}

## 商业追读规则摘要
{self.runtime.compress_text(commercial_rules, 1400, "商业规则")}

## 小说设定
```yaml
{yaml.dump(ctx, allow_unicode=True, default_flow_style=False)}
```

## 全书大纲摘要
{self.runtime.truncate_text(master_outline, 4200)}

请为第 {volume} 卷生成章节大纲。输出 YAML。"""

    def _build_compact_update_prompt(
        self,
        outline_update_prompt: str,
        ctx: dict,
        from_chapter: int,
        written_info: list[str],
        unwritten: list[dict],
        rules: str,
        commercial_rules: str,
    ) -> str:
        return (
            self.runtime.truncate_text(outline_update_prompt, 1600)
            + "\n\n## 写作规则摘要\n" + self.runtime.compress_text(rules, 1000, "写作规则")
            + "\n\n## 商业追读规则摘要\n" + self.runtime.compress_text(commercial_rules, 1000, "商业规则")
            + "\n\n## 小说设定摘要\n```yaml\n"
            + yaml.dump(ctx, allow_unicode=True, default_flow_style=False)
            + "\n```\n\n## 最近已写章节反馈\n"
            + self.runtime.truncate_text("\n".join(written_info), 3500)
            + "\n\n## 后续大纲关键字段\n```yaml\n"
            + yaml.dump(self.runtime.compact_yaml_items(unwritten, max_items=8), allow_unicode=True)
            + "\n```\n\n请从第 "
            + str(from_chapter)
            + " 章起，只输出需要调整的章节 YAML。"
        )

    def get_chapter_outlines(self) -> list:
        """获取所有章节大纲。"""
        data = load_yaml(self.paths.chapter_outline)
        if data and "chapters" in data:
            return data["chapters"]
        return []

    def get_chapter_outline(self, chapter_id: int) -> dict:
        """获取指定章节大纲。"""
        chapters = self.get_chapter_outlines()
        for ch in chapters:
            if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                return ch
        return {}

    def get_status(self) -> dict:
        """获取大纲模块状态。"""
        return {
            "master_outline": self.paths.master_outline.exists(),
            "chapter_outline": self.paths.chapter_outline.exists(),
        }

