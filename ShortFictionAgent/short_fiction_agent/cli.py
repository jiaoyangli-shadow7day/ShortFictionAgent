"""CLI 命令行接口。"""

import sys
import argparse
from pathlib import Path
from .config import load_model_config, load_multi_model_config, load_yaml
from .paths import ProjectPaths
from .llm import create_llm_client
from .models import ReviewThresholds
from .memory import FileMemoryStore
from .agents.setting_agent import SettingAgent
from .agents.outline_agent import OutlineAgent
from .agents.context_agent import ContextAgent
from .agents.writer_agent import WriterAgent
from .agents.review_agent import ReviewAgent
from .agents.data_agent import DataAgent
from .agents.query_agent import QueryAgent
from .agents.revision_agent import RevisionAgent
from .agents.consistency_agent import ConsistencyAgent
from .agents.entity_agent import EntityConsistencyAgent
from .agents.visualization_agent import VisualizationAgent
from .agents.reality_agent import RealityAgent
from .agents.pipeline_agent import PipelineAgent
from .agents.narrative_guard_agent import NarrativeGuardAgent
from .agents.commercial_editor_agent import CommercialQualitySuite
from .agents.character_canon_sync_agent import CharacterCanonSyncAgent


def check_project(project_path: Path) -> ProjectPaths:
    """检查项目目录是否存在。"""
    paths = ProjectPaths(project_path)
    if not project_path.exists():
        print(f"[错误] 项目目录不存在: {project_path}")
        print("请先执行 init 命令创建项目。")
        sys.exit(1)
    return paths


def preflight_llm_or_exit(llm, skip: bool = False):
    """在长耗时生成前做最小 API 连通性检查。"""
    if skip or not hasattr(llm, "preflight"):
        return
    print("  [预检] 正在验证 LLM API Key、模型和连通性...")
    result = llm.preflight()
    if not result.get("ok"):
        print(f"  [预检失败] {result.get('error', '')}")
        if result.get("status_code"):
            print(f"  HTTP 状态码: {result.get('status_code')}")
        sys.exit(1)
    configured = result.get("configured_model") or ""
    actual = result.get("actual_model") or ""
    if actual and configured and actual != configured:
        print(f"  [预检提醒] 配置模型 {configured}，API 实际返回模型 {actual}。")
    else:
        print("  [预检通过] LLM API 可用。")


def cmd_init(args):
    """初始化新小说项目。"""
    project_path = Path(args.project).resolve()
    print(f"初始化小说项目: {project_path}")

    paths = ProjectPaths(project_path)
    paths.ensure_dirs()

    from .config import load_model_config
    model_config_path = paths.model_config
    if model_config_path.exists():
        model_config = load_model_config(project_path)
    else:
        from .models import ModelConfig
        model_config = ModelConfig(provider="mock")

    llm = create_llm_client(model_config)
    # 题材由 --genre 选择(默认校园); 决定脚手架与后续 prompt/词表
    from .genres import resolve_pack_by_genre_string
    pack = resolve_pack_by_genre_string(getattr(args, "genre", None))
    agent = SettingAgent(paths, llm, pack)

    title = getattr(args, "title", None) or "未命名"
    stage = getattr(args, "stage", None) or "高中"
    target_words = getattr(args, "target_words", None) or 200000
    agent.create_default_settings(title=title, stage=stage, target_words=target_words)
    print(f"  题材: {pack.display_name}（genre={pack.genre_id}）")

    # 保存 ReviewThresholds 到 style_config
    from .config import save_yaml, load_yaml as load_y
    style = load_y(paths.style_config)
    style["review_thresholds"] = ReviewThresholds().to_dict()
    save_yaml(paths.style_config, style)

    print(f"\n项目初始化完成！")
    print(f"  位置: {project_path}")
    print(f"\n已创建的目录和文件:")
    print(f"  config/       - 小说配置、模型配置、风格配置")
    print(f"  story/        - 故事梗概、大纲")
    print(f"  characters/   - 角色设定（按题材角色槽位）")
    print(f"  relationship/ - 进度/关系状态")
    print(f"  campus/       - 世界/背景设定")
    print(f"  memory/       - 记忆文件")
    print(f"  chapters/     - 章节正文")
    print(f"  reviews/      - 审查报告")
    print(f"  writing_tasks/ - 写作任务书")
    print(f"  revisions/    - 修改计划")
    print(f"  consistency/  - 一致性检查报告")
    print(f"  visualization/ - 可视化仪表盘")
    print(f"  exports/      - 导出文件")
    print(f"\n下一步:")
    print(f'  python -m short_fiction_agent create-setting --project {project_path} --title "书名" --stage "高中" --target-words 300000')


def cmd_create_setting(args):
    """创建小说基础设定。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)

    agent = SettingAgent(paths, llm)

    from .config import save_yaml, load_yaml as load_y
    novel_config = load_y(paths.novel_config)
    novel_config["title"] = args.title or novel_config.get("title", "未命名")
    novel_config["stage"] = args.stage or novel_config.get("stage", "高中")
    novel_config["target_words"] = int(args.target_words) if args.target_words else novel_config.get("target_words", 200000)
    save_yaml(paths.novel_config, novel_config)

    print(f"小说: {novel_config['title']}")
    print(f"阶段: {novel_config['stage']}")
    print(f"目标字数: {novel_config['target_words']}")

    print("\n正在使用 LLM 生成设定...")
    settings = agent.generate_with_llm(
        title=novel_config["title"],
        stage=novel_config["stage"],
        target_words=novel_config["target_words"],
    )

    if settings:
        agent.apply_llm_settings(settings)
        print("\n设定生成完成！已创建/更新以下文件:")
        for key in settings:
            if key == "novel":
                print(f"  config/novel.yaml")
            elif key == "protagonist_male":
                print(f"  characters/protagonist_male.yaml")
            elif key == "protagonist_female":
                print(f"  characters/protagonist_female.yaml")
            elif key == "initial_relationship":
                print(f"  relationship/relationship_state.yaml")
            elif key == "school_setting":
                print(f"  campus/school_setting.yaml")
    else:
        print("[警告] LLM 返回的数据无法解析。已保留默认设定文件。")


def cmd_plan_book(args):
    """生成全书大纲。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    agent = OutlineAgent(paths, llm, memory)
    print("正在生成全书大纲...")
    response = agent.plan_book()
    print(f"\n全书大纲已生成: {paths.master_outline}")


def cmd_plan_chapters(args):
    """生成章节大纲。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    agent = OutlineAgent(paths, llm, memory)
    volume = int(args.volume) if args.volume else 1
    print(f"正在生成第 {volume} 卷章节大纲...")
    response = agent.plan_chapters(volume=volume)
    print(f"\n章节大纲已生成: {paths.chapter_outline}")


def cmd_update_outline(args):
    """增量更新大纲：根据已写章节调整后续大纲。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    from_chapter = int(args.from_chapter)
    print(f"正在根据第 1-{from_chapter - 1} 章的实际情况，调整第 {from_chapter} 章起的后续大纲...")
    agent = OutlineAgent(paths, llm, memory)
    response = agent.update_outline_from_chapters(from_chapter)
    print(f"\n大纲增量更新完成！")
    print(f"  更新日志: {paths.story_dir / 'outline_update_log.md'}")
    print(f"  章节大纲: {paths.chapter_outline}")


def cmd_write(args):
    """编写章节。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    preflight_llm_or_exit(llm, skip=getattr(args, "no_preflight", False))
    memory = FileMemoryStore(paths)

    chapter_id = int(args.chapter)

    print(f"正在准备第 {chapter_id} 章写作任务书...")
    context_agent = ContextAgent(paths, llm, memory)
    task = context_agent.generate_writing_task(chapter_id)
    print(f"  写作任务书已生成: chapter_{chapter_id:03d}_task.md")

    print(f"\n正在撰写第 {chapter_id} 章...")
    writer = WriterAgent(paths, llm, memory)
    content = writer.write_chapter(chapter_id)
    print(f"  章节文件: {paths.chapter_file(chapter_id)}")


def cmd_review(args):
    """审查章节（含阈值告警）。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    chapter_id = int(args.chapter)
    print(f"正在审查第 {chapter_id} 章...")
    agent = ReviewAgent(paths, llm, memory)

    # 加载阈值配置
    thresholds = None
    style = load_yaml(paths.style_config)
    if style and "review_thresholds" in style:
        from .models import ReviewThresholds
        thresholds = ReviewThresholds.from_dict(style["review_thresholds"])

    # 如果指定了 --no-threshold，只做审查不做阈值检查
    if getattr(args, "no_threshold", False):
        response = agent.review_chapter(chapter_id)
        print(f"审查报告已生成: {paths.review_file(chapter_id)}")
    else:
        result = agent.review_with_thresholds(chapter_id, thresholds)
        print(f"审查报告已生成: {paths.review_file(chapter_id)}")

        tc = result["threshold_check"]
        if tc.get("passed"):
            print(f"\n✅ 阈值检查通过（总分: {tc.get('total_score', 'N/A')}）")
        else:
            print(f"\n⚠️  阈值检查未通过！发现 {tc.get('warnings_count', 0)} 个问题：")
            for w in tc.get("warnings", []):
                icon = "🔴" if w.get("severity") == "high" else "🟠"
                print(f"  {icon} {w.get('message', '')}")
                print(f"     建议: {w.get('suggestion', '')}")
            print(f"\n阈值检查报告: {paths.reviews_dir / f'chapter_{chapter_id:03d}_threshold.md'}")


def cmd_batch_review(args):
    """批量审查章节。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    from_ch = int(args.from_chapter)
    to_ch = int(args.to_chapter)
    print(f"正在批量审查第 {from_ch}-{to_ch} 章...")
    agent = ReviewAgent(paths, llm, memory)

    thresholds = None
    style = load_yaml(paths.style_config)
    if style and "review_thresholds" in style:
        thresholds = ReviewThresholds.from_dict(style["review_thresholds"])

    result = agent.batch_review(from_ch, to_ch, thresholds)
    print(f"\n批量审查完成！")
    print(f"  检查章节: {len(result['chapters_checked'])} 章")
    print(f"  总计告警: {result['total_warnings']} 个")
    print(f"  全部通过: {'✅ 是' if result['all_passed'] else '⚠️ 否'}")

    if not result["all_passed"]:
        print(f"\n未通过的章节:")
        for r in result["results"]:
            if not r.get("passed", False):
                print(f"  第 {r['chapter_id']} 章: {r.get('warnings_count', 0)} 个告警")


def cmd_check_consistency(args):
    """跨章节一致性检查。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    agent = ConsistencyAgent(paths, llm, memory)
    from_ch = int(args.from_chapter) if args.from_chapter else 1
    to_ch = int(args.to_chapter) if args.to_chapter else None

    print(f"正在检查第 {from_ch} 章到第 {to_ch or '最后'} 章的一致性...")
    report = agent.check_all(from_chapter=from_ch, to_chapter=to_ch)

    if "error" in report:
        print(f"[错误] {report['error']}")
        return

    print(f"\n一致性检查完成！")
    print(f"  总体评分: {report['overall_score']}/100")
    print(f"  问题总数: {report['total_issues']}")
    if report["issues_by_type"]:
        for itype, count in report["issues_by_type"].items():
            type_labels = {
                "relationship_jump": "关系数值跳跃",
                "foreshadowing_overdue": "伏笔超期",
                "character_inconsistency": "角色不一致",
                "dialogue_shift": "对话风格变化",
                "event_conflict": "事件矛盾",
                "timeline_error": "时间线错误",
            }
            label = type_labels.get(itype, itype)
            icon = "🔴" if count >= 3 else "🟠" if count >= 1 else "🟡"
            print(f"  {icon} {label}: {count} 处")

    if report["overall_score"] >= 95:
        print("\n✅ 一致性良好，可以继续创作。")
    elif report["overall_score"] >= 80:
        print("\n⚠️ 存在少量不一致问题，建议修复后再继续。")
    else:
        print("\n🔴 一致性问题较多，强烈建议暂停新章节创作，优先修复！")

    print(f"\n详细报告: {paths.consistency_report_file()}")


def cmd_check_entities(args):
    """实体一致性检查 — 人物姓名/性别/班级/住校/物件归属等。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    multi_config = load_multi_model_config(project_path)
    llm = create_llm_client(multi_config.primary)
    # 实体提取用 flash 模型（routine 任务，省钱提速）
    fallback_llm = None
    if multi_config.fallback:
        fallback_llm = create_llm_client(multi_config.fallback)
    memory = FileMemoryStore(paths)

    agent = EntityConsistencyAgent(paths, llm, memory, fallback_llm=fallback_llm)
    from_ch = int(args.from_chapter) if args.from_chapter else 1
    to_ch = int(args.to_chapter) if args.to_chapter else None

    print(f"正在检查第 {from_ch} 章到第 {to_ch or '最后'} 章的实体一致性...")
    print("（这会调用 LLM 逐章提取实体，可能需要几分钟）\n")
    report = agent.check_all(from_chapter=from_ch, to_chapter=to_ch)

    if "error" in report:
        print(f"[错误] {report['error']}")
        return

    issues = report.get("issues", [])
    print(f"\n实体一致性检查完成！")
    print(f"  总体评分: {report['score']}/100")
    print(f"  问题总数: {len(issues)}")

    if not issues:
        print("\n✅ 未发现实体一致性问题。")
    else:
        severity_icons = {"critical": "⛔", "high": "🔴", "medium": "🟠", "low": "🟡"}
        for i, issue in enumerate(issues, 1):
            icon = severity_icons.get(issue.get("severity", ""), "⚪")
            print(f"\n  {i}. {icon} [{issue.get('issue_type', '')}] {issue.get('description', '')}")
            print(f"     涉及: 第{issue.get('chapter_id_a')}章 vs 第{issue.get('chapter_id_b')}章")
            print(f"     建议: {issue.get('suggestion', '')}")

    print(f"\n详细报告: {paths.reviews_dir / 'entity_consistency_report.md'}")


def cmd_visualize(args):
    """生成可视化报告。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    memory = FileMemoryStore(paths)

    agent = VisualizationAgent(paths, memory)
    chart_type = args.type if args.type else "full"

    if chart_type == "relationship" or chart_type == "rel":
        output = agent.generate_relationship_curve()
    elif chart_type == "review" or chart_type == "rev":
        output = agent.generate_review_trend()
    elif chart_type == "stats":
        output = agent.generate_chapter_stats()
    else:
        output = agent.generate_full_dashboard()

    print(output)
    if chart_type == "full":
        print(f"\n仪表盘已保存至: {paths.visualization_dir / 'dashboard.txt'}")


def cmd_update_memory(args):
    """更新记忆。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    chapter_id = int(args.chapter)
    print(f"正在更新第 {chapter_id} 章的记忆...")
    agent = DataAgent(paths, llm, memory)
    agent.update_memory(chapter_id)
    print("记忆更新完成。")


def cmd_revise(args):
    """生成修改计划。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)

    chapter_id = int(args.chapter)
    print(f"正在生成第 {chapter_id} 章修改计划...")
    agent = RevisionAgent(paths, llm)
    response = agent.generate_revision_plan(chapter_id)
    print(f"修改计划已生成: {paths.revision_file(chapter_id)}")


def cmd_query(args):
    """搜索关键词。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    memory = FileMemoryStore(paths)

    keyword = args.keyword
    print(f"正在搜索关键词: \"{keyword}\"...")
    agent = QueryAgent(paths, memory)
    results = agent.search(keyword)
    output = agent.format_results(results, keyword)
    print(output)


def cmd_check_reality(args):
    """现实逻辑审核：检查内容是否符合现实世界规则。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    agent = RealityAgent(paths, llm, memory)

    if args.chapter:
        chapter_id = int(args.chapter)
        print(f"正在审核第 {chapter_id} 章的现实逻辑...")
        result = agent.check_chapter_with_summary(chapter_id)
        print(f"\n审核报告已生成: {paths.reviews_dir / f'chapter_{chapter_id:03d}_reality.md'}")
    else:
        print("正在审核全书大纲的现实逻辑...")
        result = agent.check_outline_with_summary()
        print(f"\n审核报告已生成: {paths.reviews_dir / 'outline_reality_check.md'}")

    issues = result["issues"]
    print(f"\n问题统计:")
    print(f"  🔴 严重: {issues.get('severe', 0)} 处")
    print(f"  🟡 建议: {issues.get('suggestion', 0)} 处")
    print(f"  🔵 提醒: {issues.get('reminder', 0)} 处")

    if result.get("passed"):
        print(f"\n✅ 未发现严重现实逻辑问题。")
    else:
        print(f"\n⚠️  存在严重现实逻辑问题，建议修改后再继续创作。")


def cmd_check_narrative(args):
    """商业追读结构质检：检查大纲或章节是否具备故事驱动力。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    agent = NarrativeGuardAgent(paths)

    if args.chapter:
        chapter_id = int(args.chapter)
        if getattr(args, "text", False):
            print(f"正在检查第 {chapter_id} 章正文的追读结构...")
            result = agent.audit_chapter_text(chapter_id)
            report_path = agent.save_chapter_text_report(chapter_id, result)
        else:
            print(f"正在检查第 {chapter_id} 章大纲的追读结构...")
            result = agent.audit_outline(chapter_id)
            report_path = paths.story_dir / "narrative_guard_report.md"
            agent.audit_all_outlines()

        print(f"\n结果: {'通过' if result.get('passed') else '未通过'}")
        print(f"评分: {result.get('score', 0)}/100")
        if result.get("warnings"):
            print("\n主要问题:")
            for warning in result["warnings"][:8]:
                print(f"  - {warning}")
        if result.get("suggestions"):
            print("\n建议:")
            for suggestion in result["suggestions"][:8]:
                print(f"  - {suggestion}")
        print(f"\n报告: {report_path}")
    else:
        print("正在检查所有章节大纲的追读结构...")
        report = agent.audit_all_outlines()
        print(f"\n章节数: {report.get('total', 0)}")
        print(f"未通过: {report.get('failed', 0)}")
        print(f"平均分: {report.get('avg_score', 0)}/100")
        print(f"报告: {paths.story_dir / 'narrative_guard_report.md'}")


def cmd_check_commercial(args):
    """八模块商业编辑质检。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    agent = CommercialQualitySuite(paths)

    if args.chapter:
        chapter_id = int(args.chapter)
        if getattr(args, "text", False):
            print(f"正在检查第 {chapter_id} 章正文商业风险...")
            result = agent.audit_chapter_text(chapter_id)
            report_path = agent.save_chapter_report(chapter_id, text_result=result)
        else:
            print(f"正在检查第 {chapter_id} 章商业章纲...")
            result = agent.audit_outline(chapter_id)
            report_path = agent.save_chapter_report(chapter_id, outline_result=result)
    else:
        print("正在检查全书商业追读结构...")
        result = agent.audit_project()
        report_path = agent.save_project_report(result)

    print(f"\n结果: {'通过' if result.get('passed') else '未通过'}")
    print(f"评分: {result.get('score', 0)}/100")
    if result.get("warnings"):
        print("\n主要问题:")
        for warning in result["warnings"][:10]:
            print(f"  - {warning}")
    if result.get("suggestions"):
        print("\n建议:")
        for suggestion in result["suggestions"][:10]:
            print(f"  - {suggestion}")
    print(f"\n报告: {report_path}")


def cmd_check_canon(args):
    """角色设定同步检查。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    agent = CharacterCanonSyncAgent(paths)

    if getattr(args, "apply", False):
        update = agent.sync_profiles()
        if update.get("updated"):
            print(f"已写入 canon_sync_notes: {', '.join(update.get('paths', []))}")
        else:
            print("没有可写入的 canon_sync_notes。")

    result = agent.audit()
    report_path = agent.save_report(result)
    print(f"\n结果: {'通过' if result.get('passed') else '未通过'}")
    print(f"评分: {result.get('score', 0)}/100")
    if result.get("issues"):
        print("\n主要问题:")
        for issue in result["issues"][:10]:
            print(f"  - {issue}")
    if result.get("suggestions"):
        print("\n建议:")
        for suggestion in result["suggestions"][:10]:
            print(f"  - {suggestion}")
    print(f"\n报告: {report_path}")


def cmd_write_volume(args):
    """一口气写完整卷所有章节。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    dry_run = getattr(args, "dry_run", False)
    llm, fallback_llm = _create_pipeline_llm(project_path, dry_run=dry_run)
    preflight_llm_or_exit(llm, skip=getattr(args, "no_preflight", False) or dry_run)
    memory = FileMemoryStore(paths)

    agent = PipelineAgent(paths, llm, memory, fallback_llm=fallback_llm,
                          router=_create_router(project_path), dry_run=dry_run,
                          use_task_agent=getattr(args, 'llm_task', False))
    volume = int(args.volume)
    auto_revise = not getattr(args, "no_revise", False)
    pipeline_mode = getattr(args, "mode", "submission")

    result = agent.write_volume(volume, auto_revise=auto_revise, pipeline_mode=pipeline_mode)

    if result.get("error"):
        print(f"\n[错误] {result['error']}")
        sys.exit(1)
    else:
        print(f"\n✅ 第 {volume} 卷完成！")


def _create_pipeline_llm(project_path: Path, dry_run: bool = False):
    """创建管线 LLM 客户端（支持双模型）。

    dry_run=True 时强制 mock 双模型, 用于不花 API 费用地验证全链路接线。
    """
    if dry_run:
        from .llm import MockLLMClient
        return MockLLMClient(), MockLLMClient()
    from .config import load_multi_model_config
    mc = load_multi_model_config(project_path)
    primary = create_llm_client(mc.primary)
    fallback = create_llm_client(mc.fallback) if mc.fallback else None
    return primary, fallback


def _create_router(project_path: Path):
    """从 config/model.yaml 的 routing 段构建集中式模型路由器。"""
    from .config import load_routing_config
    from .model_router import ModelRouter
    return ModelRouter.from_config(load_routing_config(project_path))


def cmd_write_all(args):
    """一口气写完整本小说所有章节。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    dry_run = getattr(args, "dry_run", False)
    llm, fallback_llm = _create_pipeline_llm(project_path, dry_run=dry_run)
    preflight_llm_or_exit(llm, skip=getattr(args, "no_preflight", False) or dry_run)
    memory = FileMemoryStore(paths)

    agent = PipelineAgent(paths, llm, memory, fallback_llm=fallback_llm,
                          router=_create_router(project_path), dry_run=dry_run,
                          use_task_agent=getattr(args, 'llm_task', False))
    auto_revise = not getattr(args, "no_revise", False)
    pipeline_mode = getattr(args, "mode", "submission")

    print("=" * 60)
    print("  🚀 开始全自动小说创作" + ("（DRY-RUN 试运行/Mock）" if dry_run else ""))
    print("=" * 60)
    print(f"  项目: {project_path}")
    print(f"  架构模式: {pipeline_mode}")
    print(f"  自动修订: {'开启' if auto_revise else '关闭'}")
    print(f"  提示: 按 Ctrl+C 可安全中断，进度会自动保存")
    print("=" * 60)

    result = agent.write_all(auto_revise=auto_revise, pipeline_mode=pipeline_mode)

    if result.get("error"):
        print(f"\n[错误] {result['error']}")
        sys.exit(1)
    elif not result.get("success"):
        failed_count = len([r for r in result.get("results", []) if r["status"] == "failed"])
        print(f"\n⚠️  管线完成但 {failed_count} 章存在问题（已记录，可稍后重写）")
        print(f"  继续流程，不阻断。")
    else:
        print(f"\n✅ 全本小说创作完成！")


def cmd_write_range(args):
    """一口气写指定范围的章节。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    dry_run = getattr(args, "dry_run", False)
    llm, fallback_llm = _create_pipeline_llm(project_path, dry_run=dry_run)
    preflight_llm_or_exit(llm, skip=getattr(args, "no_preflight", False) or dry_run)
    memory = FileMemoryStore(paths)

    agent = PipelineAgent(paths, llm, memory, fallback_llm=fallback_llm,
                          router=_create_router(project_path), dry_run=dry_run,
                          use_task_agent=getattr(args, 'llm_task', False))
    from_ch = int(args.from_chapter)
    to_ch = int(args.to_chapter)
    auto_revise = not getattr(args, "no_revise", False)
    pipeline_mode = getattr(args, "mode", "submission")

    result = agent.write_range(from_ch, to_ch, auto_revise=auto_revise, pipeline_mode=pipeline_mode)

    if result.get("error"):
        print(f"\n[错误] {result['error']}")
        sys.exit(1)
    else:
        print(f"\n✅ 第 {from_ch}-{to_ch} 章完成！")


def cmd_pipeline_status(args):
    """查看当前管线进度。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    model_config = load_model_config(project_path)
    llm = create_llm_client(model_config)
    memory = FileMemoryStore(paths)

    agent = PipelineAgent(paths, llm, memory)
    progress = agent.get_progress()

    if not progress.get("active"):
        print("当前没有正在进行的管线。")
        return

    print(f"\n管线进度:")
    print(f"  状态: {'运行中' if progress['active'] else '已结束'}")
    print(f"  范围: 第 {progress['from_chapter']} 章 → 第 {progress['to_chapter']} 章")
    print(f"  已完成: {progress['chapters_done']} / {progress['total_chapters']} 章")
    print(f"  最后完成: 第 {progress['last_completed']} 章")
    print(f"  开始时间: {progress['started_at']}")
    print(f"  自动修订: {'开启' if progress.get('auto_revise') else '关闭'}")
    print(f"  架构模式: {progress.get('pipeline_mode', 'unknown')}")


def cmd_eval(args):
    """评测/回归 harness: 跑 fixtures 打分并存快照, 或对比两份快照。"""
    import json
    from .evaluation import EvalHarness, save_snapshot, compare_snapshots

    if getattr(args, "compare", None):
        base = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        cur = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        result = compare_snapshots(base, cur)
        print("══ 评测回归对比 (current - baseline) ══")
        for d in result["diffs"]:
            print(f"\n[{d['fixture_id']}]")
            for k, v in d["delta"].items():
                arrow = "↑" if v > 0 else ("↓" if v < 0 else "·")
                print(f"   {k}: {v:+g} {arrow}")
        if result["has_regression"]:
            print("\n🔴 检测到回归:")
            for r in result["regressions"]:
                print(f"   - {r}")
            sys.exit(1)
        print("\n✅ 未检测到回归。")
        return

    # run 模式: 有 --project 用其真实模型(质量回归), 否则 mock(结构回归)
    if getattr(args, "project", None):
        from .config import load_multi_model_config
        mc = load_multi_model_config(Path(args.project).resolve())
        llm = create_llm_client(mc.primary)
        fb = create_llm_client(mc.fallback) if mc.fallback else None
        mode = "真实API(质量回归)"
    else:
        from .llm import MockLLMClient
        llm, fb = MockLLMClient(), MockLLMClient()
        mode = "Mock(结构回归)"

    harness = EvalHarness(llm, fb, use_task_agent=not getattr(args, "deterministic_task", False))
    snap = harness.run_all()
    out = Path(getattr(args, "out", None) or "eval_snapshot.json")
    save_snapshot(snap, out)
    print(f"评测完成 [{mode}]: {snap['fixtures']} 个 fixture (git {snap['git_head']})")
    for r in snap["results"]:
        m = r["metrics"]
        line = (f"  [{r['fixture_id']}/{r['genre']}] 字数={m['zh_chars']} 对话={m['dialogue_count']} "
                f"重复警告={m['style_repeat_warnings']} 缺核心字段={m['outline_core_missing']} "
                f"评分={m['review_score']} 写成功={m['wrote_ok']}")
        if r["error"]:
            line += f"  ⚠️ {r['error']}"
        print(line)
    print(f"快照已存: {out}")


def cmd_genres(args):
    """列出全部可用题材（男频/女频），标注深度包 vs 原型基线。"""
    from .genres.taxonomy import channel_listing
    listing = channel_listing()
    label = {"male": "男频", "female": "女频"}
    for ch in ("male", "female"):
        print(f"\n【{label[ch]}】")
        for gid, name, deep in listing[ch]:
            tag = "✦深度" if deep else "·基线"
            print(f"  {tag}  {name:6}  →  --genre {gid}   (或 --genre {name})")
    print("\n✦深度 = 该题材专属词表/写作规则/prompt(玄幻/都市/校园)")
    print("·基线 = 走男频进度流 / 女频言情原型, 可端到端跑, 后续可单独深化")


def cmd_pipeline_guard(args):
    """进程护栏：列出/清理本项目残留的管线进程，防止重复写同一本书。"""
    project_path = Path(args.project).resolve()
    from .process_guard import find_pipeline_pids, cleanup_residual_pipelines, kill_all_pipelines
    pids = find_pipeline_pids(project_path)
    print(f"本项目管线进程: {pids if pids else '无'}")
    if getattr(args, "kill_all", False):
        killed = kill_all_pipelines(project_path)
        print(f"已清场(kill-all): {killed if killed else '无'}")
    elif getattr(args, "dedupe", False):
        result = cleanup_residual_pipelines(project_path)
        if result["killed"]:
            print(f"发现重复管线，已杀掉多余: {result['killed']}（保留最新）")
        else:
            print("无重复管线，无需清理。")


def cmd_pipeline_reset(args):
    """重置管线状态（清除断点，从头开始）。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    state_path = paths.memory_dir / "pipeline_state.json"
    if state_path.exists():
        state_path.unlink()
        print("管线状态已重置。")
    else:
        print("没有找到管线状态文件。")


def cmd_submission_check(args):
    """运行投稿阶段门禁，不写新章节。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)
    memory = FileMemoryStore(paths)

    from .agents.submission_gate_agent import SubmissionGateAgent
    agent = SubmissionGateAgent(paths, memory)
    gate_type = getattr(args, "gate", "final")
    from_ch = int(getattr(args, "from_chapter", 1) or 1)
    to_ch = getattr(args, "to_chapter", None)

    if to_ch is None:
        chapter_files = sorted(paths.chapters_dir.glob("chapter_*.md"))
        to_ch = len(chapter_files) if chapter_files else from_ch
    to_ch = int(to_ch)

    if gate_type == "opening10":
        result = agent.run_opening10_gate(up_to_chapter=min(to_ch, 10))
    elif gate_type == "rolling":
        result = agent.run_rolling_gate(current_chapter=to_ch)
    elif gate_type == "payoff":
        result = agent.run_payoff_ledger_gate(current_chapter=to_ch)
    elif gate_type == "arc":
        result = agent.run_arc_checkpoint_gate(current_chapter=to_ch)
    else:
        result = agent.run_final_submission_gate(from_chapter=from_ch, to_chapter=to_ch)

    print(f"\n{result.get('gate')}: {'通过' if result.get('passed') else '未通过'}")
    print(f"评分: {result.get('score')}/100")
    print(f"报告: {result.get('report_path')}")
    if result.get("warnings"):
        print("\n主要问题:")
        for warning in result["warnings"][:10]:
            print(f"  - {warning}")
    if result.get("suggestions"):
        print("\n建议:")
        for suggestion in result["suggestions"][:10]:
            print(f"  - {suggestion}")
    if not result.get("passed"):
        sys.exit(1)


def cmd_status(args):
    """查看项目状态。"""
    project_path = Path(args.project).resolve()
    paths = check_project(project_path)

    print(f"\n项目状态: {project_path}")
    print(f"{'='*60}")

    # 设定模块
    agent = SettingAgent(paths, None)
    setting_status = agent.get_status()
    print("\n[设定模块]")
    for key, exists in setting_status.items():
        status_icon = "✓" if exists else "✗"
        print(f"  {status_icon} {key}")

    # 大纲模块
    outline_agent = OutlineAgent(paths, None, None)
    outline_status = outline_agent.get_status()
    print("\n[大纲模块]")
    for key, exists in outline_status.items():
        status_icon = "✓" if exists else "✗"
        print(f"  {status_icon} {key}")

    # 章节统计
    chapters_dir = paths.chapters_dir
    chapter_files = sorted(chapters_dir.glob("chapter_*.md")) if chapters_dir.exists() else []
    print(f"\n[章节]")
    print(f"  已完成章节: {len(chapter_files)}")
    for cf in chapter_files:
        content = cf.read_text(encoding="utf-8") if cf.exists() else ""
        chinese_chars = sum(1 for c in content if '一' <= c <= '鿿')
        print(f"    {cf.name}: ~{chinese_chars} 字")

    # 审查统计
    reviews_dir = paths.reviews_dir
    review_files = sorted(reviews_dir.glob("chapter_*_review.md")) if reviews_dir.exists() else []
    threshold_files = sorted(reviews_dir.glob("chapter_*_threshold.md")) if reviews_dir.exists() else []
    print(f"\n[审查]")
    print(f"  审查报告: {len(review_files)} 份")
    print(f"  阈值检查: {len(threshold_files)} 份")

    # 一致性报告
    cons_report = paths.consistency_report_file()
    print(f"\n[一致性检查]")
    print(f"  {'✓' if cons_report.exists() else '✗'} 一致性报告")

    # 可视化
    vis_file = paths.visualization_dir / "dashboard.txt"
    print(f"\n[可视化]")
    print(f"  {'✓' if vis_file.exists() else '✗'} 仪表盘")

    # 记忆统计
    print(f"\n[记忆]")
    mem_files = [
        (paths.chapter_summaries, "章节摘要"),
        (paths.event_log, "事件日志"),
        (paths.foreshadowing, "伏笔"),
    ]
    for path, label in mem_files:
        if path.exists():
            import json
            data = json.loads(path.read_text(encoding="utf-8") or "[]")
            count = len(data) if isinstance(data, list) else 0
            print(f"  ✓ {label}: {count} 条")
        else:
            print(f"  ✗ {label}: 不存在")

    # 关系进度
    ri_path = paths.relationship_state
    if ri_path.exists():
        ri = load_yaml(ri_path)
        print(f"\n[关系进度]")
        print(f"  阶段: {ri.get('relationship_stage', '未知')}")
        print(f"  好感度: {ri.get('romance_level', 0)}/100")
        print(f"  信任度: {ri.get('trust_level', 0)}/100")
        print(f"  冲突度: {ri.get('conflict_level', 0)}/100")


def main():
    parser = argparse.ArgumentParser(
        description="ShortFictionAgent / StoryStudio - 中文故事创作系统",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    from .story_studio.cli import add_parsers as add_story_parsers
    add_story_parsers(subparsers)

    # init
    p_init = subparsers.add_parser("init", help="初始化新小说项目")
    p_init.add_argument("--project", required=True, help="项目目录路径")
    p_init.add_argument("--title", help="小说书名")
    p_init.add_argument("--stage", help="阶段/处境（如 高中/大学/落魄归来/重生归来）")
    p_init.add_argument("--target-words", type=int, help="目标字数")
    p_init.add_argument("--genre", help="题材: campus(青春校园恋爱, 默认) / urban(都市, 可含重生)")

    # create-setting
    p_setting = subparsers.add_parser("create-setting", help="创建小说基础设定")
    p_setting.add_argument("--project", required=True, help="项目目录路径")
    p_setting.add_argument("--title", help="小说书名")
    p_setting.add_argument("--stage", help="校园阶段（高中/大学）")
    p_setting.add_argument("--target-words", type=int, help="目标字数")

    # plan-book
    p_plan_book = subparsers.add_parser("plan-book", help="生成全书大纲")
    p_plan_book.add_argument("--project", required=True, help="项目目录路径")

    # plan-chapters
    p_plan_chapters = subparsers.add_parser("plan-chapters", help="生成章节大纲")
    p_plan_chapters.add_argument("--project", required=True, help="项目目录路径")
    p_plan_chapters.add_argument("--volume", type=int, default=1, help="卷号")

    # update-outline (v0.2 新命令)
    p_update_outline = subparsers.add_parser("update-outline", help="增量更新大纲：根据已写章节调整后续大纲")
    p_update_outline.add_argument("--project", required=True, help="项目目录路径")
    p_update_outline.add_argument("--from-chapter", required=True, type=int, help="从第几章开始调整（之前章节已完成）")

    # write
    p_write = subparsers.add_parser("write", help="编写章节")
    p_write.add_argument("--project", required=True, help="项目目录路径")
    p_write.add_argument("--chapter", required=True, type=int, help="章节号")
    p_write.add_argument("--no-preflight", action="store_true", help="跳过 LLM API 预检")

    # review
    p_review = subparsers.add_parser("review", help="审查章节（含阈值告警）")
    p_review.add_argument("--project", required=True, help="项目目录路径")
    p_review.add_argument("--chapter", required=True, type=int, help="章节号")
    p_review.add_argument("--no-threshold", action="store_true", help="跳过阈值检查")

    # batch-review (v0.2 新命令)
    p_batch_review = subparsers.add_parser("batch-review", help="批量审查章节")
    p_batch_review.add_argument("--project", required=True, help="项目目录路径")
    p_batch_review.add_argument("--from-chapter", required=True, type=int, help="起始章节号")
    p_batch_review.add_argument("--to-chapter", required=True, type=int, help="结束章节号")

    # check-consistency (v0.2 新命令)
    p_consistency = subparsers.add_parser("check-consistency", help="跨章节一致性检查")
    p_consistency.add_argument("--project", required=True, help="项目目录路径")
    p_consistency.add_argument("--from-chapter", type=int, default=1, help="起始章节号")
    p_consistency.add_argument("--to-chapter", type=int, help="结束章节号（默认自动检测）")

    # check-entities (v0.3 新命令 — 实体一致性)
    p_entity = subparsers.add_parser("check-entities", help="实体一致性检查（人物姓名/性别/班级/住校/物件归属）")
    p_entity.add_argument("--project", required=True, help="项目目录路径")
    p_entity.add_argument("--from-chapter", type=int, default=1, help="起始章节号")
    p_entity.add_argument("--to-chapter", type=int, help="结束章节号（默认自动检测）")

    # visualize (v0.2 新命令)
    p_visualize = subparsers.add_parser("visualize", help="生成可视化报告")
    p_visualize.add_argument("--project", required=True, help="项目目录路径")
    p_visualize.add_argument("--type", choices=["full", "relationship", "rel", "review", "rev", "stats"],
                             default="full", help="可视化类型（默认full=全部）")

    # update-memory
    p_memory = subparsers.add_parser("update-memory", help="更新记忆")
    p_memory.add_argument("--project", required=True, help="项目目录路径")
    p_memory.add_argument("--chapter", required=True, type=int, help="章节号")

    # revise
    p_revise = subparsers.add_parser("revise", help="生成修改计划")
    p_revise.add_argument("--project", required=True, help="项目目录路径")
    p_revise.add_argument("--chapter", required=True, type=int, help="章节号")

    # query
    p_query = subparsers.add_parser("query", help="搜索关键词")
    p_query.add_argument("--project", required=True, help="项目目录路径")
    p_query.add_argument("--keyword", required=True, help="搜索关键词")

    # check-reality
    p_reality = subparsers.add_parser("check-reality", help="现实逻辑审核：检查内容是否符合现实世界规则")
    p_reality.add_argument("--project", required=True, help="项目目录路径")
    p_reality.add_argument("--chapter", type=int, help="章节号（不指定则审核全书大纲）")

    # check-narrative
    p_narrative = subparsers.add_parser("check-narrative", help="商业追读结构质检：检查大纲或正文是否有故事驱动力")
    p_narrative.add_argument("--project", required=True, help="项目目录路径")
    p_narrative.add_argument("--chapter", type=int, help="章节号（不指定则检查所有章节大纲）")
    p_narrative.add_argument("--text", action="store_true", help="检查正文而不是章节大纲")

    # check-commercial
    p_commercial = subparsers.add_parser("check-commercial", help="八模块商业编辑质检：开篇、事件硬度、名场面、兑现、人设、配角、去重、长悬念")
    p_commercial.add_argument("--project", required=True, help="项目目录路径")
    p_commercial.add_argument("--chapter", type=int, help="章节号（不指定则检查全书商业结构）")
    p_commercial.add_argument("--text", action="store_true", help="检查正文语言重复风险")

    # check-canon
    p_canon = subparsers.add_parser("check-canon", help="角色设定同步检查：发现 characters/*.yaml 是否落后于正文/大纲")
    p_canon.add_argument("--project", required=True, help="项目目录路径")
    p_canon.add_argument("--apply", action="store_true", help="保守同步已确认的男女主角色设定")

    # write-volume (管线)
    p_write_vol = subparsers.add_parser("write-volume", help="一口气写完整卷所有章节")
    p_write_vol.add_argument("--project", required=True, help="项目目录路径")
    p_write_vol.add_argument("--volume", required=True, type=int, help="卷号")
    p_write_vol.add_argument("--no-revise", action="store_true", help="关闭自动修订（仅写+审，不自动改）")
    p_write_vol.add_argument("--no-preflight", action="store_true", help="跳过 LLM API 预检")
    p_write_vol.add_argument("--dry-run", action="store_true", help="试运行: 用 Mock 双模型秒级跑通全链路, 不花 API 费用")
    p_write_vol.add_argument("--llm-task", action="store_true", help="用 LLM 生成写作任务书(默认确定性拼装更快更稳; 此开关恢复 LLM 版做 A/B)")
    p_write_vol.add_argument("--mode", choices=["submission"],
                             default="submission", help="管线架构模式（目前仅 submission=投稿终稿模式）")

    # write-all (管线)
    p_write_all = subparsers.add_parser("write-all", help="一口气写完整本小说")
    p_write_all.add_argument("--project", required=True, help="项目目录路径")
    p_write_all.add_argument("--no-revise", action="store_true", help="关闭自动修订")
    p_write_all.add_argument("--no-preflight", action="store_true", help="跳过 LLM API 预检")
    p_write_all.add_argument("--dry-run", action="store_true", help="试运行: 用 Mock 双模型秒级跑通全链路, 不花 API 费用")
    p_write_all.add_argument("--llm-task", action="store_true", help="用 LLM 生成写作任务书(默认确定性拼装更快更稳; 此开关恢复 LLM 版做 A/B)")
    p_write_all.add_argument("--mode", choices=["submission"],
                             default="submission", help="管线架构模式（目前仅 submission=投稿终稿模式）")

    # write-range (管线)
    p_write_range = subparsers.add_parser("write-range", help="一口气写指定范围的章节")
    p_write_range.add_argument("--project", required=True, help="项目目录路径")
    p_write_range.add_argument("--from-chapter", required=True, type=int, help="起始章节号")
    p_write_range.add_argument("--to-chapter", required=True, type=int, help="结束章节号")
    p_write_range.add_argument("--no-revise", action="store_true", help="关闭自动修订")
    p_write_range.add_argument("--no-preflight", action="store_true", help="跳过 LLM API 预检")
    p_write_range.add_argument("--dry-run", action="store_true", help="试运行: 用 Mock 双模型秒级跑通全链路, 不花 API 费用")
    p_write_range.add_argument("--llm-task", action="store_true", help="用 LLM 生成写作任务书(默认确定性拼装更快更稳; 此开关恢复 LLM 版做 A/B)")
    p_write_range.add_argument("--mode", choices=["submission"],
                               default="submission", help="管线架构模式（目前仅 submission=投稿终稿模式）")

    # submission-check
    p_submission = subparsers.add_parser("submission-check", help="运行番茄投稿阶段门禁，不生成新章节")
    p_submission.add_argument("--project", required=True, help="项目目录路径")
    p_submission.add_argument("--gate", choices=["opening10", "rolling", "payoff", "arc", "final"],
                              default="final", help="门禁类型")
    p_submission.add_argument("--from-chapter", type=int, default=1, help="起始章节号")
    p_submission.add_argument("--to-chapter", type=int, help="结束章节号（默认自动检测）")

    # pipeline-status
    p_pipe_status = subparsers.add_parser("pipeline-status", help="查看管线进度")
    p_pipe_status.add_argument("--project", required=True, help="项目目录路径")

    # pipeline-reset
    p_pipe_reset = subparsers.add_parser("pipeline-reset", help="重置管线状态（清除断点）")
    p_pipe_reset.add_argument("--project", required=True, help="项目目录路径")

    # eval (评测/回归 harness, 题材无关)
    p_eval = subparsers.add_parser("eval", help="评测/回归: 跑 fixtures 打分存快照, 或对比两份快照检测回归")
    p_eval.add_argument("--project", help="用该项目的 model.yaml 跑真实 API 质量回归; 不给则 mock 结构回归")
    p_eval.add_argument("--out", help="快照输出路径(默认 eval_snapshot.json)")
    p_eval.add_argument("--deterministic-task", action="store_true", help="评测时用确定性任务书(不调 LLM 生成任务书)")
    p_eval.add_argument("--compare", nargs=2, metavar=("BASELINE", "CURRENT"), help="对比两份快照, 检测回归")

    # genres
    subparsers.add_parser("genres", help="列出全部可用题材(男频/女频)")

    # pipeline-guard
    p_pipe_guard = subparsers.add_parser("pipeline-guard", help="进程护栏：列出/清理本项目残留管线进程")
    p_pipe_guard.add_argument("--project", required=True, help="项目目录路径")
    p_pipe_guard.add_argument("--dedupe", action="store_true", help="发现重复管线时杀掉多余的（保留最新）")
    p_pipe_guard.add_argument("--kill-all", action="store_true", help="杀掉本项目所有管线进程（重启前清场）")

    # status
    p_status = subparsers.add_parser("status", help="查看项目状态")
    p_status.add_argument("--project", required=True, help="项目目录路径")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command in {"create", "develop", "plan", "add-inspiration", "list-inspiration", "discover-inspiration",
                        "generate", "inspect", "revise-story",
                        "export", "import-legacy", "record-feedback", "analyze-feedback", "use-feedback", "audit-workflow",
                        "prepare-publication", "match-campaigns"}:
        from .story_studio.cli import run as run_story
        sys.exit(run_story(args))

    commands = {
        "init": cmd_init,
        "create-setting": cmd_create_setting,
        "plan-book": cmd_plan_book,
        "plan-chapters": cmd_plan_chapters,
        "update-outline": cmd_update_outline,
        "write": cmd_write,
        "review": cmd_review,
        "batch-review": cmd_batch_review,
        "check-consistency": cmd_check_consistency,
        "check-entities": cmd_check_entities,
        "visualize": cmd_visualize,
        "update-memory": cmd_update_memory,
        "revise": cmd_revise,
        "query": cmd_query,
        "check-reality": cmd_check_reality,
        "check-narrative": cmd_check_narrative,
        "check-commercial": cmd_check_commercial,
        "check-canon": cmd_check_canon,
        "write-volume": cmd_write_volume,
        "write-all": cmd_write_all,
        "write-range": cmd_write_range,
        "pipeline-status": cmd_pipeline_status,
        "pipeline-reset": cmd_pipeline_reset,
        "pipeline-guard": cmd_pipeline_guard,
        "genres": cmd_genres,
        "eval": cmd_eval,
        "submission-check": cmd_submission_check,
        "status": cmd_status,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
