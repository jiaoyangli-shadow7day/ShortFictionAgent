"""Apply small, auditable repairs to a rejected scene plan."""
from __future__ import annotations

import copy
import re

from .contracts import StoryError
from .contracts import digest


_SCENE_PATH = re.compile(r"^(?:plan\.)?scenes\[(\d+)\]\.(purpose|goal|pressure|choice|consequence|new_information|emotional_change|budget|dependencies|progression_beats)$")
_BEAT_PATH = re.compile(r"^(?:plan\.)?scenes\[(\d+)\]\.progression_beats\[(\d+)\]\.(reader_question|action|change|reward|next_expectation)$")
_CONTINUITY_PATH = re.compile(r"^(?:plan\.)?continuity_map\.(timeline|locations|knowledge|motives)\[(\d+)\]$")
_CONTINUITY_FIELD = re.compile(r"^(?:plan\.)?continuity_map\.(timeline|locations|knowledge|motives)$")
_TOP_LEVEL = {"ending_resolution", "promises", "continuity_map"}
_BINDING_KEYS = {"candidate_hash", "opening_hash", "contract_hash", "planning_hash"}


def substantive_change(before: dict, after: dict) -> bool:
    content = lambda plan: {key: value for key, value in plan.items() if key not in _BINDING_KEYS}
    return digest(content(before)) != digest(content(after))


def can_edit_path(plan: dict, path: str) -> bool:
    if not isinstance(path, str):
        return False
    scene = _SCENE_PATH.fullmatch(path)
    beat = _BEAT_PATH.fullmatch(path)
    continuity = _CONTINUITY_PATH.fullmatch(path)
    field = _CONTINUITY_FIELD.fullmatch(path)
    if scene:
        index = int(scene.group(1))
        return index < len(plan.get("scenes", [])) and scene.group(2) in plan["scenes"][index]
    if beat:
        index, beat_index = int(beat.group(1)), int(beat.group(2))
        return (index < len(plan.get("scenes", [])) and
                beat_index < len(plan["scenes"][index].get("progression_beats", [])) and
                beat.group(3) in plan["scenes"][index]["progression_beats"][beat_index])
    if continuity:
        rows = plan.get("continuity_map", {}).get(continuity.group(1), [])
        return isinstance(rows, list) and int(continuity.group(2)) < len(rows)
    if field:
        return field.group(1) in plan.get("continuity_map", {})
    return path.removeprefix("plan.") in _TOP_LEVEL and path.removeprefix("plan.") in plan


def apply(plan: dict, response: dict, issues: list[dict]) -> dict:
    edits = response.get("field_edits") if isinstance(response, dict) else None
    if not isinstance(edits, list) or not 1 <= len(edits) <= 20:
        raise StoryError("方案局部修复缺少有效 field_edits")
    required_paths = {issue.get("path") for issue in issues if isinstance(issue, dict)}
    if not required_paths or any(not isinstance(path, str) for path in required_paths):
        raise StoryError("方案局部修复缺少原审查路径")
    result, seen = copy.deepcopy(plan), set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "replacement"}:
            raise StoryError("方案局部修复字段无效")
        path, value = edit["path"], edit["replacement"]
        if not isinstance(path, str) or path in seen:
            raise StoryError("方案局部修复路径重复或无效")
        seen.add(path)
        scene = _SCENE_PATH.fullmatch(path)
        beat = _BEAT_PATH.fullmatch(path)
        continuity = _CONTINUITY_PATH.fullmatch(path)
        if scene:
            index, field = int(scene.group(1)), scene.group(2)
            if index >= len(result["scenes"]):
                raise StoryError("方案局部修复场景索引无效")
            original = result["scenes"][index].get(field)
            if type(value) is not type(original) or value == original:
                raise StoryError("方案局部修复类型不符或没有实际变化")
            result["scenes"][index][field] = value
        elif beat:
            index, beat_index, field = int(beat.group(1)), int(beat.group(2)), beat.group(3)
            if index >= len(result["scenes"]) or beat_index >= len(result["scenes"][index].get("progression_beats", [])):
                raise StoryError("方案局部修复节拍索引无效")
            original = result["scenes"][index]["progression_beats"][beat_index].get(field)
            if type(value) is not type(original) or value == original:
                raise StoryError("方案局部修复节拍类型不符或没有实际变化")
            result["scenes"][index]["progression_beats"][beat_index][field] = value
        elif continuity:
            field, index = continuity.group(1), int(continuity.group(2))
            rows = result.get("continuity_map", {}).get(field, [])
            if not isinstance(rows, list) or index >= len(rows):
                raise StoryError("方案局部修复事实表索引无效")
            original = rows[index]
            if type(value) is not type(original) or value == original:
                raise StoryError("方案局部修复事实表类型不符或没有实际变化")
            rows[index] = value
        elif _CONTINUITY_FIELD.fullmatch(path):
            field = _CONTINUITY_FIELD.fullmatch(path).group(1)
            original = result.get("continuity_map", {}).get(field)
            if type(value) is not type(original) or value == original:
                raise StoryError("方案局部修复事实表字段类型不符或没有实际变化")
            result["continuity_map"][field] = value
        elif path.removeprefix("plan.") in _TOP_LEVEL:
            field = path.removeprefix("plan.")
            if type(value) is not type(result.get(field)) or value == result[field]:
                raise StoryError("方案局部修复顶层字段类型不符或没有实际变化")
            result[field] = value
        else:
            raise StoryError("方案局部修复修改了未经授权的字段")
    canonical = lambda path: path[5:] if path.startswith("plan.") else path
    if not {canonical(path) for path in required_paths}.issubset({canonical(path) for path in seen}):
        raise StoryError("方案局部修复没有逐项处理审查指出的路径")
    return result
