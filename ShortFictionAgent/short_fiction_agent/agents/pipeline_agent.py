"""PipelineAgent - 自动化长篇小说管线编排器。

支持：
- 一键写完整卷 / 整本书
- 自动审→改循环（审查不通过自动修订重写）
- 断点续传（每章保存进度，中断后可恢复）
- 滚动记忆压缩（每10章生成卷摘要）
- 定期一致性检查和现实逻辑审核
"""

import json
import time as time_module
from pathlib import Path
from datetime import datetime
from ..config import save_markdown, load_markdown, load_yaml, save_yaml
from ..paths import ProjectPaths
from ..llm import LLMClient
from ..memory import FileMemoryStore
from .context_agent import ContextAgent
from .writer_agent import WriterAgent
from .review_agent import ReviewAgent
from .data_agent import DataAgent
from .revision_agent import RevisionAgent
from .narrative_guard_agent import NarrativeGuardAgent
from .commercial_editor_agent import CommercialQualitySuite
from .submission_gate_agent import SubmissionGateAgent
from ..longform import LongformCheckpoints, atomic_json, validate_chapter_plan, review_reader_progress


class PipelineAgent:
    """长篇小说自动化创作管线。

    将 write → review → revise → update-memory 串联为一个自动化流程，
    支持断点续传和滚动记忆管理。
    """

    # 管线配置常量
    VOLUME_SUMMARY_INTERVAL = 10   # 每 N 章生成一次卷摘要
    CONSISTENCY_CHECK_INTERVAL = 5  # 每 N 章检查一致性
    REALITY_CHECK_INTERVAL = 10     # 每 N 章审核现实逻辑
    MAX_REVISIONS = 3               # 每章最多修订次数
    MAX_RETRY_ROUNDS = 2            # 管线结束后回补失败章节的最大轮数
    STALL_TIMEOUT_MINUTES = 20      # 单章无进展超时（分钟），超时自动跳过
    MAX_CONSECUTIVE_STALLS = 4      # 连续卡死次数上限，超此数自动熔断休息
    PIPELINE_MODES = {"submission"}  # 唯一模式：全部门禁+终稿检查
    MODE_CONFIG = {
        "submission": {
            "max_revisions": 1,
            "stage_gates": True,
            "final_gate": True,
            "description": "投稿终稿模式：单章修订 + 每5章硬门禁 + 终稿检查",
        },
    }

    def __init__(self, paths: ProjectPaths, llm: LLMClient, memory: FileMemoryStore,
                 fallback_llm: LLMClient | None = None,
                 router=None, dry_run: bool = False, use_task_agent: bool = False):
        self.paths = paths
        self.llm = llm
        self.fallback_llm = fallback_llm
        self.memory = memory
        self.router = router
        # 试运行: 跳过章节间真实冷却 sleep, 让 mock 闭环秒级跑完(问题: 无试运行模式)
        self.dry_run = dry_run
        # 是否用 LLM 生成写作任务书(ContextAgent 这一跳)。False=确定性拼装, 省一次 flash。
        self.use_task_agent = use_task_agent

        # 子 Agent（传递 fallback_llm 以启用超时降级）
        self.context_agent = ContextAgent(paths, llm, memory)
        self.writer_agent = WriterAgent(paths, llm, memory)
        self.review_agent = ReviewAgent(paths, llm, memory)
        self.data_agent = DataAgent(paths, llm, memory)
        self.revision_agent = RevisionAgent(paths, llm)
        # 为所有 AgentRuntime 子 Agent 注入 fallback + 集中式路由器
        for agent in [self.context_agent, self.writer_agent,
                      self.review_agent, self.data_agent, self.revision_agent]:
            if not hasattr(agent, 'runtime'):
                continue
            if fallback_llm and hasattr(agent.runtime, 'fallback_llm'):
                agent.runtime.fallback_llm = fallback_llm
            if router is not None:
                agent.runtime.router = router
        # 题材包: 从项目 novel.yaml 的 genre 解析(默认校园)。质检/门禁分析器
        # 据此取用题材专属词表, 换题材不改引擎代码。
        from ..genres import resolve_project_pack
        self.genre_pack = resolve_project_pack(paths)
        self.narrative_guard = NarrativeGuardAgent(paths, self.genre_pack)
        self.commercial_suite = CommercialQualitySuite(paths, self.genre_pack)
        self.submission_gate = SubmissionGateAgent(paths, memory, self.genre_pack)

    def _chapter_outline(self, chapter_id: int) -> dict:
        """取某章的大纲 dict（供机械门禁检查核心结构字段）。"""
        outlines = load_yaml(self.paths.chapter_outline)
        for ch in (outlines.get("chapters", []) if isinstance(outlines, dict) else []):
            if isinstance(ch, dict) and ch.get("chapter_id") == chapter_id:
                return ch
        return {}

    def _generate_task(self, ch_id: int):
        """生成写作任务书：LLM 版(默认) 或 确定性版(--deterministic-task)。"""
        if self.use_task_agent:
            return self.context_agent.generate_writing_task(ch_id)
        return self.context_agent.generate_writing_task_deterministic(ch_id)

    # ========================================================================
    # 公开 API
    # ========================================================================

    def write_volume(self, volume: int, auto_revise: bool = True,
                     pipeline_mode: str = "submission") -> dict:
        """写完整卷的所有章节。"""
        chapters = self._get_volume_chapters(volume)
        if not chapters:
            return {"error": f"第 {volume} 卷没有章节大纲，请先执行 plan-chapters --volume {volume}"}

        from_ch = chapters[0]["chapter_id"]
        to_ch = chapters[-1]["chapter_id"]
        return self.write_range(from_ch, to_ch, auto_revise=auto_revise, pipeline_mode=pipeline_mode)

    def write_all(self, auto_revise: bool = True,
                  pipeline_mode: str = "submission") -> dict:
        """从第 1 章写到最后一章（整本书）。"""
        all_chapters = self._get_all_chapters()
        if not all_chapters:
            return {"error": "没有章节大纲，请先执行 plan-chapters"}

        from_ch = all_chapters[0]["chapter_id"]
        to_ch = all_chapters[-1]["chapter_id"]
        return self.write_range(from_ch, to_ch, auto_revise=auto_revise, pipeline_mode=pipeline_mode)

    def write_range(self, from_chapter: int, to_chapter: int,
                    auto_revise: bool = True,
                    pipeline_mode: str = "submission") -> dict:
        """写指定范围的章节（带进程生命周期护栏）。

        彻底解决"被 kill→重启 导致的卡死": 单实例锁(杜绝多管线同写一本书) +
        启动清理孤儿 LLM 子进程(回收泄漏 socket) + 信号处理(被 kill 时杀子进程、
        释放锁、干净退出, 不留孤儿)。dry_run 下跳过(测试/eval 不需要)。
        """
        if self.dry_run:
            return self._write_range_impl(from_chapter, to_chapter, auto_revise, pipeline_mode)

        import os as _os, signal as _signal, atexit as _atexit
        from ..process_guard import (acquire_singleton_lock, release_singleton_lock,
                                      kill_orphan_llm_children)
        from ..agent_runtime import kill_active_children

        lock_path = self.paths.memory_dir / ".pipeline.lock"
        self.paths.memory_dir.mkdir(parents=True, exist_ok=True)
        if not acquire_singleton_lock(lock_path):
            msg = "另一个管线正在写本书(单实例锁)。先停止它(pipeline-guard --kill-all)或等它退出。"
            print(f"🛑 {msg}")
            return {"error": msg}
        # 清理上一轮被 SIGKILL 的父进程留下的孤儿 LLM 子进程(回收泄漏 socket)
        try:
            orphans = kill_orphan_llm_children(self.paths.root)
            if orphans:
                print(f"  [清理] 已杀掉 {len(orphans)} 个残留 LLM 子进程孤儿: {orphans}")
        except Exception:
            pass

        _state = {"released": False}

        def _cleanup():
            if _state["released"]:
                return
            _state["released"] = True
            try:
                kill_active_children()   # 带走当前活跃的 LLM 子进程, 不留孤儿
            except Exception:
                pass
            release_singleton_lock(lock_path)

        def _on_signal(signum, frame):
            print(f"\n[管线] 收到信号 {signum}: 杀子进程 + 释放锁 + 干净退出。")
            _cleanup()
            _os._exit(130)

        _atexit.register(_cleanup)
        try:
            _signal.signal(_signal.SIGTERM, _on_signal)
            _signal.signal(_signal.SIGINT, _on_signal)
        except (ValueError, OSError):
            pass  # 非主线程无法装信号, 忽略

        try:
            return self._write_range_impl(from_chapter, to_chapter, auto_revise, pipeline_mode)
        finally:
            _cleanup()

    def _write_range_impl(self, from_chapter: int, to_chapter: int,
                          auto_revise: bool = True,
                          pipeline_mode: str = "submission") -> dict:
        """写指定范围的章节（核心逻辑）。"""
        pipeline_mode = self._normalize_pipeline_mode(pipeline_mode)
        mode_config = self.MODE_CONFIG[pipeline_mode]
        max_revisions = mode_config["max_revisions"] if auto_revise else 0
        print(f"\n{'='*60}")
        print(f"  管线启动：第 {from_chapter} 章 → 第 {to_chapter} 章")
        print(f"  架构模式: {pipeline_mode}（{mode_config['description']}）")
        print(f"  自动修订: {'开启' if auto_revise else '关闭'}")
        print(f"  单章最大修订: {max_revisions} 轮")
        print(f"{'='*60}\n")

        checkpoints = LongformCheckpoints(self.paths)
        dirty = checkpoints.dirty_from()
        if dirty is not None:
            return {"success": False, "error": f"第{dirty}章正文或章纲已改变，请先回滚该章及后文", "dirty_from": dirty}
        checkpoint_data = checkpoints.load()
        # A crash can leave partially updated canonical memory; undo it before resume.
        checkpoints.rollback()
        state = self._load_state()
        if not state:
            state = self._init_state(from_chapter, to_chapter, auto_revise, pipeline_mode)
        else:
            state.update(pipeline_active=True, from_chapter=from_chapter,
                         to_chapter=to_chapter, auto_revise=auto_revise, pipeline_mode=pipeline_mode)
            state["chapter_statuses"] = {key: "completed" for key in checkpoint_data["chapters"]}
            state["last_completed"] = max((int(k) for k in checkpoint_data["chapters"]), default=0)
            self._save_state(state)

        results = []
        total = to_chapter - from_chapter + 1
        start_time = time_module.time()

        for ch_id in range(from_chapter, to_chapter + 1):
            chapter_start = time_module.time()

            # 检查是否已完成
            if self._is_chapter_done(state, ch_id):
                print(f"  [跳过] 第 {ch_id} 章已完成，跳过")
                results.append({"chapter_id": ch_id, "status": "skipped", "reason": "already_completed"})
                continue

            print(f"\n{'─'*50}")
            print(f"  📖 第 {ch_id} 章 / 共 {to_chapter} 章 ({ch_id - from_chapter + 1}/{total})")
            print(f"{'─'*50}")

            checkpoints.begin(ch_id)

            # ── 看门狗: 检测卡死 ──
            stall_key = f"last_activity_{ch_id}"
            last_activity = state.get(stall_key, time_module.time())
            stall_minutes = (time_module.time() - last_activity) / 60
            if stall_minutes > self.STALL_TIMEOUT_MINUTES and ch_id > from_chapter:
                consecutive = state.get("consecutive_stalls", 0) + 1
                state["consecutive_stalls"] = consecutive
                print(f"  ⏰ 看门狗: 第 {ch_id} 章 {stall_minutes:.0f} 分钟无进展 → 超时跳过")
                # If too many consecutive stalls, take a break to let API recover
                if consecutive >= self.MAX_CONSECUTIVE_STALLS:
                    cooldown = 300  # 5 minutes
                    print(f"  🔥 连续 {consecutive} 次卡死，API 可能过载。冷却 {cooldown}s...")
                    time_module.sleep(cooldown)
                    state["consecutive_stalls"] = 0
                self._save_chapter_status(state, ch_id, "stall_timeout",
                    f"看门狗: {stall_minutes:.0f}min 无进展, 自动跳过")
                self._save_state(state)
                results.append({"chapter_id": ch_id, "status": "failed",
                    "stage": "stall_timeout",
                    "error": f"看门狗超时 {stall_minutes:.0f}min"})
                # 重置计时器，否则后续所有章节都会被跳过
                state[stall_key] = time_module.time()
                self._save_state(state)
                break
            state["consecutive_stalls"] = 0
            state[stall_key] = time_module.time()
            self._save_state(state)

            commercial_outline = None
            text_guard = None
            commercial_text = None
            current_outline = self._chapter_outline(ch_id)  # 供机械门禁检查核心字段

            try:
                validate_chapter_plan(current_outline)
            except ValueError as exc:
                results.append({"chapter_id": ch_id, "status": "failed", "stage": "reader_plan", "error": str(exc)})
                self._save_chapter_status(state, ch_id, "reader_plan_failed", str(exc))
                break

            # === 0. 追读结构质检 ===
            print(f"  [1/5] 追读结构质检...")
            outline_guard = self.narrative_guard.audit_outline(ch_id)
            if outline_guard.get("passed"):
                print(f"    章纲追读评分: {outline_guard.get('score')}/100")
            else:
                print(f"    章纲追读评分: {outline_guard.get('score')}/100 ⚠️")
                for warning in outline_guard.get("warnings", [])[:3]:
                    print(f"    - {warning}")
            try:
                commercial_outline = self.commercial_suite.audit_outline(ch_id)
                print(f"    商业编辑评分: {commercial_outline.get('score')}/100")
                for warning in commercial_outline.get("warnings", [])[:2]:
                    print(f"    - {warning}")
            except Exception as e:
                print(f"    [警告] 商业编辑章纲质检失败: {e}")

            # === 1. 生成写作任务书 ===
            print(f"  [2/5] 生成写作任务书...")
            try:
                self._generate_task(ch_id)
            except Exception as e:
                print(f"  [错误] 任务书生成失败: {e}")
                self._save_chapter_status(state, ch_id, "task_failed", str(e))
                self._save_state(state)
                results.append({"chapter_id": ch_id, "status": "failed", "stage": "task", "error": str(e)})
                break

            # === 2. 写章节 ===
            print(f"  [3/5] 撰写章节...")
            try:
                content = self.writer_agent.write_chapter(ch_id)
                valid, reason = self._validate_chapter_content(content)
                if not valid:
                    raise RuntimeError(f"章节生成失败: {reason}; {content[:100]}")
            except Exception as e:
                print(f"  [错误] 章节撰写失败: {e}")
                self._save_chapter_status(state, ch_id, "write_failed", str(e))
                self._save_state(state)
                results.append({"chapter_id": ch_id, "status": "failed", "stage": "write", "error": str(e)})
                break

            word_count = self._count_chinese_chars(content)
            print(f"    字数: ~{word_count} 字")

            try:
                text_guard = self.narrative_guard.audit_chapter_text(ch_id)
                self.narrative_guard.save_chapter_text_report(ch_id, text_guard)
                if text_guard.get("passed"):
                    print(f"    正文追读评分: {text_guard.get('score')}/100")
                else:
                    print(f"    正文追读评分: {text_guard.get('score')}/100 ⚠️")
                commercial_text = self.commercial_suite.audit_chapter_text(ch_id)
                self.commercial_suite.save_chapter_report(ch_id, commercial_outline, commercial_text)
                print(f"    正文商业去重评分: {commercial_text.get('score')}/100")
            except Exception as e:
                print(f"  [警告] 正文追读质检失败: {e}")

            # === 3. 审查 + 自动修订循环 ===
            print(f"  [4/5] 审查章节...")
            try:
                review_result = self.review_agent.review_chapter(ch_id)
            except Exception as e:
                print(f"  [错误] 审查失败: {e}")
                self._save_chapter_status(state, ch_id, "review_failed", str(e))
                self._save_state(state)
                results.append({"chapter_id": ch_id, "status": "failed", "stage": "review", "error": str(e)})
                break

            if review_result and auto_revise:
                revise_count = 0
                review_blocked_error = None
                while revise_count < max_revisions:
                    gate = self._quality_revision_gate(
                        review_result=review_result,
                        chapter_id=ch_id,
                        content=content,
                        current_outline=current_outline,
                        advisory_results=[outline_guard, text_guard, commercial_outline, commercial_text],
                    )
                    score = gate.get("review_score")
                    if gate.get("passed"):
                        score_text = f"{score}/100" if score is not None else "N/A"
                        print(f"    质量门禁(LLM语义+机械): ✅ 通过（审查评分: {score_text}）")
                        break

                    score_text = f"{score}/100" if score is not None else "N/A"
                    print(f"    质量门禁(LLM语义+机械): ⚠️ 未通过（审查评分: {score_text}），自动修订第 {revise_count + 1} 次...")
                    for reason in gate.get("reasons", [])[:5]:
                        print(f"    - [硬] {reason}")
                    for hint in gate.get("advisory", [])[:3]:
                        print(f"    - [提示] {hint}")

                    try:
                        # 精简: 不再起 RevisionAgent 单独生成修订计划(多一次 flash +
                        # 一个卡死点)。WriterAgent 本就直接读审查/门禁/现实/一致性报告。
                        content = self.writer_agent.write_chapter(ch_id)
                        valid, reason = self._validate_chapter_content(content)
                        if not valid:
                            raise RuntimeError(f"修订后章节无效: {reason}")
                        word_count = self._count_chinese_chars(content)
                        print(f"    修订后字数: ~{word_count} 字")
                        try:
                            text_guard = self.narrative_guard.audit_chapter_text(ch_id)
                            self.narrative_guard.save_chapter_text_report(ch_id, text_guard)
                            print(f"    修订后正文追读评分: {text_guard.get('score')}/100")
                            commercial_text = self.commercial_suite.audit_chapter_text(ch_id)
                            self.commercial_suite.save_chapter_report(ch_id, commercial_outline, commercial_text)
                            print(f"    修订后商业去重评分: {commercial_text.get('score')}/100")
                        except Exception as guard_error:
                            print(f"    [警告] 修订后追读质检失败: {guard_error}")
                    except Exception as e:
                        print(f"    [警告] 修订失败: {e}，使用当前版本")
                        break

                    # 重新审查
                    try:
                        review_result = self.review_agent.review_chapter(ch_id)
                    except Exception as e:
                        review_blocked_error = str(e)
                        break

                    revise_count += 1

                if review_blocked_error:
                    print(f"    [错误] 修订后重新审查失败: {review_blocked_error}")
                    self._save_chapter_status(state, ch_id, "review_failed", review_blocked_error)
                    self._save_state(state)
                    results.append({
                        "chapter_id": ch_id,
                        "status": "failed",
                        "stage": "review_after_revision",
                        "error": review_blocked_error,
                    })
                    break

                if max_revisions > 0 and revise_count >= max_revisions:
                    final_score = self._extract_score(review_result)
                    final_gate = self._quality_revision_gate(
                        review_result=review_result,
                        chapter_id=ch_id,
                        content=content,
                        current_outline=current_outline,
                        advisory_results=[outline_guard, text_guard, commercial_outline, commercial_text],
                    )
                    print(f"    已修订 {max_revisions} 次，最终审查评分: {final_score}/100（继续使用当前版本）")
                    if not final_gate.get("passed"):
                        print("    剩余质量风险:")
                        for reason in final_gate.get("reasons", [])[:5]:
                            print(f"    - {reason}")
            elif review_result:
                score = self._extract_score(review_result)
                if score is not None:
                    print(f"    审查评分: {score}/100")

            # A generated chapter is not an approved chapter. Never commit its
            # facts to long-term memory when the last review still fails.
            final_quality = self._quality_revision_gate(
                review_result=review_result,
                chapter_id=ch_id,
                content=content,
                current_outline=current_outline,
                advisory_results=[outline_guard, text_guard, commercial_outline, commercial_text],
            )
            if not final_quality["passed"]:
                reason = "; ".join(final_quality["reasons"])
                self._save_chapter_status(state, ch_id, "quality_failed", reason)
                self._save_state(state)
                results.append({"chapter_id": ch_id, "status": "failed", "stage": "quality", "error": reason})
                break

            try:
                reader_report = review_reader_progress(self.paths, ch_id, self.review_agent.runtime)
                if not reader_report["passed"]:
                    # Keep concrete feedback available to the writer on the next attempt.
                    save_markdown(self.paths.revision_file(ch_id),
                                  json.dumps(reader_report, ensure_ascii=False, indent=2))
                    raise RuntimeError("本章读者回报/跨章推进未通过，须修订后继续")
            except Exception as exc:
                self._save_chapter_status(state, ch_id, "reader_progress_failed", str(exc))
                self._save_state(state)
                results.append({"chapter_id": ch_id, "status": "failed", "stage": "reader_progress", "error": str(exc)})
                break

            # === 4. 更新记忆 ===
            print(f"  [5/5] 更新记忆...")
            try:
                memory_ok = self.data_agent.update_memory(ch_id)
                if not memory_ok:
                    raise RuntimeError("DataAgent 未确认记忆更新成功")
            except Exception as e:
                print(f"  [错误] 记忆更新失败，章节不能标记完成: {e}")
                self._save_chapter_status(state, ch_id, "memory_failed", str(e))
                self._save_state(state)
                results.append({"chapter_id": ch_id, "status": "failed", "stage": "memory", "error": str(e)})
                break

            maintenance_errors = []

            # === 定期维护 ===
            # 每 N 章：滚动记忆压缩
            if ch_id % self.VOLUME_SUMMARY_INTERVAL == 0:
                vol_start = max(1, ch_id - self.VOLUME_SUMMARY_INTERVAL + 1)
                print(f"  [维护] 生成第 {vol_start}-{ch_id} 章卷摘要...")
                try:
                    self.memory.generate_volume_summary(vol_start, ch_id, llm_client=self.llm)
                except Exception as e:
                    print(f"  [警告] 卷摘要生成失败: {e}")

                # 同时压缩早期事件日志（控制 Token 膨胀）
                print(f"  [维护] 压缩早期事件日志...")
                try:
                    stats_before = self.memory.get_events_stats()
                    result = self.memory.compact_events(ch_id, recent_n=5, llm_client=self.llm)
                    if result:
                        stats_after = self.memory.get_events_stats()
                        print(f"    事件日志: {stats_before['total']} 条 → 最近 5 章保留详情，"
                              f"早期 {result.get('original_event_count', 0)} 条已压缩为摘要")
                except Exception as e:
                    print(f"  [警告] 事件压缩失败: {e}")

            # 每 N 章：一致性检查（发现问题→标记返修）
            if ch_id % self.CONSISTENCY_CHECK_INTERVAL == 0 and ch_id > 1:
                check_from = max(1, ch_id - self.CONSISTENCY_CHECK_INTERVAL + 1)
                print(f"  [维护] 一致性检查（第 {check_from}-{ch_id} 章）...")
                try:
                    from .consistency_agent import ConsistencyAgent
                    ca = ConsistencyAgent(self.paths, self.llm, self.memory, self.genre_pack)
                    if hasattr(ca, 'runtime'):
                        if self.fallback_llm:
                            ca.runtime.fallback_llm = self.fallback_llm
                        if self.router is not None:
                            ca.runtime.router = self.router
                    report = ca.check_all(from_chapter=check_from, to_chapter=ch_id)
                    con_score = report.get("overall_score", -1)
                    icon = "✅" if con_score >= 95 else "⚠️" if con_score >= 80 else "🔴"
                    print(f"    一致性评分: {con_score}/100 {icon}")
                    if con_score < 80:
                        maintenance_errors.append(f"第{check_from}-{ch_id}章一致性未通过")
                        print(f"    [返修] 一致性评分过低，标记第{check_from}-{ch_id}章返修")
                        for rt_ch in range(check_from, ch_id + 1):
                            if rt_ch < from_chapter or rt_ch > to_chapter:
                                continue
                            existing = next((r for r in results if r.get("chapter_id") == rt_ch), None)
                            if existing and existing.get("status") == "completed":
                                existing["status"] = "stage_gate_retry"
                                existing["stage"] = "consistency"
                except Exception as e:
                    print(f"  [警告] 一致性检查失败: {e}")
                    maintenance_errors.append(f"一致性检查异常: {e}")

            # 每 N 章：现实逻辑审核（发现问题→标记返修）
            if ch_id % self.REALITY_CHECK_INTERVAL == 0:
                print(f"  [维护] 现实逻辑审核...")
                try:
                    from .reality_agent import RealityAgent
                    ra = RealityAgent(self.paths, self.llm, self.memory)
                    if hasattr(ra, 'runtime'):
                        if self.fallback_llm:
                            ra.runtime.fallback_llm = self.fallback_llm
                        if self.router is not None:
                            ra.runtime.router = self.router
                    result = ra.check_chapter_with_summary(ch_id)
                    if result.get("passed"):
                        print(f"    现实逻辑: ✅")
                    else:
                        maintenance_errors.append("现实逻辑审核未通过")
                        issues = result.get("issues", {})
                        severe = issues.get("severe", 0)
                        print(f"    现实逻辑: ⚠️ 发现 {severe} 个严重问题")
                        if severe > 0:
                            print(f"    [返修] 现实逻辑严重问题，标记第{ch_id}章返修")
                            existing = next((r for r in results if r.get("chapter_id") == ch_id), None)
                            if existing and existing.get("status") == "completed":
                                existing["status"] = "stage_gate_retry"
                                existing["stage"] = "reality"
                            elif not existing:
                                results.append({
                                    "chapter_id": ch_id, "status": "stage_gate_retry",
                                    "stage": "reality",
                                    "error": f"现实逻辑审核发现{severe}个严重问题",
                                })
                except Exception as e:
                    print(f"  [警告] 现实逻辑审核失败: {e}")
                    maintenance_errors.append(f"现实逻辑审核异常: {e}")

            if maintenance_errors:
                self._save_chapter_status(state, ch_id, "maintenance_failed", "; ".join(maintenance_errors))
                self._save_state(state)
                results.append({"chapter_id": ch_id, "status": "failed", "stage": "maintenance",
                                "error": "; ".join(maintenance_errors)})
                break

            # === 投稿模式阶段门禁 ===
            try:
                if mode_config.get("stage_gates"):
                    gate_results = self._run_stage_gates(
                        ch_id=ch_id,
                        from_chapter=from_chapter,
                        to_chapter=to_chapter,
                        pipeline_mode=pipeline_mode,
                    )
                    for gate_result in gate_results:
                        if gate_result.get("passed"):
                            continue
                        scope = gate_result.get("revision_scope", {})
                        retry_from = scope.get("from", ch_id)
                        retry_to = scope.get("to", ch_id)
                        print(f"  [阶段门禁] ❌ {gate_result.get('gate')}: {gate_result.get('score')}/100")
                        print(f"  [阶段门禁] 返修范围: 第{retry_from}-{retry_to}章 → 加入回补队列")
                        # 门禁返修范围不受 from_chapter 限制
                        for rt_ch in range(retry_from, retry_to + 1):
                            if rt_ch > to_chapter:
                                continue
                            existing = next((r for r in results if r.get("chapter_id") == rt_ch), None)
                            if existing and existing.get("status") == "completed":
                                existing["status"] = "stage_gate_retry"
                                existing["stage_gate"] = gate_result.get("gate")
                            elif not existing:
                                results.append({
                                    "chapter_id": rt_ch,
                                    "status": "stage_gate_retry",
                                    "stage": "stage_gate",
                                    "gate": gate_result.get("gate"),
                                    "error": f"阶段门禁 {gate_result.get('gate')} 要求返修",
                                })
                            # 清除已完成状态，强制回补队列重写
                            state["chapter_statuses"][str(rt_ch)] = "stage_gate_retry"
                        self._save_state(state)
            except Exception as gate_err:
                self._save_chapter_status(state, ch_id, "stage_gate_retry", str(gate_err))
                results.append({"chapter_id": ch_id, "status": "stage_gate_retry", "stage": "stage_gate",
                                "error": str(gate_err)})

            # === 记录结果 ===
            if state["chapter_statuses"].get(str(ch_id)) == "stage_gate_retry":
                break
            elapsed = time_module.time() - chapter_start
            chapter_result = {
                "chapter_id": ch_id,
                "status": "completed",
                "word_count": word_count,
                "review_score": self._extract_score(review_result) if review_result else None,
                "narrative_score": text_guard.get("score") if isinstance(text_guard, dict) else None,
                "commercial_score": commercial_text.get("score") if isinstance(commercial_text, dict) else None,
                "elapsed_seconds": round(elapsed, 1),
            }
            checkpoints.commit(ch_id)
            results.append(chapter_result)
            self._save_chapter_status(state, ch_id, "completed", "")
            self._save_state(state)

            print(f"  ✅ 第 {ch_id} 章完成 ({elapsed:.0f}s, ~{word_count} 字)")
            # Reset watchdog for next chapter
            next_key = f"last_activity_{ch_id + 1}"
            state[next_key] = time_module.time()
            state["consecutive_stalls"] = 0

            # 章节间短暂冷却，避免 API 限流（试运行模式跳过，便于秒级闭环验证）
            if ch_id < to_chapter and not self.dry_run:
                time_module.sleep(2)

        # Never rewrite old chapters out of order with future memory still active.
        failed = [r for r in results if r["status"] in ("failed", "stage_gate_retry")]
        if failed:
            checkpoints.rollback()
            earliest = min(int(r["chapter_id"]) for r in failed)
            # A retrospective batch gate requires rebuilding the whole dependent suffix.
            if str(earliest) in checkpoints.load()["chapters"]:
                checkpoints.invalidate(earliest)
                state = self._load_state()
            state.update(pipeline_active=True, blocked_at=earliest)
            self._save_state(state)
            return {"success": False, "blocked_at": earliest, "results": results,
                    "summary": self._summarize_results(results, time_module.time() - start_time)}

        # === 终稿前角色设定同步 ===
        if self._should_run_final_gate(from_chapter, to_chapter, results):
            print(f"\n  [角色同步] 检查角色设定是否与正文一致...")
            try:
                from .character_canon_sync_agent import CharacterCanonSyncAgent
                canon = CharacterCanonSyncAgent(self.paths)
                canon_result = canon.audit()
                if not canon_result.get("passed"):
                    print(f"    角色设定落后于正文: {canon_result.get('score')}/100")
                    canon.sync_profiles()
                    print(f"    已自动同步角色 canon 设定")
            except Exception as e:
                print(f"    [警告] 角色同步失败: {e}")

        # === 完成前终稿门禁 ===
        if mode_config.get("final_gate") and self._should_run_final_gate(from_chapter, to_chapter, results):
            print(f"\n  [终稿门禁] 投稿前总检...")
            final_gate = self.submission_gate.run_final_submission_gate(from_chapter, to_chapter)
            icon = "✅" if final_gate.get("passed") else "❌"
            print(f"    {icon} FinalSubmissionGate: {final_gate.get('score')}/100")
            print(f"    报告: {final_gate.get('report_path')}")
            if not final_gate.get("passed"):
                reason = self._format_gate_failure(final_gate)
                results.append({
                    "chapter_id": "final",
                    "status": "failed",
                    "stage": "final_submission_gate",
                    "error": reason,
                })

        # === 完成 ===
        total_elapsed = time_module.time() - start_time
        state["pipeline_active"] = False
        state["completed_at"] = datetime.now().isoformat()
        self._save_state(state)

        summary = self._summarize_results(results, total_elapsed)
        try:
            project_commercial = self.commercial_suite.audit_project()
            report_path = self.commercial_suite.save_project_report(project_commercial)
            print(f"  商业结构总评分: {project_commercial.get('score')}/100")
            print(f"  商业质量报告: {report_path}")
        except Exception as e:
            print(f"  [警告] 商业结构总检失败: {e}")

        print(f"\n{'='*60}")
        print(f"  🎉 管线执行完成！")
        print(f"  章节: {from_chapter} → {to_chapter}")
        print(f"  成功: {summary['completed']} 章")
        print(f"  失败: {summary['failed']} 章")
        print(f"  跳过: {summary['skipped']} 章")
        print(f"  总字数: ~{summary['total_words']} 字")
        print(f"  总耗时: {summary['elapsed_display']}")
        print(f"  平均评分: {summary['avg_score']}/100" if summary['avg_score'] else "")
        print(f"{'='*60}")

        return {
            "success": summary["failed"] == 0,
            "results": results,
            "summary": summary,
        }

    def get_progress(self) -> dict:
        """获取当前管线进度。"""
        state = self._load_state()
        if not state:
            return {"active": False, "message": "没有正在进行的管线"}

        completed = sum(
            1 for v in state.get("chapter_statuses", {}).values()
            if v == "completed"
        )

        return {
            "active": state.get("pipeline_active", False),
            "from_chapter": state.get("from_chapter"),
            "to_chapter": state.get("to_chapter"),
            "last_completed": state.get("last_completed"),
            "chapters_done": completed,
            "total_chapters": state.get("to_chapter", 0) - state.get("from_chapter", 0) + 1,
            "started_at": state.get("started_at"),
            "auto_revise": state.get("auto_revise"),
            "pipeline_mode": state.get("pipeline_mode", "unknown"),
        }

    # ========================================================================
    # 内部方法
    # ========================================================================

    def _get_volume_chapters(self, volume: int) -> list:
        """获取指定卷的所有章节大纲。"""
        outlines = load_yaml(self.paths.chapter_outline)
        if not outlines or "chapters" not in outlines:
            return []
        chapters = outlines["chapters"]
        # 简单分配：假设每卷 30 章
        # 实际上章节大纲里有 volume 字段
        vol_chapters = []
        for ch in chapters:
            if isinstance(ch, dict):
                ch_volume = ch.get("volume", 0)
                ch_id = ch.get("chapter_id", 0)
                if ch_volume == volume:
                    vol_chapters.append(ch)
                elif ch_volume == 0 and ch_id:
                    # 没有 volume 字段时，按 chapter_id 范围推断
                    inferred_vol = ((ch_id - 1) // 30) + 1
                    if inferred_vol == volume:
                        vol_chapters.append(ch)
        return sorted(vol_chapters, key=lambda x: x.get("chapter_id", 0))

    def _get_all_chapters(self) -> list:
        """获取所有章节大纲。"""
        outlines = load_yaml(self.paths.chapter_outline)
        if not outlines or "chapters" not in outlines:
            return []
        chapters = outlines["chapters"]
        return sorted(
            [ch for ch in chapters if isinstance(ch, dict)],
            key=lambda x: x.get("chapter_id", 0),
        )

    def _load_state(self) -> dict:
        """加载管线状态。"""
        path = self.paths.memory_dir / "pipeline_state.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_state(self, state: dict):
        """保存管线状态。"""
        path = self.paths.memory_dir / "pipeline_state.json"
        state["updated_at"] = datetime.now().isoformat()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, state)

    def _init_state(self, from_ch: int, to_ch: int, auto_revise: bool, pipeline_mode: str) -> dict:
        """初始化管线状态。"""
        state = {
            "pipeline_active": True,
            "from_chapter": from_ch,
            "to_chapter": to_ch,
            "last_completed": from_ch - 1,
            "auto_revise": auto_revise,
            "pipeline_mode": pipeline_mode,
            "started_at": datetime.now().isoformat(),
            "chapter_statuses": {},
            "errors": [],
        }
        self._save_state(state)
        return state

    def _normalize_pipeline_mode(self, pipeline_mode: str | None) -> str:
        mode = (pipeline_mode or "submission").strip().lower()
        if mode not in self.PIPELINE_MODES:
            valid = ", ".join(sorted(self.PIPELINE_MODES))
            raise ValueError(f"未知管线模式: {pipeline_mode}。可选: {valid}")
        return mode

    def _run_stage_gates(
        self,
        ch_id: int,
        from_chapter: int,
        to_chapter: int,
        pipeline_mode: str,
    ) -> list:
        """按章节位置运行阶段门禁，返回门禁结果列表。

        门禁异常返回未通过，停止续写并保留反馈。
        失败的门禁返回 revision_scope，由调用方将受影响章节加入回补队列。
        """
        all_results = []

        def _safe_run(gate_name: str, gate_call, label: str) -> dict:
            try:
                result = gate_call()
                self._print_gate_result(result)
                return result
            except Exception as e:
                # 门禁自身崩溃 → 记录但不株连章节。章节不一定有问题。
                print(f"    ⚠️ {label} 门禁异常，不能视为通过: {e}")
                return {
                    "gate": gate_name, "score": -1, "passed": False,
                    "revision_scope": {"from": max(1, ch_id - self.submission_gate.ROLLING_WINDOW + 1), "to": ch_id},
                    "warnings": [f"门禁执行异常，尚未验证: {e}"],
                    "report_path": "",
                }

        try:
            if ch_id == self.submission_gate.OPENING_WINDOW and from_chapter <= self.submission_gate.OPENING_WINDOW:
                result = _safe_run("Opening10Gate",
                    lambda: self.submission_gate.run_opening10_gate(ch_id), "前十章")
                all_results.append(result)

            if ch_id % self.submission_gate.ROLLING_WINDOW == 0 and ch_id > 1:
                result = _safe_run("RollingGate",
                    lambda: self.submission_gate.run_rolling_gate(ch_id), "滚动连续性")
                all_results.append(result)

                payoff_result = _safe_run("PayoffLedgerGate",
                    lambda: self.submission_gate.run_payoff_ledger_gate(ch_id), "兑现账本")
                all_results.append(payoff_result)

            if ch_id % self.submission_gate.ARC_WINDOW == 0:
                result = _safe_run("ArcCheckpointGate",
                    lambda: self.submission_gate.run_arc_checkpoint_gate(ch_id), "卷级结构")
                all_results.append(result)
        except Exception as e:
            all_results.append({"gate": "StageGateError", "passed": False, "score": -1,
                                "revision_scope": {"from": ch_id, "to": ch_id},
                                "warnings": [str(e)]})

        return all_results

    def _print_gate_result(self, result: dict):
        icon = "✅" if result.get("passed") else "⚠️"
        print(f"    {icon} {result.get('gate')}: {result.get('score')}/100")
        if result.get("report_path"):
            print(f"    报告: {result.get('report_path')}")
        if not result.get("passed"):
            for warning in result.get("warnings", [])[:3]:
                print(f"    - {warning}")

    def _format_gate_failure(self, result: dict) -> str:
        scope = result.get("revision_scope") or {}
        scope_text = ""
        if scope:
            scope_text = f"返修范围第{scope.get('from')}章-第{scope.get('to')}章。"
        warning = result.get("warnings", ["未通过阶段门禁"])[0]
        report = result.get("report_path", "")
        return f"{result.get('gate')} 未通过: {result.get('score')}/100。{scope_text}首要问题: {warning}。报告: {report}"

    def _should_run_final_gate(self, from_chapter: int, to_chapter: int, results: list) -> bool:
        """只在写到全书最后一章且当前运行无失败时做终稿总检。"""
        if any(r.get("status") == "failed" for r in results):
            return False
        chapters = self._get_all_chapters()
        if not chapters:
            return False
        chapter_ids = [int(ch.get("chapter_id", 0)) for ch in chapters if isinstance(ch, dict) and ch.get("chapter_id")]
        if not chapter_ids:
            return False
        last_chapter = max(chapter_ids)
        return to_chapter >= last_chapter and from_chapter <= last_chapter

    def _is_chapter_done(self, state: dict, chapter_id: int) -> bool:
        """检查章节是否已完成（门禁返修标记的章节不算完成）。"""
        statuses = state.get("chapter_statuses", {})
        ch_status = statuses.get(str(chapter_id), "")
        if ch_status != "completed":
            return False
        # 门禁标记返修的章节不算完成，需要重写
        if ch_status == "stage_gate_retry":
            return False
        chapter_path = self.paths.chapter_file(chapter_id)
        if not chapter_path.exists():
            return False
        content = load_markdown(chapter_path)
        valid, reason = self._validate_chapter_content(content)
        if not valid:
            print(f"  [重写] 第 {chapter_id} 章状态为 completed，但文件无效：{reason}")
            state["chapter_statuses"][str(chapter_id)] = "invalid_existing_file"
            self._save_state(state)
            return False
        return True

    def _save_chapter_status(self, state: dict, chapter_id: int, status: str, error: str):
        """保存单章状态。"""
        state["chapter_statuses"][str(chapter_id)] = status
        if error:
            state["errors"].append({
                "chapter_id": chapter_id,
                "error": error,
                "time": datetime.now().isoformat(),
            })
        if status == "completed":
            state["last_completed"] = max(state.get("last_completed", 0), chapter_id)

    def _extract_score(self, review_text: str) -> int:
        """从审查报告中提取总分。"""
        if not review_text:
            return None
        import re
        match = re.search(r'总分[\s\S]*?(\d+)\s*/\s*100', review_text)
        if match:
            return int(match.group(1))
        return None

    # 章纲核心结构字段(题材无关的故事骨架, 缺失是客观缺陷)
    CORE_OUTLINE_FIELDS = ["reader_hook", "character_desire", "obstacle",
                           "action", "cost", "ending_hook"]

    def _quality_revision_gate(
        self,
        review_result: str | None,
        chapter_id: int,
        content: str = "",
        current_outline: dict | None = None,
        advisory_results: list | None = None,
    ) -> dict:
        """判断章节是否需要返修。

        设计原则(2026-06 重构): **语义质量由 LLM 评审(ReviewAgent)裁判, 机械缺陷
        由客观检查裁判, 词频启发式只做提示不做硬门禁**。

        - 硬门禁 = ReviewAgent 总分 + 机械检查(字面意象重复 / 章纲核心字段缺失 /
          对话过少 / 正文过短)。这些要么是 LLM 的语义判断, 要么是可客观计数的缺陷。
        - 事件硬度/名场面/兑现/欲望线等"词频代理"模块(advisory_results)只收集为
          提示, 不计入 pass/fail —— 避免 Goodhart: 它们会逼 LLM 堆关键词而非真正
          写好。这些模块的报告仍会流进 WriterAgent 上下文作为修改提示。
        """
        reasons = []          # 硬门禁原因
        advisory = []         # 仅提示(不阻断)

        review_score = self._extract_score(review_result) if review_result else None
        if review_score is None:
            reasons.append("ReviewAgent 未返回可验证的总分")
        if review_score is not None and review_score < 70:
            reasons.append(f"ReviewAgent 语义评分低于 70: {review_score}/100")

        # ── 机械检查(客观缺陷, 硬门禁) ──
        # 1. 字面意象/句式过度重复(单章可归因、单章可修)
        try:
            style = self.commercial_suite.style_dedupe.audit_single_chapter(chapter_id)
            for w in style.get("warnings", []):
                reasons.append(f"机械检查·{w}")
        except Exception:
            pass
        # 2. 章纲核心结构字段缺失
        outline = current_outline or {}
        missing = [f for f in self.CORE_OUTLINE_FIELDS
                   if not str(outline.get(f, "")).strip()]
        if missing:
            reasons.append(f"机械检查·章纲缺核心结构字段: {missing}")
        # 3. 对话过少 / 正文过短
        if content:
            import re as _re
            dialogue = len(_re.findall(r"[“\"].{1,80}?[”\"]", content))
            if dialogue < 6:
                reasons.append(f"机械检查·对话过少({dialogue}处), 可能主要靠旁白推进")
            zh = self._count_chinese_chars(content)
            if zh < 1500:
                reasons.append(f"机械检查·正文偏短({zh}字), 疑似截断或场景不足")

        # ── 词频启发式: 仅作提示, 不阻断 ──
        for result in (advisory_results or []):
            if isinstance(result, dict):
                advisory.extend(result.get("warnings", []))

        return {
            "passed": not reasons,
            "reasons": reasons,
            "advisory": advisory,
            "review_score": review_score,
        }

    def _count_chinese_chars(self, text: str) -> int:
        """统计中文字符数。"""
        return sum(1 for c in text if '一' <= c <= '鿿')

    def _validate_chapter_content(self, content: str) -> tuple[bool, str]:
        """章节级完整性校验，防止 API 错误片段进入后续审查/记忆。"""
        try:
            return self.writer_agent.validate_chapter_content(content)
        except Exception as e:
            return False, f"校验异常: {e}"

    def _summarize_results(self, results: list, total_elapsed: float) -> dict:
        """汇总管线结果。"""
        completed = [r for r in results if r["status"] == "completed"]
        failed = [r for r in results if r["status"] == "failed"]
        skipped = [r for r in results if r["status"] == "skipped"]

        total_words = sum(r.get("word_count", 0) for r in completed)
        scores = [r["review_score"] for r in completed if r.get("review_score") is not None]

        hours = int(total_elapsed // 3600)
        minutes = int((total_elapsed % 3600) // 60)
        seconds = int(total_elapsed % 60)

        return {
            "completed": len(completed),
            "failed": len(failed),
            "skipped": len(skipped),
            "total_words": total_words,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "elapsed_seconds": round(total_elapsed, 1),
            "elapsed_display": f"{hours}h {minutes}m {seconds}s",
        }

