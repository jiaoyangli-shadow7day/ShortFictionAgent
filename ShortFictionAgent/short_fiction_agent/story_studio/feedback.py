"""Version-aware platform observations; descriptive diagnostics, not traffic gates."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import math
import re
from uuid import uuid4
from pathlib import Path

from .contracts import StoryError, digest
from .storage import StoryProject, now, read_json, write_json


COUNTS = ('display_count', 'read_count', 'read_15s', 'read_30s', 'read_60s', 'bottom_count')
PUBLICATION_FIELDS = ('title', 'category', 'cover_sha256', 'body_sha256',
                      'preview_position', 'ai_disclosure', 'published_at')


def _timestamp(value, field):
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise StoryError(f'{field} 必须是 ISO 日期或时间')
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise StoryError(f'{field} 必须是 ISO 日期或时间') from exc


def _hash(value, field):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)):
        raise StoryError(f'{field} 必须是小写 SHA256 或 null')


def normalize_observation(raw: dict) -> dict:
    """Preserve unknowns and legacy single-metric records without guessing provenance."""
    if not isinstance(raw, dict):
        raise StoryError('平台观察必须是 JSON 对象')
    result = deepcopy(raw)
    for key in ('platform', 'entry_type', 'status', 'observed_at', 'evidence'):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise StoryError(f'平台观察缺少非空字段: {key}')
    for key in ('publication_id', 'source_package_id', 'observation_window',
                'metric_definition', 'value', 'denominator', 'missing_reason', 'statistics_cutoff'):
        result.setdefault(key, None)
    if result['value'] is not None and (not isinstance(result['metric_definition'], str)
                                       or not result['metric_definition'].strip()):
        raise StoryError('数值反馈必须说明指标定义')
    for key in ('value', 'denominator'):
        value = result[key]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
            raise StoryError(f'{key} 必须是有限非负数或 null')
    metrics = result.get('metrics', {})
    if not isinstance(metrics, dict) or set(metrics) - set(COUNTS):
        raise StoryError('metrics 只支持明确的展现、阅读、时长与触底人数')
    result['metrics'] = {key: metrics.get(key) for key in COUNTS}
    for key, value in result['metrics'].items():
        if value is not None and (type(value) is not int or value < 0):
            raise StoryError(f'{key} 必须是非负整数或 null')
    # Check all known pairs, even if an intermediate field is unavailable.
    known = [result['metrics'][key] for key in COUNTS[:-1] if result['metrics'][key] is not None]
    if any(later > earlier for earlier, later in zip(known, known[1:])):
        raise StoryError('同口径漏斗人数不能递增；请检查统计窗口和渠道')
    bottom = result['metrics']['bottom_count']
    for key in ('display_count', 'read_count'):
        upper = result['metrics'][key]
        if bottom is not None and upper is not None and bottom > upper:
            raise StoryError('触底人数不能超过同口径展现或阅读人数')
    # Bottom is deliberately NOT constrained by timed reading; its meaning may differ.
    if result['value'] is None and all(v is None for v in result['metrics'].values()):
        result['missing_reason'] = result['missing_reason'] or 'not_provided'
    result.setdefault('aggregation', 'unknown')
    if result['aggregation'] not in ('cumulative_unique', 'daily_unique', 'sum_daily_unique', 'unknown'):
        raise StoryError('aggregation 口径无效')
    result.setdefault('metric_scope', None)
    if result['metric_scope'] is not None and (not isinstance(result['metric_scope'], str) or not result['metric_scope'].strip()):
        raise StoryError('metric_scope 必须是非空渠道说明或 null')
    publication = result.get('publication', {})
    if not isinstance(publication, dict):
        raise StoryError('publication 必须是对象')
    result['publication'] = {key: publication.get(key) for key in PUBLICATION_FIELDS}
    pub = result['publication']
    for key in ('title', 'category'):
        if pub[key] is not None and (not isinstance(pub[key], str) or not pub[key].strip()):
            raise StoryError(f'publication.{key} 必须是非空文字或 null')
    for key in ('body_sha256', 'cover_sha256'):
        _hash(pub[key], f'publication.{key}')
    if pub['ai_disclosure'] is not None and type(pub['ai_disclosure']) is not bool:
        raise StoryError('ai_disclosure 必须是 true、false 或 null，不能推断')
    if pub['preview_position'] is not None and (
            not isinstance(pub['preview_position'], str) or not pub['preview_position'].strip()):
        raise StoryError('preview_position 必须说明位置及单位；无试读位置可填 not_applicable')
    observed = _timestamp(result['observed_at'], 'observed_at')
    for key, value in (('statistics_cutoff', result['statistics_cutoff']),
                       ('publication.published_at', pub['published_at'])):
        stamp = _timestamp(value, key)
        if stamp and stamp.tzinfo and observed.tzinfo and stamp > observed:
            raise StoryError(f'{key} 不能晚于观察时间')
    result['schema_version'] = 2
    return result


def bind_source(project: StoryProject, observation: dict) -> dict:
    """Bind the specified historical export, never the current draft by default."""
    package_id = observation['source_package_id']
    if package_id is None:
        return {'status': 'unbound', 'source_content_hash': None}
    if not isinstance(package_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', package_id):
        raise StoryError('source_package_id 无效，不能含路径')
    exports = (project.root / 'exports').resolve()
    folder = (exports / package_id).resolve()
    if folder.parent != exports or not (folder / 'manifest.json').exists():
        raise StoryError('反馈引用的发布包不存在或位于导出目录之外')
    manifest = read_json(folder / 'manifest.json')
    if not isinstance(manifest, dict) or manifest.get('package_id') != package_id:
        raise StoryError('发布包 manifest 身份不匹配')
    source_hash = manifest.get('source_content_hash')
    _hash(source_hash, 'manifest.source_content_hash')
    if source_hash is None or not manifest.get('source_artifact_id') or type(manifest.get('source_revision')) is not int:
        raise StoryError('发布包缺少正文版本绑定')
    published_hash = observation['publication']['body_sha256']
    return {'status': ('published_hash_unconfirmed' if published_hash is None else
                       'matches_export' if published_hash == source_hash else 'published_body_differs'),
            'source_artifact_id': manifest['source_artifact_id'],
            'source_revision': manifest['source_revision'], 'source_content_hash': source_hash,
            'manifest_hash': digest(manifest),
            'provenance': 'local_export_and_user_supplied_publication_snapshot_not_platform_verified'}


def diagnose_observation(raw: dict) -> dict:
    row = normalize_observation(raw)
    m = row['metrics']
    limitations = ['单条观察只描述现象，不证明题材胜负、AI标记限流或平台推荐门槛。',
                   '时长人数不定位具体退出句；触底不等同全文读完、解锁或到账。',
                   '不跨作品、渠道、版本或累计窗口自动求和、相减或排名。']
    missing = ['publication.' + key for key in PUBLICATION_FIELDS if row['publication'][key] is None]
    missing += [key for key in ('publication_id', 'statistics_cutoff', 'metric_scope') if row[key] is None]
    if row['aggregation'] == 'unknown':
        missing.append('aggregation')
    if missing:
        limitations.append('缺少投稿或统计信息：' + '、'.join(missing))
    for key, value in (('observed_at', row['observed_at']), ('statistics_cutoff', row['statistics_cutoff']),
                       ('publication.published_at', row['publication']['published_at'])):
        stamp = _timestamp(value, key)
        if stamp and stamp.tzinfo is None:
            limitations.append(key + ' 缺少明确时区，不能比较精确时间或计算发布时长。')
    binding = raw.get('source_binding')
    if not isinstance(binding, dict) or binding.get('status') != 'matches_export':
        limitations.append('尚未确认数据对应的实际投稿正文与导出版本一致。')

    def ratio(numerator, denominator):
        n, d = m[numerator], m[denominator]
        return {'numerator': n, 'denominator': d,
                'value': n / d if n is not None and d is not None and d > 0 else None}

    rates = {'ctr': ratio('read_count', 'display_count')}
    rates.update({key + '_per_read': ratio(key, 'read_count') for key in COUNTS[2:]})
    if all(value is None for value in m.values()):
        stage, action = 'data_unavailable', '等待可用数值并记录推荐状态，不能把缺失填成零。'
    elif m['display_count'] == 0:
        stage, action = 'no_recorded_exposure', '核对作品状态、统计截止时间与渠道，不能评价标题或正文效果。'
    elif m['read_count'] is None:
        stage, action = 'reading_count_missing', '补阅读人数，不能只按展现评价正文。'
    elif m['read_count'] == 0:
        stage, action = 'no_recorded_reading', '保留展现分母继续观察；可以检查展示材料，尚不能评价正文留存。'
    else:
        stage, action = 'reading_observed_unvalidated', '检查开篇承接与继续阅读；小样本只形成待验证编辑假设。'
    gaps = []
    for earlier, later in zip(COUNTS[:4], COUNTS[1:5]):
        # Display-to-read is a click decision, kept in CTR rather than timed reading gaps.
        if earlier == 'display_count':
            continue
        if m[earlier] is not None and m[later] is not None:
            gaps.append({'from': earlier, 'to': later, 'count_difference': m[earlier] - m[later],
                         'interpretation': '同条记录人数差，不能还原个体阅读路径或具体退出原因'})
    if any(m[key] is None for key in COUNTS[1:5]):
        limitations.append('部分时长阶段缺失，人数差只列已知相邻阶段，不跨缺失阶段补算。')
    return {'assessment_scope': 'descriptive_only', 'stage': stage, 'next_check': action,
            'sample_counts': m, 'rates': rates, 'timed_count_gaps': gaps,
            'publication_metadata_complete': not missing, 'limitations': limitations,
            'commercial_validation': 'unverified', 'automatic_learning': False}


def record_observation(project: StoryProject, raw: dict) -> tuple:
    row = normalize_observation(raw)
    row['source_binding'] = bind_source(project, row)
    row['diagnosis'] = diagnose_observation(row)
    row['observation_id'] = str(uuid4())
    row['recorded_at'] = now()
    path = project.root / 'feedback' / 'platform_records.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as output:
        output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    return path, row


def latest_diagnosis(project: StoryProject) -> dict:
    path = project.root / 'feedback' / 'platform_records.jsonl'
    if not path.exists():
        raise StoryError('尚无平台反馈记录')
    lines = [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not lines:
        raise StoryError('尚无平台反馈记录')
    try:
        row = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise StoryError('最后一条反馈记录损坏') from exc
    return {'selection': 'last_appended_not_latest_statistics_cutoff',
            'observation': row, 'diagnosis': diagnose_observation(row)}


def use_editorial_feedback(project: StoryProject, source: StoryProject,
                          observation_id: str) -> dict:
    """Explicitly carry one observation into a new experiment; no silent pooling."""
    records = source.root / 'feedback' / 'platform_records.jsonl'
    if not records.exists():
        raise StoryError('来源项目尚无反馈')
    try:
        rows = [json.loads(line) for line in records.read_text(encoding='utf-8').splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise StoryError('来源反馈记录损坏') from exc
    if any(not isinstance(row, dict) for row in rows):
        raise StoryError('来源反馈记录必须是JSON对象')
    matches = [row for row in rows if row.get('observation_id') == observation_id]
    if len(matches) != 1:
        raise StoryError('反馈ID不存在或重复；必须明确选择一条观察')
    row = matches[0]
    normalized = normalize_observation(row)
    diagnosis = diagnose_observation(row)
    options = []
    focus = {
        ('read_count', 'read_15s'): '检查开篇是否承接标题，入场是否清楚，背景是否打断正在发生的行动。',
        ('read_15s', 'read_30s'): '检查已有问题是否推进，人物选择是否带来新后果，避免重复解释。',
        ('read_30s', 'read_60s'): '检查后续冲突与生活细节是否增加期待，避免只有首句吸引人。',
    }
    for gap in diagnosis['timed_count_gaps']:
        if gap['count_difference'] > 0:
            options.append({'observed_gap': gap, 'editorial_hypothesis': focus[(gap['from'], gap['to'])]})
    payload = {
        'schema_version': 1, 'source_project': str(source.root.resolve()),
        'observation_id': observation_id, 'observation_hash': digest(row),
        'platform': normalized['platform'], 'entry_type': normalized['entry_type'],
        'statistics_cutoff': normalized['statistics_cutoff'],
        'observation_window': normalized['observation_window'],
        'aggregation': normalized['aggregation'], 'metric_scope': normalized['metric_scope'],
        'sample_counts': diagnosis['sample_counts'], 'rates': diagnosis['rates'],
        'experiment_options': options,
        'limitations': diagnosis['limitations'] + [
            '只携带指定观察；可能是多作品聚合，不能据此诊断某一篇或给题材排名。',
            '编辑试验假设可调整，不强制冲突、打脸或固定字数爽点；不以模型评分证明流量。',
            '发布条件和观察时长不一致时，不能把后续差异归因于修改。'],
        'commercial_validation': 'unverified', 'automatic_learning': False,
    }
    snapshot = {'payload': payload, 'snapshot_hash': digest(payload)}
    write_json(project.root / 'feedback' / 'editorial_context.json', snapshot)
    project.record_run({'event': 'editorial_feedback_selected', 'snapshot_hash': snapshot['snapshot_hash'],
                        'observation_id': observation_id})
    return snapshot


def load_editorial_context(project: StoryProject) -> dict:
    """Fail closed on damaged snapshots; independent review never consumes them."""
    snapshot = read_json(project.root / 'feedback' / 'editorial_context.json')
    if snapshot is None:
        return {}
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('payload'), dict)
            or snapshot.get('snapshot_hash') != digest(snapshot['payload'])
            or snapshot['payload'].get('schema_version') != 1):
        raise StoryError('编辑反馈快照损坏，请重新选择反馈')
    payload = snapshot['payload']
    try:
        path = Path(payload['source_project']) / 'feedback' / 'platform_records.jsonl'
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            raise StoryError('来源反馈记录必须是JSON对象')
        matches = [row for row in rows if row.get('observation_id') == payload['observation_id']]
        if len(matches) != 1 or digest(matches[0]) != payload['observation_hash']:
            raise StoryError('来源反馈版本已更改或缺失，请重新选择反馈')
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise StoryError('来源反馈不可读取，请重新选择反馈') from exc
    return payload
