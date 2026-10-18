"""轻量本地记忆系统 - 基于文件存储的摘要、事件、伏笔管理。"""

import json
from pathlib import Path
from typing import Optional
from .storage import safe_read_json, safe_write_json, safe_read_yaml, safe_write_yaml
from .models import ChapterSummary, EventLogItem, ForeshadowingItem, RelationshipState


class FileMemoryStore:
    """基于本地文件的轻量记忆存储。"""

    def __init__(self, paths):
        """
        Args:
            paths: ProjectPaths 实例
        """
        self.paths = paths
        self._ensure_files()

    def _ensure_files(self):
        """确保记忆文件存在。"""
        files = [
            self.paths.chapter_summaries,
            self.paths.event_log,
            self.paths.foreshadowing,
            self.paths.reusable_details,
        ]
        for f in files:
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                safe_write_json(f, [], backup=False)

    # === 章节摘要 ===
    def get_recent_summaries(self, n: int = 3):
        """获取最近 n 章摘要。"""
        summaries = safe_read_json(self.paths.chapter_summaries)
        if isinstance(summaries, list):
            return summaries[-n:] if len(summaries) >= n else summaries[:]
        return []

    def get_context_for_chapter(self, chapter_id: int, recent_n: int = 3, volume_interval: int = 10) -> list:
        """
        获取写第 chapter_id 章所需的上下文：
        - 最近 recent_n 章完整摘要
        - 更早的章节压缩为卷摘要（每 volume_interval 章一组）

        这样写到第 50 章时，不会加载 47 章完整摘要，而是只加载最近 3 章 +
        前面的 4 个卷摘要（每 10 章一个），大幅减少 Token 消耗。
        """
        summaries = safe_read_json(self.paths.chapter_summaries)
        if not isinstance(summaries, list) or len(summaries) == 0:
            return []

        result = []

        # 最近 N 章：完整摘要
        recent_start = max(0, len(summaries) - recent_n)
        for s in summaries[recent_start:]:
            result.append({"type": "chapter_summary", "data": s})

        # 更早的章节：使用卷摘要
        older = summaries[:recent_start]
        if older:
            volume_summaries = self._get_volume_summaries()
            # 只取 relevant 的卷摘要（覆盖 older 章节的）
            older_chapter_ids = {s.get("chapter_id", 0) for s in older if isinstance(s, dict)}
            for vs in volume_summaries:
                if isinstance(vs, dict):
                    vs_range = vs.get("chapter_range", [])
                    if vs_range and vs_range[0] in older_chapter_ids:
                        result.append({"type": "volume_summary", "data": vs})

        return result

    def generate_volume_summary(self, from_chapter: int, to_chapter: int, llm_client=None) -> dict:
        """
        将一段章节范围压缩为一个卷摘要。
        如果提供 llm_client，调用 LLM 生成压缩摘要；
        否则用简单的文本拼接。
        """
        summaries = safe_read_json(self.paths.chapter_summaries)
        if not isinstance(summaries, list):
            return {}

        chunk = [
            s for s in summaries
            if isinstance(s, dict) and from_chapter <= s.get("chapter_id", 0) <= to_chapter
        ]
        if not chunk:
            return {}

        # 提取关键信息
        key_events = []
        relationship_changes = []
        for s in chunk:
            sid = s.get("chapter_id", 0)
            if s.get("summary"):
                key_events.append(f"第{sid}章: {s['summary'][:200]}")
            rc = s.get("relationship_change", "")
            if rc:
                relationship_changes.append(f"第{sid}章: {rc}")

        volume_summary = {
            "chapter_range": [from_chapter, to_chapter],
            "chapter_count": len(chunk),
            "key_events": key_events,
            "relationship_changes": relationship_changes,
            "summary_text": "；".join(key_events[-5:]),  # 保留最近 5 章的事件作为摘要
        }

        # 如果提供了 LLM，调用生成更精炼的摘要
        if llm_client:
            try:
                compact_prompt = f"""请将以下 {len(chunk)} 章的小说摘要压缩为一段 200 字以内的精炼总结。
保留：关键人物关系变化、重要事件、主要伏笔。

章节摘要：
{chr(10).join(key_events)}

关系变化：
{chr(10).join(relationship_changes) if relationship_changes else '无'}

请用简洁的段落输出总结："""
                from .agent_runtime import generate_with_timeout
                compact = generate_with_timeout(llm_client, compact_prompt, "general", 180,
                                                config_dir=str(self.paths.root))
                if compact and "[API" not in compact:
                    volume_summary["summary_text"] = compact[:300]
            except Exception:
                pass  # LLM 压缩失败不影响主流程

        # 保存卷摘要
        self._save_volume_summary(volume_summary)
        return volume_summary

    def _get_volume_summaries(self) -> list:
        """获取所有卷摘要。"""
        path = self.paths.memory_dir / "volume_summaries.json"
        summaries = safe_read_json(path)
        return summaries if isinstance(summaries, list) else []

    def _save_volume_summary(self, summary: dict):
        """保存卷摘要（去重合并）。"""
        path = self.paths.memory_dir / "volume_summaries.json"
        existing = self._get_volume_summaries()
        # 替换相同范围的
        new_range = summary.get("chapter_range", [])
        found = False
        for i, s in enumerate(existing):
            if isinstance(s, dict) and s.get("chapter_range") == new_range:
                existing[i] = summary
                found = True
                break
        if not found:
            existing.append(summary)
        safe_write_json(path, existing)

    def get_chapter_summary(self, chapter_id: int) -> Optional[dict]:
        """获取指定章节摘要。"""
        summaries = safe_read_json(self.paths.chapter_summaries)
        if isinstance(summaries, list):
            for s in summaries:
                if isinstance(s, dict) and s.get("chapter_id") == chapter_id:
                    return s
        return None

    def append_chapter_summary(self, chapter_id: int, summary: dict):
        """添加或更新章节摘要。"""
        summaries = safe_read_json(self.paths.chapter_summaries)
        if not isinstance(summaries, list):
            summaries = []

        summary["chapter_id"] = chapter_id
        # 查找是否已存在
        found = False
        for i, s in enumerate(summaries):
            if isinstance(s, dict) and s.get("chapter_id") == chapter_id:
                summaries[i] = summary
                found = True
                break
        if not found:
            summaries.append(summary)

        # 按 chapter_id 排序
        summaries.sort(key=lambda x: x.get("chapter_id", 0))
        safe_write_json(self.paths.chapter_summaries, summaries)

    # === 事件日志 ===
    def get_all_events(self) -> list:
        """获取所有事件。"""
        events = safe_read_json(self.paths.event_log)
        return events if isinstance(events, list) else []

    def get_recent_events(self, n: int = 5) -> list:
        """获取最近 N 章的事件。"""
        events = self.get_all_events()
        if not events:
            return []
        # 找到最大章节号
        max_ch = max(
            (e.get("chapter_id", 0) for e in events if isinstance(e, dict)),
            default=0,
        )
        cutoff = max_ch - n
        return [e for e in events if isinstance(e, dict) and e.get("chapter_id", 0) > cutoff]

    def get_events_for_context(self, chapter_id: int, recent_n: int = 5,
                              max_items: int = 40) -> list:
        """
        获取事件上下文，分层返回：
        - 最近 recent_n 章：完整事件详情（最多 max_items 条）
        - 更早章节：压缩摘要（如果已生成），大幅减少 Token 消耗

        如果近期事件本身超过 max_items，只返回最新的 max_items 条。
        """
        all_events = self.get_all_events()
        if not all_events:
            return []

        cutoff = chapter_id - recent_n
        recent = [e for e in all_events if isinstance(e, dict) and e.get("chapter_id", 0) >= cutoff]

        # 如果近期事件过多，只取最新的 max_items 条
        if len(recent) > max_items:
            # 按 chapter_id 降序，保留最重要的（高 relationship_impact 优先）
            recent.sort(key=lambda x: (
                x.get("chapter_id", 0),
                1 if x.get("relationship_impact") and x["relationship_impact"] else 0,
            ), reverse=True)
            recent = recent[:max_items]

        result = list(recent)

        older_compressed = self._get_compressed_events()
        if older_compressed and older_compressed.get("compressed_summary"):
            result.append({
                "type": "compressed_events_summary",
                "covers_chapters": older_compressed.get("covers_chapters", []),
                "summary": older_compressed.get("compressed_summary", ""),
                "note": "以上为早期章节的事件压缩摘要，关键人物转折和伏笔已保留",
            })

        return result

    def compact_events(self, up_to_chapter: int, recent_n: int = 5, llm_client=None) -> dict:
        """
        压缩早期章节事件：最近 recent_n 章保留完整事件，
        更早章节的事件合并为一个结构化压缩摘要。
        压缩结果存储到 memory/compressed_events.json。
        """
        all_events = self.get_all_events()
        if not all_events:
            return {}

        cutoff = up_to_chapter - recent_n
        older_events = [e for e in all_events if isinstance(e, dict) and e.get("chapter_id", 0) < cutoff]

        if not older_events:
            return {}

        # 按章节分组构造结构化摘要
        chapters_covered = sorted(set(e.get("chapter_id", 0) for e in older_events))
        summary_parts = []
        for ch_id in chapters_covered:
            ch_events = [e for e in older_events if e.get("chapter_id") == ch_id]
            for e in ch_events[:3]:  # 每章最多保留 3 条关键事件
                desc = e.get("description", "")
                impact = e.get("relationship_impact", "")
                line = f"第{ch_id}章: {desc}"
                if impact:
                    line += f"（关系影响: {impact}）"
                summary_parts.append(line)

        compressed = {
            "compressed_at_chapter": up_to_chapter,
            "covers_chapters": [min(chapters_covered), max(chapters_covered)],
            "original_event_count": len(older_events),
            "compressed_summary": "\n".join(summary_parts[:100]),
        }

        # 如果有 LLM，生成更精炼的摘要
        if llm_client and len(older_events) > 20:
            try:
                compact_prompt = f"""请将以下 {len(older_events)} 条小说事件日志压缩为 300 字以内的事件摘要。
保留关键信息：人物关系转折节点、重要伏笔线索、角色成长里程碑。
去除：日常场景描述、次要细节、重复信息。

事件日志：
{json.dumps(older_events, ensure_ascii=False, indent=2)[:3000]}

请用简洁段落输出事件摘要："""
                from .agent_runtime import generate_with_timeout
                refined = generate_with_timeout(llm_client, compact_prompt, "general", 180,
                                                config_dir=str(self.paths.root))
                if refined and "[API" not in refined:
                    compressed["compressed_summary"] = refined[:500]
                    compressed["llm_compressed"] = True
            except Exception:
                pass  # LLM 压缩失败不影响主流程

        # 保存
        path = self.paths.memory_dir / "compressed_events.json"
        safe_write_json(path, compressed)

        return compressed

    def _get_compressed_events(self) -> dict:
        """获取已压缩的事件摘要。"""
        path = self.paths.memory_dir / "compressed_events.json"
        data = safe_read_json(path)
        return data if isinstance(data, dict) else {}

    def get_events_stats(self) -> dict:
        """获取事件日志统计（用于监控 Token 预算）。"""
        events = self.get_all_events()
        if not events:
            return {"total": 0, "by_chapter": {}}
        by_chapter = {}
        for e in events:
            ch = e.get("chapter_id", 0) if isinstance(e, dict) else 0
            by_chapter[ch] = by_chapter.get(ch, 0) + 1
        return {
            "total": len(events),
            "by_chapter": dict(sorted(by_chapter.items())),
        }

    def search_events(self, keyword: str) -> list:
        """按关键词搜索事件。"""
        events = self.get_all_events()
        results = []
        keyword_lower = keyword.lower()
        for evt in events:
            if isinstance(evt, dict):
                text = json.dumps(evt, ensure_ascii=False).lower()
                if keyword_lower in text:
                    results.append(evt)
        return results

    def get_events_by_chapter(self, chapter_id: int) -> list:
        """获取指定章节的事件。"""
        events = self.get_all_events()
        return [e for e in events if isinstance(e, dict) and e.get("chapter_id") == chapter_id]

    def append_event(self, event: dict):
        """添加事件。"""
        events = self.get_all_events()
        events.append(event)
        safe_write_json(self.paths.event_log, events)

    def append_events(self, events_list: list):
        """批量添加事件。"""
        events = self.get_all_events()
        events.extend(events_list)
        safe_write_json(self.paths.event_log, events)

    # === 伏笔 ===
    def get_all_foreshadowing(self) -> list:
        """获取所有伏笔。"""
        items = safe_read_json(self.paths.foreshadowing)
        return items if isinstance(items, list) else []

    def search_foreshadowing(self, keyword: str) -> list:
        """按关键词搜索伏笔。"""
        items = self.get_all_foreshadowing()
        results = []
        keyword_lower = keyword.lower()
        for item in items:
            if isinstance(item, dict):
                text = json.dumps(item, ensure_ascii=False).lower()
                if keyword_lower in text:
                    results.append(item)
        return results

    def get_active_foreshadowing(self) -> list:
        """获取所有活跃伏笔。"""
        items = self.get_all_foreshadowing()
        return [i for i in items if isinstance(i, dict) and i.get("status") == "active"]

    def get_foreshadowing_for_context(self, chapter_id: int, max_items: int = 20) -> list:
        """
        获取伏笔上下文，按重要性过滤和排序：
        - 所有活跃伏笔（按 importance 排序：high > medium > low）
        - 最近 5 章内刚回收的伏笔（帮助维持 continuity）
        - 总数不超过 max_items
        """
        all_items = self.get_all_foreshadowing()
        if not all_items:
            return []

        active = [f for f in all_items if isinstance(f, dict) and f.get("status") == "active"]
        recently_recalled = [
            f for f in all_items
            if isinstance(f, dict) and f.get("status") == "recalled"
            and f.get("actual_payoff_chapter", 0) >= chapter_id - 5
        ]

        # 去重：以 foreshadow_id 为准
        seen_ids = set()
        combined = []
        for item in active + recently_recalled:
            fid = item.get("foreshadow_id", "")
            if fid not in seen_ids:
                seen_ids.add(fid)
                combined.append(item)

        # 按重要性排序：high → medium → low
        importance_order = {"high": 0, "medium": 1, "low": 2, "critical": 0}
        combined.sort(key=lambda x: importance_order.get(x.get("importance", "low"), 2))

        return combined[:max_items]

    def upsert_foreshadowing(self, item: dict):
        """添加或更新伏笔。"""
        items = self.get_all_foreshadowing()
        fore_id = item.get("foreshadow_id", "")

        found = False
        for i, existing in enumerate(items):
            if isinstance(existing, dict) and existing.get("foreshadow_id") == fore_id:
                items[i] = item
                found = True
                break
        if not found:
            items.append(item)

        safe_write_json(self.paths.foreshadowing, items)

    def recall_foreshadowing(self, foreshadow_id: str, chapter_id: int):
        """回收伏笔（标记为已回收）。"""
        items = self.get_all_foreshadowing()
        for item in items:
            if isinstance(item, dict) and item.get("foreshadow_id") == foreshadow_id:
                item["status"] = "recalled"
                item["actual_payoff_chapter"] = chapter_id
        safe_write_json(self.paths.foreshadowing, items)

    # === 关系状态 ===
    def load_relationship_state(self) -> dict:
        """加载关系状态。"""
        return safe_read_yaml(self.paths.relationship_state)

    def update_relationship_state(self, delta: dict):
        """更新关系状态（合并方式）。"""
        current = safe_read_yaml(self.paths.relationship_state)
        if not current:
            current = {}

        def deep_update(target, source):
            for key, value in source.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    deep_update(target[key], value)
                else:
                    target[key] = value

        deep_update(current, delta)
        safe_write_yaml(self.paths.relationship_state, current)

    # === 可复用细节 ===
    def get_reusable_details(self, keyword: str = None) -> list:
        """获取可复用细节，可选关键词过滤。"""
        details = safe_read_json(self.paths.reusable_details)
        if not isinstance(details, list):
            return []
        if keyword:
            keyword_lower = keyword.lower()
            return [d for d in details if isinstance(d, dict) and
                    keyword_lower in json.dumps(d, ensure_ascii=False).lower()]
        return details

    def add_reusable_detail(self, detail: dict):
        """添加可复用细节。"""
        details = self.get_reusable_details()
        details.append(detail)
        safe_write_json(self.paths.reusable_details, details)

    # === 全文搜索 ===
    def search_all(self, keyword: str) -> list:
        """跨所有记忆文件搜索关键词。返回匹配结果列表。"""
        results = []

        # 搜索章节摘要
        summaries = safe_read_json(self.paths.chapter_summaries)
        if isinstance(summaries, list):
            for s in summaries:
                if keyword.lower() in json.dumps(s, ensure_ascii=False).lower():
                    results.append({
                        "source": str(self.paths.chapter_summaries.relative_to(self.paths.root.parent)),
                        "type": "chapter_summary",
                        "match": s,
                    })

        # 搜索事件日志
        for evt in self.search_events(keyword):
            results.append({
                "source": str(self.paths.event_log.relative_to(self.paths.root.parent)),
                "type": "event_log",
                "match": evt,
            })

        # 搜索伏笔
        for item in self.search_foreshadowing(keyword):
            results.append({
                "source": str(self.paths.foreshadowing.relative_to(self.paths.root.parent)),
                "type": "foreshadowing",
                "match": item,
            })

        return results

