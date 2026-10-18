"""Compact causal blueprints for micro stories."""

from __future__ import annotations

from .contracts import StoryError


def normalize_blueprint(blueprint: dict) -> dict:
    """Accept common list-item JSON variants without another model call."""
    if not isinstance(blueprint, dict):
        return blueprint
    result = dict(blueprint)
    for key in ("characters", "locations"):
        items = result.get(key)
        if isinstance(items, list):
            result[key] = [
                (str(item.get("name") or "") +
                 ("：" + str(item["role"]) if item.get("role") else ""))
                if isinstance(item, dict) else item for item in items
            ]
    return result


def validate_blueprint(blueprint: dict) -> None:
    if not isinstance(blueprint, dict) or any(not str(blueprint.get(key) or "").strip()
                                              for key in ("question", "answer", "mechanism", "cost")):
        raise StoryError("因果蓝图缺少主问题、答案、机制或代价")
    for key, maximum in (("characters", 3), ("locations", 2)):
        items = blueprint.get(key)
        if not isinstance(items, list) or not 1 <= len(items) <= maximum or any(
            not isinstance(item, str) or not item.strip() for item in items
        ):
            raise StoryError(f"因果蓝图 {key} 数量或内容无效")
    objects = blueprint.get("objects")
    if not isinstance(objects, list) or len(objects) > 3 or any(
        not isinstance(item, dict) or any(not str(item.get(key) or "").strip()
                                           for key in ("name", "holder_start", "holder_end", "rule"))
        for item in objects
    ):
        raise StoryError("因果蓝图物件身份或规则无效")
    beats = blueprint.get("beats")
    if not isinstance(beats, list) or not 1 <= len(beats) <= 3:
        raise StoryError("微型故事因果蓝图必须有1到3个节拍")
    required = ("scene_id", "time", "place", "object_state", "action", "evidence",
                "inference", "choice", "consequence")
    if any(not isinstance(beat, dict) or any(not str(beat.get(key) or "").strip()
                                             for key in required) or
           not isinstance(beat.get("budget"), int) or beat["budget"] <= 0
           for beat in beats):
        raise StoryError("因果蓝图节拍缺少行动、证据、推断、选择或后果")
    if len({str(beat["scene_id"]) for beat in beats}) != len(beats):
        raise StoryError("因果蓝图场景 ID 重复")
    if any(not isinstance(beat.get("emotional_change"), str) or
           not beat["emotional_change"].strip() for beat in beats):
        raise StoryError("因果蓝图缺少独立情绪变化；请重新规划，不能用事实推断代填")


def blueprint_to_plan(blueprint: dict) -> dict:
    validate_blueprint(blueprint)
    beats = blueprint["beats"]
    scenes = []
    for index, beat in enumerate(beats):
        scenes.append({"scene_id": beat["scene_id"], "purpose": beat["action"],
                       "goal": blueprint["question"], "pressure": blueprint["mechanism"],
                       "choice": beat["choice"], "consequence": beat["consequence"],
                       "new_information": beat["evidence"] + "；" + beat["inference"] +
                                          "；物件状态：" + beat["object_state"],
                       "emotional_change": beat["emotional_change"], "budget": beat["budget"],
                       "dependencies": [beats[index - 1]["scene_id"]] if index else []})
    return {"scenes": scenes, "main_question": blueprint['question'],
            "promises": [{"promise": blueprint["question"],
                          "payoff_scene": beats[-1]["scene_id"], "required": True}],
            "ending_resolution": blueprint["answer"] + "；代价：" + blueprint["cost"],
            "continuity_map": {
                "timeline": [f"{beat['scene_id']}（{beat['time']}）：{beat['action']}→{beat['consequence']}"
                             for beat in beats],
                "locations": [f"{beat['scene_id']}：{beat['place']}；{beat['object_state']}" for beat in beats],
                "knowledge": [f"{beat['scene_id']}：{beat['evidence']}→{beat['inference']}" for beat in beats],
                "motives": [f"{beat['scene_id']}：{beat['choice']}" for beat in beats] +
                           ["最终代价：" + blueprint["cost"]]},
            "blueprint": blueprint}
