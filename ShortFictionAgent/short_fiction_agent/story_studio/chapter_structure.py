"""Explicit chapter units for writing and reader review.

Generation chunks are transport details. A chapter is an authored, ordered group
of scenes. Legacy plans remain inspectable without pretending that their old
single heading supplied a usable chapter map.
"""
from __future__ import annotations

from .contracts import StoryError


PROTOCOL = 'explicit-chapter-scene-map-v1'
READING_POINT_KINDS = frozenset({'hook', 'emotion', 'conflict', 'paid_cut'})


def chapters(plan: dict, allow_legacy: bool = True) -> list[dict]:
    """Return source-bound chapter units without mutating the source plan.

    ``scene_indices`` are zero-based. New production must call with
    ``allow_legacy=False``. Missing legacy maps yield one review unit per scene,
    explicitly labelled ``legacy_inferred``; malformed explicit maps are never
    repaired or silently inferred.
    """
    scenes = plan.get('scenes')
    if (not isinstance(scenes, list) or not scenes
            or any(not isinstance(scene, dict) or not isinstance(scene.get('scene_id'), str)
                   or not scene['scene_id'].strip() for scene in scenes)):
        raise StoryError('章节映射缺少实际场景')
    ids = [scene['scene_id'] for scene in scenes]
    if len(set(ids)) != len(ids):
        raise StoryError('章节映射的实际scene_id重复')
    if 'chapters' not in plan:
        if not allow_legacy:
            raise StoryError('新方案缺少chapters章节映射，不能把模型输出分块当章节')
        return [{'chapter_id': f'legacy-chapter-{index + 1:04d}',
                 'title': f'第{index + 1}章', 'scene_ids': [scene_id],
                 'scene_indices': [index], 'legacy_inferred': True}
                for index, scene_id in enumerate(ids)]

    mapping = plan['chapters']
    if not isinstance(mapping, list) or not mapping:
        raise StoryError('chapters须为非空章节映射数组')
    result, chapter_ids, covered = [], set(), 0
    for row in mapping:
        if (not isinstance(row, dict)
                or not isinstance(row.get('chapter_id'), str) or not row['chapter_id'].strip()
                or not isinstance(row.get('title'), str) or not row['title'].strip()
                or not isinstance(row.get('scene_ids'), list) or not row['scene_ids']
                or any(not isinstance(key, str) for key in row['scene_ids'])):
            raise StoryError('章节映射须含chapter_id、title和非空scene_ids')
        if row['chapter_id'] in chapter_ids:
            raise StoryError('章节映射的chapter_id重复')
        keys = row['scene_ids']
        if keys != ids[covered:covered + len(keys)]:
            raise StoryError('章节映射须按实际场景顺序连续覆盖，每个scene_id恰好一次')
        result.append({'chapter_id': row['chapter_id'], 'title': row['title'],
                       'scene_ids': list(keys),
                       'scene_indices': list(range(covered, covered + len(keys))),
                       'legacy_inferred': False})
        covered += len(keys)
        chapter_ids.add(row['chapter_id'])
    if covered != len(ids):
        raise StoryError('章节映射遗漏实际场景，每个scene_id须恰好覆盖一次')
    return result


def validate_outline_points(plan: dict) -> None:
    """Bind a selected guide's new outline points to real chapter beats.

    This checks reference completeness, not whether the labelled moment is
    compelling fiction. Legacy chapter inspection intentionally does not call
    it. Missing points are never inferred from scene fields or later chapters.
    """
    units = chapters(plan, allow_legacy=False)
    scene_map = {scene['scene_id']: scene for scene in plan['scenes']}
    labels = READING_POINT_KINDS | {'钩子', '情绪', '矛盾', '冲突', '付费点', '付费卡点'}
    for index, unit in enumerate(units):
        path = f'chapters[{index}].reading_points'
        points = plan['chapters'][index].get('reading_points')
        if not isinstance(points, list) or len(points) < 2:
            raise StoryError(f'{path}须至少两项实际阅读点，不能从旧方案或场景补值')
        kinds = set()
        for point_index, point in enumerate(points):
            point_path = f'{path}[{point_index}]'
            if (not isinstance(point, dict) or not isinstance(point.get('kind'), str)
                    or point['kind'] not in READING_POINT_KINDS):
                raise StoryError(f'{point_path}.kind须为hook/emotion/conflict/paid_cut')
            note = point.get('note')
            if not isinstance(note, str) or not note.strip() or note.strip() in labels:
                raise StoryError(f'{point_path}.note须非空具体说明，不能只写阅读点标签')
            scene_id, beat_index = point.get('scene_id'), point.get('beat_index')
            if not isinstance(scene_id, str) or scene_id not in unit['scene_ids']:
                raise StoryError(f'{point_path}.scene_id须引用本章实际场景，不能跨章或引用未来场景')
            beats = scene_map[scene_id].get('progression_beats')
            if (not isinstance(beats, list) or type(beat_index) is not int
                    or not 0 <= beat_index < len(beats) or not isinstance(beats[beat_index], dict)):
                raise StoryError(f'{point_path}.beat_index须引用该场实际progression_beats节拍（从0开始）')
            kinds.add(point['kind'])
        if len(kinds) < 2:
            raise StoryError(f'{path}须至少两类kind，同类标签重复不能代替设计')
