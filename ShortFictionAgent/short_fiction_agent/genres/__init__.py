"""题材包层：把"题材知识"从核心引擎里分离出来。

核心引擎只依赖 GenrePack 抽象; 具体题材(校园/都市/玄幻/历史…)各提供一个
GenrePack 并在 registry 注册。加题材不需要改引擎代码。
"""

from .base import GenrePack
from .registry import (
    get_genre_pack,
    register,
    resolve_pack_by_genre_string,
    resolve_project_pack,
    DEFAULT_GENRE_ID,
)

__all__ = [
    "GenrePack",
    "get_genre_pack",
    "register",
    "resolve_pack_by_genre_string",
    "resolve_project_pack",
    "DEFAULT_GENRE_ID",
]

