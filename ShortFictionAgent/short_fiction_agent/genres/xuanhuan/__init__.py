"""玄幻题材包（男频·单主角力量流，可含重生/系统/金手指）。"""

from pathlib import Path
from ..base import GenrePack
from .lexicons import XUANHUAN_LEXICONS

_PROMPTS_DIR = str(Path(__file__).resolve().parent / "prompts")

XUANHUAN_PACK = GenrePack(
    genre_id="xuanhuan",
    display_name="玄幻",
    channel="male",
    lexicons=XUANHUAN_LEXICONS,
    # 玄幻的进度是力量/境界, 不是恋爱; 感情线弱, 跳变阈值放宽。
    progression_axes=["realm_level", "combat_power"],
    relationship_jump_threshold=20,
    # 单主角(可有道侣/红颜, 但主线是主角变强), 不写死男女主。
    character_roles=["protagonist"],
    prompts_dir=_PROMPTS_DIR,
    rules_filename="xuanhuan_rules.md",
    system_persona="你是一名擅长中国玄幻小说(单主角力量流, 可含重生/系统/金手指)的作家和创作助手。",
    scaffolding={
        "novel": {
            "genre": "玄幻",
            "tone": "热血、爽快、有压迫感, 但金手指有代价、不降智平推",
            "pace": "事件驱动、节奏明快, 周期性兑现突破/打脸/夺宝爽点",
            "perspective": "第三人称有限视角",
        },
        "style": {
            "prohibited_expressions": [
                "命运的齿轮开始转动", "全场哗然", "不可一世", "嚣张至极",
                "天才中的天才", "震惊所有人",
            ],
            "required_elements": [
                "清晰的力量/境界体系与边界", "变强/复仇/护道的外部目标线",
                "爽点要有铺垫、围观与代价, 不是凭空打脸", "动作与对白而非心理总结",
            ],
        },
    },
    genre_aliases=("玄幻", "玄幻小说", "xuanhuan", "东方玄幻", "异世大陆"),
)

