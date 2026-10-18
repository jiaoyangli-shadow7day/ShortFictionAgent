"""Publication intent and evidence checks, separate from editorial quality and reach."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timezone, timedelta
from functools import lru_cache
import json
from pathlib import Path

from .contracts import StoryError, digest, visible_count

SHANGHAI = timezone(timedelta(hours=8))
DEFAULT_ROUTE = "fanqie_app_reading"
NARRATIVE_CATEGORY_CODES = {"custom", "romance", "campus_romance", "mystery", "science_fiction", "fantasy"}
STATUS_FIELDS = {
    "category_status": ("pending", "author_backend_confirmed", "platform_confirmed"),
    "category_fit_status": ("pending", "author_confirmed", "platform_confirmed", "mismatch"),
    "completion_status": ("planned", "complete"),
    "originality_status": ("pending", "confirmed", "not_original"),
    "first_publication_status": ("pending", "confirmed", "already_published"),
    "exclusive_status": ("pending", "confirmed", "nonexclusive"),
    "signing_method": ("undecided", "platform_submission", "editor_submission"),
    "platform_status": ("not_uploaded", "under_review", "approved", "rejected"),
    "signing_status": ("not_submitted", "under_review", "signed", "rejected"),
    "activity_status": ("not_applied", "under_evaluation", "selected", "rejected"),
}


@lru_cache(maxsize=1)
def _rules() -> dict:
    return json.loads((Path(__file__).parent / "data" / "publication_routes.json").read_text(encoding="utf-8"))


def publication_routes() -> dict:
    """Return a copy so callers cannot alter the shared official snapshot."""
    return deepcopy(_rules())


def _defaults(route_id: str = DEFAULT_ROUTE, *, intent_source: str = "explicit") -> dict:
    return {"schema_version": 1, "intent_source": intent_source, "route_id": route_id,
            "official_category": "", "activity": None, "signed_completed_at": None,
            "submission_guide_id": "",
            **{key: values[0] for key, values in STATUS_FIELDS.items()}}


def _timestamp(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise StoryError(f"{name} 必须是含时区的ISO日期时间")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StoryError(f"{name} 必须是含时区的ISO日期时间") from exc
    if result.tzinfo is None:
        raise StoryError(f"{name} 必须包含时区，活动按北京时间核验")
    return result.astimezone(SHANGHAI)


def _as_of(value: date | datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(SHANGHAI)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise StoryError("as_of 日期时间必须含时区")
        return value.astimezone(SHANGHAI)
    if isinstance(value, date):
        return datetime.combine(value, time(12), SHANGHAI)
    if isinstance(value, str) and len(value) == 10:
        try:
            return _as_of(date.fromisoformat(value))
        except ValueError as exc:
            raise StoryError("as_of 日期无效") from exc
    return _timestamp(value, "as_of")


def validate_publication_contract(raw: object, channels: list[str], length: dict) -> dict:
    """Validate explicit intent without changing story genre or inflating its length."""
    if not isinstance(raw, dict):
        raise StoryError("publication_contract 必须是映射")
    if not raw:
        return {}
    allowed = set(_defaults())
    unknown = set(raw) - allowed
    if unknown:
        raise StoryError(f"未知投稿合同字段: {sorted(unknown)}")
    result = _defaults(raw.get("route_id", DEFAULT_ROUTE))
    result.update(deepcopy(raw))
    from .submission_guide import validate_submission_guide_id
    result["submission_guide_id"] = validate_submission_guide_id(result["submission_guide_id"])
    if result["schema_version"] != 1 or isinstance(result["schema_version"], bool):
        raise StoryError("publication_contract.schema_version 仅支持1")
    if result["intent_source"] not in ("explicit", "channel_default"):
        raise StoryError("投稿目标 intent_source 无效")
    route = _rules()["routes"].get(result["route_id"]) if isinstance(result["route_id"], str) else None
    if route is None:
        raise StoryError(f"未知投稿路线: {result['route_id']}")
    if route["channel"] not in channels:
        raise StoryError("投稿路线与channels不符；番茄短故事目标须声明fanqie_short")
    for field_name, values in STATUS_FIELDS.items():
        if result[field_name] not in values:
            raise StoryError(f"投稿合同 {field_name} 状态无效")
    category = result["official_category"]
    if not isinstance(category, str) or len(category) > 80:
        raise StoryError("official_category 必须是作者后台实际主分类名称或空字符串")
    result["official_category"] = category.strip()
    if not result["official_category"] and result["category_status"] != "pending":
        raise StoryError("主分类为空时只能标待确认")
    if result["signed_completed_at"] is not None:
        result["signed_completed_at"] = _timestamp(result["signed_completed_at"], "signed_completed_at").isoformat()
    activity = result["activity"]
    if activity is not None:
        if not isinstance(activity, dict) or set(activity) - {"activity_id", "period", "program"}:
            raise StoryError("activity 须包含activity_id、period及可选program")
        activity_id = activity.get("activity_id")
        rules = _rules()["activities"].get(activity_id) if isinstance(activity_id, str) else None
        if rules is None or activity_id not in route["activity_ids"]:
            raise StoryError("活动与投稿路线不符或活动未核对")
        if str(activity.get("period")) != rules["period"]:
            raise StoryError("活动期次未核对；不能用旧期规则代替所选期次")
        program = activity.get("program", rules["default_program"])
        if program not in rules["programs"]:
            raise StoryError("活动program未核对")
        result["activity"] = {"activity_id": activity_id, "period": rules["period"], "program": program}
        # This is a writing floor. It still cannot stand in for the platform's count.
        if length.get("min", 0) < rules["word_min"]:
            raise StoryError(f"所选活动写作合同length.min须至少{rules['word_min']}；只设target不足以验收，平台计字仍需确认")
    return result


def resolve_publication_contract(contract: dict, *, channel: str | None = None) -> dict:
    raw = contract.get("publication_contract") or {}
    if raw:
        return validate_publication_contract(raw, contract.get("channels") or [], contract.get("length") or {})
    if "fanqie_short" in (contract.get("channels") or []) or channel == "fanqie_short":
        return _defaults(intent_source="channel_default")
    return {}


def publication_guidance(contract: dict) -> str:
    publication = resolve_publication_contract(contract)
    if not publication:
        return ""
    rules = _rules()
    route = rules["routes"][publication["route_id"]]
    lines = [f"投稿目标：{route['name']}；官方条件核对快照{rules['checked_at']}。",
             route["guidance"],
             "正式主分类以作者后台实际选项和内容主线为依据；男生情感不自动改成男生生活或现言甜宠。",
             "普通短故事教程范围6000—80000字，建议10000—30000字；不是统一流量门槛。",
             rules["counting"]["equivalence"],
             "发布、审核、签约、活动入选和实际收益分别记录；本地编辑通过不证明平台准入、推荐或保底。"]
    selected = publication.get("activity")
    if selected:
        activity = rules["activities"][selected["activity_id"]]
        program_name = {"general_rewards": "普通活动奖励", "high_guarantee_evaluation": "编辑高保底评估",
                        "activity_evaluation": "活动入选评估"}[selected["program"]]
        rights = activity['rights_requirements'][selected['program']]
        lines += [f"活动：{activity['name']}，期次{selected['period']}，目标为{program_name}。",
                  f"{activity['window_event']}窗口：{activity['starts_at']}至{activity['ends_at']}；导出时重新核验，过期不作为当前活动。",
                  f"本地写作合同length.min至少{activity['word_min']}可见字符，平台最低字数与活动档位按首次投稿实际计字单独核验。",
                  "、".join(activity["requirements"]),
                  "权利要求：" + ("独家原创首发，状态逐项确认。" if rights == "exclusive_original_first_publication"
                                   else "以实际签约合同及平台活动资格为准，不能推断全部非独家作品享有奖励。")]
        if activity["categories"]:
            lines.append("本期目录：" + "、".join(activity["categories"]) + "；真实主线及分类适配须编辑/运营确认。")
        definition = activity.get("category_definitions", {}).get(publication.get("official_category"))
        if definition:
            lines.append("所选分类要求：" + definition + "。")
        if activity.get("word_boundary"):
            boundary = activity["word_boundary"]
            lines.append(f"金品两万边界：奖励正文及图表{boundary['specific_reward_rules']}，介绍{boundary['introduction']}；{boundary['interpretation']}。")
    from .submission_guide import submission_guide_context
    guide = submission_guide_context(contract, "publication")
    if guide["enabled"]:
        lines += [f"生产与审稿参照编辑指南{guide['profile_id']}，版本{guide['version']}，来源{guide['source']['url']}。",
                  "语义要求进入方案、导语、正文和主审；篇幅与章节建议按适用稿型理解，具体格式仅所选编辑投稿适用。"]
        lines.extend("投稿资格信息：" + row["requirement"] for row in guide["administrative"])
    return "\n".join(lines)


def activity_window_status(activity: dict, *, as_of: date | datetime | str | None = None) -> str:
    moment = _as_of(as_of)
    if moment < _timestamp(activity["starts_at"], "starts_at"):
        return "not_started"
    if moment > _timestamp(activity["ends_at"], "ends_at"):
        return "expired"
    return "open"


def _platform_count(confirmation: object, body: str) -> dict | None:
    if confirmation is None:
        return None
    if not isinstance(confirmation, dict):
        raise StoryError("platform_confirmation 必须是映射")
    if set(confirmation) - {"content_hash", "source", "evidence_ref", "word_count", "first_submission_word_count", "counted_at"}:
        raise StoryError("platform_confirmation 包含未知字段")
    if confirmation.get("content_hash") != digest(body):
        raise StoryError("平台计字未绑定当前正文，不能复用旧版本计数")
    if confirmation.get("source") != "author_backend" or not isinstance(confirmation.get("evidence_ref"), str) or not confirmation["evidence_ref"].strip():
        raise StoryError("平台计字须有author_backend来源和evidence_ref")
    for field_name in ("word_count", "first_submission_word_count"):
        if field_name not in confirmation and field_name == "first_submission_word_count":
            continue
        value = confirmation.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise StoryError(f"平台{field_name}须是正整数")
    if "counted_at" in confirmation:
        _timestamp(confirmation["counted_at"], "counted_at")
    return deepcopy(confirmation)


def publication_check(body: str, contract: dict, *, category: str | None = None,
                      as_of: date | datetime | str | None = None,
                      platform_confirmation: dict | None = None, channel: str | None = None) -> dict:
    """Check recorded intent; ready never predicts admission, revenue, or distribution."""
    publication = resolve_publication_contract(contract, channel=channel)
    if not publication:
        return {"status": "not_applicable", "blockers": [], "pending": [], "checks": []}
    moment = _as_of(as_of)
    rules = _rules()
    selected = publication.get("activity")
    activity = rules["activities"][selected["activity_id"]] if selected else None
    checks = []

    def add(check_id: str, status: str, reason: str) -> None:
        checks.append({"id": check_id, "status": status, "reason": reason})

    count = visible_count(body)
    local_min = contract.get("length", {}).get("min", 0)
    local_max = contract.get("length", {}).get("max")
    if count < local_min:
        add("local_length", "mismatch", f"本地可见字符{count}不足作者写作合同最低{local_min}")
    elif local_max is not None and count > local_max:
        add("local_length", "mismatch", f"本地可见字符{count}超过作者写作合同上限{local_max}")
    else:
        add("local_length", "ready", f"本地可见字符{count}满足写作合同；包含标点、标题及导语，不代表平台计字")
    declared_category = publication.get("official_category", "")
    effective_category = category if category is not None else declared_category
    if not isinstance(effective_category, str):
        raise StoryError("投稿主分类必须是字符串")
    effective_category = effective_category.strip()
    if declared_category and category is not None and effective_category != declared_category:
        add("official_category", "mismatch", "导出分类与已选择的正式主分类不符；须修改合同并重新审查")
    elif not effective_category or effective_category in NARRATIVE_CATEGORY_CODES:
        add("official_category", "pending", "正式主分类待作者后台确认；故事题材标签不能冒充官方主分类")
    elif publication["category_status"] == "pending":
        add("official_category", "pending", "已记录候选主分类，待作者后台确认实际选项")
    else:
        add("official_category", "ready", "已记录作者后台/平台确认的主分类")
    confirmation = _platform_count(platform_confirmation, body)
    expected = activity or rules["ordinary_short_story"]
    platform_count = None
    if confirmation:
        platform_count = confirmation.get("first_submission_word_count") if selected else confirmation["word_count"]
        if selected and platform_count is None:
            add("platform_word_count", "pending", "当前平台计字已记录，活动首次投稿计字仍待确认")
        elif not expected.get("word_min", expected.get("min")) <= platform_count <= expected.get("word_max", expected.get("max")):
            add("platform_word_count", "mismatch" if selected else "pending", f"平台计字{platform_count}不符所选目标已核对字数范围；普通教程范围不能替代平台发表或推荐判断")
        else:
            add("platform_word_count", "ready", f"已按当前正文后台证据核对平台计字{platform_count}")
    else:
        add("platform_word_count", "pending", "本地可见字符不等于平台计字，最终字数待作者后台确认")
    if publication["completion_status"] != "complete":
        add("completion", "pending", "完整完结状态待确认")
    else:
        add("completion", "ready", "合同已记录完整完结，正文审查另行验证")
    from .submission_guide import submission_guide_context
    guide_context = submission_guide_context(contract, "publication")
    if guide_context.get("administrative"):
        add("editor_guarantee_qualification", "pending",
            "已记录编辑指南的首发与历史稿费条件；当前写作合同不含经核验的历史成绩证据，保底资格须另行评估，不据正文通过宣称具备资格")
    window_status = None
    if selected:
        window_status = activity_window_status(activity, as_of=moment)
        signed_at = publication.get("signed_completed_at")
        if signed_at:
            signed_moment = _timestamp(signed_at, "signed_completed_at")
            if signed_moment > moment:
                add("activity_window", "mismatch", "已记录签约完成日期在核验时点之后")
            elif not _timestamp(activity["starts_at"], "starts_at") <= signed_moment <= _timestamp(activity["ends_at"], "ends_at"):
                add("activity_window", "mismatch", "实际签约完成时间在本期活动窗口外")
            else:
                add("activity_window", "ready", "实际签约完成时间在所选期次窗口内；活动关闭后仅核验这项历史记录")
        elif window_status == "expired":
            add("activity_window", "mismatch", "所选活动窗口已过期，未有窗口内签约完成证据；不能继续称当前活动")
        else:
            add("activity_window", "pending", "活动尚未开始，需等待并重查规则" if window_status == "not_started" else "窗口开放，实际签约完成时间待确认")
        if (activity["categories"] and effective_category and effective_category not in NARRATIVE_CATEGORY_CODES
                and effective_category not in activity["categories"]):
            add("activity_category", "mismatch", "正式/候选分类不在本期活动目录；不能通过重贴标签改变内容主线")
        elif activity["categories"]:
            fit = publication["category_fit_status"]
            add("activity_category", "mismatch" if fit == "mismatch" else "ready" if fit == "platform_confirmed" and effective_category else "pending",
                "活动目录及内容主线适配须编辑/运营评估，包含男主恋爱不自动等于男生生活")
        if activity["rights_requirements"][selected["program"]] == "exclusive_original_first_publication":
            for name in ("originality_status", "first_publication_status", "exclusive_status"):
                value = publication[name]
                add(name, "ready" if value == "confirmed" else "pending" if value == "pending" else "mismatch",
                    "本项活动须独家原创首发；按实际权利与历史发表记录确认")
        else:
            add("rights", "pending", "公告未单列全部奖励的非独家资格；按实际签约合同与平台活动确认")
        add("signing", "ready" if publication["signing_status"] == "signed" else "mismatch" if publication["signing_status"] == "rejected" else "pending",
            "签约状态单独记录，平台上传或审核通过不等于签约")
        add("activity_selection", "ready" if publication["activity_status"] == "selected" else "mismatch" if publication["activity_status"] == "rejected" else "pending",
            "活动入选以已发生的平台评估结果为据；字数达标不自动入选、保底或获得流量")
        if platform_count is not None and activity.get("unexplained_word_counts", {}).get("min", 0) <= platform_count <= activity.get("unexplained_word_counts", {}).get("max", -1):
            add("activity_word_tier", "pending", "公告未解释24901—24999字归档，需平台确认，不自行补齐边界")
        if platform_count == 20000 and selected["activity_id"] == "ip_gold":
            add("activity_word_boundary", "pending", "奖励正文及图表写至少20000，介绍写大于20000；保留差异，请平台确认恰好两万边界")
    blockers = [row["id"] for row in checks if row["status"] == "mismatch"]
    pending = [row["id"] for row in checks if row["status"] == "pending"]
    return {"status": "mismatch" if blockers else "pending" if pending else "ready",
            "meaning": "记录的投稿目标条件检查；不代表平台准入、推荐、保底、收益或改编预测",
            "route_id": publication["route_id"],
            "official_category": effective_category if effective_category and effective_category not in NARRATIVE_CATEGORY_CODES else None,
            "candidate_category": effective_category or None,
            "activity": selected, "activity_window_status": window_status,
            "as_of": moment.isoformat(), "rules_checked_at": rules["checked_at"],
            "local_count": {"unit": "visible_char_v1", "value": count},
            "platform_confirmation": confirmation, "states": {name: publication[name] for name in STATUS_FIELDS},
            "submission_guide": guide_context,
            "blockers": blockers, "pending": pending, "checks": checks,
            "sources": [rules["ordinary_short_story"]["source"]] + ([activity["source"]] if activity else [])
                       + ([guide_context["source"]["url"]] if guide_context["enabled"] else [])}
