"""青春校园恋爱题材包。"""

from pathlib import Path
from ..base import GenrePack
from .lexicons import CAMPUS_LEXICONS

_PROMPTS_DIR = str(Path(__file__).resolve().parent / "prompts")

CAMPUS_PACK = GenrePack(
    genre_id="campus_romance",
    display_name="青春校园恋爱",
    lexicons=CAMPUS_LEXICONS,
    progression_axes=["romance_level", "trust_level", "conflict_level"],
    relationship_jump_threshold=10,
    character_roles=["protagonist_male", "protagonist_female"],
    prompts_dir=_PROMPTS_DIR,  # 校园题材专属 prompt(校园味的 review/reality/outline 收进题材包)
    rules_filename="campus_love_rules.md",
    system_persona="你是一名擅长中国青春校园恋爱小说的作家和创作助手。",
    scaffolding={
        "novel": {
            "genre": "青春校园恋爱",
            "tone": "克制、清爽、真实",
            "pace": "中等偏慢，注重情绪积累",
            "perspective": "第三人称有限视角",
        },
        "style": {
            "prohibited_expressions": [
                "命运的齿轮开始转动", "他不知道的是", "少年少女", "青春的气息",
                "心像小鹿乱撞", "一眼万年", "命中注定",
            ],
            "required_elements": [
                "具体校园生活细节", "动作而非心理总结", "对话而非旁白解释",
                "场景感官细节（声音、光线、气味）",
            ],
        },
    },
    channel="female",
    genre_aliases=("青春校园恋爱", "校园", "校园恋爱", "浪漫青春", "青春", "campus_romance", "campus"),
)

