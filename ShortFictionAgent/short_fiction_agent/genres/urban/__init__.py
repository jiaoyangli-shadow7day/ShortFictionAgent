"""都市题材包(都市言情 / 都市逆袭, 可含重生设定)。"""

from pathlib import Path
from ..base import GenrePack
from .lexicons import URBAN_LEXICONS

_PROMPTS_DIR = str(Path(__file__).resolve().parent / "prompts")

URBAN_PACK = GenrePack(
    genre_id="urban",
    display_name="都市",
    channel="male",
    lexicons=URBAN_LEXICONS,
    # 都市仍有一条感情线(DataAgent 仍抽关系态), 复用关系三轴; 跳变阈值放宽一点,
    # 因为都市节奏更快、关系推进允许更大跨度。
    progression_axes=["romance_level", "trust_level", "conflict_level"],
    relationship_jump_threshold=14,
    character_roles=["protagonist_male", "protagonist_female"],
    prompts_dir=_PROMPTS_DIR,
    rules_filename="urban_rules.md",
    system_persona="你是一名擅长中国都市小说(都市言情/都市逆袭, 可含重生设定)的作家和创作助手。",
    scaffolding={
        "novel": {
            "genre": "都市",
            "tone": "节奏明快、有爽感但不悬浮、克制处见真实",
            "pace": "中等偏快，事件驱动，定期给爽点兑现",
            "perspective": "第三人称有限视角",
        },
        "style": {
            "prohibited_expressions": [
                "命运的齿轮开始转动", "他不知道的是", "霸道总裁爱上我",
                "众人哗然", "全场震惊", "不可一世", "嚣张至极",
            ],
            "required_elements": [
                "具体的职场/商战/家族细节", "成年人的利益与算计", "动作与对白而非心理总结",
                "爽点要有铺垫、围观与后果，不是凭空打脸",
            ],
        },
    },
    genre_aliases=("都市", "都市文", "都市言情", "都市逆袭", "现代都市", "重生都市", "urban"),
)

