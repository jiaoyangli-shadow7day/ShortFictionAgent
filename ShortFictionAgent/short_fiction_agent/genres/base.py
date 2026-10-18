"""GenrePack：题材包抽象。

核心引擎(管线/记忆/harness/质检算法)与"题材知识"在这里分层。
引擎只懂**算法**(数关键词、算硬度、查重复、判跳变); 而"哪些词算硬事件、
哪些意象算重复、关系线有哪些轴、主角有几个、写作规则/世界观长什么样"这些
**题材知识**全部装进 GenrePack。换题材 = 换一个 GenrePack, 引擎代码一行不动。

一个 GenrePack 提供:
- lexicons: 一组命名关键词表, 供质检分析器按 key 取用;
- progression: 进度引擎配置(关系/力量/复仇线的数值轴 + 跳变阈值);
- character_roles: 角色槽位(双主角 / 单主角金手指 / 群像);
- prompts: 题材专属 prompt 目录 + 写作规则文件名(在题材目录里找不到的 prompt
  自动回退到共享 prompts/);
- scaffolding: init 新项目时的题材默认配置(novel.yaml / style.yaml)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _shared_prompts_dir() -> Path:
    # base.py 在 short_fiction_agent/genres/ 下, 共享 prompts 在 short_fiction_agent/prompts/
    return Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class GenrePack:
    genre_id: str
    display_name: str
    # 频道: "male"(男频) / "female"(女频) / ""(通用), 对应番茄/起点的男女频分类。
    channel: str = ""
    # 质检关键词表: key -> 关键词列表。分析器只认 key, 不认具体词。
    lexicons: dict = field(default_factory=dict)
    # 进度引擎: 数值轴 + 单步跳变阈值(超过即疑似"原地起飞")。
    progression_axes: list = field(default_factory=lambda: ["romance_level", "trust_level", "conflict_level"])
    relationship_jump_threshold: int = 8
    # 角色槽位(决定 characters/*.yaml 的固定文件名与数量)。
    character_roles: list = field(default_factory=lambda: ["protagonist_male", "protagonist_female"])
    # 题材专属 prompt 目录(绝对路径); None=只用共享 prompts/。
    prompts_dir: str | None = None
    templates_dirname: str = "templates"
    # 写作规则文件名(题材"圣经"), 经 read_prompt 解析(题材目录优先, 回退共享)。
    # 默认是题材无关的通用规则; 各题材包应覆盖为自己的规则文件。
    rules_filename: str = "writing_rules.md"
    # LLM system 角色设定(题材语气定位)。
    system_persona: str = "你是一名专业的中文长篇小说作家和创作助手。"
    # init 新项目时的题材默认脚手架(novel.yaml / style.yaml 字段)。
    scaffolding: dict = field(default_factory=dict)
    # 该题材可匹配的 novel.yaml genre 字符串(用于从配置解析题材)。
    genre_aliases: tuple = ()

    def lex(self, key: str, default=None) -> list:
        """取一个命名关键词表; 缺失时返回 default 或空表。"""
        value = self.lexicons.get(key)
        if value is None:
            return list(default) if default is not None else []
        return list(value)

    def read_prompt(self, name: str) -> str:
        """读取一个 prompt 文件: 题材目录优先, 找不到回退共享 prompts/。"""
        if self.prompts_dir:
            p = Path(self.prompts_dir) / name
            if p.exists():
                return p.read_text(encoding="utf-8")
        shared = _shared_prompts_dir() / name
        return shared.read_text(encoding="utf-8") if shared.exists() else ""

    def rules_text(self) -> str:
        """题材写作规则正文。"""
        return self.read_prompt(self.rules_filename)

