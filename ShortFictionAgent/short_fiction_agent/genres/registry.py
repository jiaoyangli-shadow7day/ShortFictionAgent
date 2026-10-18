"""题材包注册表 + 解析。

加新题材 = 在这里 register 一个 GenrePack, 核心引擎代码无需改动。
"""

from __future__ import annotations

from .base import GenrePack
from .campus_romance import CAMPUS_PACK
from .urban import URBAN_PACK
from .xuanhuan import XUANHUAN_PACK
from .archetypes import MALE_PROGRESSION_PACK, FEMALE_ROMANCE_PACK
from .taxonomy import build_derived_packs

DEFAULT_GENRE_ID = "campus_romance"

_REGISTRY: dict[str, GenrePack] = {}


def register(pack: GenrePack) -> None:
    _REGISTRY[pack.genre_id] = pack


def get_genre_pack(genre_id: str | None = None) -> GenrePack:
    """按 id 取题材包; None 或未知 id 返回默认(校园)。"""
    if not genre_id:
        return _REGISTRY[DEFAULT_GENRE_ID]
    return _REGISTRY.get(genre_id, _REGISTRY[DEFAULT_GENRE_ID])


def resolve_pack_by_genre_string(genre: str | None) -> GenrePack:
    """把 novel.yaml 里的 genre 字符串(如 '青春校园恋爱')解析为题材包。"""
    if genre:
        g = str(genre).strip().lower()
        for pack in _REGISTRY.values():
            if g == pack.genre_id.lower():
                return pack
            if any(g == str(alias).strip().lower() for alias in pack.genre_aliases):
                return pack
    return _REGISTRY[DEFAULT_GENRE_ID]


def resolve_project_pack(paths) -> GenrePack:
    """读取项目 novel.yaml 的 genre, 解析题材包; 失败回退默认。"""
    try:
        from ..config import load_yaml
        data = load_yaml(paths.novel_config)
        return resolve_pack_by_genre_string(data.get("genre") if isinstance(data, dict) else None)
    except Exception:
        return _REGISTRY[DEFAULT_GENRE_ID]


# 注册内置题材: 深度作者化包 + 原型基线 + 全题材派生包
register(CAMPUS_PACK)
register(URBAN_PACK)
register(XUANHUAN_PACK)
register(MALE_PROGRESSION_PACK)
register(FEMALE_ROMANCE_PACK)
for _pack in build_derived_packs():
    register(_pack)

