"""VisualizationAgent - 关系变化可视化、审查趋势图、章节统计仪表盘。"""

import json
import yaml
from pathlib import Path
from datetime import datetime
from ..config import save_markdown, load_markdown, load_yaml, load_json
from ..paths import ProjectPaths
from ..memory import FileMemoryStore


class VisualizationAgent:
    """生成终端可读的 ASCII 图表和可视化报告。"""

    def __init__(self, paths: ProjectPaths, memory: FileMemoryStore):
        self.paths = paths
        self.memory = memory

    def generate_relationship_curve(self) -> str:
        """生成关系变化曲线图（ASCII）。"""
        ri = self.memory.load_relationship_state()
        summaries = self.memory.get_recent_summaries(n=100)

        # 从 summaries 中重建历史数据
        history = self._build_romance_history(summaries, ri)

        if not history:
            return "暂无足够数据生成关系曲线。请先完成至少 2 章并执行 update-memory。"

        output = []
        output.append("\n" + "=" * 70)
        output.append("  关系变化曲线 (Romance / Trust / Conflict)")
        output.append("=" * 70)

        # Y 轴范围 0-100
        chart_width = 50
        chapters = [h.get("chapter_id", 0) for h in history]

        for metric, label, symbol in [
            ("romance_level", "好感度", "♥"),
            ("trust_level", "信任度", "●"),
            ("conflict_level", "冲突度", "▲"),
        ]:
            output.append(f"\n  {symbol} {label}:")
            values = [min(100, max(0, h.get(metric, 0))) for h in history]

            for i, (ch_id, val) in enumerate(zip(chapters, values)):
                bar_len = int(val * chart_width / 100)
                bar = "█" * bar_len + "░" * (chart_width - bar_len)
                chapter_label = f"Ch{ch_id:02d}" if ch_id > 0 else "初始"
                output.append(f"  {chapter_label:6s} │{bar}│ {val:3d}")

        output.append("\n" + "-" * 70)
        output.append("  数值变化趋势：")
        if len(values) >= 2:
            romance_trend = values[-1] - values[0] if values else 0
            trend_icon = "↗" if romance_trend > 0 else "↘" if romance_trend < 0 else "→"
            output.append(f"  好感度变化: {romance_trend:+d} {trend_icon}")

        return "\n".join(output)

    def generate_review_trend(self) -> str:
        """生成审查评分趋势图。"""
        reviews_dir = self.paths.reviews_dir
        if not reviews_dir.exists():
            return "暂无审查报告。"

        review_files = sorted(reviews_dir.glob("chapter_*_review.md"))
        if not review_files:
            return "暂无审查报告。"

        output = []
        output.append("\n" + "=" * 70)
        output.append("  审查评分趋势")
        output.append("=" * 70)

        # 解析每个审查报告的总分
        scores = []
        for rf in review_files:
            content = load_markdown(rf)
            ch_id = self._extract_chapter_id(rf.name)
            score = self._extract_score(content)
            if score is not None:
                scores.append((ch_id, score))

        if not scores:
            return "无法从审查报告中提取评分数据。"

        scores.sort(key=lambda x: x[0])

        output.append(f"\n  {'章节':<8s} {'分数':<6s} {'评级':<8s} {'趋势'}")
        output.append("  " + "-" * 50)

        prev_score = None
        for ch_id, score in scores:
            bar_len = int(score * 30 / 100)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            if score >= 85:
                grade = "优秀"
            elif score >= 70:
                grade = "良好"
            elif score >= 60:
                grade = "一般"
            else:
                grade = "需重写"

            if prev_score is not None:
                trend = "↗" if score > prev_score else "↘" if score < prev_score else "→"
            else:
                trend = "●"
            prev_score = score

            output.append(f"  Ch{ch_id:03d}   {score:3d}    {grade:<6s}  {trend} {bar}")

        avg_score = sum(s[1] for s in scores) / len(scores)
        min_score = min(s[1] for s in scores)
        max_score = max(s[1] for s in scores)
        output.append(f"\n  平均分: {avg_score:.1f}  |  最低: {min_score}  |  最高: {max_score}")

        return "\n".join(output)

    def generate_chapter_stats(self) -> str:
        """生成章节统计仪表盘。"""
        chapters_dir = self.paths.chapters_dir
        if not chapters_dir.exists():
            return "暂无章节数据。"

        chapter_files = sorted(chapters_dir.glob("chapter_*.md"))
        if not chapter_files:
            return "暂无章节数据。"

        output = []
        output.append("\n" + "=" * 70)
        output.append("  章节统计仪表盘")
        output.append("=" * 70)

        total_words = 0
        stats = []
        for cf in chapter_files:
            content = load_markdown(cf)
            ch_id = self._extract_chapter_id(cf.name)
            chinese_chars = sum(1 for c in content if '一' <= c <= '鿿')
            total_words += chinese_chars
            stats.append((ch_id, chinese_chars, content))

        # 总体统计
        output.append(f"\n  📊 总体统计:")
        output.append(f"  已完成章节: {len(stats)}")
        output.append(f"  总字数: {total_words:,}")
        output.append(f"  平均每章: {total_words // max(len(stats), 1):,} 字")
        output.append(f"  最长章节: {max(s[1] for s in stats):,} 字 (Ch{max(stats, key=lambda x: x[1])[0]:03d})")
        output.append(f"  最短章节: {min(s[1] for s in stats):,} 字 (Ch{min(stats, key=lambda x: x[1])[0]:03d})")

        # 字数柱状图
        output.append(f"\n  📈 各章字数:")
        max_words = max(s[1] for s in stats)
        for ch_id, words, _ in stats:
            bar_len = int(words * 40 / max(max_words, 1))
            bar = "█" * bar_len
            output.append(f"  Ch{ch_id:03d} {bar} {words:,}字")

        # 伏笔统计
        foreshadowing_data = self.memory.get_all_foreshadowing()
        if isinstance(foreshadowing_data, list):
            active = sum(1 for f in foreshadowing_data if isinstance(f, dict) and f.get("status") == "active")
            recalled = sum(1 for f in foreshadowing_data if isinstance(f, dict) and f.get("status") == "recalled")
            output.append(f"\n  📝 伏笔统计:")
            output.append(f"  活跃伏笔: {active}  |  已回收: {recalled}  |  总计: {len(foreshadowing_data)}")

        # 事件统计
        events = self.memory.get_all_events()
        if isinstance(events, list):
            output.append(f"\n  📅 事件统计:")
            output.append(f"  已记录事件: {len(events)}")

        return "\n".join(output)

    def generate_full_dashboard(self) -> str:
        """生成完整可视化仪表盘。"""
        output = []
        output.append(f"\n{'='*70}")
        output.append(f"  ShortFictionAgent 创作仪表盘")
        output.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output.append(f"{'='*70}")

        output.append(self.generate_chapter_stats())
        output.append(self.generate_relationship_curve())
        output.append(self.generate_review_trend())

        # 保存
        dashboard_path = self.paths.visualization_dir / "dashboard.txt"
        full_output = "\n".join(output)
        dashboard_path.parent.mkdir(parents=True, exist_ok=True)
        dashboard_path.write_text(full_output, encoding="utf-8")

        return full_output

    def _build_romance_history(self, summaries, current_ri) -> list:
        """从摘要和当前关系状态重建数值历史。"""
        history = [{"chapter_id": 0, "romance_level": 0, "trust_level": 0, "conflict_level": 0}]

        for s in summaries:
            if isinstance(s, dict):
                history.append({
                    "chapter_id": s.get("chapter_id", 0),
                    "romance_level": s.get("romance_level", 0),
                    "trust_level": s.get("trust_level", 0),
                    "conflict_level": s.get("conflict_level", 0),
                })

        if current_ri:
            last_ch = summaries[-1].get("chapter_id", 0) + 1 if summaries else 1
            history.append({
                "chapter_id": last_ch,
                "romance_level": current_ri.get("romance_level", 0),
                "trust_level": current_ri.get("trust_level", 0),
                "conflict_level": current_ri.get("conflict_level", 0),
            })

        return history

    def _extract_chapter_id(self, filename: str) -> int:
        """从文件名提取章节号。"""
        import re
        match = re.search(r'chapter[_]?(\d+)', filename, re.IGNORECASE)
        return int(match.group(1)) if match else 0

    def _extract_score(self, review_content: str) -> int:
        """从审查报告中提取总分。"""
        import re
        # 匹配 "## 总分\nXX / 100" 或 "总分\n82 / 100"
        match = re.search(r'总分.*?(\d+)\s*/\s*100', review_content)
        if match:
            return int(match.group(1))
        # 备选匹配
        match = re.search(r'(\d{2,3})\s*/\s*100', review_content)
        if match:
            return int(match.group(1))
        return None

