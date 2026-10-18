"""RevisionAgent - 根据审查报告生成修改计划。"""

import yaml
from pathlib import Path
from ..config import save_markdown, load_markdown
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..agent_runtime import AgentRuntime, AgentCallPolicy


class RevisionAgent:
    """章节修订计划生成器。"""

    def __init__(self, paths: ProjectPaths, llm: LLMClient, pack=None):
        from ..genres import resolve_project_pack
        self.paths = paths
        self.llm = llm
        self.pack = pack or resolve_project_pack(paths)
        self.runtime = AgentRuntime(paths, llm)

    def _get_prompt(self, name: str) -> str:
        return self.pack.read_prompt(name)

    def generate_revision_plan(self, chapter_id: int) -> str:
        """生成修改计划。"""
        # 读取章节
        chapter_content = load_markdown(self.paths.chapter_file(chapter_id))
        if not chapter_content:
            return f"[错误] 第 {chapter_id} 章不存在。"

        # 读取审查/追读/商业报告。商业门禁可能是触发修订的主因，
        # 不能只把 ReviewAgent 报告交给模型。
        review_content = load_markdown(self.paths.review_file(chapter_id))
        narrative_content = load_markdown(self.paths.narrative_guard_file(chapter_id))
        commercial_content = load_markdown(self.paths.commercial_report_file(chapter_id))
        # 现实逻辑 / 一致性问题也要进入修订计划, 否则按这两类问题返修等于白修。
        reality_content = load_markdown(self.paths.reality_report_file(chapter_id))
        consistency_content = load_markdown(self.paths.consistency_report_file())
        if not review_content:
            review_content = "（ReviewAgent 审查报告不存在，本次将主要依据追读/商业报告和原文修订。）"
        if not narrative_content:
            narrative_content = "（NarrativeGuardAgent 报告不存在。）"
        if not commercial_content:
            commercial_content = "（CommercialQualitySuite 报告不存在。）"

        revise_prompt = self._get_prompt("revise_prompt.md")

        chapter_preview = self.runtime.preview_text(chapter_content, 5000, 1800)
        review_preview = self.runtime.compress_text(review_content, 5000, "审查报告")
        narrative_preview = self.runtime.compress_text(narrative_content, 3200, "追读报告")
        commercial_preview = self.runtime.compress_text(commercial_content, 3200, "商业报告")
        extra_sections = ""
        if reality_content:
            extra_sections += f"\n\n## RealityAgent 现实逻辑审核\n{self.runtime.compress_text(reality_content, 1600, '现实逻辑报告')}"
        if consistency_content:
            extra_sections += f"\n\n## ConsistencyAgent 一致性检查\n{self.runtime.compress_text(consistency_content, 1600, '一致性报告')}"

        full_prompt = f"""{revise_prompt}

## ReviewAgent 审查报告
{review_preview}

## NarrativeGuardAgent 追读质检报告
{narrative_preview}

## CommercialQualitySuite 商业编辑报告
{commercial_preview}{extra_sections}

## 原文章节（保留开头与结尾）
```markdown
{chapter_preview}
```

请综合以上报告和原文，生成第 {chapter_id} 章的修改计划。"""

        revision_path = self.paths.revision_file(chapter_id)
        policy = AgentCallPolicy(
            agent_name="RevisionAgent",
            task_type="revise",
            target_prompt_tokens=15000,
            min_response_chars=180,
            # 模型由集中式路由表决定(revise=routine→flash, 可在 model.yaml 覆盖)。
            # 修订计划随后由 pro 的 WriterAgent 消费, 且 writer 也会读原始报告。
        )
        response = self.runtime.call(
            full_prompt,
            policy,
            compact_prompt=lambda: self._build_compact_revision_prompt(
                chapter_id,
                revise_prompt,
                chapter_content,
                review_content,
                narrative_content,
                commercial_content,
            ),
            output_path=revision_path,
        )

        return response

    def _build_compact_revision_prompt(
        self,
        chapter_id: int,
        revise_prompt: str,
        chapter_content: str,
        review_content: str,
        narrative_content: str,
        commercial_content: str,
    ) -> str:
        return f"""{self.runtime.compress_text(revise_prompt, 1500, "修订提示词")}

## 核心审查问题
{self.runtime.compress_text(review_content, 2200, "审查报告")}

## 追读/商业问题摘要
{self.runtime.compress_text(narrative_content, 1400, "追读报告")}

{self.runtime.compress_text(commercial_content, 1400, "商业报告")}

## 原文章节（紧凑版）
```markdown
{self.runtime.preview_text(chapter_content, 1800, 900)}
```

请生成第 {chapter_id} 章的可执行修改计划，优先列出：
1. 必须修的结构问题
2. 需要增强的事件/代价/关系转向
3. 需要保留的有效细节
4. 重写时的分场建议
"""

