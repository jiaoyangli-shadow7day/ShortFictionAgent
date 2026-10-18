"""Bounded title/opening lessons from user screenshots, with fragment-only scope."""
import json
from pathlib import Path

LIBRARY_PATH = Path(__file__).with_name('data') / 'screenshot_opening_research.json'
TAG_FAMILIES = {'家庭': ['family_growth'], '成长': ['family_growth']}
ROMANCE_TERMS = ('爱情', '恋爱', '暗恋', '情侣', '男友', '女友', '婚恋', '甜宠')


def library():
    return json.loads(LIBRARY_PATH.read_text(encoding='utf-8'))


def positive_text(contract):
    genre = contract.get('genre') or {}
    return ' '.join(str(value) for value in (
        contract.get('title', ''), genre.get('primary', ''),
        *genre.get('secondary', []), genre.get('custom_description', ''),
        contract.get('reader_promise', ''),
        (contract.get('author_input') or {}).get('premise', ''),
        *(contract.get('audience') or {}).get('expectations', []),
        *(contract.get('constraints') or {}).get('must_keep', [])))


def tag_families(tag, default, contract, tags):
    if tag in TAG_FAMILIES:
        return TAG_FAMILIES[tag]
    # Campus is a setting. Explicit growth without a romance promise uses
    # family/growth lessons; a campus love story retains its romance route.
    positive = positive_text(contract)
    romantic = (any(word in positive for word in ROMANCE_TERMS)
                or bool(set(tags) & {'甜宠', '现代言情', '青春甜宠'})
                or (contract.get('genre') or {}).get('primary') in {'romance', 'campus_romance'})
    if tag == '校园' and not romantic and (
            '成长' in tags or any(word in positive for word in ('成长', '自尊', '求学', '日常处境'))):
        return ['family_growth']
    return default


def requested_domains(contract, topic):
    positive = positive_text(contract)
    tags = set(topic['matched_reader_tags'])
    families = set(topic['selected_families'])
    domains = set()
    fantasy = bool(tags & {'玄幻仙侠', '古言甜宠', '玄幻', '幻想言情'}) or any(
        word in positive for word in ('修仙', '师尊', '宗门', '女帝', '魔尊', '飞升'))
    campus = '校园' in tags or '校园' in positive
    if fantasy:
        domains.add('fantasy')
    if campus:
        domains.add('campus')
    if tags & {'家庭', '婚姻家庭', '亲情', '萌宝', '养崽文'} or any(
            word in positive for word in ('豪门', '认亲', '真假少', '父母', '家族', '姐弟')):
        domains.add('family')
    if not fantasy and (any(word in positive for word in ROMANCE_TERMS)
            or 'male_relationship' in families
            or ('romance' in families and not fantasy and not campus)):
        domains.add('relationship')
    if tags & {'系统', '女频脑洞'} or any(
            word in positive for word in ('系统', '弹幕', '换命', '改文', '抹杀')):
        domains.add('speculative')
    if tags & {'甜宠', '团宠', '萌宝', '养崽文'} or any(
            word in positive for word in ('照顾', '温暖', '领养', '亲情')):
        domains.add('care')
    if tags & {'历史', '历史古代', '宫斗宅斗', '权谋'} or any(
            word in positive for word in ('皇帝', '太子', '帝王')):
        domains.add('historical')
    if any(word in positive for word in ('社交', '聚会', '羞辱')):
        domains.add('social')
    return domains


def context(contract, topic):
    data = library()
    stage = topic['stage']
    result = {'version': data['version'], 'source': data['source'],
              'scope': data['scope'], 'limits': data['limits'],
              'rules': [], 'mechanisms': [], 'evidence': [], 'matched_domains': []}
    if contract.get('format') != 'short_story' or stage not in data['stage_rules']:
        result['selection'] = 'no_fragment_evidence_for_this_format_or_stage'
        return result
    domains = requested_domains(contract, topic)
    result.update(rules=data['stage_rules'][stage], matched_domains=sorted(domains))
    positive = positive_text(contract)
    tags = set(topic['matched_reader_tags'])
    gentle = bool(tags & {'甜宠', '古言甜宠', '青春甜宠', '团宠'}) or any(
        word in positive for word in ('温暖', '互助', '稳定恋爱'))
    ranked = []
    for mechanism in data['mechanisms']:
        overlap = domains.intersection(mechanism['domains'])
        keyword_matches = sum(word in positive for word in mechanism['keywords'])
        if domains == {'campus'} and mechanism['id'] != 'care_and_self_worth' and not keyword_matches:
            continue
        refs = [ref for ref in mechanism['evidence_ids']
                if domains.intersection(data['evidence'][ref]['domains'])
                and (not gentle or set(data['evidence'][ref]['emotional_paths']) & {'care', 'comedy'})]
        if overlap and refs:
            score = 3 * keyword_matches + len(overlap)
            ranked.append((score, mechanism, refs))
    ranked.sort(key=lambda item: -item[0])
    chosen = ranked[:3]
    # One source per sent mechanism, then fill the four-case budget. Preserve
    # distinct mechanisms rather than ranking by cumulative likes.
    anchors = [refs[0] for _, _, refs in chosen]
    refs = list(dict.fromkeys(anchors + [ref for _, _, ids in chosen for ref in ids]))[:4]
    for _, mechanism, ids in chosen:
        result['mechanisms'].append({
            'id': mechanism['id'], 'evidence_ids': [ref for ref in refs if ref in ids],
            'rule': mechanism['ideation_rule'] if stage == 'ideation' else mechanism['opening_rule']})
    result['evidence'] = [{key: data['evidence'][ref][key] for key in (
        'source_id', 'title', 'read_scope', 'full_text_read', 'ending_observed',
        'opening_observation', 'limit', 'source_refs')} | {'sample_id': ref} for ref in refs]
    result['selection'] = 'positive_subject_and_readership' if refs else 'no_relevant_case'
    return result
