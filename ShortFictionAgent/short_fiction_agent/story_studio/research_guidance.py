"""Curated, versioned editorial lessons; never a platform ranking predictor."""
import json
from pathlib import Path

from .contracts import StoryError, digest
from .engagement import GUIDANCE as CONTINUOUS_PULL_GUIDANCE
from .whole_story import PRINCIPLES as WHOLE_STORY_PRINCIPLES
from . import reader_rewards
from . import screenshot_research
from . import lead
from . import narrative_guidance
from . import target_reader

VERSION = 'topic-gap-enrichment-2026-10-11-v17-original-target-reader'
SOURCE = 'docs/architecture.md'
LIBRARY_PATH = Path(__file__).with_name('data') / 'short_story_research.json'
WHOLE_LIBRARY_PATH = Path(__file__).with_name('data') / 'whole_story_research.json'
DESIRE_LIBRARY_PATH = Path(__file__).with_name('data') / 'desire_payoff_research.json'
DRAMA_LIBRARY_PATH = Path(__file__).with_name('data') / 'dramatic_realization_research.json'
PROSE_LIBRARY_PATH = Path(__file__).with_name('data') / 'prose_expression_research.json'
COMMON = [
    '标题、导语和第一场兑现同一情绪承诺；直接进入可理解的关系事件、反常选择或规则异常，背景服务当前行动。',
    '特殊设定必须持续改变选择与后果；每场检查目标、阻碍、行动、结果及信息、关系或情绪的实质变化，避免重复争吵与心理独白。',
    '感受由具体行为和细节支撑；重要物件参与行动并产生后果，反转留下可回看线索。揭开一个问题后转向新的选择，不无限拖延同一误会。',
    '重生改命先让读者看懂前次经历的具体后果、回到的时刻与本次改选的理由；必要回忆随当下动作补入，不能为了快节奏删掉情绪依据。长期退让的人物通过犹豫、试探、选择和承受后果逐步改变，不瞬间喊口号。',
]
PROFILES = {
    'relationship': ['偏爱和信任用记住喜好、照顾、主动沟通和公开选择兑现；吻与拥抱承担关系变化，不能靠数量撑篇幅。', '允许稳定恋爱与喜剧；不要为了冲突机械增加背叛，不把虐情样本当甜宠范本。'],
    'growth': ['差别待遇、退出路径与选择代价要可见；主角获得行动能力，温暖与互助也可兑现成长，不强制全员反派。'],
    'mystery': ['异常规则或道具随证据递进改变含义；主角亲自验证，不能用弹幕替代行动或临时增加能力解决困局。'],
    'comedy': ['保持清晰目标，让不同障碍改变方案；人物特征影响行动，笑点同时推进因果，避免重复夸张反应。'],
    'power': ['宫斗、宅斗与权谋追踪资源、名分、联盟和行动权限的变化；旧胜利可能暴露身份或产生新的利益冲突。证据来源与人物权力不能临时增加。'],
    'adventure': ['玄幻、仙侠、升级与幻想冒险让能力边界改变策略；承诺升级获得感时，要实际使用新能力获得资源、行动权限或胜利。责任与成本按本篇冲突选择，不能成为每次获得奖励的强制抵消。'],
    'professional': ['职场、娱乐与职业故事让认可、签字、合作或公开选择产生后续责任；职业身份必须影响行动，不能用身份标签冒充职业细节。',
                     '职业细节可以是工作中的动作、合作、判断和失误，不等于技术参数与文件举证。证据揭示之后仍需人物改变策略；演员不能因职业身份自动精通工程取证。拒绝不当要求并不自动赢得大奖、爆红或头部签约，回报须由前文劳动与机会路径支撑，允许兑现为有限但具体的成果。'],
    'care': ['养成、养崽、奶爸、团宠和都市日常以照顾的反馈、能力增长和共同生活兑现承诺；信任不等于权限。舒适文可平稳推进，不强加背叛。'],
    'historical': ['年代、历史、民国、基建与生活改善使用具体时代资源和限制；变化体现在角色选择及所得，不能把清单、史料讲解或连续奖赏当成剧情。'],
    'satire': ['讽刺、沙雕与搞笑注意叙述者是否可靠和笑点指向；人物欲望可稳定，但尝试的后果应改变局面，不把样本偏见当作事实。'],
}
LIMITS = [
    '来源、阅读深度和时间见每条证据卡；公开正文实读、卡片导语和只获取元数据分别记录。第三方点赞快照不代表官网读过、自然曝光或推荐规则。',
    '不固化前300字爆点、固定间隔反转、每场打脸或吻戏数量；短标题、回忆、文学描写和主动沟通都有热门反例。',
    '尊重作者合同与目标情绪；只迁移抽象机制，不复制来源人物、道具组合、揭底顺序或原文。只评价正文证据，不补写未见结尾，不预测流量与收益。',
]

def guidance_fingerprint():
    semantic_library = dict(research_library())
    semantic_library.pop('built_at', None)
    whole_library = dict(whole_story_library())
    whole_library.pop('built_at', None)
    desire_library = desire_payoff_library()
    return digest({'version': VERSION, 'source': SOURCE, 'common': COMMON,
                   'target_reader': target_reader.criteria_hash(),
                   'profiles': PROFILES, 'limits': LIMITS, 'library': semantic_library,
                   'continuous_pull': CONTINUOUS_PULL_GUIDANCE,
                   'reader_rewards': {'protocol': reader_rewards.PROTOCOL, 'guidance': reader_rewards.GUIDANCE},
                   'desire_payoff': desire_library,
                   'dramatic_realization': dramatic_realization_library(),
                   'prose_expression': prose_expression_library(),
                   'narrative_expression': {'protocol': narrative_guidance.PROTOCOL,
                       'rules': narrative_guidance.RULES, 'examples': narrative_guidance.EXAMPLES},
                   'screenshot_openings': screenshot_research.library(),
                   'manuscript_lead': {'protocol': lead.PROTOCOL, 'library': lead.library(),
                                       'checks': lead.CHECKS},
                   'whole_story': whole_library, 'whole_story_principles': WHOLE_STORY_PRINCIPLES})


def dramatic_realization_library():
    return json.loads(DRAMA_LIBRARY_PATH.read_text(encoding='utf-8'))


def prose_expression_library():
    """Add expression evidence without rewriting the seven original research banks."""
    return json.loads(PROSE_LIBRARY_PATH.read_text(encoding='utf-8'))


def prose_expression_context(contract, topic):
    library = prose_expression_library()
    if contract.get('format') != 'short_story' or topic['stage'] not in {'write', 'review'}:
        return {'version': library['version'], 'scope': library['scope'], 'evidence': []}
    positive = ' '.join(str(value) for value in (
        contract.get('title', ''), contract.get('reader_promise', ''),
        (contract.get('author_input') or {}).get('premise', ''),
        *(contract.get('audience') or {}).get('expectations', []),
        *topic['matched_reader_tags']))
    families = set(topic['selected_families'])
    candidates = [row for row in library['evidence'] if families.intersection(row['families'])]
    # A bank example cannot sneak into an ordinary family scene on a broad tag.
    candidates = [row for row in candidates if row['id'].startswith('prose_sweet_')
                  and families.intersection({'romance', 'male_relationship'})
                  or any(term in positive for term in row['match_terms'])]
    candidates.sort(key=lambda row: (-sum(term in positive for term in row['match_terms']), row['id']))
    chosen = [{key: row[key] for key in ('id', 'title', 'source', 'evidence',
              'expression_mechanism', 'counterexample_limit')} for row in candidates[:library['selection_limit']]]
    return {'version': library['version'], 'audit': library['audit'], 'scope': library['scope'],
            'selection': 'positive_premise_and_same_family', 'evidence': chosen,
            'transfer_rule': '先用本篇欲望—动作—对方回应写场面，再核对解释或描写的作用；'
                             '保留必要事实和有作用的安静细节；只借抽象表达机制，不复制原句或情节。'}


def dramatic_realization_context(contract, topic):
    library = dramatic_realization_library()
    families = set(topic['selected_families'])
    return {'version': library['version'], 'rules': library['rules'],
            'scope': library['scope'], 'evidence': [row for row in library['evidence']
                if contract.get('format') == 'short_story' and families.intersection(row['families'])]}


def whole_story_library():
    return json.loads(WHOLE_LIBRARY_PATH.read_text(encoding='utf-8'))


def desire_payoff_library():
    return json.loads(DESIRE_LIBRARY_PATH.read_text(encoding='utf-8'))


def desire_payoff_context(contract, topic):
    """Route checked mechanisms by the positive story premise and readership."""
    library = desire_payoff_library()
    if contract.get('format') != 'short_story':
        return {'version': library['version'], 'selection': 'short_story_only', 'mechanisms': []}
    positive = ' '.join(str(value) for value in (
        contract.get('title', ''), contract.get('reader_promise', ''),
        (contract.get('author_input') or {}).get('premise', ''),
        *(contract.get('audience') or {}).get('expectations', [])))
    selected_families = set(topic['selected_families'])
    tags = set(topic['matched_reader_tags'])
    ranked = []
    for row in library['mechanisms']:
        if not selected_families.intersection(row['families']):
            continue
        terms = [term for term in row['match_terms'] if term in positive]
        specific_tags = tags.intersection(row['tags'])
        if not terms and not specific_tags:
            continue
        # A broad shared tag alone cannot make an unrelated premise a case study.
        score = 3 * len(terms) + len(specific_tags)
        ranked.append((score, row))
    ranked.sort(key=lambda item: (-item[0], item[1]['id']))
    stage = {'opening': 'ideation', 'package': 'review'}.get(topic['stage'], topic['stage'])
    def compact(row):
        return {key: row[key] for key in ('id', 'evidence_ids', 'reader_desire',
            'trigger_action_payoff', 'cumulative_change', 'counterexample')} | {
            'stage_rule': row['stages'][stage]}
    chosen = [compact(row) for _, row in ranked[:2]]
    inference = library['cross_case_inference']
    if (topic['audience_track'] == 'male' and ('系统' in tags or '系统' in positive)
            and ('女总裁' in positive or '总裁' in positive)
            and any(word in positive for word in ('钱', '财富', '夺权', '攻略', '系统'))):
        chosen.append({'id': inference['id'], 'evidence_ids': inference['source_ids'],
                       'evidence_limit': inference['scope'],
                       'stage_rule': inference['stages'][stage]})
    return {'version': library['version'], 'source': library['audit'],
            'selection': 'positive_premise_and_family', 'mechanisms': chosen,
            'limit': library['scope']}


def model_research_context(context):
    """Send actionable, traceable cards without exhausting the prose budget."""
    topics = {}
    for family, card in context['topic_guidance'].items():
        topics[family] = {
            'rules': card['rules'], 'mechanisms': card['mechanisms'],
            'evidence': [{key: item[key] for key in
                ('source_id', 'title', 'read_scope', 'reader_promise', 'progression_observations')
                if key in item} | {'limitations': [str(value)[:180] for value in
                    item.get('limitations', [])[-2:]]} for item in card['evidence']],
            'evidence_ids': card['evidence_ids'], 'coverage_limit': card['coverage_limit']}
    continuous = context['continuous_pull']
    reading = continuous['continuous_reading_evidence']
    whole = context['whole_story_research']
    return {
        'version': context['version'], 'fingerprint': context['fingerprint'],
        'scope': context['scope'],
        'stage': context['stage'], 'selected_families': context['selected_families'],
        'common': context['common'], 'profiles': context['profiles'],
        'limits': context['limits'], 'topic_guidance': topics,
        'desire_payoff_research': context['desire_payoff_research'],
        'dramatic_realization_research': context['dramatic_realization_research'],
        'prose_expression_research': context['prose_expression_research'],
        'target_reader_research': context['target_reader_research'],
        'screenshot_opening_research': context['screenshot_opening_research'],
        'manuscript_lead_research': context['manuscript_lead_research'],
        'continuous_pull': {'rules': continuous['rules'],
            'evidence': [{key: item[key] for key in
                ('book_id', 'title', 'read_scope', 'mechanism', 'caveat') if key in item}
                for item in reading['evidence']], 'transfer': reading.get('transfer', [])},
        'whole_story_research': {'principles': whole['principles'],
            'evidence': [{key: item[key] for key in
                ('book_id', 'title', 'read_scope', 'transfer', 'weaknesses') if key in item}
                for item in whole['evidence']], 'limit': whole['limit']},
    }


def _route_applicable(route, tags, positive):
    """A broad discovery tag is insufficient evidence for a specific premise."""
    if route['id'] == 'enrichment_comic_foreign_order':
        return bool(set(tags) & {'历史古代', '历史'}) or any(
            word in positive for word in ('朝堂', '暴君', '皇帝', '古代喜剧'))
    if route['id'] == 'enrichment_fantasy_care_disguise':
        return bool(set(tags) & {'奇妙物语', '无限流'}) or any(
            word in positive for word in ('怪物', '副本', '非人'))
    if route['id'] == 'enrichment_marriage_signal_test':
        return any(word in positive for word in ('误会', '试探', '验证', '弹幕', '不愿生育'))
    if route['id'] == 'apocalypse_stockpile':
        return bool(set(tags) & {'科幻末世', '末日求生'}) or any(
            word in positive for word in ('末世', '末日', '丧尸', '极寒', '全球高温', '囤物资'))
    if route['id'] == 'fantasy_deconstruction':
        return bool(set(tags) & {'玄幻仙侠', '玄幻', '幻想言情', '都市异能', '奇妙物语'}) or any(
            word in positive for word in ('修仙', '宗门', '飞升', '除妖', '女帝', '灵根'))
    return True


def _source_applicable(row, tags, positive):
    """Validate a source premise too: shared tags cannot bypass route exclusions."""
    apocalypse = set(row.get('tags') or []) & {'科幻末世', '末日求生'}
    apocalypse = apocalypse or any(word in row.get('title', '') for word in
        ('末世', '末日', '极寒', '全球高温'))
    if apocalypse:
        return _route_applicable({'id': 'apocalypse_stockpile'}, tags, positive)
    return True


def whole_story_context(contract, topic):
    library = whole_story_library()
    tags = topic['matched_reader_tags']
    # Time, broad readership and mood alone cannot pick a story premise.
    generic = {'现代', '古代', '男生生活', '女生生活', '男生情感', '爽文',
               '虐文', '重生', '穿越', '打脸', '打脸逆袭', '反转', '逆袭', '其他'}
    specific = set(tags) - generic
    records = [row for row in library['evidence'] if set(row['tags']) & specific]
    positive = ' '.join(str(value) for value in (
        contract.get('reader_promise', ''), (contract.get('author_input') or {}).get('premise', ''),
        *(contract.get('constraints') or {}).get('must_keep', [])))
    records = [row for row in records if _source_applicable(row, tags, positive)]
    def rank(row):
        return (-len(set(row['tags']) & specific), not row['full_text_read'],
                not (row.get('heat') or {}).get('qualified', False), row['book_id'])
    records.sort(key=rank)
    chosen = records[:2] if contract.get('format') == 'short_story' else []
    def summary(row):
        result = {key: row[key] for key in ('book_id', 'title', 'source_url', 'read_scope',
            'full_text_read', 'ending_observed', 'popularity_class', 'analysed_at', 'scope_limit')}
        # These are our editorial notes, not source prose. Keep the complete
        # chain in the shipped library while sending a bounded case summary.
        for key in ('choice_and_cost', 'cumulative_consequence', 'prop_function', 'transfer'):
            value = row.get(key)
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            result[key] = text if len(text) <= 280 else text[:280] + '…（摘要，完整标注见研究库）'
        result['weaknesses'] = [str(value)[:180] for value in row.get('weaknesses', [])[:2]]
        result['selection_tags'] = sorted(set(row['tags']) & specific)
        return result
    chosen = [summary(row) for row in chosen]
    return {'version': library['version'], 'source': library['source'],
            'scope': library['scope'], 'taxonomy_scope': library['taxonomy_scope'],
            'coverage': library['coverage'],
            'registered_tags': list(library['taxonomy']) if topic['stage'] == 'ideation' else [],
            'matched_tag_coverage': {tag: {key: row[key] for key in ('official_inventory',
                'continuous_readings', 'full_readings', 'heat_qualified_readings', 'coverage_status')}
                for tag in tags if tag in library['taxonomy'] for row in [library['taxonomy'][tag]]},
            'principles': WHOLE_STORY_PRINCIPLES, 'evidence': chosen,
            'selection': 'specific_tag_overlap' if chosen else 'no_direct_case_use_abstract_principles',
            'limit': '同标签仍须检查读者承诺是否相符；不强制复写案例。不足两篇就少发；没有相关证据不填其他题材。'}


def research_library():
    """Ship the curated index with the package, independent of workspace paths."""
    return json.loads(LIBRARY_PATH.read_text(encoding='utf-8'))


def validate_research_preferences(raw):
    if not isinstance(raw, dict):
        raise StoryError('research_preferences 必须是映射')
    if not raw:
        return {}
    if set(raw) - {'reader_tags', 'audience_track', 'topic_family'}:
        raise StoryError('research_preferences 含未知字段')
    tags = raw.get('reader_tags', [])
    if (not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags)):
        raise StoryError('reader_tags 必须是非空标签字符串的列表')
    track = raw.get('audience_track', 'general')
    if track not in {'male', 'female', 'general'}:
        raise StoryError('audience_track 仅支持 male/female/general')
    family = raw.get('topic_family')
    if family is not None and (not isinstance(family, str) or family not in research_library()['families']):
        raise StoryError('topic_family 必须是研究库已有题材')
    result = {'reader_tags': list(dict.fromkeys(tag.strip() for tag in tags)), 'audience_track': track}
    if family:
        result['topic_family'] = family
    return result


def _stage(task):
    if task in {'channel_metadata', 'publication_preparation', 'carousel_adapt', 'carousel_review'}:
        return 'package'
    if task in {'opening_candidates', 'opening_comparative_review', 'lead_candidates', 'lead_comparative_review'}:
        return 'opening'
    if task in {'story_candidates', 'candidate_comparative_review'}:
        return 'ideation'
    if task and ('review' in task or task in {'causal_challenge', 'blueprint_audit'}):
        return 'review'
    if task and ('write_' in task or task.startswith('repair_story') or task == 'story_edit'):
        return 'write'
    return 'plan'


def topic_context(contract, *, task=None):
    library = research_library()
    preference = validate_research_preferences(contract.get('research_preferences') or {})
    explicit_tags = preference.get('reader_tags', [])
    # Forbidden items, character gender and copied examples do not define readership.
    audience = contract.get('audience') or {}
    audience_text = str(audience.get('description', ''))
    track = preference.get('audience_track', 'general')
    if track == 'general':
        male = any(word in audience_text for word in ('男频', '男性读者', '男生读者'))
        female = any(word in audience_text for word in ('女频', '女性读者', '女生读者'))
        if male != female:
            track = 'male' if male else 'female'
    genre = contract.get('genre') or {}
    positive = ' '.join(str(value) for value in (
        genre.get('primary', ''), *genre.get('secondary', []), genre.get('custom_description', ''),
        contract.get('reader_promise', ''), (contract.get('author_input') or {}).get('premise', ''),
        *(contract.get('constraints') or {}).get('must_keep', []),
        *audience.get('expectations', [])))
    # Explicit reader tags take priority over broad canonical genres such as "campus_romance".
    tags = explicit_tags or [tag for tag in library['tags'] if tag in positive]
    # Compound labels precede contained labels, e.g. 科幻末世 before 科幻.
    if not explicit_tags:
        tags = [tag for tag in tags if not any(tag != longer and tag in longer for longer in tags)]
    tags.sort(key=lambda tag: (tag not in screenshot_research.TAG_FAMILIES,
                              library['tags'].get(tag, {}).get('priority', 50), -len(tag)))
    families, reasons = [], []
    if preference.get('topic_family'):
        families.append(preference['topic_family'])
        reasons.append({'family': families[-1], 'basis': 'explicit_topic_family'})
    for tag in tags:
        default = library['tags'].get(tag, {}).get('families', [])
        for family in screenshot_research.tag_families(tag, default, contract, tags):
            if family not in families:
                families.append(family)
                reasons.append({'family': family,
                    'basis': 'contextual_reader_tag' if family not in default else 'reader_tag', 'tag': tag})
    if not families:
        defaults = {'relationship_choice': 'romance', 'mystery_reveal': 'mystery_rules',
                    'survival_dilemma': 'survival', 'speculative_dilemma': 'speculative_choice',
                    'comedy_misunderstanding': 'comedy', 'goal_competition': 'revenge_status',
                    'progression': 'speculative_choice'}
        fallback = defaults.get(contract.get('story_engine'))
        if fallback:
            families.append(fallback)
            reasons.append({'family': fallback, 'basis': 'story_engine'})
        elif genre.get('primary') in {'romance', 'campus_romance'}:
            families.append('romance')
            reasons.append({'family': 'romance', 'basis': 'genre'})
    if track == 'male' and 'romance' in families:
        families = ['male_relationship' if f == 'romance' else f for f in families]
        reasons.append({'family': 'male_relationship', 'basis': 'explicit_male_readership'})
    selected = list(dict.fromkeys(families))[:3]
    stage = _stage(task)
    cards = {}
    for family in selected:
        profile = library['families'][family]
        def stage_refs(route):
            return [ref for ref in route['evidence_ids']
                    if stage in library['evidence'][ref]['supports_stages']
                    and _source_applicable(library['evidence'][ref], tags, positive)]
        eligible_routes = [route for route in library.get('routes', [])
                           if route['parent_family'] == family and stage_refs(route)
                           and _route_applicable(route, tags, positive)]
        def score(route):
            specific = set(route['tags']) & set(tags) - {'男生情感','男生生活','女生生活','重生','穿越'}
            return len(specific) * 3 + sum(word in positive for word in route['keywords'])
        eligible_routes.sort(key=score, reverse=True)
        selected_routes = [route for route in eligible_routes if score(route) > 0][:2]
        if not selected_routes and eligible_routes:
            default_route = {'male_relationship':'male_romance_choice','romance':'female_sweet_information_gap'}
            selected_routes = [route for route in eligible_routes if route['id'] == default_route.get(family)][:1]
        # Reserve one usable source for each mechanism before filling the bounded retrieval.
        anchors = [stage_refs(route)[0] for route in selected_routes]
        remaining = [ref for route in selected_routes for ref in stage_refs(route)]
        refs = [ref for ref in dict.fromkeys(anchors + remaining)
                if stage in library['evidence'][ref]['supports_stages']][:4]
        evidence = [library['evidence'][ref] for ref in refs]
        cards[family] = {'label': profile['label'], 'rules': profile['stages'][stage],
                         'mechanisms': [{'id':route['id'], 'rules':route['stages'][stage],
                             'evidence_ids': [ref for ref in refs if ref in stage_refs(route)],
                             'basis': 'matched_tag_or_positive_premise' if score(route)>0 else 'family_default',
                             'limits':route['limits']} for route in selected_routes],
                         'evidence': evidence, 'evidence_ids': [row['source_id'] for row in evidence],
                         'coverage_limit': profile['coverage_limit']}
        if family == 'survival' and not any(route['id'] == 'apocalypse_stockpile' for route in selected_routes):
            cards[family].update(label='求生与受限选择', rules=[
                '依据作者具体危险、资源和人物责任组织求生；前次选择的后果必须改变后续方案。',
                '没有末世前提时，不使用囤货、基地和系统奖励模板；职业事实须另查对应资料。'])
    return {'stage': stage, 'audience_track': track, 'selected_families': selected,
            'selection_basis': reasons, 'matched_reader_tags': tags,
            'deferred_families': [family for family in dict.fromkeys(families) if family not in selected],
            'tag_limitations': {tag: library['tags'][tag]['limitation'] for tag in tags if tag in library['tags']},
            'unmapped_reader_tags': [tag for tag in explicit_tags if tag not in library['tags']
                                    and tag not in screenshot_research.TAG_FAMILIES],
            'topic_guidance': cards,
            'available_families': [] if selected else {key: value['label'] for key, value in library['families'].items()},
            'tag_scope': '读者发现标签；不代替作者后台投稿分类',
            'coverage': library['coverage'], 'stage_checks': library['stage_checks'][stage]}

def research_context(contract, *, task=None):
    genre = contract.get('genre', {})
    names = {genre.get('primary'), *genre.get('secondary', [])}
    engine = contract.get('story_engine')
    reader_tags = set((contract.get('research_preferences') or {}).get('reader_tags', []))
    selected = []
    if names & {'romance', 'campus_romance'} or engine == 'relationship_choice':
        selected.append('relationship')
    if names & {'mystery', 'fantasy', 'science_fiction'} or engine in {'mystery_reveal', 'survival_dilemma', 'speculative_dilemma'}:
        selected.append('mystery')
    if engine == 'comedy_misunderstanding':
        selected.append('comedy')
    if engine in {'emotional_reframing', 'progression', 'goal_competition'}:
        selected.append('growth')
    for profile,tags in {
        'power':{'宫斗宅斗','权谋','古风世情','仕途'},
        'adventure':{'玄幻仙侠','玄幻','升级流','武侠','历史武侠','幻想言情','都市异能'},
        'professional':{'娱乐圈','明星','医生','神医','职场','直播','外卖'},
        'care':{'奶爸','团宠','养崽文','萌宝','都市日常','现实生活'},
        'historical':{'年代','历史','历史古代','民国','基建'},
        'satire':{'沙雕搞笑','搞笑轻松'},
    }.items():
        if reader_tags & tags: selected.append(profile)
    # Unknown/custom genres receive alternatives explicitly, rather than a guessed genre.
    options = selected or list(PROFILES)
    topic = topic_context(contract, task=task)
    continuous = CONTINUOUS_PULL_GUIDANCE
    if contract.get('format') != 'short_story':
        # These direct-reading cases concern short stories. Do not expand a
        # serial's full-text review with unrelated short-story case details.
        continuous = {**continuous, 'continuous_reading_evidence': {
            'scope': '短故事专项正文研究；本体裁仅参考抽象原则，不沿用其正文实证',
            'evidence': []}}
    else:
        # Retrieve a bounded set of direct-reading cases. A growing library must
        # not crowd out the complete prose or force a larger context limit.
        group_map = {'romance': 'sweet', 'male_relationship': 'sweet',
                     'family_growth': 'family', 'revenge_status': 'family',
                     'mystery_rules': 'suspense_speculative',
                     'speculative_choice': 'suspense_speculative',
                     'survival': 'suspense_speculative'}
        groups = list(dict.fromkeys(group_map[f] for f in topic['selected_families'] if f in group_map))
        library = continuous['continuous_reading_evidence']
        source_premise = ' '.join(str(value) for value in (
            contract.get('reader_promise', ''), (contract.get('author_input') or {}).get('premise', '')))
        eligible = [item for item in library['evidence'] if item['group'] in groups
                    and _source_applicable(item, topic['matched_reader_tags'], source_premise)]
        if 'survival' in topic['selected_families'] and not any(
                card['mechanisms'] for family,card in topic['topic_guidance'].items() if family == 'survival'):
            eligible = []
        # Match the author's positive premise, never an avoid-list: otherwise
        # "do not use civil-office marriage" could retrieve that very case.
        premise = json.dumps({'title': contract.get('title'),
                              'reader_promise': contract.get('reader_promise'),
                              'premise': contract.get('author_input', {}).get('premise'),
                              'reader_tags': contract.get('research_preferences', {}).get('reader_tags')},
                             ensure_ascii=False)
        matched_terms = {item['book_id']: [term for term in item.get('match_terms', [])
                                          if term in premise] for item in eligible}
        eligible.sort(key=lambda item: (-len(matched_terms[item['book_id']]),
                                        not item['full_text_read']))
        anchors = [next((item for item in eligible if item['group'] == group), None) for group in groups]
        chosen = list({item['book_id']:item for item in anchors + eligible if item}.values())[:2]
        continuous = {**continuous, 'continuous_reading_evidence': {
            'version': library['version'], 'source': library['source'],
            'scope': library['scope'], 'heat_scope': library['heat_scope'],
            'selection': 'matched_topic_group' if groups else 'choose_by_author_promise',
            'matched_groups': groups, 'evidence': chosen,
            'matched_terms': {item['book_id']: matched_terms[item['book_id']] for item in chosen},
            'deferred_source_ids': [item['book_id'] for item in library['evidence'] if item not in chosen],
            'transfer': library['transfer']}}
    return {'version': VERSION, 'fingerprint': guidance_fingerprint(), 'source': SOURCE,
            'scope': 'internal_editorial_hypotheses', 'common': COMMON,
            'profile_selection': 'contract' if selected else 'choose_by_author_promise',
            'profiles': {key: PROFILES[key] for key in options}, 'limits': LIMITS,
            'continuous_pull': continuous,
            'desire_payoff_research': desire_payoff_context(contract, topic),
            'dramatic_realization_research': dramatic_realization_context(contract, topic),
            'prose_expression_research': prose_expression_context(contract, topic),
            'target_reader_research': target_reader.research_context(contract, topic['stage']),
            'screenshot_opening_research': screenshot_research.context(contract, topic),
            'manuscript_lead_research': lead.context(contract, topic['stage']),
            'whole_story_research': whole_story_context(contract, topic),
            **topic}
