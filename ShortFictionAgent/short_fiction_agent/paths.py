"""项目路径管理工具。"""

from pathlib import Path
from typing import Optional

class ProjectPaths:
    """管理小说项目内的所有文件路径。"""

    def __init__(self, project_root: Path):
        self.root = Path(project_root).resolve()

    # === 配置 ===
    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def novel_config(self) -> Path:
        return self.config_dir / "novel.yaml"

    @property
    def model_config(self) -> Path:
        return self.config_dir / "model.yaml"

    @property
    def style_config(self) -> Path:
        return self.config_dir / "style.yaml"

    # === 故事 ===
    @property
    def story_dir(self) -> Path:
        return self.root / "story"

    @property
    def premise(self) -> Path:
        return self.story_dir / "premise.md"

    @property
    def master_outline(self) -> Path:
        return self.story_dir / "master_outline.md"

    @property
    def volume_outline(self) -> Path:
        return self.story_dir / "volume_outline.md"

    @property
    def chapter_outline(self) -> Path:
        return self.story_dir / "chapter_outline.yaml"

    # === 角色 ===
    @property
    def characters_dir(self) -> Path:
        return self.root / "characters"

    def character_file(self, role: str) -> Path:
        """题材无关的角色文件: characters/<role>.yaml。

        角色槽位(protagonist_male / protagonist / 群像…)由题材包的
        character_roles 定义, 骨架不再写死"男女主"。
        """
        return self.characters_dir / f"{role}.yaml"

    # —— 校园题材的角色名仍可用(向后兼容; 是校园的角色名, 非骨架假设) ——
    @property
    def protagonist_male(self) -> Path:
        return self.character_file("protagonist_male")

    @property
    def protagonist_female(self) -> Path:
        return self.character_file("protagonist_female")

    @property
    def supporting_characters(self) -> Path:
        return self.characters_dir / "supporting_characters.yaml"

    # === 关系 ===
    @property
    def relationship_dir(self) -> Path:
        return self.root / "relationship"

    @property
    def relationship_state(self) -> Path:
        return self.relationship_dir / "relationship_state.yaml"

    @property
    def romance_progress(self) -> Path:
        return self.relationship_dir / "romance_progress.yaml"

    @property
    def unresolved_tensions(self) -> Path:
        return self.relationship_dir / "unresolved_tensions.yaml"

    # === 校园 ===
    @property
    def campus_dir(self) -> Path:
        return self.root / "campus"

    @property
    def world_setting_file(self) -> Path:
        """题材无关的世界/背景设定文件(校园=学校, 都市=公司/城市, 玄幻=门派…)。

        物理上沿用 campus/school_setting.yaml 以兼容既有项目, 但骨架按此通用名访问。
        """
        return self.campus_dir / "school_setting.yaml"

    @property
    def school_setting(self) -> Path:
        return self.campus_dir / "school_setting.yaml"

    @property
    def class_schedule(self) -> Path:
        return self.campus_dir / "class_schedule.yaml"

    @property
    def campus_events(self) -> Path:
        return self.campus_dir / "campus_events.yaml"

    @property
    def exam_calendar(self) -> Path:
        return self.campus_dir / "exam_calendar.yaml"

    # === 记忆 ===
    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    @property
    def chapter_summaries(self) -> Path:
        return self.memory_dir / "chapter_summaries.json"

    @property
    def event_log(self) -> Path:
        return self.memory_dir / "event_log.json"

    @property
    def foreshadowing(self) -> Path:
        return self.memory_dir / "foreshadowing.json"

    @property
    def reusable_details(self) -> Path:
        return self.memory_dir / "reusable_details.json"

    # === 章节 ===
    @property
    def chapters_dir(self) -> Path:
        return self.root / "chapters"

    def chapter_file(self, chapter_id: int) -> Path:
        return self.chapters_dir / f"chapter_{chapter_id:03d}.md"

    # === 审查 ===
    @property
    def reviews_dir(self) -> Path:
        return self.root / "reviews"

    def review_file(self, chapter_id: int) -> Path:
        return self.reviews_dir / f"chapter_{chapter_id:03d}_review.md"

    def reality_report_file(self, chapter_id: int) -> Path:
        # 注意: 历史上 RealityAgent 写的是非零填充文件名(chapter_1_reality.md),
        # 这里集中管理并保持与历史写入格式一致, 避免读写路径不匹配。
        return self.reviews_dir / f"chapter_{chapter_id}_reality.md"

    def narrative_guard_file(self, chapter_id: int) -> Path:
        return self.reviews_dir / f"chapter_{chapter_id:03d}_narrative_guard.md"

    def commercial_report_file(self, chapter_id: int) -> Path:
        return self.reviews_dir / f"chapter_{chapter_id:03d}_commercial.md"

    # === 写作任务书 ===
    @property
    def writing_tasks_dir(self) -> Path:
        return self.root / "writing_tasks"

    def writing_task_file(self, chapter_id: int) -> Path:
        return self.writing_tasks_dir / f"chapter_{chapter_id:03d}_task.md"

    # === 修订 ===
    @property
    def revisions_dir(self) -> Path:
        return self.root / "revisions"

    def revision_file(self, chapter_id: int) -> Path:
        return self.revisions_dir / f"chapter_{chapter_id:03d}_revision_plan.md"

    # === 导出 ===
    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    # === 一致性检查 ===
    @property
    def consistency_dir(self) -> Path:
        return self.root / "consistency"

    def consistency_report_file(self) -> Path:
        return self.consistency_dir / "consistency_report.md"

    # === 可视化 ===
    @property
    def visualization_dir(self) -> Path:
        return self.root / "visualization"

    # === 对话样本 ===
    @property
    def dialogue_samples_dir(self) -> Path:
        return self.root / "dialogue_samples"

    def ensure_dirs(self):
        """确保所有目录存在。"""
        dirs = [
            self.config_dir, self.story_dir, self.characters_dir,
            self.relationship_dir, self.campus_dir, self.memory_dir,
            self.chapters_dir, self.reviews_dir, self.writing_tasks_dir,
            self.revisions_dir, self.exports_dir, self.consistency_dir,
            self.visualization_dir, self.dialogue_samples_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

def get_prompt_dir() -> Path:
    """获取 Prompt 模板目录。"""
    return Path(__file__).resolve().parent / "prompts"

