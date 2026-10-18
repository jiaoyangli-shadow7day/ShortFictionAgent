"""ContextAgent - 写作前上下文收集与任务书生成。"""

import yaml
from pathlib import Path
from ..config import save_markdown, load_markdown, load_yaml, load_json
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class ContextAgent:
    """写作前准备：收集上下文，生成写作任务书。

    上下文策略（精妙分层，不牺牲质量）：
    - WriterAgent 必须看到: 当前章纲(全)、角色设定(全)、关系状态(全)、
      最近2章摘要(全)、活跃伏笔(10条)、规则(2K/1.5K)
    - 随着章节推进，从最旧的信息开始裁剪，保护当前章所需信息
    """

    TARGET_PROMPT_TOKENS = 28000
    # 分层规则长度
    RULES_MAX_CHARS = 2500        # 接近 v0.5 的 3000，保留写作规则质量
    COMMERCIAL_RULES_MAX = 2000   # 接近 v0.5 的 2500，保留结构指导

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

    def gather_context(self, chapter_id: int) -> dict:
        """收集写第 chapter_id 章所需的所有上下文。"""
        context = {}

        # 1. 小说梗概
        context["premise"] = load_markdown(self.paths.premise)

        # 2. 全书大纲
        context["master_outline"] = load_markdown(self.paths.master_outline)

        # 3. 当前章节大纲
        chapter_outlines = load_yaml(self.paths.chapter_outline)
        current_outline = {}
        if chapter_outlines and "chapters" in chapter_outlines:
            for ch in chapter_outlines["chapters"]:
                if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                    current_outline = ch
                    break
        context["chapter_outline"] = current_outline

        # 4. 男女主设定
        context["protagonist_male"] = load_yaml(self.paths.protagonist_male)
        context["protagonist_female"] = load_yaml(self.paths.protagonist_female)

        # 5. 配角设定
        context["supporting_characters"] = load_yaml(self.paths.supporting_characters)

        # 6. 当前关系状态
        context["relationship_state"] = self.memory.load_relationship_state()

        # 7. 滚动上下文（最近 3 章完整摘要 + 更早章节的卷摘要）
        context["recent_summaries"] = self.memory.get_context_for_chapter(
            chapter_id, recent_n=3, volume_interval=10
        )

        # 8. 相关事件（分层检索：近期详情 + 早期压缩摘要，控制 Token 膨胀）
        context["events"] = self.memory.get_events_for_context(chapter_id, recent_n=3, max_items=25)

        # 9. 活跃伏笔（按重要性过滤，最多 10 条）
        context["active_foreshadowing"] = self.memory.get_foreshadowing_for_context(chapter_id, max_items=10)

        # 10. 校园设定
        context["school_setting"] = load_yaml(self.paths.school_setting)

        # 11. 风格规则
        context["style_config"] = load_yaml(self.paths.style_config)

        # 12. 写作规则
        context["rules"] = self._get_rules()

        # 13. 商业追读规则
        context["commercial_rules"] = self._get_commercial_rules()

        return context

    def _collect_revision_focus(self, chapter_id: int) -> str:
        """若本章已存在审查/门禁/现实/一致性反馈，则汇总为"返修要点"。

        首写时这些文件都不存在 → 返回空串, 任务书照常生成。
        返修(回补/重写)时存在 → 任务书会显式带上要解决的问题,
        避免"返修只是再生成一次一模一样的任务书"。
        """
        sources = [
            ("审查", load_markdown(self.paths.review_file(chapter_id))),
            ("商业门禁", load_markdown(self.paths.commercial_report_file(chapter_id))),
            ("追读质检", load_markdown(self.paths.narrative_guard_file(chapter_id))),
            ("现实逻辑", load_markdown(self.paths.reality_report_file(chapter_id))),
            ("一致性", load_markdown(self.paths.consistency_report_file())),
            ("修订计划", load_markdown(self.paths.revision_file(chapter_id))),
        ]
        parts = []
        for label, content in sources:
            if content and content.strip():
                parts.append(f"### {label}\n{self.runtime.compress_text(content, 700, label)}")
        if not parts:
            return ""
        return self.runtime.compress_text("\n\n".join(parts), 1800, "返修要点")

    def generate_writing_task(self, chapter_id: int) -> str:
        """生成写作任务书（自适应截断上下文以控制 Token 预算）。"""
        context = self.gather_context(chapter_id)
        context_prompt = self._get_prompt("context_prompt.md")
        revision_focus = self._collect_revision_focus(chapter_id)

        # 自适应截断长文本
        master_outline = context.get("master_outline", "")
        # 只保留当前章节所在卷的大纲段落（大幅减少 Token）
        volume_outline = self._extract_volume_section(master_outline, chapter_id)
        premise = self._truncate_text(context.get("premise", ""), 500)
        rules = self.runtime.compress_text(context.get("rules", ""), self.RULES_MAX_CHARS, "写作规则")
        commercial_rules = self.runtime.compress_text(context.get("commercial_rules", ""), self.COMMERCIAL_RULES_MAX, "商业规则")

        # 精简角色设定
        male = context.get("protagonist_male", {})
        female = context.get("protagonist_female", {})
        male_brief = {k: male.get(k, "") for k in ["name", "personality", "speaking_style", "forbidden_behaviors"] if k in male}
        female_brief = {k: female.get(k, "") for k in ["name", "personality", "speaking_style", "forbidden_behaviors"] if k in female}

        full_prompt = f"""{self._truncate_text(context_prompt, 2500)}

## 当前小说上下文

### 梗概
{premise}

### 当前卷大纲（节选）
{volume_outline}

### 当前章节大纲
```yaml
{yaml.dump(context.get("chapter_outline", {}), allow_unicode=True)}
```

### 男女主设定
男主：
```yaml
{yaml.dump(male_brief, allow_unicode=True)}
```

女主：
```yaml
{yaml.dump(female_brief, allow_unicode=True)}
```

### 当前关系状态
```yaml
{yaml.dump(context.get("relationship_state", {}), allow_unicode=True)}
```

### 最近章节摘要
```json
{yaml.dump(context.get("recent_summaries", []), allow_unicode=True)}
```

### 活跃伏笔
{yaml.dump(context.get("active_foreshadowing", []), allow_unicode=True)}

### 写作规则
{rules}

### 商业追读叙事规则
{commercial_rules}
{(f'''
### ⚠️ 本章为返修重写——必须解决以下既有问题
{revision_focus}

请在任务书中针对上述问题给出具体、可执行的修改方向, 不要照抄首写思路。''' if revision_focus else '')}

请为第 {chapter_id} 章生成写作任务书。"""

        full_prompt = self._ensure_prompt_budget(full_prompt, chapter_id)

        policy = AgentCallPolicy(
            agent_name="ContextAgent",
            task_type="context",
            target_prompt_tokens=self.TARGET_PROMPT_TOKENS,
            min_response_chars=300,
            # 模型由集中式路由表决定(context=routine→flash, 可在 model.yaml 覆盖)
            allow_fallback=True,
        )
        response = self.runtime.call(full_prompt, policy)
        if not self._is_valid_task_response(response):
            raise RuntimeError(f"写作任务书生成失败或响应不完整: {response[:160]}")

        # 保存写作任务书
        task_path = self.paths.writing_task_file(chapter_id)
        save_markdown(task_path, response)

        return response

    def generate_writing_task_deterministic(self, chapter_id: int) -> str:
        """不调用 LLM, 直接从章纲 + 关系状态 + 返修要点拼出写作任务书。

        用于 A/B 对照"ContextAgent 这一跳是否值得": WriterAgent 本来就直接读章纲/
        关系/摘要/伏笔, 任务书很大程度是把章纲 LLM 改写了一遍。确定性版本去掉这次
        LLM 调用(省一次 flash + 一处漂移点), 把章纲字段结构化列给 writer。
        """
        chapter_outlines = load_yaml(self.paths.chapter_outline)
        outline = {}
        for ch in (chapter_outlines.get("chapters", []) if isinstance(chapter_outlines, dict) else []):
            if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                outline = ch
                break
        rel = self.memory.load_relationship_state() or {}
        revision_focus = self._collect_revision_focus(chapter_id)

        def line(label, key):
            v = outline.get(key)
            return f"- {label}: {v}" if v not in (None, "", [], {}) else ""

        parts = [
            f"# 写作任务书 - 第 {chapter_id} 章（确定性）",
            f"\n## 本章故事链（必须兑现）",
            line("读者入口钩子", "reader_hook"),
            line("故事问题", "story_question"),
            line("人物欲望", "character_desire"),
            line("主事件", "main_event"),
            line("阻碍", "obstacle"),
            line("行动", "action"),
            line("代价", "cost"),
            line("情绪推进", "emotion_progress"),
            line("关系转向", "relationship_turn"),
            line("不可逆变化", "irreversible_change"),
            line("名场面", "viral_scene"),
            line("结尾钩子", "ending_hook"),
            line("下一章追问", "next_chapter_question"),
            f"\n## 当前关系阶段\n- {rel.get('relationship_stage', '未知')}",
            f"\n## 本章禁止事项\n" + "\n".join(f"- {x}" for x in outline.get("forbidden_items", []) or ["（无）"]),
        ]
        if revision_focus:
            parts.append(f"\n## ⚠️ 本章为返修重写——必须解决以下既有问题\n{revision_focus}")
        task = "\n".join(p for p in parts if p)
        save_markdown(self.paths.writing_task_file(chapter_id), task)
        return task

    def _extract_volume_section(self, master_outline: str, chapter_id: int) -> str:
        """从全书大纲中提取当前卷相关的段落。"""
        if not master_outline:
            return ""
        # 根据 chapter_id 推断卷号
        if chapter_id <= 10:
            vol_marker = "卷一"
        elif chapter_id <= 27:
            vol_marker = "卷二"
        elif chapter_id <= 50:
            vol_marker = "卷三"
        else:
            vol_marker = "卷四"

        # 查找卷标记
        vol_start = master_outline.find(f"# {vol_marker}")
        if vol_start < 0:
            return master_outline[:3000]  # fallback: 前3000字

        # 查找下一卷标记
        next_vol_start = len(master_outline)
        for marker in ["# 卷二", "# 卷三", "# 卷四"]:
            pos = master_outline.find(marker, vol_start + 10)
            if pos > 0 and pos < next_vol_start:
                next_vol_start = pos

        section = master_outline[vol_start:next_vol_start]
        return self._truncate_text(section, 5000)

    def _ensure_prompt_budget(self, prompt: str, chapter_id: int) -> str:
        """最终兜底：确保任务书 Prompt 不超过固定预算。"""
        target = min(getattr(self.llm, "max_prompt_tokens", self.TARGET_PROMPT_TOKENS) * 0.75, self.TARGET_PROMPT_TOKENS)
        if self._estimate_tokens(prompt) <= target:
            return prompt

        marker = "\n\n[系统提示：为避免上下文过长，中间背景材料已压缩；请以当前章节大纲和最近上下文为准。]\n\n"
        head_chars = 1800
        tail_chars = 14000
        fitted = prompt[:head_chars].rstrip() + marker + prompt[-tail_chars:].lstrip()
        while self._estimate_tokens(fitted) > target and tail_chars > 3000:
            tail_chars = int(tail_chars * 0.8)
            fitted = prompt[:head_chars].rstrip() + marker + prompt[-tail_chars:].lstrip()
        if self._estimate_tokens(fitted) > target:
            raise RuntimeError(
                f"写作任务书 Prompt 超过预算，已压缩仍为 {self._estimate_tokens(fitted)}/{int(target)} tokens"
            )
        return fitted

    def _truncate_text(self, text: str, max_chars: int) -> str:
        from ..context_budget import truncate_text
        return truncate_text(text, max_chars, suffix="\n\n（内容已截断以适应上下文窗口）")

    def _estimate_tokens(self, text: str) -> int:
        from ..context_budget import estimate_tokens
        return estimate_tokens(text)

    def _is_valid_task_response(self, response: str) -> bool:
        if not response:
            return False
        stripped = response.strip()
        if stripped.startswith("[API 调用失败]") or stripped.startswith("[错误]"):
            return False
        if "Response ended prematurely" in stripped:
            return False
        return len(stripped) >= 300 and self._estimate_tokens(stripped) >= 150

    def get_writing_task(self, chapter_id: int) -> str:
        """获取已有写作任务书。"""
        task_path = self.paths.writing_task_file(chapter_id)
        return load_markdown(task_path)

