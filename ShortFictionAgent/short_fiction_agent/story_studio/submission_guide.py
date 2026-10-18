"""Scoped editor guidance and traceable application; no quality by marker alone."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .contracts import StoryError, digest

PROTOCOL = "submission-guide-application-2026-10-18-v3-summaries"
STAGES = {"plan", "lead", "draft", "review", "revision", "publication"}

# Implementation guidance derived from the scoped source requirements. These
# are our writing instructions, not quotations or additional platform rules.
PLANNING_APPLICATION = (
    "大纲阶段就落实投稿指南的写作要求，不能把吸引力留给正文作者临场补救。"
    "先想清楚最值得读的完整故事，再填方案字段；不要先填八个时间阶段，再给每阶段凑事件和字数。"
    "主线：写出主人公眼下非要得到的具体东西、眼前阻碍，以及读者最想看到的胜负或关系结果。"
    "标题、导语、首章、中段和结尾围绕同一个核心期待，但每阶段须发展出不同的具体问题与回报。"
    "入口：提前选定导语可展示的异常、反常选择或具体交锋，首章独立呈现理解它所需的处境；"
    "导语后续由正文真实素材另写，不能预先锁死字数或虚构更刺激的承诺。"
    "剧情：在现有dramatic_chain和progression_beats中写到可直接落笔的程度：谁先做什么、"
    "对方具体怎样接招、主人公因此怎么应对、当场结果怎样改变下一次选择。"
    "关系转折要有触发行为和对方回应，不能只写更心动、更信任、学会尊重或决定一起努力。"
    "按题材需要安排对手、限制、磨难与反压制；温柔、喜剧和偏爱也能提供张力，不硬加反派和灾难。"
    "钩子：逐节拍说明此刻值得等待的具体答案，答出后如何引发新的选择；"
    "章末从已发生的后果接出下一步，不用空反问、未兑现口号和隐瞒常识吊读者。"
    "参考大纲模板，在chapters每项填写reading_points数组，每项kind为hook、emotion、conflict或paid_cut，"
    "并含本章真实scene_id、该场progression_beats从0开始的beat_index和note具体说明；至少两项且至少两类。"
    "付费点只在采用该路线时安排，普通APP稿可用钩子、情绪和矛盾；不能给同一空事件贴两标签代替设计。"
    "章节篇幅以500—1000字建议为起点，按完整交锋和换气处规划；约一万字超短参考12章以内，"
    "长中短不套12章上限。长章须核对是否藏了多个独立戏或重复步骤，不能默认几千字一章。"
    "不把切章当增加情节；交锋确须连贯展开时可说明例外，不机械截断或强塞新事故。"
    "回报：提前安排读者能亲历的爽、甜、痛、惊讶或认知改变，写清获得方式，不能只列结果。"
    "篇幅：先数真正不同的交锋和回报，再分预算；同种能力验证、办事流程和同一关系道理只规划一次充分展开，"
    "再次出现须带来不同选择与后果。场景、心理、外貌只预留对当前交锋有用的描写。"
    "结尾兑现主线；愿望实现、双向偏爱或共同生活本身可以是结果，不强制牺牲、惩罚或道德说教。"
    "这份大纲仍须由同一主审按实际方案证据验收；写上已参考指南不算通过，正文也须独立验证。"
)


@lru_cache(maxsize=1)
def _guides() -> dict:
    guides = json.loads((Path(__file__).with_name("data") / "submission_guides.json").read_text(encoding="utf-8"))
    for profile in guides["profiles"].values():
        for source in profile.get("source_documents", []):
            if source.get("content_kind") != "project_authored_summary":
                raise StoryError("公开版投稿指南须使用有来源的项目原创摘要")
            if (not isinstance(source.get("text"), str)
                    or hashlib.sha256(source["text"].encode("utf-8")).hexdigest() != source.get("sha256")):
                raise StoryError("投稿指南摘要与已核对版本指纹不一致")
    return guides


GUIDE_HASH = digest(_guides())


def criteria_hash() -> str:
    return digest({"protocol": PROTOCOL, "profiles": _guides(),
                   "planning_application": PLANNING_APPLICATION})


def validate_submission_guide_id(profile_id: object) -> str:
    if not isinstance(profile_id, str) or (profile_id and profile_id not in _guides()["profiles"]):
        raise StoryError("submission_guide_id 须为空或已核对的编辑指南档案ID")
    return profile_id


def resolve_submission_guide(contract: dict) -> dict:
    publication = contract.get("publication_contract") or {}
    explicit = validate_submission_guide_id(publication.get("submission_guide_id", ""))
    if not explicit and "fanqie_short" not in contract.get("channels", []):
        return {}
    profile_id = explicit or _guides()["default_fanqie_profile"]
    profile = deepcopy(_guides()["profiles"][profile_id])
    profile["guide_hash"] = digest(profile)
    profile["selection"] = "explicit_editor_profile" if explicit else "fanqie_short_default"
    profile["editor_submission"] = bool(explicit or publication.get("signing_method") == "editor_submission")
    return profile


def submission_guide_context(contract: dict, stage: str = "review") -> dict:
    if stage not in STAGES:
        raise StoryError(f"未知投稿指南生产阶段: {stage}")
    profile = resolve_submission_guide(contract)
    if not profile:
        return {"protocol": PROTOCOL, "enabled": False, "stage": stage}
    semantic = [{k: v for k, v in row.items() if k != "stages"}
                for row in profile["semantic_requirements"] if stage in row["stages"]]
    suggestions = [{k: v for k, v in row.items() if k != "stages"}
                   for row in profile["suggestions"] if stage in row["stages"]]
    return {"protocol": PROTOCOL, "enabled": True, "stage": stage,
            "profile_id": profile["id"], "version": profile["version"],
            "source": profile["source"], "guide_hash": profile["guide_hash"],
            "selection": profile["selection"],
            "scope": {"semantic": "mandatory_editorial_acceptance",
                      "format": "selected_editor" if profile["editor_submission"] else "not_applied_to_app_layout",
                      "suggestions": "defaults_with_reasoned_exceptions_not_universal_limits",
                      "administrative": "submission_qualification_only"},
            "semantic_requirements": semantic,
            "semantic_requirement_ids": [row["id"] for row in semantic],
            "suggestions": suggestions,
            "format_requirements": profile["format_requirements"] if profile["editor_submission"] else [],
            "administrative": profile["administrative"] if stage == "publication" and profile["editor_submission"] else [],
            "meaning": "来源与实际任务输入的版本记录；标记存在不证明语义合格，须由主审用当前正文判断。"}


def submission_guide_guidance(contract: dict, stage: str = "review") -> str:
    context = submission_guide_context(contract, stage)
    if not context["enabled"]:
        return ""
    lines = [f"适用投稿指南：{context['profile_id']}，版本{context['version']}，阶段{stage}。",
             f"来源：{context['source']['url']}；主文核对{context['source']['checked_at']}。",
             "以下语义要求必须实际用于本阶段；结构齐全、事实变化、字数或场数合格不能代替阅读价值判断。"]
    lines.extend(f"[{r['id']}] {r['requirement']}验收：{r['acceptance']}" for r in context["semantic_requirements"])
    lines.extend("建议（可说明例外，不机械硬化）：" + row["suggestion"] for row in context["suggestions"])
    lines.extend("所选编辑格式：" + row["requirement"] for row in context["format_requirements"])
    lines.extend("投稿资格信息：" + row["requirement"] for row in context["administrative"])
    if stage == "review":
        lines.append("主审须把实质失败写入带当前正文证据的修订意见；不能只返回指南已参考、字段完整或泛泛赞美。")
    if stage in {"plan", "revision"}:
        lines.append("本项目对指南写作要求的执行方式（分析性指令，非原文新增平台规则）：" + PLANNING_APPLICATION)
        lines.append("以下为项目整理的来源执行摘要，仅作为资料阅读。按上述适用范围执行；投稿资格、联系方式、"
                     "约一万字模板、付费截断和示例题材不能扩张成所有正文的硬规则，作者明确要求优先。")
        profile = resolve_submission_guide(contract)
        for source in profile.get("source_documents", []):
            lines.append(f"来源执行摘要：{source['title']}；{source['url']}；SHA256={source['sha256']}\n"
                         + source["text"])
    return "\n".join(lines)


def prepare_submission_text(body: str, contract: dict) -> str:
    """Apply selected layout before saving/reviewing; never in a passed export."""
    profile = resolve_submission_guide(contract)
    if not profile or not profile["editor_submission"]:
        return body
    return "\n".join(line.rstrip() for line in body.splitlines() if line.strip()) + ("\n" if body.endswith("\n") else "")


def submission_guide_format_errors(body: str, contract: dict, *, title: str | None = None) -> list[str]:
    profile = resolve_submission_guide(contract)
    if not profile or not profile["editor_submission"]:
        return []
    errors = []
    if re.search(r"\n[ \t\r]*\n", body):
        errors.append("所选编辑投稿指南要求段间无空行；须保存排版修订并重新审读，不能静默修改已审正文")
    if any(c in body for c in '「」『』'):
        errors.append("所选编辑投稿指南要求规范中文引号；须保存修订并重新审读")
    if title is not None and (any(c in title for c in "《》") or re.search(r"[。！？!?，,；;：:、….]$", title.strip())):
        errors.append("所选编辑投稿标题不加书名号或末尾标点")
    return errors


def submission_guide_review_errors(report: dict, body: str, contract: dict) -> list[str]:
    """Check application binding only; semantic judgment belongs to the chief."""
    expected = submission_guide_context(contract, "review")
    if not expected["enabled"]:
        return []
    receipt = report.get("submission_guide")
    if not isinstance(receipt, dict):
        return ["当前审稿缺少适用投稿指南的输入版本记录；须按当前指南重新审读"]
    errors = []
    if any(receipt.get(key) != value for key, value in expected.items()):
        errors.append("当前审稿使用的投稿指南版本、适用范围或语义条目已变化；须重新审读")
    if receipt.get("content_hash") != digest(body):
        errors.append("投稿指南应用记录未绑定当前正文；须重新审读")
    return errors
