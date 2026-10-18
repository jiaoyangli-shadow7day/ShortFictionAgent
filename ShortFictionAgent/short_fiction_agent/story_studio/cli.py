"""CLI adapters for the v2 workflow; legacy commands remain available."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import yaml

from ..config import load_model_config
from ..llm import create_llm_client
from .channels import StoryExporter
from .contracts import StoryBrief, StoryError
from .inspiration import add_seed, discover_sources, load_seeds
from .quality import current_review
from .storage import StoryProject, now, read_json, write_json, write_text
from .workflow import StoryWorkflow
from .feedback import record_observation, latest_diagnosis, use_editorial_feedback


def add_parsers(subparsers) -> None:
    p = subparsers.add_parser("create", help="创建多题材短故事项目")
    p.add_argument("--project", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--brief", help="已有 brief.yaml")
    group.add_argument("--idea", help="一句话故事想法")
    group.add_argument("--free-idea", action="store_true", help="不锁定前提，从灵感池自由选题")
    p.add_argument("--title", default="")
    p.add_argument("--genre", default="custom")
    p.add_argument("--format", choices=["micro_story", "short_story", "short_serial", "novella", "long_serial"], default="micro_story")
    p.add_argument("--target", type=int)
    p.add_argument("--engine", default="emotional_reframing")
    p.add_argument("--channel", action="append", default=[])

    p = subparsers.add_parser("develop", help="生成不同点子并比较开头")
    p.add_argument("--project", required=True)
    p.add_argument("--candidate-index", type=int)
    p.add_argument("--opening-index", type=int)
    p.add_argument("--refresh", action="store_true")

    p = subparsers.add_parser("plan", help="生成并预审大纲，保存一页选题与剧情方案")
    p.add_argument("--project", required=True)

    p = subparsers.add_parser("add-inspiration", help="导入可溯源的作者素材或公开灵感")
    p.add_argument("--project", required=True)
    p.add_argument("--source-type", required=True,
                   choices=["author_note", "public_fact", "market_signal", "licensed_work"])
    p.add_argument("--summary", required=True, help="自己的事实摘要或抽象观察，不粘贴小说全文")
    p.add_argument("--title", default="")
    p.add_argument("--pattern", default="", help="抽象冲突或读者期待")
    p.add_argument("--url", default="", help="外部素材的公开 HTTPS 来源")
    p.add_argument("--rights", default="", help="owned/facts_only/trend_only/commercial_license/unverified")
    p.add_argument("--license", default="")
    p.add_argument("--attribution", default="")
    p.add_argument("--fetch-metadata", action="store_true", help="联网核对页面标题和描述，不保存正文")

    p = subparsers.add_parser("list-inspiration", help="查看当前项目灵感池")
    p.add_argument("--project", required=True)

    p = subparsers.add_parser("discover-inspiration", help="联网搜索指定公开域名，保存待核实的来源列表")
    p.add_argument("--project", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--domain", required=True, help="如 fanqienovel.com 或 beijing.gov.cn")
    p.add_argument("--limit", type=int, default=5)

    p = subparsers.add_parser("generate", help="生成、编辑和审查完整短故事")
    p.add_argument("--project", required=True)
    p.add_argument("--no-auto-revise", action="store_true")
    p.add_argument("--resume", action="store_true", help="从有原生生成来源的当前稿继续审查，保留已完成修订")
    p.add_argument("--editor-edits", help="仅配合 --resume，读取绑定当前正文哈希的独立局部编辑JSON")

    p = subparsers.add_parser("inspect", help="查看短故事版本和问题")
    p.add_argument("--project", required=True)

    for name, help_text in [('approve-lead', '确认当前导语，后续保留原文'),
                            ('stop-story', '停止本篇新增模型调用')]:
        p = subparsers.add_parser(name, help=help_text)
        p.add_argument('--project', required=True)
    p = subparsers.add_parser('continue-story', help='作者明确继续，并可追加运行额度')
    p.add_argument('--project', required=True)
    p.add_argument('--extra-calls', type=int, default=0)
    p.add_argument('--one-repair', action='store_true')

    p = subparsers.add_parser("revise-story", help="按问题ID局部修改短故事")
    p.add_argument("--project", required=True)
    p.add_argument("--issue", required=True)

    p = subparsers.add_parser("export", help="导出文本或图文包")
    p.add_argument("--project", required=True)
    p.add_argument("--channel", required=True,
                   choices=["generic_text", "fanqie_long", "fanqie_short", "qidian", "douyin_carousel"])
    p.add_argument("--draft", action="store_true")
    p.add_argument("--card-size", choices=["1080x1440", "1080x1920"], default="1080x1440")
    p.add_argument("--category", help="平台实际申报主分类，例如现言甜宠")

    p = subparsers.add_parser("import-legacy", help="只读导入旧小说，保留原始文件")
    p.add_argument("--source", required=True)
    p.add_argument("--dest", required=True)

    p = subparsers.add_parser("match-campaigns", help="核对公开扶持条件，输出待平台确认的候选活动")
    p.add_argument("--project", required=True)
    p.add_argument("--rules", required=True, help="有出处和日期的活动JSON")
    p.add_argument("--input", required=True, help="JSON: platform,entry_type,category,word_count,as_of")

    p = subparsers.add_parser("audit-workflow", help="只读核验原生流程与版本证据")
    p.add_argument("--project", required=True)

    p = subparsers.add_parser("prepare-publication", help="审读实际标题、简介和正文发布准备度")
    p.add_argument("--project", required=True)
    p.add_argument("--input", required=True, help="JSON: channel,titles,synopsis,category")

    p = subparsers.add_parser("record-feedback", help="导入平台后台的结构化观察")
    p.add_argument("--project", required=True)
    p.add_argument("--input", required=True, help="一条JSON观察记录")

    p = subparsers.add_parser("analyze-feedback", help="只读诊断最后导入的反馈，不预测推荐或收益")
    p.add_argument("--project", required=True)

    p = subparsers.add_parser("use-feedback", help="为新创作显式选择一条历史反馈作为编辑试验")
    p.add_argument("--project", required=True)
    p.add_argument("--source-project", required=True)
    p.add_argument("--observation-id", required=True)


def _workflow(project: StoryProject) -> StoryWorkflow:
    config = load_model_config(project.root)
    if config.provider == "mock":
        raise StoryError("短故事创作需要真实模型；请先配置项目 config/model.yaml 的 provider、model 与密钥")
    return StoryWorkflow(project, create_llm_client(config))


def run(args) -> int:
    try:
        return _run(args)
    except StoryError as exc:
        print(f"[StoryStudio] {exc}", file=sys.stderr)
        return 2


def _run(args) -> int:
    command = args.command
    if command == "create":
        if args.brief:
            raw = yaml.safe_load(Path(args.brief).read_text(encoding="utf-8"))
        else:
            raw = {"title": args.title, "genre": {"primary": args.genre},
                   "format": args.format, "story_engine": args.engine,
                   "author_input": {"premise": args.idea or "", "locked_facts": []},
                   "channels": args.channel or ["generic_text"]}
            if args.free_idea:
                raw["reader_promise"] = "以明确冲突开篇，由人物选择推动情节，结尾兑现主问题"
            if args.target:
                raw["length"] = {"target": args.target}
        project = StoryProject(Path(args.project))
        brief = StoryBrief.from_dict(raw)
        project.create(brief)
        model_path = project.root / "config" / "model.yaml"
        if not model_path.exists():
            write_text(model_path, "provider: mock\nmodel: ''\nmax_tokens: 6000\n")
        print(json.dumps({"project": str(project.root), "format": brief.format,
                          "genre": brief.genre, "support_level": brief.support_level}, ensure_ascii=False))
        return 0
    if command == "import-legacy":
        return import_legacy(Path(args.source), StoryProject(Path(args.dest)))
    project = StoryProject(Path(args.project))
    if command == "match-campaigns":
        from .campaigns import match_campaigns
        rules = json.loads(Path(args.rules).read_text(encoding="utf-8"))
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not {"platform", "entry_type", "category", "word_count", "as_of"}.issubset(data):
            raise StoryError("活动匹配输入缺少平台、形式、分类、字数或查询日期")
        print(json.dumps(match_campaigns(rules["rules"], **data), ensure_ascii=False, indent=2))
        return 0
    if command == "audit-workflow":
        from .distribution import audit_workflow
        result = audit_workflow(project)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "complete" else 3
    if command == "prepare-publication":
        from .distribution import PublicationPreparationAgent, chief_preparation
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not {"channel", "titles", "synopsis"}.issubset(data):
            raise StoryError("发布资料缺少 channel、titles 或 synopsis")
        callback = lambda task, prompt: _workflow(project).call_json(task, prompt)
        prepare = (lambda **kw: chief_preparation(project, callback, **kw)) if project.contract().get('review_mode', 'chief_editor') == 'chief_editor' else PublicationPreparationAgent(project, callback).review
        result = prepare(
            channel=data["channel"], titles=data["titles"], synopsis=data["synopsis"],
            category=data.get("category", ""))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["decision"] == "editorial_ready" else 3
    if command == "record-feedback":
        observation = json.loads(Path(args.input).read_text(encoding="utf-8"))
        path, _ = record_observation(project, observation)
        print(str(path))
        return 0
    if command == "analyze-feedback":
        print(json.dumps(latest_diagnosis(project), ensure_ascii=False, indent=2))
        return 0
    if command == "use-feedback":
        snapshot = use_editorial_feedback(project, StoryProject(Path(args.source_project)), args.observation_id)
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0
    project.brief()
    if command == 'approve-lead':
        from . import lead
        _, body = project.active_draft()
        receipt = lead.approve_lead(body, project.contract())
        write_json(project.root / 'approved_lead.json', receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    if command in ('stop-story', 'continue-story'):
        from . import run_control
        state = (run_control.stop(project) if command == 'stop-story' else
                 run_control.authorize_continue(project, extra_calls=args.extra_calls, repairs=args.one_repair))
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    if command == "add-inspiration":
        seed = add_seed(project.root, source_type=args.source_type, summary=args.summary,
                        title=args.title, pattern=args.pattern, url=args.url,
                        rights_status=args.rights, license_name=args.license,
                        attribution=args.attribution, fetch_metadata=args.fetch_metadata)
        project.record_run({"event": "inspiration_added", "seed_id": seed["id"],
                            "source_type": seed["source_type"]})
        print(json.dumps(seed, ensure_ascii=False, indent=2))
        return 0
    if command == "list-inspiration":
        print(json.dumps(load_seeds(project.root), ensure_ascii=False, indent=2))
        return 0
    if command == "discover-inspiration":
        result = discover_sources(project.root, query=args.query,
                                  domain=args.domain, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if command == "inspect":
        state = read_json(project.root / "project.json", {})
        try:
            record, body = project.active_draft()
            report = current_review(project, require_passed=False)
            state.update({"artifact": record, "review": {"decision": report["decision"],
                                                     "blockers": report["blockers"],
                                                     "issues": report["issues"],
                                                     "market_checks": report.get("market_checks", [])}})
        except StoryError as exc:
            state["note"] = str(exc)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0
    if command == "export" and args.channel != "douyin_carousel" and (args.draft or load_model_config(project.root).provider == "mock"):
        exported = StoryExporter(project).export(args.channel, draft=args.draft,
                                                 category=getattr(args, 'category', None))
        print(str(exported))
        return 0
    if command == "plan":
        from . import plan_preview
        # The compact and serial planners do not have the short-story cache.
        result = plan_preview.current(project) if project.brief().format in ("micro_story", "short_serial") else None
        if result is None:
            plan = _workflow(project).plan()
            result = plan_preview.save(project, plan)
        print(json.dumps({"stage": "plan_only", "working_title": result["working_title"],
                          "proposal_text": result["paths"]["text"], "proposal_json": result["paths"]["json"],
                          "cache_reused": result["cache_reused"]}, ensure_ascii=False))
        return 0
    workflow = _workflow(project)
    if command == "develop":
        result = workflow.develop(candidate_index=args.candidate_index,
                                  opening_index=args.opening_index, refresh=args.refresh)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if command == "generate":
        if args.editor_edits and not args.resume:
            raise StoryError("--editor-edits 需要 --resume")
        if args.resume:
            changes = read_json(Path(args.editor_edits)) if args.editor_edits else None
            if args.editor_edits and changes is None:
                raise StoryError("独立编辑文件不存在")
            report = workflow.resume_generation(editor_edits=changes, auto_revise=not args.no_auto_revise)
        else:
            report = workflow.generate(auto_revise=not args.no_auto_revise)
        print(json.dumps({"decision": report["decision"], "blockers": report["blockers"],
                          "review_id": report["review_id"]}, ensure_ascii=False))
        return 0 if report["decision"] == "passed" else 3
    if command == "revise-story":
        report = workflow.revise_issue(args.issue)
        print(json.dumps({"decision": report["decision"], "blockers": report["blockers"]}, ensure_ascii=False))
        return 0 if report["decision"] == "passed" else 3
    if command == "export":
        size = tuple(int(x) for x in args.card_size.split("x"))
        exported = StoryExporter(project, workflow.call_json).export(args.channel,
                                                               draft=args.draft, card_size=size,
                                                               category=getattr(args, 'category', None))
        print(str(exported))
        return 0
    raise StoryError(f"未知命令: {command}")


def import_legacy(source: Path, dest: StoryProject) -> int:
    source = source.resolve()
    if not source.exists():
        raise StoryError(f"旧项目不存在: {source}")
    config_file = source / "config" / "novel.yaml"
    chapters = sorted((source / "chapters").glob("chapter_*.md"))
    if not config_file.exists() or not chapters:
        raise StoryError("旧项目需要 config/novel.yaml 和 chapters/chapter_*.md")
    original_hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in [config_file, *chapters]}
    config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    characters = []
    for path in sorted((source / "characters").glob("*.yaml")):
        item = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(item, dict) and item.get("name"):
            characters.append({"id": path.stem, **item})
    world_file = source / "campus" / "school_setting.yaml"
    world = yaml.safe_load(world_file.read_text(encoding="utf-8")) if world_file.exists() else {}
    brief = StoryBrief.from_dict({"title": config.get("title", ""),
                                  "genre": {"primary": config.get("genre", "custom")},
                                  "format": "long_serial", "story_engine": "relationship_choice",
                                  "tone": config.get("tone", ""),
                                  "point_of_view": config.get("perspective", "third_person_limited"),
                                  "author_input": {"premise": config.get("core_theme") or config.get("title") or "旧稿导入",
                                                   "locked_facts": []},
                                  "length": {"target": max(10000, int(config.get("target_words") or 200000))},
                                  "characters": characters, "world": world or {}})
    dest.create(brief)
    body = "\n\n".join(p.read_text(encoding="utf-8") for p in chapters)
    record = dest.save_draft(body)
    dest.update_state(record, "legacy_generated")
    migration = {"source": str(source), "imported_at": now(), "files_sha256": original_hashes,
                 "chapter_count": len(chapters), "assumptions": ["旧稿按 long_serial 导入",
                 "旧审查报告没有v2正文哈希与完整覆盖证明，必须重新审查"],
                 "v2_artifact_id": record["artifact_id"], "v2_revision": record["revision"]}
    write_json(dest.root / "migration_report.json", migration)
    if any(hashlib.sha256((source / relative).read_bytes()).hexdigest() != value
           for relative, value in original_hashes.items()):
        raise StoryError("导入期间旧项目源文件发生变化")
    print(json.dumps({"dest": str(dest.root), "chapter_count": len(chapters),
                      "state": "legacy_generated"}, ensure_ascii=False))
    return 0
