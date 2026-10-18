"""WriterAgent - 负责生成章节正文。"""

import yaml
import re
from pathlib import Path
from ..config import save_markdown, load_markdown, load_yaml
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class WriterAgent:
    """章节写作者 - 根据任务书生成正文。"""

    MIN_CHAPTER_CHARS = 500
    TARGET_PROMPT_TOKENS = 30000

    # ── 后处理：自动清洗 LLM 输出中的 Markdown + AI 穿帮痕迹 ──
    # 这些清洗是投稿番茄小说等平台的硬性要求，LLM 可能不遵守，代码强制保证。
    _MD_HEADING = re.compile(r'^#\s+', re.MULTILINE)
    _MD_HR = re.compile(r'^\s*---\s*$', re.MULTILINE)
    _MD_BOLD = re.compile(r'\*\*(.+?)\*\*')
    _MD_ITALIC = re.compile(r'(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)')

    # AI 穿帮痕迹 — 投稿后会被读者认出是 AI 写的
    _AI_NEXT_PREVIEW = re.compile(
        r'^(下章预告|下一章问题|下章问题|下章预告)[：:]\s*$.*?(?=\n\n|\Z)',
        re.MULTILINE | re.DOTALL
    )
    _AI_BRACKET_PREVIEW = re.compile(r'^【下一章[：:].*?】\s*$', re.MULTILINE)
    _AI_CHAPTER_END = re.compile(r'^（?第[一二三四五六七八九十百千\d]+章\s*完）?\s*$', re.MULTILINE)

    @classmethod
    def _sanitize_chapter(cls, text: str) -> str:
        """去除 LLM 输出中所有不可投稿的格式和 AI 痕迹。

        包括：Markdown 标记、下章预告、下一章问题、第X章完 等。
        番茄小说等投稿平台不允许这些内容。
        """
        # Markdown 格式
        text = cls._MD_HEADING.sub('', text)
        text = cls._MD_HR.sub('', text)
        text = cls._MD_BOLD.sub(r'\1', text)
        text = cls._MD_ITALIC.sub(r'\1', text)
        # AI 穿帮痕迹
        text = cls._AI_NEXT_PREVIEW.sub('', text)
        text = cls._AI_BRACKET_PREVIEW.sub('', text)
        text = cls._AI_CHAPTER_END.sub('', text)
        # 清理多余空行（AI 预告块删除后可能残留 3+ 连续空行）
        text = re.sub(r'\n{4,}', '\n\n\n', text)
        return text.strip() + '\n'

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

    def write_chapter(self, chapter_id: int) -> str:
        """编写指定章节（含自适应 Prompt 截断以控制 Token 预算）。"""
        paths = self.paths

        # 章节大纲
        chapter_outlines = load_yaml(paths.chapter_outline)
        current_outline = {}
        if chapter_outlines and "chapters" in chapter_outlines:
            for ch in chapter_outlines["chapters"]:
                if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                    current_outline = ch
                    break

        # 写作任务书
        writing_task = load_markdown(paths.writing_task_file(chapter_id))

        # 关系状态
        relationship_state = self.memory.load_relationship_state()

        # 滚动上下文（最近 3 章完整摘要 + 更早章节的卷摘要）
        recent_summaries = self.memory.get_context_for_chapter(
            chapter_id, recent_n=3, volume_interval=10
        )

        # 活跃伏笔（按重要性过滤，最多 10 条）
        active_foreshadowing = self.memory.get_foreshadowing_for_context(chapter_id, max_items=10)

        # 角色设定: 按题材包定义的角色槽位加载(校园=双主角, 其它题材可单主角/群像)
        characters = {role: load_yaml(paths.character_file(role))
                      for role in self.pack.character_roles}
        # 校园兼容: 若题材含 protagonist_male/female 角色, 仍单独取出供老占位符使用
        male = characters.get("protagonist_male") or load_yaml(paths.protagonist_male)
        female = characters.get("protagonist_female") or load_yaml(paths.protagonist_female)

        # 世界/背景设定(校园=学校, 都市=公司/城市, 玄幻=门派…)
        school = load_yaml(paths.world_setting_file)

        # Prompt 模板
        write_prompt = self._get_prompt("write_chapter_prompt.md")
        rules = self._get_rules()
        commercial_rules = self._get_commercial_rules()

        # 所有审核反馈（确保重写时全部审查意见进入上下文）
        revision_plan = load_markdown(paths.revision_file(chapter_id))
        review_report = load_markdown(paths.review_file(chapter_id))
        narrative_report = load_markdown(paths.narrative_guard_file(chapter_id))
        commercial_report = load_markdown(paths.commercial_report_file(chapter_id))
        # 现实逻辑 / 一致性报告之前只打印不消费, 导致按这两类问题返修的章节
        # 重写时根本不知道错在哪。这里把它们接入写作上下文(信号链打通)。
        reality_report = load_markdown(paths.reality_report_file(chapter_id))
        consistency_report = load_markdown(paths.consistency_report_file())

        # 阶段门禁报告（如果本章处于门禁返修范围，注入门禁意见）
        gate_feedback_parts = []
        # Opening10Gate (chapter 10)
        opening10 = load_markdown(paths.reviews_dir / "opening10_submission_gate.md")
        if opening10 and chapter_id <= 10:
            gate_feedback_parts.append(f"## 前十章门禁\n{self.runtime.compress_text(opening10, 1200, '前十章门禁')}")

        # Rolling gate (every 5 chapters)
        for gate_ch in range(5, chapter_id + 1, 5):
            rolling = load_markdown(paths.reviews_dir / f"rolling_continuity_{gate_ch-4:03d}_{gate_ch:03d}.md")
            if rolling and chapter_id <= gate_ch:
                gate_feedback_parts.append(f"## 滚动门禁(1-{gate_ch})\n{self.runtime.compress_text(rolling, 800, '滚动门禁')}")
                break

        # Payoff gate (every 5 chapters)
        for gate_ch in range(5, chapter_id + 1, 5):
            payoff = load_markdown(paths.reviews_dir / f"payoff_ledger_gate_{gate_ch:03d}.md")
            if payoff and chapter_id <= gate_ch:
                gate_feedback_parts.append(f"## 兑现门禁\n{self.runtime.compress_text(payoff, 800, '兑现门禁')}")
                break

        # ArcCheckpoint gate (every 30 chapters — 卷级)
        for arc_ch in [30]:
            arc = load_markdown(paths.reviews_dir / f"arc_checkpoint_{arc_ch-29:03d}_{arc_ch:03d}.md")
            if arc and chapter_id <= arc_ch:
                gate_feedback_parts.append(f"## 卷级门禁\n{self.runtime.compress_text(arc, 1000, '卷级门禁')}")

        # Final submission gate (全书)
        final = load_markdown(paths.reviews_dir / "final_submission_gate.md")
        if final:
            gate_feedback_parts.append(f"## 全书终稿门禁\n{self.runtime.compress_text(final, 1200, '终稿门禁')}")

        # 如果本章处于门禁返修范围，生成章节级指导
        targeted_gate_guidance = ""
        if gate_feedback_parts:
            combined_gates = "\n".join(gate_feedback_parts)
            targeted_gate_guidance = self._build_gate_chapter_guidance(
                chapter_id, combined_gates, current_outline
            )

        all_feedback = "\n\n".join(filter(None, [
            f"## 修订计划\n{revision_plan}" if revision_plan else "",
            f"## 审查报告\n{self.runtime.compress_text(review_report, 1500, '审查报告')}" if review_report else "",
            f"## 追读质检\n{self.runtime.compress_text(narrative_report, 1000, '追读报告')}" if narrative_report else "",
            f"## 商业门禁\n{self.runtime.compress_text(commercial_report, 1000, '商业报告')}" if commercial_report else "",
            f"## 现实逻辑审核\n{self.runtime.compress_text(reality_report, 800, '现实逻辑报告')}" if reality_report else "",
            f"## 一致性检查\n{self.runtime.compress_text(consistency_report, 800, '一致性报告')}" if consistency_report else "",
            f"## ⚠️ 阶段门禁返修指导（本章专属）\n{targeted_gate_guidance}" if targeted_gate_guidance else "",
        ]))

        # 提取禁止事项
        forbidden = current_outline.get("forbidden_items", [])
        relationship = relationship_state if relationship_state else {}

        # 构建 prompt，自适应截断以控制 Token 预算
        full_prompt = self._build_prompt_with_budget(
            write_prompt=write_prompt,
            rules=rules,
            commercial_rules=commercial_rules,
            relationship=relationship,
            forbidden=forbidden,
            current_outline=current_outline,
            writing_task=writing_task,
            recent_summaries=recent_summaries,
            active_foreshadowing=active_foreshadowing,
            characters=characters,
            male=male,
            female=female,
            school=school,
            chapter_id=chapter_id,
            all_feedback=all_feedback if all_feedback else "（首次写作，无审核反馈）",
        )

        from ..longform import validate_chapter_plan
        reader_contract = current_outline.get("reader_contract")
        if reader_contract is not None:
            validate_chapter_plan(current_outline)
            previous_path = paths.chapter_file(chapter_id - 1)
            previous_tail = previous_path.read_text(encoding="utf-8")[-2400:] if previous_path.exists() else "首章"
            full_prompt += "\n## 前章结尾（直接承接，不能重置已经发生的事）\n" + previous_tail
            full_prompt += "\n## 本章读者回报合同\n" + yaml.dump(reader_contract, allow_unicode=True)
            full_prompt += ("\n目标约2200个非空白字符，允许1800—2800；对话统一使用中文双引号“”。"
                            "人物须采取有代价的行动，带来可见变化和本章情绪回报。"
                            "不要复述前章，不能连续靠相同误会拖延关系。日常描写必须推进人物选择。"
                            "章末给出下一步具体期待，禁止突然切断已开始的高潮冒充钩子。")

        policy = AgentCallPolicy(
            agent_name="WriterAgent",
            task_type="write_chapter",
            target_prompt_tokens=self.TARGET_PROMPT_TOKENS,
            min_response_chars=self.MIN_CHAPTER_CHARS,
            quality_tier="critical",  # 路由表: write_chapter=critical→primary(pro)
            allow_fallback=True,
        )
        response = self.runtime.call(full_prompt, policy)
        response = self._sanitize_chapter(response)
        valid, reason = self.validate_chapter_content(response)
        if not valid:
            raise RuntimeError(f"章节生成响应无效: {reason}; 内容片段: {str(response)[:160]}")

        if reader_contract is not None:
            visible_chars = len("".join(response.split()))
            if not 1800 <= visible_chars <= 2800:
                draft = paths.revisions_dir / f"chapter_{chapter_id:03d}_length_rejected.md"
                save_markdown(draft, response)
                raise RuntimeError(f"本章篇幅{visible_chars}超出1800—2800，原稿已保留")

        # 保存章节
        chapter_path = paths.chapter_file(chapter_id)
        save_markdown(chapter_path, response)

        # 统计字数
        word_count = self._count_chinese_chars(response)
        print(f"  第 {chapter_id} 章已生成（约 {word_count} 字）")

        return response

    def _build_prompt_with_budget(self, write_prompt, rules, commercial_rules, relationship, forbidden,
                                   current_outline, writing_task, recent_summaries,
                                   active_foreshadowing, male, female, school, chapter_id,
                                   all_feedback="", characters=None):
        """构建 Prompt，自适应截断以控制 Token 预算。"""
        # 精简角色设定（只保留写作必需字段）。
        # canon_sync_notes 是 CharacterCanonSyncAgent 的纠偏意见, 必须保留——
        # 否则这个审核 agent 的意见进不了写作上下文(信号链漏接)。
        _brief_keys = ["name", "personality", "speaking_style",
                       "forbidden_behaviors", "defense_mechanisms", "canon_sync_notes"]

        def _brief(profile):
            return {k: profile.get(k, "") for k in _brief_keys if k in (profile or {})}

        male_brief = _brief(male)
        female_brief = _brief(female)
        # 题材无关的角色块: 题材包定义的所有角色槽位(单主角/双主角/群像都支持)
        characters = characters or {}
        characters_brief = {role: _brief(profile) for role, profile in characters.items()}
        school_brief = {
            "school_name": school.get("school_name", ""),
            "location": school.get("location", ""),
            "features": school.get("features", [])[:3],
            "daily_schedule": school.get("daily_schedule", [])[:4],
        }

        # 进度/关系状态(校园=恋爱三轴, 其它题材=题材包定义的进度轴)。
        # 模板里 {{relationship_state}} 之前从未被替换, 会泄漏字面量。
        prog_keys = ["relationship_stage", "stage_description"] + list(
            getattr(self.pack, "progression_axes", ["romance_level", "trust_level", "conflict_level"])) + [
            "male_to_female", "female_to_male", "unresolved_tensions"]
        relationship_brief = {
            k: relationship.get(k) for k in prog_keys
            if relationship.get(k) not in (None, "", [], {})
        }
        rel_block = (yaml.dump(relationship_brief, allow_unicode=True)
                     if relationship_brief else "（暂无进度/关系状态记录）")

        # 构建替换映射
        replacements = {
            "{{relationship_stage}}": str(relationship.get("relationship_stage", "初始")),
            "{{relationship_state}}": rel_block,
            "{{progression_state}}": rel_block,  # 题材无关别名
            "{{forbidden_progress}}": yaml.dump(forbidden, allow_unicode=True) + "\n" +
                                       yaml.dump(relationship.get("forbidden_progress", []), allow_unicode=True),
            "{{chapter_outline}}": yaml.dump(current_outline, allow_unicode=True),
            "{{writing_task}}": writing_task,
            "{{chapter_summaries}}": yaml.dump(recent_summaries, allow_unicode=True),
            "{{foreshadowing}}": yaml.dump(active_foreshadowing, allow_unicode=True),
            "{{campus_love_rules}}": rules,          # 校园模板沿用此名
            "{{writing_rules}}": rules,              # 题材无关别名
            "{{commercial_narrative_rules}}": commercial_rules,
            "{{protagonist_male}}": yaml.dump(male_brief, allow_unicode=True),
            "{{protagonist_female}}": yaml.dump(female_brief, allow_unicode=True),
            "{{characters}}": yaml.dump(characters_brief, allow_unicode=True),  # 题材无关角色块
            "{{school_setting}}": yaml.dump(school_brief, allow_unicode=True),
            "{{world_setting}}": yaml.dump(school_brief, allow_unicode=True),    # 题材无关别名
            "{{chapter_id}}": str(chapter_id),
            "{{all_feedback}}": (self.runtime.compress_text(all_feedback, 3000, '全部审核反馈')
                if all_feedback else "（首次写作，无审核反馈）"),
        }

        # 第一遍构建
        full_prompt = write_prompt
        for key, value in replacements.items():
            full_prompt = full_prompt.replace(key, value)

        # 估算 Token 数，超预算则逐级截断
        estimated = self._estimate_tokens(full_prompt)
        target_budget = 45000  # 安全预算，留足余量给 API 响应

        if estimated > target_budget:
            # Level 1: 智能压缩写作任务书（flash 保留关键信息）
            compressed_task = self.runtime.compress_text(writing_task, 2500, "写作任务书")
            if len(compressed_task) < len(writing_task):
                replacements["{{writing_task}}"] = compressed_task
                full_prompt = write_prompt
                for key, value in replacements.items():
                    full_prompt = full_prompt.replace(key, value)
                estimated = self._estimate_tokens(full_prompt)

        if estimated > target_budget:
            # Level 2: 智能压缩章节摘要
            brief_summaries = self.memory.get_context_for_chapter(chapter_id, recent_n=2, volume_interval=10)
            replacements["{{chapter_summaries}}"] = self.runtime.compress_text(
                yaml.dump(brief_summaries, allow_unicode=True), 2000, "章节摘要"
            )
            full_prompt = write_prompt
            for key, value in replacements.items():
                full_prompt = full_prompt.replace(key, value)
            estimated = self._estimate_tokens(full_prompt)

        if estimated > target_budget:
            # Level 3: 伏笔减少到 5 条
            replacements["{{foreshadowing}}"] = yaml.dump(active_foreshadowing[:5], allow_unicode=True)
            full_prompt = write_prompt
            for key, value in replacements.items():
                full_prompt = full_prompt.replace(key, value)

        if estimated > target_budget:
            full_prompt = self._ensure_prompt_budget(full_prompt)

        return full_prompt

    def _ensure_prompt_budget(self, prompt: str) -> str:
        """最终兜底：硬性压缩正文写作 Prompt 到预算内。"""
        target = min(getattr(self.llm, "max_prompt_tokens", self.TARGET_PROMPT_TOKENS) * 0.75, self.TARGET_PROMPT_TOKENS)
        if self._estimate_tokens(prompt) <= target:
            return prompt

        marker = "\n\n[系统提示：中间上下文因预算限制已压缩；请优先遵守当前章节大纲、写作任务书和禁止事项。]\n\n"
        head_chars = 2200
        tail_chars = 16000
        fitted = prompt[:head_chars].rstrip() + marker + prompt[-tail_chars:].lstrip()
        while self._estimate_tokens(fitted) > target and tail_chars > 4000:
            tail_chars = int(tail_chars * 0.8)
            fitted = prompt[:head_chars].rstrip() + marker + prompt[-tail_chars:].lstrip()
        if self._estimate_tokens(fitted) > target:
            raise RuntimeError(
                f"正文写作 Prompt 超过预算，已压缩仍为 {self._estimate_tokens(fitted)}/{int(target)} tokens"
            )
        return fitted

    def _build_gate_chapter_guidance(
        self, chapter_id: int, gate_feedback: str, current_outline: dict
    ) -> str:
        """把门禁意见直接交给 writer 参考(确定性截断)。

        精简: 不再用 flash 把门禁意见"翻译"成本章建议(每个返修章多一次 flash +
        一个卡死点)。门禁已是 advisory, writer 是 pro, 自会判断与本章的相关性。
        """
        return self.runtime.truncate_text(gate_feedback, 600)

    @classmethod
    def validate_chapter_content(cls, content: str) -> tuple[bool, str]:
        """校验章节正文是否像一章可用内容，而不是 API 错误或截断片段。"""
        if not content:
            return False, "内容为空"
        stripped = content.strip()
        bad_prefixes = ["[API 调用失败]", "[错误]", "Response ended prematurely"]
        if any(stripped.startswith(prefix) for prefix in bad_prefixes):
            return False, "内容是 API 错误/截断标记"
        bad_markers = ["Response ended prematurely", "finish_reason=length", "context length exceeded"]
        if any(marker in stripped for marker in bad_markers):
            return False, "内容包含 API 错误/截断标记"
        chinese_chars = sum(1 for c in stripped if '一' <= c <= '鿿')
        if chinese_chars < cls.MIN_CHAPTER_CHARS:
            return False, f"中文正文过短: {chinese_chars} 字，低于 {cls.MIN_CHAPTER_CHARS} 字"
        return True, ""

    def _estimate_tokens(self, text: str) -> int:
        from ..context_budget import estimate_tokens
        return estimate_tokens(text)

    def _truncate_text(self, text: str, max_chars: int) -> str:
        from ..context_budget import truncate_text
        return truncate_text(text, max_chars, suffix="")

    def _count_chinese_chars(self, text: str) -> int:
        """统计中文字符数（不含标点和空白）。"""
        count = 0
        for char in text:
            if '一' <= char <= '鿿':
                count += 1
        return count

    def get_chapter(self, chapter_id: int) -> str:
        """读取已有章节。"""
        chapter_path = self.paths.chapter_file(chapter_id)
        return load_markdown(chapter_path)

