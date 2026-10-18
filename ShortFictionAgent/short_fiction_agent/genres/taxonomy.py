"""番茄/起点男频·女频全题材分类表。

每个细分题材要么直接用一个**深度作者化包**(玄幻/都市/校园), 要么从**原型基线**
(male_progression / female_romance)派生(共享词表/prompt, 覆盖 id/名称/别名/channel)。
全部类别都注册可跑; 旗舰深度作者化, 其余走原型基线(诚实分层)。
"""

from dataclasses import replace

from .archetypes import MALE_PROGRESSION_PACK, FEMALE_ROMANCE_PACK

# 源: "deep" 表示直接用已有深度包(玄幻/都市/校园, 别名已覆盖中文名, 此处不派生);
#     "male"/"female" 表示从对应原型派生。
# (genre_id, 中文名, channel, 源, 别名)
TAXONOMY = [
    # ── 男频 ──
    ("xuanhuan",    "玄幻",   "male",   "deep",   ()),                       # 深度包
    ("qihuan",      "奇幻",   "male",   "male",   ("西幻", "奇幻小说")),
    ("wuxia",       "武侠",   "male",   "male",   ("武侠小说", "传统武侠")),
    ("xianxia",     "仙侠",   "male",   "male",   ("修真", "修仙", "仙侠小说")),
    ("urban",       "都市",   "male",   "deep",   ()),                       # 深度包
    ("xianshi",     "现实",   "male",   "male",   ("现实题材",)),
    ("lishi",       "历史",   "male",   "male",   ("历史小说", "架空历史")),
    ("junshi",      "军事",   "male",   "male",   ("军事小说",)),
    ("youxi",       "游戏",   "male",   "male",   ("网游", "电竞", "游戏小说")),
    ("tiyu",        "体育",   "male",   "male",   ("体育小说", "竞技")),
    ("kehuan",      "科幻",   "male",   "male",   ("科幻小说", "星际", "赛博朋克")),
    ("xuanyi_male", "悬疑灵异", "male", "male",   ("悬疑灵异", "灵异")),
    ("qingxiaoshuo_male", "轻小说", "male", "male", ("轻小说",)),
    ("duanpian_male",     "短篇",   "male", "male", ("短篇小说",)),
    ("zhutian",     "诸天无限", "male", "male",   ("无限流", "诸天", "诸天无限")),
    # ── 女频 ──
    ("guyan",       "古代言情", "female", "female", ("古言", "古代言情小说")),
    ("xianyan",     "现代言情", "female", "female", ("现言", "现代言情小说")),
    ("xuanyan",     "玄幻言情", "female", "female", ("玄言", "玄幻言情小说")),
    ("xuanyi_female", "悬疑推理", "female", "female", ("推理", "悬疑推理")),
    ("campus_romance", "浪漫青春", "female", "deep", ()),                   # 深度包(校园)
    ("xianxia_qiyuan", "仙侠奇缘", "female", "female", ("仙恋", "仙侠奇缘小说")),
    ("kehuan_female",  "科幻空间", "female", "female", ("女频科幻",)),
    ("youxi_female",   "游戏竞技", "female", "female", ("电竞言情", "女频游戏")),
    ("xianshi_female", "现实生活", "female", "female", ("女频现实", "生活")),
]


def build_derived_packs():
    """为"原型派生"的细分题材构建 GenrePack 列表(深度包不在此, 它们各自已注册)。"""
    base = {"male": MALE_PROGRESSION_PACK, "female": FEMALE_ROMANCE_PACK}
    packs = []
    for genre_id, name, channel, source, aliases in TAXONOMY:
        if source == "deep":
            continue
        archetype = base[source]
        packs.append(replace(
            archetype,
            genre_id=genre_id,
            display_name=name,
            channel=channel,
            genre_aliases=(name,) + tuple(aliases),
        ))
    return packs


def channel_listing():
    """按 channel 返回 [(channel, [(genre_id, 名称, 是否深度包)...])] 供 CLI 展示。"""
    deep_ids = {"xuanhuan", "urban", "campus_romance"}
    out = {"male": [], "female": []}
    for genre_id, name, channel, source, _ in TAXONOMY:
        out[channel].append((genre_id, name, source == "deep"))
    return out

