"""Match curated public campaigns without claiming award eligibility."""
from datetime import date
from urllib.parse import urlparse
from .contracts import StoryError


def match_campaigns(rules, *, platform, entry_type, category, word_count,
                    as_of, signed_at=None, exclusive_original=None):
    today = date.fromisoformat(as_of)
    if type(word_count) is not int or word_count < 0:
        raise StoryError('字数需为非负整数；平台统计口径需另行核对')
    output = []
    for rule in rules:
        required = {'id', 'source_url', 'checked_at', 'start', 'end', 'platform', 'entry_type',
                    'categories', 'min_words', 'max_words', 'benefit', 'external_conditions'}
        if not isinstance(rule, dict) or not required.issubset(rule):
            raise StoryError('活动资料缺少来源、时效、形式或条件')
        if (not isinstance(rule['categories'], list)
                or not isinstance(rule['external_conditions'], list)
                or any(type(rule[k]) is not int or rule[k] < 0 for k in ('min_words', 'max_words')
                       if rule[k] is not None)):
            raise StoryError('活动分类、待核验条件或字数范围格式无效')
        if rule['min_words'] is not None and rule['max_words'] is not None and rule['min_words'] > rule['max_words']:
            raise StoryError('活动字数范围矛盾')
        if not isinstance(rule['source_url'], str) or urlparse(rule['source_url']).scheme != 'https':
            raise StoryError('活动来源须为公开 HTTPS URL')
        if rule['platform'] != platform or rule['entry_type'] != entry_type:
            continue
        checked = date.fromisoformat(rule['checked_at'])
        start, end = date.fromisoformat(rule['start']), date.fromisoformat(rule['end'])
        if end < start:
            raise StoryError('活动起止日期矛盾')
        reasons = []
        if not start <= today <= end: reasons.append('outside_campaign_window')
        if checked > today or (today - checked).days > 7:
            reasons.append('source_refresh_required')  # Internal freshness policy, not platform rule.
        if rule['categories'] and category not in rule['categories']:
            reasons.append('category_not_listed')
        if rule['min_words'] is not None and word_count < rule['min_words']:
            reasons.append('below_length')
        if rule['max_words'] is not None and word_count > rule['max_words']:
            reasons.append('above_length')
        if signed_at and not start <= date.fromisoformat(signed_at) <= end:
            reasons.append('signature_outside_window')
        if exclusive_original is False: reasons.append('exclusive_original_not_met')
        ambiguous = word_count in rule.get('ambiguous_word_boundaries', [])
        status = ('not_matched' if reasons and not (ambiguous and set(reasons) <= {'below_length', 'above_length'})
                  else 'requires_confirmation' if ambiguous else 'potential_match')
        output.append({'id': rule['id'], 'status': status, 'reasons': reasons,
                       'source_url': rule['source_url'], 'benefit': rule['benefit'],
                       'pending': rule['external_conditions'] +
                                  ([] if signed_at else ['签约日期待确认']) +
                                  ([] if exclusive_original is True else ['独家原创首发待确认']) +
                                  ['平台实际字数与主分类', '平台编辑/运营入选判断'],
                       'commercial_validation': 'unverified'})
    return output
