"""轻量评测/回归 harness（V 层）。

目的: 让"改了 prompt/门禁/模型, 小说到底变好还是变差"可被**测量**, 而不是肉眼猜。
设计原则:
- **题材无关**: fixture 自带 genre, harness 按 genre 解析题材包, 用对应题材的
  prompt/词表/门禁来写和评。同一套 harness 能评校园/都市/任何已注册题材。
- **两种模式**: mock(零成本, 验证管线没坏 + 机械指标稳定=结构回归);
  真实 API(用 ReviewAgent 这个 LLM 评委打分=质量回归)。
- **快照 + 对比**: 每次评测存快照, 改完代码再评, diff 出每个维度涨了还是跌了。
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ..config import save_yaml, save_markdown, load_yaml
from ..paths import ProjectPaths
from ..memory import FileMemoryStore
from ..genres import resolve_project_pack


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixtures(directory: Path | None = None) -> list[dict]:
    directory = directory or FIXTURES_DIR
    out = []
    for p in sorted(Path(directory).glob("*.yaml")):
        data = load_yaml(p)
        if isinstance(data, dict) and data.get("fixture_id"):
            out.append(data)
    return out


def _dialogue_count(text: str) -> int:
    return len(re.findall(r"[“\"].{1,80}?[”\"]", text))


def _zh(text: str) -> int:
    return sum(1 for c in text if "一" <= c <= "鿿")


def _review_score(report: str) -> int | None:
    if not report:
        return None
    m = re.search(r"总分[\s\S]*?(\d+)\s*/\s*100", report)
    return int(m.group(1)) if m else None


class EvalHarness:
    """对一组 fixture 跑"写一章→打分", 产出可对比的指标快照。"""

    # 核心结构字段(题材无关骨架)
    CORE_FIELDS = ["reader_hook", "character_desire", "obstacle", "action", "cost", "ending_hook"]

    def __init__(self, llm, fallback_llm=None, use_task_agent: bool = True):
        self.llm = llm
        self.fallback_llm = fallback_llm
        self.use_task_agent = use_task_agent

    def _materialize(self, fixture: dict) -> ProjectPaths:
        """把 fixture 落成一个临时项目(novel.yaml + 设定 + 章纲)。"""
        paths = ProjectPaths(Path(tempfile.mkdtemp(prefix="eval_")))
        paths.ensure_dirs()
        save_yaml(paths.novel_config, {"title": fixture.get("title", "eval"),
                                       "genre": fixture.get("genre", "")})
        setting = fixture.get("setting", {}) or {}
        # 角色按题材角色槽位落盘(单主角=protagonist, 双主角=protagonist_male/female)
        for key, val in setting.items():
            if key.startswith("protagonist") or key.startswith("character_"):
                save_yaml(paths.character_file(key), val)
        if "relationship_state" in setting:
            save_yaml(paths.relationship_state, setting["relationship_state"])
        if "world_setting" in setting:
            save_yaml(paths.world_setting_file, setting["world_setting"])
        ch = dict(fixture.get("chapter_outline", {}))
        ch.setdefault("chapter_id", 1)
        save_yaml(paths.chapter_outline, {"chapters": [ch]})
        return paths

    def run_fixture(self, fixture: dict) -> dict:
        from ..agents.context_agent import ContextAgent
        from ..agents.writer_agent import WriterAgent
        from ..agents.review_agent import ReviewAgent

        paths = self._materialize(fixture)
        pack = resolve_project_pack(paths)
        mem = FileMemoryStore(paths)
        ch_id = int(fixture.get("chapter_outline", {}).get("chapter_id", 1))

        # 注入 fallback 给各 agent runtime
        def _wire(agent):
            if hasattr(agent, "runtime") and self.fallback_llm:
                agent.runtime.fallback_llm = self.fallback_llm
            return agent

        ctx = _wire(ContextAgent(paths, self.llm, mem, pack))
        writer = _wire(WriterAgent(paths, self.llm, mem, pack))
        reviewer = _wire(ReviewAgent(paths, self.llm, mem, pack))

        error = ""
        content = ""
        review_score = None
        try:
            if self.use_task_agent:
                ctx.generate_writing_task(ch_id)
            else:
                ctx.generate_writing_task_deterministic(ch_id)
            content = writer.write_chapter(ch_id)
            try:
                report = reviewer.review_chapter(ch_id)
                review_score = _review_score(report)
            except Exception as e:
                error = f"review:{e}"
        except Exception as e:
            error = f"write:{e}"

        # 机械指标(题材无关) + 题材感知的风格去重
        outline = fixture.get("chapter_outline", {})
        missing = [f for f in self.CORE_FIELDS if not str(outline.get(f, "")).strip()]
        try:
            from ..agents.commercial_editor_agent import StyleDedupeAgent
            style = StyleDedupeAgent(paths, pack).audit_single_chapter(ch_id)
            style_warn = len(style.get("warnings", []))
        except Exception:
            style_warn = 0

        return {
            "fixture_id": fixture.get("fixture_id"),
            "genre": fixture.get("genre"),
            "metrics": {
                "zh_chars": _zh(content),
                "dialogue_count": _dialogue_count(content),
                "style_repeat_warnings": style_warn,
                "outline_core_missing": len(missing),
                "review_score": review_score,
                "wrote_ok": 1 if content and not error.startswith("write") else 0,
            },
            "error": error,
        }

    def run_all(self, fixtures: list[dict] | None = None) -> dict:
        fixtures = fixtures if fixtures is not None else load_fixtures()
        results = [self.run_fixture(f) for f in fixtures]
        return {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "git_head": _git_head(),
            "fixtures": len(results),
            "results": results,
        }


def _git_head() -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True,
                           cwd=str(Path(__file__).resolve().parent.parent.parent))
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def save_snapshot(snapshot: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def compare_snapshots(baseline: dict, current: dict) -> dict:
    """对比两份快照, 给出每个 fixture / 指标的变化(current - baseline)。"""
    base = {r["fixture_id"]: r["metrics"] for r in baseline.get("results", [])}
    diffs = []
    regressions = []
    for r in current.get("results", []):
        fid = r["fixture_id"]
        bm = base.get(fid, {})
        cm = r["metrics"]
        delta = {}
        for k, cv in cm.items():
            bv = bm.get(k)
            if isinstance(cv, (int, float)) and isinstance(bv, (int, float)):
                d = round(cv - bv, 3)
                delta[k] = d
                # 长度和对话数量是描述性指标；删去冗余不应算退步。
                worse_if_down = k in ("review_score", "wrote_ok")
                worse_if_up = k in ("style_repeat_warnings", "outline_core_missing")
                if (worse_if_down and d < 0) or (worse_if_up and d > 0):
                    regressions.append(f"{fid}.{k}: {bv}→{cv} ({d:+})")
        diffs.append({"fixture_id": fid, "delta": delta})
    return {"diffs": diffs, "regressions": regressions,
            "has_regression": bool(regressions)}

