"""Render an optional author proposal from existing, bound planning artifacts."""

from __future__ import annotations

from copy import deepcopy

from .contracts import StoryError, digest
from .publication import resolve_publication_contract, publication_routes
from .storage import StoryProject, now, read_json, write_json, write_text
from .workflow import StoryWorkflow
from .scene_planning import main_question

PROTOCOL = "story-plan-preview-v1"
TRIAL_NOTICE = "写前试写，尚未按完成正文核验，不能作为正式投稿导语。"
PLATFORM_NOTICE = "平台实际计字、主分类、审核、签约、活动入选及流量以平台记录为准；本方案不预测结果。"


def _context(project: StoryProject) -> dict:
    # Reuse the workflow's complete source, feedback and comparison validation.
    # This path only reads and validates planning inputs; it never calls a model.
    candidate, opening = StoryWorkflow(project, None)._selection()
    return {"contract": project.brief().effective_contract(), "candidate": candidate,
            "opening": opening, "selection": read_json(project.root / "selection.json"),
            "candidates": read_json(project.root / "candidates.json"),
            "openings": read_json(project.root / "openings.json"),
            "comparison": read_json(project.root / "candidate_comparison.json"),
            "opening_comparison": read_json(project.root / "opening_comparison.json"),
            "editorial_feedback": read_json(project.root / "editorial_feedback.json")}


def _bindings(project: StoryProject, plan: dict, context: dict) -> dict:
    if not isinstance(plan, dict):
        raise StoryError("方案预览需要已保存的场景方案")
    for key, value in (("contract_hash", context["contract"]), ("candidate_hash", context["candidate"]),
                       ("opening_hash", context["opening"])):
        if plan.get(key) != digest(value) or context["selection"].get(key) != digest(value):
            raise StoryError("场景方案与当前合同、选题或开头绑定不符；请重新plan")
    if plan.get("selection_hash") and plan["selection_hash"] != digest(context["selection"]):
        raise StoryError("场景方案的选题记录绑定已失效")
    saved_plan = read_json(project.root / "plan" / "scenes.json")
    if saved_plan is None or digest(saved_plan) != digest(plan):
        raise StoryError("方案预览与已保存的大纲版本不符")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes or any(not isinstance(scene, dict) for scene in scenes):
        raise StoryError("方案预览缺少实际场景")
    receipt = read_json(project.root / "plan" / "preflight_receipt.json")
    if plan.get("planning_hash") and (not isinstance(receipt, dict)
            or receipt.get("decision") != "pass" or receipt.get("plan_hash") != digest(plan)
            or receipt.get("planning_hash") != plan["planning_hash"]):
        raise StoryError("场景方案缺少当前版本的通过预审记录")
    return {"contract_hash": digest(context["contract"]), "selection_hash": digest(context["selection"]),
            "candidate_hash": digest(context["candidate"]), "opening_hash": digest(context["opening"]),
            "candidates_hash": digest(context["candidates"]), "openings_hash": digest(context["openings"]),
            "comparison_hash": digest(context["comparison"]),
            "opening_comparison_hash": digest(context["opening_comparison"]),
            "editorial_feedback_hash": digest(context["editorial_feedback"]),
            "plan_hash": digest(plan), "preflight_receipt_hash": digest(receipt)}


def _proposal(project: StoryProject, plan: dict, context: dict, bindings: dict) -> dict:
    candidate, contract, opening = context["candidate"], context["contract"], context["opening"]
    trial = candidate.get("provisional_lead") or {}
    lead_text = trial.get("text") if isinstance(trial, dict) else None
    blueprint = plan.get("blueprint") or {}
    question = main_question(plan, required=True)
    scenes = deepcopy(plan["scenes"])
    if not question:
        question = next((beat.get("reader_question") for scene in scenes
                         for beat in (scene.get("progression_beats") or [])
                         if isinstance(beat, dict) and beat.get("reader_question")), "")
    turns = candidate.get("turns")
    missing = []
    if isinstance(turns, list) and turns:
        turns = deepcopy(turns)
        turn_basis = "selected_entry_pack"
    else:
        # A scene summary is not an independently judged dramatic turn.
        turns = []
        turn_basis = "not_supplied"
        missing.append("原候选没有三次转折样包；实际场景另列，不补造转折或假称已完成入口审读。")
    if not lead_text:
        lead_text = str(opening.get("text") or "")
        lead_basis = "selected_opening"
    else:
        lead_basis = "selected_entry_pack"
    if not question:
        missing.append("原候选没有具体追读问题，当前仅记录候选承诺。")
    comparison = context["comparison"] or {}
    selected_review = next((row for row in comparison.get("comparisons", [])
                            if str(row.get("candidate_id")) == str(candidate.get("id"))), {})
    publication = resolve_publication_contract(contract)
    route = publication_routes()["routes"].get(publication.get("route_id"), {})
    publication_preview = {"route_id": publication.get("route_id"), "name": route.get("name") or "通用文本",
                           "channels": contract.get("channels") or ["generic_text"],
                           "official_category": publication.get("official_category") or None,
                           "activity": publication.get("activity"), "platform_validation": "unverified"}
    selected_activity = publication.get("activity")
    if selected_activity:
        activity = publication_routes()["activities"][selected_activity["activity_id"]]
        publication_preview.update(activity_name=activity["name"], starts_at=activity["starts_at"], ends_at=activity["ends_at"])
    title = candidate.get("working_title") or contract.get("title") or "待定"
    return {"protocol": PROTOCOL, "project_id": contract["project_id"],
            "input_hash": digest({"protocol": PROTOCOL, "bindings": bindings}), "bindings": bindings,
            "stage": "plan_only", "working_title": title, "author_title": contract.get("title") or None,
            "provisional_lead": {"text": lead_text, "basis": lead_basis, "notice": TRIAL_NOTICE},
            "read_question": question or None, "candidate_promise": candidate.get("promise") or "",
            "character_desire": {key: candidate.get(key) or "" for key in ("protagonist", "goal", "obstacle", "cost")},
            "turns": turns, "turn_basis": turn_basis,
            "ending_payoff": candidate.get("ending_payoff") or plan.get("ending_resolution") or candidate.get("ending") or "",
            "planned_resolution": plan.get("ending_resolution") or "", "scenes": scenes,
            "selected_comparison": deepcopy(selected_review), "publication": publication_preview,
            "length": deepcopy(contract["length"]), "missing_preview_fields": missing,
            "platform_notice": PLATFORM_NOTICE,
            "paths": {"text": str(project.root / "plan" / "proposal.txt"),
                      "json": str(project.root / "plan" / "proposal.json")}}


def _line(value, limit=110) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def render(proposal: dict) -> str:
    desire, publication = proposal["character_desire"], proposal["publication"]
    length = proposal["length"]
    lines = ["故事方案｜供作者看选题和剧情", f"工作标题：{_line(proposal['working_title'], 80)}"]
    if proposal["author_title"] and proposal["author_title"] != proposal["working_title"]:
        lines.append("作者既定标题：" + _line(proposal["author_title"], 80))
    lines += [f"篇幅：本地可见字符最低{length['min']}，目标{length['target']}，上限{length['max'] or '未设'}。",
              "", "导语/开头试写：" + _line(proposal["provisional_lead"]["text"], 240), TRIAL_NOTICE,
              "具体追读问题：" + _line(proposal["read_question"] or "待补；候选承诺：" + proposal["candidate_promise"], 140),
              "人物欲望：" + _line(desire["protagonist"] + "想要" + desire["goal"] + "；阻力：" + desire["obstacle"] + "；代价：" + desire["cost"], 160),
              "", "三次转折（选中候选的写前承诺）："]
    for index, turn in enumerate(proposal["turns"][:3], 1):
        lines.append(f"{index}. {_line(turn.get('event'), 45)}；{_line(turn.get('before'), 45)} → {_line(turn.get('after'), 45)}；下一步：{_line(turn.get('new_action'), 45)}")
    if not proposal["turns"]:
        lines.append("待补；当前候选未提供三次转折样包。")
    elif len(proposal["turns"]) > 3:
        lines.append("其余转折保存在同目录proposal.json。")
    lines += ["结尾回报：" + _line(proposal["ending_payoff"], 160), "", "场景摘要（已有模型大纲）："]
    for index, scene in enumerate(proposal["scenes"], 1):
        summary = "；".join(str(scene.get(key) or "") for key in ("goal", "pressure", "choice", "consequence"))
        lines.append(f"{index}. {_line(scene.get('scene_id'), 24)}｜{_line(summary, 110)}")
    review = proposal["selected_comparison"]
    if review:
        lines += ["", "独立比较：优势：" + _line(review.get("strength"), 85),
                  "短板/执行风险：" + _line(str(review.get("weakness") or "") + "；" + str(review.get("execution_risk") or ""), 130)]
    lines += ["", "投稿路线：" + publication["name"] + "；渠道：" + "、".join(publication["channels"]),
              "正式主分类：" + (publication["official_category"] or "待作者后台确认")]
    if publication["activity"]:
        lines.append(f"活动：{publication['activity_name']}，期次{publication['activity']['period']}；签约窗口{publication['starts_at']}至{publication['ends_at']}。")
    lines.append(PLATFORM_NOTICE)
    lines += proposal["missing_preview_fields"]
    return "\n".join(lines) + "\n"


def save(project: StoryProject, plan: dict) -> dict:
    """Save a one-page view without generating prose or requiring author approval."""
    context = _context(project)
    bindings = _bindings(project, plan, context)
    proposal = _proposal(project, plan, context, bindings)
    cached = read_json(project.root / "plan" / "proposal.json")
    # Compare all source-derived fields so edited preview text cannot claim old evidence.
    previous_payload = {key: value for key, value in (cached or {}).items() if key != "created_at"}
    if previous_payload == proposal:
        expected_text = render(cached)
        text_path = project.root / "plan" / "proposal.txt"
        if not text_path.exists() or text_path.read_text(encoding="utf-8") != expected_text:
            write_text(text_path, expected_text)
        return {**cached, "cache_reused": True}
    proposal["created_at"] = now()
    write_json(project.root / "plan" / "proposal.json", proposal)
    write_text(project.root / "plan" / "proposal.txt", render(proposal))
    project.record_run({"event": "plan_preview_saved", "input_hash": proposal["input_hash"], "plan_hash": bindings["plan_hash"]})
    return {**proposal, "cache_reused": False}


def current(project: StoryProject) -> dict | None:
    """Reuse an unchanged optional proposal for formats whose planner lacks a cache."""
    cached = read_json(project.root / "plan" / "proposal.json")
    plan = read_json(project.root / "plan" / "scenes.json")
    if not cached or not plan:
        return None
    context = _context(project)
    try:
        bindings = _bindings(project, plan, context)
    except StoryError:
        return None
    expected_hash = digest({"protocol": PROTOCOL, "bindings": bindings})
    if cached.get("input_hash") != expected_hash:
        return None
    return save(project, plan)
